import json
import os
from typing import Any

from zerver.lib.armada import ARMADA_AGENT_NAME
from zerver.lib.armada_agent import (
    ARMADA_ADDRESSING_CONTEXT_MESSAGE_CHARACTERS,
    ARMADA_ADDRESSING_CONTEXT_MESSAGES,
    ARMADA_ADDRESSING_NEW_MESSAGE_CHARACTERS,
)
from zerver.lib.armada_typesafe import (
    armada_typesafe_agent_context,
    armada_typesafe_agent_probability,
    build_armada_typesafe_addressing_request,
)
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
            f"Is `new_message` asking {ARMADA_AGENT_NAME} to do or answer something now, "
            "or is it meant for the people in the chat?",
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
                    self.assertIs(is_bot, sender == "{agent}")
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
