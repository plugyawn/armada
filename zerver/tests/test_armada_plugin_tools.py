import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import responses
from django.test import override_settings
from typing_extensions import override

from zerver.lib.armada import (
    ARMADA_EMBEDDED_BOT_SERVICE,
    ArmadaLiveRunOperation,
    ArmadaRunAsking,
    ArmadaWorkerTurn,
    get_armada_live_run_state,
)
from zerver.lib.armada_agent import (
    ArmadaAppServerNotification,
    ArmadaAppServerTurnContext,
    ArmadaCodexAppServerToolRunner,
    armada_codex_dynamic_tools,
)
from zerver.lib.armada_message_tools import (
    ARMADA_STARTED_BY_EVENT,
    ArmadaToolCaller,
    ArmadaToolTurn,
)
from zerver.lib.armada_plugin_check import ARMADA_PLUGIN_PREVIEW_DIRECTORY
from zerver.lib.armada_plugin_repository import init_team_plugin_repository, team_plugin_repository
from zerver.lib.armada_plugin_tools import run_origin
from zerver.lib.armada_plugins import install_plugin, update_plugin
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRun, AgentRunEvent, PluginInstall, RealmAuditLog, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.tests.test_armada_plugin_preview import fake_chromium

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"
WANDB_KEY = "wandb-local-0a1b2c3d4e5f60718293a4b5c6d7f3a"
TRIAL = Path(__file__).parent / "fixtures" / "armada_plugins" / "trial"
REDIS_URL = "redis://:hunter2-0123456789abcdef@10.0.0.7:6379/0"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def call_plugin_tool(
    tool: str,
    arguments: object,
    *,
    requester: UserProfile,
    run: AgentRun,
    workdir: Path | None = None,
    person_wait: Callable[..., bool] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Send Codex's call of a plugin tool through the runtime, for the run's
    person; return the reply's result and the row the run's trace shows."""
    runner = ArmadaCodexAppServerToolRunner(
        codex_bin="/bin/false",
        workdir=None if workdir is None else str(workdir),
        model="gpt-6-astra",
        sandbox="workspace-write",
        approval_policy="never",
        timeout_seconds=5,
        output_limit=1000,
    )
    turn = ArmadaAppServerTurnContext(
        thread_id="thread-1",
        event_callback=None,
        user_input_provider=None if person_wait is None else PersonAsking(person_wait),
        is_main=True,
        requesting_user_id=requester.id,
        trigger_message_id=run.trigger_message_id,
    )
    sent: list[dict[str, Any]] = []
    with patch.object(runner, "send_message", side_effect=sent.append):
        runner.handle_server_request(
            {
                "id": 7,
                "method": "item/tool/call",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "tool": tool,
                    "namespace": None,
                    "arguments": json.dumps(arguments),
                },
            },
            turn_context=turn,
        )
    [reply] = sent
    notification = turn.messages.get_nowait()
    assert isinstance(notification, ArmadaAppServerNotification)
    [event] = notification.events
    return reply["result"], dict(event)


def answer_of(result: dict[str, Any]) -> Any:
    return json.loads(result["contentItems"][0]["text"])


class PersonAsking:
    """A run's user-input provider, as ArmadaRunAsking is, with the wait
    on its person a test gives it."""

    def __init__(self, wait: Callable[..., bool]) -> None:
        self.wait_on_person = wait

    def __call__(self, questions: list[dict[str, Any]]) -> dict[str, list[str]] | None:
        raise AssertionError("A settings request asks no question.")


class PluginSettingsRequestTest(ZulipTestCase):
    """armada_plugin_request_settings (zerver/lib/armada_plugin_tools.py),
    called as Codex calls it: it asks the person the run acts for to fill in
    a plugin's settings in its sheet, waits as a question does, and answers
    with names, never values."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.agent = self.create_test_bot(
            "agent",
            self.example_user("iago"),
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        self.install = install_plugin(
            self.hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            plugin_settings={"entity": "runix"},
        )
        message_id = self.send_stream_message(self.hamlet, "Denmark", "make us a W&B widget")
        self.agent_run = AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=self.agent,
            requesting_user=self.hamlet,
            trigger_message_id=message_id,
            stream_name="Denmark",
            topic_name="test",
            status=AgentRun.RUNNING,
        )
        self.asks: list[tuple[str, ArmadaLiveRunOperation]] = []

    def waiting(self, person_saves: Callable[[], None] | None) -> Callable[..., bool]:
        """A run's person-wait: the person saves the sheet, or doesn't."""

        def wait(ask: str, operation: ArmadaLiveRunOperation, answered: Callable[[], bool]) -> bool:
            self.asks.append((ask, operation))
            if person_saves is not None:
                person_saves()
            return answered()

        return wait

    def call(
        self, arguments: object, *, person_wait: Callable[..., bool] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return call_plugin_tool(
            "armada_plugin_request_settings",
            arguments,
            requester=self.hamlet,
            run=self.agent_run,
            person_wait=person_wait,
        )

    def test_a_new_thread_is_offered_the_tool(self) -> None:
        self.assertIn(
            "armada_plugin_request_settings",
            [spec["name"] for spec in armada_codex_dynamic_tools()],
        )

    def test_the_person_sets_a_secret_in_the_sheet_and_only_its_name_comes_back(self) -> None:
        result, event = self.call(
            {"id": "wandb", "fields": ["api_key"], "prefill": {"posts": "crashes"}},
            person_wait=self.waiting(
                lambda: update_plugin(self.hamlet, self.install, secrets={"api_key": WANDB_KEY})
            ),
        )
        self.assertTrue(result["success"])
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]), {"set": ["api_key"], "missing": []}
        )
        self.assertEqual(
            (event["status"], event["title"]),
            ("done", "Asked King Hamlet for api_key, in the settings."),
        )
        [(ask, operation)] = self.asks
        self.assertEqual(
            ask,
            f"Onix asks @**King Hamlet|{self.hamlet.id}** to set up **Weights & Biases**, in its "
            "settings: API key.\n\n*Open the settings from the button, or from Weights & Biases "
            "in Plugins. The run waits up to 10 minutes.*",
        )
        self.assertEqual(
            operation,
            {
                "kind": "settings",
                "label": "waiting on you",
                "install_id": self.install.id,
                "fields": ["api_key"],
                "prefill": {"posts": "crashes"},
            },
        )
        # The secret is in the plugin's store alone: not in the answer Codex
        # reads, the trace's row, or the message that asked.
        for text in (json.dumps(result), json.dumps(event), ask):
            self.assertNotIn(WANDB_KEY, text)

    def test_what_is_still_missing_is_said(self) -> None:
        result, event = self.call(
            {"id": "wandb", "fields": ["api_key", "entity"]}, person_wait=self.waiting(None)
        )
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]),
            {"set": ["entity"], "missing": ["api_key"]},
        )
        self.assertEqual(
            event["title"],
            "Asked King Hamlet for api_key, entity, in the settings. Still missing: api_key.",
        )

    def test_what_is_set_already_needs_no_asking(self) -> None:
        result, event = self.call(
            {"id": "wandb", "fields": ["entity"]}, person_wait=self.waiting(None)
        )
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]), {"set": ["entity"], "missing": []}
        )
        self.assertEqual(event["title"], "Already set for Weights & Biases: entity.")
        self.assertEqual(self.asks, [])

    def test_what_cannot_be_asked(self) -> None:
        wait = self.waiting(None)
        for arguments, refusal in (
            (
                {"id": "wandb", "fields": ["token"]},
                "Weights & Biases has no setting or secret named token.",
            ),
            ({"id": "wandb", "fields": []}, "fields names each setting or secret to fill in once."),
            (
                {"id": "wandb", "fields": ["api_key", "api_key"]},
                "fields names each setting or secret to fill in once.",
            ),
            (
                {"id": "wandb", "fields": ["api_key"], "prefill": {"api_key": "x"}},
                "prefill gives values to settings only; a secret is never given.",
            ),
            ({"id": "gpus", "fields": ["api_key"]}, "No such plugin"),
        ):
            result, event = self.call(arguments, person_wait=wait)
            self.assertFalse(result["success"])
            self.assertEqual(event["title"], refusal)
        # A side conversation asks nobody.
        result, event = self.call({"id": "wandb", "fields": ["api_key"]})
        self.assertEqual(
            event["title"],
            "Nobody can be asked from here; say in your reply what Weights & Biases needs.",
        )
        # A draft is its maker's alone.
        PluginInstall.objects.filter(id=self.install.id).update(
            state=PluginInstall.DRAFT, made_by=self.example_user("iago")
        )
        PluginInstall.objects.filter(id=self.install.id).update(installed_by=None)
        result, event = self.call({"id": "wandb", "fields": ["api_key"]}, person_wait=wait)
        self.assertEqual(event["title"], "No such plugin")
        self.assertEqual(self.asks, [])


