from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import orjson
from django.utils.timezone import now as timezone_now

from zerver.actions.message_flags import do_mark_all_as_read, do_mark_stream_messages_as_read
from zerver.actions.realm_settings import (
    do_change_realm_permission_group_setting,
    do_set_realm_property,
)
from zerver.actions.user_settings import do_change_user_setting
from zerver.lib.armada_read_receipts import (
    ARMADA_READ_RECEIPTS_BATCH_LIMIT,
    get_armada_read_receipt_recipients,
)
from zerver.lib.event_schema import check_armada_read_receipts_event
from zerver.lib.muted_users import add_user_mute
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import NamedUserGroup, UserProfile
from zerver.models.realms import get_realm
from zerver.models.streams import get_stream

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class ArmadaReadReceiptsTest(ZulipTestCase):
    def mark_read(self, user: UserProfile, message_ids: list[int]) -> None:
        result = self.api_post(
            user,
            "/api/v1/messages/flags",
            {"messages": orjson.dumps(message_ids).decode(), "op": "add", "flag": "read"},
        )
        self.assert_json_success(result)

    def mark_read_capturing_receipts(
        self, user: UserProfile, message_ids: list[int], expected_num_receipts: int
    ) -> list[Mapping[str, Any]]:
        # Marking messages read also tells the reader's own clients, in
        # one update_message_flags event.
        with self.capture_send_event_calls(expected_num_events=1 + expected_num_receipts) as events:
            self.mark_read(user, message_ids)
        return [event for event in events if event["event"]["type"] == "read_receipts"]

    def batch(self, user: UserProfile, message_ids: list[int]) -> "TestHttpResponse":
        self.login_user(user)
        return self.client_get(
            "/json/armada/read_receipts",
            {"message_ids": orjson.dumps(message_ids).decode()},
            intentionally_undocumented=True,
        )

    def read_receipts(self, user: UserProfile, message_ids: list[int]) -> dict[str, list[int]]:
        return self.assert_json_success(self.batch(user, message_ids))["read_receipts"]

    def test_batch_answers_for_own_messages_only(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        cordelia = self.example_user("cordelia")

        stream_message_id = self.send_stream_message(othello, "Verona", "read receipts")
        direct_message_id = self.send_personal_message(othello, hamlet)
        unread_message_id = self.send_stream_message(othello, "Verona", "nobody yet")
        someone_elses_message_id = self.send_stream_message(cordelia, "Verona", "mine")
        self.mark_read(hamlet, [stream_message_id, direct_message_id, someone_elses_message_id])
        self.mark_read(iago, [stream_message_id])

        self.assertEqual(
            self.read_receipts(
                othello,
                [
                    stream_message_id,
                    direct_message_id,
                    unread_message_id,
                    someone_elses_message_id,
                    999999999,
                ],
            ),
            {
                str(stream_message_id): sorted([hamlet.id, iago.id]),
                str(direct_message_id): [hamlet.id],
                str(unread_message_id): [],
            },
        )

    def test_batch_limit(self) -> None:
        othello = self.example_user("othello")
        message_ids = list(range(1, ARMADA_READ_RECEIPTS_BATCH_LIMIT + 1))
        self.assert_json_success(self.batch(othello, message_ids))
        self.assert_json_error(
            self.batch(othello, [*message_ids, ARMADA_READ_RECEIPTS_BATCH_LIMIT + 1]),
            "Too many messages: at most 100 at a time.",
        )

    def test_realm_setting_off(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        message_id = self.send_stream_message(othello, "Verona", "read receipts")
        do_set_realm_property(othello.realm, "enable_read_receipts", False, acting_user=None)

        self.assertEqual(self.mark_read_capturing_receipts(hamlet, [message_id], 0), [])
        self.assert_json_error(
            self.batch(othello, [message_id]),
            "Read receipts are disabled in this organization.",
        )

    def test_reader_who_turned_receipts_off(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        message_id = self.send_stream_message(othello, "Verona", "read receipts")
        do_change_user_setting(hamlet, "send_read_receipts", False, acting_user=None)

        self.assertEqual(self.mark_read_capturing_receipts(hamlet, [message_id], 0), [])
        self.mark_read(iago, [message_id])
        self.assertEqual(self.read_receipts(othello, [message_id]), {str(message_id): [iago.id]})

    def test_mutes_hide_receipts_both_ways(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        message_id = self.send_stream_message(othello, "Verona", "read receipts")
        # Hamlet muted the sender; the sender muted Iago.
        add_user_mute(hamlet, othello, date_muted=timezone_now())
        add_user_mute(othello, iago, date_muted=timezone_now())

        self.assertEqual(self.mark_read_capturing_receipts(hamlet, [message_id], 0), [])
        self.assertEqual(self.mark_read_capturing_receipts(iago, [message_id], 0), [])
        self.assertEqual(self.read_receipts(othello, [message_id]), {str(message_id): []})

    def test_event_tells_each_sender_about_their_own_messages(self) -> None:
        othello = self.example_user("othello")
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        othello_message_ids = [
            self.send_stream_message(othello, "Verona", "one"),
            self.send_personal_message(othello, hamlet),
        ]
        cordelia_message_id = self.send_stream_message(cordelia, "Verona", "two")
        own_message_id = self.send_stream_message(hamlet, "Verona", "three")
        bot = self.example_user("default_bot")
        bot_message_id = self.send_stream_message(bot, "Verona", "four")

        receipts = self.mark_read_capturing_receipts(
            hamlet,
            [*othello_message_ids, cordelia_message_id, own_message_id, bot_message_id],
            2,
        )
        for receipt in receipts:
            check_armada_read_receipts_event("event", {**receipt["event"], "id": 1})
        self.assertEqual(
            [(receipt["event"], receipt["users"]) for receipt in receipts],
            [
                (
                    {
                        "type": "read_receipts",
                        "message_ids": sorted(othello_message_ids),
                        "user_id": hamlet.id,
                    },
                    [othello.id],
                ),
                (
                    {
                        "type": "read_receipts",
                        "message_ids": [cordelia_message_id],
                        "user_id": hamlet.id,
                    },
                    [cordelia.id],
                ),
            ],
        )

    def test_event_from_marking_a_topic_and_everything_read(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        # The fixtures leave Hamlet with unread messages from many people.
        do_mark_all_as_read(hamlet)
        topic_message_id = self.send_stream_message(othello, "Verona", "one", topic_name="ticks")
        other_message_id = self.send_stream_message(othello, "Denmark", "two")

        verona_recipient_id = get_stream("Verona", hamlet.realm).recipient_id
        assert verona_recipient_id is not None
        with self.capture_send_event_calls(expected_num_events=2) as events:
            do_mark_stream_messages_as_read(hamlet, verona_recipient_id, topic_name="ticks")
        [receipt] = [event for event in events if event["event"]["type"] == "read_receipts"]
        self.assertEqual(
            receipt["event"],
            {"type": "read_receipts", "message_ids": [topic_message_id], "user_id": hamlet.id},
        )
        self.assertEqual(receipt["users"], [othello.id])

        with self.capture_send_event_calls(expected_num_events=2) as events:
            do_mark_all_as_read(hamlet)
        [receipt] = [event for event in events if event["event"]["type"] == "read_receipts"]
        self.assertEqual(
            receipt["event"],
            {"type": "read_receipts", "message_ids": [other_message_id], "user_id": hamlet.id},
        )
        self.assertEqual(receipt["users"], [othello.id])

    def test_guest_under_limited_access(self) -> None:
        self.set_up_db_for_testing_user_access()
        realm = get_realm("zulip")
        polonius = self.example_user("polonius")
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")

        # Cordelia receives the guest's message, then leaves the channel:
        # she shares nothing with him afterwards, so he cannot see her.
        self.subscribe(cordelia, "test_stream1")
        message_id = self.send_stream_message(polonius, "test_stream1", "read receipts")
        self.unsubscribe(cordelia, "test_stream1")
        self.mark_read(cordelia, [message_id])

        [receipt] = self.mark_read_capturing_receipts(hamlet, [message_id], 1)
        self.assertEqual(receipt["users"], [polonius.id])
        self.assertEqual(get_armada_read_receipt_recipients(cordelia, [message_id]), {})
        self.assertEqual(self.read_receipts(polonius, [message_id]), {str(message_id): [hamlet.id]})

        # With access to everyone, the guest learns of Cordelia too.
        everyone = NamedUserGroup.objects.get(name="role:everyone", realm_for_sharding=realm)
        do_change_realm_permission_group_setting(
            realm, "can_access_all_users_group", everyone, acting_user=None
        )
        self.assertEqual(
            get_armada_read_receipt_recipients(cordelia, [message_id]),
            {polonius.id: [message_id]},
        )
        self.assertEqual(
            self.read_receipts(polonius, [message_id]),
            {str(message_id): sorted([hamlet.id, cordelia.id])},
        )
