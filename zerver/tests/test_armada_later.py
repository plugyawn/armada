from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

from django.db import transaction
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.reminders import do_delete_reminder, schedule_reminder_for_message
from zerver.actions.scheduled_messages import (
    check_schedule_message,
    delete_scheduled_message,
    edit_scheduled_message,
    try_deliver_one_scheduled_message,
)
from zerver.lib.armada import ARMADA_EMBEDDED_BOT_SERVICE, ArmadaWorkerRunStatus
from zerver.lib.armada_later import access_run_to_wait_for
from zerver.lib.armada_worker import ArmadaDatabaseWorkerRunStore
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, ScheduledMessage, UserProfile
from zerver.models.bots import AgentRun, AgentRunWait
from zerver.models.clients import get_client
from zerver.models.streams import get_stream

TOPIC = "later"


class AgentRunWaitTest(ZulipTestCase):
    """Later and Send when (zerver/lib/armada_later.py): a scheduled
    message or a reminder that waits for an agent run is sent once, when
    the run first reaches done, failed or cancelled."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.agent = self.create_test_bot(
            "onix",
            self.hamlet,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.store = ArmadaDatabaseWorkerRunStore()
        self.handler = SimpleNamespace(user_profile=self.agent)

    def start_run(self, status: str = AgentRun.RUNNING, stream_name: str = "Denmark") -> AgentRun:
        trigger_message_id = self.send_stream_message(
            self.hamlet, stream_name, "run the tests", topic_name=TOPIC
        )
        return AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=self.agent,
            requesting_user=self.hamlet,
            trigger_message_id=trigger_message_id,
            stream_name=stream_name,
            topic_name=TOPIC,
            status=status,
        )

    def send_when_run_ends(
        self, run: AgentRun, content: str = "after this lands, open the PR"
    ) -> ScheduledMessage:
        stream = get_stream("Denmark", self.hamlet.realm)
        scheduled_message_id = check_schedule_message(
            self.hamlet,
            get_client("test suite"),
            "stream",
            [stream.id],
            TOPIC,
            content,
            None,
            self.hamlet.realm,
            agent_run=run,
        )
        return ScheduledMessage.objects.get(id=scheduled_message_id)

    def remind_when_run_ends(self, run: AgentRun, message_id: int) -> ScheduledMessage:
        reminder_id = schedule_reminder_for_message(
            self.hamlet, get_client("test suite"), message_id, None, "", agent_run=run
        )
        return ScheduledMessage.objects.get(id=reminder_id)

    def finish(self, run: AgentRun, status: ArmadaWorkerRunStatus) -> None:
        """Move the run into status as the worker does, and run what
        waits for the transaction to commit."""
        with self.captureOnCommitCallbacks(execute=True):
            self.store.record(
                self.handler, run.trigger_message_id, status, stream="Denmark", topic=TOPIC
            )
        run.refresh_from_db()
        self.assertEqual(run.status, status)

    def cancel(self, run: AgentRun) -> None:
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic(savepoint=False):
            locked = AgentRun.objects.select_for_update(no_key=True).get(id=run.id)
            self.assertTrue(self.store.cancel_locked_run(locked, summary="Cancelled."))

    @contextmanager
    def assert_sends(self, *scheduled_messages: ScheduledMessage) -> Iterator[None]:
        """The block delivers these, in this order, as Zulip's delivery
        logs it, and nothing else."""
        with self.assertLogs(level="INFO") as logs:
            yield
        expected = []
        for scheduled_message in scheduled_messages:
            scheduled_message.refresh_from_db()
            expected.append(
                f"INFO:root:Sending scheduled message {scheduled_message.id} with date "
                f"{scheduled_message.scheduled_timestamp} (sender: {self.hamlet.id})"
            )
        self.assertEqual(logs.output, expected)

    def assert_sent(self, scheduled_message: ScheduledMessage) -> Message:
        scheduled_message.refresh_from_db()
        self.assertTrue(scheduled_message.delivered)
        self.assertFalse(scheduled_message.failed)
        assert scheduled_message.delivered_message is not None
        return scheduled_message.delivered_message

    def test_a_waiting_message_is_not_due_at_any_time(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)

        self.assertEqual(scheduled_message.scheduled_timestamp, AgentRunWait.WAITING_TIMESTAMP)
        self.assertEqual(AgentRunWait.objects.get(scheduled_message=scheduled_message).run, run)
        # The delivery worker, which sends what is due, leaves it alone.
        self.assertFalse(try_deliver_one_scheduled_message())

    def test_a_waiting_message_is_sent_when_the_run_is_done(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)

        with self.assert_sends(scheduled_message):
            self.finish(run, AgentRun.DONE)

        message = self.assert_sent(scheduled_message)
        self.assertEqual(message.content, "after this lands, open the PR")
        self.assertEqual(message.sender, self.hamlet)
        self.assertEqual(message.topic_name(), TOPIC)
        self.assertFalse(AgentRunWait.objects.exists())
        # Nothing is left for the delivery worker.
        self.assertFalse(try_deliver_one_scheduled_message())

    def test_a_waiting_message_is_sent_when_the_run_fails(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)

        with self.assert_sends(scheduled_message):
            self.finish(run, AgentRun.FAILED)

        self.assert_sent(scheduled_message)

    def test_a_waiting_message_is_sent_when_the_run_is_cancelled(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)

        with self.assert_sends(scheduled_message):
            self.cancel(run)

        self.assert_sent(scheduled_message)

    def test_a_queued_run_that_is_cancelled_releases_its_messages(self) -> None:
        run = self.start_run(status=AgentRun.QUEUED)
        scheduled_message = self.send_when_run_ends(run)

        with self.assert_sends(scheduled_message):
            self.cancel(run)

        self.assert_sent(scheduled_message)

    def test_only_the_messages_waiting_for_that_run_are_sent(self) -> None:
        run = self.start_run()
        other_run = self.start_run()
        mine = self.send_when_run_ends(run, "first")
        other = self.send_when_run_ends(other_run, "second")

        with self.assert_sends(mine):
            self.finish(run, AgentRun.DONE)

        self.assert_sent(mine)
        other.refresh_from_db()
        self.assertFalse(other.delivered)
        self.assertEqual(other.scheduled_timestamp, AgentRunWait.WAITING_TIMESTAMP)
        self.assertEqual(AgentRunWait.objects.get().scheduled_message, other)

    def test_a_run_that_fails_and_finishes_later_sends_nothing_twice(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)
        with self.assert_sends(scheduled_message):
            self.finish(run, AgentRun.FAILED)
        message = self.assert_sent(scheduled_message)

        # Requeued explicitly, as a person's new request does, and then
        # finished: the message it held was sent once, and is not again.
        self.assertTrue(
            self.store.start(self.handler, run.trigger_message_id, stream="Denmark", topic=TOPIC)
        )
        self.finish(run, AgentRun.RUNNING)
        self.finish(run, AgentRun.DONE)
        # The same status recorded twice is not a second end either.
        self.finish(run, AgentRun.DONE)

        self.assertEqual(
            Message.objects.filter(
                realm_id=self.hamlet.realm_id, sender=self.hamlet, content=message.content
            ).count(),
            1,
        )

    def test_the_delivery_worker_finds_nothing_left_to_send(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)
        # The run ends, and the message is made due, but the immediate
        # delivery after the commit does not happen (the process stops).
        with self.captureOnCommitCallbacks(execute=False):
            self.store.record(
                self.handler, run.trigger_message_id, AgentRun.DONE, stream="Denmark", topic=TOPIC
            )

        # The delivery worker sends it on its next look, once.
        with self.assert_sends(scheduled_message):
            self.assertTrue(try_deliver_one_scheduled_message())
            self.assertFalse(try_deliver_one_scheduled_message())
        self.assert_sent(scheduled_message)

    def test_a_deleted_message_is_never_sent(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)

        delete_scheduled_message(self.hamlet, scheduled_message.id)

        # Its wait went with it.
        self.assertFalse(AgentRunWait.objects.exists())
        self.finish(run, AgentRun.DONE)
        self.assertFalse(
            Message.objects.filter(
                realm_id=self.hamlet.realm_id, content="after this lands, open the PR"
            ).exists()
        )

    def test_a_message_given_a_time_no_longer_waits_for_the_run(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)
        tomorrow = timezone_now() + timedelta(days=1)

        edit_scheduled_message(
            self.hamlet,
            get_client("test suite"),
            scheduled_message.id,
            None,
            None,
            None,
            None,
            tomorrow,
            self.hamlet.realm,
        )

        self.assertFalse(AgentRunWait.objects.exists())
        self.finish(run, AgentRun.DONE)
        scheduled_message.refresh_from_db()
        self.assertFalse(scheduled_message.delivered)
        self.assertEqual(scheduled_message.scheduled_timestamp, tomorrow)

    def test_a_new_time_or_new_words_keep_or_drop_the_wait(self) -> None:
        run = self.start_run()
        scheduled_message = self.send_when_run_ends(run)

        # New words keep it waiting for the run.
        edit_scheduled_message(
            self.hamlet,
            get_client("test suite"),
            scheduled_message.id,
            None,
            None,
            None,
            "after this lands, open the PR and ask Iago",
            None,
            self.hamlet.realm,
        )

        with self.assert_sends(scheduled_message):
            self.finish(run, AgentRun.DONE)
        message = self.assert_sent(scheduled_message)
        self.assertEqual(message.content, "after this lands, open the PR and ask Iago")

    def test_a_reminder_returns_when_the_run_ends(self) -> None:
        notification_bot = self.notification_bot(self.hamlet.realm)
        run = self.start_run()
        reminder = self.remind_when_run_ends(run, run.trigger_message_id)
        self.assertEqual(reminder.scheduled_timestamp, AgentRunWait.WAITING_TIMESTAMP)

        with self.assert_sends(reminder):
            self.finish(run, AgentRun.DONE)

        delivered = self.assert_sent(reminder)
        self.assertEqual(delivered.sender, notification_bot)
        self.assertIn("You requested a reminder for the following message.", delivered.content)
        self.assertIn("run the tests", delivered.content)

    def test_a_deleted_reminder_never_returns(self) -> None:
        run = self.start_run()
        reminder = self.remind_when_run_ends(run, run.trigger_message_id)

        do_delete_reminder(self.hamlet, reminder)

        self.assertFalse(AgentRunWait.objects.exists())
        self.cancel(run)
        notification_bot = self.notification_bot(self.hamlet.realm)
        self.assertFalse(
            Message.objects.filter(realm_id=self.hamlet.realm_id, sender=notification_bot).exists()
        )

    def test_only_a_live_run_i_can_see_can_be_waited_for(self) -> None:
        run = self.start_run()
        self.assertEqual(access_run_to_wait_for(self.hamlet, run.id), run)

        done = self.start_run(status=AgentRun.DONE)
        with self.assertRaisesRegex(JsonableError, "This run has already ended."):
            access_run_to_wait_for(self.hamlet, done.id)

        # A run in a private channel I am not in is not there for me.
        self.make_stream("hidden", invite_only=True)
        self.subscribe(self.hamlet, "hidden")
        hidden = self.start_run(stream_name="hidden")
        with self.assertRaisesRegex(JsonableError, "Invalid channel name 'hidden'"):
            access_run_to_wait_for(self.example_user("cordelia"), hidden.id)
        with self.assertRaisesRegex(JsonableError, "Run not found."):
            access_run_to_wait_for(self.hamlet, hidden.id + 1000)
