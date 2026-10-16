import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import time_machine
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.armada import ARMADA_EMBEDDED_BOT_SERVICE
from zerver.lib.armada_agent import ArmadaAppServerTurnContext, ArmadaCodexAppServerToolRunner
from zerver.lib.armada_later_tools import answer_armada_later_tool
from zerver.lib.armada_worker import ArmadaDatabaseWorkerRunStore
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import ScheduledMessage, UserProfile
from zerver.models.bots import AgentRun, AgentRunWait

TOPIC = "later"


class LaterToolsTest(ZulipTestCase):
    """The agent's tools for Later and Send when
    (zerver/lib/armada_later_tools.py): they act for the person whose
    message started the turn, and know that turn's run."""

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
        self.subscribe(self.agent, "Denmark")
        self.run_ = self.start_run("run the tests")

    def start_run(self, request: str) -> AgentRun:
        trigger_message_id = self.send_stream_message(
            self.hamlet, "Denmark", request, topic_name=TOPIC
        )
        return AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=self.agent,
            requesting_user=self.hamlet,
            trigger_message_id=trigger_message_id,
            stream_name="Denmark",
            topic_name=TOPIC,
            status=AgentRun.RUNNING,
        )

    def call(self, tool: str, arguments: object, asker_id: int | None = None) -> dict[str, object]:
        answer = answer_armada_later_tool(
            self.hamlet.id if asker_id is None else asker_id,
            self.run_.trigger_message_id,
            tool,
            arguments,
        )
        return dict(answer)

    def end_run(self, run: AgentRun) -> None:
        with self.assertLogs(level="INFO"), self.captureOnCommitCallbacks(execute=True):
            ArmadaDatabaseWorkerRunStore().record(
                SimpleNamespace(user_profile=self.agent),
                run.trigger_message_id,
                AgentRun.DONE,
                stream="Denmark",
                topic=TOPIC,
            )

    def test_later_brings_the_request_back_when_this_run_ends(self) -> None:
        answer = self.call("armada_later", {"note": "The tests finished."})

        self.assertEqual(
            answer,
            {
                "success": True,
                "text": (
                    f"Message {self.run_.trigger_message_id} is on King Hamlet's Later list. It "
                    f"comes back to them as a reminder when run {self.run_.id} ends. Say in "
                    "your reply that you put it there."
                ),
                "title": "Put a message on King Hamlet's Later list.",
            },
        )
        reminder = ScheduledMessage.objects.get(sender=self.hamlet)
        self.assertEqual(reminder.delivery_type, ScheduledMessage.REMIND)
        self.assertEqual(reminder.reminder_target_message_id, self.run_.trigger_message_id)
        self.assertEqual(reminder.reminder_note, "The tests finished.")
        self.assertEqual(AgentRunWait.objects.get(scheduled_message=reminder).run, self.run_)

        self.end_run(self.run_)
        reminder.refresh_from_db()
        self.assertTrue(reminder.delivered)

    def test_later_can_wait_for_another_run_or_for_a_while(self) -> None:
        other_run = self.start_run("deploy it")
        answer = self.call("armada_later", {"run_id": other_run.id})
        self.assertTrue(answer["success"])
        self.assertEqual(AgentRunWait.objects.get().run, other_run)

        now = timezone_now()
        with time_machine.travel(now, tick=False):
            answer = self.call(
                "armada_later", {"message_id": other_run.trigger_message_id, "in_minutes": 30}
            )
        self.assertIn(f"{now + timedelta(minutes=30):%Y-%m-%d %H:%M} UTC", str(answer["text"]))
        timed = ScheduledMessage.objects.get(
            sender=self.hamlet, agent_run_wait__isnull=True, delivered=False
        )
        self.assertEqual(timed.scheduled_timestamp, now + timedelta(minutes=30))
        self.assertEqual(timed.reminder_target_message_id, other_run.trigger_message_id)

    def test_send_when_posts_as_the_agent_when_the_run_ends(self) -> None:
        other_run = self.start_run("deploy it")
        answer = self.call(
            "armada_send_when",
            {"content": "The deploy landed; the PR is next.", "run_id": other_run.id},
        )

        scheduled_message = ScheduledMessage.objects.get(sender=self.agent)
        self.assertEqual(
            answer,
            {
                "success": True,
                "text": (
                    f"Scheduled message {scheduled_message.id} is posted in #Denmark > later "
                    f"when run {other_run.id} ends."
                ),
                "title": "Will post in #Denmark > later when the run ends.",
            },
        )
        self.assertEqual(AgentRunWait.objects.get().run, other_run)

        self.end_run(other_run)
        scheduled_message.refresh_from_db()
        assert scheduled_message.delivered_message is not None
        self.assertEqual(scheduled_message.delivered_message.sender, self.agent)
        self.assertEqual(
            scheduled_message.delivered_message.content, "The deploy landed; the PR is next."
        )
        self.assertEqual(scheduled_message.delivered_message.topic_name(), TOPIC)

    def test_send_when_goes_where_the_person_asks(self) -> None:
        self.subscribe(self.hamlet, "Verona")
        self.subscribe(self.agent, "Verona")
        self.assertTrue(
            self.call("armada_send_when", {"content": "done", "topic": "status"})["success"]
        )
        self.assertTrue(
            self.call(
                "armada_send_when", {"content": "done", "channel": "Verona", "topic": "status"}
            )["success"]
        )
        self.assertEqual(
            sorted(
                (m.stream.name if m.stream else "", m.topic_name())
                for m in ScheduledMessage.objects.filter(sender=self.agent)
            ),
            [("Denmark", "status"), ("Verona", "status")],
        )

    def test_refusals_are_what_the_person_would_be_told(self) -> None:
        # A private channel the agent may post in and the person who
        # asked may not: the person's own check refuses it.
        self.make_stream("hidden", invite_only=True)
        self.subscribe(self.agent, "hidden")
        done = self.start_run("done already")
        done.status = AgentRun.DONE
        done.save(update_fields=["status"])
        bot = self.example_user("default_bot")
        scheduled_before = ScheduledMessage.objects.count()
        for asker_id, tool, arguments, refusal in [
            (bot.id, "armada_later", {}, "No person asked for this turn, so it has no Later list."),
            (self.hamlet.id, "armada_later", "{", "The tool's arguments are not JSON."),
            (self.hamlet.id, "armada_later", {"run_id": done.id}, "This run has already ended."),
            (
                self.hamlet.id,
                "armada_later",
                {"run_id": done.id, "in_minutes": 5},
                "Give run_id or in_minutes, not both.",
            ),
            (
                self.hamlet.id,
                "armada_later",
                {"in_minutes": 0},
                "in_minutes must be from 1 to 10080.",
            ),
            (
                self.hamlet.id,
                "armada_later",
                {"message_id": 10**9},
                "Invalid message(s)",
            ),
            (self.hamlet.id, "armada_later", {"note": 7}, "note must be a string."),
            (
                self.hamlet.id,
                "armada_send_when",
                {"content": "x", "channel": "hidden", "topic": "t"},
                "Not authorized to send to channel 'hidden'",
            ),
            (
                self.hamlet.id,
                "armada_send_when",
                {"content": "x", "channel": "Verona"},
                "Give the topic with the channel.",
            ),
            (self.hamlet.id, "armada_send_when", {}, "content must be a string."),
        ]:
            self.assertEqual(
                self.call(tool, arguments, asker_id),
                {"success": False, "text": refusal, "title": refusal},
            )
        self.assertEqual(ScheduledMessage.objects.count(), scheduled_before)

    def test_a_turn_with_no_person_or_no_run_has_nothing_to_wait_for(self) -> None:
        answer = answer_armada_later_tool(None, self.run_.trigger_message_id, "armada_later", {})
        self.assertEqual(answer["text"], "No person asked for this turn, so it has no Later list.")
        answer = answer_armada_later_tool(self.hamlet.id, None, "armada_later", {})
        self.assertEqual(answer["text"], "This turn is not part of a run.")

    def test_a_tool_thread_closes_its_database_connection(self) -> None:
        with mock.patch("zerver.lib.armada_page_tools.connection") as connection:
            connection.in_atomic_block = False
            self.call("armada_later", {"in_minutes": 5})
        connection.close.assert_called_once_with()