class WaitOnPersonTest(ZulipTestCase):
    """ArmadaWorkerTurn.wait_on_person: the run waits on a person as it
    waits on a question's answer, with its live state saying what for."""

    def turn(self) -> tuple[ArmadaWorkerTurn, MagicMock, MagicMock]:
        handler = MagicMock()
        handler.worker_run_cancel_requested.return_value = False
        bot_handler = MagicMock()
        turn = ArmadaWorkerTurn(
            handler,
            {"id": 900},
            bot_handler,
            content="make us a widget",
            active_session=None,
            message_id=900,
            stream="Denmark",
            topic="test",
            post_started_progress=False,
        )
        return turn, handler, bot_handler

    def test_the_wait_shows_what_it_is_for_until_it_is_answered(self) -> None:
        turn, handler, bot_handler = self.turn()
        operation: ArmadaLiveRunOperation = {
            "kind": "settings",
            "label": "waiting on you",
            "install_id": 3,
            "fields": ["api_key"],
            "prefill": {},
        }
        checks: list[object] = []

        def answered() -> bool:
            state = get_armada_live_run_state(900)
            checks.append(state.get("operation") if state is not None else None)
            return len(checks) == 3

        with (
            patch("zerver.lib.armada.ARMADA_QUESTION_POLL_SECONDS", 0),
            patch("zerver.lib.armada.ARMADA_QUESTION_HEARTBEAT_SECONDS", 0),
        ):
            # As Codex's tool reaches it: through the run's provider.
            self.assertTrue(ArmadaRunAsking(turn).wait_on_person("Set it up.", operation, answered))
        bot_handler.send_reply.assert_called_once_with({"id": 900}, "Set it up.")
        self.assertEqual(checks, [operation, operation, operation])
        handler.record_worker_event.assert_called_with(
            bot_handler, 900, "question_waiting", summary="Waiting for a person."
        )
        # Done waiting, the run shows no operation and takes steering again.
        state = get_armada_live_run_state(900)
        assert state is not None
        self.assertIsNone(state.get("operation"))
        self.assertFalse(turn.question_open)

    def test_time_running_out_or_a_stop_ends_the_wait(self) -> None:
        operation: ArmadaLiveRunOperation = {"kind": "settings", "label": "waiting on you"}
        turn, _handler, _bot_handler = self.turn()
        with (
            patch("zerver.lib.armada.ARMADA_QUESTION_TIMEOUT_SECONDS", 0),
            patch("zerver.lib.armada.ARMADA_QUESTION_POLL_SECONDS", 0),
        ):
            self.assertFalse(turn.wait_on_person("Set it up.", operation, lambda: False))

        turn, handler, _bot_handler = self.turn()
        handler.worker_run_cancel_requested.return_value = True
        self.assertFalse(turn.wait_on_person("Set it up.", operation, lambda: True))
        handler.worker_runtime.interrupt.assert_called_once_with()


