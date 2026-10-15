from datetime import timedelta
from typing import Any
from unittest import mock

import orjson
import time_machine
from django.db import IntegrityError
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.create_user import do_create_user
from zerver.lib.armada_client_keys import first_message_id, send_once
from zerver.lib.armada_gate import invalidate_armada_selected_bot_cache
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, MessageClientKey, UserProfile
from zerver.models.bots import Service


class ClientKeyTest(ZulipTestCase):
    """Exactly-once sends by client key (zerver/lib/armada_client_keys.py)."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.login_user(self.hamlet)

    def send(self, content: str = "once", **extra: Any) -> Any:
        return self.client_post(
            "/json/messages",
            {"type": "stream", "to": "Verona", "topic": "keys", "content": content, **extra},
            # client_key is the beta's, and not in the API's documentation.
            intentionally_undocumented=True,
        )

    def test_a_repeat_answers_with_the_first_message_and_sends_nothing(self) -> None:
        first = self.assert_json_success(self.send(client_key="draft-1_a"))
        count = Message.objects.count()
        with self.capture_send_event_calls(expected_num_events=0):
            repeat = self.assert_json_success(self.send(client_key="draft-1_a"))
        self.assertEqual(repeat["id"], first["id"])
        self.assertEqual(Message.objects.count(), count)
        # Even when the repeat's content differs, the key wins.
        repeat = self.assert_json_success(self.send("changed", client_key="draft-1_a"))
        self.assertEqual(repeat["id"], first["id"])

        # A new key, or none, sends.
        second = self.assert_json_success(self.send(client_key="draft-2"))
        self.assertNotEqual(second["id"], first["id"])
        self.assert_json_success(self.send())
        self.assertEqual(Message.objects.count(), count + 2)
        self.assertEqual(
            set(
                MessageClientKey.objects.filter(user_profile=self.hamlet).values_list(
                    "client_key", "message_id"
                )
            ),
            {("draft-1_a", first["id"]), ("draft-2", second["id"])},
        )

    def test_a_repeat_starts_no_agent_run(self) -> None:
        realm = self.hamlet.realm
        agent = do_create_user(
            "armada-agent-bot@zulip.testserver",
            None,
            realm,
            "Agent",
            bot_type=UserProfile.EMBEDDED_BOT,
            bot_owner=self.example_user("iago"),
            acting_user=None,
        )
        Service.objects.create(name="armada", user_profile=agent, base_url="", token="")
        invalidate_armada_selected_bot_cache(realm.id)
        self.addCleanup(invalidate_armada_selected_bot_cache, realm.id)
        self.subscribe(agent, "Verona")

        def queues_after(**extra: Any) -> list[str]:
            with mock.patch("zerver.actions.message_send.queue_event_on_commit") as queued:
                self.assert_json_success(self.send("@**Agent** do the thing", **extra))
            return [call.args[0] for call in queued.call_args_list]

        self.assertIn("embedded_bots", queues_after(client_key="run-1"))
        self.assertEqual(queues_after(client_key="run-1"), [])

    def test_keys_are_each_persons_own(self) -> None:
        mine = self.assert_json_success(self.send(client_key="shared"))
        self.login("cordelia")
        theirs = self.assert_json_success(self.send(client_key="shared"))
        self.assertNotEqual(mine["id"], theirs["id"])

    def test_a_direct_message_is_sent_once_too(self) -> None:
        cordelia = self.example_user("cordelia")

        def send_direct() -> int:
            result = self.client_post(
                "/json/messages",
                {
                    "type": "direct",
                    "to": orjson.dumps([cordelia.id]).decode(),
                    "content": "between us",
                    "client_key": "dm-1",
                },
                intentionally_undocumented=True,
            )
            return self.assert_json_success(result)["id"]

        first = send_direct()
        self.assertEqual(send_direct(), first)
        self.assertEqual(
            Message.objects.filter(realm_id=cordelia.realm_id, content="between us").count(), 1
        )

    def test_a_key_lasts_a_day(self) -> None:
        first = self.assert_json_success(self.send(client_key="daily"))
        stale = MessageClientKey.objects.create(
            user_profile=self.hamlet, client_key="stale", message_id=first["id"]
        )
        with time_machine.travel(timezone_now() + timedelta(hours=23), tick=False):
            self.assertEqual(
                self.assert_json_success(self.send(client_key="daily"))["id"], first["id"]
            )
        with time_machine.travel(timezone_now() + timedelta(hours=25), tick=False):
            again = self.assert_json_success(self.send(client_key="daily"))
        self.assertNotEqual(again["id"], first["id"])
        # A claim clears the person's keys older than a day.
        self.assertFalse(MessageClientKey.objects.filter(id=stale.id).exists())
        self.assertEqual(
            list(
                MessageClientKey.objects.filter(user_profile=self.hamlet).values_list(
                    "message_id", flat=True
                )
            ),
            [again["id"]],
        )

    def test_a_refused_send_does_not_use_up_its_key(self) -> None:
        self.assert_json_error(
            self.client_post(
                "/json/messages",
                {
                    "type": "stream",
                    "to": "no such channel",
                    "topic": "keys",
                    "content": "x",
                    "client_key": "k",
                },
                intentionally_undocumented=True,
            ),
            "Channel 'no such channel' does not exist",
        )
        self.assertFalse(MessageClientKey.objects.exists())
        self.assert_json_success(self.send(client_key="k"))

    def test_malformed_keys_are_refused(self) -> None:
        for client_key in ["", "a" * 65, "with space", "dot.ted", "ключ"]:
            self.assert_json_error(
                self.send(client_key=client_key),
                "client_key must be 1 to 64 letters, digits, hyphens or underscores.",
            )
        self.assert_json_success(self.send(client_key="a" * 64))

    def test_a_repeat_racing_the_first_send(self) -> None:
        # The repeat looked before the first send committed, so it goes on
        # to claim the key and meets the first send's row.
        first = self.assert_json_success(self.send(client_key="race"))
        with mock.patch(
            "zerver.lib.armada_client_keys.first_message_id",
            side_effect=[None, first["id"]],
        ):
            repeat = self.assert_json_success(self.send(client_key="race"))
        self.assertEqual(repeat["id"], first["id"])

        # An integrity error that is not the key's is raised as it is.
        def broken_send() -> Any:
            raise IntegrityError("something else")

        with self.assertRaisesRegex(IntegrityError, "something else"):
            send_once(self.hamlet, "fresh", broken_send)
        self.assertIsNone(first_message_id(self.hamlet, "fresh"))
