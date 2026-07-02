import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import orjson
from django.utils.timezone import now as timezone_now

from zerver.lib.pear import (
    PEAR_ACTIVE_SESSION_ENDPOINT,
    PEAR_AGENT_RUNTIME_PACKAGES,
    PEAR_CODEX_MCP_TOOLS,
    PEAR_CONFIG_ENDPOINT,
    PEAR_DEFAULT_STREAM,
    PEAR_DEFAULT_TOPIC,
    PEAR_EMBEDDED_BOT_SERVICE,
    PEAR_OFFICIAL_AGENT_RUNTIME,
    PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
    PEAR_OFFICIAL_CODEX_SDK_PACKAGES,
    PEAR_SESSION_CHECKLIST,
    PEAR_SESSION_ENDPOINT,
    PEAR_SIDE_ENDPOINT,
    PEAR_TOPIC_CONTEXT_ENDPOINT,
    build_pear_active_session_summary_from_messages,
    get_pear_session_payload,
    write_pear_live_run_state,
)
from zerver.lib.pear_worker import (
    PearDatabaseWorkerRunStore,
    build_pear_workspace_context,
    build_pear_worker_runtime_from_settings,
    clone_or_update_pear_repository_workdir,
    configure_pear_channel_repository_context,
    ensure_pear_topic_context,
    ensure_pear_topic_workdir,
    invalidate_pear_git_status_delta,
    load_pear_active_session_from_zulip,
    maybe_refactor_pear_stream_contexts_on_rename,
    maybe_refactor_pear_topic_context_on_move,
    pear_expected_topic_workdir,
    pear_git_status_delta,
    pear_topic_workdir_root,
    pear_topic_process_isolation,
    pear_worker_process_isolation_error,
    pear_worker_runtime_isolation_error,
    pear_worker_model_for_topic,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_sandbox_for_topic,
    pear_worker_workdir_for_topic,
    sync_pear_topic_workdir_overlay,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, UserProfile
from zerver.models.bots import PearTopicContext, PearWorkerRun, PearWorkerRunEvent
from zerver.models.streams import get_stream
from zerver.views.pear import (
    build_pear_git_context,
    extract_pear_codex_model_catalog,
    serialize_pear_topic_context,
    serialize_pear_worker_run,
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
        self.assertEqual(pear_session["checklist"], list(PEAR_SESSION_CHECKLIST))
        self.assertIn("@**Pear**", pear_session["seed_message"])
        self.assertIn("Steering update:", pear_session["steer_message"])
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
        self.assertIn("What can you access", pear_session["access_message"])
        self.assertIn("Pause session:", pear_session["pause_message"])
        self.assertIn("Resume session:", pear_session["resume_message"])
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
        with self.settings(
            EMBEDDED_BOTS_ENABLED=True,
            PEAR_WORKER_AGENT_RUNTIME="codex-agents-mcp",
        ), patch(
            "zerver.views.pear.pear_worker_runtime_dependency_status",
            return_value=(
                True,
                "codex mcp-server via /usr/local/bin/codex; openai-agents installed.",
            ),
        ), patch(
            "zerver.views.pear.pear_worker_runtime_auth_status",
            return_value=(
                True,
                "OpenAI Agents SDK credentials present; Codex CLI is logged in.",
                "codex-cli-and-openai-api-key",
                "codex login",
                "worker-environment",
                "Run Codex CLI login inside the worker environment and provide "
                "OPENAI_API_KEY or OPENAI_ADMIN_KEY for the OpenAI Agents SDK.",
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
        self.assertEqual(pear_config["worker_agent_runtime"], "codex-agents-mcp")
        self.assertTrue(pear_config["worker_runtime_ready"])
        self.assertTrue(pear_config["worker_runtime_auth_ready"])
        self.assertEqual(
            pear_config["worker_runtime_login_method"], "codex-cli-and-openai-api-key"
        )
        self.assertEqual(pear_config["worker_runtime_login_command"], "codex login")
        self.assertEqual(pear_config["worker_runtime_login_target"], "worker-environment")
        self.assertIn("OPENAI_API_KEY", pear_config["worker_runtime_login_instructions"])
        self.assertEqual(
            pear_config["setup_steps"][2]["detail"],
            "codex mcp-server via /usr/local/bin/codex; openai-agents installed.",
        )
        self.assertEqual(
            pear_config["setup_steps"][3]["detail"],
            "OpenAI Agents SDK credentials present; Codex CLI is logged in.",
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
        with self.settings(
            EMBEDDED_BOTS_ENABLED=True,
            PEAR_WORKER_AGENT_RUNTIME="codex-agents-mcp",
        ), patch(
            "zerver.views.pear.pear_worker_runtime_dependency_status",
            return_value=(False, "Missing openai-agents package."),
        ), patch(
            "zerver.views.pear.pear_worker_runtime_auth_status",
            return_value=(
                True,
                "Codex CLI is logged in.",
                "codex-cli",
                "codex login",
                "worker-environment",
                "Run Codex CLI login inside the worker environment used by Pear.",
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
        self.assertEqual(pear_config["worker_agent_runtime"], "codex-agents-mcp")
        self.assertFalse(pear_config["worker_runtime_ready"])
        self.assertEqual(
            pear_config["setup_steps"][2],
            {
                "key": "worker_runtime",
                "label": "Worker runtime dependencies",
                "complete": False,
                "detail": "Missing openai-agents package.",
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

        with patch("zerver.views.pear.which", return_value="/usr/local/bin/codex"), patch(
            "zerver.views.pear.pear_python_module_available",
            side_effect=lambda module_name: module_name in {"agents", "agents.mcp"},
        ):
            self.assertEqual(
                pear_worker_runtime_dependency_status("codex-agents-mcp"),
                (
                    True,
                    "codex mcp-server via /usr/local/bin/codex; openai-agents installed.",
                ),
            )

        with patch("zerver.views.pear.which", return_value=None), patch(
            "zerver.views.pear.pear_python_module_available",
            return_value=False,
        ):
            available, detail = pear_worker_runtime_dependency_status("codex-agents-mcp")
            self.assertFalse(available)
            self.assertIn("codex executable", detail)
            self.assertIn("openai-agents package", detail)

        with patch("zerver.views.pear.pear_python_module_available", return_value=True):
            self.assertEqual(
                pear_worker_runtime_dependency_status("codex-sdk"),
                (True, "openai-codex package installed."),
            )

    def test_worker_runtime_auth_status_reports_login_paths(self) -> None:
        from zerver.views.pear import pear_worker_runtime_auth_status

        with patch(
            "zerver.views.pear.pear_codex_login_available",
            return_value=True,
        ), patch(
            "zerver.views.pear.pear_codex_auth_file_available",
            return_value=True,
        ), patch(
            "zerver.views.pear.pear_openai_agents_auth_available",
            return_value=False,
        ):
            (
                available,
                detail,
                login_method,
                login_command,
                login_target,
                login_instructions,
            ) = pear_worker_runtime_auth_status("codex-exec")
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
            self.assertIn("OpenAI Agents SDK credentials", detail)
            self.assertEqual(login_method, "codex-cli-and-openai-api-key")
            self.assertEqual(login_command, "codex login")
            self.assertEqual(login_target, "worker-environment")
            self.assertIn("OPENAI_API_KEY", login_instructions)

        with patch(
            "zerver.views.pear.pear_codex_login_available",
            return_value=True,
        ), patch(
            "zerver.views.pear.pear_codex_auth_file_available",
            return_value=True,
        ), patch(
            "zerver.views.pear.pear_openai_agents_auth_available",
            return_value=True,
        ):
            self.assertEqual(
                pear_worker_runtime_auth_status("codex-agents-mcp"),
                (
                    True,
                    "OpenAI Agents SDK credentials present; Codex CLI is logged in.",
                    "codex-cli-and-openai-api-key",
                    "codex login",
                    "worker-environment",
                    "Run Codex CLI login inside the worker environment and provide "
                    "OPENAI_API_KEY or OPENAI_ADMIN_KEY for the OpenAI Agents SDK.",
                ),
            )

    def test_serialize_pear_worker_run_includes_usage_summary(self) -> None:
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
            content="Streaming answer",
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
        self.assertEqual(live["content"], "Streaming answer")
        self.assertIsInstance(live["updated_at"], float)

    def test_post_pear_config_creates_bot(self) -> None:
        owner = self.example_user("iago")
        self.login_user(owner)

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

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_WORKER_MODEL="gpt-5.5",
            PEAR_WORKER_MODEL_CHOICES=["gpt-5.5", "gpt-5.5-mini"],
            PEAR_WORKER_WORKDIR="/workspace/base",
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
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
            self.assertEqual(context_payload["workdir"], "/workspace/base")
            self.assertFalse(
                PearTopicContext.objects.filter(
                    realm=owner.realm,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name=PEAR_DEFAULT_TOPIC,
                ).exists()
            )

    def test_existing_base_workdir_topic_context_is_repaired(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = str(Path(temp_dir) / "base")
            topic_root = str(Path(temp_dir) / "topics")
            PearTopicContext.objects.create(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                workdir=base_workdir,
            )

            with self.settings(
                PEAR_WORKER_WORKDIR=base_workdir,
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
            ):
                context = ensure_pear_topic_context(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic=PEAR_DEFAULT_TOPIC,
                )

            self.assertNotEqual(context.workdir, base_workdir)
            self.assertTrue(context.workdir.startswith(topic_root))
            self.assertTrue(Path(context.workdir).exists())

    def test_topic_workdir_is_detached_worktree_when_created(self) -> None:
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
                context = ensure_pear_topic_context(
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
            self.assertEqual(completed.stdout.strip(), "")
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

    def test_topic_worker_sandbox_downgrades_full_access_to_workspace_write(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_root = str(Path(temp_dir) / "topics")
            self.create_git_repository(base_workdir)

            with self.settings(
                PEAR_WORKER_AGENT_RUNTIME="codex-exec",
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
                PEAR_TOPIC_ISOLATION_BACKEND="codex-sandbox",
                PEAR_WORKER_SANDBOX="danger-full-access",
            ):
                context = ensure_pear_topic_context(
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
                PEAR_WORKER_AGENT_RUNTIME="codex-exec",
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=topic_root,
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
                PEAR_WORKER_SANDBOX="workspace-write",
            ):
                context = ensure_pear_topic_context(
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
                PEAR_WORKER_AGENT_RUNTIME="codex-exec",
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

            self.assertEqual(tool_results[0], {"label": "Runtime", "value": "disabled"})
            self.assertIn("outside the Pear topic workspace root", tool_results[1]["value"])

    def test_os_user_topic_context_uses_independent_repository_clone(self) -> None:
        owner = self.example_user("hamlet")
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"
        cloned_paths: list[Path] = []

        def fake_clone(_repository_url: str, workdir: Path) -> None:
            cloned_paths.append(workdir)
            workdir.mkdir(parents=True)
            (workdir / ".git").mkdir()

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            PEAR_TOPIC_ISOLATION_BACKEND="os-user",
        ), patch(
            "zerver.lib.pear_worker.clone_or_update_pear_repository_workdir",
            side_effect=fake_clone,
        ):
            channel_context = configure_pear_channel_repository_context(
                owner,
                stream="megatron-lm",
                repository_url=repository_url,
            )
            topic_context = ensure_pear_topic_context(
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

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            PEAR_TOPIC_ISOLATION_HELPER="/usr/local/sbin/pear-topic-isolate",
        ):
            workdir = str(Path(temp_dir) / "topic")
            Path(workdir).mkdir()
            process_isolation = pear_topic_process_isolation(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="isolated topic",
            )
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("zerver.lib.pear_worker.subprocess.run", side_effect=fake_run):
                error = pear_worker_process_isolation_error(
                    process_isolation,
                    workdir=workdir,
                )

        assert process_isolation is not None
        self.assertIsNone(error)
        self.assertEqual(calls[0][0][:6], ["sudo", "-n", "/usr/local/sbin/pear-topic-isolate", "--user", process_isolation.username, "--workdir"])
        self.assertIn("--topic-root", calls[0][0])
        self.assertIn(temp_dir, calls[0][0])

    def test_os_user_process_isolation_tolerates_private_prepared_workdir(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            PEAR_TOPIC_ISOLATION_HELPER="/usr/local/sbin/pear-topic-isolate",
        ):
            workdir = Path(temp_dir) / "private-topic"
            workdir.mkdir()
            process_isolation = pear_topic_process_isolation(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="private topic",
            )
            with patch(
                "zerver.lib.pear_worker.Path.is_file",
                side_effect=PermissionError,
            ), patch(
                "zerver.lib.pear_worker.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ) as run_mock:
                error = pear_worker_process_isolation_error(
                    process_isolation,
                    workdir=str(workdir),
                )

        self.assertIsNone(error)
        run_mock.assert_called_once()

    def test_os_user_process_isolation_refuses_shared_git_worktree(self) -> None:
        owner = self.example_user("hamlet")

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            PEAR_TOPIC_ISOLATION_BACKEND="os-user",
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
                context = ensure_pear_topic_context(
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

            with self.settings(
                PEAR_WORKER_WORKDIR=str(base_workdir),
                PEAR_TOPIC_WORKDIR_ROOT=str(unsafe_topic_root),
                PEAR_TOPIC_ISOLATION_BACKEND="os-user",
            ), patch("zerver.lib.pear_worker.Path.home", return_value=service_home):
                self.assertEqual(pear_topic_workdir_root(), Path("/srv/pear-workspaces"))

    def test_os_user_topic_workdir_initializes_independent_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
            PEAR_TOPIC_ISOLATION_BACKEND="os-user",
        ):
            workdir = Path(temp_dir) / "topic"
            ensure_pear_topic_workdir(str(workdir))

            self.assertTrue((workdir / ".git").is_dir())

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

    def test_pear_git_status_delta_uses_cache_until_invalidated(self) -> None:
        invalidate_pear_git_status_delta()
        clean_status = "\n".join(
            [
                "# branch.oid 1234567890abcdef",
                "# branch.head topic-a",
                "# branch.upstream origin/topic-a",
                "# branch.ab +2 -1",
            ]
        )
        dirty_status = "\n".join([clean_status, "1 .M N... 100644 100644 100644 a b file.txt"])

        with patch(
            "zerver.lib.pear_worker.pear_git_output",
            side_effect=[clean_status, dirty_status],
        ) as git_output:
            clean_delta = pear_git_status_delta("/repo", topic="topic-a")
            cached_delta = pear_git_status_delta("/repo", topic="topic-a")
            invalidate_pear_git_status_delta("/repo")
            dirty_delta = pear_git_status_delta("/repo", topic="topic-a")

        self.assertEqual(git_output.call_count, 2)
        self.assertEqual(clean_delta["head"], "1234567")
        self.assertEqual(clean_delta["ahead"], 2)
        self.assertEqual(clean_delta["behind"], 1)
        self.assertTrue(clean_delta["branch_matches_topic"])
        self.assertFalse(clean_delta["dirty"])
        self.assertEqual(cached_delta, clean_delta)
        self.assertTrue(dirty_delta["dirty"])
        self.assertEqual(dirty_delta["changed_count"], 1)

    def test_pear_git_status_delta_runs_one_porcelain_command(self) -> None:
        invalidate_pear_git_status_delta()
        status_output = "\n".join(
            [
                "# branch.oid 1234567890abcdef",
                "# branch.head topic-a",
                "# branch.ab +0 -0",
                "? new.txt",
            ]
        )
        completed = subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=status_output,
            stderr="",
        )
        with patch("zerver.lib.pear_worker.subprocess.run", return_value=completed) as run_mock:
            delta = pear_git_status_delta("/repo", topic="topic-b")

        run_mock.assert_called_once()
        self.assertTrue(delta["dirty"])
        self.assertEqual(delta["changed_count"], 1)
        self.assertFalse(delta["branch_matches_topic"])

    def test_get_pear_git_tree_returns_on_demand_graph(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)
        self.login_user(owner)

        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "topic"
            self.create_git_repository(workdir)
            (workdir / "notes.txt").write_text("dirty\n", encoding="utf-8")
            PearTopicContext.objects.create(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                workdir=str(workdir),
            )

            result = self.client_get(
                "/json/pear/git-tree",
                {
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                intentionally_undocumented=True,
            )

        git_tree = self.assert_json_success(result)["git_tree"]
        self.assertTrue(git_tree["available"])
        self.assertIn("*", git_tree["graph"])
        self.assertIn("initial", git_tree["graph"])
        self.assertIn("?? notes.txt", git_tree["status_short"])
        self.assertTrue(git_tree["delta"]["dirty"])

    def test_workspace_context_reports_local_topic_repo_without_external_pin(self) -> None:
        owner = self.example_user("hamlet")
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_WORKER_WORKDIR=str(Path(temp_dir) / "base"),
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
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
            context = PearTopicContext.objects.create(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
                workdir=str(workdir),
            )

            workspace_context = build_pear_workspace_context(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )

            self.assertEqual(workspace_context["repository"], PEAR_DEFAULT_STREAM)
            self.assertEqual(
                workspace_context["remote_url"],
                "https://github.com/zulip/zulip.git",
            )
            self.assertEqual(workspace_context["worktree"], PEAR_DEFAULT_TOPIC)
            topic_payload = serialize_pear_topic_context(
                context,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )
            self.assertEqual(topic_payload["github_repo"], "")
            self.assertEqual(topic_payload["repo_label"], PEAR_DEFAULT_STREAM)
            self.assertEqual(topic_payload["worktree_label"], PEAR_DEFAULT_TOPIC)

            context.github_repo = "zulip/zulip"
            context.save(update_fields=["github_repo", "date_updated"])

            workspace_context = build_pear_workspace_context(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )

            self.assertEqual(workspace_context["repository"], "zulip/zulip")
            self.assertEqual(
                workspace_context["remote_url"],
                "https://github.com/zulip/zulip.git",
            )

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
                context = ensure_pear_topic_context(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="clean-topic",
                )

            self.assertFalse((Path(context.workdir) / "zerver/lib/pear_worker.py").exists())

    def test_topic_workdir_overlay_copies_only_pear_owned_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_workdir = Path(temp_dir) / "base"
            topic_workdir = Path(temp_dir) / "topic"
            base_workdir.mkdir()
            topic_workdir.mkdir()
            subprocess.run(["git", "init", "--quiet", str(base_workdir)], check=True, timeout=10)

            pear_file = base_workdir / "zerver/lib/pear.py"
            pear_file.parent.mkdir(parents=True)
            pear_file.write_text("pear source\n", encoding="utf-8")
            base_worker_file = base_workdir / "zerver/lib/pear_worker.py"
            base_worker_file.write_text("base worker\n", encoding="utf-8")
            (base_workdir / "pyproject.toml").write_text("megatron-core\n", encoding="utf-8")

            topic_worker_file = topic_workdir / "zerver/lib/pear_worker.py"
            topic_worker_file.parent.mkdir(parents=True)
            topic_worker_file.write_text("topic worker\n", encoding="utf-8")

            sync_pear_topic_workdir_overlay(base_workdir, topic_workdir)

            self.assertEqual(
                (topic_workdir / "zerver/lib/pear.py").read_text(encoding="utf-8"),
                "pear source\n",
            )
            self.assertEqual(topic_worker_file.read_text(encoding="utf-8"), "topic worker\n")
            self.assertFalse((topic_workdir / "pyproject.toml").exists())

    def test_channel_creation_can_pin_pear_repository(self) -> None:
        owner = self.example_user("hamlet")
        self.login_user(owner)
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
        ), patch(
            "zerver.lib.pear_worker.clone_or_update_pear_repository_workdir"
        ) as clone_mock:
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
            context = PearTopicContext.objects.get(
                realm=owner.realm,
                stream_name="megatron-lm",
                topic_name="",
            )

            self.assertEqual(context.github_repo, repository_url)
            self.assertTrue(context.workdir.startswith(temp_dir))
            self.assertNotIn("pear_repository_error", result_dict)
            clone_mock.assert_called_once_with(repository_url, Path(context.workdir))

    def test_topic_context_inherits_channel_repository_checkout(self) -> None:
        owner = self.example_user("hamlet")
        repository_url = "https://github.com/NVIDIA/Megatron-LM.git"

        def fake_clone(_repository_url: str, workdir: Path) -> None:
            workdir.mkdir(parents=True)

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
        ), patch(
            "zerver.lib.pear_worker.clone_or_update_pear_repository_workdir",
            side_effect=fake_clone,
        ):
            channel_context = configure_pear_channel_repository_context(
                owner,
                stream="megatron-lm",
                repository_url=repository_url,
            )
            topic_context = ensure_pear_topic_context(
                owner,
                stream="megatron-lm",
                topic="optimizer work",
            )

            self.assertEqual(topic_context.parent_context_id, channel_context.id)
            self.assertEqual(channel_context.github_repo, repository_url)
            self.assertNotEqual(topic_context.workdir, channel_context.workdir)
            self.assertTrue(Path(topic_context.workdir).exists())

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
                context = ensure_pear_topic_context(
                    owner,
                    stream=PEAR_DEFAULT_STREAM,
                    topic="old topic",
                )
                old_workdir = context.workdir
                run = PearWorkerRun.objects.create(
                    realm=owner.realm,
                    bot_profile=pear_bot,
                    requesting_user=owner,
                    trigger_message_id=3001,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name="old topic",
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

                result = maybe_refactor_pear_topic_context_on_move(
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
            self.assertIn("Pear workspace context moved", result.message)
            self.assertEqual(context.topic_name, "new topic")
            self.assertNotEqual(context.workdir, old_workdir)
            self.assertTrue(context.workdir.startswith(topic_root))
            self.assertTrue(Path(context.workdir).exists())
            self.assertEqual(run.topic_name, "new topic")
            self.assertEqual(side_run.source_topic_name, "new topic")
            self.assertFalse(
                PearTopicContext.objects.filter(
                    realm=owner.realm,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name="old topic",
                ).exists()
            )

    def test_topic_context_refactor_warns_for_partial_topic_move(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=str(Path(temp_dir) / "topics"),
        ):
            context = ensure_pear_topic_context(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic="old topic",
            )
            old_workdir = context.workdir

            result = maybe_refactor_pear_topic_context_on_move(
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
        PearTopicContext.objects.create(
            realm=owner.realm,
            stream_name="old-repo",
            topic_name="",
            github_repo="https://github.com/example/old-repo.git",
            workdir="/tmp/old-repo",
        )
        PearTopicContext.objects.create(
            realm=owner.realm,
            stream_name="old-repo",
            topic_name="feature",
            workdir="/tmp/old-repo-feature",
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

        message = maybe_refactor_pear_stream_contexts_on_rename(
            owner,
            old_stream_name="old-repo",
            new_stream_name="new-repo",
        )

        assert message is not None
        self.assertIn("Pear channel context renamed", message)
        self.assertEqual(
            PearTopicContext.objects.filter(
                realm=owner.realm,
                stream_name="new-repo",
            ).count(),
            2,
        )
        self.assertFalse(
            PearTopicContext.objects.filter(
                realm=owner.realm,
                stream_name="old-repo",
            ).exists()
        )
        run.refresh_from_db()
        side_run.refresh_from_db()
        self.assertEqual(run.stream_name, "new-repo")
        self.assertEqual(side_run.source_stream_name, "new-repo")

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

    def test_worker_repo_artifact_updates_topic_context(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
        ):
            PearDatabaseWorkerRunStore().record_topic_context_results(
                SimpleNamespace(user_profile=pear_bot),
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                tool_results=[
                    {
                        "label": "Repo",
                        "value": "NVIDIA/Megatron-LM <main>",
                    }
                ],
            )

            context = PearTopicContext.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )
            self.assertEqual(context.github_repo, "NVIDIA/Megatron-LM <main>")

    def test_worker_repo_artifact_updates_topic_context_for_local_topic_repo(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, PEAR_DEFAULT_STREAM)

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
        ):
            workdir = pear_expected_topic_workdir(
                owner,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )
            self.create_git_repository(workdir)

            PearDatabaseWorkerRunStore().record_topic_context_results(
                SimpleNamespace(user_profile=pear_bot),
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                tool_results=[{"label": "Repo", "value": str(workdir)}],
            )

            context = PearTopicContext.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )
            self.assertEqual(context.github_repo, str(workdir))
            topic_payload = serialize_pear_topic_context(
                context,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name=PEAR_DEFAULT_TOPIC,
            )
            self.assertEqual(topic_payload["github_repo"], str(workdir))
            self.assertEqual(topic_payload["pinned_repo"], "")
            self.assertEqual(topic_payload["repo_label"], PEAR_DEFAULT_STREAM)
            self.assertEqual(topic_payload["worktree_label"], PEAR_DEFAULT_TOPIC)

    def test_worker_repo_artifact_ignores_prose_repo_answer(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
        ):
            PearDatabaseWorkerRunStore().record_topic_context_results(
                SimpleNamespace(user_profile=pear_bot),
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                tool_results=[{"label": "Repo", "value": "Zulip repo"}],
            )
            PearDatabaseWorkerRunStore().record_topic_context_results(
                SimpleNamespace(user_profile=pear_bot),
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                tool_results=[{"label": "Repo", "value": "/home/zulipdev/zulip"}],
            )

            self.assertFalse(
                PearTopicContext.objects.filter(
                    realm=owner.realm,
                    stream_name=PEAR_DEFAULT_STREAM,
                    topic_name=PEAR_DEFAULT_TOPIC,
                ).exists()
            )

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
        context = ensure_pear_topic_context(
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
        context = ensure_pear_topic_context(
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
        context = ensure_pear_topic_context(
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

    def test_post_pear_topic_context_changes_model_and_posts_note(self) -> None:
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

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_WORKER_MODEL="gpt-5.5",
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
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
            self.assertEqual(model, "gpt-5.5-mini")
            self.assertEqual(reasoning_effort, "high")
            self.assertTrue(workdir.startswith(temp_dir))

        note = Message.objects.latest("id")
        self.assertEqual(note.sender_id, pear_bot.id)
        self.assertEqual(
            note.content,
            "King Hamlet changed current model to `gpt-5.5-mini`.",
        )

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

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_WORKER_MODEL="gpt-5.5",
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
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
            parent_context = PearTopicContext.objects.get(
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
            side_context = PearTopicContext.objects.get(
                realm=owner.realm,
                stream_name="direct messages",
                topic_name=run.topic_name,
            )

            self.assertEqual(run.source_stream_name, PEAR_DEFAULT_STREAM)
            self.assertEqual(run.source_topic_name, PEAR_DEFAULT_TOPIC)
            self.assertTrue(run.topic_name.startswith("side/"))
            self.assertEqual(side_context.parent_context_id, parent_context.id)
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

    def test_worker_loader_uses_prior_codex_thread_without_visible_artifact(self) -> None:
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
        self.assertEqual(active_session["session_record_count"], 0)
        self.assertIsNone(active_session["latest_session_record"])
        self.assertEqual(
            [message["content"] for message in active_session["recent_messages"]],
            [
                "agent, Choose a random 15 digit number.",
                "agent, what was the number you picked?",
            ],
        )
        self.assertEqual(active_session["recent_messages"][0]["sender"], owner.full_name)

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
            [berry.full_name, owner.full_name],
        )
        self.assertEqual(
            [message["content"] for message in active_session["recent_messages"]],
            [
                "hello pear. what is occupancy?",
                "pear, did you hear what berry said?",
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir, self.settings(
            PEAR_WORKER_MODEL="gpt-5.5",
            PEAR_TOPIC_WORKDIR_ROOT=temp_dir,
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
            PearDatabaseWorkerRunStore().record_topic_context_results(
                SimpleNamespace(user_profile=pear_bot),
                stream=PEAR_DEFAULT_STREAM,
                topic="",
                tool_results=[{"label": "Repo", "value": "NVIDIA/Megatron-LM <main>"}],
            )
            context = PearTopicContext.objects.get(
                realm=owner.realm,
                stream_name=PEAR_DEFAULT_STREAM,
                topic_name="",
            )
            self.assertEqual(context.github_repo, "NVIDIA/Megatron-LM <main>")

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
        request_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "@**Pear** fix checkout latency.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        first_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "- [ ] Confirm repo, branch, and target runtime.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        latest_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "\n".join(
                [
                    "- [x] Confirm repo, branch, and target runtime.",
                    "- [~] State the first concrete edit before changing files.",
                    "- [ ] Keep progress visible in this topic.",
                ]
            ),
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        steering_message_id = self.send_stream_message(
            user,
            PEAR_DEFAULT_STREAM,
            "Fold in the terminal status popup too.",
            topic_name=PEAR_DEFAULT_TOPIC,
        )
        pause_message_id = self.send_stream_message(
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
        self.assertEqual(active_session["state"], "paused")
        self.assertEqual(active_session["source"], "server")
        self.assertEqual(active_session["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(active_session["topic"], PEAR_DEFAULT_TOPIC)
        self.assertEqual(active_session["topic_message_count"], 5)
        self.assertEqual(active_session["request_count"], 1)
        self.assertEqual(active_session["latest_request_message_id"], request_message_id)
        self.assertEqual(active_session["latest_message_id"], latest_message_id)
        self.assertNotEqual(active_session["latest_message_id"], first_message_id)
        self.assertEqual(active_session["done_count"], 1)
        self.assertEqual(active_session["in_progress_count"], 1)
        self.assertEqual(active_session["pending_count"], 1)
        self.assertEqual(active_session["steering_count"], 1)
        self.assertEqual(active_session["latest_steering_message_id"], steering_message_id)
        self.assertEqual(
            active_session["latest_steering_instruction"],
            "Fold in the terminal status popup too.",
        )
        self.assertTrue(active_session["is_paused"])
        self.assertEqual(active_session["interruption_count"], 1)
        self.assertEqual(active_session["latest_interruption_kind"], "pause")
        self.assertEqual(active_session["latest_interruption_message_id"], pause_message_id)
        self.assertEqual(active_session["latest_interruption_instruction"], "waiting for CI")
        self.assertEqual(active_session["worker_state"], "blocked_paused")
        self.assertFalse(active_session["worker_can_run"])
        self.assertIn("paused", active_session["worker_blocked_reason"])
