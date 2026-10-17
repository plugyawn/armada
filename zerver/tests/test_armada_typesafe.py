import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import requests
import responses
from django.test import override_settings

from zerver.lib.armada import (
    ARMADA_AGENT_NAME,
    ArmadaActiveSessionSummary,
    ArmadaAmbientAction,
    ArmadaAmbientAddressee,
    ArmadaAmbientDecision,
    ArmadaEmbeddedBotHandler,
    build_armada_active_session_summary_from_messages,
    get_armada_session_payload,
    is_armada_request_message,
)
from zerver.lib.armada_agent import (
    ARMADA_ADDRESSING_CONTEXT_MESSAGE_CHARACTERS,
    ARMADA_ADDRESSING_CONTEXT_MESSAGES,
    ARMADA_ADDRESSING_NEW_MESSAGE_CHARACTERS,
    ArmadaAgentRuntimeError,
    ArmadaTypeSafeAddressingIntentClassifier,
)
from zerver.lib.armada_typesafe import (
    armada_typesafe_agent_context,
    armada_typesafe_agent_probability,
    build_armada_typesafe_addressing_request,
)
from zerver.lib.armada_worker import build_armada_addressing_intent_classifier_from_settings
from zerver.lib.test_classes import ZulipTestCase

LABELLED_CHATS = os.path.join(os.path.dirname(__file__), "fixtures/armada_addressing_cases.json")


def typesafe_answer(probabilities: object) -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "addressee": {
                "type": "choice",
                "choice": "agent",
                "confidence": 0.74,
                "probabilities": probabilities,
            }
        },
        "usage": {"input_tokens": 683, "output_tokens": 34},
    }