class LaterToolCallsTest(ZulipTestCase):
    """The runner offers the Later tools to a new thread and answers
    Codex's calls of them for the person and the run of the turn."""

    def make_runner(self) -> ArmadaCodexAppServerToolRunner:
        return ArmadaCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=None,
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="on-request",
            timeout_seconds=5,
            output_limit=1000,
        )

    def test_a_new_thread_is_offered_the_later_tools(self) -> None:
        names = [tool["name"] for tool in self.make_runner().thread_params()["dynamicTools"]]
        self.assertEqual(names[-2:], ["armada_later", "armada_send_when"])

    def test_a_call_is_answered_for_the_turns_person_and_run(self) -> None:
        hamlet = self.example_user("hamlet")
        agent = self.create_test_bot(
            "onix",
            hamlet,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        trigger_message_id = self.send_stream_message(hamlet, "Denmark", "run the tests")
        run = AgentRun.objects.create(
            realm=hamlet.realm,
            bot_profile=agent,
            requesting_user=hamlet,
            trigger_message_id=trigger_message_id,
            stream_name="Denmark",
            topic_name="test",
            status=AgentRun.RUNNING,
        )
        result, error, event = self.make_runner().answer_for_server_request(
            "item/tool/call",
            {
                "tool": "armada_later",
                "callId": "call-1",
                "namespace": None,
                "arguments": json.dumps({}),
            },
            turn_context=ArmadaAppServerTurnContext(
                thread_id="thread-1",
                event_callback=None,
                user_input_provider=None,
                is_main=True,
                requesting_user_id=hamlet.id,
                trigger_message_id=trigger_message_id,
            ),
        )
        self.assertIsNone(error)
        assert result is not None
        self.assertTrue(result["success"])
        self.assertEqual(AgentRunWait.objects.get().run, run)
        self.assertEqual(
            event,
            {
                "kind": "tool",
                "status": "done",
                "title": f"Put a message on {hamlet.full_name}'s Later list.",
                "item_id": "call-1",
                "metadata": {"tool_name": "armada_later", "human_tool_result": True},
            },
        )
