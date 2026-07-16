import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import orjson
from django.core.cache import cache
from django.utils.timezone import now as timezone_now

from zerver.actions.pear import send_pear_run_event
from zerver.lib.event_schema import check_pear_run_event
from zerver.lib.events import fetch_initial_state_data
from zerver.lib.pear import (
    PEAR_ACTIVE_SESSION_ENDPOINT,
    PEAR_AGENT_RUNTIME_PACKAGES,
    PEAR_CODEX_MCP_TOOLS,
    PEAR_CONFIG_ENDPOINT,
    PEAR_DEFAULT_STREAM,
    PEAR_DEFAULT_TOPIC,
    PEAR_EMBEDDED_BOT_SERVICE,
    PEAR_FIRST_RUN_ENDPOINT,
    PEAR_FIRST_RUN_STARTER_MESSAGE,
    PEAR_FIRST_RUN_STREAM,
    PEAR_FIRST_RUN_TOPIC,
    PEAR_OFFICIAL_AGENT_RUNTIME,
    PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
    PEAR_OFFICIAL_CODEX_SDK_PACKAGES,
    PEAR_RUNS_ENDPOINT,
    PEAR_SESSION_ENDPOINT,
    PEAR_SIDE_ENDPOINT,
    PEAR_TOPIC_CONTEXT_ENDPOINT,
    build_pear_active_session_summary_from_messages,
    get_pear_session_payload,
    write_pear_live_run_state,
)
from zerver.lib.pear_agent import PearProcessIsolation
from zerver.lib.pear_gate import (
    get_pear_followup_service_bot_event,
    get_selected_pear_bot_user_id_for_realm,
    invalidate_pear_selected_bot_cache,
    pear_selected_bot_cache_key,
)
from zerver.lib.pear_monitor import (
    PEAR_MONITOR_FAILURE_LIMIT,
    PEAR_MONITOR_MAX_PER_TOPIC,
    PearMonitorSpecError,
    pear_apply_monitor_setup_result,
    pear_monitor_apply_tick,
    pear_monitor_bind_data,
    pear_monitor_run_recipe_command,
    pear_monitor_spec_relative_path,
    pear_validate_monitor_spec,
)
from zerver.lib.pear_worker import (
    PearDatabaseWorkerRunStore,
    build_pear_worker_runtime_from_settings,
    build_pear_workspace_context,
    clone_or_update_pear_repository_workdir,
    configure_pear_channel_repository_workspace,
    ensure_pear_topic_workdir,
    ensure_pear_workspace_node,
    invalidate_pear_git_status_delta,
    load_pear_active_session_from_zulip,
    maybe_refactor_pear_stream_workspaces_on_rename,
    maybe_refactor_pear_workspace_on_move,
    pear_expected_topic_workdir,
    pear_git_current_branch_name,
    pear_git_output,
    pear_git_status_delta,
    pear_read_text_path,
    pear_topic_branch_name,
    pear_topic_isolation_user_exists,
    pear_topic_isolation_username,
    pear_topic_process_isolation,
    pear_topic_workdir_root,
    pear_worker_model_for_topic,
    pear_worker_process_isolation_error,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_runtime_isolation_error,
    pear_worker_sandbox_for_topic,
    pear_worker_workdir_for_topic,
    pear_workspace_node_default_branch,
    pear_workspace_relative_path,
    reap_stale_pear_worker_runs,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, Recipient, Subscription, UserProfile
from zerver.models.bots import (
    PearMonitor,
    PearProject,
    PearWorkerRun,
    PearWorkerRunEvent,
    PearWorkspaceNode,
)
from zerver.models.streams import get_stream
from zerver.views.pear import (
    build_pear_git_context,
    extract_pear_codex_model_catalog,
    pear_worker_run_summary_events,
    pear_workspace_isolation_username,
    serialize_pear_worker_run,
    serialize_pear_workspace_context,
)


class PearSessionEndpointTest(ZulipTestCase):
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
                "user.email=pear@example.com",
                "-c",
                "user.name=Pear",
                "commit",
                "--quiet",
                "-m",
                "initial",
            ],
            check=True,
            timeout=10,
        )

    def test_pear_run_push_event_is_typed_and_visibility_scoped(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        pear_bot = self.create_test_bot(
            "pear-push",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        # This test exercises the typed browser event contract directly.  Do
        # not also wake the embedded bot worker when creating its fixture
        # message; that is a separate integration boundary with its own tests.
        with patch("zerver.worker.embedded_bots.queue_json_publish_rollback_unsafe"):
            trigger_message_id = self.send_stream_message(
                owner,
                PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                content="@**Pear** check the repository",
            )
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=trigger_message_id,
            result_message_id=trigger_message_id,
            status=PearWorkerRun.RUNNING,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name=PEAR_DEFAULT_TOPIC,
            current_activity="Running tests",
            tool_count=2,
        )
        run_event = PearWorkerRunEvent.objects.create(
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

        with patch("zerver.actions.pear.send_event_on_commit") as send_event:
            send_pear_run_event(run, run_event)

        send_event.assert_called_once()
        event = send_event.call_args.args[1]
        check_pear_run_event("event", event)
        self.assertEqual(event["run_id"], run.id)
        self.assertEqual(event["sequence"], run_event.id)
        self.assertEqual(event["current_activity"], "Running tests")
        self.assertEqual(event["plan"][1], {"step": "Verify", "status": "in_progress"})
        self.assertIn(owner.id, send_event.call_args.args[2])

    def test_get_pear_session(self) -> None:
        self.login("hamlet")

        result = self.client_get(
            f"/json{PEAR_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        pear_session = result_dict["pear_session"]
        self.assertEqual(pear_session["agent_name"], "Pear")
        self.assertEqual(pear_session["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(pear_session["topic"], PEAR_DEFAULT_TOPIC)
        self.assertIn("@**Pear**", pear_session["seed_message"])
        self.assertIn("Fix bug:", pear_session["fix_bug_message"])
        self.assertIn("Status check:", pear_session["status_check_message"])
        self.assertIn("Handoff summary:", pear_session["handoff_message"])
        self.assertIn("Catch up:", pear_session["catch_up_message"])
        self.assertIn("Triage request:", pear_session["triage_message"])
        self.assertIn("Find answer:", pear_session["find_answer_message"])
        self.assertIn("Review changes:", pear_session["review_message"])
        self.assertIn("Implementation plan:", pear_session["plan_message"])
        self.assertIn("Checkpoint:", pear_session["checkpoint_message"])
        self.assertIn("Apply plan:", pear_session["apply_plan_message"])
        self.assertIn("Save work:", pear_session["save_work_message"])
        self.assertIn("Verify changes:", pear_session["verify_changes_message"])
        self.assertIn("Draft artifact:", pear_session["draft_artifact_message"])
        self.assertEqual(
            pear_session["official_agent_runtime"],
            PEAR_OFFICIAL_AGENT_RUNTIME,
        )
        self.assertEqual(
            pear_session["official_agent_runtime_label"],
            PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
        )
        self.assertEqual(
            pear_session["agent_runtime_packages"],
            list(PEAR_AGENT_RUNTIME_PACKAGES),
        )
        self.assertEqual(
            pear_session["official_codex_sdk_packages"],
            list(PEAR_OFFICIAL_CODEX_SDK_PACKAGES),
        )
        self.assertEqual(pear_session["codex_mcp_tools"], list(PEAR_CODEX_MCP_TOOLS))

    def test_api_v1_pear_session(self) -> None:
        user = self.example_user("hamlet")

        result = self.client_get(
            f"/api/v1{PEAR_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
            HTTP_AUTHORIZATION=self.encode_user(user),
        )

        result_dict = self.assert_json_success(result)
        self.assertEqual(result_dict["pear_session"]["agent_name"], "Pear")

    def test_get_pear_config_without_bot(self) -> None:
        self.login("hamlet")

        result = self.client_get(
            f"/json{PEAR_CONFIG_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        pear_config = result_dict["pear_config"]
        self.assertEqual(pear_config["bot_short_name"], "pear")
        self.assertEqual(pear_config["embedded_bot_service"], PEAR_EMBEDDED_BOT_SERVICE)
        self.assertFalse(pear_config["configured"])
        self.assertEqual(pear_config["worker_agent_runtime"], "disabled")
        self.assertFalse(pear_config["worker_runtime_ready"])
        self.assertFalse(pear_config["worker_runtime_auth_ready"])
        self.assertIsNone(pear_config["worker_runtime_login_method"])
        self.assertIsNone(pear_config["worker_runtime_login_command"])
        self.assertIsNone(pear_config["worker_runtime_login_target"])
        self.assertIsNone(pear_config["worker_runtime_login_instructions"])
        self.assertEqual(pear_config["bot_count"], 0)
        self.assertIsNone(pear_config["bot_user_id"])
        self.assertIsNone(pear_config["selected_bot_user_id"])
        self.assertEqual(pear_config["bot_identities"], [])
        self.assertEqual(
            [step["key"] for step in pear_config["setup_steps"]],
            ["embedded_bots", "pear_bot", "worker_runtime", "worker_auth"],
        )

    def test_get_pear_config_with_bot(self) -> None:
        owner = self.example_user("hamlet")
        with (
            self.settings(
                EMBEDDED_BOTS_ENABLED=True,
                PEAR_WORKER_AGENT_RUNTIME="codex-app-server",
            ),
            patch(
                "zerver.views.pear.pear_worker_runtime_dependency_status",
                return_value=(
                    True,
                    "codex app-server via /usr/local/bin/codex.",
                ),
            ),
            patch(
                "zerver.views.pear.pear_worker_runtime_auth_status",
                return_value=(
                    True,
                    "Codex CLI is logged in.",
                    "codex-cli",
                    "codex login",
                    "worker-environment",
                    "Run Codex CLI login inside the worker environment used by Pear.",
                ),
            ),
        ):
            pear_bot = self.create_test_bot(
                "pear",
                owner,
                full_name="Pear",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=PEAR_EMBEDDED_BOT_SERVICE,
            )
            self.login_user(owner)

            result = self.client_get(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        pear_config = result_dict["pear_config"]
        self.assertTrue(pear_config["embedded_bots_enabled"])
        self.assertTrue(pear_config["configured"])
        self.assertTrue(pear_config["enabled"])
        self.assertEqual(pear_config["bot_count"], 1)
        self.assertEqual(pear_config["bot_user_id"], pear_bot.id)
        self.assertEqual(pear_config["selected_bot_user_id"], pear_bot.id)
        self.assertEqual(pear_config["selected_bot_full_name"], "Pear")
        self.assertEqual(pear_config["bot_full_name"], "Pear")
        self.assertEqual(pear_config["worker_agent_runtime"], "codex-app-server")
        self.assertTrue(pear_config["worker_runtime_ready"])
        self.assertTrue(pear_config["worker_runtime_auth_ready"])
        self.assertEqual(pear_config["worker_runtime_login_method"], "codex-cli")
        self.assertEqual(pear_config["worker_runtime_login_command"], "codex login")
        self.assertEqual(pear_config["worker_runtime_login_target"], "worker-environment")
        self.assertIn("worker environment", pear_config["worker_runtime_login_instructions"])
        self.assertEqual(
            pear_config["setup_steps"][2]["detail"],
            "codex app-server via /usr/local/bin/codex.",
        )
        self.assertEqual(
            pear_config["setup_steps"][3]["detail"],
            "Codex CLI is logged in.",
        )
        self.assertEqual(
            pear_config["bot_identities"],
            [
                {
                    "bot_user_id": pear_bot.id,
                    "bot_full_name": "Pear",
                    "bot_email": pear_bot.email,
                    "selected": True,
                }
            ],
        )

    def test_get_pear_config_reports_missing_runtime_dependency(self) -> None:
        owner = self.example_user("hamlet")
        with (
            self.settings(
                EMBEDDED_BOTS_ENABLED=True,
                PEAR_WORKER_AGENT_RUNTIME="codex-app-server",
            ),
            patch(
                "zerver.views.pear.pear_worker_runtime_dependency_status",
                return_value=(False, "Missing codex executable."),
            ),
            patch(
                "zerver.views.pear.pear_worker_runtime_auth_status",
                return_value=(
                    True,
                    "Codex CLI is logged in.",
                    "codex-cli",
                    "codex login",
                    "worker-environment",
                    "Run Codex CLI login inside the worker environment used by Pear.",
                ),
            ),
        ):
            pear_bot = self.create_test_bot(
                "pear",
                owner,
                full_name="Pear",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=PEAR_EMBEDDED_BOT_SERVICE,
            )
            self.login_user(owner)

            result = self.client_get(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        pear_config = result_dict["pear_config"]
        self.assertEqual(pear_config["selected_bot_user_id"], pear_bot.id)
        self.assertEqual(pear_config["worker_agent_runtime"], "codex-app-server")
        self.assertFalse(pear_config["worker_runtime_ready"])
        self.assertEqual(
            pear_config["setup_steps"][2],
            {
                "key": "worker_runtime",
                "label": "Worker runtime dependencies",
                "complete": False,
                "detail": "Missing codex executable.",
            },
        )
        self.assertEqual(
            pear_config["setup_steps"][3],
            {
                "key": "worker_auth",
                "label": "Worker runtime login",
                "complete": True,
                "detail": "Codex CLI is logged in.",
            },
        )

    def test_worker_runtime_dependency_status_checks_official_surfaces(self) -> None:
        from zerver.views.pear import pear_worker_runtime_dependency_status

        with patch("zerver.views.pear.which", return_value="/usr/local/bin/codex"):
            self.assertEqual(
                pear_worker_runtime_dependency_status("codex-app-server"),
                (True, "codex app-server via /usr/local/bin/codex."),
            )

        with patch("zerver.views.pear.which", return_value=None):
            available, detail = pear_worker_runtime_dependency_status("codex-app-server")
            self.assertFalse(available)
            self.assertIn("codex executable", detail)

        available, detail = pear_worker_runtime_dependency_status("codex-agents-mcp")
        self.assertFalse(available)
        self.assertEqual(detail, "Set PEAR_WORKER_AGENT_RUNTIME to codex-app-server.")

    def test_worker_runtime_auth_status_reports_login_paths(self) -> None:
        from zerver.views.pear import pear_worker_runtime_auth_status

        with patch(
            "zerver.views.pear.pear_codex_login_available",
            return_value=True,
        ):
            (
                available,
                detail,
                login_method,
                login_command,
                login_target,
                login_instructions,
            ) = pear_worker_runtime_auth_status("codex-app-server")
            self.assertTrue(available)
            self.assertEqual(detail, "Codex CLI is logged in.")
            self.assertEqual(login_method, "codex-cli")
            self.assertEqual(login_command, "codex login")
            self.assertEqual(login_target, "worker-environment")
            self.assertIn("worker environment", login_instructions)

            (
                available,
                detail,
                login_method,
                login_command,
                login_target,
                login_instructions,
            ) = pear_worker_runtime_auth_status("codex-agents-mcp")
            self.assertFalse(available)
            self.assertEqual(detail, "Select a supported worker runtime before logging in.")
            self.assertIsNone(login_method)
            self.assertIsNone(login_command)
            self.assertIsNone(login_target)
            self.assertIsNone(login_instructions)

    def test_serialize_pear_worker_run_includes_usage_summary(self) -> None:
        user = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            user,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.addCleanup(invalidate_pear_selected_bot_cache, user.realm_id)
        run = PearWorkerRun.objects.create(
            realm=user.realm,
            bot_profile=pear_bot,
            trigger_message_id=1,
            requesting_user=user,
            status="running",
            stream_name="devel",
            topic_name="cache test",
            runtime_label="Codex app-server",
            model="gpt-5.5",
            workdir="/workspace",
        )
        PearWorkerRunEvent.objects.create(
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
        write_pear_live_run_state(
            run.trigger_message_id,
            status="running",
        )

        serialized_run = serialize_pear_worker_run(run)
        usage = serialized_run["usage"]

        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["cached_input_tokens"], 80)
        self.assertEqual(usage["cache_percent"], 80.0)
        self.assertIn("80/100 cached", usage["summary"])
        live = serialized_run["live"]
        self.assertIsNotNone(live)
        assert live is not None
        self.assertEqual(live["status"], "running")
        self.assertIsInstance(live["updated_at"], float)
        # No tool_results events yet.
        self.assertEqual(serialized_run["tool_count"], 0)

        # The run's final events are usage-less transcript markers;
        # the serializer must scan back to the usage-bearing event
        # rather than trust the single latest one (a live regression:
        # every run reported usage=None once "turn completed" landed).
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Codex turn completed.",
            payload={"kind": "status", "title": "Codex turn completed.", "status": "done"},
        )
        usage_after_completion = serialize_pear_worker_run(run)["usage"]
        self.assertIsNotNone(usage_after_completion)
        assert usage_after_completion is not None
        self.assertEqual(usage_after_completion["total_tokens"], 106)

        # The run footer sums tool_count across the per-segment
        # tool_results events; malformed payloads are skipped.
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="tool_results",
            summary="Ran 2 tools",
            payload={"tool_count": 2, "tool_results": []},
        )
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="tool_results",
            summary="Ran 3 tools",
            payload={"tool_count": 3, "tool_results": []},
        )
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="tool_results",
            summary="Malformed",
            payload={"tool_count": "nope"},
        )
        self.assertEqual(serialize_pear_worker_run(run)["tool_count"], 5)

        # The rail's plan card takes the newest plan_update; the scan
        # must skip past newer non-plan transcript events.
        PearWorkerRunEvent.objects.create(
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
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Codex turn completed.",
            payload={"kind": "status", "title": "Codex turn completed.", "status": "done"},
        )
        plan = serialize_pear_worker_run(run)["plan"]
        self.assertEqual(
            plan,
            [
                {"step": "Check branch", "status": "completed"},
                {"step": "Commit fix", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ],
        )

    def test_serialize_pear_worker_run_hides_stale_active_live_state(self) -> None:
        user = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            user,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        run = PearWorkerRun.objects.create(
            realm=user.realm,
            bot_profile=pear_bot,
            trigger_message_id=1,
            requesting_user=user,
            status=PearWorkerRun.DONE,
            stream_name="devel",
            topic_name="cache test",
            runtime_label="Codex app-server",
            model="gpt-5.5",
            workdir="/workspace",
        )
        write_pear_live_run_state(
            run.trigger_message_id,
            status=PearWorkerRun.RUNNING,
        )

        serialized_run = serialize_pear_worker_run(run)

        self.assertEqual(serialized_run["status"], PearWorkerRun.DONE)
        self.assertIsNone(serialized_run["live"])

    def test_run_summary_events_are_batched_for_polling(self) -> None:
        user = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            user,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        runs = [
            PearWorkerRun.objects.create(
                realm=user.realm,
                bot_profile=pear_bot,
                trigger_message_id=100 + index,
                requesting_user=user,
                status=PearWorkerRun.RUNNING,
                tool_count=index + 2,
            )
            for index in range(5)
        ]
        for index, run in enumerate(runs):
            PearWorkerRunEvent.objects.create(
                run=run,
                event_type="usage",
                payload={"usage": {"input_tokens": index + 1, "total_tokens": index + 1}},
            )
            PearWorkerRunEvent.objects.create(
                run=run,
                event_type="presentation",
                payload={"overview": f"Visible overview {index}."},
            )

        with self.assert_database_query_count(2):
            events_by_run_id = pear_worker_run_summary_events(runs)
        with self.assert_database_query_count(0):
            serialized = [
                serialize_pear_worker_run(run, summary_events=events_by_run_id[run.id])
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

    def test_get_pear_runs_includes_requested_run_ids_outside_topic_window(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        requested_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=101,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="pinned older run",
            status=PearWorkerRun.DONE,
        )
        topic_runs = [
            PearWorkerRun.objects.create(
                realm=owner.realm,
                bot_profile=pear_bot,
                requesting_user=owner,
                trigger_message_id=200 + index,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                status=PearWorkerRun.DONE,
            )
            for index in range(25)
        ]
        self.login_user(owner)

        result = self.client_get(
            f"/json{PEAR_RUNS_ENDPOINT}",
            {
                "stream": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "run_ids": orjson.dumps([requested_run.id]).decode(),
                "include_context": orjson.dumps(False).decode(),
            },
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        run_ids = [run["id"] for run in result_dict["pear_runs"]]
        self.assertIn(requested_run.id, run_ids)
        self.assertIn(topic_runs[-1].id, run_ids)
        self.assertNotIn(topic_runs[0].id, run_ids)
        self.assertIsNone(result_dict["pear_topic_context"])

    def test_get_pear_runs_defaults_to_cheap_without_topic_context(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with patch("zerver.views.pear.ensure_pear_workspace_node") as ensure_context:
            result = self.client_get(
                f"/json{PEAR_RUNS_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        self.assertEqual(result_dict["pear_runs"], [])
        self.assertIsNone(result_dict["pear_topic_context"])
        ensure_context.assert_not_called()

    def test_get_pear_runs_rejects_inaccessible_requested_run_id(self) -> None:
        owner = self.example_user("hamlet")
        other_user = self.example_user("othello")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        other_side_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=other_user,
            trigger_message_id=301,
            stream_name="direct messages",
            topic_name="side",
            session_kind=PearWorkerRun.SIDE,
            source_stream_name=PEAR_DEFAULT_STREAM,
            source_topic_name=PEAR_DEFAULT_TOPIC,
            status=PearWorkerRun.DONE,
        )
        self.login_user(owner)

        result = self.client_get(
            f"/json{PEAR_RUNS_ENDPOINT}",
            {
                "run_ids": orjson.dumps([other_side_run.id]).decode(),
                "include_context": orjson.dumps(False).decode(),
            },
            intentionally_undocumented=True,
        )

        self.assert_json_error(result, "Pear run not found.")

    def test_get_pear_runs_limits_requested_run_ids(self) -> None:
        owner = self.example_user("hamlet")
        self.login_user(owner)

        result = self.client_get(
            f"/json{PEAR_RUNS_ENDPOINT}",
            {
                "run_ids": orjson.dumps(list(range(1, 22))).decode(),
                "include_context": orjson.dumps(False).decode(),
            },
            intentionally_undocumented=True,
        )

        self.assert_json_error(result, "Too many Pear runs requested.")

    def test_post_pear_config_creates_bot(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)
        cache.set(
            pear_selected_bot_cache_key(owner.realm_id),
            {"user_profile_id": None},
        )

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )

            result_dict = self.assert_json_success(result)
            pear_config = result_dict["pear_config"]
            self.assertTrue(result_dict["created"])
            self.assertTrue(pear_config["enabled"])
            self.assertEqual(pear_config["bot_short_name"], "pear")
            self.assertEqual(pear_config["embedded_bot_service"], PEAR_EMBEDDED_BOT_SERVICE)
            self.assertEqual(pear_config["bot_count"], 1)
            self.assertEqual(pear_config["bot_full_name"], "Pear")
            self.assertEqual(pear_config["worker_agent_runtime"], "disabled")
            self.assertFalse(pear_config["worker_runtime_ready"])
            self.assertEqual(
                pear_config["selected_bot_user_id"],
                pear_config["bot_user_id"],
            )
            self.assertEqual(len(pear_config["bot_identities"]), 1)
            self.assertTrue(pear_config["bot_identities"][0]["selected"])
            self.assertEqual(
                get_selected_pear_bot_user_id_for_realm(owner.realm_id),
                pear_config["bot_user_id"],
            )

            second_result = self.client_post(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )

        second_result_dict = self.assert_json_success(second_result)
        self.assertFalse(second_result_dict["created"])
        self.assertEqual(
            second_result_dict["pear_config"]["bot_user_id"],
            pear_config["bot_user_id"],
        )
        self.assertEqual(
            second_result_dict["pear_config"]["selected_bot_user_id"],
            pear_config["selected_bot_user_id"],
        )

    def test_pear_followup_gate_defaults_new_topics_to_ambient(self) -> None:
        owner = self.example_user("iago")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        cache.delete(pear_selected_bot_cache_key(owner.realm_id))

        with self.settings(
            EMBEDDED_BOTS_ENABLED=True,
            PEAR_ADDRESSING_ROUTER_RUNTIME="gemini-interactions",
        ):
            missing_context_event = get_pear_followup_service_bot_event(
                realm_id=owner.realm_id,
                recipient_id=123,
                recipient_type=Recipient.STREAM,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="missing-context",
                content="can someone check the failing test?",
                mentioned_user_ids=set(),
            )
            PearWorkspaceNode.objects.create(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="quiet-context",
                response_policy="mentions_only",
            )
            quiet_context_event = get_pear_followup_service_bot_event(
                realm_id=owner.realm_id,
                recipient_id=123,
                recipient_type=Recipient.STREAM,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="quiet-context",
                content="can someone check the failing test?",
                mentioned_user_ids=set(),
            )

        self.assertEqual(
            missing_context_event,
            {
                "trigger": "pear_followup",
                "user_profile_id": pear_bot.id,
            },
        )
        self.assertIsNone(quiet_context_event)

    def test_post_pear_first_run_creates_sandbox_once(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)
        cache.set(
            pear_selected_bot_cache_key(owner.realm_id),
            {"user_profile_id": None},
        )

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{PEAR_FIRST_RUN_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )
            second_result = self.client_post(
                f"/json{PEAR_FIRST_RUN_ENDPOINT}",
                {},
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        first_run = result_dict["pear_first_run"]
        self.assertEqual(result_dict["narrow_url"], first_run["narrow_url"])
        self.assertEqual(first_run["stream"], PEAR_FIRST_RUN_STREAM)
        self.assertEqual(first_run["topic"], PEAR_FIRST_RUN_TOPIC)
        self.assertTrue(first_run["bot_created"])
        self.assertTrue(first_run["stream_created"])
        self.assertTrue(first_run["message_created"])
        self.assertTrue(first_run["pear_config"]["enabled"])
        self.assertEqual(
            get_selected_pear_bot_user_id_for_realm(owner.realm_id),
            first_run["pear_config"]["bot_user_id"],
        )
        self.assertIn("/#narrow/channel/", first_run["narrow_url"])
        self.assertIn("/topic/", first_run["narrow_url"])

        stream = get_stream(PEAR_FIRST_RUN_STREAM, owner.realm)
        self.assertEqual(first_run["stream_id"], stream.id)
        pear_bot = UserProfile.objects.get(id=first_run["pear_config"]["bot_user_id"])
        for subscriber in (owner, pear_bot):
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
        self.assertEqual(message.topic_name(), PEAR_FIRST_RUN_TOPIC)
        self.assertEqual(message.content, PEAR_FIRST_RUN_STARTER_MESSAGE)
        self.assertEqual(
            Message.objects.filter(
                realm=owner.realm,
                recipient=stream.recipient,
                content=PEAR_FIRST_RUN_STARTER_MESSAGE,
            ).count(),
            1,
        )

        second_result_dict = self.assert_json_success(second_result)
        second_first_run = second_result_dict["pear_first_run"]
        self.assertFalse(second_first_run["bot_created"])
        self.assertFalse(second_first_run["stream_created"])
        self.assertFalse(second_first_run["message_created"])
        self.assertFalse(second_first_run["selection_changed"])
        self.assertEqual(second_first_run["message_id"], first_run["message_id"])
        self.assertEqual(
            Message.objects.filter(
                realm=owner.realm,
                recipient=stream.recipient,
                content=PEAR_FIRST_RUN_STARTER_MESSAGE,
            ).count(),
            1,
        )

    def test_post_pear_first_run_requires_admin(self) -> None:
        self.login("hamlet")

        result = self.client_post(
            f"/json{PEAR_FIRST_RUN_ENDPOINT}",
            {},
            intentionally_undocumented=True,
        )

        self.assert_json_error(result, "Must be an organization administrator")

    def test_post_pear_config_selects_existing_bot(self) -> None:
        owner = self.example_user("iago")
        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            pear_bot = self.create_test_bot(
                "pear",
                owner,
                full_name="Pear",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=PEAR_EMBEDDED_BOT_SERVICE,
            )
            pear_infra_bot = self.create_test_bot(
                "pear-infra",
                owner,
                full_name="Pear Infra",
                bot_type=UserProfile.EMBEDDED_BOT,
                service_name=PEAR_EMBEDDED_BOT_SERVICE,
            )
            self.login_user(owner)
            self.assertEqual(
                get_selected_pear_bot_user_id_for_realm(owner.realm_id),
                pear_bot.id,
            )

            result = self.client_post(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                {"selected_bot_user_id": str(pear_infra_bot.id)},
                intentionally_undocumented=True,
            )
            followup_result = self.client_get(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                intentionally_undocumented=True,
            )

        result_dict = self.assert_json_success(result)
        pear_config = result_dict["pear_config"]
        self.assertFalse(result_dict["created"])
        self.assertTrue(result_dict["selection_changed"])
        self.assertEqual(pear_config["bot_count"], 2)
        self.assertEqual(pear_config["bot_user_id"], pear_infra_bot.id)
        self.assertEqual(pear_config["selected_bot_user_id"], pear_infra_bot.id)
        self.assertEqual(pear_config["selected_bot_full_name"], "Pear Infra")
        self.assertEqual(
            pear_config["bot_identities"],
            [
                {
                    "bot_user_id": pear_bot.id,
                    "bot_full_name": "Pear",
                    "bot_email": pear_bot.email,
                    "selected": False,
                },
                {
                    "bot_user_id": pear_infra_bot.id,
                    "bot_full_name": "Pear Infra",
                    "bot_email": pear_infra_bot.email,
                    "selected": True,
                },
            ],
        )

        followup_result_dict = self.assert_json_success(followup_result)
        self.assertEqual(
            followup_result_dict["pear_config"]["selected_bot_user_id"],
            pear_infra_bot.id,
        )
        self.assertEqual(
            get_selected_pear_bot_user_id_for_realm(owner.realm_id),
            pear_infra_bot.id,
        )

    def test_post_pear_config_rejects_invalid_selection(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)

        with self.settings(EMBEDDED_BOTS_ENABLED=True):
            result = self.client_post(
                f"/json{PEAR_CONFIG_ENDPOINT}",
                {"selected_bot_user_id": "999999"},
                intentionally_undocumented=True,
            )

        self.assert_json_error(result, "Invalid Pear bot selection.")

    def test_get_pear_topic_context_is_read_only_without_existing_context(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_WORKER_MODEL="gpt-5.5",
                PEAR_WORKER_MODEL_CHOICES=["gpt-5.5", "gpt-5.5-mini"],
                PEAR_WORKER_WORKDIR="/workspace/base",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            result = self.client_get(
                f"/json{PEAR_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

            result_dict = self.assert_json_success(result)
            context_payload = result_dict["pear_topic_context"]

            self.assertEqual(context_payload["model"], "gpt-5.5")
            self.assertIn("gpt-5.5", context_payload["model_choices"])
            self.assertIn("gpt-5.5-mini", context_payload["model_choices"])
            self.assertEqual(context_payload["reasoning_effort"], "medium")
            self.assertEqual(context_payload["default_reasoning_effort"], "medium")
            self.assertEqual(context_payload["response_policy"], "ambient")
            self.assertEqual(context_payload["workdir"], "")
            self.assertFalse(context_payload["git"]["available"])
            self.assertFalse(
                PearWorkspaceNode.objects.filter(
                    realm=owner.realm,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name=PEAR_DEFAULT_TOPIC,
                ).exists()
            )

    def test_topic_context_can_skip_git_status_for_runtime_picker(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            self.settings(PEAR_WORKER_WORKDIR="/workspace/base"),
            patch("zerver.views.pear.build_pear_git_context") as build_git_context,
        ):
            topic_result = self.client_get(
                f"/json{PEAR_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "include_git": orjson.dumps(False).decode(),
                },
                intentionally_undocumented=True,
            )
            runs_result = self.client_get(
                f"/json{PEAR_RUNS_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "include_context": orjson.dumps(True).decode(),
                    "include_git": orjson.dumps(False).decode(),
                },
                intentionally_undocumented=True,
            )

        topic_payload = self.assert_json_success(topic_result)["pear_topic_context"]
        runs_payload = self.assert_json_success(runs_result)["pear_topic_context"]
        self.assertNotIn("git", topic_payload)
        self.assertNotIn("git", runs_payload)
        self.assertEqual(topic_payload["model"], "gpt-5.5")
        self.assertEqual(runs_payload["reasoning_effort"], "medium")
        build_git_context.assert_not_called()

    def test_ensure_pear_workspace_node_does_not_create_readme(self) -> None:
        owner = self.example_user("hamlet")
        topic_name = "isolated topic"

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch(
                "zerver.lib.pear_worker.ensure_pear_topic_workdir",
                return_value="main",
            ),
        ):
            context = ensure_pear_workspace_node(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=topic_name,
            )

        self.assertFalse((Path(context.workdir) / "README.md").exists())

    def test_pear_read_text_path_distinguishes_missing_from_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(pear_read_text_path(Path(temp_dir) / "missing.md"), "")

        with (
            patch(
                "zerver.lib.pear_worker.Path.read_text",
                side_effect=PermissionError,
            ),
            self.assertRaises(OSError),
        ):
            pear_read_text_path(Path("/unreadable/README.md"))

    def test_existing_base_workdir_topic_context_is_repaired(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = str(Path(temp_dir) / "base")
            topic_root = str(Path(temp_dir) / "topics")
            PearWorkspaceNode.objects.create(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                workdir=base_workdir,
            )

            with self.settings(
                PEAR_WORKER_WORKDIR=base_workdir,
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic=PEAR_DEFAULT_TOPIC,
                )

            self.assertNotEqual(context.workdir, base_workdir)
            self.assertTrue(context.workdir.startswith(topic_root))
            self.assertTrue(Path(context.workdir).exists())

    def test_topic_branch_name_uses_valid_topic_and_sanitizes_invalid_topic(self) -> None:
        self.assertEqual(pear_topic_branch_name("pear-efficient-smoke"), "pear-efficient-smoke")
        self.assertEqual(pear_topic_branch_name("Feature/Some Work!"), "Feature-Some-Work")
        self.assertEqual(pear_topic_branch_name(""), "topic")

    def test_workspace_default_branch_skips_blank_candidates(self) -> None:
        # An unset project base ref must not surface pear_topic_branch_name's
        # literal "topic" fallback as the branch for every workspace.
        self.assertEqual(pear_workspace_node_default_branch(), "main")
        self.assertEqual(
            pear_workspace_node_default_branch(topic="fable-ui-tour"),
            "fable-ui-tour",
        )
        self.assertEqual(
            pear_workspace_node_default_branch(topic="Feature/Some Work!"),
            "Feature-Some-Work",
        )

    def test_topic_workdir_uses_workspace_default_branch_when_created(self) -> None:
        owner = self.example_user("hamlet")
        topic = "pear-efficient-smoke"

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
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
            self.assertEqual(context.stream_name, PEAR_DEFAULT_STREAM)
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
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir:
            # A dedicated base repository keeps worktree/branch creation
            # hermetic: against a shared checkout, the topic branch can
            # already be held by a stale worktree and fall into the
            # digest-suffix retry, making branch assertions flaky.
            base_workdir = Path(temp_dir) / "base"
            self.create_git_repository(base_workdir)
            with self.settings(
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics"),
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic=PEAR_DEFAULT_TOPIC,
                )
                payload = serialize_pear_workspace_context(
                    context,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name=PEAR_DEFAULT_TOPIC,
                )

        self.assertEqual(payload["workspace_id"], context.id)
        self.assertEqual(payload["workspace_uuid"], str(context.uuid))
        self.assertEqual(payload["branch"], context.branch_name)
        # Fresh workspaces work on the topic-derived branch, so clients can
        # collapse the redundant branch chip in the topic header.
        self.assertEqual(payload["branch"], pear_topic_branch_name(PEAR_DEFAULT_TOPIC))
        self.assertTrue(payload["branch_is_topic_default"])
        self.assertEqual(payload["base_ref"], "")
        self.assertEqual(payload["head"], "")

        # A legacy or custom branch is surprising and must stay visible.
        context.branch_name = "master"
        context.save(update_fields=["branch_name"])
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(PEAR_TOPIC_WORKDIR_ROOT=temp_dir),
        ):
            legacy_payload = serialize_pear_workspace_context(
                context,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
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
            self.settings(PEAR_TOPIC_WORKDIR_ROOT=temp_dir),
        ):
            trunk_payload = serialize_pear_workspace_context(
                context,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="",
            )
        self.assertEqual(trunk_payload["branch"], "main")
        self.assertTrue(trunk_payload["branch_is_topic_default"])

    def test_startup_reap_fails_orphaned_running_runs_regardless_of_age(self) -> None:
        user = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            user,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        run = PearWorkerRun.objects.create(
            realm=user.realm,
            bot_profile=pear_bot,
            trigger_message_id=4242,
            requesting_user=user,
            status=PearWorkerRun.RUNNING,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name=PEAR_DEFAULT_TOPIC,
            heartbeat_at=timezone_now(),
        )

        # A fresh heartbeat protects the run from the age-gated reap.
        self.assertEqual(reap_stale_pear_worker_runs(), 0)
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.RUNNING)

        # The queue is single-consumer: at worker boot, a RUNNING run is
        # by definition orphaned, however young its heartbeat.
        self.assertEqual(reap_stale_pear_worker_runs(assume_orphaned=True), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.FAILED)
        self.assertIn("orphaned", run.error)

    def test_worker_run_store_links_runs_to_workspace_node(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        store = PearDatabaseWorkerRunStore()

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            started = store.start(
                SimpleNamespace(user_profile=pear_bot),
                9876,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )
            run = PearWorkerRun.objects.get(trigger_message_id=9876)
            context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )

        self.assertTrue(started)
        self.assertEqual(run.workspace_node_id, context.id)

    def test_topic_worker_sandbox_downgrades_full_access_to_workspace_write(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                PEAR_WORKER_AGENT_RUNTIME="codex-app-server",
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
                PEAR_TOPIC_ISOLATION_BACKEND="codex-sandbox",
                PEAR_WORKER_SANDBOX="danger-full-access",
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="isolated topic",
                )
                runtime = build_pear_worker_runtime_from_settings(
                    model_override="gpt-5.5",
                    workdir_override=context.workdir,
                )

                self.assertIsNone(pear_worker_runtime_isolation_error(context.workdir))
                self.assertEqual(
                    pear_worker_sandbox_for_topic(
                        owner,
                        stream=PEAR_DEFAULT_STREAM,
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
                PEAR_WORKER_AGENT_RUNTIME="codex-app-server",
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_WORKER_SANDBOX="workspace-write",
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="isolated topic",
                )
                runtime = build_pear_worker_runtime_from_settings(
                    model_override="gpt-5.5",
                    workdir_override=context.workdir,
                )

                self.assertEqual(
                    pear_worker_sandbox_for_topic(
                        owner,
                        stream=PEAR_DEFAULT_STREAM,
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
            active_session = build_pear_active_session_summary_from_messages(
                pear_session=get_pear_session_payload(),
                source="server",
                stream_exists=True,
                messages=[(10, "@Pear edit the control plane", False)],
            )

            with self.settings(
                PEAR_WORKER_AGENT_RUNTIME="codex-app-server",
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
                PEAR_WORKER_SANDBOX="danger-full-access",
            ):
                runtime = build_pear_worker_runtime_from_settings(
                    model_override="gpt-5.5",
                    workdir_override=str(base_workdir),
                )
                tool_results = list(
                    runtime.run_tools(active_session, task="edit the control plane")
                )

            self.assertEqual(tool_results[0]["label"], "Error")
            self.assertIn("outside the Pear topic workspace root", tool_results[0]["value"])

    def test_os_user_topic_context_uses_independent_repository_clone(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, "megatron-lm")
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"
        cloned_paths: list[Path] = []

        def fake_clone(_repository_url: str, workdir: Path) -> None:
            cloned_paths.append(workdir)
            workdir.mkdir(parents=True)
            (workdir / ".git").mkdir()

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            ),
            patch(
                "zerver.lib.pear_worker.clone_or_update_pear_repository_workdir",
                side_effect=fake_clone,
            ),
        ):
            channel_context = configure_pear_channel_repository_workspace(
                owner,
                stream="megatron-lm",
                repository_url=repository_url,
            )
            project = PearProject.objects.get(
                realm=owner.realm,
                stream=get_stream("megatron-lm", owner.realm),
            )
            project.default_base_ref = "dev"
            project.save(update_fields=["default_base_ref", "date_updated"])
            topic_context = ensure_pear_workspace_node(
                owner,
                stream="megatron-lm",
                topic="optimizer work",
            )

            self.assertEqual(
                cloned_paths,
                [Path(channel_context.workdir), Path(topic_context.workdir)],
            )
            self.assertTrue((Path(topic_context.workdir) / ".git").is_dir())
            self.assertFalse((Path(topic_context.workdir) / ".git").is_file())

    def test_os_user_process_isolation_invokes_topic_helper(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_TOPIC_ISOLATION_HELPER="/usr/local/sbin/pear-topic-isolate",
                PEAR_CODEX_SKILLS_DIR=str(Path(temp_dir) / "missing-skills"),
            ),
        ):
            workdir = str(Path(temp_dir) / "topic")
            Path(workdir).mkdir()
            process_isolation = pear_topic_process_isolation(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="isolated topic",
            )
            calls = []

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("zerver.lib.pear_worker.subprocess.run", side_effect=fake_run):
                error = pear_worker_process_isolation_error(
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
                "/usr/local/sbin/pear-topic-isolate",
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
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_TOPIC_ISOLATION_HELPER="/usr/local/sbin/pear-topic-isolate",
                PEAR_CODEX_SKILLS_DIR=str(Path(temp_dir) / "missing-skills"),
            ),
        ):
            workdir = Path(temp_dir) / "private-topic"
            workdir.mkdir()
            process_isolation = pear_topic_process_isolation(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="private topic",
            )
            with (
                patch(
                    "zerver.lib.pear_worker.Path.is_file",
                    side_effect=PermissionError,
                ),
                patch(
                    "zerver.lib.pear_worker.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                ) as run_mock,
            ):
                error = pear_worker_process_isolation_error(
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
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            workdir = Path(temp_dir) / "legacy-worktree"
            workdir.mkdir()
            (workdir / ".git").write_text(
                "gitdir: /home/zulipdev/zulip/.git/worktrees/legacy\n",
                encoding="utf-8",
            )
            process_isolation = pear_topic_process_isolation(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="legacy worktree",
            )
            with patch("zerver.lib.pear_worker.subprocess.run") as run_mock:
                error = pear_worker_process_isolation_error(
                    process_isolation,
                    workdir=str(workdir),
                )

        self.assertIn("shared Git worktree", error)
        run_mock.assert_not_called()

    def test_topic_workdir_root_inside_base_is_repaired_to_sibling_root(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            self.create_git_repository(base_workdir)
            unsafe_topic_root = base_workdir / "var" / "pear-workspaces"

            with self.settings(
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=str(unsafe_topic_root),
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="outside-root",
                )

            context_workdir = Path(context.workdir).resolve(strict=False)
            self.assertFalse(context_workdir.is_relative_to(base_workdir.resolve()))
            self.assertIn("pear-workspaces", context.workdir)
            self.assertTrue((context_workdir / ".git").exists())

    def test_os_user_topic_workdir_root_inside_service_home_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service_home = Path(temp_dir) / "home" / "zulipdev"
            base_workdir = service_home / "zulip"
            unsafe_topic_root = service_home / "pear-workspaces"

            with (
                self.settings(
                    PEAR_WORKER_WORKDIR=str(base_workdir),
                    PEAR_TOPIC_WORKDIR_ROOT=str(unsafe_topic_root),
                    PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                ),
                patch("zerver.lib.pear_worker.Path.home", return_value=service_home),
            ):
                self.assertEqual(pear_topic_workdir_root(), Path("/srv/pear-workspaces"))

    def test_os_user_topic_workdir_initializes_independent_git_repository(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            workdir = Path(temp_dir) / "topic"
            ensure_pear_topic_workdir(str(workdir))

            self.assertTrue((workdir / ".git").is_dir())

    def test_topic_workdir_provisions_git_author_identity(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            workdir = Path(temp_dir) / "fable-ui-tour"
            ensure_pear_topic_workdir(str(workdir))

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
            self.assertEqual(name, "Pear")
            self.assertTrue(email.startswith("pear-worker@"))

    def test_topic_workdir_keeps_existing_git_author_identity(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            ),
        ):
            workdir = Path(temp_dir) / "fable-ui-tour"
            ensure_pear_topic_workdir(str(workdir))
            subprocess.run(
                ["git", "-C", str(workdir), "config", "user.email", "human@example.com"],
                check=True,
                timeout=10,
            )

            ensure_pear_topic_workdir(str(workdir))

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
            topic_workdir = Path(temp_dir) / "devel-pear-efficient-smoke"
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

            git_context = build_pear_git_context(
                str(topic_workdir),
                topic="devel-pear-efficient-smoke",
            )

            self.assertEqual(git_context["worktree_name"], "devel-pear-efficient-smoke")
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

    def test_pear_git_status_delta_uses_cache_until_invalidated(self) -> None:
        invalidate_pear_git_status_delta()
        clean_status = "# branch.oid 1234567890abcdef\n# branch.head topic-a\n# branch.upstream origin/topic-a\n# branch.ab +2 -1"
        dirty_status = f"{clean_status}\n1 .M N... 100644 100644 100644 a b file.txt"

        # Each refresh issues three git commands (status, log -1,
        # rev-list --count); the commit-info calls return "" here.
        with patch(
            "zerver.lib.pear_worker.pear_git_output",
            side_effect=[clean_status, "", "", dirty_status, "", ""],
        ) as git_output:
            clean_delta = pear_git_status_delta("/repo", topic="topic-a")
            cached_delta = pear_git_status_delta("/repo", topic="topic-a")
            invalidate_pear_git_status_delta("/repo")
            dirty_delta = pear_git_status_delta("/repo", topic="topic-a")

        self.assertEqual(git_output.call_count, 6)
        self.assertEqual(clean_delta["head"], "1234567")
        self.assertEqual(clean_delta["ahead"], 2)
        self.assertEqual(clean_delta["behind"], 1)
        self.assertTrue(clean_delta["branch_matches_topic"])
        self.assertFalse(clean_delta["dirty"])
        self.assertEqual(cached_delta, clean_delta)
        self.assertTrue(dirty_delta["dirty"])
        self.assertEqual(dirty_delta["changed_count"], 1)

    def test_pear_git_status_delta_reports_structured_changed_files(self) -> None:
        invalidate_pear_git_status_delta()
        status_output = "# branch.oid 1234567890abcdef\n# branch.head topic-a\n1 .M N... 100644 100644 100644 a b modified.txt\n1 A. N... 000000 100644 100644 a b added.txt\n1 D. N... 100644 000000 000000 a b deleted.txt\n2 R. N... 100644 100644 100644 a b R100 renamed.txt\told-name.txt\nu UU N... 100644 100644 100644 100644 a b c conflicted.txt\n? untracked.txt"

        with patch(
            "zerver.lib.pear_worker.pear_git_output",
            return_value=status_output,
        ):
            delta = pear_git_status_delta("/repo", topic="topic-a")

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

    def test_pear_git_status_delta_runs_bounded_git_commands(self) -> None:
        invalidate_pear_git_status_delta()
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
            patch("zerver.lib.pear_worker.subprocess.run", return_value=completed) as run_mock,
        ):
            Path(workdir, ".git").touch()
            delta = pear_git_status_delta(workdir, topic="topic-b")

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

    def test_pear_git_status_delta_skips_commit_reads_on_unborn_head(self) -> None:
        invalidate_pear_git_status_delta()
        status_output = "# branch.oid (initial)\n# branch.head topic-a\n? new.txt"
        completed = subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=status_output,
            stderr="",
        )
        with (
            tempfile.TemporaryDirectory() as workdir,
            patch("zerver.lib.pear_worker.subprocess.run", return_value=completed) as run_mock,
        ):
            Path(workdir, ".git").touch()
            delta = pear_git_status_delta(workdir, topic="topic-a")

        # A freshly initialized workspace has no HEAD yet; running the
        # commit-info commands would make git fail (and warn) on every
        # refresh, so only the status command may run.
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(run_mock.call_args_list[0].args[0][3], "status")
        self.assertNotIn("last_commit", delta)
        self.assertNotIn("commit_count", delta)
        self.assertEqual(delta["head_short"], "")

    def test_pear_git_output_logs_stderr_on_failure(self) -> None:
        completed = subprocess.CompletedProcess(
            ["git"],
            128,
            stdout="",
            stderr="fatal: not a git repository",
        )

        with tempfile.TemporaryDirectory() as workdir:
            Path(workdir, ".git").touch()
            with (
                patch("zerver.lib.pear_worker.subprocess.run", return_value=completed),
                self.assertLogs("zerver.lib.pear_worker", level="WARNING") as logs,
            ):
                self.assertEqual(pear_git_output(workdir, ["status"]), "")

        log_output = "\n".join(logs.output)
        self.assertIn("status 128", log_output)
        self.assertIn("fatal: not a git repository", log_output)

    def test_pear_git_output_skips_missing_workdir_without_warning(self) -> None:
        with (
            tempfile.TemporaryDirectory() as parent,
            patch("zerver.lib.pear_worker.subprocess.run") as run_mock,
        ):
            missing_workdir = str(Path(parent) / "removed-topic-worktree")
            self.assertEqual(pear_git_output(missing_workdir, ["status"]), "")

        run_mock.assert_not_called()

    def test_pear_git_output_skips_uninitialized_workdir_without_warning(self) -> None:
        with (
            tempfile.TemporaryDirectory() as workdir,
            patch("zerver.lib.pear_worker.subprocess.run") as run_mock,
        ):
            self.assertEqual(pear_git_output(workdir, ["status"]), "")

        run_mock.assert_not_called()

    def test_workspace_context_reports_local_topic_repo_without_external_pin(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_WORKER_WORKDIR=str(Path(temp_dir) / "base"),
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            workdir = pear_expected_topic_workdir(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )
            self.create_git_repository(workdir)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workdir),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/zulip/zulip.git",
                ],
                check=True,
                timeout=10,
            )
            context = PearWorkspaceNode.objects.create(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                workdir=str(workdir),
            )
            expected_branch = pear_git_current_branch_name(workdir)

            workspace_context = build_pear_workspace_context(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )

            self.assertEqual(workspace_context["repository"], PEAR_DEFAULT_STREAM)
            self.assertEqual(workspace_context["remote_url"], "")
            self.assertEqual(workspace_context["worktree"], PEAR_DEFAULT_TOPIC)
            self.assertEqual(workspace_context["branch"], expected_branch)
            self.assertEqual(workspace_context["head"], "")
            topic_payload = serialize_pear_workspace_context(
                context,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )
            self.assertEqual(topic_payload["github_repo"], "")
            self.assertEqual(topic_payload["repo_label"], PEAR_DEFAULT_STREAM)
            self.assertEqual(topic_payload["worktree_label"], PEAR_DEFAULT_TOPIC)

            project, _created = PearProject.objects.update_or_create(
                realm=owner.realm,
                stream=get_stream(PEAR_DEFAULT_STREAM, owner.realm),
                defaults={
                    "repository_url": "zulip/zulip",
                    "repo_cache_path": str(workdir),
                    "clone_status": PearProject.READY,
                },
            )
            context.project = project
            context.save(update_fields=["project", "date_updated"])

            workspace_context = build_pear_workspace_context(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )

            self.assertEqual(workspace_context["repository"], "zulip/zulip")
            self.assertEqual(workspace_context["remote_url"], "zulip/zulip")

    def test_topic_workdir_creation_does_not_copy_self_dev_overlay(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)
            pear_file = base_workdir / "zerver/lib/pear_worker.py"
            pear_file.parent.mkdir(parents=True)
            pear_file.write_text("dirty pear self-dev file\n", encoding="utf-8")

            with self.settings(
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="clean-topic",
                )

            self.assertFalse((Path(context.workdir) / "zerver/lib/pear_worker.py").exists())

    def test_channel_creation_can_pin_pear_repository(self) -> None:
        owner = self.example_user("hamlet")
        self.login_user(owner)
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch("zerver.lib.pear_worker.clone_or_update_pear_repository_workdir") as clone_mock,
        ):
            result = self.client_post(
                "/json/users/me/subscriptions",
                {
                    "subscriptions": orjson.dumps(
                        [{"name": "megatron-lm", "description": "Pinned repository"}]
                    ).decode(),
                    "principals": orjson.dumps([owner.id]).decode(),
                    "pear_repository_url": repository_url,
                },
            )

            result_dict = self.assert_json_success(result)
            context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name="megatron-lm",
                topic_name="",
            )

            assert context.project is not None
            self.assertEqual(context.project.repository_url, repository_url)
            self.assertTrue(context.workdir.startswith(temp_dir))
            project = PearProject.objects.get(
                realm=owner.realm,
                stream=get_stream("megatron-lm", owner.realm),
            )
            self.assertEqual(project.repository_url, repository_url)
            self.assertEqual(project.repo_cache_path, context.workdir)
            self.assertNotIn("pear_repository_error", result_dict)
            clone_mock.assert_called_once_with(repository_url, Path(context.workdir))

    def test_topic_context_inherits_channel_repository_checkout(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, "megatron-lm")
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"

        def fake_clone(_repository_url: str, workdir: Path) -> None:
            workdir.mkdir(parents=True)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch(
                "zerver.lib.pear_worker.clone_or_update_pear_repository_workdir",
                side_effect=fake_clone,
            ),
        ):
            channel_context = configure_pear_channel_repository_workspace(
                owner,
                stream="megatron-lm",
                repository_url=repository_url,
            )
            project = PearProject.objects.get(
                realm=owner.realm,
                stream=get_stream("megatron-lm", owner.realm),
            )
            project.default_base_ref = "dev"
            project.save(update_fields=["default_base_ref", "date_updated"])
            topic_context = ensure_pear_workspace_node(
                owner,
                stream="megatron-lm",
                topic="optimizer work",
            )

            self.assertEqual(topic_context.parent_node_id, channel_context.id)
            assert channel_context.project is not None
            self.assertEqual(channel_context.project.repository_url, repository_url)
            self.assertNotEqual(topic_context.workdir, channel_context.workdir)
            self.assertTrue(Path(topic_context.workdir).exists())
            assert topic_context.project is not None
            self.assertEqual(topic_context.project.repository_url, repository_url)
            self.assertEqual(topic_context.branch_name, "dev")

    def test_topic_context_does_not_clone_local_parent_workspace_path(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
            patch("zerver.lib.pear_worker.clone_or_update_pear_repository_workdir") as clone,
        ):
            parent_path = Path(temp_dir) / "devel-base"
            parent_path.mkdir()
            PearWorkspaceNode.objects.create(
                realm=owner.realm,
                stream_name="devel",
                topic_name="",
                workdir=str(parent_path),
            )

            topic_context = ensure_pear_workspace_node(
                owner,
                stream="devel",
                topic="general chat",
            )

            clone.assert_not_called()
            self.assertIsNotNone(topic_context.parent_node)
            self.assertEqual(topic_context.parent_node.stream_name, "devel")
            self.assertTrue(Path(topic_context.workdir).is_dir())

    def test_context_isolation_username_waits_for_existing_os_user(self) -> None:
        owner = self.example_user("hamlet")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            workdir = Path(temp_dir) / "devel-general-chat"
            context = PearWorkspaceNode.objects.create(
                realm=owner.realm,
                stream_name="devel",
                topic_name="general chat",
                workdir=str(workdir),
            )
            expected_username = pear_topic_isolation_username(
                owner,
                stream="devel",
                topic="general chat",
            )

            with patch("zerver.lib.pear_worker.pwd.getpwnam", side_effect=KeyError):
                self.assertIsNone(
                    pear_workspace_isolation_username(
                        context,
                        stream_name="devel",
                        topic_name="general chat",
                        workdir=str(workdir),
                    )
                )

            with patch(
                "zerver.lib.pear_worker.pwd.getpwnam",
                return_value=SimpleNamespace(pw_name=expected_username),
            ):
                self.assertEqual(
                    pear_workspace_isolation_username(
                        context,
                        stream_name="devel",
                        topic_name="general chat",
                        workdir=str(workdir),
                    ),
                    expected_username,
                )

    def test_topic_isolation_user_exists_rejects_missing_user(self) -> None:
        self.assertFalse(pear_topic_isolation_user_exists("pear-user-that-should-not-exist"))

    def test_topic_context_refactor_moves_full_topic_rename(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, PEAR_DEFAULT_STREAM)
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_pear_workspace_node(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
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
                run = PearWorkerRun.objects.create(
                    realm=owner.realm,
                    bot_profile=pear_bot,
                    requesting_user=owner,
                    trigger_message_id=3001,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name="old topic",
                    workspace_node=context,
                    status=PearWorkerRun.DONE,
                    codex_thread_id="thread-1",
                )
                side_run = PearWorkerRun.objects.create(
                    realm=owner.realm,
                    bot_profile=pear_bot,
                    requesting_user=owner,
                    trigger_message_id=3002,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name="side topic",
                    session_kind=PearWorkerRun.SIDE,
                    source_stream_name=PEAR_DEFAULT_STREAM,
                    source_topic_name="old topic",
                    status=PearWorkerRun.DONE,
                )

                result = maybe_refactor_pear_workspace_on_move(
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

            self.assertEqual(result.topic_name, "new topic")
            self.assertEqual(result.message, "Pear workspace label moved with this topic.")
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
            self.assertFalse(
                PearWorkspaceNode.objects.filter(
                    realm=owner.realm,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name="old topic",
                ).exists()
            )

    def test_topic_context_refactor_warns_for_partial_topic_move(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics"),
            ),
        ):
            context = ensure_pear_workspace_node(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="old topic",
            )
            old_workdir = context.workdir

            result = maybe_refactor_pear_workspace_on_move(
                owner,
                old_stream=stream,
                old_topic="old topic",
                new_stream=stream,
                new_topic="new topic",
                moved_all_visible_messages=False,
            )

            context.refresh_from_db()

        assert result is not None
        self.assertIn("only part of the topic was moved", result.message)
        self.assertEqual(result.topic_name, "new topic")
        self.assertEqual(context.topic_name, "old topic")
        self.assertEqual(context.workdir, old_workdir)

    def test_stream_context_refactor_updates_channel_rename_metadata(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        PearWorkspaceNode.objects.create(
            realm=owner.realm,
            stream_name="old-repo",
            topic_name="",
            workdir="/tmp/old-repo",  # noqa: S108
        )
        PearWorkspaceNode.objects.create(
            realm=owner.realm,
            stream_name="old-repo",
            topic_name="feature",
            workdir="/tmp/old-repo-feature",  # noqa: S108
        )
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=3101,
            stream_name="old-repo",
            topic_name="feature",
            status=PearWorkerRun.RUNNING,
        )
        side_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=3102,
            stream_name="side",
            topic_name="private",
            session_kind=PearWorkerRun.SIDE,
            source_stream_name="old-repo",
            source_topic_name="feature",
            status=PearWorkerRun.DONE,
        )

        message = maybe_refactor_pear_stream_workspaces_on_rename(
            owner,
            old_stream_name="old-repo",
            new_stream_name="new-repo",
        )

        assert message is not None
        self.assertIn("Pear channel context renamed", message)
        self.assertEqual(
            PearWorkspaceNode.objects.filter(
                realm=owner.realm,
                stream_name="new-repo",
            ).count(),
            2,
        )
        self.assertFalse(
            PearWorkspaceNode.objects.filter(
                realm=owner.realm,
                stream_name="old-repo",
            ).exists()
        )
        run.refresh_from_db()
        side_run.refresh_from_db()
        self.assertEqual(run.stream_name, "old-repo")
        self.assertEqual(side_run.source_stream_name, "old-repo")

    def test_clone_or_update_pear_repository_workdir_clones_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            destination = Path(temp_dir) / "checkout"
            source.mkdir()
            subprocess.run(["git", "-C", str(source), "init", "--quiet"], check=True, timeout=10)
            (source / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True, timeout=10)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.email=pear@example.com",
                    "-c",
                    "user.name=Pear",
                    "commit",
                    "--quiet",
                    "-m",
                    "initial",
                ],
                check=True,
                timeout=10,
            )

            clone_or_update_pear_repository_workdir(source.as_uri(), destination)

            self.assertEqual((destination / "README.md").read_text(encoding="utf-8"), "hello\n")
            self.assertTrue((destination / ".git").exists())

    def test_worker_start_allows_same_user_in_different_topics(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=901,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
        )

        reason = PearDatabaseWorkerRunStore().start_blocked_reason(
            SimpleNamespace(user_profile=pear_bot),
            902,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-b",
            requesting_user_id=owner.id,
        )

        self.assertIsNone(reason)

    def test_worker_start_blocks_parallel_shared_run_in_same_topic(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=903,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
        )

        reason = PearDatabaseWorkerRunStore().start_blocked_reason(
            SimpleNamespace(user_profile=pear_bot),
            904,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-a",
            requesting_user_id=owner.id,
        )

        self.assertEqual(reason, "This topic already has an active Pear worker run.")

    def test_worker_run_store_rejects_illegal_terminal_transitions(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        store = PearDatabaseWorkerRunStore()
        done_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=905,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.DONE,
        )
        cancelled_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=906,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.CANCELLED,
        )

        store.record(
            bot_handler,
            done_run.trigger_message_id,
            PearWorkerRun.RUNNING,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-a",
        )
        store.record(
            bot_handler,
            cancelled_run.trigger_message_id,
            PearWorkerRun.DONE,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-a",
        )

        done_run.refresh_from_db()
        cancelled_run.refresh_from_db()
        self.assertEqual(done_run.status, PearWorkerRun.DONE)
        self.assertEqual(cancelled_run.status, PearWorkerRun.CANCELLED)
        done_rejection = PearWorkerRunEvent.objects.get(
            run=done_run,
            event_type="transition_rejected",
        )
        cancelled_rejection = PearWorkerRunEvent.objects.get(
            run=cancelled_run,
            event_type="transition_rejected",
        )
        self.assertEqual(
            done_rejection.payload["reason"],
            "illegal_transition",
        )
        self.assertEqual(
            cancelled_rejection.payload["new_status"],
            PearWorkerRun.DONE,
        )

    def test_worker_run_store_only_requeues_failed_runs_explicitly(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        store = PearDatabaseWorkerRunStore()
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=907,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.FAILED,
            error="crashed",
        )

        store.record(
            bot_handler,
            run.trigger_message_id,
            PearWorkerRun.QUEUED,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-a",
        )
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.FAILED)
        self.assertEqual(run.error, "crashed")
        implicit_requeue_rejection = PearWorkerRunEvent.objects.get(
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
                stream=PEAR_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.QUEUED)
        self.assertIsNone(run.error)

    def test_worker_run_store_claims_queued_run_once(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        store = PearDatabaseWorkerRunStore()
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=908,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.QUEUED,
        )

        self.assertTrue(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=PEAR_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.RUNNING)
        self.assertEqual(run.attempts, 1)
        self.assertIsNotNone(run.heartbeat_at)

        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=PEAR_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.RUNNING)
        self.assertEqual(run.attempts, 1)
        self.assertEqual(
            PearWorkerRunEvent.objects.filter(run=run, event_type="claimed").count(),
            1,
        )
        self.assertEqual(
            PearWorkerRunEvent.objects.get(run=run, event_type="claim_dropped").payload["reason"],
            "already_running",
        )

    def test_worker_run_store_drops_completed_redelivery(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        store = PearDatabaseWorkerRunStore()
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=909,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.DONE,
            result_message_id=400,
        )

        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=PEAR_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.DONE)
        self.assertEqual(run.result_message_id, 400)
        self.assertEqual(
            PearWorkerRunEvent.objects.get(run=run, event_type="claim_dropped").payload["reason"],
            "terminal_or_finished_done",
        )

    def test_worker_run_store_reclaims_stale_running_once(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        store = PearDatabaseWorkerRunStore()
        stale_time = timezone_now() - timedelta(seconds=400)
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=910,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
            attempts=1,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )

        with self.settings(PEAR_WORKER_TIMEOUT_SECONDS=1):
            self.assertTrue(
                store.claim_for_run(
                    bot_handler,
                    run.trigger_message_id,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="topic-a",
                )
            )
            run.refresh_from_db()
            self.assertEqual(run.status, PearWorkerRun.RUNNING)
            self.assertEqual(run.attempts, 2)
            self.assertGreater(run.heartbeat_at, stale_time)
            self.assertEqual(
                PearWorkerRunEvent.objects.get(run=run, event_type="claimed").payload[
                    "stale_reclaim"
                ],
                True,
            )

            PearWorkerRun.objects.filter(id=run.id).update(
                heartbeat_at=stale_time,
                date_updated=stale_time,
            )
            self.assertFalse(
                store.claim_for_run(
                    bot_handler,
                    run.trigger_message_id,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="topic-a",
                )
            )
            run.refresh_from_db()
            self.assertEqual(run.status, PearWorkerRun.FAILED)
            self.assertEqual(
                run.error,
                "Pear worker run exceeded its stale-claim retry limit.",
            )
            self.assertEqual(
                PearWorkerRunEvent.objects.filter(run=run, event_type="claim_dropped")
                .latest("id")
                .payload["reason"],
                "stale_retry_limit",
            )

    def test_worker_run_store_busy_reader_reaps_stale_running_run(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        stale_time = timezone_now() - timedelta(seconds=400)
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=911,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
            attempts=1,
            heartbeat_at=stale_time,
            date_updated=stale_time,
        )

        with self.settings(PEAR_WORKER_TIMEOUT_SECONDS=1):
            self.assertIsNone(
                PearDatabaseWorkerRunStore().active_worker_run_message_id(
                    bot_handler,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="topic-a",
                )
            )

        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.FAILED)
        self.assertEqual(run.error, "Pear worker run became stale and was recovered.")
        self.assertEqual(
            PearWorkerRunEvent.objects.get(run=run, event_type="stale_reaped").payload[
                "stale_after_seconds"
            ],
            301,
        )

    def test_worker_run_store_events_refresh_heartbeat_when_due(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        old_heartbeat = timezone_now() - timedelta(seconds=20)
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=912,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
            attempts=1,
            heartbeat_at=old_heartbeat,
            date_updated=old_heartbeat,
        )
        store = PearDatabaseWorkerRunStore()

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
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        bot_handler = SimpleNamespace(user_profile=pear_bot)
        context = ensure_pear_workspace_node(
            pear_bot,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-a",
        )
        context.is_paused = True
        context.save(update_fields=["is_paused"])
        store = PearDatabaseWorkerRunStore()

        self.assertEqual(
            store.start_blocked_reason(
                bot_handler,
                913,
                stream=PEAR_DEFAULT_STREAM,
                topic="topic-a",
                requesting_user_id=owner.id,
            ),
            "Pear session is paused until a resume control is posted.",
        )
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=913,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.QUEUED,
        )
        self.assertFalse(
            store.claim_for_run(
                bot_handler,
                run.trigger_message_id,
                stream=PEAR_DEFAULT_STREAM,
                topic="topic-a",
            )
        )
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.FAILED)
        self.assertEqual(run.error, "Pear topic is paused.")
        self.assertEqual(
            PearWorkerRunEvent.objects.get(run=run, event_type="claim_dropped").payload["reason"],
            "topic_paused",
        )

    def test_active_session_reflects_database_pause_flag(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, PEAR_DEFAULT_STREAM)
        context = ensure_pear_workspace_node(
            user,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
        )
        context.is_paused = True
        context.save(update_fields=["is_paused"])
        self.login_user(user)

        result = self.client_get(
            f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        active_session = self.assert_json_success(result)["active_session"]
        self.assertEqual(active_session["state"], "paused")
        self.assertTrue(active_session["is_paused"])
        self.assertFalse(active_session["worker_can_run"])

    def test_run_controls_request_cancel_and_toggle_topic_pause(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        context = ensure_pear_workspace_node(
            pear_bot,
            stream=PEAR_DEFAULT_STREAM,
            topic="topic-a",
        )
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=914,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
        )
        self.login_user(owner)

        pause_result = self.client_post(
            f"/json/pear/runs/{run.id}/pause",
            intentionally_undocumented=True,
        )
        self.assert_json_success(pause_result)
        context.refresh_from_db()
        self.assertTrue(context.is_paused)

        resume_result = self.client_post(
            f"/json/pear/runs/{run.id}/resume",
            intentionally_undocumented=True,
        )
        self.assert_json_success(resume_result)
        context.refresh_from_db()
        self.assertFalse(context.is_paused)

        cancel_result = self.client_post(
            f"/json/pear/runs/{run.id}/cancel",
            intentionally_undocumented=True,
        )
        self.assert_json_success(cancel_result)
        run.refresh_from_db()
        self.assertEqual(run.status, PearWorkerRun.CANCELLED)
        self.assertIsNotNone(run.cancel_requested_at)

    def test_run_trace_is_safe_and_attention_is_bounded_to_active_runs(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        pear_bot = self.create_test_bot(
            "pear-trace",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=919,
            result_message_id=920,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
        )
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Hidden reasoning",
            payload={"kind": "reasoning", "body": "private chain of thought"},
        )
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Reading files",
            payload={
                "kind": "command",
                "title": "Read repository files",
                "body": "secret raw command output",
            },
        )
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Codex",
            payload={"kind": "assistant", "body": "I’m checking the relevant files."},
        )
        self.login_user(owner)

        trace_result = self.client_get(
            f"/json/pear/runs/{run.id}/trace",
            intentionally_undocumented=True,
        )
        trace = self.assert_json_success(trace_result)["trace"]
        trace_text = orjson.dumps(trace).decode()
        self.assertIn("Read repository files", trace_text)
        self.assertIn("I’m checking the relevant files.", trace_text)
        self.assertNotIn("private chain of thought", trace_text)
        self.assertNotIn("secret raw command output", trace_text)

        first_page_result = self.client_get(
            f"/json/pear/runs/{run.id}/trace?limit=1",
            intentionally_undocumented=True,
        )
        first_page = self.assert_json_success(first_page_result)
        self.assertEqual(len(first_page["trace"]), 1)
        self.assertEqual(first_page["trace"][0]["kind"], "assistant")
        self.assertTrue(first_page["has_more"])
        second_page_result = self.client_get(
            f"/json/pear/runs/{run.id}/trace?limit=1&before_event_id={first_page['next_before_event_id']}",
            intentionally_undocumented=True,
        )
        second_page = self.assert_json_success(second_page_result)
        self.assertEqual(len(second_page["trace"]), 1)
        self.assertEqual(second_page["trace"][0]["kind"], "command")
        self.assertFalse(second_page["has_more"])

        with (
            patch("zerver.views.pear.mark_pear_run_attended") as mark_attended,
            patch("zerver.views.pear.maybe_enqueue_pear_run_presentation") as enqueue,
        ):
            attention_result = self.client_post(
                f"/json/pear/runs/{run.id}/attention",
                intentionally_undocumented=True,
            )

        self.assertTrue(self.assert_json_success(attention_result)["attended"])
        mark_attended.assert_called_once_with(run.id)
        enqueue.assert_called_once()

        run.status = PearWorkerRun.DONE
        run.save(update_fields=["status"])
        settled_result = self.client_post(
            f"/json/pear/runs/{run.id}/attention",
            intentionally_undocumented=True,
        )
        self.assertFalse(self.assert_json_success(settled_result)["attended"])

    def test_not_for_pear_feedback_is_ambient_only_and_idempotent(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        ambient_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=915,
            result_message_id=916,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.RUNNING,
            ambient_triggered=True,
        )
        explicit_run = PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=917,
            result_message_id=918,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name="topic-a",
            status=PearWorkerRun.DONE,
        )
        self.login_user(owner)

        first_result = self.client_post(
            f"/json/pear/runs/{ambient_run.id}/not-for-pear",
            intentionally_undocumented=True,
        )
        first_payload = self.assert_json_success(first_result)
        self.assertTrue(first_payload["changed"])
        ambient_run.refresh_from_db()
        self.assertEqual(ambient_run.status, PearWorkerRun.CANCELLED)
        self.assertEqual(
            PearWorkerRunEvent.objects.get(
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
            f"/json/pear/runs/{ambient_run.id}/not-for-pear",
            intentionally_undocumented=True,
        )
        second_payload = self.assert_json_success(second_result)
        self.assertFalse(second_payload["changed"])
        self.assertEqual(
            PearWorkerRunEvent.objects.filter(
                run=ambient_run,
                event_type="routing_feedback",
            ).count(),
            1,
        )

        explicit_result = self.client_post(
            f"/json/pear/runs/{explicit_run.id}/not-for-pear",
            intentionally_undocumented=True,
        )
        self.assert_json_error(explicit_result, "This Pear response was explicitly requested.")

    def test_worker_run_store_sets_topic_response_policy(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            store = PearDatabaseWorkerRunStore()
            bot_handler = SimpleNamespace(user_profile=pear_bot)
            store.set_topic_response_policy(
                bot_handler,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                response_policy="ambient",
            )
            context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )

        self.assertEqual(context.response_policy, "ambient")
        self.assertEqual(
            store.topic_response_policy(
                bot_handler,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            ),
            "ambient",
        )

    def test_pear_codex_model_catalog_uses_visible_priority_and_reasoning(self) -> None:
        catalog = extract_pear_codex_model_catalog(
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

    def test_post_pear_topic_context_changes_model_without_posting(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        self.login_user(owner)
        last_message_id_before_change = Message.objects.latest("id").id

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_WORKER_MODEL="gpt-5.5",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            result = self.client_post(
                f"/json{PEAR_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "model": "gpt-5.5-mini",
                    "reasoning_effort": "high",
                },
                intentionally_undocumented=True,
            )
            model = pear_worker_model_for_topic(
                pear_bot,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )
            reasoning_effort = pear_worker_reasoning_effort_for_topic(
                pear_bot,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )
            workdir = pear_worker_workdir_for_topic(
                pear_bot,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )

            result_dict = self.assert_json_success(result)
            self.assertTrue(result_dict["changed"])
            self.assertEqual(result_dict["pear_topic_context"]["model"], "gpt-5.5-mini")
            self.assertEqual(result_dict["pear_topic_context"]["reasoning_effort"], "high")
            self.assertEqual(
                result_dict["pear_topic_context"]["response_policy"],
                "ambient",
            )
            self.assertEqual(model, "gpt-5.5-mini")
            self.assertEqual(reasoning_effort, "high")
            self.assertTrue(workdir.startswith(temp_dir))

        # Changing the runtime is a settings action, not a conversation:
        # no message may land in the topic.
        self.assertEqual(Message.objects.latest("id").id, last_message_id_before_change)

    def test_post_pear_topic_context_changes_response_policy_without_model(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_WORKER_MODEL="gpt-5.5",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            result = self.client_post(
                f"/json{PEAR_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "response_policy": "mentions_only",
                },
                intentionally_undocumented=True,
            )
            active_session_result = self.client_get(
                f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

            result_dict = self.assert_json_success(result)
            active_session_dict = self.assert_json_success(active_session_result)
            context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )

        self.assertTrue(result_dict["changed"])
        self.assertEqual(result_dict["pear_topic_context"]["response_policy"], "mentions_only")
        self.assertEqual(context.response_policy, "mentions_only")
        self.assertEqual(active_session_dict["active_session"]["response_policy"], "mentions_only")

    def test_side_session_uses_child_context_workdir_and_parent_model(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_WORKER_MODEL="gpt-5.5",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            model_result = self.client_post(
                f"/json{PEAR_TOPIC_CONTEXT_ENDPOINT}",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "model": "gpt-5.5-mini",
                    "reasoning_effort": "xhigh",
                },
                intentionally_undocumented=True,
            )
            self.assert_json_success(model_result)
            parent_context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )

            with patch("zerver.views.pear.PearQueueWorkerDispatcher.dispatch") as dispatch_mock:
                side_result = self.client_post(
                    f"/json{PEAR_SIDE_ENDPOINT}",
                    {
                        "stream": PEAR_DEFAULT_STREAM,
                        "topic": PEAR_DEFAULT_TOPIC,
                        "request": "check this privately",
                    },
                    intentionally_undocumented=True,
                )

            result_dict = self.assert_json_success(side_result)
            run = PearWorkerRun.objects.get(id=result_dict["pear_run"]["id"])
            side_context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name="direct messages",
                topic_name=run.topic_name,
            )

            self.assertEqual(run.source_stream_name, PEAR_DEFAULT_STREAM)
            self.assertEqual(run.source_topic_name, PEAR_DEFAULT_TOPIC)
            self.assertTrue(run.topic_name.startswith("side/"))
            self.assertEqual(side_context.parent_node_id, parent_context.id)
            self.assertEqual(run.model, "gpt-5.5-mini")
            self.assertEqual(
                pear_worker_reasoning_effort_for_topic(
                    pear_bot,
                    stream="direct messages",
                    topic=run.topic_name,
                ),
                "xhigh",
            )
            self.assertEqual(run.workdir, side_context.workdir)
            self.assertNotEqual(run.workdir, parent_context.workdir)
            self.assertTrue(run.workdir.startswith(temp_dir))
            self.assertEqual(dispatch_mock.call_args.kwargs["topic"], run.topic_name)

    def test_get_pear_active_session_without_stream(self) -> None:
        self.login("hamlet")

        result = self.client_get(
            f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
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
        self.assertIn("stream", active_session["worker_blocked_reason"])

    def test_get_pear_active_session_from_request_message(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, PEAR_DEFAULT_STREAM)
        request_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "@Pear please debug the flaky parser test.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
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

    def test_get_pear_active_session_uses_ambient_run_state(self) -> None:
        user = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            user,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.addCleanup(invalidate_pear_selected_bot_cache, user.realm_id)
        self.subscribe(user, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        request_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "In one sentence, explain what a hash map is.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        result_message_id = self.send_stream_message(
            pear_bot,
            PEAR_DEFAULT_STREAM,
            "A hash map stores key-value pairs for fast lookup.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        PearWorkerRun.objects.create(
            realm=user.realm,
            bot_profile=pear_bot,
            requesting_user=user,
            trigger_message_id=request_message_id,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name=PEAR_DEFAULT_TOPIC,
            status=PearWorkerRun.DONE,
            codex_thread_id="thread-ambient",
            ambient_triggered=True,
            result_message_id=result_message_id,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
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
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        first_message_id = self.send_stream_message(
            owner,
            PEAR_DEFAULT_STREAM,
            "agent, Choose a random 15 digit number.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        PearWorkerRun.objects.create(
            realm=owner.realm,
            bot_profile=pear_bot,
            requesting_user=owner,
            trigger_message_id=first_message_id,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name=PEAR_DEFAULT_TOPIC,
            status=PearWorkerRun.DONE,
            codex_thread_id="thread-first",
        )
        self.send_stream_message(
            pear_bot,
            PEAR_DEFAULT_STREAM,
            "I picked a number and retained it in the Codex thread.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        berry = self.example_user("othello")
        self.subscribe(berry, PEAR_DEFAULT_STREAM)
        self.send_stream_message(
            berry,
            PEAR_DEFAULT_STREAM,
            "The number should still be in the Codex thread.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        second_message_id = self.send_stream_message(
            owner,
            PEAR_DEFAULT_STREAM,
            "agent, what was the number you picked?",
            topic_name=PEAR_DEFAULT_TOPIC,
        )

        active_session = load_pear_active_session_from_zulip(
            {
                "id": second_message_id,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "agent, what was the number you picked?",
            },
            SimpleNamespace(user_profile=pear_bot),
        )

        self.assertEqual(active_session["latest_codex_thread_id"], "thread-first")
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

    def test_worker_loader_preserves_empty_topic_history(self) -> None:
        owner = self.example_user("hamlet")
        berry = self.example_user("othello")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.subscribe(berry, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        self.send_stream_message(
            berry,
            PEAR_DEFAULT_STREAM,
            "hello pear. what is occupancy?",
            topic_name="",
        )
        trigger_message_id = self.send_stream_message(
            owner,
            PEAR_DEFAULT_STREAM,
            "pear, did you hear what berry said?",
            topic_name="",
        )
        self.send_stream_message(
            berry,
            PEAR_DEFAULT_STREAM,
            "this message was posted after the trigger",
            topic_name="",
        )

        active_session = load_pear_active_session_from_zulip(
            {
                "id": trigger_message_id,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": "",
                "content": "pear, did you hear what berry said?",
                "sender_full_name": owner.full_name,
            },
            SimpleNamespace(user_profile=pear_bot),
        )

        self.assertEqual(active_session["topic"], "")
        self.assertEqual(
            [message["sender"] for message in active_session["recent_messages"]],
            [berry.full_name],
        )
        self.assertEqual(
            [message["content"] for message in active_session["recent_messages"]],
            [
                "hello pear. what is occupancy?",
            ],
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(
                PEAR_WORKER_MODEL="gpt-5.5",
                PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            ),
        ):
            self.assertEqual(
                pear_worker_model_for_topic(
                    pear_bot,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="",
                ),
                "gpt-5.5",
            )
            self.assertTrue(
                pear_worker_workdir_for_topic(
                    pear_bot,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="",
                ).startswith(temp_dir)
            )
            context = PearWorkspaceNode.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="",
            )
            assert context.project is not None
            self.assertEqual(context.project.repository_url, "")

            PearWorkerRun.objects.create(
                realm=owner.realm,
                bot_profile=pear_bot,
                requesting_user=owner,
                trigger_message_id=trigger_message_id,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="",
                status=PearWorkerRun.RUNNING,
            )
            self.assertEqual(
                PearDatabaseWorkerRunStore().active_worker_run_message_id(
                    SimpleNamespace(user_profile=pear_bot),
                    stream=PEAR_DEFAULT_STREAM,
                    topic="",
                ),
                trigger_message_id,
            )

    def test_get_pear_active_session_for_target_topic(self) -> None:
        user = self.example_user("hamlet")
        target_stream = "frontend"
        target_topic = "checkout/regression"
        self.subscribe(user, target_stream)
        self.subscribe(user, PEAR_DEFAULT_STREAM)
        request_message_id = self.send_stream_message(
            user,
            target_stream,
            "@Pear please debug the checkout regression.",
            topic_name=target_topic,
        )
        self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "@Pear this default session should not be counted.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
            {"stream": target_stream, "topic": target_topic},
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(result_dict["pear_session"]["stream"], target_stream)
        self.assertEqual(result_dict["pear_session"]["topic"], target_topic)
        self.assertEqual(active_session["stream"], target_stream)
        self.assertEqual(active_session["topic"], target_topic)
        self.assertEqual(active_session["state"], "requested")
        self.assertEqual(active_session["request_count"], 1)
        self.assertEqual(active_session["latest_request_message_id"], request_message_id)
        self.assertEqual(active_session["worker_state"], "ready")
        self.assertTrue(active_session["worker_can_run"])

    def test_get_pear_active_session_from_topic_messages(self) -> None:
        user = self.example_user("hamlet")
        self.subscribe(user, PEAR_DEFAULT_STREAM)
        self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "@**Pear** fix checkout latency.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "- [ ] Confirm repo, branch, and target runtime.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "Fold in the terminal status popup too.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        latest_request_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "@**Pear** Pause session:\n\nReason: waiting for CI",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        self.login_user(user)

        result = self.client_get(
            f"/json{PEAR_ACTIVE_SESSION_ENDPOINT}",
            intentionally_undocumented=True,
        )

        result_dict = self.assert_json_success(result)
        active_session = result_dict["active_session"]
        self.assertEqual(active_session["state"], "requested")
        self.assertEqual(active_session["source"], "server")
        self.assertEqual(active_session["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(active_session["topic"], PEAR_DEFAULT_TOPIC)
        self.assertEqual(active_session["topic_message_count"], 4)
        self.assertEqual(active_session["request_count"], 2)
        self.assertEqual(active_session["latest_request_message_id"], latest_request_message_id)
        self.assertIsNone(active_session["latest_message_id"])
        self.assertFalse(active_session["is_paused"])
        self.assertEqual(active_session["worker_state"], "ready")
        self.assertTrue(active_session["worker_can_run"])


class PearWorkspacePathTest(ZulipTestCase):
    def test_workspace_relative_path_guard(self) -> None:
        self.assertIsNone(pear_workspace_relative_path("/w", "../etc/passwd"))
        self.assertIsNone(pear_workspace_relative_path("/w", "/etc/passwd"))
        self.assertIsNone(pear_workspace_relative_path("/w", ".git/config"))
        self.assertIsNone(pear_workspace_relative_path("/w", "a/../.."))
        self.assertEqual(
            str(pear_workspace_relative_path("/w", "src/./main.py")),
            "/w/src/main.py",
        )


class PearImageEmbedTest(ZulipTestCase):
    def test_embed_workspace_images_uploads_and_appends(self) -> None:
        from zerver.lib.pear import pear_embed_workspace_images

        user = self.example_user("hamlet")
        with tempfile.TemporaryDirectory() as temp_dir:
            results = Path(temp_dir) / "results"
            results.mkdir()
            png = results / "training_curve.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            content = "The plot is at `results/training_curve.png` in the worktree."
            embedded = pear_embed_workspace_images(
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
        from zerver.lib.pear import pear_embed_workspace_images

        user = self.example_user("hamlet")
        with tempfile.TemporaryDirectory() as temp_dir:
            content = "See `../../etc/secret.png` and `missing.png`."
            embedded = pear_embed_workspace_images(
                content,
                workdir=temp_dir,
                isolation_username=None,
                bot_profile=user,
            )
        self.assertEqual(embedded, content)


class PearTaskMarkerNormalizationTest(ZulipTestCase):
    def test_normalize_pear_task_markers(self) -> None:
        from zerver.lib.pear import normalize_pear_task_markers

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
        normalized = normalize_pear_task_markers(content)
        self.assertIn("- \u2610 keep it standalone", normalized)
        self.assertIn("- \u2611 improve the visuals", normalized)
        self.assertIn("1. \u2611 numbered done item", normalized)
        self.assertIn("- [ ] not a checklist, code", normalized)
        self.assertIn("`- [ ] code`", normalized)
        # Idempotent: a second pass changes nothing.
        self.assertEqual(normalize_pear_task_markers(normalized), normalized)

    def test_normalize_pear_task_markers_leaves_plain_text(self) -> None:
        from zerver.lib.pear import normalize_pear_task_markers

        content = "An array literal [ ] in prose stays untouched.\n"
        self.assertEqual(normalize_pear_task_markers(content), content)


class PearMathNormalizationTest(ZulipTestCase):
    def test_normalize_pear_math_delimiters(self) -> None:
        from zerver.lib.pear import normalize_pear_math_delimiters

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
        normalized = normalize_pear_math_delimiters(content)
        # Single-line display math renders as $$...$$ so it survives
        # list nesting; only multi-line bodies become fences.
        self.assertIn("$$g_i = (u_i, v_i)$$", normalized)
        multiline = normalize_pear_math_delimiters("\\[\nx = 1\\\\\ny = 2\n\\]")
        self.assertIn("```math", multiline)
        self.assertIn("$$u_i$$", normalized)
        # Fenced code and inline code spans stay untouched.
        self.assertIn('x = "\\\\[not math\\\\]"', normalized)
        self.assertIn("`\\\\(inline code\\\\)`", normalized)
        # Idempotent, and a no-op for plain content.
        self.assertEqual(normalize_pear_math_delimiters(normalized), normalized)
        self.assertEqual(normalize_pear_math_delimiters("plain"), "plain")


class PearWorkspaceMapTest(ZulipTestCase):
    def test_workspace_topics_endpoint_lists_active_worktrees(self) -> None:
        user = self.example_user("hamlet")
        self.login_user(user)
        stream = self.subscribe(user, "workmap")
        project = PearProject.objects.create(realm=user.realm, stream=stream)
        PearWorkspaceNode.objects.create(
            realm=user.realm,
            project=project,
            stream_name=stream.name,
            topic_name="feature-a",
            branch_name="feature-a",
        )
        PearWorkspaceNode.objects.create(
            realm=user.realm,
            project=project,
            stream_name=stream.name,
            topic_name="retired",
            branch_name="retired",
            lifecycle_status=PearWorkspaceNode.ARCHIVED,
        )

        result = self.client_get("/json/pear/workspace-topics", {"stream": stream.name})
        payload = self.assert_json_success(result)["pear_workspace_topics"]
        self.assertEqual(payload, [{"topic": "feature-a", "branch": "feature-a"}])

    def test_workspace_topics_requires_stream_access(self) -> None:
        iago = self.example_user("iago")
        self.make_stream("sekrit", invite_only=True)
        self.subscribe(iago, "sekrit")
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get("/json/pear/workspace-topics", {"stream": "sekrit"})
        self.assert_json_error(result, "Invalid channel name 'sekrit'")

    def test_register_reports_accessible_pear_project_stream_ids(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "workmap")
        PearProject.objects.create(realm=user.realm, stream=stream)
        private_stream = self.make_stream("sekrit", invite_only=True)
        PearProject.objects.create(realm=user.realm, stream=private_stream)

        state = fetch_initial_state_data(user, realm=user.realm)
        self.assertIn(stream.id, state["pear_project_stream_ids"])
        # Bindings on channels the user cannot see stay invisible.
        self.assertNotIn(private_stream.id, state["pear_project_stream_ids"])


class PearMonitorTest(ZulipTestCase):
    def create_pear_bot(self, owner: UserProfile) -> UserProfile:
        return self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )

    def create_live_monitor(
        self, owner: UserProfile, pear_bot: UserProfile, **overrides: object
    ) -> PearMonitor:
        fields: dict[str, object] = dict(
            realm=owner.realm,
            bot_profile=pear_bot,
            creator=owner,
            stream_name=PEAR_DEFAULT_STREAM,
            topic_name=PEAR_DEFAULT_TOPIC,
            title="loader",
            ask="training loader",
            status=PearMonitor.LIVE,
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
        return PearMonitor.objects.create(**fields)

    def test_create_monitor_queues_setup_run(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_pear_bot(owner)
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.subscribe(pear_bot, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(PEAR_WORKER_MODEL="gpt-5.5", PEAR_TOPIC_WORKDIR_ROOT=temp_dir),
            patch("zerver.views.pear.PearQueueWorkerDispatcher.dispatch") as dispatch_mock,
        ):
            result = self.client_post(
                "/json/pear/monitors",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "request": "training loader",
                },
                intentionally_undocumented=True,
            )
            result_dict = self.assert_json_success(result)
            monitor = PearMonitor.objects.get(id=result_dict["pear_monitor"]["id"])
            self.assertEqual(monitor.status, PearMonitor.PROVISIONING)
            self.assertEqual(monitor.ask, "training loader")
            self.assertIsNotNone(monitor.anchor_message_id)

            run = PearWorkerRun.objects.get(
                bot_profile=pear_bot, trigger_message_id=monitor.anchor_message_id
            )
            self.assertEqual(run.session_kind, PearWorkerRun.MONITOR)
            kwargs = dispatch_mock.call_args.kwargs
            self.assertEqual(kwargs["session_kind"], PearWorkerRun.MONITOR)
            self.assertIn("training loader", kwargs["content"])
            self.assertIn(pear_monitor_spec_relative_path(monitor.id), kwargs["content"])

            # Setup runs are DM-anchored and never appear in the topic's
            # runs list: the wall itself is the provisioning surface.
            runs_result = self.client_get(
                f"/json{PEAR_RUNS_ENDPOINT}",
                {"stream": PEAR_DEFAULT_STREAM, "topic": PEAR_DEFAULT_TOPIC},
                intentionally_undocumented=True,
            )
            runs_dict = self.assert_json_success(runs_result)
            self.assertNotIn(run.id, [r["id"] for r in runs_dict["pear_runs"]])

    def test_create_monitor_enforces_topic_limit(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_pear_bot(owner)
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)
        for index in range(PEAR_MONITOR_MAX_PER_TOPIC):
            self.create_live_monitor(owner, pear_bot, title=f"m{index}")
        result = self.client_post(
            "/json/pear/monitors",
            {
                "stream": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "request": "one too many",
            },
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "This topic already has the maximum number of monitors.")

    def test_monitor_actions_and_delete(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_pear_bot(owner)
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)
        monitor = self.create_live_monitor(owner, pear_bot)

        result = self.client_patch(
            f"/json/pear/monitors/{monitor.id}",
            {"action": "pause"},
            intentionally_undocumented=True,
        )
        self.assertEqual(self.assert_json_success(result)["pear_monitor"]["status"], "paused")

        result = self.client_patch(
            f"/json/pear/monitors/{monitor.id}",
            {"action": "cadence", "cadence_seconds": "2"},
            intentionally_undocumented=True,
        )
        # Cadence clamps to the 5s floor.
        self.assertEqual(self.assert_json_success(result)["pear_monitor"]["cadence_seconds"], 5)

        result = self.client_patch(
            f"/json/pear/monitors/{monitor.id}",
            {"action": "rename", "title": "gpu loss"},
            intentionally_undocumented=True,
        )
        self.assertEqual(self.assert_json_success(result)["pear_monitor"]["title"], "gpu loss")

        result = self.client_patch(
            f"/json/pear/monitors/{monitor.id}",
            {"action": "resume"},
            intentionally_undocumented=True,
        )
        self.assertEqual(self.assert_json_success(result)["pear_monitor"]["status"], "live")

        result = self.client_patch(
            f"/json/pear/monitors/{monitor.id}",
            {"action": "sabotage"},
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "Unknown monitor action.")

        result = self.client_delete(
            f"/json/pear/monitors/{monitor.id}",
            intentionally_undocumented=True,
        )
        self.assert_json_success(result)
        self.assertFalse(PearMonitor.objects.filter(id=monitor.id).exists())

    def test_monitor_list_requires_stream_access(self) -> None:
        owner = self.example_user("hamlet")
        outsider = self.example_user("cordelia")
        pear_bot = self.create_pear_bot(owner)
        self.make_stream("pear-private", invite_only=True)
        self.subscribe(owner, "pear-private")
        monitor = self.create_live_monitor(owner, pear_bot, stream_name="pear-private")
        self.login_user(outsider)
        result = self.client_get(
            "/json/pear/monitors",
            {"stream": "pear-private", "topic": PEAR_DEFAULT_TOPIC},
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "Invalid channel name 'pear-private'")
        result = self.client_delete(
            f"/json/pear/monitors/{monitor.id}",
            intentionally_undocumented=True,
        )
        self.assert_json_error(result, "Invalid channel name 'pear-private'")
        self.assertTrue(PearMonitor.objects.filter(id=monitor.id).exists())

    def test_validate_monitor_spec(self) -> None:
        good = {
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
        spec = pear_validate_monitor_spec(good)
        self.assertEqual(spec["cadence_seconds"], 15)
        self.assertEqual(spec["title"], "training loader")
        self.assertEqual(len(spec["artifact_spec"]["blocks"]), 2)

        explainer = pear_validate_monitor_spec(
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

        with self.assertRaisesRegex(PearMonitorSpecError, "Invalid artifact"):
            pear_validate_monitor_spec(
                {
                    **good,
                    "artifact": {"version": 1, "blocks": [{"type": "hologram"}]},
                }
            )
        with self.assertRaisesRegex(PearMonitorSpecError, "Invalid artifact"):
            pear_validate_monitor_spec(
                {
                    **good,
                    "artifact": {
                        "version": 1,
                        "blocks": [{"type": "metric", "label": "x", "value": 1, "style": {}}],
                    },
                }
            )
        with self.assertRaisesRegex(PearMonitorSpecError, "Bad binding source"):
            pear_validate_monitor_spec(
                {
                    **good,
                    "recipe": {
                        **good["recipe"],
                        "bindings": {"data.loss": "exec:rm -rf /"},
                    },
                }
            )
        with self.assertRaisesRegex(PearMonitorSpecError, "Invalid regex"):
            pear_validate_monitor_spec(
                {
                    **good,
                    "recipe": {**good["recipe"], "parse": {"mode": "regex", "pattern": "("}},
                }
            )
        with self.assertRaisesRegex(PearMonitorSpecError, "Invalid artifact"):
            pear_validate_monitor_spec(
                {
                    "artifact": {"version": 1, "blocks": [{"type": "svg", "markup": "x"}]},
                }
            )
        with self.assertRaisesRegex(PearMonitorSpecError, "Invalid artifact"):
            pear_validate_monitor_spec(
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
        bound = pear_monitor_bind_data(regex_recipe, "loss=0.91 step=10\nloss=0.42 step=57\n")
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
        bound = pear_monitor_bind_data(json_recipe, '{"credits": {"remaining": 41.5}}')
        self.assertEqual(bound["remaining"], 41.5)
        self.assertEqual(bound["provider"], "lambda")

        text_recipe = {
            "parse": {"mode": "text"},
            "bindings": {"data.lines": "lines:2", "data.raw": "text"},
        }
        bound = pear_monitor_bind_data(text_recipe, "one\ntwo\nthree")
        self.assertEqual(bound["lines"], ["two", "three"])
        self.assertEqual(bound["raw"], "one\ntwo\nthree")

    def test_monitor_tick_success_and_failure(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_pear_bot(owner)
        monitor = self.create_live_monitor(owner, pear_bot)
        process_isolation = PearProcessIsolation(
            username="pear-test",
            home="/tmp/pear-test",  # noqa: S108
            codex_home="/tmp/pear-test/.codex",  # noqa: S108
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "zerver.lib.pear_monitor.pear_monitor_run_recipe_command",
                return_value="value=0.5",
            ):
                pear_monitor_apply_tick(
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
                "zerver.lib.pear_monitor.pear_monitor_run_recipe_command",
                side_effect=OSError("recipe exited 1: failed"),
            ):
                for _ in range(PEAR_MONITOR_FAILURE_LIMIT):
                    pear_monitor_apply_tick(
                        monitor,
                        workdir=temp_dir,
                        process_isolation=process_isolation,
                    )
                    monitor.refresh_from_db()
            self.assertEqual(monitor.status, PearMonitor.ERROR)
            self.assertIn("recipe exited", monitor.last_error)
            # The sample from the good ticks is preserved on error.
            self.assertEqual(monitor.version, 1)

    def test_executable_monitor_recipe_requires_process_isolation(self) -> None:
        with self.assertRaisesRegex(OSError, "require the topic's isolated OS user"):
            pear_monitor_run_recipe_command(
                "echo 1",
                workdir="/tmp",  # noqa: S108
                process_isolation=None,
            )
        isolation = PearProcessIsolation(
            username="pear-test",
            home="/home/pear-test",
            codex_home="/home/pear-test/.codex",
        )
        with patch("zerver.lib.pear_monitor.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="1\n", stderr="")
            self.assertEqual(
                pear_monitor_run_recipe_command(
                    "echo 1",
                    workdir="/tmp/topic",  # noqa: S108
                    process_isolation=isolation,
                ),
                "1\n",
            )
        self.assertEqual(run.call_args.args[0][:6], ["sudo", "-n", "-H", "-u", "pear-test", "env"])

    def test_apply_monitor_setup_result(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_pear_bot(owner)
        monitor = self.create_live_monitor(
            owner,
            pear_bot,
            status=PearMonitor.PROVISIONING,
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
            spec_path = Path(temp_dir) / pear_monitor_spec_relative_path(monitor.id)
            spec_path.parent.mkdir(parents=True)
            spec_path.write_text(orjson.dumps(spec).decode())
            pear_apply_monitor_setup_result(
                pear_bot, 987654, workdir=temp_dir, isolation_username=None
            )
            monitor.refresh_from_db()
            self.assertEqual(monitor.status, PearMonitor.LIVE)
            self.assertEqual(monitor.title, "gpu credits")
            self.assertEqual(monitor.cadence_seconds, 30)
            self.assertEqual(monitor.recipe["command"], "echo 41")
            self.assertIsNotNone(monitor.next_due_at)

        # A run whose agent never wrote the file settles to error.
        monitor.status = PearMonitor.PROVISIONING
        monitor.save(update_fields=["status"])
        with tempfile.TemporaryDirectory() as temp_dir:
            pear_apply_monitor_setup_result(
                pear_bot, 987654, workdir=temp_dir, isolation_username=None
            )
        monitor.refresh_from_db()
        self.assertEqual(monitor.status, PearMonitor.ERROR)
        self.assertIn("did not write the spec file", monitor.last_error)

    def test_monitor_dispatch_routes_to_monitor_queue(self) -> None:
        owner = self.example_user("hamlet")
        self.create_pear_bot(owner)
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.settings(PEAR_WORKER_MODEL="gpt-5.5", PEAR_TOPIC_WORKDIR_ROOT=temp_dir),
            patch("zerver.lib.pear_worker.queue_json_publish_rollback_unsafe") as publish_mock,
        ):
            result = self.client_post(
                "/json/pear/monitors",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "request": "training loader",
                },
                intentionally_undocumented=True,
            )
            self.assert_json_success(result)
            queue_name, event = publish_mock.call_args.args
            # Monitor turns ride their own consumer so a provisioning
            # wall block never queues ahead of a chat run.
            self.assertEqual(queue_name, "pear_monitor_worker")
            self.assertEqual(event["session_kind"], PearWorkerRun.MONITOR)

    def test_spec_direct_monitor_creation_pins_without_agent_run(self) -> None:
        owner = self.example_user("hamlet")
        self.create_pear_bot(owner)
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
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
        runs_before = PearWorkerRun.objects.count()
        messages_before = Message.objects.count()
        with patch("zerver.views.pear.PearQueueWorkerDispatcher.dispatch") as dispatch_mock:
            result = self.client_post(
                "/json/pear/monitors",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "request": "pinned from chat",
                    "spec": orjson.dumps(spec).decode(),
                },
                intentionally_undocumented=True,
            )
        result_dict = self.assert_json_success(result)
        monitor = PearMonitor.objects.get(id=result_dict["pear_monitor"]["id"])
        # Pinning is instant: live monitor, no setup run, no DM anchor.
        self.assertEqual(monitor.status, PearMonitor.LIVE)
        self.assertEqual(monitor.title, "gpu credits")
        self.assertIsNotNone(monitor.next_due_at)
        self.assertIsNone(monitor.anchor_message_id)
        dispatch_mock.assert_not_called()
        self.assertEqual(PearWorkerRun.objects.count(), runs_before)
        self.assertEqual(Message.objects.count(), messages_before)

    def test_spec_direct_monitor_creation_rejects_bad_specs(self) -> None:
        owner = self.example_user("hamlet")
        self.create_pear_bot(owner)
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)
        bad_spec = {
            "title": "nope",
            "artifact": {"version": 1, "blocks": [{"type": "hologram"}]},
            "recipe": {"command": "", "parse": {"mode": "static"}, "bindings": {}},
        }
        result = self.client_post(
            "/json/pear/monitors",
            {
                "stream": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "request": "pinned from chat",
                "spec": orjson.dumps(bad_spec).decode(),
            },
            intentionally_undocumented=True,
        )
        self.assert_json_error_contains(result, "Invalid artifact spec")
        self.assertFalse(PearMonitor.objects.exists())