class ArmadaTypeSafeRequestTest(ZulipTestCase):
    def test_the_chat_is_the_state_and_the_agent_is_described_with_the_question(self) -> None:
        request = build_armada_typesafe_addressing_request(
            agent_name=ARMADA_AGENT_NAME,
            model="jev-latest",
            channel="ml-training",
            topic="resnet baseline",
            agent_run_in_progress=False,
            recent_messages=[
                {
                    "message_id": 10,
                    "sender": "Kenji",
                    "sender_is_bot": False,
                    "content": "train the baseline",
                },
                {
                    "message_id": 11,
                    "sender": ARMADA_AGENT_NAME,
                    "sender_is_bot": True,
                    "content": "Done: 76.2%.",
                },
            ],
            new_message_sender="Kenji",
            new_message="can you rerun it with lr 3e-4?",
        )

        self.assertEqual(request["model"], "jev-latest")
        self.assertEqual(
            request["state"],
            {
                "channel": "ml-training",
                "topic": "resnet baseline",
                "agent_run_in_progress": False,
                "recent_messages": [
                    {"sender": "Kenji", "is_bot": False, "text": "train the baseline"},
                    {"sender": ARMADA_AGENT_NAME, "is_bot": True, "text": "Done: 76.2%."},
                ],
                "new_message": {"sender": "Kenji", "text": "can you rerun it with lr 3e-4?"},
            },
        )
        self.assertEqual(list(request["questions"]), ["addressee"])
        question = request["questions"]["addressee"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(
            question["instructions"]["context"], armada_typesafe_agent_context(ARMADA_AGENT_NAME)
        )
        self.assertIn(f"agent named {ARMADA_AGENT_NAME}", question["instructions"]["context"])
        self.assertEqual(
            question["instructions"]["question"],
            f"Is `new_message` addressed to {ARMADA_AGENT_NAME}, so that {ARMADA_AGENT_NAME} "
            f"should reply (a request, a question, or anything said to {ARMADA_AGENT_NAME}, "
            "such as a greeting or a follow-up), or is it meant for the people in the "
            "conversation?",
        )
        # The safe answer is listed first.
        self.assertEqual(list(question["criteria"]), ["people", "agent"])

    def test_the_agent_is_named_by_its_constant(self) -> None:
        request = build_armada_typesafe_addressing_request(
            agent_name="Tester",
            model="jev-latest",
            channel="",
            topic="",
            agent_run_in_progress=True,
            recent_messages=[],
            new_message_sender="",
            new_message="",
        )

        self.assertNotIn(ARMADA_AGENT_NAME, json.dumps(request))
        self.assertIn("Tester", request["questions"]["addressee"]["criteria"]["agent"])
        self.assertIs(request["state"]["agent_run_in_progress"], True)

    def test_the_agent_probability_is_read_from_the_answer(self) -> None:
        self.assertEqual(
            armada_typesafe_agent_probability(typesafe_answer({"people": 0.13, "agent": 0.87})),
            0.87,
        )
        self.assertEqual(
            armada_typesafe_agent_probability(typesafe_answer({"people": 0, "agent": 1})), 1.0
        )

    def test_an_answer_without_a_usable_probability_is_refused(self) -> None:
        unusable: list[object] = [
            None,
            "not an answer",
            {"model": "jev-1.13.0"},
            {"answers": []},
            {"answers": {"other_question": {"probabilities": {"agent": 0.9}}}},
            {"answers": {"addressee": "agent"}},
            typesafe_answer(None),
            typesafe_answer({"people": 0.1, "yes": 0.9}),
            typesafe_answer({"people": 0.1, "agent": "0.9"}),
            typesafe_answer({"people": 0.0, "agent": True}),
            typesafe_answer({"people": -0.5, "agent": 1.5}),
            typesafe_answer({"people": 1.1, "agent": -0.1}),
        ]
        for answer in unusable:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                armada_typesafe_agent_probability(answer)


class ArmadaTypeSafeLabelledChatsTest(ZulipTestCase):
    def test_the_labelled_chats_are_what_the_server_would_send(self) -> None:
        # tools/armada/typesafe_addressing_eval sends these without the
        # server's cuts, so each must be within them already.
        with open(LABELLED_CHATS) as f:
            cases = json.load(f)["cases"]

        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertEqual({case["label"] for case in cases}, {"agent", "people"})
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertLessEqual(len(case["messages"]), ARMADA_ADDRESSING_CONTEXT_MESSAGES)
                for sender, is_bot, text in case["messages"]:
                    # Other bots speak too, such as the ops check's alerts.
                    self.assertTrue(is_bot or sender != "{agent}")
                    self.assertLessEqual(
                        len(text.replace("{agent}", ARMADA_AGENT_NAME)),
                        ARMADA_ADDRESSING_CONTEXT_MESSAGE_CHARACTERS,
                    )
                sender, text = case["new_message"]
                self.assertNotEqual(sender, "{agent}")
                self.assertLessEqual(
                    len(text.replace("{agent}", ARMADA_AGENT_NAME)),
                    ARMADA_ADDRESSING_NEW_MESSAGE_CHARACTERS,
                )
                self.assertIsInstance(case["agent_run_in_progress"], bool)
                self.assertNotIn(ARMADA_AGENT_NAME, json.dumps(case))

    def test_the_name_rule_decides_only_chats_meant_for_the_agent(self) -> None:
        # A message that says the agent's name to it never reaches the
        # router; on the labelled chats that rule starts no run for people.
        with open(LABELLED_CHATS) as f:
            cases = json.load(f)["cases"]

        named = {
            case["id"]: case["label"]
            for case in cases
            if is_armada_request_message(
                case["new_message"][1].replace("{agent}", ARMADA_AGENT_NAME)
            )
        }
        self.assertEqual(
            named,
            {
                "request-name-without-mention": "agent",
                "mixed-request-naming-a-person": "agent",
                "greeting-after-bot-alerts": "agent",
                "greeting-hi": "agent",
                "greeting-morning-with-request": "agent",
                "vocative-name-last": "agent",
                "vocative-name-alone": "agent",
            },
        )


TYPESAFE_URL = "https://typesafe.example/v1/systemone"
# Built at run time, so that this file holds no credential-shaped line.
TYPESAFE_KEY = "typesafe-" + "k" * 24
CHANNEL = "ml-training"
TOPIC = "resnet baseline"
MESSAGE = {
    "id": 212,
    "type": "stream",
    "display_recipient": CHANNEL,
    "topic": TOPIC,
    "sender_full_name": "Kenji",
    "content": "can you rerun it with lr 3e-4?",
}


def typesafe_classifier(
    *, api_key: str | None = TYPESAFE_KEY, threshold: float = 0.9
) -> ArmadaTypeSafeAddressingIntentClassifier:
    return ArmadaTypeSafeAddressingIntentClassifier(
        api_key=api_key,
        model="jev-latest",
        url=TYPESAFE_URL,
        threshold=threshold,
        timeout_seconds=3,
    )


def topic_session() -> ArmadaActiveSessionSummary:
    return build_armada_active_session_summary_from_messages(
        armada_session=get_armada_session_payload(stream=CHANNEL, topic=TOPIC),
        source="server",
        stream_exists=True,
        messages=[
            (210, "@**Onix** train the resnet50 baseline", False, "Kenji"),
            (211, "Done. Final val accuracy 76.2%.", True, ARMADA_AGENT_NAME),
            (212, "can you rerun it with lr 3e-4?", False, "Kenji"),
        ],
        response_policy="ambient",
    )


def decision(
    addressee: ArmadaAmbientAddressee, action: ArmadaAmbientAction
) -> ArmadaAmbientDecision:
    return {"addressee": addressee, "action": action, "presentation": "compact"}


def classify(
    classifier: ArmadaTypeSafeAddressingIntentClassifier,
    *,
    content: str = "can you rerun it with lr 3e-4?",
    active_session: ArmadaActiveSessionSummary | None = None,
    active_run: bool = False,
) -> ArmadaAmbientDecision:
    return classifier(
        content=content,
        active_session=active_session,
        active_run=active_run,
        message=MESSAGE,
        bot_handler=object(),
    )


class ArmadaTypeSafeAddressingRouterTest(ZulipTestCase):
    @responses.activate
    def test_the_request_is_the_topic_seen_as_the_gemini_router_sees_it(self) -> None:
        responses.add(
            responses.POST, TYPESAFE_URL, json=typesafe_answer({"people": 0.03, "agent": 0.97})
        )
        classifier = typesafe_classifier()

        with self.assertLogs("zerver.lib.armada_agent", level="INFO") as logs:
            routed = classify(
                classifier,
                content="  can you rerun it with lr 3e-4?\n",
                active_session=topic_session(),
            )

        self.assertEqual(routed, decision("agent", "run"))
        self.assertEqual(
            logs.output,
            [
                "INFO:zerver.lib.armada_agent:TypeSafe addressing: message_id=212 "
                "p_agent=0.97 threshold=0.90 model=jev-1.13.0"
            ],
        )
        self.assert_length(responses.calls, 1)
        sent = responses.calls[0].request
        self.assertEqual(sent.url, TYPESAFE_URL)
        self.assertEqual(sent.headers["Authorization"], f"Bearer {TYPESAFE_KEY}")
        self.assertEqual(sent.headers["Content-Type"], "application/json")
        assert sent.body is not None
        # The new message is not among the recent ones, and is trimmed.
        self.assertEqual(
            json.loads(sent.body),
            build_armada_typesafe_addressing_request(
                agent_name=ARMADA_AGENT_NAME,
                model="jev-latest",
                channel=CHANNEL,
                topic=TOPIC,
                agent_run_in_progress=False,
                recent_messages=[
                    {
                        "sender": "Kenji",
                        "sender_is_bot": False,
                        "content": "@**Onix** train the resnet50 baseline",
                    },
                    {
                        "sender": ARMADA_AGENT_NAME,
                        "sender_is_bot": True,
                        "content": "Done. Final val accuracy 76.2%.",
                    },
                ],
                new_message_sender="Kenji",
                new_message="can you rerun it with lr 3e-4?",
            ),
        )
        self.assertEqual(classifier.session.get_adapter(TYPESAFE_URL).timeout, 3)  # type: ignore[attr-defined] # OutgoingHTTPAdapter

    @responses.activate
    def test_the_agent_acts_only_at_the_threshold(self) -> None:
        run = decision("agent", "run")
        steer = decision("agent", "steer")
        uncertain = decision("uncertain", "ignore")
        people = decision("other", "ignore")
        cases: list[tuple[float, float, bool, ArmadaAmbientDecision]] = [
            # (the agent's probability, the threshold, a run is going, the decision)
            (0.9, 0.9, False, run),
            (1.0, 0.9, True, steer),
            (0.89, 0.9, False, uncertain),
            (0.5, 0.9, True, uncertain),
            (0.49, 0.9, False, people),
            (0.0, 0.9, False, people),
            (0.94, 0.95, False, uncertain),
            (0.6, 0.5, False, run),
        ]
        for agent_probability, threshold, active_run, expected in cases:
            responses.upsert(
                responses.POST,
                TYPESAFE_URL,
                json=typesafe_answer({"people": 1 - agent_probability, "agent": agent_probability}),
            )
            with (
                self.subTest(p_agent=agent_probability, threshold=threshold),
                self.assertLogs("zerver.lib.armada_agent", level="INFO"),
            ):
                self.assertEqual(
                    classify(
                        typesafe_classifier(threshold=threshold),
                        active_session=topic_session(),
                        active_run=active_run,
                    ),
                    expected,
                )

    @responses.activate
    def test_every_failure_raises_without_the_key(self) -> None:
        failures: list[tuple[dict[str, Any], str]] = [
            (
                {"status": 401, "json": {"detail": "Invalid API key"}},
                'TypeSafe answered HTTP 401: {"detail": "Invalid API key"}',
            ),
            ({"status": 529, "body": "overloaded"}, "TypeSafe answered HTTP 529: overloaded"),
            (
                {"body": requests.exceptions.ReadTimeout("Read timed out. (read timeout=3)")},
                "TypeSafe did not answer: Read timed out. (read timeout=3)",
            ),
            (
                {"body": requests.exceptions.ConnectionError("Connection refused")},
                "TypeSafe did not answer: Connection refused",
            ),
            ({"body": "<html>Bad gateway</html>"}, "TypeSafe's answer is not usable: "),
            (
                {"json": {"model": "jev-1.13.0", "answers": {}}},
                "TypeSafe's answer is not usable: no probability for 'agent' in the answer",
            ),
            (
                {"json": typesafe_answer({"people": 0.0, "agent": 2.0})},
                "TypeSafe's answer is not usable: the probability for 'agent' is 2.0",
            ),
        ]
        for response, error in failures:
            responses.upsert(responses.POST, TYPESAFE_URL, **response)
            with (
                self.subTest(error=error),
                self.assertRaises(ArmadaAgentRuntimeError) as raised,
            ):
                classify(typesafe_classifier(), active_session=topic_session())
            self.assertTrue(str(raised.exception).startswith(error), str(raised.exception))
            self.assertNotIn(TYPESAFE_KEY, str(raised.exception))

    @responses.activate
    def test_without_a_key_nothing_is_sent(self) -> None:
        for api_key in (None, "", "  "):
            with self.assertRaisesRegex(
                ArmadaAgentRuntimeError,
                r"^TypeSafe has no API key: set armada_typesafe_api_key in zulip-secrets.conf\.$",
            ):
                classify(typesafe_classifier(api_key=api_key))
        self.assert_length(responses.calls, 0)

    @responses.activate
    def test_a_failure_logs_once_tells_the_topic_and_starts_nothing(self) -> None:
        # The same path as the Gemini router's failures: a warning, the
        # notice that the agent could not tell, and no run.
        responses.add(
            responses.POST,
            TYPESAFE_URL,
            body=requests.exceptions.ReadTimeout("Read timed out. (read timeout=3)"),
        )
        # A store with only the methods every store has: the handler asks
        # the others (the topic's policy, its run) only when they exist.
        run_store = Mock(spec=["start", "record"])
        dispatcher = Mock()
        sent_replies: list[str] = []

        class FakeBotHandler:
            user_profile = SimpleNamespace(id=99)

            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        handler = ArmadaEmbeddedBotHandler(
            active_session_loader=lambda message, bot_handler: topic_session(),
            worker_run_store=run_store,
            worker_dispatcher=dispatcher,
            addressing_intent_classifier=typesafe_classifier(),
        )
        with self.assertLogs("zerver.lib.armada", level="WARNING") as logs:
            handler.handle_message(MESSAGE, FakeBotHandler())

        self.assertEqual(
            logs.output[0].splitlines()[0], "WARNING:zerver.lib.armada:Addressing router failed."
        )
        self.assertIn("TypeSafe did not answer: Read timed out. (read timeout=3)", logs.output[0])
        self.assert_length(logs.output, 1)
        self.assertEqual(
            sent_replies,
            [
                f"{ARMADA_AGENT_NAME} couldn't determine whether that message was meant for it. "
                f"Address it as `@{ARMADA_AGENT_NAME}` to bypass ambient routing."
            ],
        )
        run_store.start.assert_not_called()
        self.assertEqual(dispatcher.mock_calls, [])

    def test_the_typesafe_runtime_builds_the_router_from_settings(self) -> None:
        with override_settings(
            ARMADA_ADDRESSING_ROUTER_RUNTIME="typesafe",
            ARMADA_TYPESAFE_API_KEY=TYPESAFE_KEY,
            ARMADA_ADDRESSING_ROUTER_TYPESAFE_URL=TYPESAFE_URL,
            ARMADA_ADDRESSING_ROUTER_TYPESAFE_MODEL="jev-1.13.0",
            ARMADA_ADDRESSING_ROUTER_TYPESAFE_THRESHOLD=0.85,
        ):
            classifier = build_armada_addressing_intent_classifier_from_settings()
        assert isinstance(classifier, ArmadaTypeSafeAddressingIntentClassifier)
        self.assertEqual(classifier.api_key, TYPESAFE_KEY)
        self.assertEqual(classifier.url, TYPESAFE_URL)
        self.assertEqual(classifier.model, "jev-1.13.0")
        self.assertEqual(classifier.threshold, 0.85)
        self.assertEqual(classifier.session.get_adapter(TYPESAFE_URL).timeout, 3)  # type: ignore[attr-defined] # OutgoingHTTPAdapter

        with override_settings(ARMADA_ADDRESSING_ROUTER_RUNTIME="disabled"):
            self.assertIsNone(build_armada_addressing_intent_classifier_from_settings())
