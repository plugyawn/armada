import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from email.message import Message
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import TestCase, mock

from typing_extensions import Self

ROOT_DIR = Path(__file__).resolve().parents[2]


def load_tool() -> ModuleType:
    """The tool has no .py suffix, since it is run as a command."""
    path = ROOT_DIR / "tools/armada/typesafe_addressing_eval"
    loader = SourceFileLoader("typesafe_addressing_eval", str(path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


evaluation = load_tool()

# Built at run time, so that this file holds no credential-shaped line.
KEY = "test-" + "k" * 24
CASES = [
    {
        "id": "follow-up",
        "label": "agent",
        "channel": "web-app",
        "topic": "lint",
        "agent_run_in_progress": False,
        "messages": [["{agent}", True, "Fixed the lint errors."]],
        "new_message": ["Maya", "now the tests"],
    },
    {
        "id": "to-person",
        "label": "people",
        "channel": "web-app",
        "topic": "lint",
        "agent_run_in_progress": False,
        "messages": [["Arjun", False, "I'm free after lunch"]],
        "new_message": ["Maya", "can you review my PR later?"],
    },
    {
        "id": "chatter",
        "label": "people",
        "channel": "web-app",
        "topic": "lunch",
        "agent_run_in_progress": True,
        "messages": [],
        "new_message": ["Lena", "ramen?"],
    },
]
# None: TypeSafe does not answer in time.
AGENT_PROBABILITY = {"now the tests": 0.97, "can you review my PR later?": 0.6, "ramen?": None}


class FakeResponse:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return json.dumps(self.answer).encode()


def answer_for(request: urllib.request.Request) -> FakeResponse:
    assert isinstance(request.data, bytes)
    body = json.loads(request.data)
    probability = AGENT_PROBABILITY[body["state"]["new_message"]["text"]]
    if probability is None:
        raise TimeoutError("The read operation timed out")
    return FakeResponse(
        {
            "model": "jev-1.13.0",
            "answers": {
                "addressee": {
                    "type": "choice",
                    "choice": "agent" if probability >= 0.5 else "people",
                    "confidence": abs(2 * probability - 1),
                    "probabilities": {"people": 1 - probability, "agent": probability},
                }
            },
            "usage": {"input_tokens": 683, "output_tokens": 34},
        }
    )


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(evaluation.URL, code, "error", Message(), io.BytesIO(body))


class TypeSafeAddressingEvalTest(TestCase):
    def run_tool(self, *args: str) -> tuple[str, list[urllib.request.Request]]:
        sent: list[urllib.request.Request] = []

        def urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
            self.assertEqual(timeout, 3)
            sent.append(request)
            return answer_for(request)

        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory, "key")
            key_file.write_text(KEY + "\n")
            cases = Path(directory, "cases.json")
            cases.write_text(json.dumps({"cases": CASES}))
            argv = ["typesafe_addressing_eval", f"--key-file={key_file}", f"--cases={cases}"]
            stdout = io.StringIO()
            with (
                mock.patch.object(sys, "argv", [*argv, *args]),
                mock.patch.object(urllib.request, "urlopen", urlopen),
                redirect_stdout(stdout),
            ):
                self.assertEqual(evaluation.main(), 0)
        return stdout.getvalue(), sent

    def test_the_agent_name_is_read_from_its_one_definition(self) -> None:
        source = (ROOT_DIR / "zerver/lib/armada.py").read_text()
        self.assertIn(f'ARMADA_AGENT_NAME: Final = "{evaluation.agent_name()}"', source)

        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "zerver/lib"))
            armada = Path(root, "zerver/lib/armada.py")
            armada.write_text('from typing import Final\n\nARMADA_AGENT_NAME: Final = "Tester"\n')
            with mock.patch.object(evaluation, "ROOT_DIR", Path(root)):
                self.assertEqual(evaluation.agent_name(), "Tester")
                armada.write_text('AGENT_NAME = "Tester"\n')
                with self.assertRaises(SystemExit):
                    evaluation.agent_name()

    def test_each_variant_is_scored_and_the_key_is_never_printed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory, "out.json")
            printed, sent = self.run_tool(f"--out={out}")
            written = out.read_text()
        results = json.loads(written)

        self.assertNotIn(KEY, printed)
        self.assertNotIn(KEY, written)
        self.assertEqual(
            {request.get_header("Authorization") for request in sent}, {f"Bearer {KEY}"}
        )
        self.assertEqual(
            list(results), ["production", "no-context", "context-in-state", "agent-first"]
        )
        self.assertIn("production: jev-1.13.0, 683 input tokens a decision, latency p50 ", printed)
        self.assertIn(" ms, 1 unanswered within 3 s\n", printed)
        for expected in [
            "  >= 0.50: accuracy 66.7%, false positives to-person, false negatives none\n",
            "  >= 0.60: accuracy 66.7%, false positives to-person, false negatives none\n",
            "  >= 0.70: accuracy 100.0%, false positives none, false negatives none\n",
            "  >= 0.95: accuracy 100.0%, false positives none, false negatives none\n",
        ]:
            self.assertIn(expected, printed)
        rows = results["production"]["rows"]
        self.assertEqual([row["p_agent"] for row in rows], [0.97, 0.6, None])
        self.assertEqual(rows[2]["unanswered"], "The read operation timed out")

        bodies = [json.loads(request.data) for request in sent if isinstance(request.data, bytes)]
        production, no_context, in_state, agent_first = bodies[0::3]
        question = production["questions"]["addressee"]
        self.assertEqual(set(question["instructions"]), {"context", "question"})
        self.assertNotIn("about", production["state"])
        self.assertEqual(list(question["criteria"]), ["people", "agent"])
        self.assertEqual(
            no_context["questions"]["addressee"]["instructions"],
            question["instructions"]["question"],
        )
        self.assertEqual(
            in_state["questions"]["addressee"]["instructions"],
            question["instructions"]["question"],
        )
        self.assertEqual(in_state["state"]["about"], question["instructions"]["context"])
        self.assertEqual(
            agent_first["questions"]["addressee"]["instructions"], question["instructions"]
        )
        self.assertEqual(
            list(agent_first["questions"]["addressee"]["criteria"]), ["agent", "people"]
        )

        printed, sent = self.run_tool("--variants=no-context")
        self.assertTrue(printed.startswith("no-context: jev-1.13.0, "))
        texts = [
            json.loads(request.data)["state"]["new_message"]["text"]
            for request in sent
            if isinstance(request.data, bytes)
        ]
        self.assertEqual(texts, list(AGENT_PROBABILITY))

    def test_an_unanswered_request_leaves_the_agent_silent(self) -> None:
        self.assertEqual(
            evaluation.scores([{"id": "follow-up", "label": "agent", "p_agent": None}], 0.5),
            {
                "threshold": 0.5,
                "accuracy": 0.0,
                "false_positives": [],
                "false_negatives": ["follow-up"],
            },
        )
        with (
            mock.patch.object(urllib.request, "urlopen", side_effect=TimeoutError("timed out")),
            self.assertRaisesRegex(SystemExit, r"^TypeSafe answered none of the production"),
        ):
            evaluation.evaluate(CASES, "production", KEY, "jev-latest", 3)

    def test_a_rate_limit_is_waited_out_and_other_errors_stop(self) -> None:
        request = evaluation.production_request(CASES[0], "Tester", "jev-latest")
        replies: list[object] = [http_error(429, b"slow down"), http_error(529, b"busy")]

        def urlopen(http_request: urllib.request.Request, timeout: float) -> FakeResponse:
            reply = replies.pop(0) if replies else answer_for(http_request)
            if isinstance(reply, Exception):
                raise reply
            assert isinstance(reply, FakeResponse)
            return reply

        with (
            mock.patch.object(urllib.request, "urlopen", urlopen),
            mock.patch.object(evaluation.time, "sleep") as sleep,
        ):
            answer, seconds = evaluation.ask(request, KEY, 3)
            self.assertEqual(answer["answers"]["addressee"]["probabilities"]["agent"], 0.97)
            self.assertGreaterEqual(seconds, 0)
            self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

            replies.append(http_error(401, b"invalid API key"))
            with self.assertRaisesRegex(
                SystemExit, r"^TypeSafe answered HTTP 401: invalid API key$"
            ):
                evaluation.ask(request, KEY, 3)

            replies.extend([http_error(429, b"slow down")] * 4)
            with self.assertRaisesRegex(SystemExit, r"^TypeSafe answered HTTP 429: slow down$"):
                evaluation.ask(request, KEY, 3)
