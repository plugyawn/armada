import dataclasses
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypedDict, cast
from unittest.mock import patch

import orjson
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.armada import send_armada_run_event, send_armada_workspace_changed_event
from zerver.lib.armada import (
    ARMADA_ACTIVE_SESSION_ENDPOINT,
    ARMADA_AGENT_RUNTIME_PACKAGES,
    ARMADA_CODEX_MCP_TOOLS,
    ARMADA_CONFIG_ENDPOINT,
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ARMADA_EMBEDDED_BOT_SERVICE,
    ARMADA_FIRST_RUN_ENDPOINT,
    ARMADA_FIRST_RUN_STARTER_MESSAGE,
    ARMADA_FIRST_RUN_STREAM,
    ARMADA_FIRST_RUN_TOPIC,
    ARMADA_OFFICIAL_AGENT_RUNTIME,
    ARMADA_OFFICIAL_AGENT_RUNTIME_LABEL,
    ARMADA_OFFICIAL_CODEX_SDK_PACKAGES,
    ARMADA_RUNS_ENDPOINT,
    ARMADA_SESSION_ENDPOINT,
    ARMADA_TOPIC_CONTEXT_ENDPOINT,
    ARMADA_WORKING_MESSAGE,
    ArmadaEmbeddedBotHandler,
    ArmadaLiveRunState,
    ArmadaTokenUsage,
    ArmadaWorkerLeaseLostError,
    ArmadaWorkerRuntime,
    build_armada_active_session_summary_from_messages,
    build_armada_question_message,
    get_armada_session_payload,
    write_armada_live_run_state,
)
from zerver.lib.armada_agent import ArmadaCodexAppServerToolRunner, ArmadaProcessIsolation
from zerver.lib.armada_background_read import ArmadaBackgroundRead
from zerver.lib.armada_gate import (
    armada_selected_bot_cache_key,
    get_armada_followup_service_bot_event,
    get_selected_armada_bot_user_id_for_realm,
    invalidate_armada_selected_bot_cache,
)
from zerver.lib.armada_monitor import (
    ARMADA_MONITOR_FAILURE_LIMIT,
    ARMADA_MONITOR_MAX_PER_TOPIC,
    ArmadaMonitorSpecError,
    armada_apply_monitor_setup_result,
    armada_monitor_apply_tick,
    armada_monitor_bind_data,
    armada_monitor_run_recipe_command,
    armada_monitor_spec_relative_path,
    armada_validate_monitor_spec,
)
from zerver.lib.armada_worker import (
    ArmadaDatabaseWorkerRunStore,
    ArmadaQueueWorkerDispatcher,
    armada_expected_project_cache_path,
    armada_expected_topic_workdir,
    armada_git_branch_name_is_valid,
    armada_git_current_branch_name,
    armada_git_output,
    armada_git_status_delta,
    armada_initialize_bare_origin,
    armada_read_text_path,
    armada_referenced_conversations,
    armada_run_git_checked,
    armada_seed_empty_bare_origin,
    armada_topic_branch_name,
    armada_topic_isolation_user_exists,
    armada_topic_isolation_username,
    armada_topic_process_isolation,
    armada_topic_workdir_root,
    armada_worker_model_for_topic,
    armada_worker_process_isolation_error,
    armada_worker_reasoning_effort_for_topic,
    armada_worker_runtime_isolation_error,
    armada_worker_sandbox_for_topic,
    armada_worker_workdir_for_topic,
    armada_workspace_node_default_branch,
    armada_workspace_relative_path,
    build_armada_worker_runtime_from_settings,
    build_armada_workspace_context,
    configure_armada_channel_repository_workspace,
    ensure_armada_project_origin,
    ensure_armada_topic_workdir,
    ensure_armada_workspace_node,
    invalidate_armada_git_status_delta,
    latest_armada_codex_run_for_topic,
    load_armada_active_session_from_zulip,
    load_armada_referenced_threads,
    maintain_armada_worker_run_liveness,
    maybe_refactor_armada_stream_workspaces_on_rename,
    maybe_refactor_armada_workspace_on_move,
    reap_stale_armada_worker_runs,
    wait_for_redelivered_armada_worker_run,
)
from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.event_schema import check_armada_run_event, check_armada_workspace_changed_event
from zerver.lib.events import fetch_initial_state_data
from zerver.lib.markdown import markdown_convert
from zerver.lib.message_cache import MessageDict
from zerver.lib.partner import get_agent_bot
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, Recipient, Subscription, UserProfile
from zerver.models.bots import AgentMonitor, AgentRun, AgentRunEvent, Repository, Workspace
from zerver.models.realms import get_realm
from zerver.models.streams import get_stream
from zerver.views.armada import (
    ARMADA_MODEL_CATALOG_CACHE_SECONDS,
    armada_model_catalog,
    armada_worker_run_summary_events,
    armada_workspace_isolation_username,
    build_armada_git_context,
    build_armada_runtime_config_payload,
    ensure_armada_configured_bot,
    extract_armada_codex_model_catalog,
    serialize_armada_worker_run,
    serialize_armada_workspace_context,
    update_armada_run_control_event,
)
from zerver.worker import armada_worker as armada_worker_module

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class ArmadaWorkerRunUsage(ArmadaTokenUsage):
    summary: str
    duration_seconds: float | None
    event_id: int | None


class ArmadaMonitorSpec(TypedDict):
    title: str
    cadence_seconds: int
    artifact: dict[str, object]
    recipe: dict[str, object]


class ArmadaSessionEndpointTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        # Every run/store path now resolves a per-channel Repository, which
        # requires the channel to exist; most tests in this class act on the
        # default Onix channel without creating it first.
        self.subscribe(self.example_user("hamlet"), ARMADA_DEFAULT_STREAM)
        # Topic workdirs would persist on disk across tests in this class while
        # the database resets, and the strict channel-repository attach check
        # then rejects the stale workdir. Each test gets its own root: the
        # default root sits next to the checkout, where a development server
        # keeps its real worktrees.
        workdir_root = tempfile.mkdtemp(prefix="armada-test-workspaces-")
        self.addCleanup(shutil.rmtree, workdir_root, ignore_errors=True)
        workdir_root_setting = self.settings(ARMADA_TOPIC_WORKDIR_ROOT=workdir_root)
        workdir_root_setting.enable()
        self.addCleanup(workdir_root_setting.disable)

    def create_git_repository(self, path: Path) -> None:
        path.mkdir()
        subprocess.run(["git", "-C", str(path), "init", "--quiet"], check=True, timeout=10)
        (path / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, timeout=10)
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "-c",
                "user.email=armada@example.com",
                "-c",
                "user.name=Onix",
                "commit",
                "--quiet",
                "-m",
                "initial",
            ],
            check=True,
            timeout=10,
        )

    def create_bare_origin(self, root: Path) -> tuple[Path, str]:
        source = root / "source"
        origin = root / "origin.git"
        self.create_git_repository(source)
        branch = armada_git_current_branch_name(source)
        subprocess.run(
            ["git", "clone", "--quiet", "--bare", str(source), str(origin)],
            check=True,
            timeout=10,
        )
        return origin, branch

    def test_armada_run_push_event_is_typed_and_visibility_scoped(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "armada-push",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        # This test exercises the typed browser event contract directly.  Do
        # not also wake the embedded bot worker when creating its fixture
        # message; that is a separate integration boundary with its own tests.
        with patch("zerver.worker.embedded_bots.queue_json_publish_rollback_unsafe"):
            trigger_message_id = self.send_stream_message(
                owner,
                ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
                content="@**Onix** check the repository",
            )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=trigger_message_id,
            result_message_id=trigger_message_id,
            status=AgentRun.RUNNING,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            current_activity="Running tests",
            tool_count=2,
        )
        run_event = AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Plan updated",
            payload={
                "kind": "plan",
                "metadata": {
                    "steps": [
                        {"step": "Inspect", "status": "completed"},
                        {"step": "Verify", "status": "in_progress"},
                    ]
                },
            },
        )

        with patch("zerver.actions.armada.send_event_on_commit") as send_event:
            send_armada_run_event(run, run_event)

        send_event.assert_called_once()
        event = send_event.call_args.args[1]
        check_armada_run_event("event", {**event, "id": 1})
        self.assertEqual(event["run_id"], run.id)
        self.assertEqual(event["sequence"], run_event.id)
        self.assertEqual(event["current_activity"], "Running tests")
        self.assertEqual(event["plan"][1], {"step": "Verify", "status": "in_progress"})
        self.assertIsNone(event["has_visible_assistant_text"])
        check_armada_run_event(
            "event",
            {**event, "id": 2, "has_visible_assistant_text": True},
        )
        self.assertIn(owner.id, send_event.call_args.args[2])

        run.codex_thread_id = "root-thread"
        run.save(update_fields=["codex_thread_id"])
        agent_event = AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Started Scout",
            payload={
                "kind": "session",
                "metadata": {
                    "codex_thread_id": "child-thread",
                    "thread": {
                        "id": "child-thread",
                        "parent_thread_id": "root-thread",
                        "agent_nickname": "Scout",
                        "status": {"type": "active"},
                    },
                },
            },
        )
        with patch("zerver.actions.armada.send_event_on_commit") as send_agent_event:
            send_armada_run_event(run, agent_event)

        agent_push = send_agent_event.call_args.args[1]
        check_armada_run_event("event", {**agent_push, "id": 3})
        self.assertEqual(agent_push["agent_thread_id"], "child-thread")
        self.assertEqual(
            agent_push["agents"],
            [
                {
                    "thread_id": "child-thread",
                    "nickname": "Scout",
                    "role": None,
                    "name": None,
                    "status": "active",
                    "current_activity": "",
                }
            ],
        )
        self.assertNotIn("agent", agent_push)

        activity_event = AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Inspect files",
            payload={
                "kind": "command",
                "title": "Inspect files",
                "metadata": {
                    "codex_method": "item/completed",
                    "codex_thread_id": "child-thread",
                    "parent_thread_id": "root-thread",
                },
            },
        )
        with (
            patch("zerver.actions.armada.send_event_on_commit") as send_activity_event,
            patch("zerver.actions.armada.serialize_armada_agent_threads") as serialize_agents,
        ):
            send_armada_run_event(run, activity_event)
        serialize_agents.assert_not_called()

        activity_push = send_activity_event.call_args.args[1]
        check_armada_run_event("event", {**activity_push, "id": 4})
        self.assertEqual(activity_push["agent_thread_id"], "child-thread")
        self.assertEqual(
            activity_push["agent"],
            {
                "thread_id": "child-thread",
                "current_activity": "Inspect files",
            },
        )
        self.assertNotIn("agents", activity_push)

        turn_event = AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Agent turn started",
            payload={
                "kind": "agent",
                "title": "Agent turn started",
                "metadata": {
                    "codex_method": "turn/started",
                    "codex_thread_id": "child-thread",
                    "codex_turn_id": "child-turn",
                    "parent_thread_id": "root-thread",
                },
            },
        )
        with (
            patch("zerver.actions.armada.send_event_on_commit") as send_turn_event,
            patch("zerver.actions.armada.serialize_armada_agent_threads") as serialize_agents,
        ):
            send_armada_run_event(run, turn_event)
        serialize_agents.assert_not_called()

        turn_push = send_turn_event.call_args.args[1]
        self.assertNotIn("agents", turn_push)
        self.assertEqual(
            turn_push["agent"],
            {
                "thread_id": "child-thread",
                "status": "active",
                "current_activity": "Agent turn started",
            },
        )

    def test_workspace_changed_push_event_is_typed_and_visibility_scoped(self) -> None:
        owner = self.example_user("hamlet")
        node = SimpleNamespace(
            uuid="12345678-1234-5678-1234-567812345678",
            realm=owner.realm,
            stream_name=ARMADA_DEFAULT_STREAM,
        )
        stream = SimpleNamespace(id=42)
        with (
            patch("zerver.actions.armada.Stream.objects.filter") as stream_filter,
            patch(
                "zerver.actions.armada.get_user_ids_for_streams",
                return_value={stream.id: {owner.id}},
            ),
            patch("zerver.actions.armada.send_event_on_commit") as send_event,
        ):
            stream_filter.return_value.first.return_value = stream
            send_armada_workspace_changed_event(node, 7)  # type: ignore[arg-type]

        send_event.assert_called_once()
        event = send_event.call_args.args[1]
        check_armada_workspace_changed_event("event", {**event, "id": 1})
        self.assertEqual(event["workspace_id"], str(node.uuid))
        self.assertEqual(event["revision"], 7)
        self.assertEqual(send_event.call_args.args[2], [owner.id])

    def test_get_armada_session(self) -> None:
        self.login("hamlet")

        result = self.client_get(
            f"/json{ARMADA_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        armada_session = result_dict["armada_session"]
        self.assertEqual(armada_session["agent_name"], "Onix")
        self.assertEqual(armada_session["stream"], ARMADA_DEFAULT_STREAM)
        self.assertEqual(armada_session["topic"], ARMADA_DEFAULT_TOPIC)
        self.assertIn("@**Onix**", armada_session["seed_message"])
        self.assertIn("Fix bug:", armada_session["fix_bug_message"])
        self.assertIn("Status check:", armada_session["status_check_message"])
        self.assertIn("Handoff summary:", armada_session["handoff_message"])
        self.assertIn("Catch up:", armada_session["catch_up_message"])
        self.assertIn("Triage request:", armada_session["triage_message"])
        self.assertIn("Find answer:", armada_session["find_answer_message"])
        self.assertIn("Review changes:", armada_session["review_message"])
        self.assertIn("Implementation plan:", armada_session["plan_message"])
        self.assertIn("Checkpoint:", armada_session["checkpoint_message"])
        self.assertIn("Apply plan:", armada_session["apply_plan_message"])
        self.assertIn("Save work:", armada_session["save_work_message"])
        self.assertIn("Verify changes:", armada_session["verify_changes_message"])
        self.assertIn("Draft artifact:", armada_session["draft_artifact_message"])
        self.assertEqual(
            armada_session["official_agent_runtime"],
            ARMADA_OFFICIAL_AGENT_RUNTIME,
        )
        self.assertEqual(
            armada_session["official_agent_runtime_label"],
            ARMADA_OFFICIAL_AGENT_RUNTIME_LABEL,
        )
        self.assertEqual(
            armada_session["agent_runtime_packages"],
            list(ARMADA_AGENT_RUNTIME_PACKAGES),
        )
        self.assertEqual(
            armada_session["official_codex_sdk_packages"],
            list(ARMADA_OFFICIAL_CODEX_SDK_PACKAGES),
        )
        self.assertEqual(armada_session["codex_mcp_tools"], list(ARMADA_CODEX_MCP_TOOLS))

    def test_armada_session_csrf_token_and_user_id(self) -> None:
        hamlet = self.example_user("hamlet")

        def sign_in() -> str:
            # Through the login form, as a browser does, so that the
            # CSRF cookie is the one Django's login rotates. The forms'
            # own CSRF checks are not what is tested here.
            self.client.handler.enforce_csrf_checks = False
            self.assertEqual(self.login_with_return(hamlet.delivery_email).status_code, 302)
            self.client.handler.enforce_csrf_checks = True
            return self.client.cookies[settings.CSRF_COOKIE_NAME].value

        def sign_out() -> None:
            self.client.handler.enforce_csrf_checks = False
            self.assertEqual(self.client_post("/accounts/logout/").status_code, 302)
            self.client.handler.enforce_csrf_checks = True

        def send(csrf_token: str) -> "TestHttpResponse":
            return self.client_post(
                "/json/messages",
                {
                    "type": "stream",
                    "to": orjson.dumps("Verona").decode(),
                    "topic": "csrf",
                    "content": "hello",
                },
                HTTP_X_CSRFTOKEN=csrf_token,
            )

        def session() -> dict[str, Any]:
            result = self.client_get(
                f"/json{ARMADA_SESSION_ENDPOINT}", intentionally_undocumented=True
            )
            return self.assert_json_success(result)

        first_cookie = sign_in()
        first = session()
        self.assertEqual(first["user_id"], hamlet.id)
        self.assert_json_success(send(first["csrf_token"]))

        # Signing out and in again in another tab gives the browser a new
        # CSRF secret, which the open page cannot read; the token it holds
        # is refused, and the session endpoint answers with one that works.
        # (Signing in while signed in only redirects, rotating nothing.)
        sign_out()
        self.assertNotEqual(sign_in(), first_cookie)
        with self.assertLogs("django.security.csrf", "WARNING") as logs:
            result = send(first["csrf_token"])
        self.assertEqual(
            logs.output,
            [
                "WARNING:django.security.csrf:Forbidden (CSRF token from the 'X-Csrftoken'"
                " HTTP header incorrect.): /json/messages"
            ],
        )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()["code"], "CSRF_FAILED")

        second = session()
        self.assertNotEqual(second["csrf_token"], first["csrf_token"])
        self.assert_json_success(send(second["csrf_token"]))

    def test_api_v1_armada_session(self) -> None:
        user = self.example_user("hamlet")

        result = self.client_get(
            f"/api/v1{ARMADA_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
            HTTP_AUTHORIZATION=self.encode_user(user),
        )

        result_dict = self.assert_json_success(result)
        self.assertEqual(result_dict["armada_session"]["agent_name"], "Onix")

    def test_get_armada_config_without_bot(self) -> None:
        self.login("hamlet")

        result = self.client_get(
            f"/json{ARMADA_CONFIG_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        armada_config = result_dict["armada_config"]
        self.assertEqual(armada_config["bot_short_name"], "onix")
        self.assertEqual(armada_config["embedded_bot_service"], ARMADA_EMBEDDED_BOT_SERVICE)
        self.assertFalse(armada_config["configured"])
        self.assertEqual(armada_config["worker_agent_runtime"], "disabled")
        self.assertFalse(armada_config["worker_runtime_ready"])
        self.assertFalse(armada_config["worker_runtime_auth_ready"])
        self.assertIsNone(armada_config["worker_runtime_login_method"])
        self.assertIsNone(armada_config["worker_runtime_login_command"])
        self.assertIsNone(armada_config["worker_runtime_login_target"])
        self.assertIsNone(armada_config["worker_runtime_login_instructions"])
        self.assertEqual(armada_config["bot_count"], 0)
        self.assertIsNone(armada_config["bot_user_id"])
        self.assertIsNone(armada_config["selected_bot_user_id"])
        self.assertEqual(armada_config["bot_identities"], [])
        self.assertEqual(
            [step["key"] for step in armada_config["setup_steps"]],
            ["embedded_bots", "armada_bot", "worker_runtime", "worker_auth"],
        )

    def test_get_armada_config_with_bot(self) -> None:
        owner = self.example_user("hamlet")
        with (
            self.settings(
                EMBEDDED_BOTS_ENABLED=True,
                ARMADA_WORKER_AGENT_RUNTIME="codex-app-server",
            ),
            patch(
                "zerver.views.armada.armada_worker_runtime_dependency_status",
                return_value=(
                    True,
                    "codex app-server via /usr/local/bin/codex.",
                ),
            ),
            patch(
                "zerver.views.armada.armada_worker_runtime_auth_status",
                return_value=(
                    True,
                    "Codex CLI is logged in.",
                    "codex-cli",
                    "codex login",
                    "worker-environment",
                    "Run Codex CLI login inside the worker environment used by Onix.",
                ),
            ),
        ):
            armada_bot = self.create_test_bot(
                "onix",
                owner,
                full_name="Onix",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=ARMADA_EMBEDDED_BOT_SERVICE,
            )
            self.login_user(owner)

            result = self.client_get(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        armada_config = result_dict["armada_config"]
        self.assertTrue(armada_config["embedded_bots_enabled"])
        self.assertTrue(armada_config["configured"])
        self.assertTrue(armada_config["enabled"])
        self.assertEqual(armada_config["bot_count"], 1)
        self.assertEqual(armada_config["bot_user_id"], armada_bot.id)
        self.assertEqual(armada_config["selected_bot_user_id"], armada_bot.id)
        self.assertEqual(armada_config["selected_bot_full_name"], "Onix")
        self.assertEqual(armada_config["bot_full_name"], "Onix")
        self.assertEqual(armada_config["worker_agent_runtime"], "codex-app-server")
        self.assertTrue(armada_config["worker_runtime_ready"])
        self.assertTrue(armada_config["worker_runtime_auth_ready"])
        self.assertEqual(armada_config["worker_runtime_login_method"], "codex-cli")
        self.assertEqual(armada_config["worker_runtime_login_command"], "codex login")
        self.assertEqual(armada_config["worker_runtime_login_target"], "worker-environment")
        self.assertIn("worker environment", armada_config["worker_runtime_login_instructions"])
        self.assertEqual(
            armada_config["setup_steps"][2]["detail"],
            "codex app-server via /usr/local/bin/codex.",
        )
        self.assertEqual(
            armada_config["setup_steps"][3]["detail"],
            "Codex CLI is logged in.",
        )
        self.assertEqual(
            armada_config["bot_identities"],
            [
                {
                    "bot_user_id": armada_bot.id,
                    "bot_full_name": "Onix",
                    "bot_email": armada_bot.email,
                    "selected": True,
                }
            ],
        )

    def test_get_armada_config_reports_missing_runtime_dependency(self) -> None:
        owner = self.example_user("hamlet")
        with (
            self.settings(
                EMBEDDED_BOTS_ENABLED=True,
                ARMADA_WORKER_AGENT_RUNTIME="codex-app-server",
            ),
            patch(
                "zerver.views.armada.armada_worker_runtime_dependency_status",
                return_value=(False, "Missing codex executable."),
            ),
            patch(
                "zerver.views.armada.armada_worker_runtime_auth_status",
                return_value=(
                    True,
                    "Codex CLI is logged in.",
                    "codex-cli",
                    "codex login",
                    "worker-environment",
                    "Run Codex CLI login inside the worker environment used by Onix.",
                ),
            ),
        ):
            armada_bot = self.create_test_bot(
                "onix",
                owner,
                full_name="Onix",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=ARMADA_EMBEDDED_BOT_SERVICE,
            )
            self.login_user(owner)

            result = self.client_get(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        armada_config = result_dict["armada_config"]
        self.assertEqual(armada_config["selected_bot_user_id"], armada_bot.id)
        self.assertEqual(armada_config["worker_agent_runtime"], "codex-app-server")
        self.assertFalse(armada_config["worker_runtime_ready"])
        self.assertEqual(
            armada_config["setup_steps"][2],
            {
                "key": "worker_runtime",
                "label": "Worker runtime dependencies",
                "complete": False,
                "detail": "Missing codex executable.",
            },
        )
        self.assertEqual(
            armada_config["setup_steps"][3],
            {
                "key": "worker_auth",
                "label": "Worker runtime login",
                "complete": True,
                "detail": "Codex CLI is logged in.",
            },
        )

    def test_worker_runtime_dependency_status_checks_official_surfaces(self) -> None:
        from zerver.views.armada import armada_worker_runtime_dependency_status

        with patch("zerver.views.armada.which", return_value="/usr/local/bin/codex"):
            self.assertEqual(
                armada_worker_runtime_dependency_status("codex-app-server"),
                (True, "codex app-server via /usr/local/bin/codex."),
            )

        with patch("zerver.views.armada.which", return_value=None):
            available, detail = armada_worker_runtime_dependency_status("codex-app-server")
            self.assertFalse(available)
            self.assertIn("codex executable", detail)

        available, detail = armada_worker_runtime_dependency_status("codex-agents-mcp")
        self.assertFalse(available)
        self.assertEqual(detail, "Set ARMADA_WORKER_AGENT_RUNTIME to codex-app-server.")

    def test_worker_runtime_auth_status_reports_login_paths(self) -> None:
        from zerver.views.armada import armada_worker_runtime_auth_status

        with patch(
            "zerver.views.armada.armada_codex_login_available",
            return_value=True,
        ):
            (
                available,
                detail,
                login_method,
                login_command,
                login_target,
                login_instructions,
            ) = armada_worker_runtime_auth_status("codex-app-server")
            self.assertTrue(available)
            self.assertEqual(detail, "Codex CLI is logged in.")
            self.assertEqual(login_method, "codex-cli")
            self.assertEqual(login_command, "codex login")
            self.assertEqual(login_target, "worker-environment")
            assert login_instructions is not None
            self.assertIn("worker environment", login_instructions)

            (
                available,
                detail,
                login_method,
                login_command,
                login_target,
                login_instructions,
            ) = armada_worker_runtime_auth_status("codex-agents-mcp")
            self.assertFalse(available)
            self.assertEqual(detail, "Select a supported worker runtime before logging in.")
            self.assertIsNone(login_method)
            self.assertIsNone(login_command)
            self.assertIsNone(login_target)
            self.assertIsNone(login_instructions)

    def test_serialize_armada_worker_run_includes_usage_summary(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.addCleanup(invalidate_armada_selected_bot_cache, user.realm_id)
        run = AgentRun.objects.create(
            realm=user.realm,
            bot_profile=armada_bot,
            trigger_message_id=1,
            requesting_user=user,
            status="running",
            stream_name="devel",
            topic_name="cache test",
            runtime_label="Codex app-server",
            model="gpt-5.5",
            workdir="/workspace",
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="usage",
            summary="Token usage: 80/100 cached (80.0%); 4 output; 2 reasoning.",
            payload={
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 106,
                    "cache_percent": 80.0,
                }
            },
        )
        write_armada_live_run_state(
            run.trigger_message_id,
            status="running",
            has_visible_assistant_text=True,
        )

        serialized_run = serialize_armada_worker_run(run)
        usage = cast(ArmadaWorkerRunUsage | None, serialized_run["usage"])

        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["cached_input_tokens"], 80)
        self.assertEqual(usage["cache_percent"], 80.0)
        self.assertIn("80/100 cached", usage["summary"])
        live = cast(ArmadaLiveRunState | None, serialized_run["live"])
        self.assertIsNotNone(live)
        assert live is not None
        self.assertEqual(live["status"], "running")
        self.assertIsInstance(live["updated_at"], float)
        self.assertIs(live["has_visible_assistant_text"], True)
        # No tool_results events yet.
        self.assertEqual(serialized_run["tool_count"], 0)

        # The run's final events are usage-less transcript markers;
        # the serializer must scan back to the usage-bearing event
        # rather than trust the single latest one (a live regression:
        # every run reported usage=None once "turn completed" landed).
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Codex turn completed.",
            payload={"kind": "status", "title": "Codex turn completed.", "status": "done"},
        )
        usage_after_completion = cast(
            ArmadaWorkerRunUsage | None, serialize_armada_worker_run(run)["usage"]
        )
        self.assertIsNotNone(usage_after_completion)
        assert usage_after_completion is not None
        self.assertEqual(usage_after_completion["total_tokens"], 106)

        # The run footer sums tool_count across the per-segment
        # tool_results events; malformed payloads are skipped.
        AgentRunEvent.objects.create(
            run=run,
            event_type="tool_results",
            summary="Ran 2 tools",
            payload={"tool_count": 2, "tool_results": []},
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="tool_results",
            summary="Ran 3 tools",
            payload={"tool_count": 3, "tool_results": []},
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="tool_results",
            summary="Malformed",
            payload={"tool_count": "nope"},
        )
        self.assertEqual(serialize_armada_worker_run(run)["tool_count"], 5)

        # The rail's plan card takes the newest plan_update; the scan
        # must skip past newer non-plan transcript events.
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Plan",
            payload={
                "kind": "plan",
                "metadata": {
                    "steps": [
                        {"step": "Check branch", "status": "completed"},
                        {"step": "Commit fix", "status": "in_progress"},
                        {"step": "Verify", "status": "pending"},
                    ]
                },
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Codex turn completed.",
            payload={"kind": "status", "title": "Codex turn completed.", "status": "done"},
        )
        plan = serialize_armada_worker_run(run)["plan"]
        self.assertEqual(
            plan,
            [
                {"step": "Check branch", "status": "completed"},
                {"step": "Commit fix", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ],
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="presentation",
            summary="Committing focused fix",
            payload={
                "overview": "Committing focused fix",
                "current_step": "Commit fix",
            },
        )
        self.assertEqual(serialize_armada_worker_run(run)["overview"], "Committing focused fix")
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Plan advanced",
            payload={
                "kind": "plan",
                "metadata": {
                    "steps": [
                        {"step": "Check branch", "status": "completed"},
                        {"step": "Commit fix", "status": "completed"},
                        {"step": "Verify", "status": "in_progress"},
                    ]
                },
            },
        )
        self.assertIsNone(serialize_armada_worker_run(run)["overview"])

    def test_serialize_armada_worker_run_hides_stale_active_live_state(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=user.realm,
            bot_profile=armada_bot,
            trigger_message_id=1,
            requesting_user=user,
            status=AgentRun.DONE,
            stream_name="devel",
            topic_name="cache test",
            runtime_label="Codex app-server",
            model="gpt-5.5",
            workdir="/workspace",
        )
        write_armada_live_run_state(
            run.trigger_message_id,
            status=AgentRun.RUNNING,
        )

        serialized_run = serialize_armada_worker_run(run)

        self.assertEqual(serialized_run["status"], AgentRun.DONE)
        self.assertIsNone(serialized_run["live"])

    def test_run_summary_events_are_batched_for_polling(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        runs = [
            AgentRun.objects.create(
                realm=user.realm,
                bot_profile=armada_bot,
                trigger_message_id=100 + index,
                requesting_user=user,
                status=AgentRun.RUNNING,
                tool_count=index + 2,
            )
            for index in range(5)
        ]
        for index, run in enumerate(runs):
            AgentRunEvent.objects.create(
                run=run,
                event_type="usage",
                payload={"usage": {"input_tokens": index + 1, "total_tokens": index + 1}},
            )
            AgentRunEvent.objects.create(
                run=run,
                event_type="transcript",
                payload={
                    "kind": "plan",
                    "metadata": {"steps": [{"step": f"Step {index}", "status": "in_progress"}]},
                },
            )
            AgentRunEvent.objects.create(
                run=run,
                event_type="presentation",
                payload={
                    "overview": f"Visible overview {index}.",
                    "current_step": f"Step {index}",
                },
            )

        # Usage, display state, and the bounded child-session lookup are one
        # fixed batch each. With no child sessions, no reducer query is needed.
        with self.assert_database_query_count(3):
            events_by_run_id = armada_worker_run_summary_events(runs)
        with self.assert_database_query_count(0):
            serialized = [
                serialize_armada_worker_run(run, summary_events=events_by_run_id[run.id])
                for run in runs
            ]

        self.assertEqual([item["tool_count"] for item in serialized], [2, 3, 4, 5, 6])
        self.assertEqual(
            [item["usage"]["input_tokens"] for item in serialized],  # type: ignore[index]
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [item["overview"] for item in serialized],
            [f"Visible overview {index}." for index in range(5)],
        )

    def test_run_summary_keeps_each_agent_latest_lifecycle_event(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=user.realm,
            bot_profile=armada_bot,
            trigger_message_id=710,
            requesting_user=user,
            status=AgentRun.RUNNING,
            codex_thread_id="root-thread",
        )
        for thread_id, nickname in (
            ("quiet-child", "  Quiet\nScout  "),
            ("noisy-child", "Builder"),
        ):
            AgentRunEvent.objects.create(
                run=run,
                event_type="agent",
                summary=f"Started {nickname}",
                payload={
                    "kind": "session",
                    "metadata": {
                        "codex_thread_id": thread_id,
                        "thread": {
                            "id": thread_id,
                            "parent_thread_id": "root-thread",
                            "agent_nickname": nickname,
                            "agent_path": f"unused/{thread_id}",
                            "depth": 1,
                            "status": {"type": "active"},
                        },
                    },
                },
            )
        AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Quiet child failed",
            payload={
                "kind": "status",
                "title": "Quiet child failed",
                "metadata": {
                    "codex_method": "turn/completed",
                    "codex_thread_id": "quiet-child",
                    "codex_turn_id": "quiet-turn",
                    "turn_status": "failed",
                },
            },
        )
        AgentRunEvent.objects.bulk_create(
            [
                AgentRunEvent(
                    run=run,
                    event_type="agent",
                    summary=f"Noisy activity {index}",
                    payload={
                        "kind": "command",
                        "title": f"Noisy activity {index}",
                        "metadata": {
                            "codex_method": "item/completed",
                            "codex_thread_id": "noisy-child",
                        },
                    },
                )
                for index in range(70)
            ]
        )

        # The fourth query is one latest lifecycle/activity reducer row per
        # native child, not a global tail that a noisy sibling can monopolize.
        with self.assert_database_query_count(4):
            events_by_run_id = armada_worker_run_summary_events([run])
        with self.assert_database_query_count(0):
            agents = serialize_armada_worker_run(run, summary_events=events_by_run_id[run.id])[
                "agents"
            ]
        assert isinstance(agents, list)
        [quiet, noisy] = agents

        self.assertEqual(quiet["thread_id"], "quiet-child")
        self.assertEqual(quiet["nickname"], "Quiet Scout")
        self.assertEqual(quiet["status"], "failed")
        self.assertEqual(
            set(quiet),
            {"thread_id", "nickname", "role", "name", "status", "current_activity"},
        )
        self.assertEqual(noisy["thread_id"], "noisy-child")
        self.assertEqual(noisy["status"], "active")
        self.assertEqual(noisy["current_activity"], "Noisy activity 69")

    def test_get_armada_runs_includes_requested_run_ids_outside_topic_window(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        requested_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=101,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="pinned older run",
            status=AgentRun.DONE,
        )
        topic_runs = [
            AgentRun.objects.create(
                realm=owner.realm,
                bot_profile=armada_bot,
                requesting_user=owner,
                trigger_message_id=200 + index,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
                status=AgentRun.DONE,
            )
            for index in range(25)
        ]
        self.login_user(owner)

        result = self.client_get(
            f"/json{ARMADA_RUNS_ENDPOINT}",
            {
                "stream": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "run_ids": orjson.dumps([requested_run.id]).decode(),
                "include_context": orjson.dumps(False).decode(),
            },
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        run_ids = [run["id"] for run in result_dict["armada_runs"]]
        self.assertIn(requested_run.id, run_ids)
        self.assertIn(topic_runs[-1].id, run_ids)
        self.assertNotIn(topic_runs[0].id, run_ids)
        self.assertIsNone(result_dict["armada_topic_context"])

    def test_get_armada_runs_carries_authoritative_event_watermark(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "armada-watermark",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=251,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.RUNNING,
        )
        first_event = AgentRunEvent.objects.create(
            run=run,
            event_type="running",
            summary="Started.",
            payload={},
        )
        latest_event = AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Working.",
            payload={},
        )
        self.assertGreater(latest_event.id, first_event.id)
        self.login_user(owner)

        result = self.client_get(
            f"/json{ARMADA_RUNS_ENDPOINT}",
            {
                "stream": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
            },
            intentionally_undocumented=True,
        )

        serialized_run = self.assert_json_success(result)["armada_runs"][0]
        self.assertEqual(serialized_run["id"], run.id)
        self.assertEqual(serialized_run["snapshot_sequence"], latest_event.id)

    def test_get_armada_runs_defaults_to_cheap_without_topic_context(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)

        with patch("zerver.views.armada.ensure_armada_workspace_node") as ensure_context:
            result = self.client_get(
                f"/json{ARMADA_RUNS_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        self.assertEqual(result_dict["armada_runs"], [])
        self.assertIsNone(result_dict["armada_topic_context"])
        ensure_context.assert_not_called()

    def test_get_armada_runs_rejects_inaccessible_requested_run_id(self) -> None:
        owner = self.example_user("hamlet")
        other_user = self.example_user("othello")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        other_side_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=other_user,
            trigger_message_id=301,
            stream_name="direct messages",
            topic_name="side",
            session_kind=AgentRun.SIDE,
            source_stream_name=ARMADA_DEFAULT_STREAM,
            source_topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.DONE,
        )
        self.login_user(owner)

        result = self.client_get(
            f"/json{ARMADA_RUNS_ENDPOINT}",
            {
                "run_ids": orjson.dumps([other_side_run.id]).decode(),
                "include_context": orjson.dumps(False).decode(),
            },
            intentionally_undocumented=True,
        )

        self.assert_json_error(result, "Run not found.")

    def test_get_armada_runs_limits_requested_run_ids(self) -> None:
        owner = self.example_user("hamlet")
        self.login_user(owner)

        result = self.client_get(
            f"/json{ARMADA_RUNS_ENDPOINT}",
            {
                "run_ids": orjson.dumps(list(range(1, 22))).decode(),
                "include_context": orjson.dumps(False).decode(),
            },
            intentionally_undocumented=True,
        )

        self.assert_json_error(result, "Too many runs requested.")

    def test_post_armada_config_creates_bot(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)
        cache.set(
            armada_selected_bot_cache_key(owner.realm_id),
            {"user_profile_id": None},
        )

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )

            result_dict = self.assert_json_success(result)
            armada_config = result_dict["armada_config"]
            self.assertTrue(result_dict["created"])
            self.assertTrue(armada_config["enabled"])
            self.assertEqual(armada_config["bot_short_name"], "onix")
            self.assertEqual(armada_config["embedded_bot_service"], ARMADA_EMBEDDED_BOT_SERVICE)
            self.assertEqual(armada_config["bot_count"], 1)
            self.assertEqual(armada_config["bot_full_name"], "Onix")
            self.assertEqual(armada_config["worker_agent_runtime"], "disabled")
            self.assertFalse(armada_config["worker_runtime_ready"])
            self.assertEqual(
                armada_config["selected_bot_user_id"],
                armada_config["bot_user_id"],
            )
            self.assertEqual(len(armada_config["bot_identities"]), 1)
            self.assertTrue(armada_config["bot_identities"][0]["selected"])
            self.assertEqual(
                get_selected_armada_bot_user_id_for_realm(owner.realm_id),
                armada_config["bot_user_id"],
            )

            second_result = self.client_post(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )

        second_result_dict = self.assert_json_success(second_result)
        self.assertFalse(second_result_dict["created"])
        self.assertEqual(
            second_result_dict["armada_config"]["bot_user_id"],
            armada_config["bot_user_id"],
        )
        self.assertEqual(
            second_result_dict["armada_config"]["selected_bot_user_id"],
            armada_config["selected_bot_user_id"],
        )

    def test_ensure_armada_configured_bot_without_a_request(self) -> None:
        # populate_db turns the agent on for the development realm this
        # way, with no request at hand.
        owner = self.example_user("desdemona")

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            service, armada_config, created, _selection_changed = ensure_armada_configured_bot(
                owner
            )
            second_service, _second_config, second_created, _second_selection_changed = (
                ensure_armada_configured_bot(owner)
            )

        armada_bot = service.user_profile
        self.assertTrue(created)
        self.assertEqual(service.name, ARMADA_EMBEDDED_BOT_SERVICE)
        self.assertEqual(armada_bot.bot_type, UserProfile.EMBEDDED_BOT)
        self.assertEqual(armada_bot.full_name, "Onix")
        self.assertEqual(armada_bot.bot_owner_id, owner.id)
        self.assertEqual(armada_config["bot_user_id"], armada_bot.id)
        self.assertEqual(get_agent_bot(owner.realm_id), armada_bot)
        self.assertFalse(second_created)
        self.assertEqual(second_service.user_profile_id, armada_bot.id)

    def test_post_armada_config_makes_bot_from_its_own_fields(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                {"default_sending_stream": "Verona"},
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(
            result, ignored_parameters=["default_sending_stream"]
        )
        self.assertTrue(result_dict["created"])
        armada_bot = UserProfile.objects.get(id=result_dict["armada_config"]["bot_user_id"])
        self.assertIsNone(armada_bot.default_sending_stream)

    def test_armada_followup_gate_defaults_new_topics_to_ambient(self) -> None:
        owner = self.example_user("iago")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        cache.delete(armada_selected_bot_cache_key(owner.realm_id))

        with self.settings(
            EMBEDDED_BOTS_ENABLED=True,
            ARMADA_ADDRESSING_ROUTER_RUNTIME="gemini-interactions",
        ):
            missing_context_event = get_armada_followup_service_bot_event(
                realm_id=owner.realm_id,
                recipient_id=123,
                recipient_type=Recipient.STREAM,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name="missing-context",
                content="can someone check the failing test?",
                mentioned_user_ids=set(),
            )
            Workspace.objects.create(
                realm=owner.realm,
                stream_name=ARMADA_DEFAULT_STREAM.upper(),
                topic_name="Quiet-Context",
                response_policy="mentions_only",
            )
            quiet_context_event = get_armada_followup_service_bot_event(
                realm_id=owner.realm_id,
                recipient_id=123,
                recipient_type=Recipient.STREAM,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name="quiet-context",
                content="can someone check the failing test?",
                mentioned_user_ids=set(),
            )

        self.assertEqual(
            missing_context_event,
            {
                "trigger": "agent_followup",
                "user_profile_id": armada_bot.id,
            },
        )
        self.assertIsNone(quiet_context_event)

    def test_post_armada_first_run_creates_sandbox_once(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)
        cache.set(
            armada_selected_bot_cache_key(owner.realm_id),
            {"user_profile_id": None},
        )

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{ARMADA_FIRST_RUN_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )
            second_result = self.client_post(
                f"/json{ARMADA_FIRST_RUN_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        first_run = result_dict["armada_first_run"]
        self.assertEqual(result_dict["narrow_url"], first_run["narrow_url"])
        self.assertEqual(first_run["stream"], ARMADA_FIRST_RUN_STREAM)
        self.assertEqual(first_run["topic"], ARMADA_FIRST_RUN_TOPIC)
        self.assertTrue(first_run["bot_created"])
        self.assertTrue(first_run["stream_created"])
        self.assertTrue(first_run["message_created"])
        self.assertTrue(first_run["armada_config"]["enabled"])
        self.assertEqual(
            get_selected_armada_bot_user_id_for_realm(owner.realm_id),
            first_run["armada_config"]["bot_user_id"],
        )
        self.assertIn("/#narrow/channel/", first_run["narrow_url"])
        self.assertIn("/topic/", first_run["narrow_url"])

        stream = get_stream(ARMADA_FIRST_RUN_STREAM, owner.realm)
        self.assertEqual(first_run["stream_id"], stream.id)
        armada_bot = UserProfile.objects.get(id=first_run["armada_config"]["bot_user_id"])
        for subscriber in (owner, armada_bot):
            self.assertTrue(
                Subscription.objects.filter(
                    user_profile=subscriber,
                    recipient=stream.recipient,
                    active=True,
                ).exists()
            )

        message = Message.objects.get(id=first_run["message_id"])
        self.assertEqual(message.sender_id, owner.id)
        self.assertEqual(message.recipient_id, stream.recipient_id)
        self.assertEqual(message.topic_name(), ARMADA_FIRST_RUN_TOPIC)
        self.assertEqual(message.content, ARMADA_FIRST_RUN_STARTER_MESSAGE)
        self.assertEqual(
            Message.objects.filter(
                realm=owner.realm,
                recipient=stream.recipient,
                content=ARMADA_FIRST_RUN_STARTER_MESSAGE,
            ).count(),
            1,
        )

        second_result_dict = self.assert_json_success(second_result)
        second_first_run = second_result_dict["armada_first_run"]
        self.assertFalse(second_first_run["bot_created"])
        self.assertFalse(second_first_run["stream_created"])
        self.assertFalse(second_first_run["message_created"])
        self.assertFalse(second_first_run["selection_changed"])
        self.assertEqual(second_first_run["message_id"], first_run["message_id"])
        self.assertEqual(
            Message.objects.filter(
                realm=owner.realm,
                recipient=stream.recipient,
                content=ARMADA_FIRST_RUN_STARTER_MESSAGE,
            ).count(),
            1,
        )

    def test_post_armada_first_run_requires_admin(self) -> None:
        self.login("hamlet")

        result = self.client_post(
            f"/json{ARMADA_FIRST_RUN_ENDPOINT}",
            {},
            intentionally_undocumented=True,
        )

        self.assert_json_error(result, "Must be an organization administrator")

    def test_post_armada_config_selects_existing_bot(self) -> None:
        owner = self.example_user("iago")
        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            armada_bot = self.create_test_bot(
                "onix",
                owner,
                full_name="Onix",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=ARMADA_EMBEDDED_BOT_SERVICE,
            )
            armada_infra_bot = self.create_test_bot(
                "armada-infra",
                owner,
                full_name="Onix Infra",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=ARMADA_EMBEDDED_BOT_SERVICE,
            )
            self.login_user(owner)
            self.assertEqual(
                get_selected_armada_bot_user_id_for_realm(owner.realm_id),
                armada_bot.id,
            )

            result = self.client_post(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                {"selected_bot_user_id": str(armada_infra_bot.id)},
                intentionally_undocumented=True,
            )
            followup_result = self.client_get(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        armada_config = result_dict["armada_config"]
        self.assertFalse(result_dict["created"])
        self.assertTrue(result_dict["selection_changed"])
        self.assertEqual(armada_config["bot_count"], 2)
        self.assertEqual(armada_config["bot_user_id"], armada_infra_bot.id)
        self.assertEqual(armada_config["selected_bot_user_id"], armada_infra_bot.id)
        self.assertEqual(armada_config["selected_bot_full_name"], "Onix Infra")
        self.assertEqual(
            armada_config["bot_identities"],
            [
                {
                    "bot_user_id": armada_bot.id,
                    "bot_full_name": "Onix",
                    "bot_email": armada_bot.email,
                    "selected": False,
                },
                {
                    "bot_user_id": armada_infra_bot.id,
                    "bot_full_name": "Onix Infra",
                    "bot_email": armada_infra_bot.email,
                    "selected": True,
                },
            ],
        )

        followup_result_dict = self.assert_json_success(followup_result)
        self.assertEqual(
            followup_result_dict["armada_config"]["selected_bot_user_id"],
            armada_infra_bot.id,
        )
        self.assertEqual(
            get_selected_armada_bot_user_id_for_realm(owner.realm_id),
            armada_infra_bot.id,
        )

    def test_post_armada_config_rejects_invalid_selection(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{ARMADA_CONFIG_ENDPOINT}",
                {"selected_bot_user_id": "999999"},
                intentionally_undocumented=True,
            )

        self.assert_json_error(result, "Invalid bot selection.")

    def test_get_armada_topic_context_is_read_only_without_existing_context(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_WORKER_MODEL="gpt-5.5",
                ARMADA_WORKER_MODEL_CHOICES=["gpt-5.5", "gpt-5.5-mini"],
                ARMADA_WORKER_REASONING_EFFORT="medium",
                ARMADA_WORKER_WORKDIR="/workspace/base",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            result = self.client_get(
                f"/json{ARMADA_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

            result_dict = self.assert_json_success(result)
            context_payload = result_dict["armada_topic_context"]

            self.assertEqual(context_payload["model"], "gpt-5.5")
            self.assertIn("gpt-5.5", context_payload["model_choices"])
            self.assertIn("gpt-5.5-mini", context_payload["model_choices"])
            self.assertEqual(context_payload["reasoning_effort"], "medium")
            self.assertEqual(context_payload["default_reasoning_effort"], "medium")
            self.assertEqual(context_payload["response_policy"], "ambient")
            self.assertEqual(context_payload["workdir"], "")
            self.assertFalse(context_payload["git"]["available"])
            self.assertFalse(
                Workspace.objects.filter(
                    realm=owner.realm,
                    stream_name=ARMADA_DEFAULT_STREAM,
                    topic_name=ARMADA_DEFAULT_TOPIC,
                ).exists()
            )

    def test_topic_context_can_skip_git_status_for_runtime_picker(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            self.settings(
                ARMADA_WORKER_MODEL="gpt-5.5",
                ARMADA_WORKER_REASONING_EFFORT="medium",
                ARMADA_WORKER_WORKDIR="/workspace/base",
            ),
            patch("zerver.views.armada.build_armada_git_context") as build_git_context,
        ):
            topic_result = self.client_get(
                f"/json{ARMADA_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "include_git": orjson.dumps(False).decode(),
                },
                intentionally_undocumented=True,
            )
            runs_result = self.client_get(
                f"/json{ARMADA_RUNS_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "include_context": orjson.dumps(True).decode(),
                    "include_git": orjson.dumps(False).decode(),
                },
                intentionally_undocumented=True,
            )

        topic_payload = self.assert_json_success(topic_result)["armada_topic_context"]
        runs_payload = self.assert_json_success(runs_result)["armada_topic_context"]
        self.assertNotIn("git", topic_payload)
        self.assertNotIn("git", runs_payload)
        self.assertEqual(topic_payload["model"], "gpt-5.5")
        self.assertEqual(runs_payload["reasoning_effort"], "medium")
        build_git_context.assert_not_called()

    def test_ensure_armada_workspace_node_does_not_create_readme(self) -> None:
        owner = self.example_user("hamlet")
        topic_name = "isolated topic"

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch(
                "zerver.lib.armada_worker.ensure_armada_topic_workdir",
                return_value="main",
            ),
        ):
            context = ensure_armada_workspace_node(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic=topic_name,
            )

        self.assertFalse((Path(context.workdir) / "README.md").exists())

    def test_armada_read_text_path_distinguishes_missing_from_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(armada_read_text_path(Path(temp_dir) / "missing.md"), "")

        with (
            patch(
                "zerver.lib.armada_worker.Path.read_text",
                side_effect=PermissionError,
            ),
            self.assertRaises(OSError),
        ):
            armada_read_text_path(Path("/unreadable/README.md"))

    def test_existing_base_workdir_topic_context_is_repaired(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = str(Path(temp_dir) / "base")
            topic_root = str(Path(temp_dir) / "topics")
            Workspace.objects.create(
                realm=owner.realm,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
                workdir=base_workdir,
            )

            with self.settings(
                ARMADA_WORKER_WORKDIR=base_workdir,
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=ARMADA_DEFAULT_TOPIC,
                )

            self.assertNotEqual(context.workdir, base_workdir)
            self.assertTrue(context.workdir.startswith(topic_root))
            self.assertTrue(Path(context.workdir).exists())

    def test_topic_branch_name_uses_valid_topic_and_sanitizes_invalid_topic(self) -> None:
        self.assertEqual(
            armada_topic_branch_name("armada-efficient-smoke"), "armada-efficient-smoke"
        )
        self.assertEqual(armada_topic_branch_name("Feature/Some Work!"), "Feature-Some-Work")
        self.assertEqual(armada_topic_branch_name(""), "topic")

    def test_branch_name_check_agrees_with_git(self) -> None:
        names = [
            "main",
            "feature/some-work",
            "Feature/Some Work!",
            "armada-efficient-smoke",
            "café/naïve-ünïcode",
            "@",
            "@{-1}",
            "a@{b",
            "HEAD",
            "head",
            "-leading-dash",
            "trailing.",
            "trailing.lock",
            "dir.lock/name",
            "name.locked",
            ".hidden",
            "dir/.hidden",
            "a..b",
            "a.b",
            "a//b",
            "/leading",
            "trailing/",
            "tab\there",
            "bell\x07",
            "del\x7f",
            "colon:name",
            "question?",
            "star*",
            "bracket[",
            "back\\slash",
            "caret^",
            "tilde~",
            "brace{}",
            "plus+comma,semicolon;",
            "quote'\"",
            "x" * 300,
        ]
        with (
            tempfile.TemporaryDirectory() as not_a_repository,
            patch("zerver.lib.armada_worker.subprocess.run") as subprocess_run,
        ):
            answers = {name: armada_git_branch_name_is_valid(name) for name in names}
            subprocess_run.assert_not_called()
            for name in names:
                completed = subprocess.Popen(
                    ["git", "check-ref-format", "--branch", name],
                    cwd=not_a_repository,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.assertEqual(answers[name], completed.wait(timeout=10) == 0, name)
        self.assertFalse(armada_git_branch_name_is_valid(""))
        self.assertFalse(armada_git_branch_name_is_valid("lone\ud800surrogate"))
        self.assertEqual(armada_topic_branch_name("lone\ud800surrogate"), "lone-surrogate")

    def test_workspace_default_branch_skips_blank_candidates(self) -> None:
        # An unset project base ref must not surface armada_topic_branch_name's
        # literal "topic" fallback as the branch for every workspace.
        self.assertEqual(armada_workspace_node_default_branch(), "main")
        self.assertEqual(
            armada_workspace_node_default_branch(topic="fable-ui-tour"),
            "fable-ui-tour",
        )
        self.assertEqual(
            armada_workspace_node_default_branch(topic="Feature/Some Work!"),
            "Feature-Some-Work",
        )

    def test_topic_workdir_uses_workspace_default_branch_when_created(self) -> None:
        owner = self.example_user("hamlet")
        topic = "armada-efficient-smoke"

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=topic,
                )

            completed = subprocess.run(
                ["git", "-C", context.workdir, "branch", "--show-current"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            actual_branch = completed.stdout.strip()
            # A fresh topic workspace works on a branch named for its
            # topic, so sibling topics never fight over the base branch.
            self.assertEqual(actual_branch, topic)
            context.refresh_from_db()
            self.assertEqual(context.stream_name, ARMADA_DEFAULT_STREAM)
            self.assertEqual(context.topic_name, topic)
            self.assertEqual(context.branch_name, actual_branch)
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", context.workdir, "rev-parse", "--is-inside-work-tree"],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                    timeout=10,
                ).stdout.strip(),
                "true",
            )

    def test_topic_context_payload_exposes_managed_workspace_identity(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir:
            # A dedicated base repository keeps worktree/branch creation
            # hermetic: against a shared checkout, the topic branch can
            # already be held by a stale worktree and fall into the
            # digest-suffix retry, making branch assertions flaky.
            base_workdir = Path(temp_dir) / "base"
            self.create_git_repository(base_workdir)
            with self.settings(
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics"),
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=ARMADA_DEFAULT_TOPIC,
                )
                payload = serialize_armada_workspace_context(
                    context,
                    stream_name=ARMADA_DEFAULT_STREAM,
                    topic_name=ARMADA_DEFAULT_TOPIC,
                )

        self.assertEqual(payload["workspace_id"], context.id)
        self.assertEqual(payload["workspace_uuid"], str(context.uuid))
        self.assertEqual(payload["branch"], context.branch_name)
        # Fresh workspaces work on the topic-derived branch, so clients can
        # collapse the redundant branch chip in the topic header.
        self.assertEqual(payload["branch"], armada_topic_branch_name(ARMADA_DEFAULT_TOPIC))
        self.assertTrue(payload["branch_is_topic_default"])
        # A freshly seeded local origin carries real provenance from day one:
        # the "main" base ref and the "Initialize workspace" seed commit.
        self.assertEqual(payload["base_ref"], "main")
        # The recorded HEAD pointer stays blank until the first refresh.
        self.assertEqual(payload["head"], "")

        # A legacy or custom branch is surprising and must stay visible.
        context.branch_name = "master"
        context.save(update_fields=["branch_name"])
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(ARMADA_TOPIC_WORKDIR_ROOT=temp_dir),
        ):
            legacy_payload = serialize_armada_workspace_context(
                context,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
            )
        self.assertEqual(legacy_payload["branch"], "master")
        self.assertFalse(legacy_payload["branch_is_topic_default"])

        # The empty topic ("general chat") derives the trunk default, so
        # its branch is also recognized as default rather than surprising.
        context.branch_name = "main"
        context.topic_name = ""
        context.save(update_fields=["branch_name", "topic_name"])
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(ARMADA_TOPIC_WORKDIR_ROOT=temp_dir),
        ):
            trunk_payload = serialize_armada_workspace_context(
                context,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name="",
            )
        self.assertEqual(trunk_payload["branch"], "main")
        self.assertTrue(trunk_payload["branch_is_topic_default"])

    def test_shared_pool_startup_reap_preserves_fresh_running_lease(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=user.realm,
            bot_profile=armada_bot,
            trigger_message_id=4242,
            requesting_user=user,
            status=AgentRun.RUNNING,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            heartbeat_at=timezone_now(),
        )

        # Either slot may boot while the other is running.  A fresh shared
        # heartbeat lease must therefore survive every startup reap.
        self.assertEqual(reap_stale_armada_worker_runs(), 0)
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.RUNNING)

    def test_redelivered_run_waits_for_the_database_lease(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=user.realm,
            bot_profile=armada_bot,
            trigger_message_id=4343,
            requesting_user=user,
            status=AgentRun.RUNNING,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            heartbeat_at=timezone_now(),
        )

        def expire_lease(_seconds: float) -> None:
            run.heartbeat_at = timezone_now() - timedelta(minutes=2)
            run.save(update_fields=["heartbeat_at"])

        with patch("zerver.lib.armada_worker.time.sleep", side_effect=expire_lease) as sleep:
            wait_for_redelivered_armada_worker_run(
                bot_profile_id=armada_bot.id,
                trigger_message_id=run.trigger_message_id,
            )

        sleep.assert_called_once()

    def test_worker_run_store_links_runs_to_workspace_node(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        store = ArmadaDatabaseWorkerRunStore()

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            started = store.start(
                SimpleNamespace(user_profile=armada_bot),
                9876,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )
            run = AgentRun.objects.get(trigger_message_id=9876)
            context = Workspace.objects.get(
                realm=owner.realm,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
            )

        self.assertTrue(started)
        self.assertEqual(run.workspace_node_id, context.id)

    def test_worker_run_store_starts_side_run_beside_active_shared_run(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        store = ArmadaDatabaseWorkerRunStore()
        bot_handler = SimpleNamespace(user_profile=armada_bot)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM=4,
                ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC=1,
            ),
        ):
            self.assertTrue(
                store.start(
                    bot_handler,
                    9876,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=ARMADA_DEFAULT_TOPIC,
                )
            )
            self.assertIsNone(
                store.side_start_blocked_reason(
                    bot_handler,
                    9877,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=ARMADA_DEFAULT_TOPIC,
                    requesting_user_id=owner.id,
                )
            )
            self.assertTrue(
                store.start_side(
                    bot_handler,
                    9877,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=ARMADA_DEFAULT_TOPIC,
                    requesting_user_id=owner.id,
                    source_message_id=9876,
                )
            )

        shared_run = AgentRun.objects.get(trigger_message_id=9876)
        side_run = AgentRun.objects.get(trigger_message_id=9877)
        self.assertEqual(side_run.session_kind, AgentRun.SIDE)
        self.assertEqual(side_run.workspace_node_id, shared_run.workspace_node_id)
        self.assertEqual(side_run.workdir, shared_run.workdir)
        self.assertEqual(side_run.source_stream_name, ARMADA_DEFAULT_STREAM)
        self.assertEqual(side_run.source_topic_name, ARMADA_DEFAULT_TOPIC)
        self.assertEqual(side_run.source_message_id, 9876)
        self.assertEqual(
            store.active_worker_run_message_id(
                bot_handler,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            ),
            9876,
        )

    def test_shared_run_redelivery_reuses_queued_run_and_progress_message(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            topic_name="shared-redelivery",
            content="@Onix inspect this",
        )
        trigger_message = Message.objects.get(id=trigger_message_id)
        message = MessageDict.wide_dict(trigger_message)
        store = ArmadaDatabaseWorkerRunStore()
        bot_handler = EmbeddedBotHandler(armada_bot)

        self.assertTrue(
            store.start(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="shared-redelivery",
            )
        )
        self.assertIsNone(
            store.start_blocked_reason(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="shared-redelivery",
                requesting_user_id=owner.id,
            )
        )
        # Redelivery after the durable row commit must republish this same run.
        self.assertTrue(
            store.start(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="shared-redelivery",
            )
        )
        progress_message_id = store.ensure_progress_message(
            bot_handler,
            trigger_message_id,
            message=message,
            stream=ARMADA_DEFAULT_STREAM,
            topic="shared-redelivery",
            content=ARMADA_WORKING_MESSAGE,
        )
        self.assertIsNotNone(progress_message_id)

        # Redelivery after progress registration reuses the ordinary Message.
        self.assertTrue(
            store.start(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="shared-redelivery",
            )
        )
        self.assertEqual(
            store.ensure_progress_message(
                bot_handler,
                trigger_message_id,
                message=message,
                stream=ARMADA_DEFAULT_STREAM,
                topic="shared-redelivery",
                content=ARMADA_WORKING_MESSAGE,
            ),
            progress_message_id,
        )
        self.assertEqual(
            Message.objects.filter(
                sender=armada_bot,
                subject="shared-redelivery",
                content=ARMADA_WORKING_MESSAGE,
            ).count(),
            1,
        )

    def test_side_run_redelivery_reuses_queued_run_and_progress_message(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        root_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            topic_name="side-redelivery",
            content="Root request",
        )
        trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            topic_name="side-redelivery",
            content="@Onix side question",
        )
        Message.objects.filter(id=trigger_message_id).update(thread_root_id=root_message_id)
        trigger_message = Message.objects.get(id=trigger_message_id)
        message = MessageDict.wide_dict(trigger_message)
        store = ArmadaDatabaseWorkerRunStore()
        bot_handler = EmbeddedBotHandler(armada_bot)
        self.assertTrue(
            store.start_side(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="side-redelivery",
                requesting_user_id=owner.id,
                source_message_id=root_message_id,
            )
        )
        self.assertIsNone(
            store.side_start_blocked_reason(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="side-redelivery",
                requesting_user_id=owner.id,
            )
        )
        self.assertTrue(
            store.start_side(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="side-redelivery",
                requesting_user_id=owner.id,
                source_message_id=root_message_id,
            )
        )
        queued_side_run = armada_worker_module.ArmadaWorker.next_queued_side_run(
            object.__new__(armada_worker_module.ArmadaWorker),
            bot_profile_id=armada_bot.id,
            stream=ARMADA_DEFAULT_STREAM,
            topic="side-redelivery",
        )
        self.assertIsNotNone(queued_side_run)
        assert queued_side_run is not None
        self.assertIsNone(queued_side_run.result_message_id)
        progress_message_id = store.ensure_progress_message(
            bot_handler,
            trigger_message_id,
            message=message,
            stream=ARMADA_DEFAULT_STREAM,
            topic="side-redelivery",
            content=ARMADA_WORKING_MESSAGE,
        )
        self.assertIsNotNone(progress_message_id)

        self.assertTrue(
            store.start_side(
                bot_handler,
                trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="side-redelivery",
                requesting_user_id=owner.id,
                source_message_id=root_message_id,
            )
        )
        self.assertEqual(
            store.ensure_progress_message(
                bot_handler,
                trigger_message_id,
                message=message,
                stream=ARMADA_DEFAULT_STREAM,
                topic="side-redelivery",
                content=ARMADA_WORKING_MESSAGE,
            ),
            progress_message_id,
        )
        self.assertEqual(
            Message.objects.filter(
                sender=armada_bot,
                subject="side-redelivery",
                content=ARMADA_WORKING_MESSAGE,
            ).count(),
            1,
        )

    def test_side_parent_resolution_never_borrows_an_unrelated_topic_run(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        workspace_node = Workspace.objects.create(
            realm=owner.realm,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="exact-side-parent",
        )
        parent_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=9201,
            result_message_id=9202,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="exact-side-parent",
            workspace_node=workspace_node,
            status=AgentRun.DONE,
            codex_thread_id="thread-parent",
            source_stream_name=ARMADA_DEFAULT_STREAM,
            source_topic_name="exact-side-parent",
            source_message_id=9198,
        )

        self.assertIsNone(
            latest_armada_codex_run_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic="exact-side-parent",
                before_message_id=9300,
                source_message_id=9199,
            )
        )
        self.assertEqual(
            latest_armada_codex_run_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic="exact-side-parent",
                before_message_id=9300,
                source_message_id=parent_run.trigger_message_id,
            ),
            parent_run,
        )
        self.assertEqual(
            latest_armada_codex_run_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic="exact-side-parent",
                before_message_id=9300,
                source_message_id=parent_run.source_message_id,
            ),
            parent_run,
        )
        self.assertEqual(
            latest_armada_codex_run_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic="exact-side-parent",
                before_message_id=9300,
                source_message_id=parent_run.result_message_id,
            ),
            parent_run,
        )

    def test_side_pump_settles_result_when_queued_trigger_was_deleted(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "armada-missing-side-trigger",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        root_message_id = self.send_stream_message(
            armada_bot,
            ARMADA_DEFAULT_STREAM,
            "The parent answer.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix what does that mean?",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        result_message_id = self.send_stream_message(
            armada_bot,
            ARMADA_DEFAULT_STREAM,
            ARMADA_WORKING_MESSAGE,
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        Message.objects.filter(id__in=[trigger_message_id, result_message_id]).update(
            thread_root_id=root_message_id
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=trigger_message_id,
            result_message_id=result_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            session_kind=AgentRun.SIDE,
            source_stream_name=ARMADA_DEFAULT_STREAM,
            source_topic_name=ARMADA_DEFAULT_TOPIC,
            source_message_id=root_message_id,
            status=AgentRun.QUEUED,
        )
        Message.objects.filter(id=trigger_message_id).delete()
        stop = threading.Event()
        stop.set()

        # This deliberately exercises the side-pump body synchronously.  Its
        # production entry point runs in a dedicated thread, where database
        # connection cleanup is thread-local; running that cleanup here would
        # instead close this TestCase's transaction connection.
        with (
            patch.object(armada_worker_module, "close_old_connections"),
            patch.object(armada_worker_module, "reset_queries"),
        ):
            armada_worker_module.ArmadaWorker.run_side_pump(
                object.__new__(armada_worker_module.ArmadaWorker),
                stop=stop,
                runtime=ArmadaWorkerRuntime(),
                bot_profile_id=armada_bot.id,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )

        run.refresh_from_db()
        result_message = Message.objects.get(id=result_message_id)
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(
            run.error,
            f"Side run trigger message {trigger_message_id} no longer exists.",
        )
        self.assertEqual(
            result_message.content,
            "This side conversation could not start because its request message was deleted.",
        )
        self.assertNotEqual(result_message.content, ARMADA_WORKING_MESSAGE)
        failure_event = AgentRunEvent.objects.get(run=run, event_type="failed")
        self.assertEqual(failure_event.payload["reason"], "missing_trigger_message")

    def test_codex_memories_follow_the_setting(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            self.create_git_repository(base_workdir)
            with self.settings(
                ARMADA_WORKER_AGENT_RUNTIME="codex-app-server",
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics"),
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="memories",
                )
                for enabled, expected in ((True, "true"), (False, "false")):
                    with self.settings(ARMADA_WORKER_CODEX_MEMORIES=enabled):
                        runtime = build_armada_worker_runtime_from_settings(
                            workdir_override=context.workdir,
                        )
                    assert isinstance(runtime.tool_runner, ArmadaCodexAppServerToolRunner)
                    command = runtime.tool_runner.build_command()
                    self.assertIn(f"features.memories={expected}", command)
                    self.assertNotIn(f"features.memories={'false' if enabled else 'true'}", command)

    def test_topic_worker_sandbox_downgrades_full_access_to_workspace_write(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                ARMADA_WORKER_AGENT_RUNTIME="codex-app-server",
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
                ARMADA_TOPIC_ISOLATION_BACKEND="codex-sandbox",
                ARMADA_WORKER_SANDBOX="danger-full-access",
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="isolated topic",
                )
                runtime = build_armada_worker_runtime_from_settings(
                    model_override="gpt-5.5",
                    workdir_override=context.workdir,
                )

                self.assertIsNone(armada_worker_runtime_isolation_error(context.workdir))
                self.assertEqual(
                    armada_worker_sandbox_for_topic(
                        owner,
                        stream=ARMADA_DEFAULT_STREAM,
                        topic="isolated topic",
                    ),
                    "workspace-write",
                )
                assert runtime.tool_runner is not None
                self.assertEqual(vars(runtime.tool_runner)["sandbox"], "workspace-write")

    def test_os_user_topic_worker_uses_full_access_codex_sandbox(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                ARMADA_WORKER_AGENT_RUNTIME="codex-app-server",
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ARMADA_WORKER_SANDBOX="workspace-write",
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="isolated topic",
                )
                runtime = build_armada_worker_runtime_from_settings(
                    model_override="gpt-5.5",
                    workdir_override=context.workdir,
                )

                self.assertEqual(
                    armada_worker_sandbox_for_topic(
                        owner,
                        stream=ARMADA_DEFAULT_STREAM,
                        topic="isolated topic",
                    ),
                    "danger-full-access",
                )
                assert runtime.tool_runner is not None
                self.assertEqual(vars(runtime.tool_runner)["sandbox"], "danger-full-access")

    def test_topic_worker_refuses_control_plane_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)
            active_session = build_armada_active_session_summary_from_messages(
                armada_session=get_armada_session_payload(),
                source="server",
                stream_exists=True,
                messages=[(10, "@Onix edit the control plane", False)],
            )

            with self.settings(
                ARMADA_WORKER_AGENT_RUNTIME="codex-app-server",
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
                ARMADA_WORKER_SANDBOX="danger-full-access",
            ):
                runtime = build_armada_worker_runtime_from_settings(
                    model_override="gpt-5.5",
                    workdir_override=str(base_workdir),
                )
                tool_results = list(
                    runtime.run_tools(active_session, task="edit the control plane")
                )

            self.assertEqual(tool_results[0]["label"], "Error")
            self.assertIn("outside the topic workspace root", tool_results[0]["value"])

    def test_os_user_topic_context_uses_independent_repository_clone(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, "megatron-lm")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            channel_context = ensure_armada_workspace_node(
                owner,
                stream="megatron-lm",
                topic="",
            )
            topic_context = ensure_armada_workspace_node(
                owner,
                stream="megatron-lm",
                topic="optimizer work",
            )

            self.assertEqual(topic_context.project_id, channel_context.project_id)
            assert channel_context.project is not None
            self.assertEqual(
                channel_context.project.repo_cache_path,
                str(armada_expected_project_cache_path(owner, stream="megatron-lm")),
            )
            self.assertTrue(Path(channel_context.project.repo_cache_path).is_dir())
            self.assertEqual(channel_context.branch_name, "main")
            self.assertEqual(topic_context.branch_name, "optimizer-work")
            self.assertTrue((Path(topic_context.workdir) / ".git").is_dir())
            self.assertFalse((Path(topic_context.workdir) / ".git").is_file())

    def test_os_user_process_isolation_invokes_topic_helper(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ARMADA_TOPIC_ISOLATION_HELPER="/usr/local/sbin/armada-topic-isolate",
                ARMADA_CODEX_SKILLS_DIR=str(Path(temp_dir) / "missing-skills"),
            ),
        ):
            workdir = str(Path(temp_dir) / "topic")
            Path(workdir).mkdir()
            process_isolation = armada_topic_process_isolation(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic="isolated topic",
            )
            calls = []

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("zerver.lib.armada_worker.subprocess.run", side_effect=fake_run):
                error = armada_worker_process_isolation_error(
                    process_isolation,
                    workdir=workdir,
                )

        assert process_isolation is not None
        self.assertIsNone(error)
        self.assertEqual(
            calls[0][0][:6],
            [
                "sudo",
                "-n",
                "/usr/local/sbin/armada-topic-isolate",
                "--user",
                process_isolation.username,
                "--workdir",
            ],
        )
        self.assertIn("--topic-root", calls[0][0])
        self.assertIn(temp_dir, calls[0][0])

    def test_os_user_process_isolation_tolerates_private_prepared_workdir(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ARMADA_TOPIC_ISOLATION_HELPER="/usr/local/sbin/armada-topic-isolate",
                ARMADA_CODEX_SKILLS_DIR=str(Path(temp_dir) / "missing-skills"),
            ),
        ):
            workdir = Path(temp_dir) / "private-topic"
            workdir.mkdir()
            process_isolation = armada_topic_process_isolation(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic="private topic",
            )
            with (
                patch(
                    "zerver.lib.armada_worker.Path.is_file",
                    side_effect=PermissionError,
                ),
                patch(
                    "zerver.lib.armada_worker.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                ) as run_mock,
            ):
                error = armada_worker_process_isolation_error(
                    process_isolation,
                    workdir=str(workdir),
                )

        self.assertIsNone(error)
        run_mock.assert_called_once()

    def test_os_user_process_isolation_refuses_shared_git_worktree(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            workdir = Path(temp_dir) / "legacy-worktree"
            workdir.mkdir()
            (workdir / ".git").write_text(
                "gitdir: /home/zulipdev/zulip/.git/worktrees/legacy\n",
                encoding="utf-8",
            )
            process_isolation = armada_topic_process_isolation(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic="legacy worktree",
            )
            with patch("zerver.lib.armada_worker.subprocess.run") as run_mock:
                error = armada_worker_process_isolation_error(
                    process_isolation,
                    workdir=str(workdir),
                )

        assert error is not None
        self.assertIn("shared Git worktree", error)
        run_mock.assert_not_called()

    def test_topic_workdir_root_inside_base_is_repaired_to_sibling_root(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            self.create_git_repository(base_workdir)
            unsafe_topic_root = base_workdir / "var" / "armada-workspaces"

            with self.settings(
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=str(unsafe_topic_root),
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="outside-root",
                )

            context_workdir = Path(context.workdir).resolve(strict=False)
            self.assertFalse(context_workdir.is_relative_to(base_workdir.resolve()))
            self.assertIn("armada-workspaces", context.workdir)
            self.assertTrue((context_workdir / ".git").exists())

    def test_os_user_topic_workdir_root_inside_service_home_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service_home = Path(temp_dir) / "home" / "zulipdev"
            base_workdir = service_home / "zulip"
            unsafe_topic_root = service_home / "armada-workspaces"

            with (
                self.settings(
                    ARMADA_WORKER_WORKDIR=str(base_workdir),
                    ARMADA_TOPIC_WORKDIR_ROOT=str(unsafe_topic_root),
                    ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ),
                patch("zerver.lib.armada_worker.Path.home", return_value=service_home),
            ):
                self.assertEqual(armada_topic_workdir_root(), Path("/srv/armada-workspaces"))

    def test_os_user_topic_workdir_initializes_independent_git_repository(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            origin, base_ref = self.create_bare_origin(Path(temp_dir))
            workdir = Path(temp_dir) / "topic"
            ensure_armada_topic_workdir(
                str(workdir),
                origin=origin,
                branch_name="topic",
                base_ref=base_ref,
            )

            self.assertTrue((workdir / ".git").is_dir())
            self.assertEqual(armada_git_current_branch_name(workdir), "topic")

    def test_topic_workdir_provisions_git_author_identity(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            origin, base_ref = self.create_bare_origin(Path(temp_dir))
            workdir = Path(temp_dir) / "fable-ui-tour"
            ensure_armada_topic_workdir(
                str(workdir),
                origin=origin,
                branch_name="fable-ui-tour",
                base_ref=base_ref,
            )

            email = subprocess.run(
                ["git", "-C", str(workdir), "config", "user.email"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            name = subprocess.run(
                ["git", "-C", str(workdir), "config", "user.name"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            self.assertEqual(name, "Armada")
            self.assertTrue(email.startswith("armada-worker@"))

    def test_topic_workdir_keeps_existing_git_author_identity(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            origin, base_ref = self.create_bare_origin(Path(temp_dir))
            workdir = Path(temp_dir) / "fable-ui-tour"
            ensure_armada_topic_workdir(
                str(workdir),
                origin=origin,
                branch_name="fable-ui-tour",
                base_ref=base_ref,
            )
            subprocess.run(
                ["git", "-C", str(workdir), "config", "user.email", "human@example.com"],
                check=True,
                timeout=10,
            )

            ensure_armada_topic_workdir(
                str(workdir),
                origin=origin,
                branch_name="fable-ui-tour",
                base_ref=base_ref,
            )

            email = subprocess.run(
                ["git", "-C", str(workdir), "config", "user.email"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            self.assertEqual(email, "human@example.com")

    def test_git_context_reports_status_delta_for_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_workdir = Path(temp_dir) / "devel-armada-efficient-smoke"
            self.create_git_repository(base_workdir)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(base_workdir),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/zulip/zulip.git",
                ],
                check=True,
                timeout=10,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(base_workdir),
                    "worktree",
                    "add",
                    "--detach",
                    str(topic_workdir),
                    "HEAD",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=30,
            )

            git_context = build_armada_git_context(
                str(topic_workdir),
                topic="devel-armada-efficient-smoke",
            )

            self.assertEqual(git_context["worktree_name"], "devel-armada-efficient-smoke")
            self.assertEqual(git_context["branch"], "")
            self.assertEqual(git_context["ahead"], 0)
            self.assertEqual(git_context["behind"], 0)
            self.assertFalse(git_context["dirty"])
            self.assertEqual(git_context["changed_count"], 0)
            self.assertTrue(git_context["detached"])
            self.assertFalse(git_context["branch_matches_topic"])
            # The header commit sentence: latest commit identity + total
            # count ride the same cached delta.
            last_commit = git_context["last_commit"]
            assert isinstance(last_commit, dict)
            self.assertGreaterEqual(len(str(last_commit["sha"])), 7)
            self.assertTrue(str(last_commit["subject"]))
            self.assertTrue(str(last_commit["author"]))
            self.assertIsInstance(last_commit["timestamp"], int)
            self.assertGreaterEqual(int(str(git_context["commit_count"])), 1)

    def test_armada_git_status_delta_uses_cache_until_invalidated(self) -> None:
        invalidate_armada_git_status_delta()
        clean_status = "# branch.oid 1234567890abcdef\n# branch.head topic-a\n# branch.upstream origin/topic-a\n# branch.ab +2 -1"
        dirty_status = f"{clean_status}\n1 .M N... 100644 100644 100644 a b file.txt"

        # Each refresh issues three git commands (status, log -1,
        # rev-list --count); the commit-info calls return "" here.
        with patch(
            "zerver.lib.armada_worker.armada_git_output",
            side_effect=[clean_status, "", "", dirty_status, "", ""],
        ) as git_output:
            clean_delta = armada_git_status_delta("/repo", topic="topic-a")
            cached_delta = armada_git_status_delta("/repo", topic="TOPIC-A")
            invalidate_armada_git_status_delta("/repo")
            dirty_delta = armada_git_status_delta("/repo", topic="topic-a")

        self.assertEqual(git_output.call_count, 6)
        self.assertEqual(clean_delta["head"], "1234567")
        self.assertEqual(clean_delta["ahead"], 2)
        self.assertEqual(clean_delta["behind"], 1)
        self.assertTrue(clean_delta["branch_matches_topic"])
        self.assertFalse(clean_delta["dirty"])
        self.assertEqual(cached_delta, clean_delta)
        self.assertTrue(dirty_delta["dirty"])
        self.assertEqual(dirty_delta["changed_count"], 1)

    def test_armada_git_status_delta_reports_structured_changed_files(self) -> None:
        invalidate_armada_git_status_delta()
        status_output = "# branch.oid 1234567890abcdef\n# branch.head topic-a\n1 .M N... 100644 100644 100644 a b modified.txt\n1 A. N... 000000 100644 100644 a b added.txt\n1 D. N... 100644 000000 000000 a b deleted.txt\n2 R. N... 100644 100644 100644 a b R100 renamed.txt\told-name.txt\nu UU N... 100644 100644 100644 100644 a b c conflicted.txt\n? untracked.txt"

        with patch(
            "zerver.lib.armada_worker.armada_git_output",
            return_value=status_output,
        ):
            delta = armada_git_status_delta("/repo", topic="topic-a")

        self.assertEqual(delta["changed_count"], 6)
        self.assertFalse(delta["changed_files_truncated"])
        self.assertEqual(
            delta["changed_files"],
            [
                {"status": "M", "kind": "modified", "path": "modified.txt"},
                {"status": "A", "kind": "added", "path": "added.txt"},
                {"status": "D", "kind": "deleted", "path": "deleted.txt"},
                {"status": "R", "kind": "renamed", "path": "renamed.txt"},
                {"status": "U", "kind": "changed", "path": "conflicted.txt"},
                {"status": "??", "kind": "untracked", "path": "untracked.txt"},
            ],
        )

    def test_armada_git_status_delta_runs_bounded_git_commands(self) -> None:
        invalidate_armada_git_status_delta()
        status_output = (
            "# branch.oid 1234567890abcdef\n# branch.head topic-a\n# branch.ab +0 -0\n? new.txt"
        )
        completed = subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=status_output,
            stderr="",
        )
        with (
            tempfile.TemporaryDirectory() as workdir,
            patch("zerver.lib.armada_worker.subprocess.run", return_value=completed) as run_mock,
        ):
            Path(workdir, ".git").touch()
            delta = armada_git_status_delta(workdir, topic="topic-b")

        # Exactly one porcelain status plus the two commit-info commands
        # per (cached) refresh — never N+1 per file or per commit.
        self.assertEqual(run_mock.call_count, 3)
        self.assertEqual(
            [call.args[0][3] for call in run_mock.call_args_list],
            ["status", "log", "rev-list"],
        )
        self.assertTrue(delta["dirty"])
        self.assertEqual(delta["changed_count"], 1)
        self.assertFalse(delta["branch_matches_topic"])

    def test_armada_git_status_delta_skips_commit_reads_on_unborn_head(self) -> None:
        invalidate_armada_git_status_delta()
        status_output = "# branch.oid (initial)\n# branch.head topic-a\n? new.txt"
        completed = subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=status_output,
            stderr="",
        )
        with (
            tempfile.TemporaryDirectory() as workdir,
            patch("zerver.lib.armada_worker.subprocess.run", return_value=completed) as run_mock,
        ):
            Path(workdir, ".git").touch()
            delta = armada_git_status_delta(workdir, topic="topic-a")

        # A freshly initialized workspace has no HEAD yet; running the
        # commit-info commands would make git fail (and warn) on every
        # refresh, so only the status command may run.
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(run_mock.call_args_list[0].args[0][3], "status")
        self.assertNotIn("last_commit", delta)
        self.assertNotIn("commit_count", delta)
        self.assertEqual(delta["head_short"], "")

    def test_armada_git_output_logs_stderr_on_failure(self) -> None:
        completed = subprocess.CompletedProcess(
            ["git"],
            128,
            stdout="",
            stderr="fatal: not a git repository",
        )

        with tempfile.TemporaryDirectory() as workdir:
            Path(workdir, ".git").touch()
            with (
                patch("zerver.lib.armada_worker.subprocess.run", return_value=completed),
                self.assertLogs("zerver.lib.armada_worker", level="WARNING") as logs,
            ):
                self.assertEqual(armada_git_output(workdir, ["status"]), "")

        log_output = "\n".join(logs.output)
        self.assertIn("status 128", log_output)
        self.assertIn("fatal: not a git repository", log_output)

    def test_armada_git_output_logs_stderr_on_timeout(self) -> None:
        # TimeoutExpired carries the captured output as bytes, even with
        # text=True.
        timeout = subprocess.TimeoutExpired(
            ["git"], 2, output=b"", stderr=b"fatal: index.lock is held\n"
        )

        with tempfile.TemporaryDirectory() as workdir:
            Path(workdir, ".git").touch()
            with (
                patch("zerver.lib.armada_worker.subprocess.run", side_effect=timeout),
                self.assertLogs("zerver.lib.armada_worker", level="WARNING") as logs,
            ):
                self.assertEqual(armada_git_output(workdir, ["status"]), "")

        self.assertTrue(
            logs.output[0].startswith(
                f"WARNING:zerver.lib.armada_worker:Git command timed out in {workdir} "
                "with args ['status']: fatal: index.lock is held\n"
            )
        )

    def test_armada_git_output_skips_missing_workdir_without_warning(self) -> None:
        with (
            tempfile.TemporaryDirectory() as parent,
            patch("zerver.lib.armada_worker.subprocess.run") as run_mock,
        ):
            missing_workdir = str(Path(parent) / "removed-topic-worktree")
            self.assertEqual(armada_git_output(missing_workdir, ["status"]), "")

        run_mock.assert_not_called()

    def test_armada_git_output_skips_uninitialized_workdir_without_warning(self) -> None:
        with (
            tempfile.TemporaryDirectory() as workdir,
            patch("zerver.lib.armada_worker.subprocess.run") as run_mock,
        ):
            self.assertEqual(armada_git_output(workdir, ["status"]), "")

        run_mock.assert_not_called()

    def test_workspace_context_reports_local_topic_repo_without_external_pin(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_WORKER_WORKDIR=str(Path(temp_dir) / "base"),
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            # With no external repository pinned, the topic workspace is
            # provisioned from the seeded local origin on the topic branch.
            context = ensure_armada_workspace_node(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )

            workspace_context = build_armada_workspace_context(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )

            self.assertEqual(workspace_context["repository"], ARMADA_DEFAULT_STREAM)
            self.assertEqual(workspace_context["remote_url"], "")
            self.assertEqual(workspace_context["worktree"], ARMADA_DEFAULT_TOPIC)
            self.assertEqual(
                workspace_context["branch"], armada_topic_branch_name(ARMADA_DEFAULT_TOPIC)
            )
            self.assertEqual(workspace_context["head"], "")
            topic_payload = serialize_armada_workspace_context(
                context,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
            )
            self.assertEqual(topic_payload["github_repo"], "")
            self.assertEqual(topic_payload["repo_label"], ARMADA_DEFAULT_STREAM)
            self.assertEqual(topic_payload["worktree_label"], ARMADA_DEFAULT_TOPIC)

        # An externally pinned repository is reported as the remote. Pin in a
        # fresh workdir scope: changing an existing channel cache's repository
        # is (correctly) rejected as wrong-file protection.
        with (
            tempfile.TemporaryDirectory() as pin_dir,
            self.settings(
                ARMADA_WORKER_WORKDIR=str(Path(pin_dir) / "base"),
                ARMADA_TOPIC_WORKDIR_ROOT=pin_dir,
            ),
        ):
            (Path(pin_dir) / "external").mkdir()
            external_origin, _external_branch = self.create_bare_origin(Path(pin_dir) / "external")
            # A pin is only a durable remote as a real URL; plain local paths
            # are (deliberately) treated as local topic paths instead.
            pinned_url = f"file://{external_origin}"
            project, _created = Repository.objects.update_or_create(
                realm=owner.realm,
                stream=get_stream(ARMADA_DEFAULT_STREAM, owner.realm),
                defaults={
                    "repository_url": pinned_url,
                    "repo_cache_path": "",
                    "clone_status": Repository.UNCONFIGURED,
                },
            )
            context.project = project
            context.save(update_fields=["project", "date_updated"])

            workspace_context = build_armada_workspace_context(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )

            self.assertEqual(workspace_context["repository"], pinned_url)
            self.assertEqual(workspace_context["remote_url"], pinned_url)

    def test_topic_workdir_creation_does_not_copy_self_dev_overlay(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)
            armada_file = base_workdir / "zerver/lib/armada_worker.py"
            armada_file.parent.mkdir(parents=True)
            armada_file.write_text("dirty armada self-dev file\n", encoding="utf-8")

            with self.settings(
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="clean-topic",
                )

            self.assertFalse((Path(context.workdir) / "zerver/lib/armada_worker.py").exists())

    def test_channel_creation_can_pin_armada_repository(self) -> None:
        owner = self.example_user("hamlet")
        self.login_user(owner)
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"

        def initialize_without_network(
            origin: Path, *, repository_url: str, fetch_timeout_seconds: int
        ) -> tuple[str, str]:
            subprocess.run(
                ["git", "init", "--bare", "--quiet", "--initial-branch=main", str(origin)],
                check=True,
                timeout=10,
            )
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(origin),
                    "remote",
                    "add",
                    "upstream",
                    repository_url,
                ],
                check=True,
                timeout=10,
            )
            return "main", armada_seed_empty_bare_origin(origin, branch_name="main")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch(
                "zerver.lib.armada_worker.armada_initialize_bare_origin",
                side_effect=initialize_without_network,
            ),
        ):
            result = self.client_post(
                "/json/users/me/subscriptions",
                {
                    "subscriptions": orjson.dumps(
                        [{"name": "megatron-lm", "description": "Pinned repository"}]
                    ).decode(),
                    "principals": orjson.dumps([owner.id]).decode(),
                    "repository_url": repository_url,
                },
            )

            result_dict = self.assert_json_success(result)
            context = Workspace.objects.get(
                realm=owner.realm,
                stream_name="megatron-lm",
                topic_name="",
            )

            assert context.project is not None
            self.assertEqual(context.project.repository_url, repository_url)
            self.assertTrue(context.workdir.startswith(temp_dir))
            project = Repository.objects.get(
                realm=owner.realm,
                stream=get_stream("megatron-lm", owner.realm),
            )
            self.assertEqual(project.repository_url, repository_url)
            self.assertEqual(
                project.repo_cache_path,
                str(armada_expected_project_cache_path(owner, stream="megatron-lm")),
            )
            self.assertNotEqual(project.repo_cache_path, context.workdir)
            self.assertTrue(Path(project.repo_cache_path).is_dir())
            self.assertNotIn("armada_repository_error", result_dict)

    def test_topic_context_inherits_channel_repository_checkout(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, "megatron-lm")
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"

        def initialize_without_network(
            origin: Path, *, repository_url: str, fetch_timeout_seconds: int
        ) -> tuple[str, str]:
            subprocess.run(
                ["git", "init", "--bare", "--quiet", "--initial-branch=dev", str(origin)],
                check=True,
                timeout=10,
            )
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(origin),
                    "remote",
                    "add",
                    "upstream",
                    repository_url,
                ],
                check=True,
                timeout=10,
            )
            return "dev", armada_seed_empty_bare_origin(origin, branch_name="dev")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch(
                "zerver.lib.armada_worker.armada_initialize_bare_origin",
                side_effect=initialize_without_network,
            ),
        ):
            channel_context = configure_armada_channel_repository_workspace(
                owner,
                stream="megatron-lm",
                repository_url=repository_url,
            )
            topic_context = ensure_armada_workspace_node(
                owner,
                stream="megatron-lm",
                topic="optimizer work",
            )

            assert channel_context.project is not None
            self.assertEqual(channel_context.project.repository_url, repository_url)
            self.assertNotEqual(topic_context.workdir, channel_context.workdir)
            self.assertTrue(Path(topic_context.workdir).exists())
            assert topic_context.project is not None
            self.assertEqual(topic_context.project.repository_url, repository_url)
            self.assertEqual(channel_context.branch_name, "dev")
            self.assertEqual(topic_context.branch_name, "optimizer-work")
            self.assertEqual(topic_context.base_ref, "dev")
            self.assertEqual(topic_context.project_id, channel_context.project_id)

    def test_operator_attach_fetches_with_its_own_timeout(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, "megatron-lm")
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"
        fetch_timeouts: list[int] = []

        def record_fetch(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if "fetch" in command:
                fetch_timeouts.append(kwargs["timeout"])
            return armada_run_git_checked(command, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            upstream = Path(temp_dir) / "upstream"
            self.create_git_repository(upstream)

            def initialize_from_local_upstream(
                origin: Path, *, repository_url: str, fetch_timeout_seconds: int
            ) -> tuple[str, str]:
                base = armada_initialize_bare_origin(
                    origin,
                    repository_url=str(upstream),
                    fetch_timeout_seconds=fetch_timeout_seconds,
                )
                subprocess.run(
                    [
                        "git",
                        "--git-dir",
                        str(origin),
                        "remote",
                        "set-url",
                        "upstream",
                        repository_url,
                    ],
                    check=True,
                    timeout=10,
                )
                return base

            with (
                self.settings(ARMADA_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics")),
                patch(
                    "zerver.lib.armada_worker.armada_initialize_bare_origin",
                    side_effect=initialize_from_local_upstream,
                ),
                patch("zerver.lib.armada_worker.armada_run_git_checked", side_effect=record_fetch),
            ):
                channel_context = configure_armada_channel_repository_workspace(
                    owner,
                    stream="megatron-lm",
                    repository_url=repository_url,
                    fetch_timeout_seconds=3600,
                )

            self.assertEqual(fetch_timeouts, [3600])
            project = Repository.objects.get(
                realm=owner.realm, stream=get_stream("megatron-lm", owner.realm)
            )
            self.assertEqual(project.clone_status, Repository.READY)
            self.assertEqual(project.repository_url, repository_url)
            self.assertTrue(Path(channel_context.workdir, "README.md").exists())

    def test_topic_context_does_not_use_general_topic_as_repository_parent(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            self.subscribe(owner, "devel")
            parent_path = Path(temp_dir) / "devel-base"
            parent_path.mkdir()
            general_context = Workspace.objects.create(
                realm=owner.realm,
                stream_name="devel",
                topic_name="",
                workdir=str(parent_path),
            )

            topic_context = ensure_armada_workspace_node(
                owner,
                stream="devel",
                topic="general chat",
            )

            self.assertIsNone(general_context.project_id)
            self.assertIsNotNone(topic_context.project_id)
            self.assertEqual(topic_context.branch_name, "general-chat")
            self.assertTrue(Path(topic_context.workdir).is_dir())

    def test_context_isolation_username_waits_for_existing_os_user(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_ISOLATION_BACKEND="os-user",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            workdir = Path(temp_dir) / "devel-general-chat"
            context = Workspace.objects.create(
                realm=owner.realm,
                stream_name="devel",
                topic_name="general chat",
                workdir=str(workdir),
            )
            expected_username = armada_topic_isolation_username(
                owner,
                stream="devel",
                topic="general chat",
            )

            with patch("zerver.lib.armada_worker.pwd.getpwnam", side_effect=KeyError):
                self.assertIsNone(
                    armada_workspace_isolation_username(
                        context,
                        stream_name="devel",
                        topic_name="general chat",
                        workdir=str(workdir),
                    )
                )

            with patch(
                "zerver.lib.armada_worker.pwd.getpwnam",
                return_value=SimpleNamespace(pw_name=expected_username),
            ):
                self.assertEqual(
                    armada_workspace_isolation_username(
                        context,
                        stream_name="devel",
                        topic_name="general chat",
                        workdir=str(workdir),
                    ),
                    expected_username,
                )

    def test_topic_isolation_user_exists_rejects_missing_user(self) -> None:
        self.assertFalse(armada_topic_isolation_user_exists("armada-user-that-should-not-exist"))

    def test_topic_context_refactor_moves_full_topic_rename(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                ARMADA_WORKER_WORKDIR=str(base_workdir),
                ARMADA_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="old topic",
                )
                old_workdir = context.workdir
                old_workspace_node_id = context.id
                old_branch = subprocess.run(
                    ["git", "-C", context.workdir, "branch", "--show-current"],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                    timeout=10,
                ).stdout.strip()
                run = AgentRun.objects.create(
                    realm=owner.realm,
                    bot_profile=armada_bot,
                    requesting_user=owner,
                    trigger_message_id=3001,
                    stream_name=ARMADA_DEFAULT_STREAM,
                    topic_name="old topic",
                    workspace_node=context,
                    status=AgentRun.DONE,
                    codex_thread_id="thread-1",
                )
                side_run = AgentRun.objects.create(
                    realm=owner.realm,
                    bot_profile=armada_bot,
                    requesting_user=owner,
                    trigger_message_id=3002,
                    stream_name=ARMADA_DEFAULT_STREAM,
                    topic_name="side topic",
                    session_kind=AgentRun.SIDE,
                    source_stream_name=ARMADA_DEFAULT_STREAM,
                    source_topic_name="old topic",
                    status=AgentRun.DONE,
                )
                monitor = AgentMonitor.objects.create(
                    realm=owner.realm,
                    bot_profile=armada_bot,
                    creator=owner,
                    stream_name=ARMADA_DEFAULT_STREAM.upper(),
                    topic_name="OLD TOPIC",
                    ask="watch the moved topic",
                )

                result = maybe_refactor_armada_workspace_on_move(
                    owner,
                    old_stream=stream,
                    old_topic="old topic",
                    new_stream=stream,
                    new_topic="new topic",
                    moved_all_visible_messages=True,
                )

            assert result is not None
            context.refresh_from_db()
            run.refresh_from_db()
            side_run.refresh_from_db()
            monitor.refresh_from_db()

            self.assertEqual(result.topic_name, "new topic")
            self.assertEqual(result.message, "The workspace label moved with this topic.")
            self.assertNotIn("Worktree:", result.message)
            self.assertNotIn(topic_root, result.message)
            self.assertEqual(context.topic_name, "new topic")
            self.assertEqual(context.workdir, old_workdir)
            self.assertEqual(context.id, old_workspace_node_id)
            self.assertTrue(context.workdir.startswith(topic_root))
            self.assertTrue(Path(context.workdir).exists())
            branch = subprocess.run(
                ["git", "-C", context.workdir, "branch", "--show-current"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
                timeout=10,
            ).stdout.strip()
            self.assertEqual(branch, old_branch)
            self.assertEqual(context.topic_name, "new topic")
            self.assertEqual(context.branch_name, old_branch)
            self.assertEqual(run.topic_name, "old topic")
            self.assertEqual(run.workspace_node_id, context.id)
            self.assertEqual(side_run.source_topic_name, "old topic")
            self.assertEqual(monitor.stream_name, ARMADA_DEFAULT_STREAM.lower())
            self.assertEqual(monitor.topic_name, "new topic")
            self.assertFalse(
                Workspace.objects.filter(
                    realm=owner.realm,
                    stream_name=ARMADA_DEFAULT_STREAM,
                    topic_name="old topic",
                ).exists()
            )

    def test_topic_context_refactor_warns_for_partial_topic_move(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics"),
            ),
        ):
            context = ensure_armada_workspace_node(
                owner,
                stream=ARMADA_DEFAULT_STREAM,
                topic="old topic",
            )
            old_workdir = context.workdir
            monitor = AgentMonitor.objects.create(
                realm=owner.realm,
                bot_profile=armada_bot,
                creator=owner,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name="old topic",
                ask="stay with the original topic",
            )

            result = maybe_refactor_armada_workspace_on_move(
                owner,
                old_stream=stream,
                old_topic="old topic",
                new_stream=stream,
                new_topic="new topic",
                moved_all_visible_messages=False,
            )

            context.refresh_from_db()
            monitor.refresh_from_db()

        assert result is not None
        self.assertIn("only part of the topic was moved", result.message)
        self.assertEqual(result.topic_name, "new topic")
        self.assertEqual(context.topic_name, "old topic")
        self.assertEqual(context.workdir, old_workdir)
        self.assertEqual(monitor.topic_name, "old topic")

    def test_stream_context_refactor_updates_channel_rename_metadata(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        Workspace.objects.create(
            realm=owner.realm,
            stream_name="old-repo",
            topic_name="",
            workdir="/tmp/old-repo",  # noqa: S108
        )
        Workspace.objects.create(
            realm=owner.realm,
            stream_name="old-repo",
            topic_name="feature",
            workdir="/tmp/old-repo-feature",  # noqa: S108
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=3101,
            stream_name="old-repo",
            topic_name="feature",
            status=AgentRun.RUNNING,
        )
        side_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=3102,
            stream_name="side",
            topic_name="private",
            session_kind=AgentRun.SIDE,
            source_stream_name="old-repo",
            source_topic_name="feature",
            status=AgentRun.DONE,
        )
        monitor = AgentMonitor.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            creator=owner,
            stream_name="OLD-REPO",
            topic_name="feature",
            ask="watch the renamed channel",
        )

        message = maybe_refactor_armada_stream_workspaces_on_rename(
            owner,
            old_stream_name="old-repo",
            new_stream_name="new-repo",
        )

        assert message is not None
        self.assertIn("Channel context renamed", message)
        self.assertEqual(
            Workspace.objects.filter(
                realm=owner.realm,
                stream_name="new-repo",
            ).count(),
            2,
        )
        self.assertFalse(
            Workspace.objects.filter(
                realm=owner.realm,
                stream_name="old-repo",
            ).exists()
        )
        run.refresh_from_db()
        side_run.refresh_from_db()
        monitor.refresh_from_db()
        self.assertEqual(run.stream_name, "old-repo")
        self.assertEqual(side_run.source_stream_name, "old-repo")
        self.assertEqual(monitor.stream_name, "new-repo")

    def test_project_origin_clones_repository_and_seeds_topic_branch(self) -> None:
        owner = self.example_user("hamlet")
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            self.create_git_repository(source)
            project = Repository.objects.create(
                realm=owner.realm,
                stream=get_stream(ARMADA_DEFAULT_STREAM, owner.realm),
                repository_url=source.as_uri(),
            )

            with self.settings(ARMADA_TOPIC_WORKDIR_ROOT=temp_dir):
                origin = ensure_armada_project_origin(owner, project)
                workspace = ensure_armada_workspace_node(
                    owner,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="origin-smoke",
                )

            self.assertTrue(origin.name.endswith(".git"))
            self.assertEqual(
                (Path(workspace.workdir) / "README.md").read_text(encoding="utf-8"),
                "hello\n",
            )
            self.assertEqual(workspace.branch_name, "origin-smoke")

    def test_worker_start_allows_same_user_in_different_topics(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=901,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
        )

        reason = ArmadaDatabaseWorkerRunStore().start_blocked_reason(
            SimpleNamespace(user_profile=armada_bot),
            902,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-b",
            requesting_user_id=owner.id,
        )

        self.assertIsNone(reason)

    def test_worker_start_blocks_parallel_shared_run_in_same_topic(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=903,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
        )

        reason = ArmadaDatabaseWorkerRunStore().start_blocked_reason(
            SimpleNamespace(user_profile=armada_bot),
            904,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-a",
            requesting_user_id=owner.id,
        )

        self.assertEqual(reason, "This topic already has an active run.")

    def test_worker_start_serializes_case_variants_of_the_same_topic(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        first_store = ArmadaDatabaseWorkerRunStore()
        second_store = ArmadaDatabaseWorkerRunStore()

        with (
            self.settings(
                ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC=1,
                ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM=2,
            ),
            patch.object(
                first_store,
                "_lock_workspace_node",
                wraps=first_store._lock_workspace_node,
            ) as first_lock,
            patch.object(
                second_store,
                "_lock_workspace_node",
                wraps=second_store._lock_workspace_node,
            ) as second_lock,
        ):
            self.assertTrue(
                first_store.start(
                    bot_handler,
                    951,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="serialized-start",
                )
            )
            self.assertFalse(
                second_store.start(
                    bot_handler,
                    952,
                    stream=ARMADA_DEFAULT_STREAM.upper(),
                    topic="SERIALIZED-START",
                )
            )
        first_lock.assert_called_once()
        second_lock.assert_called_once()

        self.assertEqual(
            list(
                AgentRun.objects.filter(topic_name="serialized-start").values_list(
                    "trigger_message_id", "status"
                )
            ),
            [(951, AgentRun.QUEUED)],
        )

    def test_worker_start_and_claim_serialize_in_the_same_topic(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        start_store = ArmadaDatabaseWorkerRunStore()
        claim_store = ArmadaDatabaseWorkerRunStore()

        with (
            self.settings(
                ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC=1,
                ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM=2,
            ),
            patch.object(
                start_store,
                "_lock_workspace_node",
                wraps=start_store._lock_workspace_node,
            ) as start_lock,
            patch.object(
                claim_store,
                "_lock_workspace_node",
                wraps=claim_store._lock_workspace_node,
            ) as claim_lock,
        ):
            self.assertTrue(
                start_store.start(
                    bot_handler,
                    953,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="serialized-claim",
                )
            )
            self.assertFalse(
                claim_store.claim_for_run(
                    bot_handler,
                    954,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="serialized-claim",
                )
            )
        start_lock.assert_called_once()
        claim_lock.assert_called_once()

        self.assertTrue(
            AgentRun.objects.filter(
                trigger_message_id=953,
                status=AgentRun.QUEUED,
            ).exists()
        )
        self.assertFalse(AgentRun.objects.filter(trigger_message_id=954).exists())

    def test_streamless_runs_are_rejected(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()

        self.assertEqual(
            store.start_blocked_reason(
                bot_handler,
                957,
                stream="",
                topic="",
                requesting_user_id=owner.id,
            ),
            "Codex runs currently require a channel topic.",
        )
        self.assertFalse(store.start(bot_handler, 957, stream="", topic=""))
        self.assertFalse(store.claim_for_run(bot_handler, 957, stream="", topic=""))
        self.assertFalse(AgentRun.objects.filter(trigger_message_id=957).exists())

    def test_worker_claims_in_different_topics_are_independent(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)

        with self.settings(
            ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC=1,
            ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM=2,
        ):
            self.assertTrue(
                ArmadaDatabaseWorkerRunStore().claim_for_run(
                    bot_handler,
                    955,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="independent-a",
                )
            )
            self.assertTrue(
                ArmadaDatabaseWorkerRunStore().claim_for_run(
                    bot_handler,
                    956,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="independent-b",
                )
            )

        self.assertEqual(
            set(
                AgentRun.objects.filter(trigger_message_id__in=(955, 956)).values_list(
                    "topic_name", "status", "attempts"
                )
            ),
            {
                ("independent-a", AgentRun.RUNNING, 1),
                ("independent-b", AgentRun.RUNNING, 1),
            },
        )

    def test_worker_run_store_rejects_illegal_terminal_transitions(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        done_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=905,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.DONE,
        )
        cancelled_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=906,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.CANCELLED,
        )

        store.record(
            bot_handler,
            done_run.trigger_message_id,
            AgentRun.RUNNING,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-a",
        )
        store.record(
            bot_handler,
            cancelled_run.trigger_message_id,
            AgentRun.DONE,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-a",
        )

        done_run.refresh_from_db()
        cancelled_run.refresh_from_db()
        self.assertEqual(done_run.status, AgentRun.DONE)
        self.assertEqual(cancelled_run.status, AgentRun.CANCELLED)
        done_rejection = AgentRunEvent.objects.get(
            run=done_run,
            event_type="transition_rejected",
        )
        cancelled_rejection = AgentRunEvent.objects.get(
            run=cancelled_run,
            event_type="transition_rejected",
        )
        self.assertEqual(
            done_rejection.payload["reason"],
            "illegal_transition",
        )
        self.assertEqual(
            cancelled_rejection.payload["new_status"],
            AgentRun.DONE,
        )

    def test_worker_run_store_observes_workspace_once_on_terminal_transition(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        workspace_node = Workspace.objects.create(
            realm=owner.realm,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="workspace-observer",
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=940,
            workspace_node=workspace_node,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="workspace-observer",
            status=AgentRun.QUEUED,
        )
        store = ArmadaDatabaseWorkerRunStore()
        bot_handler = SimpleNamespace(user_profile=armada_bot)

        with (
            patch(
                "zerver.lib.armada_worker.refresh_armada_workspace_after_terminal_run"
            ) as refresh_workspace,
            self.captureOnCommitCallbacks(execute=True),
        ):
            store.record(
                bot_handler,
                run.trigger_message_id,
                AgentRun.RUNNING,
                stream=ARMADA_DEFAULT_STREAM,
                topic="workspace-observer",
            )
            store.record(
                bot_handler,
                run.trigger_message_id,
                AgentRun.RUNNING,
                stream=ARMADA_DEFAULT_STREAM,
                topic="workspace-observer",
            )
            refresh_workspace.assert_not_called()
            store.record(
                bot_handler,
                run.trigger_message_id,
                AgentRun.DONE,
                stream=ARMADA_DEFAULT_STREAM,
                topic="workspace-observer",
            )
            store.record(
                bot_handler,
                run.trigger_message_id,
                AgentRun.DONE,
                stream=ARMADA_DEFAULT_STREAM,
                topic="workspace-observer",
            )

        refresh_workspace.assert_called_once_with(workspace_node.id)

    def test_worker_run_store_only_requeues_failed_runs_explicitly(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=907,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.FAILED,
            error="crashed",
        )

        store.record(
            bot_handler,
            run.trigger_message_id,
            AgentRun.QUEUED,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-a",
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(run.error, "crashed")
        implicit_requeue_rejection = AgentRunEvent.objects.get(
            run=run,
            event_type="transition_rejected",
        )
        self.assertEqual(
            implicit_requeue_rejection.payload["reason"],
            "implicit_requeue",
        )

        self.assertTrue(
            store.start(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.QUEUED)
        self.assertIsNone(run.error)

    def test_worker_run_store_claims_queued_run_once(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=908,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.QUEUED,
        )

        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.RUNNING)
        self.assertEqual(run.attempts, 1)
        self.assertIsNotNone(run.heartbeat_at)

        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.RUNNING)
        self.assertEqual(run.attempts, 1)
        self.assertEqual(
            AgentRunEvent.objects.filter(run=run, event_type="claimed").count(),
            1,
        )
        self.assertEqual(
            AgentRunEvent.objects.get(run=run, event_type="claim_dropped").payload["reason"],
            "already_running",
        )

    def test_stale_worker_attempt_rejects_every_worker_owned_store_path(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                957,
                stream=ARMADA_DEFAULT_STREAM,
                topic="fenced-store",
            )
        )
        run = AgentRun.objects.get(trigger_message_id=957)
        self.assertEqual(run.attempts, 1)
        event_count = run.events.count()
        AgentRun.objects.filter(id=run.id).update(attempts=2)

        operations = {
            "progress": lambda: store.register_progress_message(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="fenced-store",
                progress_message_id=4001,
            ),
            "status": lambda: store.record(
                bot_handler,
                run.trigger_message_id,
                AgentRun.DONE,
                stream=ARMADA_DEFAULT_STREAM,
                topic="fenced-store",
                result_message_id=4001,
            ),
            "metadata": lambda: store.update_metadata(
                bot_handler,
                run.trigger_message_id,
                summary="stale summary",
            ),
            "event": lambda: store.record_event(
                bot_handler,
                run.trigger_message_id,
                "transcript",
                summary="stale event",
            ),
            "cancel": lambda: store.cancel_requested(
                bot_handler,
                run.trigger_message_id,
            ),
            "steering": lambda: store.steering_events_after(
                bot_handler,
                run.trigger_message_id,
                after_event_id=0,
            ),
        }
        for operation_name, operation in operations.items():
            with (
                self.subTest(operation=operation_name),
                self.assertRaisesRegex(ArmadaWorkerLeaseLostError, "lost attempt 1"),
                transaction.atomic(),
            ):
                operation()

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.RUNNING)
        self.assertEqual(run.attempts, 2)
        self.assertIsNone(run.result_message_id)
        self.assertEqual(run.summary, "")
        self.assertEqual(run.events.count(), event_count)

    def test_record_event_rechecks_attempt_inside_its_durable_transaction(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                958,
                stream=ARMADA_DEFAULT_STREAM,
                topic="fenced-event",
            )
        )
        run = AgentRun.objects.get(trigger_message_id=958)
        original_record_event = store._record_event

        def reclaim_before_durable_write(
            locked_run: AgentRun,
            event_type: str,
            *,
            summary: str,
            payload: dict[str, Any],
        ) -> None:
            AgentRun.objects.filter(id=locked_run.id).update(attempts=2)
            original_record_event(
                locked_run,
                event_type,
                summary=summary,
                payload=payload,
            )

        with (
            patch.object(store, "_record_event", side_effect=reclaim_before_durable_write),
            self.assertRaisesRegex(ArmadaWorkerLeaseLostError, "lost attempt 1"),
            transaction.atomic(),
        ):
            store.record_event(
                bot_handler,
                run.trigger_message_id,
                "transcript",
                summary="must not commit",
            )

        run.refresh_from_db()
        self.assertEqual(run.attempts, 1)
        self.assertFalse(run.events.filter(summary="must not commit").exists())

    def test_worker_completion_rejects_terminal_run_with_the_same_attempt(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()

        for message_id, status in (
            (1961, AgentRun.CANCELLED),
            (1962, AgentRun.FAILED),
        ):
            topic = f"terminal-fence-{status}"
            self.assertTrue(
                store.claim_for_run(
                    bot_handler,
                    message_id,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic=topic,
                )
            )
            run = AgentRun.objects.get(trigger_message_id=message_id)
            self.assertEqual(run.attempts, 1)
            AgentRun.objects.filter(id=run.id).update(status=status)

            entered_completion = False
            with (
                self.subTest(status=status),
                self.assertRaisesRegex(ArmadaWorkerLeaseLostError, "lost attempt 1"),
                transaction.atomic(),
                store.worker_completion(bot_handler, message_id),
            ):
                entered_completion = True
            self.assertFalse(entered_completion)

    def test_claimed_cancel_check_distinguishes_cancelled_from_failed_run(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        cancelled_at = timezone_now()

        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                1963,
                stream=ARMADA_DEFAULT_STREAM,
                topic="cancel-check-cancelled",
            )
        )
        cancelled_run = AgentRun.objects.get(trigger_message_id=1963)
        AgentRun.objects.filter(id=cancelled_run.id).update(
            status=AgentRun.CANCELLED,
            cancel_requested_at=None,
        )
        self.assertTrue(store.cancel_requested(bot_handler, 1963))

        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                1964,
                stream=ARMADA_DEFAULT_STREAM,
                topic="cancel-check-failed",
            )
        )
        failed_run = AgentRun.objects.get(trigger_message_id=1964)
        AgentRun.objects.filter(id=failed_run.id).update(
            status=AgentRun.FAILED,
            cancel_requested_at=cancelled_at,
        )
        with self.assertRaisesRegex(ArmadaWorkerLeaseLostError, "lost attempt 1"):
            store.cancel_requested(bot_handler, 1964)

    def test_steering_events_carry_their_authors_in_one_query(self) -> None:
        owner = self.example_user("hamlet")
        iago = self.example_user("iago")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=960,
            stream_name="Verona",
            topic_name="authors",
            status=AgentRun.RUNNING,
        )
        store = ArmadaDatabaseWorkerRunStore()

        def steer(sender: UserProfile | None, instruction: str) -> AgentRunEvent:
            message_id = (
                self.send_stream_message(sender, "Verona", instruction, topic_name="authors")
                if sender is not None
                else 999999
            )
            return AgentRunEvent.objects.create(
                run=run,
                event_type="steering",
                summary=instruction,
                payload={"instruction": instruction, "message_id": message_id},
            )

        first = steer(owner, "keep the patch small")
        # The run, its steering events, and their messages with senders.
        with self.assert_database_query_count(3):
            self.assert_length(store.steering_events_after(bot_handler, 960, after_event_id=0), 1)
        second = steer(iago, "and add a test")
        # A steer whose message is gone keeps its words, with no name.
        third = steer(None, "and update the docs")
        with self.assert_database_query_count(3):
            pending = store.steering_events_after(bot_handler, 960, after_event_id=0)

        self.assertEqual(
            [
                (event["event_id"], event.get("sender_full_name"), event["instruction"])
                for event in pending
            ],
            [
                (first.id, owner.full_name, "keep the patch small"),
                (second.id, iago.full_name, "and add a test"),
                (third.id, None, "and update the docs"),
            ],
        )

    def test_unclaimed_control_store_keeps_preclaim_paths_unfenced(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        cancelled_at = timezone_now()
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=959,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="control-store",
            status=AgentRun.RUNNING,
            attempts=7,
            cancel_requested_at=cancelled_at,
        )
        steering_event = AgentRunEvent.objects.create(
            run=run,
            event_type="steering",
            summary="Continue carefully",
            payload={"instruction": "Continue carefully", "message_id": 991},
        )
        control_store = ArmadaDatabaseWorkerRunStore()

        control_store.register_progress_message(
            bot_handler,
            run.trigger_message_id,
            stream=ARMADA_DEFAULT_STREAM,
            topic="control-store",
            progress_message_id=4002,
        )
        control_store.record(
            bot_handler,
            run.trigger_message_id,
            AgentRun.RUNNING,
            stream=ARMADA_DEFAULT_STREAM,
            topic="control-store",
            result_message_id=4002,
        )
        control_store.update_metadata(
            bot_handler,
            run.trigger_message_id,
            summary="control metadata",
        )
        control_store.record_event(
            bot_handler,
            run.trigger_message_id,
            "control_event",
            summary="control event",
        )

        self.assertTrue(control_store.cancel_requested(bot_handler, run.trigger_message_id))
        self.assertEqual(
            control_store.steering_events_after(
                bot_handler,
                run.trigger_message_id,
                after_event_id=0,
            ),
            [
                {
                    "event_id": steering_event.id,
                    "message_id": 991,
                    "instruction": "Continue carefully",
                }
            ],
        )
        # Nothing is pending past the last steer.
        self.assertEqual(
            control_store.steering_events_after(
                bot_handler,
                run.trigger_message_id,
                after_event_id=steering_event.id,
            ),
            [],
        )
        run.refresh_from_db()
        self.assertEqual(run.attempts, 7)
        self.assertEqual(run.result_message_id, 4002)
        self.assertEqual(run.summary, "control metadata")
        self.assertTrue(run.events.filter(event_type="control_event").exists())

    def test_worker_run_store_drops_completed_redelivery(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=909,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.DONE,
            result_message_id=400,
        )

        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.DONE)
        self.assertEqual(run.result_message_id, 400)
        self.assertEqual(
            AgentRunEvent.objects.get(run=run, event_type="claim_dropped").payload["reason"],
            "terminal_or_finished_done",
        )

    def test_worker_run_store_reclaims_stale_running_once(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        stale_time = timezone_now() - timedelta(seconds=400)
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=910,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            attempts=1,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )

        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.RUNNING)
        self.assertEqual(run.attempts, 2)
        assert run.heartbeat_at is not None
        self.assertGreater(run.heartbeat_at, stale_time)
        self.assertEqual(
            AgentRunEvent.objects.get(run=run, event_type="claimed").payload["stale_reclaim"],
            True,
        )

        AgentRun.objects.filter(id=run.id).update(
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )
        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(
            run.error,
            "The run exceeded its stale-claim retry limit.",
        )
        self.assertEqual(
            AgentRunEvent.objects.filter(run=run, event_type="claim_dropped")
            .latest("id")
            .payload["reason"],
            "stale_retry_limit",
        )

    def test_redelivery_reclaims_a_run_after_the_reaper_wins_the_race(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        store = ArmadaDatabaseWorkerRunStore()
        stale_time = timezone_now() - timedelta(seconds=400)
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=911,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            attempts=1,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )

        with patch("zerver.lib.armada_worker.settle_orphaned_armada_run_message"):
            self.assertEqual(reap_stale_armada_worker_runs(bot_profile=armada_bot), 1)

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(run.events.latest("id").event_type, "stale_reaped")
        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
                redelivered=True,
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.RUNNING)
        self.assertEqual(run.attempts, 2)
        self.assertIsNone(run.error)
        self.assertEqual(run.events.latest("id").event_type, "claimed")

    def test_worker_run_store_busy_reader_reaps_stale_running_run(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        stale_time = timezone_now() - timedelta(seconds=400)
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=911,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            attempts=1,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )

        self.assertIsNone(
            ArmadaDatabaseWorkerRunStore().active_worker_run_message_id(
                bot_handler,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(run.error, "The run became stale and was recovered.")
        self.assertEqual(
            AgentRunEvent.objects.get(run=run, event_type="stale_reaped").payload[
                "stale_after_seconds"
            ],
            60,
        )

    def test_worker_liveness_pass_protects_active_run_and_settles_inactive_stale_run(
        self,
    ) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        stale_time = timezone_now() - timedelta(seconds=61)
        active_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=913,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            attempts=1,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )
        abandoned_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=914,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-b",
            status=AgentRun.RUNNING,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )

        self.assertEqual(
            maintain_armada_worker_run_liveness(
                active_run_id=active_run.id,
                active_run_attempt=active_run.attempts,
                session_kinds=(AgentRun.SHARED,),
            ),
            1,
        )

        active_run.refresh_from_db()
        abandoned_run.refresh_from_db()
        self.assertEqual(active_run.status, AgentRun.RUNNING)
        assert active_run.heartbeat_at is not None
        self.assertGreater(active_run.heartbeat_at, stale_time)
        self.assertEqual(abandoned_run.status, AgentRun.FAILED)
        self.assertEqual(
            AgentRunEvent.objects.get(run=abandoned_run, event_type="stale_reaped").payload[
                "stale_after_seconds"
            ],
            60,
        )

    def test_worker_liveness_renewal_requires_the_matching_claim_attempt(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        old_heartbeat = timezone_now() - timedelta(seconds=20)
        active_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=960,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="attempt-liveness",
            status=AgentRun.RUNNING,
            attempts=3,
            heartbeat_at=old_heartbeat,
            date_updated=old_heartbeat,
        )

        for stale_attempt in (None, 2):
            with (
                self.subTest(attempt=stale_attempt),
                self.assertRaises(ArmadaWorkerLeaseLostError),
            ):
                maintain_armada_worker_run_liveness(
                    active_run_id=active_run.id,
                    active_run_attempt=stale_attempt,
                    session_kinds=(AgentRun.SHARED,),
                )

        active_run.refresh_from_db()
        self.assertEqual(active_run.heartbeat_at, old_heartbeat)

        self.assertEqual(
            maintain_armada_worker_run_liveness(
                active_run_id=active_run.id,
                active_run_attempt=3,
                session_kinds=(AgentRun.SHARED,),
            ),
            0,
        )
        active_run.refresh_from_db()
        assert active_run.heartbeat_at is not None
        self.assertGreater(active_run.heartbeat_at, old_heartbeat)

    def test_worker_liveness_ignores_the_exact_attempt_after_it_settles(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        settled_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=961,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="settled-liveness",
            status=AgentRun.DONE,
            attempts=4,
            heartbeat_at=timezone_now(),
        )

        self.assertEqual(
            maintain_armada_worker_run_liveness(
                active_run_id=settled_run.id,
                active_run_attempt=4,
                session_kinds=(AgentRun.SHARED,),
            ),
            0,
        )

    def test_worker_run_store_events_refresh_heartbeat_when_due(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        old_heartbeat = timezone_now() - timedelta(seconds=20)
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=912,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            attempts=1,
            heartbeat_at=old_heartbeat,
            date_updated=old_heartbeat,
        )
        store = ArmadaDatabaseWorkerRunStore()

        store.record_event(
            bot_handler,
            run.trigger_message_id,
            "transcript",
            summary="Ran tool.",
            payload={},
        )
        run.refresh_from_db()
        refreshed_heartbeat = run.heartbeat_at
        self.assertIsNotNone(refreshed_heartbeat)
        assert refreshed_heartbeat is not None
        self.assertGreater(refreshed_heartbeat, old_heartbeat)

        store.record_event(
            bot_handler,
            run.trigger_message_id,
            "transcript",
            summary="Ran another tool.",
            payload={},
        )
        run.refresh_from_db()
        self.assertEqual(run.heartbeat_at, refreshed_heartbeat)

    def test_worker_run_store_refuses_paused_topic_at_start_and_claim(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=armada_bot)
        context = ensure_armada_workspace_node(
            armada_bot,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-a",
        )
        context.is_paused = True
        context.save(update_fields=["is_paused"])
        store = ArmadaDatabaseWorkerRunStore()

        self.assertEqual(
            store.start_blocked_reason(
                bot_handler,
                913,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
                requesting_user_id=owner.id,
            ),
            "The session is paused until a resume control is posted.",
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=913,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.QUEUED,
        )
        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=ARMADA_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(run.error, "The topic is paused.")
        self.assertEqual(
            AgentRunEvent.objects.get(run=run, event_type="claim_dropped").payload["reason"],
            "topic_paused",
        )

    def test_active_session_reflects_database_pause_flag(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, ARMADA_DEFAULT_STREAM)
        context = ensure_armada_workspace_node(
            user,
            stream=ARMADA_DEFAULT_STREAM,
            topic=ARMADA_DEFAULT_TOPIC,
        )
        context.is_paused = True
        context.save(update_fields=["is_paused"])
        self.login_user(user)

        result = self.client_get(
            f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        active_session = self.assert_json_success(result)["active_session"]
        self.assertEqual(active_session["state"], "paused")
        self.assertTrue(active_session["is_paused"])
        self.assertFalse(active_session["worker_can_run"])

    def test_run_controls_request_cancel_and_toggle_topic_pause(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        context = ensure_armada_workspace_node(
            armada_bot,
            stream=ARMADA_DEFAULT_STREAM,
            topic="topic-a",
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=914,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
        )
        self.login_user(owner)

        with (
            self.captureOnCommitCallbacks(execute=True),
            patch("zerver.views.armada.send_armada_run_event") as send_run_event,
        ):
            pause_result = self.client_post(
                f"/json/armada/runs/{run.id}/pause",
                intentionally_undocumented=True,
            )
            self.assert_json_success(pause_result)
            context.refresh_from_db()
            self.assertTrue(context.is_paused)

            resume_result = self.client_post(
                f"/json/armada/runs/{run.id}/resume",
                intentionally_undocumented=True,
            )
            self.assert_json_success(resume_result)
            context.refresh_from_db()
            self.assertFalse(context.is_paused)

            cancel_result = self.client_post(
                f"/json/armada/runs/{run.id}/cancel",
                intentionally_undocumented=True,
            )
            self.assert_json_success(cancel_result)

        self.assertEqual(
            [call.args[1].event_type for call in send_run_event.call_args_list],
            ["pause", "resume", "cancel"],
        )
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.CANCELLED)
        self.assertIsNotNone(run.cancel_requested_at)

    def test_pause_locks_workspace_before_run(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "armada-lock-order",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=916,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-lock-order",
            status=AgentRun.RUNNING,
        )
        call_order: list[str] = []
        real_select_for_update = AgentRun.objects.select_for_update

        def select_for_update(*args: Any, **kwargs: Any) -> Any:
            call_order.append("run")
            return real_select_for_update(*args, **kwargs)

        with (
            patch(
                "zerver.views.armada.set_armada_workspace_paused",
                side_effect=lambda *args, **kwargs: call_order.append("workspace"),
            ),
            patch.object(
                AgentRun.objects,
                "select_for_update",
                side_effect=select_for_update,
            ),
            patch("zerver.views.armada.send_armada_run_event"),
        ):
            update_armada_run_control_event(owner, run.id, "pause", "Paused by user.")

        self.assertEqual(call_order, ["workspace", "run"])

    def test_cancel_does_not_record_false_event_after_completion(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=915,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.DONE,
        )
        self.login_user(owner)

        with patch("zerver.views.armada.send_armada_run_event") as send_run_event:
            result = self.client_post(
                f"/json/armada/runs/{run.id}/cancel",
                intentionally_undocumented=True,
            )

        response = self.assert_json_success(result)
        self.assertEqual(response["armada_run"]["status"], AgentRun.DONE)
        self.assertFalse(run.events.filter(event_type="cancel").exists())
        send_run_event.assert_not_called()

    def test_worker_event_push_and_presentation_respect_commit_order(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "armada-commit-order",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        # Give the run a real visibility-scoped trigger message.  The push
        # helper intentionally drops events whose trigger no longer exists.
        with patch("zerver.worker.embedded_bots.queue_json_publish_rollback_unsafe"):
            trigger_message_id = self.send_stream_message(
                owner,
                ARMADA_DEFAULT_STREAM,
                topic_name="topic-a",
                content="@**Onix** inspect commit ordering",
            )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=trigger_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
        )
        store = ArmadaDatabaseWorkerRunStore()

        with (
            patch("zerver.actions.armada.send_event_on_commit") as send_event_on_commit,
            patch("zerver.lib.armada_worker.maybe_enqueue_armada_run_presentation") as enqueue,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            store.record_event(
                SimpleNamespace(user_profile=armada_bot),
                run.trigger_message_id,
                "transcript",
                summary="Read files.",
                payload={},
            )
            # Push delivery is registered through the transaction-aware
            # action helper while its payload still reflects this event.
            send_event_on_commit.assert_called_once()
            enqueue.assert_not_called()

        self.assertEqual(len(callbacks), 1)
        enqueue.assert_called_once()

    def test_run_trace_is_safe_and_attention_is_bounded_to_active_runs(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "armada-trace",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=919,
            result_message_id=920,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            codex_thread_id="root-thread",
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Hidden reasoning",
            payload={"kind": "reasoning", "body": "private chain of thought"},
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Reading files",
            payload={
                "kind": "command",
                "title": "Read repository files",
                "body": "secret raw command output",
                "item_id": "command-1",
                "status": "running",
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Read files",
            payload={
                "kind": "command",
                "title": "Read repository files",
                "item_id": "command-1",
                "status": "done",
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Codex",
            payload={"kind": "assistant", "body": "I’m checking the relevant files."},
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Codex thread child-thread",
            payload={
                "kind": "session",
                "title": "Codex thread child-thread",
                "metadata": {
                    "codex_method": "thread/started",
                    "codex_thread_id": "child-thread",
                    "thread": {
                        "id": "child-thread",
                        "parent_thread_id": "root-thread",
                        "agent_nickname": "Scout",
                        "agent_role": "researcher",
                        "status": {"type": "active"},
                        "created_at": 1.0,
                    },
                },
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Hidden child reasoning",
            payload={
                "kind": "reasoning",
                "body": "private child chain of thought",
                "metadata": {
                    "codex_method": "item/completed",
                    "codex_thread_id": "child-thread",
                    "codex_turn_id": "child-turn",
                },
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Inspect child files",
            payload={
                "kind": "command",
                "title": "Inspect child files",
                "body": "secret child command output",
                "status": "done",
                "metadata": {
                    "codex_method": "item/completed",
                    "codex_thread_id": "child-thread",
                    "codex_turn_id": "child-turn",
                },
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Child result",
            payload={
                "kind": "assistant",
                "body": "The child found the relevant rendering seam.",
                "status": "done",
                "metadata": {
                    "codex_method": "item/completed",
                    "codex_thread_id": "child-thread",
                    "codex_turn_id": "child-turn",
                },
            },
        )
        AgentRunEvent.objects.create(
            run=run,
            event_type="agent",
            summary="Codex runtime error.",
            payload={
                "kind": "error",
                "title": "Codex runtime error.",
                "body": "secret child runtime detail",
                "status": "failed",
                "metadata": {
                    "codex_method": "error",
                    "codex_thread_id": "child-thread",
                    "codex_turn_id": "child-turn",
                    "parent_thread_id": "root-thread",
                },
            },
        )
        self.login_user(owner)

        trace_result = self.client_get(
            f"/json/armada/runs/{run.id}/trace",
            intentionally_undocumented=True,
        )
        trace_payload = self.assert_json_success(trace_result)
        trace = trace_payload["trace"]
        trace_text = orjson.dumps(trace).decode()
        self.assertIn("Read repository files", trace_text)
        self.assertIn("I’m checking the relevant files.", trace_text)
        self.assertNotIn("private chain of thought", trace_text)
        self.assertNotIn("secret raw command output", trace_text)
        self.assertIn("spoiler-block", trace_payload["rendered"])
        self.assertEqual(trace_payload["rendered"].count("Read repository files"), 1)
        self.assertIn("I’m checking the relevant files.", trace_payload["rendered"])
        self.assertNotIn("private chain of thought", trace_payload["rendered"])
        self.assertNotIn("secret raw command output", trace_payload["rendered"])

        first_page_result = self.client_get(
            f"/json/armada/runs/{run.id}/trace?limit=1",
            intentionally_undocumented=True,
        )
        first_page = self.assert_json_success(first_page_result)
        self.assertEqual(len(first_page["trace"]), 1)
        self.assertEqual(first_page["trace"][0]["kind"], "assistant")
        self.assertTrue(first_page["has_more"])
        second_page_result = self.client_get(
            f"/json/armada/runs/{run.id}/trace?limit=1&before_event_id={first_page['next_before_event_id']}",
            intentionally_undocumented=True,
        )
        second_page = self.assert_json_success(second_page_result)
        self.assertEqual(len(second_page["trace"]), 1)
        self.assertEqual(second_page["trace"][0]["kind"], "command")
        self.assertTrue(second_page["has_more"])
        third_page_result = self.client_get(
            f"/json/armada/runs/{run.id}/trace?limit=1&before_event_id={second_page['next_before_event_id']}",
            intentionally_undocumented=True,
        )
        third_page = self.assert_json_success(third_page_result)
        self.assertEqual(len(third_page["trace"]), 1)
        self.assertEqual(third_page["trace"][0]["kind"], "command")
        self.assertFalse(third_page["has_more"])

        child_trace_result = self.client_get(
            f"/json/armada/runs/{run.id}/trace?thread_id=child-thread",
            intentionally_undocumented=True,
        )
        child_trace_payload = self.assert_json_success(child_trace_result)
        self.assertEqual(child_trace_payload["thread_id"], "child-thread")
        child_trace_text = orjson.dumps(child_trace_payload["trace"]).decode()
        self.assertIn("Inspect child files", child_trace_text)
        self.assertIn("The child found the relevant rendering seam.", child_trace_text)
        self.assertIn("Codex runtime error.", child_trace_text)
        self.assertNotIn("private child chain of thought", child_trace_text)
        self.assertNotIn("secret child command output", child_trace_text)
        self.assertNotIn("secret child runtime detail", child_trace_text)
        self.assertIn(
            "The child found the relevant rendering seam.", child_trace_payload["rendered"]
        )
        self.assertIn("Codex runtime error.", child_trace_payload["rendered"])
        self.assertNotIn("private child chain of thought", child_trace_payload["rendered"])
        self.assertNotIn("secret child command output", child_trace_payload["rendered"])
        self.assertNotIn("secret child runtime detail", child_trace_payload["rendered"])

        missing_child_result = self.client_get(
            f"/json/armada/runs/{run.id}/trace?thread_id=unrelated-thread",
            intentionally_undocumented=True,
        )
        self.assert_json_error(missing_child_result, "Agent thread not found.")

        run_payload = self.assert_json_success(
            self.client_get(
                f"/json/armada/runs/{run.id}",
                intentionally_undocumented=True,
            )
        )["armada_run"]
        self.assertEqual(len(run_payload["agents"]), 1)
        self.assertEqual(run_payload["agents"][0]["thread_id"], "child-thread")
        self.assertEqual(run_payload["agents"][0]["nickname"], "Scout")
        self.assertEqual(run_payload["agents"][0]["status"], "failed")

        with (
            patch("zerver.views.armada.mark_armada_run_attended") as mark_attended,
            patch("zerver.views.armada.maybe_enqueue_armada_run_presentation") as enqueue,
        ):
            attention_result = self.client_post(
                f"/json/armada/runs/{run.id}/attention",
                intentionally_undocumented=True,
            )

        self.assertTrue(self.assert_json_success(attention_result)["attended"])
        mark_attended.assert_called_once_with(run.id)
        enqueue.assert_called_once()

        run.status = AgentRun.DONE
        run.save(update_fields=["status"])
        settled_result = self.client_post(
            f"/json/armada/runs/{run.id}/attention",
            intentionally_undocumented=True,
        )
        self.assertFalse(self.assert_json_success(settled_result)["attended"])

    def test_not_for_agent_feedback_is_ambient_only_and_idempotent(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        ambient_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=915,
            result_message_id=916,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.RUNNING,
            ambient_triggered=True,
        )
        explicit_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=917,
            result_message_id=918,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="topic-a",
            status=AgentRun.DONE,
        )
        self.login_user(owner)

        first_result = self.client_post(
            f"/json/armada/runs/{ambient_run.id}/not-for-agent",
            intentionally_undocumented=True,
        )
        first_payload = self.assert_json_success(first_result)
        self.assertTrue(first_payload["changed"])
        ambient_run.refresh_from_db()
        self.assertEqual(ambient_run.status, AgentRun.CANCELLED)
        self.assertEqual(
            AgentRunEvent.objects.get(
                run=ambient_run,
                event_type="routing_feedback",
            ).payload,
            {
                "label": "false_positive",
                "trigger_message_id": 915,
                "result_message_id": 916,
                "user_id": owner.id,
            },
        )

        second_result = self.client_post(
            f"/json/armada/runs/{ambient_run.id}/not-for-agent",
            intentionally_undocumented=True,
        )
        second_payload = self.assert_json_success(second_result)
        self.assertFalse(second_payload["changed"])
        self.assertEqual(
            AgentRunEvent.objects.filter(
                run=ambient_run,
                event_type="routing_feedback",
            ).count(),
            1,
        )

        explicit_result = self.client_post(
            f"/json/armada/runs/{explicit_run.id}/not-for-agent",
            intentionally_undocumented=True,
        )
        self.assert_json_error(explicit_result, "This response was explicitly requested.")

    def test_worker_run_store_sets_topic_response_policy(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            store = ArmadaDatabaseWorkerRunStore()
            bot_handler = SimpleNamespace(user_profile=armada_bot)
            store.set_topic_response_policy(
                bot_handler,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
                response_policy="ambient",
            )
            context = Workspace.objects.get(
                realm=owner.realm,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
            )

        self.assertEqual(context.response_policy, "ambient")
        self.assertEqual(
            store.topic_response_policy(
                bot_handler,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            ),
            "ambient",
        )

    def test_armada_codex_model_catalog_uses_visible_priority_and_reasoning(self) -> None:
        catalog = extract_armada_codex_model_catalog(
            {
                "models": [
                    {
                        "slug": "hidden-model",
                        "display_name": "Hidden",
                        "visibility": "hide",
                        "priority": 1,
                    },
                    {
                        "slug": "gpt-visible-b",
                        "display_name": "Visible B",
                        "visibility": "list",
                        "priority": 20,
                        "supported_reasoning_levels": [{"effort": "low"}],
                    },
                    {
                        "slug": "gpt-visible-a",
                        "display_name": "Visible A",
                        "visibility": "list",
                        "priority": 10,
                        "default_reasoning_level": "high",
                        "supported_reasoning_levels": [{"effort": "high"}],
                    },
                ],
            }
        )

        self.assertEqual([entry["slug"] for entry in catalog], ["gpt-visible-a", "gpt-visible-b"])
        self.assertEqual(catalog[0]["display_name"], "Visible A")
        self.assertEqual(catalog[0]["default_reasoning_level"], "high")
        self.assertEqual(
            catalog[0]["supported_reasoning_levels"],
            [{"effort": "high", "label": "High", "description": ""}],
        )

    def test_post_armada_topic_context_changes_model_without_posting(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)
        last_message_id_before_change = Message.objects.latest("id").id

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_WORKER_MODEL="gpt-5.5",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            result = self.client_post(
                f"/json{ARMADA_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "model": "gpt-5.5-mini",
                    "reasoning_effort": "high",
                },
                intentionally_undocumented=True,
            )
            model = armada_worker_model_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )
            reasoning_effort = armada_worker_reasoning_effort_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )
            workdir = armada_worker_workdir_for_topic(
                armada_bot,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
            )

            result_dict = self.assert_json_success(result)
            self.assertTrue(result_dict["changed"])
            self.assertEqual(result_dict["armada_topic_context"]["model"], "gpt-5.5-mini")
            self.assertEqual(result_dict["armada_topic_context"]["reasoning_effort"], "high")
            self.assertEqual(
                result_dict["armada_topic_context"]["response_policy"],
                "ambient",
            )
            self.assertEqual(model, "gpt-5.5-mini")
            self.assertEqual(reasoning_effort, "high")
            self.assertTrue(workdir.startswith(temp_dir))

        # Changing the runtime is a settings action, not a conversation:
        # no message may land in the topic.
        self.assertEqual(Message.objects.latest("id").id, last_message_id_before_change)

    def test_post_armada_topic_context_changes_response_policy_without_model(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_WORKER_MODEL="gpt-5.5",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            result = self.client_post(
                f"/json{ARMADA_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "response_policy": "mentions_only",
                },
                intentionally_undocumented=True,
            )
            active_session_result = self.client_get(
                f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

            result_dict = self.assert_json_success(result)
            active_session_dict = self.assert_json_success(active_session_result)
            context = Workspace.objects.get(
                realm=owner.realm,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=ARMADA_DEFAULT_TOPIC,
            )

        self.assertTrue(result_dict["changed"])
        self.assertEqual(result_dict["armada_topic_context"]["response_policy"], "mentions_only")
        self.assertEqual(context.response_policy, "mentions_only")
        self.assertEqual(active_session_dict["active_session"]["response_policy"], "mentions_only")

    def test_get_armada_active_session_without_stream(self) -> None:
        self.login("hamlet")

        # Ask for a deliberately nonexistent channel; the default channel is
        # created in setUp, so the missing-stream state needs a named stream.
        result = self.client_get(
            f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
            {"stream": "definitely-not-a-channel"},
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(active_session["state"], "not_started")
        self.assertEqual(active_session["source"], "server")
        self.assertFalse(active_session["stream_exists"])
        self.assertEqual(active_session["topic_message_count"], 0)
        self.assertEqual(active_session["request_count"], 0)
        self.assertIsNone(active_session["latest_request_message_id"])
        self.assertEqual(active_session["worker_state"], "blocked_missing_stream")
        self.assertFalse(active_session["worker_can_run"])
        self.assertIn("channel", active_session["worker_blocked_reason"])

    def test_get_armada_active_session_from_request_message(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, ARMADA_DEFAULT_STREAM)
        request_message_id = self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "@Onix please debug the flaky parser test.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(active_session["state"], "requested")
        self.assertEqual(active_session["request_count"], 1)
        self.assertEqual(active_session["latest_request_message_id"], request_message_id)
        self.assertIsNone(active_session["latest_message_id"])
        self.assertEqual(active_session["worker_state"], "ready")
        self.assertTrue(active_session["worker_can_run"])
        self.assertIsNone(active_session["worker_blocked_reason"])

    def test_get_armada_active_session_uses_ambient_run_state(self) -> None:
        user = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            user,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.addCleanup(invalidate_armada_selected_bot_cache, user.realm_id)
        self.subscribe(user, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        request_message_id = self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "In one sentence, explain what a hash map is.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        result_message_id = self.send_stream_message(
            armada_bot,
            ARMADA_DEFAULT_STREAM,
            "A hash map stores key-value pairs for fast lookup.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        AgentRun.objects.create(
            realm=user.realm,
            bot_profile=armada_bot,
            requesting_user=user,
            trigger_message_id=request_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.DONE,
            codex_thread_id="thread-ambient",
            ambient_triggered=True,
            result_message_id=result_message_id,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(active_session["state"], "active")
        self.assertEqual(active_session["request_count"], 1)
        self.assertEqual(active_session["latest_request_message_id"], request_message_id)
        self.assertEqual(active_session["latest_message_id"], result_message_id)
        self.assertEqual(active_session["latest_codex_thread_id"], "thread-ambient")

    def test_worker_loader_uses_prior_run_as_thread_ingestion_cursor(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        first_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "agent, Choose a random 15 digit number.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=first_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.DONE,
            codex_thread_id="thread-first",
        )
        self.send_stream_message(
            armada_bot,
            ARMADA_DEFAULT_STREAM,
            "I picked a number and retained it in the Codex thread.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        berry = self.example_user("othello")
        self.subscribe(berry, ARMADA_DEFAULT_STREAM)
        self.send_stream_message(
            berry,
            ARMADA_DEFAULT_STREAM,
            "The number should still be in the Codex thread.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        second_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "agent, what was the number you picked?",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )

        active_session = load_armada_active_session_from_zulip(
            {
                "id": second_message_id,
                "type": "stream",
                "display_recipient": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "content": "agent, what was the number you picked?",
            },
            SimpleNamespace(user_profile=armada_bot),
        )

        self.assertEqual(active_session["latest_codex_thread_id"], "thread-first")
        # The agent's page tools act as the person whose message started the run.
        self.assertEqual(active_session.get("requesting_user_id"), owner.id)
        self.assertEqual(
            [message["content"] for message in active_session["recent_messages"]],
            [
                "The number should still be in the Codex thread.",
            ],
        )
        self.assertEqual(active_session["recent_messages"][0]["sender"], berry.full_name)
        self.assertEqual(
            [
                message["content"]
                for message in active_session["routing_messages"]
                if message["message_id"] != second_message_id
            ],
            [
                "agent, Choose a random 15 digit number.",
                "I picked a number and retained it in the Codex thread.",
                "The number should still be in the Codex thread.",
            ],
        )

    def test_worker_loader_keeps_child_replies_out_of_parent_prompt_delta(self) -> None:
        owner = self.example_user("hamlet")
        berry = self.example_user("othello")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        for user in (owner, berry, armada_bot):
            self.subscribe(user, ARMADA_DEFAULT_STREAM)
        first_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix inspect the parser",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=first_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.DONE,
            codex_thread_id="thread-first",
        )
        parent_message_id = self.send_stream_message(
            berry,
            ARMADA_DEFAULT_STREAM,
            "The shared parser fixture changed.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        child_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix /btw what does that mean?",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        Message.objects.filter(id=child_message_id).update(thread_root_id=parent_message_id)
        trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix continue the parent task",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )

        active_session = load_armada_active_session_from_zulip(
            {
                "id": trigger_message_id,
                "type": "stream",
                "display_recipient": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "content": "@Onix continue the parent task",
            },
            SimpleNamespace(user_profile=armada_bot),
        )

        self.assertEqual(active_session["latest_codex_thread_id"], "thread-first")
        self.assertEqual(
            [message["content"] for message in active_session["recent_messages"]],
            ["The shared parser fixture changed."],
        )
        self.assertIn(
            "@Onix /btw what does that mean?",
            [message["content"] for message in active_session["routing_messages"]],
        )

    def test_worker_loader_uses_replied_to_message_to_select_side_parent(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        old_trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix explain the parser",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        old_result_message_id = self.send_stream_message(
            armada_bot,
            ARMADA_DEFAULT_STREAM,
            "The parser has two phases.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=old_trigger_message_id,
            result_message_id=old_result_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            session_kind=AgentRun.SHARED,
            status=AgentRun.DONE,
            codex_thread_id="thread-old",
        )
        new_trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix inspect the renderer",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        new_result_message_id = self.send_stream_message(
            armada_bot,
            ARMADA_DEFAULT_STREAM,
            "The renderer is healthy.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=new_trigger_message_id,
            result_message_id=new_result_message_id,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            session_kind=AgentRun.SHARED,
            status=AgentRun.DONE,
            codex_thread_id="thread-new",
        )
        side_trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "@Onix what do you mean by two phases?",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )

        active_session = load_armada_active_session_from_zulip(
            {
                "id": side_trigger_message_id,
                "type": "stream",
                "display_recipient": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "content": "@Onix what do you mean by two phases?",
                "sender_full_name": owner.full_name,
                "thread_root_message_id": old_result_message_id,
            },
            SimpleNamespace(user_profile=armada_bot),
        )

        self.assertEqual(active_session["latest_codex_thread_id"], "thread-old")
        self.assertEqual(
            active_session["latest_codex_trigger_message_id"],
            old_trigger_message_id,
        )

    def test_armada_reference_parser_uses_canonical_zulip_link_syntax(self) -> None:
        self.assertEqual(
            armada_referenced_conversations(
                "Compare #**devel>parser** with #**research** and #**Devel>parser**; ignore #devel."
            ),
            [("devel", "parser"), ("research", None)],
        )
        self.assertEqual(armada_referenced_conversations("No explicit references here."), [])

    def test_worker_loader_resolves_references_with_requester_permissions(self) -> None:
        owner = self.example_user("hamlet")
        berry = self.example_user("othello")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        for user in (owner, berry, armada_bot):
            self.subscribe(user, ARMADA_DEFAULT_STREAM)
        source_message_id = self.send_stream_message(
            berry,
            ARMADA_DEFAULT_STREAM,
            "The parser has a bounded reference resolver.",
            topic_name="source-topic",
        )
        private_stream = self.make_stream("sekrit", invite_only=True)
        self.subscribe(berry, private_stream.name)
        self.send_stream_message(
            berry,
            private_stream.name,
            "This message must remain private.",
            topic_name="hidden-topic",
        )
        trigger_content = f"@Onix compare #**{ARMADA_DEFAULT_STREAM}>source-topic** with #**sekrit>hidden-topic**."
        trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            trigger_content,
            topic_name=ARMADA_DEFAULT_TOPIC,
        )

        active_session = load_armada_active_session_from_zulip(
            {
                "id": trigger_message_id,
                "sender_id": owner.id,
                "type": "stream",
                "display_recipient": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "content": trigger_content,
            },
            SimpleNamespace(user_profile=armada_bot),
        )

        self.assertEqual(
            active_session["referenced_threads"],
            [
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": "source-topic",
                    "accessible": True,
                    "truncated": False,
                    "messages": [
                        {
                            "message_id": source_message_id,
                            "sender": berry.full_name,
                            "sender_is_bot": False,
                            "content": "The parser has a bounded reference resolver.",
                            "topic": "source-topic",
                        }
                    ],
                },
                {
                    "stream": "sekrit",
                    "topic": "hidden-topic",
                    "accessible": False,
                    "truncated": False,
                    "messages": [],
                },
            ],
        )

    def test_reference_loader_skips_current_topic_and_caps_shared_budget(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "alpha beta gamma delta",
            topic_name="source-topic",
        )

        with patch(
            "zerver.lib.armada_worker.ARMADA_REFERENCED_TOTAL_TOKEN_LIMIT",
            2,
        ):
            references = load_armada_referenced_threads(
                owner,
                content=(
                    f"#**{ARMADA_DEFAULT_STREAM}>{ARMADA_DEFAULT_TOPIC}** "
                    f"#**{ARMADA_DEFAULT_STREAM}>source-topic**"
                ),
                current_stream=ARMADA_DEFAULT_STREAM,
                current_topic=ARMADA_DEFAULT_TOPIC,
            )

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["topic"], "source-topic")
        self.assertTrue(references[0]["accessible"])
        self.assertTrue(references[0]["truncated"])
        self.assertEqual(references[0]["messages"], [])

    def test_worker_loader_preserves_empty_topic_history(self) -> None:
        owner = self.example_user("hamlet")
        berry = self.example_user("othello")
        armada_bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(berry, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        self.send_stream_message(
            berry,
            ARMADA_DEFAULT_STREAM,
            "hello armada. what is occupancy?",
            topic_name="",
        )
        trigger_message_id = self.send_stream_message(
            owner,
            ARMADA_DEFAULT_STREAM,
            "onix, did you hear what berry said?",
            topic_name="",
        )
        self.send_stream_message(
            berry,
            ARMADA_DEFAULT_STREAM,
            "this message was posted after the trigger",
            topic_name="",
        )

        active_session = load_armada_active_session_from_zulip(
            {
                "id": trigger_message_id,
                "type": "stream",
                "display_recipient": ARMADA_DEFAULT_STREAM,
                "topic": "",
                "content": "onix, did you hear what berry said?",
                "sender_full_name": owner.full_name,
            },
            SimpleNamespace(user_profile=armada_bot),
        )

        self.assertEqual(active_session["topic"], "")
        self.assertEqual(
            [message["sender"] for message in active_session["recent_messages"]],
            [berry.full_name],
        )
        self.assertEqual(
            [message["content"] for message in active_session["recent_messages"]],
            [
                "hello armada. what is occupancy?",
            ],
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                ARMADA_WORKER_MODEL="gpt-5.5",
                ARMADA_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            self.assertEqual(
                armada_worker_model_for_topic(
                    armada_bot,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="",
                ),
                "gpt-5.5",
            )
            self.assertTrue(
                armada_worker_workdir_for_topic(
                    armada_bot,
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="",
                ).startswith(temp_dir)
            )
            context = Workspace.objects.get(
                realm=owner.realm,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name="",
            )
            assert context.project is not None
            self.assertEqual(context.project.repository_url, "")

            AgentRun.objects.create(
                realm=owner.realm,
                bot_profile=armada_bot,
                requesting_user=owner,
                trigger_message_id=trigger_message_id,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name="",
                status=AgentRun.RUNNING,
            )
            self.assertEqual(
                ArmadaDatabaseWorkerRunStore().active_worker_run_message_id(
                    SimpleNamespace(user_profile=armada_bot),
                    stream=ARMADA_DEFAULT_STREAM,
                    topic="",
                ),
                trigger_message_id,
            )

    def test_get_armada_active_session_for_target_topic(self) -> None:
        user = self.example_user("hamlet")
        target_stream = "frontend"
        target_topic = "checkout/regression"
        self.subscribe(user, target_stream)
        self.subscribe(user, ARMADA_DEFAULT_STREAM)
        request_message_id = self.send_stream_message(
            user,
            target_stream,
            "@Onix please debug the checkout regression.",
            topic_name=target_topic,
        )
        self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "@Onix this default session should not be counted.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
            {"stream": target_stream, "topic": target_topic},
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(result_dict["armada_session"]["stream"], target_stream)
        self.assertEqual(result_dict["armada_session"]["topic"], target_topic)
        self.assertEqual(active_session["stream"], target_stream)
        self.assertEqual(active_session["topic"], target_topic)
        self.assertEqual(active_session["state"], "requested")
        self.assertEqual(active_session["request_count"], 1)
        self.assertEqual(active_session["latest_request_message_id"], request_message_id)
        self.assertEqual(active_session["worker_state"], "ready")
        self.assertTrue(active_session["worker_can_run"])

    def test_get_armada_active_session_from_topic_messages(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, ARMADA_DEFAULT_STREAM)
        self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "@**Onix** fix checkout latency.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "- [ ] Confirm repo, branch, and target runtime.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "Fold in the terminal status popup too.",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        latest_request_message_id = self.send_stream_message(
            user,
            ARMADA_DEFAULT_STREAM,
            "@**Onix** Pause session:\n\nReason: waiting for CI",
            topic_name=ARMADA_DEFAULT_TOPIC,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{ARMADA_ACTIVE_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(active_session["state"], "requested")
        self.assertEqual(active_session["source"], "server")
        self.assertEqual(active_session["stream"], ARMADA_DEFAULT_STREAM)
        self.assertEqual(active_session["topic"], ARMADA_DEFAULT_TOPIC)
        self.assertEqual(active_session["topic_message_count"], 4)
        self.assertEqual(active_session["request_count"], 2)
        self.assertEqual(active_session["latest_request_message_id"], latest_request_message_id)
        self.assertIsNone(active_session["latest_message_id"])
        self.assertFalse(active_session["is_paused"])
        self.assertEqual(active_session["worker_state"], "ready")
        self.assertTrue(active_session["worker_can_run"])


def codex_debug_models_output(slugs: list[str]) -> str:
    models = [
        {"slug": slug, "visibility": "list", "priority": priority}
        for priority, slug in enumerate(slugs)
    ]
    return orjson.dumps({"models": models}).decode()


class HeldCodexDebugModels:
    """Stands in for subprocess.run running `codex debug models`: it notes
    the thread that ran it, and answers only once released."""

    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.release = threading.Event()
        self.callers: list[str] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.callers.append(threading.current_thread().name)
        self.release.wait(timeout=10)
        if isinstance(self.answer, Exception):
            raise self.answer
        return subprocess.CompletedProcess(command, 0, stdout=self.answer)


def new_model_catalog(
    value: list[dict[str, object]] | None = None, checked_at: float | None = None
) -> ArmadaBackgroundRead[list[dict[str, object]]]:
    """A fresh copy of the server's model catalog, as a process starts with."""
    return dataclasses.replace(
        armada_model_catalog,
        value=value or [],
        checked_at=checked_at,
        refresh=None,
        failing=False,
        lock=threading.Lock(),
    )


class ArmadaModelCatalogTest(ZulipTestCase):
    @contextmanager
    def codex_on_path(
        self, cache: ArmadaBackgroundRead[list[dict[str, object]]], codex: HeldCodexDebugModels
    ) -> Iterator[None]:
        self.addCleanup(codex.release.set)
        with (
            patch("zerver.views.armada.armada_model_catalog", cache),
            patch("zerver.views.armada.which", return_value="/usr/local/bin/codex"),
            patch("zerver.views.armada.subprocess.run", side_effect=codex),
            self.settings(
                ARMADA_WORKER_MODEL="gpt-6-astra",
                ARMADA_WORKER_MODEL_CHOICES=["gpt-6-astra", "gpt-6-sol"],
            ),
        ):
            yield

    def served_models(self) -> list[str]:
        return cast(list[str], build_armada_runtime_config_payload()["model_choices"])

    def finish_refresh(
        self, cache: ArmadaBackgroundRead[list[dict[str, object]]], codex: HeldCodexDebugModels
    ) -> None:
        refresh = cache.refresh
        assert refresh is not None
        codex.release.set()
        refresh.join(timeout=10)
        self.assertFalse(refresh.is_alive())
        self.assertIsNone(cache.refresh)

    def test_stale_catalog_is_served_while_codex_is_asked_in_the_background(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, ARMADA_DEFAULT_STREAM)
        self.login_user(user)
        cache = new_model_catalog(
            value=extract_armada_codex_model_catalog(
                orjson.loads(codex_debug_models_output(["gpt-old"]))
            ),
            checked_at=time.monotonic() - ARMADA_MODEL_CATALOG_CACHE_SECONDS,
        )
        codex = HeldCodexDebugModels(codex_debug_models_output(["gpt-new"]))

        def served_by_runs_endpoint() -> list[str]:
            result = self.client_get(
                f"/json{ARMADA_RUNS_ENDPOINT}",
                {"stream": ARMADA_DEFAULT_STREAM, "topic": ARMADA_DEFAULT_TOPIC},
                intentionally_undocumented=True,
            )
            return self.assert_json_success(result)["runtime_config"]["model_choices"]

        with self.codex_on_path(cache, codex):
            # Codex answers only once released, after this response.
            self.assertEqual(served_by_runs_endpoint(), ["gpt-old", "gpt-6-astra", "gpt-6-sol"])
            self.finish_refresh(cache, codex)
            self.assertEqual(codex.callers, ["armada-model-catalog"])

            self.assertEqual(served_by_runs_endpoint(), ["gpt-new", "gpt-6-astra", "gpt-6-sol"])
            self.assertIsNone(cache.refresh)
        self.assertEqual(codex.callers, ["armada-model-catalog"])

    def test_cold_start_serves_the_configured_models(self) -> None:
        cache = new_model_catalog()
        codex = HeldCodexDebugModels(
            codex_debug_models_output(["gpt-6-sol", "gpt-6-astra", "gpt-new"])
        )
        with self.codex_on_path(cache, codex):
            self.assertEqual(self.served_models(), ["gpt-6-astra", "gpt-6-sol"])
            self.finish_refresh(cache, codex)
            self.assertEqual(self.served_models(), ["gpt-6-sol", "gpt-6-astra", "gpt-new"])

    def test_only_one_refresh_runs_at_a_time(self) -> None:
        cache = new_model_catalog()
        codex = HeldCodexDebugModels(codex_debug_models_output(["gpt-new"]))
        with self.codex_on_path(cache, codex):
            self.served_models()
            refresh = cache.refresh
            self.assertIsNotNone(refresh)
            # As if this refresh outlasted the five minutes: the requests
            # that find the catalog stale again start no second one.
            cache.checked_at = None
            self.served_models()
            self.served_models()
            self.assertIs(cache.refresh, refresh)
            self.finish_refresh(cache, codex)
        self.assertEqual(codex.callers, ["armada-model-catalog"])

    def test_failed_refresh_keeps_the_catalog_and_logs_once(self) -> None:
        # The refresh thread's body runs here, in the test's own thread,
        # where test-backend's coverage can see it; checked_at keeps the
        # requests below from starting refreshes of their own.
        cache = new_model_catalog(checked_at=time.monotonic())
        codex = HeldCodexDebugModels(codex_debug_models_output(["gpt-new"]))
        codex.release.set()
        warning = "Could not refresh Codex's model catalog; keeping the last one"
        with self.codex_on_path(cache, codex):
            cache.refresh_now()

            failure = subprocess.CalledProcessError(1, ["codex", "debug", "models"])
            codex.answer = failure
            with self.assertLogs("zerver.lib.armada_background_read", level="WARNING") as logs:
                cache.refresh_now()
            self.assertEqual([record.getMessage() for record in logs.records], [warning])
            exc_info = logs.records[0].exc_info
            assert exc_info is not None
            self.assertIs(exc_info[1], failure)
            self.assertEqual(self.served_models(), ["gpt-new", "gpt-6-astra", "gpt-6-sol"])

            # Still failing, now by listing no models: kept, and not logged again.
            codex.answer = codex_debug_models_output([])
            with self.assertNoLogs("zerver.lib.armada_background_read"):
                cache.refresh_now()
            self.assertEqual(self.served_models(), ["gpt-new", "gpt-6-astra", "gpt-6-sol"])

            # A refresh that succeeds ends the run; the next failure is logged.
            codex.answer = codex_debug_models_output(["gpt-newer"])
            cache.refresh_now()
            codex.answer = subprocess.TimeoutExpired(["codex", "debug", "models"], 5)
            with self.assertLogs("zerver.lib.armada_background_read", level="WARNING") as logs:
                cache.refresh_now()
            self.assertEqual([record.getMessage() for record in logs.records], [warning])
            self.assertEqual(self.served_models(), ["gpt-newer", "gpt-6-astra", "gpt-6-sol"])

    def test_no_refresh_without_codex(self) -> None:
        cache = new_model_catalog()
        codex = HeldCodexDebugModels(codex_debug_models_output(["gpt-new"]))
        with (
            self.codex_on_path(cache, codex),
            patch("zerver.views.armada.which", return_value=None),
        ):
            self.assertEqual(self.served_models(), ["gpt-6-astra", "gpt-6-sol"])
            self.assertIsNone(cache.refresh)
            self.assertIsNotNone(cache.checked_at)
        self.assertEqual(codex.callers, [])


class ArmadaWorkspacePathTest(ZulipTestCase):
    def test_workspace_relative_path_guard(self) -> None:
        self.assertIsNone(armada_workspace_relative_path("/w", "../etc/passwd"))
        self.assertIsNone(armada_workspace_relative_path("/w", "/etc/passwd"))
        self.assertIsNone(armada_workspace_relative_path("/w", ".git/config"))
        self.assertIsNone(armada_workspace_relative_path("/w", "a/../.."))
        self.assertEqual(
            str(armada_workspace_relative_path("/w", "src/./main.py")),
            "/w/src/main.py",
        )


class ArmadaImageEmbedTest(ZulipTestCase):
    def test_embed_workspace_images_uploads_and_appends(self) -> None:
        from zerver.lib.armada import armada_embed_workspace_images

        user = self.example_user("hamlet")
        with tempfile.TemporaryDirectory() as temp_dir:
            results = Path(temp_dir) / "results"
            results.mkdir()
            png = results / "training_curve.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            content = "The plot is at `results/training_curve.png` in the worktree."
            embedded = armada_embed_workspace_images(
                content,
                workdir=temp_dir,
                isolation_username=None,
                bot_profile=user,
            )
        self.assertIn("/user_uploads/", embedded)
        self.assertIn("[training_curve.png](", embedded)
        # Original path reference stays; the preview link is appended.
        self.assertIn("`results/training_curve.png`", embedded)

    def test_embed_workspace_images_guards_paths_and_misses(self) -> None:
        from zerver.lib.armada import armada_embed_workspace_images

        user = self.example_user("hamlet")
        with tempfile.TemporaryDirectory() as temp_dir:
            content = "See `../../etc/secret.png` and `missing.png`."
            embedded = armada_embed_workspace_images(
                content,
                workdir=temp_dir,
                isolation_username=None,
                bot_profile=user,
            )
        self.assertEqual(embedded, content)


class ArmadaTaskMarkerNormalizationTest(ZulipTestCase):
    def test_normalize_armada_task_markers(self) -> None:
        from zerver.lib.armada import normalize_armada_task_markers

        content = (
            "Checklist:\n"
            "- [ ] keep it standalone\n"
            "- [x] improve the visuals\n"
            "1. [X] numbered done item\n"
            "```\n"
            "- [ ] not a checklist, code\n"
            "```\n"
            "and inline `- [ ] code` stays.\n"
        )
        normalized = normalize_armada_task_markers(content)
        self.assertIn("- \u2610 keep it standalone", normalized)
        self.assertIn("- \u2611 improve the visuals", normalized)
        self.assertIn("1. \u2611 numbered done item", normalized)
        self.assertIn("- [ ] not a checklist, code", normalized)
        self.assertIn("`- [ ] code`", normalized)
        # Idempotent: a second pass changes nothing.
        self.assertEqual(normalize_armada_task_markers(normalized), normalized)

    def test_normalize_armada_task_markers_leaves_plain_text(self) -> None:
        from zerver.lib.armada import normalize_armada_task_markers

        content = "An array literal [ ] in prose stays untouched.\n"
        self.assertEqual(normalize_armada_task_markers(content), content)


class ArmadaMathNormalizationTest(ZulipTestCase):
    def test_normalize_armada_math_delimiters(self) -> None:
        from zerver.lib.armada import normalize_armada_math_delimiters

        content = (
            "Intro\n"
            "\\[\n"
            "g_i = (u_i, v_i)\n"
            "\\]\n"
            "where \\(u_i\\) is the source.\n"
            "```python\n"
            'x = "\\\\[not math\\\\]"\n'
            "```\n"
            "and `\\\\(inline code\\\\)` stays.\n"
        )
        normalized = normalize_armada_math_delimiters(content)
        # Single-line display math renders as $$...$$ so it survives
        # list nesting; only multi-line bodies become fences.
        self.assertIn("$$g_i = (u_i, v_i)$$", normalized)
        multiline = normalize_armada_math_delimiters("\\[\nx = 1\\\\\ny = 2\n\\]")
        self.assertIn("```math", multiline)
        self.assertIn("$$u_i$$", normalized)
        # Fenced code and inline code spans stay untouched.
        self.assertIn('x = "\\\\[not math\\\\]"', normalized)
        self.assertIn("`\\\\(inline code\\\\)`", normalized)
        # Idempotent, and a no-op for plain content.
        self.assertEqual(normalize_armada_math_delimiters(normalized), normalized)
        self.assertEqual(normalize_armada_math_delimiters("plain"), "plain")


class ArmadaQuestionMessageTest(ZulipTestCase):
    def test_a_question_renders_as_the_beta_reads_it(self) -> None:
        # The message that asks Codex's question, as Zulip renders it. The
        # beta's test reads the same fixture (web/tests/armada/beta_ask.test.cjs):
        # the options become buttons, and the caption, known by its words,
        # says what answers the question while the run waits.
        fixture = orjson.loads(self.fixture_data("armada_question_message.json"))
        rendered = markdown_convert(
            build_armada_question_message(fixture["questions"]),
            message_realm=get_realm("zulip"),
        ).rendered_content
        self.assertEqual(rendered, fixture["rendered"])


class ArmadaWorkspaceMapTest(ZulipTestCase):
    def test_workspace_topics_endpoint_lists_active_worktrees(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)
        stream = self.subscribe(user, "workmap")
        project = Repository.objects.create(realm=user.realm, stream=stream)
        Workspace.objects.create(
            realm=user.realm,
            project=project,
            stream_name=stream.name,
            topic_name="feature-a",
            branch_name="feature-a",
        )
        Workspace.objects.create(
            realm=user.realm,
            project=project,
            stream_name=stream.name,
            topic_name="retired",
            branch_name="retired",
            lifecycle_status=Workspace.ARCHIVED,
        )

        result = self.client_get("/json/armada/workspace-topics", {"stream": stream.name})
        payload = self.assert_json_success(result)["armada_workspace_topics"]
        self.assertEqual(payload, [{"topic": "feature-a", "branch": "feature-a"}])

    def test_workspace_topics_requires_stream_access(self) -> None:
        iago = self.example_user("iago")
        self.make_stream("sekrit", invite_only=True)
        self.subscribe(iago, "sekrit")
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get("/json/armada/workspace-topics", {"stream": "sekrit"})
        self.assert_json_error(result, "Invalid channel name 'sekrit'")

    def test_register_reports_accessible_armada_project_stream_ids(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workmap")
        Repository.objects.create(realm=user.realm, stream=stream)
        private_stream = self.make_stream("sekrit", invite_only=True)
        Repository.objects.create(realm=user.realm, stream=private_stream)

        state = fetch_initial_state_data(user, realm=user.realm)
        self.assertIn(stream.id, state["repository_stream_ids"])
        # Bindings on channels the user cannot see stay invisible.
        self.assertNotIn(private_stream.id, state["repository_stream_ids"])


class ArmadaMonitorTest(ZulipTestCase):
    def create_armada_bot(self, owner: UserProfile) -> UserProfile:
        return self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )

    def create_live_monitor(
        self, owner: UserProfile, armada_bot: UserProfile, **overrides: object
    ) -> AgentMonitor:
        fields: dict[str, object] = dict(
            realm=owner.realm,
            bot_profile=armada_bot,
            creator=owner,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            title="loader",
            ask="training loader",
            status=AgentMonitor.LIVE,
            cadence_seconds=20,
            artifact_spec={
                "version": 1,
                "title": "loader",
                "data": {"loss": 0},
                "blocks": [{"type": "metric", "label": "loss", "value": 0, "bind": "data.loss"}],
            },
            recipe={
                "command": "echo value=0.5",
                "parse": {"mode": "regex", "pattern": r"value=(?P<loss>[\d.]+)"},
                "bindings": {"data.loss": "group:loss"},
            },
            next_due_at=timezone_now(),
        )
        fields.update(overrides)
        return AgentMonitor.objects.create(**fields)

    def test_create_monitor_queues_setup_run(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(ARMADA_WORKER_MODEL="gpt-5.5", ARMADA_TOPIC_WORKDIR_ROOT=temp_dir),
            patch("zerver.views.armada.ArmadaQueueWorkerDispatcher.dispatch") as dispatch_mock,
        ):
            result = self.client_post(
                "/json/armada/monitors",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "request": "training loader",
                },
                intentionally_undocumented=True,
            )
            result_dict = self.assert_json_success(result)
            monitor = AgentMonitor.objects.get(id=result_dict["armada_monitor"]["id"])
            self.assertEqual(monitor.status, AgentMonitor.PROVISIONING)
            self.assertEqual(monitor.ask, "training loader")
            self.assertIsNotNone(monitor.anchor_message_id)
            assert monitor.anchor_message_id is not None

            run = AgentRun.objects.get(
                bot_profile=armada_bot, trigger_message_id=monitor.anchor_message_id
            )
            self.assertEqual(run.session_kind, AgentRun.MONITOR)
            kwargs = dispatch_mock.call_args.kwargs
            self.assertEqual(kwargs["session_kind"], AgentRun.MONITOR)
            self.assertIn("training loader", kwargs["content"])
            self.assertIn(armada_monitor_spec_relative_path(monitor.id), kwargs["content"])

            # Setup runs are DM-anchored and never appear in the topic's
            # runs list: the wall itself is the provisioning surface.
            runs_result = self.client_get(
                f"/json{ARMADA_RUNS_ENDPOINT}",
                {"stream": ARMADA_DEFAULT_STREAM, "topic": ARMADA_DEFAULT_TOPIC},
                intentionally_undocumented=True,
            )
            runs_dict = self.assert_json_success(runs_result)
            self.assertNotIn(run.id, [r["id"] for r in runs_dict["armada_runs"]])

    def test_create_monitor_enforces_topic_limit(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)
        for index in range(ARMADA_MONITOR_MAX_PER_TOPIC):
            self.create_live_monitor(
                owner,
                armada_bot,
                title=f"m{index}",
                topic_name=(ARMADA_DEFAULT_TOPIC.upper() if index == 0 else ARMADA_DEFAULT_TOPIC),
            )
        result = self.client_post(
            "/json/armada/monitors",
            {
                "stream": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "request": "one too many",
            },
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "This topic already has the maximum number of monitors.")

    def test_monitor_list_uses_logical_case_insensitive_topic_scope(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        monitor = self.create_live_monitor(
            owner,
            armada_bot,
            topic_name=ARMADA_DEFAULT_TOPIC.upper(),
        )
        self.login_user(owner)

        result = self.client_get(
            "/json/armada/monitors",
            {"stream": ARMADA_DEFAULT_STREAM, "topic": ARMADA_DEFAULT_TOPIC},
            intentionally_undocumented=True,
        )

        payload = self.assert_json_success(result)
        self.assertEqual([item["id"] for item in payload["armada_monitors"]], [monitor.id])

    def test_monitor_actions_and_delete(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)
        monitor = self.create_live_monitor(owner, armada_bot)

        result = self.client_patch(
            f"/json/armada/monitors/{monitor.id}",
            {"action": "pause"},
            intentionally_undocumented=True,
        )
        self.assertEqual(self.assert_json_success(result)["armada_monitor"]["status"], "paused")

        result = self.client_patch(
            f"/json/armada/monitors/{monitor.id}",
            {"action": "cadence", "cadence_seconds": "2"},
            intentionally_undocumented=True,
        )
        # Cadence clamps to the 5s floor.
        self.assertEqual(self.assert_json_success(result)["armada_monitor"]["cadence_seconds"], 5)

        result = self.client_patch(
            f"/json/armada/monitors/{monitor.id}",
            {"action": "rename", "title": "gpu loss"},
            intentionally_undocumented=True,
        )
        self.assertEqual(self.assert_json_success(result)["armada_monitor"]["title"], "gpu loss")

        result = self.client_patch(
            f"/json/armada/monitors/{monitor.id}",
            {"action": "resume"},
            intentionally_undocumented=True,
        )
        self.assertEqual(self.assert_json_success(result)["armada_monitor"]["status"], "live")

        result = self.client_patch(
            f"/json/armada/monitors/{monitor.id}",
            {"action": "sabotage"},
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "Unknown monitor action.")

        result = self.client_delete(
            f"/json/armada/monitors/{monitor.id}",
            intentionally_undocumented=True,
        )
        self.assert_json_success(result)
        self.assertFalse(AgentMonitor.objects.filter(id=monitor.id).exists())

    def test_monitor_list_requires_stream_access(self) -> None:
        owner = self.example_user("hamlet")
        outsider = self.example_user("cordelia")
        armada_bot = self.create_armada_bot(owner)
        self.make_stream("armada-private", invite_only=True)
        self.subscribe(owner, "armada-private")
        monitor = self.create_live_monitor(owner, armada_bot, stream_name="armada-private")
        self.login_user(outsider)
        result = self.client_get(
            "/json/armada/monitors",
            {"stream": "armada-private", "topic": ARMADA_DEFAULT_TOPIC},
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "Invalid channel name 'armada-private'")
        result = self.client_delete(
            f"/json/armada/monitors/{monitor.id}",
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "Invalid channel name 'armada-private'")
        self.assertTrue(AgentMonitor.objects.filter(id=monitor.id).exists())

    def test_validate_monitor_spec(self) -> None:
        good: ArmadaMonitorSpec = {
            "title": "training loader",
            "cadence_seconds": 15,
            "artifact": {
                "version": 1,
                "data": {"epoch": 0, "loss": 0},
                "blocks": [
                    {"type": "meter", "label": "epoch", "value": 0, "bind": "data.epoch"},
                    {"type": "metric", "label": "loss", "value": 0, "bind": "data.loss"},
                ],
            },
            "recipe": {
                "command": "tail -n 5 train.log",
                "parse": {"mode": "regex", "pattern": r"loss=(?P<loss>[\d.]+)"},
                "bindings": {"data.epoch": "literal:1", "data.loss": "group:loss"},
            },
        }
        spec = armada_validate_monitor_spec(good)
        self.assertEqual(spec["cadence_seconds"], 15)
        self.assertEqual(spec["title"], "training loader")
        self.assertEqual(len(spec["artifact_spec"]["blocks"]), 2)

        explainer = armada_validate_monitor_spec(
            {
                "title": "pipeline",
                "artifact": {
                    "version": 1,
                    "title": "pipeline",
                    "blocks": [{"type": "copy", "heading": "Pipeline", "body": "Live"}],
                    "actions": [{"label": "Explain", "prompt": "Explain this pipeline"}],
                },
                "recipe": {"parse": {"mode": "static"}},
            }
        )
        self.assertEqual(explainer["recipe"]["parse"]["mode"], "static")
        self.assertEqual(explainer["artifact_spec"]["actions"][0]["label"], "Explain")

        with self.assertRaisesRegex(ArmadaMonitorSpecError, "Invalid artifact"):
            armada_validate_monitor_spec(
                {
                    **good,
                    "artifact": {"version": 1, "blocks": [{"type": "hologram"}]},
                }
            )
        with self.assertRaisesRegex(ArmadaMonitorSpecError, "Invalid artifact"):
            armada_validate_monitor_spec(
                {
                    **good,
                    "artifact": {
                        "version": 1,
                        "blocks": [{"type": "metric", "label": "x", "value": 1, "style": {}}],
                    },
                }
            )
        with self.assertRaisesRegex(ArmadaMonitorSpecError, "Bad binding source"):
            armada_validate_monitor_spec(
                {
                    **good,
                    "recipe": {
                        **good["recipe"],
                        "bindings": {"data.loss": "exec:rm -rf /"},
                    },
                }
            )
        with self.assertRaisesRegex(ArmadaMonitorSpecError, "Invalid regex"):
            armada_validate_monitor_spec(
                {
                    **good,
                    "recipe": {**good["recipe"], "parse": {"mode": "regex", "pattern": "("}},
                }
            )
        with self.assertRaisesRegex(ArmadaMonitorSpecError, "Invalid artifact"):
            armada_validate_monitor_spec(
                {
                    "artifact": {"version": 1, "blocks": [{"type": "svg", "markup": "x"}]},
                }
            )
        with self.assertRaisesRegex(ArmadaMonitorSpecError, "Invalid artifact"):
            armada_validate_monitor_spec(
                {
                    "artifact": {
                        "version": 1,
                        "blocks": [{"type": "copy", "body": "hello"}],
                        "actions": [{"label": "broken"}],
                    }
                }
            )

    def test_monitor_bind_data_modes(self) -> None:
        regex_recipe = {
            "parse": {"mode": "regex", "pattern": r"loss=(?P<loss>[\d.]+) step=(?P<step>\d+)"},
            "bindings": {"data.loss": "group:loss", "data.step": "group:step"},
        }
        bound = armada_monitor_bind_data(regex_recipe, "loss=0.91 step=10\nloss=0.42 step=57\n")
        # The LAST match wins: monitors track the newest line.
        self.assertEqual(bound["loss"], 0.42)
        self.assertEqual(bound["step"], 57)

        json_recipe = {
            "parse": {"mode": "json"},
            "bindings": {
                "data.remaining": "path:credits.remaining",
                "data.provider": "literal:lambda",
            },
        }
        bound = armada_monitor_bind_data(json_recipe, '{"credits": {"remaining": 41.5}}')
        self.assertEqual(bound["remaining"], 41.5)
        self.assertEqual(bound["provider"], "lambda")

        text_recipe = {
            "parse": {"mode": "text"},
            "bindings": {"data.lines": "lines:2", "data.raw": "text"},
        }
        bound = armada_monitor_bind_data(text_recipe, "one\ntwo\nthree")
        self.assertEqual(bound["lines"], ["two", "three"])
        self.assertEqual(bound["raw"], "one\ntwo\nthree")

    def test_monitor_tick_success_and_failure(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(owner)
        monitor = self.create_live_monitor(owner, armada_bot)
        process_isolation = ArmadaProcessIsolation(
            username="armada-test",
            home="/tmp/armada-test",  # noqa: S108
            codex_home="/tmp/armada-test/.codex",  # noqa: S108
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "zerver.lib.armada_monitor.armada_monitor_run_recipe_command",
                return_value="value=0.5",
            ):
                armada_monitor_apply_tick(
                    monitor,
                    workdir=temp_dir,
                    process_isolation=process_isolation,
                )
            monitor.refresh_from_db()
            self.assertEqual(monitor.version, 1)
            self.assertEqual(monitor.last_sample["data"]["loss"], 0.5)
            self.assertEqual(monitor.consecutive_failures, 0)
            self.assertIsNotNone(monitor.next_due_at)

            monitor.recipe = {
                "command": "false",
                "parse": {"mode": "text"},
                "bindings": {},
            }
            monitor.save(update_fields=["recipe"])
            with patch(
                "zerver.lib.armada_monitor.armada_monitor_run_recipe_command",
                side_effect=OSError("recipe exited 1: failed"),
            ):
                for _ in range(ARMADA_MONITOR_FAILURE_LIMIT):
                    armada_monitor_apply_tick(
                        monitor,
                        workdir=temp_dir,
                        process_isolation=process_isolation,
                    )
                    monitor.refresh_from_db()
            self.assertEqual(monitor.status, AgentMonitor.ERROR)
            self.assertIn("recipe exited", monitor.last_error)
            # The sample from the good ticks is preserved on error.
            self.assertEqual(monitor.version, 1)

    def test_executable_monitor_recipe_requires_process_isolation(self) -> None:
        with self.assertRaisesRegex(OSError, "require the topic's isolated OS user"):
            armada_monitor_run_recipe_command(
                "echo 1",
                workdir="/tmp",  # noqa: S108
                process_isolation=None,
            )
        isolation = ArmadaProcessIsolation(
            username="armada-test",
            home="/home/armada-test",
            codex_home="/home/armada-test/.codex",
        )
        with patch("zerver.lib.armada_monitor.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="1\n", stderr="")
            self.assertEqual(
                armada_monitor_run_recipe_command(
                    "echo 1",
                    workdir="/tmp/topic",  # noqa: S108
                    process_isolation=isolation,
                ),
                "1\n",
            )
        self.assertEqual(
            run.call_args.args[0][:6], ["sudo", "-n", "-H", "-u", "armada-test", "env"]
        )

    def test_apply_monitor_setup_result(self) -> None:
        owner = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(owner)
        monitor = self.create_live_monitor(
            owner,
            armada_bot,
            status=AgentMonitor.PROVISIONING,
            anchor_message_id=987654,
            recipe={},
            artifact_spec={},
        )
        spec = {
            "title": "gpu credits",
            "cadence_seconds": 30,
            "artifact": {
                "version": 1,
                "data": {"credits": 0},
                "blocks": [
                    {"type": "metric", "label": "credits", "value": 0, "bind": "data.credits"}
                ],
            },
            "recipe": {
                "command": "echo 41",
                "parse": {"mode": "text"},
                "bindings": {"data.credits": "text"},
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            spec_path = Path(temp_dir) / armada_monitor_spec_relative_path(monitor.id)
            spec_path.parent.mkdir(parents=True)
            spec_path.write_text(orjson.dumps(spec).decode())
            armada_apply_monitor_setup_result(
                armada_bot, 987654, workdir=temp_dir, isolation_username=None
            )
            monitor.refresh_from_db()
            self.assertEqual(monitor.status, AgentMonitor.LIVE)
            self.assertEqual(monitor.title, "gpu credits")
            self.assertEqual(monitor.cadence_seconds, 30)
            self.assertEqual(monitor.recipe["command"], "echo 41")
            self.assertIsNotNone(monitor.next_due_at)

        # A run whose agent never wrote the file settles to error.
        monitor.status = AgentMonitor.PROVISIONING
        monitor.save(update_fields=["status"])
        with tempfile.TemporaryDirectory() as temp_dir:
            armada_apply_monitor_setup_result(
                armada_bot, 987654, workdir=temp_dir, isolation_username=None
            )
        monitor.refresh_from_db()
        self.assertEqual(monitor.status, AgentMonitor.ERROR)
        self.assertIn("did not write the spec file", monitor.last_error)

    def test_monitor_dispatch_routes_to_monitor_queue(self) -> None:
        owner = self.example_user("hamlet")
        self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(ARMADA_WORKER_MODEL="gpt-5.5", ARMADA_TOPIC_WORKDIR_ROOT=temp_dir),
            patch("zerver.lib.armada_worker.queue_json_publish_rollback_unsafe") as publish_mock,
        ):
            result = self.client_post(
                "/json/armada/monitors",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "request": "training loader",
                },
                intentionally_undocumented=True,
            )
            self.assert_json_success(result)
            queue_name, event = publish_mock.call_args.args
            # Monitor turns ride their own consumer so a provisioning
            # wall block never queues ahead of a chat run.
            self.assertEqual(queue_name, "armada_monitor_worker")
            self.assertEqual(event["session_kind"], AgentRun.MONITOR)

    def test_spec_direct_monitor_creation_pins_without_agent_run(self) -> None:
        owner = self.example_user("hamlet")
        self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)
        spec = {
            "title": "gpu credits",
            "cadence_seconds": 60,
            "artifact": {
                "version": 1,
                "title": "gpu credits",
                "data": {"credits": 41},
                "blocks": [{"type": "metric", "label": "credits", "value": 41}],
            },
            "recipe": {"command": "", "parse": {"mode": "static"}, "bindings": {}},
        }
        runs_before = AgentRun.objects.count()
        messages_before = Message.objects.count()
        with patch("zerver.views.armada.ArmadaQueueWorkerDispatcher.dispatch") as dispatch_mock:
            result = self.client_post(
                "/json/armada/monitors",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "request": "pinned from chat",
                    "spec": orjson.dumps(spec).decode(),
                },
                intentionally_undocumented=True,
            )
        result_dict = self.assert_json_success(result)
        monitor = AgentMonitor.objects.get(id=result_dict["armada_monitor"]["id"])
        # Pinning is instant: live monitor, no setup run, no DM anchor.
        self.assertEqual(monitor.status, AgentMonitor.LIVE)
        self.assertEqual(monitor.title, "gpu credits")
        self.assertIsNotNone(monitor.next_due_at)
        self.assertIsNone(monitor.anchor_message_id)
        dispatch_mock.assert_not_called()
        self.assertEqual(AgentRun.objects.count(), runs_before)
        self.assertEqual(Message.objects.count(), messages_before)

    def test_spec_direct_monitor_creation_rejects_bad_specs(self) -> None:
        owner = self.example_user("hamlet")
        self.create_armada_bot(owner)
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.login_user(owner)
        bad_spec = {
            "title": "nope",
            "artifact": {"version": 1, "blocks": [{"type": "hologram"}]},
            "recipe": {"command": "", "parse": {"mode": "static"}, "bindings": {}},
        }
        result = self.client_post(
            "/json/armada/monitors",
            {
                "stream": ARMADA_DEFAULT_STREAM,
                "topic": ARMADA_DEFAULT_TOPIC,
                "request": "pinned from chat",
                "spec": orjson.dumps(bad_spec).decode(),
            },
            intentionally_undocumented=True,
        )
        self.assert_json_error_contains(result, "Invalid artifact spec")
        self.assertFalse(AgentMonitor.objects.exists())


class ArmadaTrustedUsersTest(ZulipTestCase):
    """With ARMADA_WORKER_TRUSTED_USERS_ONLY (the production template's
    default), only administrators and ARMADA_WORKER_TRUSTED_USER_IDS may
    drive the agent or give a channel a repository URL."""

    def create_armada_bot(self, owner: UserProfile) -> UserProfile:
        return self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )

    def test_run_start_requires_trusted_user(self) -> None:
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        armada_bot = self.create_armada_bot(hamlet)
        store = ArmadaDatabaseWorkerRunStore()
        bot_handler = SimpleNamespace(user_profile=armada_bot)

        def start_reason(user: UserProfile) -> str | None:
            return store.start_blocked_reason(
                bot_handler,
                950,
                stream=ARMADA_DEFAULT_STREAM,
                topic="trusted-users",
                requesting_user_id=user.id,
            )

        def side_start_reason(user: UserProfile) -> str | None:
            return store.side_start_blocked_reason(
                bot_handler,
                951,
                stream=ARMADA_DEFAULT_STREAM,
                topic="trusted-users",
                requesting_user_id=user.id,
            )

        refused = "This user is not allowed to start Onix's runs."
        self.assertIsNone(start_reason(hamlet))
        with self.settings(ARMADA_WORKER_TRUSTED_USERS_ONLY=True):
            self.assertEqual(start_reason(hamlet), refused)
            self.assertEqual(side_start_reason(hamlet), refused)
            self.assertIsNone(start_reason(iago))
        with self.settings(
            ARMADA_WORKER_TRUSTED_USERS_ONLY=True, ARMADA_WORKER_TRUSTED_USER_IDS=[hamlet.id]
        ):
            self.assertIsNone(start_reason(hamlet))
        with self.settings(ARMADA_WORKER_TRUSTED_USER_IDS=[iago.id]):
            self.assertEqual(start_reason(hamlet), refused)

    def test_steering_requires_trusted_user(self) -> None:
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        armada_bot = self.create_armada_bot(hamlet)
        loaded_session = build_armada_active_session_summary_from_messages(
            armada_session=get_armada_session_payload(),
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Onix fix the checkout test", False),
                (11, ARMADA_WORKING_MESSAGE, True),
            ],
        )
        sent_replies: list[str] = []
        steering_events: list[str] = []

        class FakeRunStore(ArmadaDatabaseWorkerRunStore):
            @override
            def active_worker_run_message_id(
                self,
                bot_handler: Any,
                *,
                stream: str,
                topic: str,
                requesting_user_id: int | None = None,
            ) -> int | None:
                return 101

            @override
            def record_event(
                self,
                bot_handler: Any,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, Any] | None = None,
            ) -> None:
                steering_events.append(event_type)

            @override
            def update_metadata(self, bot_handler: Any, message_id: int, **metadata: Any) -> None:
                pass

        class FakeBotHandler:
            user_profile = armada_bot

            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        def steer_as(sender_id: int | None) -> None:
            ArmadaEmbeddedBotHandler(
                active_session_loader=lambda message, bot_handler: loaded_session,
                worker_run_store=FakeRunStore(),
                worker_dispatcher=ArmadaQueueWorkerDispatcher(),
            ).handle_message(
                {
                    "id": 206,
                    "type": "stream",
                    "display_recipient": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "sender_id": sender_id,
                    "content": "@Onix make it the 30th",
                },
                FakeBotHandler(),
            )

        refused = "This user is not allowed to steer Onix's runs."
        with self.settings(ARMADA_WORKER_TRUSTED_USERS_ONLY=True):
            steer_as(hamlet.id)
            steer_as(None)
            steer_as(self.mit_user("starnine").id)
            self.assertEqual(steering_events, [])
            self.assert_length(sent_replies, 3)
            for reply in sent_replies:
                self.assertIn(refused, reply)

            steer_as(iago.id)
            self.assertEqual(steering_events, ["steering"])
            self.assert_length(sent_replies, 3)

        steer_as(hamlet.id)
        self.assertEqual(steering_events, ["steering", "steering"])

    def test_repository_url_requires_trusted_user(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        with self.settings(ARMADA_WORKER_TRUSTED_USERS_ONLY=True):
            result = self.client_post(
                "/json/users/me/subscriptions",
                {
                    "subscriptions": orjson.dumps([{"name": "untrusted-repository"}]).decode(),
                    "repository_url": "https://github.com/pallets/click.git",
                },
            )
        self.assert_json_error(result, "You are not allowed to give a channel a Git repository.")

    def test_monitor_setup_run_requires_trusted_user(self) -> None:
        hamlet = self.example_user("hamlet")
        armada_bot = self.create_armada_bot(hamlet)
        self.subscribe(hamlet, ARMADA_DEFAULT_STREAM)
        self.subscribe(armada_bot, ARMADA_DEFAULT_STREAM)
        self.login_user(hamlet)
        monitor = AgentMonitor.objects.create(
            realm=hamlet.realm,
            bot_profile=armada_bot,
            creator=hamlet,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            title="loader",
            ask="training loader",
            status=AgentMonitor.ERROR,
        )

        with (
            self.settings(ARMADA_WORKER_TRUSTED_USERS_ONLY=True),
            patch("zerver.views.armada.ArmadaQueueWorkerDispatcher.dispatch") as dispatch_mock,
        ):
            result = self.client_post(
                "/json/armada/monitors",
                {
                    "stream": ARMADA_DEFAULT_STREAM,
                    "topic": ARMADA_DEFAULT_TOPIC,
                    "request": "training loader",
                },
                intentionally_undocumented=True,
            )
            self.assert_json_error(result, "You are not allowed to start agent runs.")
            result = self.client_patch(
                f"/json/armada/monitors/{monitor.id}",
                {"action": "rebuild"},
                intentionally_undocumented=True,
            )
            self.assert_json_error(result, "You are not allowed to start agent runs.")

        dispatch_mock.assert_not_called()
        self.assertEqual(AgentMonitor.objects.count(), 1)
        self.assertFalse(AgentRun.objects.filter(session_kind=AgentRun.MONITOR).exists())