class PluginBuildTest(ZulipTestCase):
    """The agent makes a plugin for the person who asked, end to end, with
    its tools: it checks the plugin (a trial of its program, and previews
    drawn by a browser, here a fake one), asks for its secret in the
    settings, installs it from the team's repository, and updates it. The
    plugin is the trial's fake (fixtures/armada_plugins/trial)."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        overridden = override_settings(
            ARMADA_TOPIC_WORKDIR_ROOT=str(self.root / "workspaces"),
            ARMADA_PLUGIN_DIR=str(self.root / "plugins"),
            ARMADA_PLUGIN_PREVIEW_BROWSER=fake_chromium(self.root),
            ARMADA_WORKER_BROWSER_DIR=None,
        )
        overridden.enable()
        self.addCleanup(overridden.disable)
        # The tests may not reach the internet; the plugin on trial, on
        # this machine, they do reach.
        network = responses.RequestsMock(assert_all_requests_are_fired=False)
        network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        network.start()
        self.addCleanup(network.stop)
        self.hamlet = self.example_user("hamlet")
        self.realm = self.hamlet.realm
        # The fake browser loads what the beta's page asks the server for.
        environment = patch.dict(
            "os.environ",
            {
                "FAKE_CHROMIUM": "",
                "FAKE_CHROMIUM_LOG": str(self.root / "chromium.log"),
                "FAKE_CHROMIUM_REQUESTS": json.dumps(
                    [
                        f"{self.realm.url}/json/register",
                        f"{self.realm.url}/json/events?queue_id=preview",
                    ]
                ),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        agent = self.create_test_bot(
            "agent",
            self.example_user("iago"),
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        init_team_plugin_repository(self.realm, acting_user=self.example_user("desdemona"))
        # Hamlet asked in Denmark › lr-sweep; that run started the build in
        # plugins › queue (armada_start_run).
        self.asked_id = self.send_stream_message(
            self.hamlet, "Denmark", "make us a widget for the eval queue", topic_name="lr-sweep"
        )
        brief_id = self.send_stream_message(
            agent, "plugins", "Make a widget for the eval queue.", topic_name="queue"
        )
        self.build_run = AgentRun.objects.create(
            realm=self.realm,
            bot_profile=agent,
            requesting_user=self.hamlet,
            trigger_message_id=brief_id,
            stream_name="plugins",
            topic_name="queue",
            status=AgentRun.RUNNING,
        )
        AgentRunEvent.objects.create(
            run=self.build_run,
            event_type=ARMADA_STARTED_BY_EVENT,
            payload={
                "depth": 1,
                "stream": "Denmark",
                "topic": "lr-sweep",
                "message_id": self.asked_id,
            },
        )
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        self.origin = repository.repo_cache_path
        self.worktree = self.root / "worktree"
        git("clone", "-q", self.origin, str(self.worktree))
        self.sha = self.commit("0.1.0")

    def commit(self, version: str) -> str:
        """The trial plugin in plugins/queue, its secret required, committed."""
        directory = self.worktree / "plugins" / "queue"
        shutil.copytree(TRIAL, directory, dirs_exist_ok=True)
        manifest = json.loads((TRIAL / "armada-plugin.json").read_text())
        manifest["version"] = version
        manifest["secrets"]["redis_url"]["required"] = True
        (directory / "armada-plugin.json").write_text(json.dumps(manifest))
        git("-C", str(self.worktree), "add", "-A")
        git("-C", str(self.worktree), "commit", "-q", "-m", f"Make the queue plugin {version}.")
        return git("-C", str(self.worktree), "rev-parse", "HEAD")

    def tool(
        self,
        tool: str,
        arguments: object,
        *,
        person_wait: Callable[..., bool] | None = None,
        workdir: Path | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return call_plugin_tool(
            tool,
            arguments,
            requester=self.hamlet,
            run=self.build_run,
            workdir=workdir or self.worktree,
            person_wait=person_wait,
        )

    def test_a_plugin_is_checked_set_up_installed_and_updated_for_who_asked(self) -> None:
        seen: list[str] = []
        result, event = self.tool("armada_plugin_install", {"id": "queue", "commit": self.sha})
        seen += [json.dumps(result), json.dumps(event)]
        self.assertEqual(
            event["title"],
            "Check queue first with armada_plugin_check: that makes its draft, where its "
            "settings are kept until it is installed.",
        )

        # The check: a trial, the previews, and what to ask for.
        result, event = self.tool("armada_plugin_check", {"path": "plugins/queue"})
        seen += [json.dumps(result), json.dumps(event)]
        check = answer_of(result)
        self.assertEqual(
            (check["ok"], check["waiting_for"], event["title"]),
            (True, ["redis_url"], "Checked queue · 1 widget, 1 tool, no problems"),
        )
        previews = self.worktree / ARMADA_PLUGIN_PREVIEW_DIRECTORY
        self.assertEqual(
            sorted(
                Path(preview["path"]).relative_to(previews).as_posix()
                for preview in check["previews"]
            ),
            [
                "queue-queue-pressed-dark.png",
                "queue-queue-pressed-light.png",
                "queue-rest-dark.png",
                "queue-rest-light.png",
            ],
        )
        for preview in check["previews"]:
            self.assertEqual(Path(preview["path"]).read_bytes()[:8], PNG_SIGNATURE)
        draft = PluginInstall.objects.get(realm=self.realm, plugin_id="queue")
        self.assertEqual(draft.state, PluginInstall.DRAFT)

        # Not installed half set up.
        result, event = self.tool("armada_plugin_install", {"id": "queue", "commit": self.sha})
        self.assertEqual(
            event["title"],
            "Eval queue still needs redis_url: ask for them with armada_plugin_request_settings "
            "before installing it.",
        )

        # Hamlet saves the secret in the sheet.
        def person_saves(ask: str, operation: Any, answered: Callable[[], bool]) -> bool:
            seen.append(ask)
            self.assertEqual(
                (operation["install_id"], operation["fields"]), (draft.id, ["redis_url"])
            )
            update_plugin(self.hamlet, draft, secrets={"redis_url": REDIS_URL})
            return answered()

        result, event = self.tool(
            "armada_plugin_request_settings",
            {"id": "queue", "fields": ["redis_url"]},
            person_wait=person_saves,
        )
        seen += [json.dumps(result), json.dumps(event)]
        self.assertEqual(answer_of(result), {"set": ["redis_url"], "missing": []})

        # Installed, for Hamlet, from the team's repository, where it was asked.
        result, event = self.tool("armada_plugin_install", {"id": "queue", "commit": self.sha})
        seen += [json.dumps(result), json.dumps(event)]
        self.assertEqual(event["title"], "Installed queue 0.1.0 for the team, for King Hamlet.")
        self.assertEqual(
            answer_of(result),
            {
                "id": "queue",
                "version": "0.1.0",
                "sha": self.sha,
                "state": "starting",
                "widgets": {"queue": {"nav": "shown", "column": False}},
            },
        )
        install = PluginInstall.objects.select_related("made_in_stream").get(id=draft.id)
        assert install.made_in_stream is not None
        self.assertEqual(
            (
                install.state,
                install.installed_by_id,
                install.made_by_id,
                install.made_by_run_id,
                install.made_in_stream.name,
                install.made_in_topic,
                install.made_in_message_id,
                install.sha,
            ),
            (
                PluginInstall.STARTING,
                self.hamlet.id,
                self.hamlet.id,
                self.build_run.id,
                "Denmark",
                "lr-sweep",
                self.asked_id,
                self.sha,
            ),
        )
        self.assertEqual(git("--git-dir", self.origin, "rev-parse", "main"), self.sha)
        self.assertEqual(
            git("--git-dir", self.origin, "rev-parse", "queue/v0.1.0^{commit}"), self.sha
        )
        [made] = RealmAuditLog.objects.filter(
            modified_user_id=install.bot_id, event_type=AuditLogEventType.PLUGIN_MADE
        )
        self.assertEqual(
            (made.acting_user_id, made.extra_data["run_id"], made.extra_data["for_user_id"]),
            (self.hamlet.id, self.build_run.id, self.hamlet.id),
        )
        self.assertEqual(
            (made.extra_data["to_sha"], made.extra_data["to_version"]), (self.sha, "0.1.0")
        )

        # A new version, for everyone.
        sha = self.commit("0.2.0")
        result, event = self.tool("armada_plugin_update", {"id": "queue", "commit": sha})
        seen += [json.dumps(result), json.dumps(event)]
        self.assertEqual(event["title"], "Updated queue to 0.2.0 for the team, for King Hamlet.")
        updated = answer_of(result)
        self.assertEqual(
            (updated["version"], updated["sha"], updated["waiting_for"]), ("0.2.0", sha, [])
        )
        [update] = RealmAuditLog.objects.filter(
            modified_user_id=install.bot_id, event_type=AuditLogEventType.PLUGIN_UPDATED
        )
        self.assertEqual(
            {
                key: update.extra_data[key]
                for key in (
                    "from_sha",
                    "to_sha",
                    "from_version",
                    "to_version",
                    "run_id",
                    "for_user_id",
                )
            },
            {
                "from_sha": self.sha,
                "to_sha": sha,
                "from_version": "0.1.0",
                "to_version": "0.2.0",
                "run_id": self.build_run.id,
                "for_user_id": self.hamlet.id,
            },
        )
        # The secret reached the plugin's store and nothing the agent or the
        # trace sees.
        for text in seen:
            self.assertNotIn("hunter2", text)

    def test_what_the_build_tools_refuse(self) -> None:
        result, event = call_plugin_tool(
            "armada_plugin_check",
            {"path": "plugins/queue"},
            requester=self.hamlet,
            run=self.build_run,
        )
        self.assertEqual(event["title"], "This run has no worktree, where a plugin is made.")
        # Another person's draft is theirs to install.
        othello = self.example_user("othello")
        call_plugin_tool(
            "armada_plugin_check",
            {"path": "plugins/queue"},
            requester=othello,
            run=self.build_run,
            workdir=self.worktree,
        )
        result, event = self.tool("armada_plugin_install", {"id": "queue", "commit": self.sha})
        self.assertTrue(event["title"].startswith("Check queue first with armada_plugin_check"))
        PluginInstall.objects.filter(realm=self.realm, plugin_id="queue").update(
            made_by=self.hamlet, installed_by=self.hamlet
        )
        # A check that finds a problem names it.
        result, event = self.tool("armada_plugin_check", {"path": "plugins/elsewhere"})
        check = answer_of(result)
        self.assertFalse(check["ok"])
        self.assertEqual(
            event["title"], f"Checked plugins/elsewhere · {check['problems'][0]['what']}"
        )
        self.tool("armada_plugin_check", {"path": "plugins/queue"})
        draft = PluginInstall.objects.get(realm=self.realm, plugin_id="queue")
        result, event = self.tool("armada_plugin_update", {"id": "queue", "commit": self.sha})
        self.assertEqual(
            event["title"], "queue is not installed yet: install it with armada_plugin_install."
        )
        update_plugin(self.hamlet, draft, secrets={"redis_url": REDIS_URL})
        # What the release refuses, it says as it is.
        result, event = self.tool("armada_plugin_install", {"id": "queue", "commit": "HEAD"})
        self.assertEqual(
            event["title"], "A plugin is installed at a commit: 40 hexadecimal digits."
        )

    def test_a_run_no_run_started_was_asked_for_where_it_runs(self) -> None:
        caller = ArmadaToolCaller(
            asker=self.hamlet,
            run=self.build_run,
            turn=ArmadaToolTurn(
                requesting_user_id=self.hamlet.id,
                trigger_message_id=self.build_run.trigger_message_id,
                workdir=str(self.worktree),
                isolation_username=None,
            ),
        )
        origin = run_origin(caller)
        assert origin.stream is not None
        self.assertEqual(
            (origin.stream.name, origin.topic, origin.message_id),
            ("Denmark", "lr-sweep", self.asked_id),
        )
        AgentRunEvent.objects.filter(run=self.build_run).delete()
        origin = run_origin(caller)
        assert origin.stream is not None
        self.assertEqual(
            (origin.made_by, origin.run, origin.stream.name, origin.topic, origin.message_id),
            (self.hamlet, self.build_run, "plugins", "queue", self.build_run.trigger_message_id),
        )
