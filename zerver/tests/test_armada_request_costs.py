"""What Armada's hot paths cost: queries per request or per event.

Each test pins a cost that a topic open, a run event or a worktree read
pays many times a minute, so that a change that adds a query to one of
them has to change a number here.
"""

from unittest.mock import patch

from typing_extensions import override

from zerver.actions.armada import (
    ARMADA_RUN_RECIPIENTS_SECONDS,
    armada_run_recipient_ids,
    armada_run_recipients,
    send_armada_run_event,
)
from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ARMADA_EMBEDDED_BOT_SERVICE,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile
from zerver.models.bots import AgentRun, AgentRunEvent


class ArmadaRequestCostTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        armada_run_recipients.clear()
        self.addCleanup(armada_run_recipients.clear)
        self.owner = self.example_user("hamlet")
        self.subscribe(self.owner, ARMADA_DEFAULT_STREAM)
        self.agent = self.create_test_bot(
            "armada-costs",
            self.owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )

    def make_run(self, status: str = AgentRun.RUNNING) -> AgentRun:
        trigger_message_id = self.send_stream_message(
            self.owner,
            ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            content="check the repository",
        )
        return AgentRun.objects.create(
            realm=self.owner.realm,
            bot_profile=self.agent,
            requesting_user=self.owner,
            trigger_message_id=trigger_message_id,
            status=status,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
        )

    def test_live_run_events_reuse_their_recipients(self) -> None:
        run = self.make_run()
        event = AgentRunEvent.objects.create(run=run, event_type="transcript", summary="Reading")
        with patch("zerver.actions.armada.send_event_on_commit") as send_event:
            # The trigger message, its channel, the subscribers and the
            # people holding the message.
            with self.assert_database_query_count(4):
                send_armada_run_event(run, event)
            with self.assert_database_query_count(0):
                send_armada_run_event(run, event)
            self.assertNotIn(self.example_user("cordelia").id, send_event.call_args.args[2])

            self.subscribe(self.example_user("cordelia"), ARMADA_DEFAULT_STREAM)
            # Once the recipients are older than they may be, they are
            # read again.
            expires_at, user_ids = armada_run_recipients[run.id]
            armada_run_recipients[run.id] = (expires_at - ARMADA_RUN_RECIPIENTS_SECONDS, user_ids)
            with self.assert_database_query_count(4):
                send_armada_run_event(run, event)
            self.assertIn(self.example_user("cordelia").id, send_event.call_args.args[2])
        self.assertEqual(send_event.call_count, 3)

    def test_finished_runs_are_not_kept(self) -> None:
        run = self.make_run()
        armada_run_recipient_ids(run)
        self.assertIn(run.id, armada_run_recipients)

        run.status = AgentRun.DONE
        with self.assert_database_query_count(4):
            recipients = armada_run_recipient_ids(run)
        self.assertIn(self.owner.id, recipients)
        self.assertNotIn(run.id, armada_run_recipients)
        with self.assert_database_query_count(4):
            armada_run_recipient_ids(run)

    def test_kept_recipients_are_bounded(self) -> None:
        runs = [self.make_run() for _ in range(3)]
        with patch("zerver.actions.armada.ARMADA_RUN_RECIPIENTS_LIMIT", 2):
            for run in runs:
                armada_run_recipient_ids(run)
        self.assertEqual(list(armada_run_recipients), [runs[1].id, runs[2].id])

    def test_run_without_its_trigger_message_has_no_recipients(self) -> None:
        run = self.make_run()
        run.trigger_message_id = 0
        with patch("zerver.actions.armada.send_event_on_commit") as send_event:
            send_armada_run_event(run, AgentRunEvent(run=run, event_type="transcript"))
        send_event.assert_not_called()

        side_run = self.make_run()
        side_run.session_kind = AgentRun.SIDE
        self.assertEqual(armada_run_recipient_ids(side_run), [self.owner.id])
