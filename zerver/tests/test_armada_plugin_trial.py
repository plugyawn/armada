import base64
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlencode

import responses
from typing_extensions import override

from zerver.lib.armada_plugin_trial import (
    ARMADA_PLUGIN_TRIAL_REQUEST_LIMIT,
    ARMADA_PLUGIN_TRIAL_SECONDS,
    Problem,
    TrialResult,
    _Capture,
    blank,
    contract_problem,
    plain_words,
    run_plugin_trial,
)
from zerver.lib.armada_plugin_validation import canonicalize_plugin_manifest
from zerver.lib.test_classes import ZulipTestCase

TRIAL = Path(__file__).parent / "fixtures" / "armada_plugins" / "trial"
REDIS_URL = "redis://:hunter2-0123456789abcdef@10.0.0.7:6379/0"
EMAIL = "queue-plugin-bot@zulip.testserver"


class PluginTrialTest(ZulipTestCase):
    """A plugin's trial (zerver/lib/armada_plugin_trial.py), with a plugin
    in the standard library whose setting says what it does: what it pushes
    is checked and kept, what it would post is kept, it never reaches
    Armada's API, its secrets are blanked in all it returns, and it is
    stopped once it has shown its widgets, or killed when its minute is
    up."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.plugin = self.root / "queue"
        shutil.copytree(TRIAL, self.plugin)
        # The tests may not reach the internet; the plugin on trial, on
        # this machine, they do reach.
        network = responses.RequestsMock(assert_all_requests_are_fired=False)
        network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        network.start()
        self.addCleanup(network.stop)

    def manifest(self, **changes: Any) -> dict[str, Any]:
        manifest = json.loads((TRIAL / "armada-plugin.json").read_text())
        return canonicalize_plugin_manifest({**manifest, **changes})

    def trial(
        self,
        behaviour: str = "good",
        *,
        manifest: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
    ) -> TrialResult:
        return run_plugin_trial(
            self.plugin,
            manifest or self.manifest(),
            plugin_settings={"behaviour": behaviour},
            secret_environment=secrets or {},
            email=EMAIL,
            plugin_dir=self.root / "plugins",
        )

    def test_a_trial_keeps_what_the_program_shows_and_offers(self) -> None:
        result = self.trial()
        [(scope, document)] = result.documents.items()
        self.assertEqual(
            (scope, document["data"]["waiting"], document["rest"]["sizer"]),
            (("queue", ""), "13 waiting", "88 waiting"),
        )
        self.assertEqual(
            (result.problems, result.posts, result.asked, result.pushes, result.killed),
            ([], [], [], 2, False),
        )
        self.assertEqual(
            result.tools,
            [{"name": "queue_status", "description": "What is waiting, and for what."}],
        )
        self.assertEqual(
            result.lines,
            ["queue plugin starting in a trial: 1", "push: success", "push: success"],
        )
        # Two seconds after it showed its widget, not a minute.
        self.assertLess(result.seconds, 10)
        self.assertEqual((self.plugin / "setup-ran").read_text(), "1")

    def test_a_refused_push_says_where_and_what_and_ends_the_trial(self) -> None:
        result = self.trial("bad")
        self.assertEqual(
            result.problems,
            [
                Problem(
                    "push 1 of queue at $.blocks[0].items[1].figure",
                    "is longer than 24 characters",
                )
            ],
        )
        self.assertEqual(
            (result.documents, result.lines[-1], result.killed), ({}, "push: error", False)
        )
        self.assertLess(result.seconds, 10)

        result = self.trial("patches")
        self.assertEqual(
            result.problems,
            [
                Problem("push 1 of queue", "queue's document comes before its data."),
                Problem("push 2 of nope", "Eval queue declares no widget named nope."),
            ],
        )
        self.assertEqual(list(result.documents), [("queue", "")])

    def test_what_it_would_post_or_ask_is_kept_and_reaches_no_one(self) -> None:
        result = self.trial("chatty")
        self.assertEqual(
            (result.posts, result.asked),
            (
                [{"type": "stream", "to": "plugins", "topic": "queue", "content": "Queue is up."}],
                ["GET /api/v1/streams"],
            ),
        )
        self.assertEqual(
            result.lines[1:3],
            ["posted: success", "asked: A trial does not answer GET /api/v1/streams."],
        )
        # Its long poll for events was let go at the end, not after a minute.
        self.assertLess(result.seconds, 10)
        with mock.patch("zerver.lib.armada_plugin_trial.ARMADA_PLUGIN_TRIAL_POSTS", 0):
            self.assertEqual(self.trial("chatty").posts, [])

    def test_a_secret_reaches_the_program_and_nothing_it_returns(self) -> None:
        result = self.trial("secret", secrets={"QUEUE_REDIS_URL": REDIS_URL})
        self.assertIn("connecting to [secret]", result.lines)
        self.assertEqual(result.documents[("queue", "")]["title"], "Queue at [secret]")
        self.assertNotIn(REDIS_URL, json.dumps([result.lines, list(result.documents.values())]))

    def test_a_trial_is_killed_when_its_minute_is_up(self) -> None:
        self.assertEqual(ARMADA_PLUGIN_TRIAL_SECONDS, 60)
        with mock.patch("zerver.lib.armada_plugin_trial.ARMADA_PLUGIN_TRIAL_SECONDS", 2):
            result = self.trial("silent")
        self.assertEqual(
            (result.problems, result.killed),
            ([Problem("widget queue", "It pushed no document for queue in 2 seconds.")], True),
        )
        self.assertGreaterEqual(result.seconds, 2)
        pid = int((self.plugin / "pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_a_program_that_goes_wrong_says_how(self) -> None:
        result = self.trial("exit")
        self.assertEqual(
            (result.problems, result.lines[-1]),
            ([Problem("run.command", "It exited with status 3.")], "crashing on purpose"),
        )
        with mock.patch("zerver.lib.armada_plugin_trial.ARMADA_PLUGIN_TRIAL_STARTUP_SECONDS", 1):
            result = self.trial("unhealthy")
        self.assertEqual(
            result.problems,
            [Problem("run.command", "It did not answer GET /health on its port within 1 seconds.")],
        )
        command = ["python3", "armada_trial_plugin.py"]
        for run, problem in (
            (
                {
                    "command": command,
                    "setup": ["python3", "-c", "import sys; print('no uv'); sys.exit(2)"],
                },
                Problem("run.setup", "Its setup failed: no uv"),
            ),
            (
                {"command": command, "setup": ["python3", "-c", "import sys; sys.exit(2)"]},
                Problem(
                    "run.setup",
                    "Its setup failed: Command '['python3', '-c', 'import sys; sys.exit(2)']'"
                    " returned non-zero exit status 2.",
                ),
            ),
            (
                {"command": command, "setup": ["/nonexistent/uv", "sync"]},
                Problem(
                    "run.setup",
                    "Its setup failed: [Errno 2] No such file or directory: '/nonexistent/uv'",
                ),
            ),
            (
                {"command": ["/nonexistent/plugin"]},
                Problem("run.command", "It could not start: No such file or directory."),
            ),
        ):
            self.assertEqual(self.trial(manifest=self.manifest(run=run)).problems, [problem])
        provides = self.manifest()["provides"]
        result = self.trial(
            manifest=self.manifest(provides={**provides, "tools": {"path": "/nope"}})
        )
        self.assertEqual(
            (result.problems, result.tools),
            (
                [
                    Problem(
                        "provides.tools",
                        "Its tools could not be listed: it answered initialize with HTTP 404.",
                    )
                ],
                None,
            ),
        )

    def test_the_trials_api_answers_as_armadas_does(self) -> None:
        capture = _Capture(self.manifest(), email=EMAIL, api_key="trial-key", hidden=[REDIS_URL])
        signed = "Basic " + base64.b64encode(f"{EMAIL}:trial-key".encode()).decode()

        def form(**fields: Any) -> bytes:
            return urlencode(
                {
                    name: value if isinstance(value, str) else json.dumps(value)
                    for name, value in fields.items()
                }
            ).encode()

        document = {
            "version": 1,
            "title": f"Queue at {REDIS_URL}",
            "data": {"waiting": "12 waiting"},
            "rest": {"figure": "data.waiting", "sizer": "88 waiting"},
            "blocks": [{"type": "list", "label": "Waiting", "items": []}],
        }
        self.assertEqual(
            capture.handle("GET", "/api/v1/users/me", "Basic nope", b""),
            (401, {"result": "error", "msg": "Invalid API key", "code": "UNAUTHORIZED"}),
        )
        self.assertEqual(
            capture.handle(
                "POST", "/api/v1/messages", signed, b"x" * (ARMADA_PLUGIN_TRIAL_REQUEST_LIMIT + 1)
            )[0],
            413,
        )
        self.assertEqual(
            capture.handle("GET", "/api/v1/users/me", signed, b""),
            (200, {"result": "success", "msg": "", "email": EMAIL, "is_bot": True}),
        )
        self.assertEqual(
            capture.handle(
                "POST", "/api/v1/armada/widgets", signed, form(widget="queue", document=document)
            ),
            (200, {"result": "success", "msg": "", "version": 1}),
        )
        self.assertEqual(
            capture.handle(
                "POST",
                "/api/v1/armada/widgets?widget=queue",
                signed,
                form(data={"waiting": "9 waiting"}),
            ),
            (200, {"result": "success", "msg": "", "version": 2}),
        )
        self.assertEqual(capture.result.documents[("queue", "")]["data"]["waiting"], "9 waiting")
        self.assertEqual(capture.result.documents[("queue", "")]["title"], "Queue at [secret]")
        for fields, message in (
            ({"widget": "queue", "link_id": 4, "document": document}, "No such link"),
            ({"widget": "queue", "document": "{"}, 'Argument "document" is not valid JSON.'),
            (
                {"widget": "queue", "item": "lr-1", "data": {}},
                "queue is not opened from a row, so it has no item lr-1.",
            ),
        ):
            status, answer = capture.handle(
                "POST", "/api/v1/armada/widgets", signed, form(**fields)
            )
            self.assertEqual((status, answer["msg"]), (400, message))
        self.assertEqual(
            capture.result.problems[-3:],
            [
                Problem("push 3 of queue", "No such link"),
                Problem("push 4 of queue", 'Argument "document" is not valid JSON.'),
                Problem(
                    "push 5 of queue, item lr-1",
                    "queue is not opened from a row, so it has no item lr-1.",
                ),
            ],
        )
        self.assertEqual(
            capture.handle("POST", "/api/v1/register", signed, b"event_types=%5B%5D")[1][
                "queue_id"
            ],
            "trial",
        )
        # Its long poll waits until the trial is over.
        capture.over.set()
        self.assertEqual(
            capture.handle("GET", "/api/v1/events?queue_id=trial&last_event_id=-1", signed, b"")[1][
                "events"
            ],
            [{"type": "heartbeat", "id": 0}],
        )
        self.assertEqual(capture.handle("DELETE", "/api/v1/events", signed, b"")[0], 200)
        capture.handle(
            "POST",
            "/api/v1/messages",
            signed,
            form(type="stream", to="plugins", topic="q", content=f"at {REDIS_URL}"),
        )
        self.assertEqual(
            capture.result.posts,
            [{"type": "stream", "to": "plugins", "topic": "q", "content": "at [secret]"}],
        )
        for _ in range(2):
            self.assertEqual(
                capture.handle("PATCH", "/api/v1/settings", signed, b""),
                (
                    400,
                    {
                        "result": "error",
                        "msg": "A trial does not answer PATCH /api/v1/settings.",
                        "code": "BAD_REQUEST",
                    },
                ),
            )
        self.assertEqual(capture.result.asked, ["PATCH /api/v1/settings"])

    def test_the_contracts_words_plainly(self) -> None:
        for words, plain in (
            ("string exceeds 24 characters", "is longer than 24 characters"),
            ("string must contain at least 1 character", "is shorter than 1 characters"),
            ("missing required property 'label'", "lacks label, which it needs"),
            ("unsupported property", "is not something the contract knows"),
            ("expected type string", "should be a string"),
            ("array exceeds 32 items", "has more than 32 items"),
            ("value must be >= 1", "is below 1"),
            ("value must be <= 3", "is above 3"),
            ('expected one of ["nav"], got "side"', 'is "side", not one of ["nav"]'),
            (
                "value does not match any allowed contract variant",
                "is not one of the shapes the contract allows",
            ),
            (
                "a widget in the column gives column_slots",
                "a widget in the column gives column_slots",
            ),
        ):
            self.assertEqual(plain_words(words), plain)
        self.assertEqual(
            contract_problem(
                "push 1", "The widget is not valid: $.title: string exceeds 80 characters"
            ),
            Problem("push 1 at $.title", "is longer than 80 characters"),
        )
        self.assertEqual(
            contract_problem("push 1", "No such link"), Problem("push 1", "No such link")
        )
        self.assertEqual(
            blank({"a": ["x hunter2", 3, None], "b": "hunter2"}, ["hunter2"]),
            {"a": ["x [secret]", 3, None], "b": "[secret]"},
        )
