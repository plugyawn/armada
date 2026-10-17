from dataclasses import dataclass
from typing import Any
from unittest.mock import Mock

from django.test import override_settings
from typing_extensions import override

from zerver.actions.message_delete import do_delete_messages
from zerver.lib.armada import (
    ARMADA_AGENT_NAME,
    ARMADA_EMBEDDED_BOT_SERVICE,
    ARMADA_EXPLICIT_MENTION_MESSAGE_KEY,
    ARMADA_ROUTED_REACTION_EMOJI_CODE,
    ARMADA_ROUTED_REACTION_EMOJI_NAME,
    ArmadaActiveSessionSummary,
    ArmadaAmbientDecision,
    ArmadaEmbeddedBotHandler,
    build_armada_active_session_summary_from_messages,
    get_armada_session_payload,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, Reaction, UserProfile

STREAM = "Denmark"
TOPIC = "flaky websocket test"
RUN = ArmadaAmbientDecision(addressee="agent", action="run", presentation="compact")
STEER = ArmadaAmbientDecision(addressee="agent", action="steer", presentation="compact")
PEOPLE = ArmadaAmbientDecision(addressee="other", action="ignore", presentation="compact")


def eyes_on(message_id: int) -> bool:
    return Reaction.objects.filter(
        message_id=message_id,
        emoji_name=ARMADA_ROUTED_REACTION_EMOJI_NAME,
        emoji_code=ARMADA_ROUTED_REACTION_EMOJI_CODE,
        reaction_type=Reaction.UNICODE_EMOJI,
    ).exists()


class FakeBotHandler:
    """The agent's bot, posting nothing: each reply records whether the
    reaction was already on the routed message when the run spoke."""

    def __init__(self, user_profile: UserProfile, message_id: int) -> None:
        self.user_profile = user_profile
        self.message_id = message_id
        self.replies: list[tuple[str, bool]] = []

    def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
        self.replies.append((response, eyes_on(self.message_id)))
        return {"id": 9000 + len(self.replies)}


@dataclass
class Handled:
    bot_handler: FakeBotHandler
    run_store: Mock
    dispatcher: Mock
    logs: list[str]


# The messages are handed to the agent's handler by each test, not by the
# embedded bots' queue.
@override_settings(EMBEDDED_BOTS_ENABLED=False)
class ArmadaRoutedReactionTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.person = self.example_user("hamlet")
        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            self.agent = self.create_test_bot(
                "onix",
                self.example_user("cordelia"),
                full_name=ARMADA_AGENT_NAME,
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=ARMADA_EMBEDDED_BOT_SERVICE,
            )
        self.subscribe(self.person, STREAM)
        self.subscribe(self.agent, STREAM)
        self.classified: list[str] = []

    def send(self, content: str) -> int:
        return self.send_stream_message(self.person, STREAM, content, TOPIC)

    def handle(
        self,
        message_id: int,
        content: str,
        decision: ArmadaAmbientDecision,
        *,
        explicit_mention: bool = False,
        run_store: Mock | None = None,
    ) -> Handled:
        session = build_armada_active_session_summary_from_messages(
            armada_session=get_armada_session_payload(stream=STREAM, topic=TOPIC),
            source="server",
            stream_exists=True,
            messages=[(message_id - 1, "Fixed the websocket wait.", True, ARMADA_AGENT_NAME)],
            response_policy="ambient",
        )

        def classifier(
            *,
            content: str,
            active_session: ArmadaActiveSessionSummary | None,
            active_run: bool,
            message: dict[str, Any],
            bot_handler: Any,
        ) -> ArmadaAmbientDecision:
            self.classified.append(content)
            # The handler changes the decision it is given.
            return decision.copy()

        if run_store is None:
            run_store = Mock(spec=["start", "record"])
            run_store.start.return_value = True
        handled = Handled(FakeBotHandler(self.agent, message_id), run_store, Mock(), [])
        message: dict[str, Any] = {
            "id": message_id,
            "type": "stream",
            "display_recipient": STREAM,
            "topic": TOPIC,
            "sender_id": self.person.id,
            "content": content,
        }
        if explicit_mention:
            message[ARMADA_EXPLICIT_MENTION_MESSAGE_KEY] = True
        handler = ArmadaEmbeddedBotHandler(
            active_session_loader=lambda message, bot_handler: session,
            worker_run_store=run_store,
            worker_dispatcher=handled.dispatcher,
            addressing_intent_classifier=classifier,
        )
        if explicit_mention or ARMADA_AGENT_NAME in content:
            # Explicit: no router is asked, so nothing is logged.
            with self.assertNoLogs("zerver.lib.armada", level="INFO"):
                handler.handle_message(message, handled.bot_handler)
        else:
            with self.assertLogs("zerver.lib.armada", level="INFO") as logs:
                handler.handle_message(message, handled.bot_handler)
            handled.logs = logs.output
        return handled

    def test_a_message_the_router_gives_the_agent_gets_eyes_before_the_run_speaks(self) -> None:
        message_id = self.send("still failing on my machine")
        with self.capture_send_event_calls(expected_num_events=1) as events:
            handled = self.handle(message_id, "still failing on my machine", RUN)

        self.assertEqual(self.classified, ["still failing on my machine"])
        reaction = Reaction.objects.get(message_id=message_id)
        self.assertEqual(
            (
                reaction.user_profile_id,
                reaction.emoji_name,
                reaction.emoji_code,
                reaction.reaction_type,
            ),
            (self.agent.id, "eyes", "1f440", Reaction.UNICODE_EMOJI),
        )
        # Everyone in the channel sees it land, through the usual event.
        self.assertEqual(
            {
                key: events[0]["event"][key]
                for key in ("type", "op", "user_id", "message_id", "emoji_name")
            },
            {
                "type": "reaction",
                "op": "add",
                "user_id": self.agent.id,
                "message_id": message_id,
                "emoji_name": "eyes",
            },
        )
        self.assertIn(self.person.id, events[0]["users"])
        # The run's first words come after it.
        self.assertEqual([seen for _reply, seen in handled.bot_handler.replies], [True])
        self.assert_length(handled.dispatcher.dispatch.call_args_list, 1)

    def test_a_message_the_router_gives_a_running_agent_gets_eyes_and_steers(self) -> None:
        run_store = Mock(spec=["start", "record", "record_event", "active_worker_run_message_id"])
        run_store.active_worker_run_message_id.return_value = 77
        message_id = self.send("also add type hints")
        handled = self.handle(message_id, "also add type hints", STEER, run_store=run_store)

        self.assertTrue(eyes_on(message_id))
        self.assertEqual(run_store.record_event.call_args.args[1:3], (77, "steering"))
        self.assertEqual(handled.dispatcher.mock_calls, [])

    def test_a_message_the_router_gives_the_people_gets_no_reaction(self) -> None:
        message_id = self.send("@**King Hamlet** did you see this?")
        handled = self.handle(message_id, "@**King Hamlet** did you see this?", PEOPLE)

        self.assertFalse(Reaction.objects.filter(message_id=message_id).exists())
        self.assertEqual(handled.bot_handler.replies, [])
        self.assertEqual(handled.dispatcher.mock_calls, [])

    def test_a_mention_or_the_agents_name_gets_no_reaction(self) -> None:
        for content, explicit_mention in [
            ("fix the flaky test", True),
            (f"Hey {ARMADA_AGENT_NAME}, can you fix the flaky test?", False),
        ]:
            with self.subTest(content=content):
                message_id = self.send(content)
                handled = self.handle(message_id, content, RUN, explicit_mention=explicit_mention)
                self.assertFalse(Reaction.objects.filter(message_id=message_id).exists())
                self.assert_length(handled.dispatcher.dispatch.call_args_list, 1)
        # Neither was a question for the router.
        self.assertEqual(self.classified, [])

    def test_a_message_routed_again_gets_one_reaction(self) -> None:
        message_id = self.send("still failing on my machine")
        self.handle(message_id, "still failing on my machine", RUN)
        run_store = Mock(spec=["start", "record"])
        run_store.start.return_value = False
        handled = self.handle(message_id, "still failing on my machine", RUN, run_store=run_store)

        self.assertEqual(Reaction.objects.filter(message_id=message_id).count(), 1)
        self.assertEqual([line.split(":")[0] for line in handled.logs], ["INFO"])

    def test_a_refused_run_still_gets_eyes(self) -> None:
        # The refusal notice says why no run starts.
        run_store = Mock(spec=["start", "record", "start_blocked_reason"])
        run_store.start_blocked_reason.return_value = "This topic already has an active run."
        message_id = self.send("still failing on my machine")
        handled = self.handle(message_id, "still failing on my machine", RUN, run_store=run_store)

        self.assertTrue(eyes_on(message_id))
        self.assertEqual(
            handled.bot_handler.replies,
            [
                (
                    f"{ARMADA_AGENT_NAME} won't start a run for this message. "
                    "This topic already has an active run.",
                    True,
                )
            ],
        )
        run_store.start.assert_not_called()
        self.assertEqual(handled.dispatcher.mock_calls, [])

    def test_a_reaction_that_cannot_be_added_is_logged_and_the_run_starts(self) -> None:
        message_id = self.send("still failing on my machine")
        do_delete_messages(
            self.person.realm, [Message.objects.get(id=message_id)], acting_user=None
        )

        handled = self.handle(message_id, "still failing on my machine", RUN)

        self.assertEqual(
            handled.logs[1],
            f"WARNING:zerver.lib.armada:Could not react to message {message_id}, "
            "which the router gave the agent: Invalid message(s)",
        )
        self.assert_length(handled.logs, 2)
        self.assert_length(handled.dispatcher.dispatch.call_args_list, 1)
