# Test-local protocol doubles deliberately use structural duck typing; adding
# `Any` to every fake callback would not increase coverage or type safety.
# ruff: noqa: ANN001, ANN002, ANN003, ANN202, ANN204, S108

import json
import queue
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import NoReturn
from unittest import TestCase
from unittest.mock import Mock, patch

from zerver.lib.exceptions import JsonableError
from zerver.lib.pear import (
    PEAR_ACTIVE_SESSION_ENDPOINT,
    PEAR_AGENT_MENTION,
    PEAR_AGENT_RUNTIME_PACKAGES,
    PEAR_APPLY_PLAN_PROMPT,
    PEAR_BOT_SHORT_NAME,
    PEAR_CATCH_UP_PROMPT,
    PEAR_CHECKPOINT_PROMPT,
    PEAR_CODEX_MCP_TOOLS,
    PEAR_CONFIG_ENDPOINT,
    PEAR_DEFAULT_STREAM,
    PEAR_DEFAULT_TOPIC,
    PEAR_DRAFT_ARTIFACT_PROMPT,
    PEAR_EMBEDDED_BOT_SERVICE,
    PEAR_EXPLICIT_MENTION_MESSAGE_KEY,
    PEAR_FIND_ANSWER_PROMPT,
    PEAR_FIRST_RUN_ENDPOINT,
    PEAR_FIX_BUG_PROMPT,
    PEAR_FOLLOWUP_TRIGGER,
    PEAR_HANDOFF_PROMPT,
    PEAR_NO_VISIBLE_RESPONSE_MESSAGE,
    PEAR_OFFICIAL_AGENT_RUNTIME,
    PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
    PEAR_OFFICIAL_CODEX_SDK_PACKAGES,
    PEAR_PLAN_PROMPT,
    PEAR_REVIEW_PROMPT,
    PEAR_RUNS_ENDPOINT,
    PEAR_SAVE_WORK_PROMPT,
    PEAR_SERVER_CONTRACT_VERSION,
    PEAR_SESSION_ENDPOINT,
    PEAR_SIDE_ENDPOINT,
    PEAR_STATUS_CHECK_PROMPT,
    PEAR_TOPIC_PREFIX,
    PEAR_TRIGGER_MESSAGE_KEY,
    PEAR_VERIFY_CHANGES_PROMPT,
    PEAR_WORKER_RUNS_STORAGE_KEY,
    PEAR_WORKING_MESSAGE,
    PearActiveSessionSummary,
    PearAmbientDecision,
    PearEmbeddedBotHandler,
    PearWorkerLeaseLostError,
    PearWorkerRunResult,
    PearWorkerRuntime,
    PearWorkerTranscriptCallback,
    build_pear_ack_message,
    build_pear_active_session_summary,
    build_pear_active_session_summary_from_messages,
    build_pear_apply_plan_message,
    build_pear_catch_up_message,
    build_pear_checkpoint_message,
    build_pear_config_payload,
    build_pear_draft_artifact_message,
    build_pear_explicit_steering_event,
    build_pear_find_answer_message,
    build_pear_fix_bug_message,
    build_pear_handoff_message,
    build_pear_plan_message,
    build_pear_review_message,
    build_pear_save_work_message,
    build_pear_seed_message,
    build_pear_status_check_message,
    build_pear_triage_message,
    build_pear_verify_changes_message,
    build_pear_worker_completed_message,
    build_pear_worker_completed_message_chunks,
    build_pear_worker_progress_message,
    clean_pear_task_text,
    get_pear_live_run_state,
    get_pear_session_defaults,
    get_pear_session_payload,
    is_pear_request_message,
    is_pear_thread_request_message,
    load_pear_worker_run_records,
    normalize_pear_response_policy,
    pear_distinct_transcript_tool_events,
    pear_normalize_token_usage,
    pear_sanitized_child_agent_event,
    pear_worker_already_started,
    pear_worker_event_tool_results,
    pear_worker_run_for_message,
    pear_worker_should_start,
    pear_worker_summary_from_tool_results,
    record_pear_worker_run,
    split_pear_persistent_message,
    update_pear_worker_status_message,
)
from zerver.lib.pear_agent import (
    PearAgentRuntimeError,
    PearCodexAppServerToolRunner,
    PearCodexAppServerTranscriptTranslator,
    PearDisabledAgentRunner,
    PearGeminiInteractionsAddressingIntentClassifier,
    PearProcessIsolation,
    build_pear_addressing_router_prompt,
    build_pear_codex_developer_instructions,
    build_pear_codex_turn_prompt,
    parse_pear_addressing_json_output,
    pear_worker_results_from_final_text,
)
from zerver.lib.pear_worker import (
    PEAR_ADDRESSING_QUEUE_NAME,
    PEAR_WORKER_QUEUE_NAMES,
    PearQueueWorkerDispatcher,
    pear_provision_codex_skills,
    pear_worker_queue_name,
)
from zerver.lib.queue import QueueEventDiscardError, SimpleQueueClient
from zerver.worker import pear_worker as pear_worker_module
from zerver.worker.embedded_bots import EmbeddedBotWorker
from zerver.worker.pear_addressing import PearAddressingWorker
from zproject import computed_settings as pear_computed_settings


class PearComputedSettingsEnvTest(TestCase):
    def test_pear_env_override_table_parses_runtime_knobs(self) -> None:
        env = {
            "PEAR_WORKER_AGENT_RUNTIME": "codex-app-server",
            "PEAR_WORKER_MODEL": "",
            "PEAR_WORKER_MODEL_CHOICES": "gpt-5.5,gpt-5.4-mini gpt-5.3-codex-spark",
            "PEAR_WORKER_FAST_MODE": "off",
            "PEAR_WORKER_TIMEOUT_SECONDS": "42",
            "PEAR_WORKER_CODEX_EXTRA_ARGS": "--ignore-rules --foo",
            "PEAR_WORKER_TRUSTED_USER_IDS": "1, 2 3",
        }

        with patch.dict("os.environ", env, clear=False):
            values = {
                setting_name: parser(setting_name)
                for setting_name, parser in pear_computed_settings._PEAR_ENV_OVERRIDES.items()
            }

        self.assertEqual(values["PEAR_WORKER_AGENT_RUNTIME"], "codex-app-server")
        self.assertIsNone(values["PEAR_WORKER_MODEL"])
        self.assertEqual(
            values["PEAR_WORKER_MODEL_CHOICES"],
            ["gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex-spark"],
        )
        self.assertFalse(values["PEAR_WORKER_FAST_MODE"])
        self.assertEqual(values["PEAR_WORKER_TIMEOUT_SECONDS"], 42)
        self.assertEqual(values["PEAR_WORKER_CODEX_EXTRA_ARGS"], ["--ignore-rules", "--foo"])
        self.assertEqual(values["PEAR_WORKER_TRUSTED_USER_IDS"], [1, 2, 3])

    def test_pear_env_override_table_uses_gemini_key_aliases(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_GENAI_API_KEY": "gemini-key"}, clear=True):
            api_key = pear_computed_settings._PEAR_ENV_OVERRIDES[
                "PEAR_GEMINI_INTERACTIONS_API_KEY"
            ]("PEAR_GEMINI_INTERACTIONS_API_KEY")

        self.assertEqual(api_key, "gemini-key")

    def test_pear_model_choices_default_to_configured_worker_model(self) -> None:
        with patch.dict("os.environ", {"PEAR_WORKER_MODEL": "gpt-5.4-mini"}, clear=True):
            choices = pear_computed_settings._PEAR_ENV_OVERRIDES["PEAR_WORKER_MODEL_CHOICES"](
                "PEAR_WORKER_MODEL_CHOICES"
            )

        self.assertEqual(choices, ["gpt-5.4-mini"])

    def test_pear_env_bool_rejects_invalid_value(self) -> None:
        with (
            patch.dict("os.environ", {"PEAR_WORKER_FAST_MODE": "sometimes"}, clear=False),
            self.assertRaisesRegex(ValueError, "PEAR_WORKER_FAST_MODE"),
        ):
            pear_computed_settings._PEAR_ENV_OVERRIDES["PEAR_WORKER_FAST_MODE"](
                "PEAR_WORKER_FAST_MODE"
            )


class PearSessionDefaultsTest(TestCase):
    def test_defaults(self) -> None:
        defaults = get_pear_session_defaults()

        self.assertEqual(defaults.agent_mention, PEAR_AGENT_MENTION)
        self.assertEqual(defaults.stream, PEAR_DEFAULT_STREAM)
        self.assertEqual(defaults.topic, PEAR_DEFAULT_TOPIC)
        self.assertEqual(defaults.topic_prefix, PEAR_TOPIC_PREFIX)
        self.assertEqual(PEAR_BOT_SHORT_NAME, "pear")
        self.assertEqual(PEAR_EMBEDDED_BOT_SERVICE, "pear")
        self.assertEqual(PEAR_FIX_BUG_PROMPT, "Fix bug:")

    def test_response_policy_normalization_is_strict(self) -> None:
        self.assertEqual(normalize_pear_response_policy(None), "ambient")
        self.assertEqual(normalize_pear_response_policy("ambient"), "ambient")
        self.assertEqual(normalize_pear_response_policy("mentions_only"), "mentions_only")
        with self.assertRaisesRegex(ValueError, "Invalid Pear response policy"):
            normalize_pear_response_policy("ambien")

    def test_structured_artifacts_never_enter_run_telemetry_or_summary(self) -> None:
        raw = '{"version":1,"blocks":[{"type":"copy","body":"private detail"}]}'
        results = [{"label": "Artifact", "value": raw}]
        self.assertEqual(pear_worker_summary_from_tool_results(results), "")
        sanitized = pear_worker_event_tool_results(results)
        self.assertEqual(sanitized[0]["label"], "Artifact")
        self.assertIn("Structured artifact", sanitized[0]["value"])
        self.assertNotIn("private detail", sanitized[0]["value"])

    def test_update_pear_worker_status_message_logs_rejected_update(self) -> None:
        class FakeBotHandler:
            def update_message(self, payload: dict[str, object]) -> None:
                raise JsonableError("edit window is closed")

        with self.assertLogs("zerver.lib.pear", level="WARNING") as logs:
            self.assertFalse(
                update_pear_worker_status_message(
                    FakeBotHandler(),
                    progress_message_id=123,
                    content="working",
                )
            )

        self.assertIn("message 123", "\n".join(logs.output))
        self.assertIn("edit window is closed", "\n".join(logs.output))

    def test_update_pear_worker_status_message_can_request_rendering_only(self) -> None:
        updated_messages: list[dict[str, object]] = []

        class FakeBotHandler:
            def update_message(self, payload: dict[str, object]) -> None:
                updated_messages.append(payload)

        self.assertTrue(
            update_pear_worker_status_message(
                FakeBotHandler(),
                progress_message_id=123,
                content="working",
                rendering_only=True,
            )
        )

        self.assertEqual(
            updated_messages,
            [
                {
                    "message_id": 123,
                    "content": "working",
                    "rendering_only": True,
                }
            ],
        )

    def test_update_pear_worker_status_message_logs_unexpected_update_failure(self) -> None:
        class FakeBotHandler:
            def update_message(self, payload: dict[str, object]) -> None:
                raise RuntimeError("database connection dropped")

        with self.assertLogs("zerver.lib.pear", level="ERROR") as logs:
            self.assertFalse(
                update_pear_worker_status_message(
                    FakeBotHandler(),
                    progress_message_id=456,
                    content="working",
                )
            )

        log_output = "\n".join(logs.output)
        self.assertIn("message 456", log_output)
        self.assertIn("database connection dropped", log_output)

    def test_prompt_constants(self) -> None:
        self.assertEqual(PEAR_STATUS_CHECK_PROMPT, "Status check:")
        self.assertEqual(PEAR_CATCH_UP_PROMPT, "Catch up:")
        self.assertEqual(PEAR_FIND_ANSWER_PROMPT, "Find answer:")
        self.assertEqual(PEAR_REVIEW_PROMPT, "Review changes:")
        self.assertEqual(PEAR_PLAN_PROMPT, "Implementation plan:")
        self.assertEqual(PEAR_CHECKPOINT_PROMPT, "Checkpoint:")
        self.assertEqual(PEAR_APPLY_PLAN_PROMPT, "Apply plan:")
        self.assertEqual(PEAR_SAVE_WORK_PROMPT, "Save work:")
        self.assertEqual(PEAR_VERIFY_CHANGES_PROMPT, "Verify changes:")
        self.assertEqual(PEAR_DRAFT_ARTIFACT_PROMPT, "Draft artifact:")
        self.assertEqual(PEAR_HANDOFF_PROMPT, "Handoff summary:")

    def test_build_seed_message(self) -> None:
        seed_message = build_pear_seed_message("fix the failing checkout test")

        self.assertEqual(seed_message, "@**Pear** fix the failing checkout test")

    def test_build_seed_message_with_empty_task(self) -> None:
        seed_message = build_pear_seed_message()

        self.assertEqual(seed_message, "@**Pear** Start the topic Codex thread:")

    def test_build_fix_bug_message(self) -> None:
        fix_message = build_pear_fix_bug_message("checkout crashes on empty cart")

        self.assertIn("@**Pear** Fix bug:", fix_message)
        self.assertIn("Bug: checkout crashes on empty cart", fix_message)
        self.assertIn("Observed behavior: ", fix_message)
        self.assertIn("Expected behavior: ", fix_message)
        self.assertIn("How to verify: ", fix_message)
        self.assertIn(
            "Workspace context: use the typed workspace context",
            fix_message,
        )

    def test_build_status_check_message(self) -> None:
        status_message = build_pear_status_check_message("checkout flow")

        self.assertIn("@**Pear** Status check:", status_message)
        self.assertIn("Focus: checkout flow", status_message)
        self.assertIn("Completed since last update: ", status_message)
        self.assertIn("Still in progress: ", status_message)
        self.assertIn("Blocked or needs decision: ", status_message)
        self.assertIn("Next concrete step: ", status_message)
        self.assertIn("Checks to run: ", status_message)

    def test_build_handoff_message(self) -> None:
        handoff_message = build_pear_handoff_message("next developer")

        self.assertIn("@**Pear** Handoff summary:", handoff_message)
        self.assertIn("Audience: next developer", handoff_message)
        self.assertIn("Goal and scope: ", handoff_message)
        self.assertIn("Current state: ", handoff_message)
        self.assertIn("Key files and links: ", handoff_message)
        self.assertIn("Open decisions: ", handoff_message)
        self.assertIn("Next steps: ", handoff_message)
        self.assertIn("Checks still needed: ", handoff_message)

    def test_build_catch_up_message(self) -> None:
        catch_up_message = build_pear_catch_up_message("yesterday")

        self.assertIn("@**Pear** Catch up:", catch_up_message)
        self.assertIn("Since: yesterday", catch_up_message)
        self.assertIn("Summarize changes: ", catch_up_message)
        self.assertIn("Decisions made: ", catch_up_message)
        self.assertIn("Files or links to inspect: ", catch_up_message)
        self.assertIn("Current blockers: ", catch_up_message)
        self.assertIn("Next actions: ", catch_up_message)
        self.assertIn("Use the active topic and typed workspace context", catch_up_message)

    def test_build_triage_message(self) -> None:
        triage_message = build_pear_triage_message("checkout failures")

        self.assertIn("@**Pear** Triage request:", triage_message)
        self.assertIn("Request: checkout failures", triage_message)
        self.assertIn("Possible duplicate or related work:", triage_message)
        self.assertIn("Answer if already known:", triage_message)
        self.assertIn("Suggested owner or reviewer:", triage_message)
        self.assertIn("Suggested scope:", triage_message)
        self.assertIn("Priority and risk: ", triage_message)
        self.assertIn("Next action: ", triage_message)
        self.assertIn("Output: answer normally", triage_message)

    def test_build_find_answer_message(self) -> None:
        find_answer_message = build_pear_find_answer_message("Where is auth configured?")

        self.assertIn("@**Pear** Find answer:", find_answer_message)
        self.assertIn("Question: Where is auth configured?", find_answer_message)
        self.assertIn("Search scope:", find_answer_message)
        self.assertIn("Evidence to cite:", find_answer_message)
        self.assertIn("Answer policy:", find_answer_message)
        self.assertIn("do not encode evidence as label-prefixed state lines", find_answer_message)

    def test_build_review_message(self) -> None:
        review_message = build_pear_review_message("current branch")

        self.assertIn("@**Pear** Review changes:", review_message)
        self.assertIn("Scope: current branch", review_message)
        self.assertIn("Review focus: correctness", review_message)
        self.assertIn("Diff or files to inspect: use the typed workspace context", review_message)
        self.assertIn("Risk areas: ", review_message)
        self.assertIn("Expected output: prioritized findings", review_message)
        self.assertIn("Verification to consider: ", review_message)

    def test_build_plan_message(self) -> None:
        plan_message = build_pear_plan_message("refactor checkout flow")

        self.assertIn("@**Pear** Implementation plan:", plan_message)
        self.assertIn("Goal: refactor checkout flow", plan_message)
        self.assertIn("Constraints: ", plan_message)
        self.assertIn("Files to inspect: ", plan_message)
        self.assertIn("Proposed steps: ", plan_message)
        self.assertIn("Risks and unknowns: ", plan_message)
        self.assertIn("Verification plan: ", plan_message)
        self.assertIn("Do not edit files yet", plan_message)

    def test_build_checkpoint_message(self) -> None:
        checkpoint_message = build_pear_checkpoint_message("checkout regression")

        self.assertIn("@**Pear** Checkpoint:", checkpoint_message)
        self.assertIn("Focus: checkout regression", checkpoint_message)
        self.assertIn("current branch, diff summary", checkpoint_message)
        self.assertIn("summarize the exact diff and verification", checkpoint_message)
        self.assertIn("wait for confirmation", checkpoint_message)

    def test_build_apply_plan_message(self) -> None:
        apply_plan_message = build_pear_apply_plan_message("checkout regression")

        self.assertIn("@**Pear** Apply plan:", apply_plan_message)
        self.assertIn("Scope: checkout regression", apply_plan_message)
        self.assertIn("latest Implementation plan", apply_plan_message)
        self.assertIn("make the scoped edits now", apply_plan_message)
        self.assertIn("planned verification", apply_plan_message)

    def test_build_save_work_message(self) -> None:
        save_work_message = build_pear_save_work_message("checkout regression")

        self.assertIn("@**Pear** Save work:", save_work_message)
        self.assertIn("Scope: checkout regression", save_work_message)
        self.assertIn("branch, draft PR, patch", save_work_message)
        self.assertIn("report the exact saved branch, PR, diff, or document", save_work_message)
        self.assertIn("recoverable from this topic", save_work_message)

    def test_build_verify_changes_message(self) -> None:
        verify_message = build_pear_verify_changes_message("checkout regression")

        self.assertIn("@**Pear** Verify changes:", verify_message)
        self.assertIn("Target: checkout regression", verify_message)
        self.assertIn("Checks to run: use the planned verification", verify_message)
        self.assertIn("Fix policy: if a check fails", verify_message)
        self.assertIn("Evidence to post: commands run", verify_message)

    def test_build_draft_artifact_message(self) -> None:
        artifact_message = build_pear_draft_artifact_message("issue for flaky tests")

        self.assertIn("@**Pear** Draft artifact:", artifact_message)
        self.assertIn("Artifact: issue for flaky tests", artifact_message)
        self.assertIn("Source context: use this Zulip topic", artifact_message)
        self.assertIn("Must include: scope, decisions", artifact_message)
        self.assertIn("available native artifact tool", artifact_message)
        self.assertIn("Do not encode artifact state as label-prefixed prose", artifact_message)

    def test_build_ack_message(self) -> None:
        ack_message = build_pear_ack_message("debug the flaky parser test")

        self.assertEqual(PEAR_WORKING_MESSAGE, "· Working")
        self.assertEqual(ack_message, PEAR_WORKING_MESSAGE)
        self.assertNotIn("Open the Pear panel", ack_message)
        self.assertNotIn("Confirm repo, branch, and target runtime", ack_message)

    def test_build_ack_message_strips_pear_mention(self) -> None:
        ack_message = build_pear_ack_message("@**Pear** debug the flaky parser test")

        self.assertEqual(ack_message, PEAR_WORKING_MESSAGE)
        self.assertNotIn("Task: @**Pear**", ack_message)

    def test_session_payload(self) -> None:
        payload = get_pear_session_payload()

        self.assertEqual(payload["agent_name"], "Pear")
        self.assertEqual(payload["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(payload["topic"], PEAR_DEFAULT_TOPIC)
        self.assertEqual(payload["fix_bug_message"], build_pear_fix_bug_message())
        self.assertEqual(
            payload["status_check_message"],
            build_pear_status_check_message(),
        )
        self.assertEqual(payload["handoff_message"], build_pear_handoff_message())
        self.assertEqual(payload["catch_up_message"], build_pear_catch_up_message())
        self.assertEqual(payload["triage_message"], build_pear_triage_message())
        self.assertEqual(
            payload["find_answer_message"],
            build_pear_find_answer_message(),
        )
        self.assertEqual(payload["review_message"], build_pear_review_message())
        self.assertEqual(payload["plan_message"], build_pear_plan_message())
        self.assertEqual(payload["checkpoint_message"], build_pear_checkpoint_message())
        self.assertEqual(payload["apply_plan_message"], build_pear_apply_plan_message())
        self.assertEqual(payload["save_work_message"], build_pear_save_work_message())
        self.assertEqual(
            payload["verify_changes_message"],
            build_pear_verify_changes_message(),
        )
        self.assertEqual(
            payload["draft_artifact_message"],
            build_pear_draft_artifact_message(),
        )
        self.assertEqual(
            payload["official_agent_runtime"],
            PEAR_OFFICIAL_AGENT_RUNTIME,
        )
        self.assertEqual(
            payload["official_agent_runtime_label"],
            PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
        )
        self.assertEqual(
            payload["agent_runtime_packages"],
            list(PEAR_AGENT_RUNTIME_PACKAGES),
        )
        self.assertEqual(
            payload["official_codex_sdk_packages"],
            list(PEAR_OFFICIAL_CODEX_SDK_PACKAGES),
        )
        self.assertEqual(payload["codex_mcp_tools"], list(PEAR_CODEX_MCP_TOOLS))
        self.assertEqual(
            payload["server_contract_version"],
            PEAR_SERVER_CONTRACT_VERSION,
        )
        self.assertEqual(PEAR_SESSION_ENDPOINT, "/pear/session")
        self.assertEqual(PEAR_ACTIVE_SESSION_ENDPOINT, "/pear/session/active")
        self.assertEqual(PEAR_CONFIG_ENDPOINT, "/pear/config")
        self.assertEqual(PEAR_FIRST_RUN_ENDPOINT, "/pear/first-run")
        self.assertEqual(PEAR_RUNS_ENDPOINT, "/pear/runs")
        self.assertEqual(PEAR_SIDE_ENDPOINT, "/pear/side")

    def test_session_payload_for_target_topic(self) -> None:
        payload = get_pear_session_payload(
            stream=" frontend ",
            topic=" checkout/regression ",
        )

        self.assertEqual(payload["stream"], "frontend")
        self.assertEqual(payload["topic"], "checkout/regression")
        self.assertEqual(payload["topic_prefix"], PEAR_TOPIC_PREFIX)
        self.assertEqual(payload["seed_message"], build_pear_seed_message())

    def test_build_config_payload_without_bot(self) -> None:
        config = build_pear_config_payload(
            embedded_bots_enabled=True,
            bot_count=0,
        )

        self.assertEqual(config["bot_short_name"], "pear")
        self.assertEqual(config["embedded_bot_service"], "pear")
        self.assertTrue(config["embedded_bots_enabled"])
        self.assertFalse(config["configured"])
        self.assertFalse(config["enabled"])
        self.assertEqual(config["worker_agent_runtime"], "disabled")
        self.assertFalse(config["worker_runtime_ready"])
        self.assertFalse(config["worker_runtime_auth_ready"])
        self.assertIsNone(config["worker_runtime_login_method"])
        self.assertIsNone(config["worker_runtime_login_command"])
        self.assertIsNone(config["worker_runtime_login_target"])
        self.assertIsNone(config["worker_runtime_login_instructions"])
        self.assertEqual(config["bot_count"], 0)
        self.assertIsNone(config["bot_user_id"])
        self.assertIsNone(config["selected_bot_user_id"])
        self.assertEqual(config["bot_identities"], [])
        self.assertEqual(
            [step["key"] for step in config["setup_steps"]],
            ["embedded_bots", "pear_bot", "worker_runtime", "worker_auth"],
        )
        self.assertTrue(config["setup_steps"][0]["complete"])
        self.assertFalse(config["setup_steps"][1]["complete"])
        self.assertFalse(config["setup_steps"][2]["complete"])
        self.assertFalse(config["setup_steps"][3]["complete"])

    def test_build_config_payload_with_bot(self) -> None:
        config = build_pear_config_payload(
            embedded_bots_enabled=True,
            bot_count=1,
            bot_user_id=7,
            bot_full_name="Pear",
            bot_email="pear-bot@example.com",
            bot_identities=[
                {
                    "bot_user_id": 7,
                    "bot_full_name": "Pear",
                    "bot_email": "pear-bot@example.com",
                    "selected": True,
                }
            ],
            worker_agent_runtime="codex-app-server",
            worker_runtime_available=True,
            worker_runtime_detail="codex app-server via /usr/local/bin/codex.",
            worker_runtime_auth_available=True,
            worker_runtime_auth_detail="Codex CLI is logged in.",
            worker_runtime_login_command="codex login",
        )

        self.assertTrue(config["configured"])
        self.assertTrue(config["enabled"])
        self.assertEqual(config["worker_agent_runtime"], "codex-app-server")
        self.assertTrue(config["worker_runtime_ready"])
        self.assertTrue(config["worker_runtime_auth_ready"])
        self.assertEqual(config["worker_runtime_login_method"], "codex-cli")
        self.assertEqual(config["worker_runtime_login_command"], "codex login")
        self.assertEqual(config["worker_runtime_login_target"], "worker-environment")
        self.assertIn(
            "worker environment",
            config["worker_runtime_login_instructions"],
        )
        self.assertEqual(config["bot_user_id"], 7)
        self.assertEqual(config["bot_full_name"], "Pear")
        self.assertEqual(config["bot_email"], "pear-bot@example.com")
        self.assertEqual(config["selected_bot_user_id"], 7)
        self.assertEqual(config["selected_bot_full_name"], "Pear")
        self.assertEqual(config["selected_bot_email"], "pear-bot@example.com")
        self.assertEqual(
            config["bot_identities"],
            [
                {
                    "bot_user_id": 7,
                    "bot_full_name": "Pear",
                    "bot_email": "pear-bot@example.com",
                    "selected": True,
                }
            ],
        )
        self.assertEqual(
            config["setup_steps"][2]["detail"],
            "codex app-server via /usr/local/bin/codex.",
        )
        self.assertEqual(
            config["setup_steps"][3]["detail"],
            "Codex CLI is logged in.",
        )
        self.assertTrue(all(step["complete"] for step in config["setup_steps"]))

    def test_build_config_payload_requires_runtime_dependency(self) -> None:
        config = build_pear_config_payload(
            embedded_bots_enabled=True,
            bot_count=1,
            bot_user_id=7,
            bot_full_name="Pear",
            bot_email="pear-bot@example.com",
            worker_agent_runtime="codex-app-server",
            worker_runtime_available=False,
            worker_runtime_detail="Missing codex executable.",
        )

        self.assertFalse(config["worker_runtime_ready"])
        self.assertTrue(config["worker_runtime_auth_ready"])
        self.assertEqual(config["setup_steps"][2]["label"], "Worker runtime dependencies")
        self.assertFalse(config["setup_steps"][2]["complete"])
        self.assertEqual(
            config["setup_steps"][2]["detail"],
            "Missing codex executable.",
        )
        self.assertEqual(config["setup_steps"][3]["label"], "Worker runtime login")
        self.assertTrue(config["setup_steps"][3]["complete"])

    def test_build_config_payload_with_multiple_bots(self) -> None:
        config = build_pear_config_payload(
            embedded_bots_enabled=True,
            bot_count=2,
            bot_user_id=7,
            bot_full_name="Pear",
            bot_email="pear-bot@example.com",
            bot_identities=[
                {
                    "bot_user_id": 7,
                    "bot_full_name": "Pear",
                    "bot_email": "pear-bot@example.com",
                    "selected": False,
                },
                {
                    "bot_user_id": 8,
                    "bot_full_name": "Pear Infra",
                    "bot_email": "pear-infra@example.com",
                    "selected": True,
                },
            ],
            selected_bot_user_id=8,
            selected_bot_full_name="Pear Infra",
            selected_bot_email="pear-infra@example.com",
        )

        self.assertEqual(config["bot_count"], 2)
        self.assertEqual(config["bot_user_id"], 7)
        self.assertEqual(config["selected_bot_user_id"], 8)
        self.assertEqual(config["selected_bot_full_name"], "Pear Infra")
        self.assertEqual(len(config["bot_identities"]), 2)
        self.assertEqual(config["bot_identities"][1]["bot_full_name"], "Pear Infra")
        self.assertTrue(config["bot_identities"][1]["selected"])

    def test_build_config_payload_disabled_server(self) -> None:
        config = build_pear_config_payload(
            embedded_bots_enabled=False,
            bot_count=1,
            bot_user_id=7,
            bot_full_name="Pear",
            bot_email="pear-bot@example.com",
        )

        self.assertTrue(config["configured"])
        self.assertFalse(config["enabled"])
        self.assertFalse(config["setup_steps"][0]["complete"])
        self.assertFalse(config["worker_runtime_ready"])

    def test_build_pear_explicit_steering_event(self) -> None:
        self.assertEqual(
            build_pear_explicit_steering_event(
                "@**Pear** keep the fix minimal",
                82,
                sender_is_bot=False,
                session_started=True,
            ),
            {
                "instruction": "keep the fix minimal",
                "message_id": 82,
                "explicitly_addressed": True,
            },
        )
        self.assertIsNone(
            build_pear_explicit_steering_event(
                "Fold in the failing terminal UI test too.",
                83,
                sender_is_bot=False,
                session_started=True,
            )
        )
        self.assertIsNone(
            build_pear_explicit_steering_event(
                "- [~] State the first concrete edit before changing files.",
                84,
                sender_is_bot=True,
                session_started=True,
            )
        )
        self.assertIsNone(
            build_pear_explicit_steering_event(
                "@Pear start a new pairing session",
                85,
                sender_is_bot=False,
                session_started=False,
            )
        )

    def test_is_pear_request_message(self) -> None:
        self.assertTrue(is_pear_request_message("@**Pear** fix the checkout test"))
        self.assertTrue(is_pear_request_message("@Pear fix the checkout test"))
        self.assertTrue(is_pear_request_message("Hi @Pear"))
        self.assertTrue(is_pear_request_message("@pear, are you there?"))
        self.assertFalse(is_pear_request_message("@**Other** fix the checkout test"))
        self.assertFalse(is_pear_request_message("@Pearl fix the checkout test"))

    def test_clean_pear_task_text_removes_mentions_anywhere(self) -> None:
        self.assertEqual(
            clean_pear_task_text("@Pear fix the checkout test"), "fix the checkout test"
        )
        self.assertEqual(clean_pear_task_text("Hi @Pear"), "Hi")
        self.assertEqual(clean_pear_task_text("Hi @**Pear**"), "Hi")

    def test_is_pear_thread_request_message(self) -> None:
        self.assertTrue(is_pear_thread_request_message("@Pear debug the flaky parser test"))
        self.assertTrue(is_pear_thread_request_message("@Pear implement the login view"))
        self.assertTrue(is_pear_thread_request_message("@Pear start a new pairing session"))
        self.assertTrue(
            is_pear_thread_request_message("@Pear every weekday at 9am, check the linked PRs")
        )
        self.assertTrue(is_pear_thread_request_message("@Pear hi"))
        self.assertTrue(is_pear_thread_request_message("@Pear Hello."))
        self.assertTrue(is_pear_thread_request_message("@Pear What's up!"))
        self.assertTrue(is_pear_thread_request_message("@Pear hey tell me something"))
        self.assertTrue(is_pear_thread_request_message("@Pear tell me a story"))
        self.assertTrue(is_pear_thread_request_message("@Pear find answer to question"))
        self.assertTrue(is_pear_thread_request_message("@Pear thanks!"))
        self.assertTrue(is_pear_thread_request_message("@Pear thank you."))
        self.assertTrue(is_pear_thread_request_message("@Pear What can you access?"))
        self.assertFalse(is_pear_thread_request_message("debug the flaky parser test"))

    def test_build_active_session_summary(self) -> None:
        payload = get_pear_session_payload()
        summary = build_pear_active_session_summary(
            pear_session=payload,
            source="server",
            stream_exists=True,
            topic_message_count=3,
            latest_message_id=42,
        )

        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["source"], "server")
        self.assertEqual(summary["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(summary["topic_message_count"], 3)
        self.assertEqual(summary["request_count"], 0)
        self.assertIsNone(summary["latest_request_message_id"])
        self.assertIsNone(summary["latest_codex_thread_id"])
        self.assertEqual(summary["response_policy"], "ambient")
        self.assertFalse(summary["is_paused"])
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])
        self.assertIsNone(summary["worker_blocked_reason"])
        self.assertEqual(summary["recent_messages"], [])

    def test_build_active_session_summary_from_messages(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            topic_message_count=10,
            messages=[
                (10, "@**Pear** fix the checkout test", False),
                (11, "- [ ] Confirm repo, branch, and target runtime.", True),
                (12, "Fold in the failing terminal UI test too.", False),
                (
                    13,
                    "- [x] Confirm repo, branch, and target runtime.\n- [~] State the first concrete edit before changing files.\nBranch: pear/runtime-artifacts\nSession: Codex thread thread-123\nVerification: pytest zerver/tests/test_pear.py",
                    True,
                ),
            ],
            response_policy="ambient",
        )

        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["latest_message_id"], 13)
        self.assertEqual(summary["latest_request_message_id"], 10)
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["topic_message_count"], 10)
        self.assertIsNone(summary["latest_codex_thread_id"])
        self.assertEqual(summary["response_policy"], "ambient")
        assistant_message = next(
            message for message in summary["recent_messages"] if message["message_id"] == 13
        )
        self.assertIn("Branch: pear/runtime-artifacts", assistant_message["content"])
        self.assertIn(
            "Verification: pytest zerver/tests/test_pear.py",
            assistant_message["content"],
        )
        self.assertFalse(summary["is_paused"])
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])
        self.assertIsNone(summary["worker_blocked_reason"])
        self.assertEqual(summary["recent_messages"][2]["message_id"], 12)
        self.assertEqual(
            summary["recent_messages"][2]["content"],
            "Fold in the failing terminal UI test too.",
        )

    def test_active_session_summary_preserves_recent_named_transcript(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "Choose a number from 1 to 240", False, "plugyawn"),
                (11, "Session context recorded.", True, "Pear"),
                (12, "Agent, do the above", False, "plugyawn"),
            ],
        )

        self.assertEqual(
            summary["recent_messages"],
            [
                {
                    "message_id": 10,
                    "sender": "plugyawn",
                    "sender_is_bot": False,
                    "content": "Choose a number from 1 to 240",
                },
                {
                    "message_id": 11,
                    "sender": "Pear",
                    "sender_is_bot": True,
                    "content": "Session context recorded.",
                },
                {
                    "message_id": 12,
                    "sender": "plugyawn",
                    "sender_is_bot": False,
                    "content": "Agent, do the above",
                },
            ],
        )

    def test_fresh_topic_waits_for_request_but_can_run(self) -> None:
        payload = get_pear_session_payload()
        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["worker_state"], "waiting_for_request")
        self.assertEqual(
            summary["worker_blocked_reason"],
            "No Pear request has been posted in this topic.",
        )
        # A fresh topic is not blocked: the worker runs on the first request.
        self.assertTrue(summary["worker_can_run"])

    def test_build_active_session_summary_uses_typed_pause_state(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@**Pear** fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
            is_paused=True,
        )

        self.assertEqual(summary["state"], "paused")
        self.assertTrue(summary["is_paused"])
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["worker_state"], "blocked_paused")
        self.assertFalse(summary["worker_can_run"])
        self.assertIn("paused", summary["worker_blocked_reason"])

    def test_pause_like_prose_is_only_a_codex_request(self) -> None:
        payload = get_pear_session_payload()
        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(12, "@**Pear** Pause session: waiting for CI", False)],
        )

        self.assertEqual(summary["state"], "requested")
        self.assertFalse(summary["is_paused"])
        self.assertEqual(summary["request_count"], 1)

    def test_build_active_session_summary_from_request_only_messages(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(13, "@Pear start a new pairing session")],
        )

        self.assertEqual(summary["state"], "requested")
        self.assertEqual(summary["latest_request_message_id"], 13)
        self.assertEqual(summary["request_count"], 1)
        self.assertIsNone(summary["latest_message_id"])
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])
        self.assertIsNone(summary["worker_blocked_reason"])

    def test_access_question_starts_codex_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(13, "@Pear What can you access from this topic?", False)],
        )

        self.assertEqual(summary["state"], "requested")
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["latest_request_message_id"], 13)
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_generic_chat_request_starts_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(13, "@Pear hey tell me something", False)],
        )

        self.assertEqual(summary["state"], "requested")
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["latest_request_message_id"], 13)
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_plain_bot_reply_settles_requested_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (13, "@Pear reply with only ok", False),
                (14, "ok", True, "Pear"),
            ],
        )

        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["latest_request_message_id"], 13)
        self.assertEqual(summary["latest_message_id"], 14)
        self.assertEqual(summary["recent_messages"][1]["content"], "ok")
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_worker_should_start_only_after_addressing_decision(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (13, "Fold in the failing terminal UI test too.", False),
            ],
            response_policy="ambient",
        )

        # Untagged topic traffic must first pass through the addressing
        # classifier. Topic history alone is not permission to invoke Codex.
        self.assertFalse(
            pear_worker_should_start(
                "Fold in the failing terminal UI test too.",
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                "Fold in the failing terminal UI test too.",
                active_session,
                explicitly_addressed=True,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_fix_bug_message("checkout crashes on empty cart"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start("@Pear debug the flaky parser test", active_session)
        )
        self.assertTrue(pear_worker_should_start("@Pear Hi", active_session))
        self.assertTrue(pear_worker_should_start("Hi @Pear", active_session))
        self.assertTrue(pear_worker_should_start("@Pear What's up!", active_session))
        # Explicit mentions are always delivered to Codex; Pear must not
        # suppress them with a lexical "trivial acknowledgement" filter.
        self.assertTrue(pear_worker_should_start("@Pear thanks!", active_session))
        self.assertTrue(pear_worker_should_start("thank you @Pear", active_session))
        self.assertTrue(
            pear_worker_should_start(
                "Hi, agent, how are you?",
                active_session,
                explicitly_addressed=True,
            )
        )
        self.assertTrue(pear_worker_should_start("@Pear hey tell me something", active_session))
        self.assertTrue(pear_worker_should_start("@Pear tell me a story", active_session))
        self.assertTrue(pear_worker_should_start("@Pear find answer to question", active_session))
        self.assertTrue(
            pear_worker_should_start(
                build_pear_status_check_message("checkout flow"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_handoff_message("next developer"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_catch_up_message("yesterday"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_find_answer_message("Where is auth configured?"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_review_message("current branch"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_plan_message("refactor checkout flow"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_checkpoint_message("checkout regression"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_apply_plan_message("checkout regression"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_save_work_message("checkout regression"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_verify_changes_message("checkout regression"),
                active_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                build_pear_draft_artifact_message("issue for flaky tests"),
                active_session,
            )
        )
        mentions_only_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
            response_policy="mentions_only",
        )
        self.assertFalse(
            pear_worker_should_start(
                "Fold in the failing terminal UI test too.",
                mentions_only_session,
            )
        )
        self.assertTrue(
            pear_worker_should_start("@Pear continue the checkout fix", mentions_only_session)
        )
        self.assertTrue(
            pear_worker_should_start(
                "continue the checkout fix",
                mentions_only_session,
                explicitly_addressed=True,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                "Hello.",
                mentions_only_session,
                explicitly_addressed=True,
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                "Fold in the failing terminal UI test too.",
                active_session,
                explicitly_addressed=True,
                response_policy="mentions_only",
            )
        )
        self.assertTrue(
            pear_worker_should_start(
                "Fold in the failing terminal UI test too.",
                mentions_only_session,
                explicitly_addressed=True,
                response_policy="ambient",
            )
        )

    def test_build_active_session_summary_without_stream_blocks_worker(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary(
            pear_session=payload,
            source="server",
            stream_exists=False,
            topic_message_count=0,
            latest_message_id=None,
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["worker_state"], "blocked_missing_stream")
        self.assertFalse(summary["worker_can_run"])
        self.assertIn("stream", summary["worker_blocked_reason"])

    def test_build_worker_progress_message(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="fix the checkout test",
            tool_results=[
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                }
            ],
        )

        self.assertEqual(progress_message, PEAR_WORKING_MESSAGE)
        self.assertNotIn("Status: working...", progress_message)
        self.assertNotIn("Task: fix the checkout test", progress_message)
        self.assertNotIn("Status: queued", progress_message)
        self.assertNotIn("Confirm repo, branch, and target runtime.", progress_message)
        self.assertNotIn("Verification: pytest zerver/tests/test_pear.py", progress_message)

    def test_build_worker_progress_message_shows_only_current_paragraph(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="answer quickly",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I’ll inspect the helper and repository first.",
                    "item_id": "msg-0",
                },
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "Partial answer",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(progress_message, "Partial answer")
        self.assertNotIn("Thinking", progress_message)
        self.assertNotIn("Status:", progress_message)

    def test_build_worker_progress_message_preserves_visible_assistant_text(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="answer quickly",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I'll check the repo first.",
                    "item_id": "msg-1",
                },
                {
                    "kind": "reasoning",
                    "status": "done",
                    "title": "Thinking",
                    "body": "private chain of thought",
                },
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "The answer is almost ready.",
                    "item_id": "msg-2",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(progress_message, "The answer is almost ready.")
        self.assertNotIn("private chain of thought", progress_message)
        self.assertNotIn("Status:", progress_message)

    def test_build_worker_progress_message_ignores_tool_transcript(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="check status",
            transcript_events=[
                {
                    "kind": "session",
                    "status": "done",
                    "title": "Codex thread 019f",
                    "body": "019f",
                },
                {
                    "kind": "status",
                    "status": "running",
                    "title": "Codex turn started.",
                    "body": "",
                },
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I'll check the repo state.",
                    "item_id": "msg-1",
                },
                {
                    "kind": "command",
                    "status": "running",
                    "title": "/bin/bash -lc pwd",
                    "body": "",
                    "item_id": "cmd-1",
                },
                {
                    "kind": "command",
                    "status": "done",
                    "title": "/bin/bash -lc pwd",
                    "body": "",
                    "item_id": "cmd-1",
                },
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "Result: Status checked.",
                    "item_id": "msg-2",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(progress_message, "Result: Status checked.")

    def test_build_worker_progress_message_collapses_running_tool_group(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="check status",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I'll run the checks.",
                    "item_id": "msg-1",
                },
                {
                    "kind": "command",
                    "status": "done",
                    "title": "/bin/bash -lc pwd",
                    "body": "",
                    "item_id": "cmd-1",
                },
                {
                    "kind": "command",
                    "status": "running",
                    "title": "/bin/bash -lc pytest",
                    "body": "",
                    "item_id": "cmd-2",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(progress_message, "I'll run the checks.")

    def test_build_worker_progress_message_coalesces_dynamic_tool_result(self) -> None:
        events = [
            {
                "kind": "tool",
                "status": "running",
                "title": "pear_artifact",
                "item_id": "tool-1",
                "metadata": {"tool_name": "pear_artifact"},
            },
            {
                "kind": "tool",
                "status": "done",
                "title": "Artifact ready.",
                "item_id": "tool-1",
                "metadata": {
                    "tool_name": "pear_artifact",
                    "human_tool_result": True,
                },
            },
            {
                "kind": "tool",
                "status": "done",
                "title": "pear_artifact",
                "item_id": "tool-1",
                "metadata": {"tool_name": "pear_artifact"},
            },
        ]

        self.assertEqual(len(pear_distinct_transcript_tool_events(events)), 1)
        self.assertEqual(
            build_pear_worker_progress_message(
                task="draw",
                transcript_events=events,
                tool_results=[],
            ),
            PEAR_WORKING_MESSAGE,
        )

    def test_distinct_transcript_tool_events_count_actions_not_updates(self) -> None:
        events = [
            {
                "kind": "command",
                "status": "running",
                "title": "read core skill",
                "item_id": "command-1",
            },
            {
                "kind": "command",
                "status": "done",
                "title": "read core skill",
                "item_id": "command-1",
            },
            {
                "kind": "command",
                "status": "done",
                "title": "read native guide",
                "item_id": "command-2",
            },
            {
                "kind": "tool",
                "status": "running",
                "title": "pear_artifact",
                "item_id": "tool-1",
            },
            {
                "kind": "tool",
                "status": "done",
                "title": "Artifact ready.",
                "item_id": "tool-1",
                "metadata": {"human_tool_result": True},
            },
            {
                "kind": "tool",
                "status": "done",
                "title": "pear_artifact",
                "item_id": "tool-1",
            },
        ]

        distinct_events = pear_distinct_transcript_tool_events(events)
        self.assertEqual(len(distinct_events), 3)
        self.assertEqual(
            [event["title"] for event in distinct_events],
            [
                "read core skill",
                "read native guide",
                "Artifact ready.",
            ],
        )

    def test_build_worker_progress_message_collapses_streaming_deltas_by_item(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="answer quickly",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "Partial ",
                    "item_id": "msg-1",
                },
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "Partial answer",
                    "item_id": "msg-1",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(progress_message, "Partial answer")

    def test_build_worker_message_normalizes_markdown_image_syntax(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="find images",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "![Pikachu](https://img.pokemondb.net/artwork/large/pikachu.jpg)\n\n![Bulbasaur](https://img.pokemondb.net/artwork/large/bulbasaur.jpg)",
                }
            ],
            tool_results=[],
        )
        completed_message = build_pear_worker_completed_message(
            task="find images",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "![Pikachu](https://img.pokemondb.net/artwork/large/pikachu.jpg)",
                }
            ],
            tool_results=[],
        )

        self.assertEqual(
            progress_message,
            "[Pikachu](https://img.pokemondb.net/artwork/large/pikachu.jpg)\n\n[Bulbasaur](https://img.pokemondb.net/artwork/large/bulbasaur.jpg)",
        )
        self.assertEqual(
            completed_message,
            "[Pikachu](https://img.pokemondb.net/artwork/large/pikachu.jpg)",
        )
        self.assertNotIn("![", progress_message)
        self.assertNotIn("![", completed_message)

    def test_worker_runtime_runs_tool_when_ready(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        seen_worker_states: list[str] = []
        seen_tasks: list[str] = []

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            seen_worker_states.append(active_session["worker_state"])
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                }
            ]

        result = PearWorkerRuntime(tool_runner).run(
            active_session=active_session,
            task="fix the checkout test",
        )

        self.assertTrue(result["should_post"])
        self.assertTrue(result["worker_can_run"])
        self.assertEqual(result["tool_count"], 1)
        self.assertEqual(
            result["tool_results"],
            [{"label": "Verification", "value": "pytest zerver/tests/test_pear.py"}],
        )
        self.assertEqual(seen_worker_states, ["ready"])
        self.assertEqual(seen_tasks, ["fix the checkout test"])
        self.assertIsNotNone(result["content"])
        self.assertEqual(result["content"], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_worker_runtime_close_delegates_to_tool_runner(self) -> None:
        class CloseableToolRunner:
            def __init__(self) -> None:
                self.closed = False

            def __call__(
                self,
                active_session: PearActiveSessionSummary,
                task: str,
                *,
                event_callback: PearWorkerTranscriptCallback | None = None,
                **kwargs: object,
            ) -> list[dict[str, str]]:
                return []

            def close(self) -> None:
                self.closed = True

        tool_runner = CloseableToolRunner()
        PearWorkerRuntime(tool_runner).close()

        self.assertTrue(tool_runner.closed)

    def test_pear_worker_runtime_cache_eviction_closes_lru_runtime(self) -> None:
        class FakeRuntime(PearWorkerRuntime):
            def __init__(self, label: str) -> None:
                super().__init__()
                self.label = label
                self.closed = False

            def close(self) -> None:
                self.closed = True

        created_runtimes: list[FakeRuntime] = []

        def build_runtime(**kwargs: object) -> FakeRuntime:
            runtime = FakeRuntime(str(kwargs["workdir_override"]))
            created_runtimes.append(runtime)
            return runtime

        pear_worker_module.close_pear_worker_runtimes()
        try:
            with (
                patch.object(
                    pear_worker_module,
                    "PEAR_WORKER_RUNTIME_CACHE_LIMIT",
                    2,
                ),
                patch.object(
                    pear_worker_module,
                    "build_pear_worker_runtime_from_settings",
                    side_effect=build_runtime,
                ),
            ):
                first_runtime = pear_worker_module.get_pear_worker_runtime(
                    "model-a",
                    "/tmp/pear-a",
                    "medium",
                    None,
                    None,
                )
                second_runtime = pear_worker_module.get_pear_worker_runtime(
                    "model-a",
                    "/tmp/pear-b",
                    "medium",
                    None,
                    None,
                )
                self.assertIs(
                    pear_worker_module.get_pear_worker_runtime(
                        "model-a",
                        "/tmp/pear-a",
                        "medium",
                        None,
                        None,
                    ),
                    first_runtime,
                )
                third_runtime = pear_worker_module.get_pear_worker_runtime(
                    "model-a",
                    "/tmp/pear-c",
                    "medium",
                    None,
                    None,
                )

                self.assertEqual(created_runtimes, [first_runtime, second_runtime, third_runtime])
                self.assertFalse(first_runtime.closed)
                self.assertTrue(second_runtime.closed)
                self.assertFalse(third_runtime.closed)
                self.assertEqual(
                    list(pear_worker_module.pear_worker_runtimes.values()),
                    [first_runtime, third_runtime],
                )

                pear_worker_module.close_pear_worker_runtimes()

                self.assertTrue(first_runtime.closed)
                self.assertTrue(third_runtime.closed)
                self.assertEqual(pear_worker_module.pear_worker_runtimes, {})
        finally:
            pear_worker_module.close_pear_worker_runtimes()

    def test_pear_worker_runtime_cache_is_per_worktree_not_model(self) -> None:
        class FakeRuntime(PearWorkerRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.configurations: list[tuple[str, str]] = []

            def configure_turn(self, *, model: str, reasoning_effort: str) -> None:
                self.configurations.append((model, reasoning_effort))

        runtime = FakeRuntime()
        pear_worker_module.close_pear_worker_runtimes()
        try:
            with patch.object(
                pear_worker_module,
                "build_pear_worker_runtime_from_settings",
                return_value=runtime,
            ) as build_runtime:
                first = pear_worker_module.get_pear_worker_runtime(
                    "gpt-5.5",
                    "/srv/pear-workspaces/topic-a",
                    "medium",
                    None,
                    None,
                )
                second = pear_worker_module.get_pear_worker_runtime(
                    "gpt-5.6-luna",
                    "/srv/pear-workspaces/topic-a",
                    "high",
                    None,
                    None,
                )

            self.assertIs(first, second)
            build_runtime.assert_called_once()
            self.assertEqual(
                runtime.configurations,
                [("gpt-5.5", "medium"), ("gpt-5.6-luna", "high")],
            )
        finally:
            pear_worker_module.close_pear_worker_runtimes()

    def test_pear_worker_runtime_cache_reaps_idle_worktrees(self) -> None:
        class FakeRuntime(PearWorkerRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.closed = False

            def close(self) -> None:
                self.closed = True

        runtime = FakeRuntime()
        runtime_key = ("/srv/pear-workspaces/topic-a", "danger-full-access", "")
        pear_worker_module.close_pear_worker_runtimes()
        try:
            with patch.object(pear_worker_module, "PEAR_WORKER_RUNTIME_IDLE_SECONDS", 30):
                pear_worker_module.cache_pear_worker_runtime(
                    runtime_key,
                    runtime,
                    current_time=10,
                )
                pear_worker_module.reap_idle_pear_worker_runtimes(current_time=39)
                self.assertFalse(runtime.closed)
                pear_worker_module.reap_idle_pear_worker_runtimes(current_time=40)

            self.assertTrue(runtime.closed)
            self.assertEqual(pear_worker_module.pear_worker_runtimes, {})
            self.assertEqual(pear_worker_module.pear_worker_runtime_last_used, {})
        finally:
            pear_worker_module.close_pear_worker_runtimes()

    def test_evict_pear_worker_runtime_removes_every_cache_alias_and_closes_once(self) -> None:
        class FakeRuntime(PearWorkerRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.close_count = 0

            def close(self) -> None:
                self.close_count += 1

        stolen_runtime = FakeRuntime()
        home_runtime = FakeRuntime()
        stolen_keys = (
            ("/srv/pear-workspaces/stolen-a", "danger-full-access", ""),
            ("/srv/pear-workspaces/stolen-b", "danger-full-access", ""),
        )
        home_key = ("/srv/pear-workspaces/home", "danger-full-access", "")
        pear_worker_module.close_pear_worker_runtimes()
        try:
            for index, runtime_key in enumerate(stolen_keys):
                pear_worker_module.pear_worker_runtimes[runtime_key] = stolen_runtime
                pear_worker_module.pear_worker_runtime_last_used[runtime_key] = float(index)
            pear_worker_module.pear_worker_runtimes[home_key] = home_runtime
            pear_worker_module.pear_worker_runtime_last_used[home_key] = 3.0

            pear_worker_module.evict_pear_worker_runtime(stolen_runtime)

            self.assertEqual(stolen_runtime.close_count, 1)
            self.assertEqual(
                list(pear_worker_module.pear_worker_runtimes.items()),
                [(home_key, home_runtime)],
            )
            self.assertEqual(pear_worker_module.pear_worker_runtime_last_used, {home_key: 3.0})
            self.assertEqual(home_runtime.close_count, 0)
        finally:
            pear_worker_module.close_pear_worker_runtimes()

    def test_pear_worker_liveness_loop_runs_until_stopped_and_closes_connections(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.run_liveness_stop = Mock()
        worker.run_liveness_stop.wait.side_effect = [False, True]

        with (
            patch.object(worker, "run_liveness_pass") as run_liveness_pass,
            patch.object(pear_worker_module, "close_old_connections") as close_connections,
            patch.object(pear_worker_module, "reset_queries") as reset_queries,
        ):
            worker.run_liveness_loop()

        worker.run_liveness_stop.wait.assert_any_call(
            pear_worker_module.PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS
        )
        run_liveness_pass.assert_called_once_with()
        reset_queries.assert_called_once_with()
        self.assertGreaterEqual(close_connections.call_count, 2)

    def test_pear_worker_liveness_pass_uses_the_active_attempt(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.active_run_id = 42
        worker.active_run_attempt = 3

        with patch.object(
            pear_worker_module,
            "maintain_pear_worker_run_liveness",
            return_value=2,
        ) as maintain_liveness:
            self.assertEqual(worker.run_liveness_pass(), 2)

        maintain_liveness.assert_called_once_with(
            active_run_id=42,
            active_run_attempt=3,
            session_kinds=worker.OWNED_SESSION_KINDS,
        )

    def test_pear_worker_liveness_stop_is_bounded_and_reports_a_stuck_thread(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.run_liveness_stop = Mock()
        worker.run_liveness_thread = Mock()
        worker.run_liveness_thread.is_alive.return_value = True

        with self.assertLogs("zerver.worker.pear_worker", level="WARNING"):
            worker.stop_liveness_loop()

        worker.run_liveness_stop.set.assert_called_once_with()
        worker.run_liveness_thread.join.assert_called_once_with(
            timeout=pear_worker_module.PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS
        )

    def test_pear_worker_consume_resets_active_lease_and_runtime_on_failure(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        stale_runtime = Mock(spec=PearWorkerRuntime)
        claimed_runtime = Mock(spec=PearWorkerRuntime)
        worker.active_run_id = 10
        worker.active_run_attempt = 1
        worker.active_runtime = stale_runtime

        def fail_after_claim(_event: dict[str, object]) -> NoReturn:
            self.assertIsNone(worker.active_run_id)
            self.assertIsNone(worker.active_run_attempt)
            self.assertIsNone(worker.active_runtime)
            worker.active_run_id = 20
            worker.active_run_attempt = 2
            worker.active_runtime = claimed_runtime
            raise RuntimeError("turn failed")

        with (
            patch.object(worker, "_consume", side_effect=fail_after_claim),
            self.assertRaisesRegex(RuntimeError, "turn failed"),
        ):
            worker.consume({"message_id": 20})

        self.assertIsNone(worker.active_run_id)
        self.assertIsNone(worker.active_run_attempt)
        self.assertIsNone(worker.active_runtime)

    def test_pear_worker_propagates_pre_turn_failure_to_the_queue(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.q = None
        worker.idle = False
        worker.threaded = False
        worker.disable_timeout = True
        worker.consumed_since_last_emptied = 0
        worker.recent_consume_times = []
        worker.consume_iteration_counter = 0
        worker.queue_last_emptied_timestamp = 0

        with (
            patch.object(worker, "consume", side_effect=RuntimeError("setup failed")),
            patch.object(worker, "_handle_consume_exception") as handle_exception,
            patch.object(worker, "update_statistics"),
            self.assertRaisesRegex(RuntimeError, "setup failed"),
        ):
            worker.consume_single_event({"message_id": 20})

        handle_exception.assert_called_once()

    def test_monitor_worker_keeps_the_standard_queue_exception_contract(self) -> None:
        from zerver.worker.pear_monitor_worker import PearMonitorWorker

        self.assertFalse(PearMonitorWorker.RERAISE_CONSUME_EXCEPTIONS)
        monitor = object.__new__(PearMonitorWorker)
        monitor.queue_name = "pear_monitor_worker"
        monitor.worker_num = None
        self.assertEqual(monitor.statistics_file_name(), "pear_monitor_worker.stats")

    def test_pear_worker_liveness_loss_aborts_the_current_runtime(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.run_liveness_stop = Mock()
        worker.run_liveness_stop.wait.side_effect = [False, True]
        worker.active_runtime = Mock(spec=PearWorkerRuntime)
        lease_error = PearWorkerLeaseLostError("attempt 1 lost its lease")

        with (
            patch.object(worker, "run_liveness_pass", side_effect=lease_error),
            patch.object(pear_worker_module, "close_old_connections"),
            patch.object(pear_worker_module, "reset_queries"),
            self.assertLogs("zerver.worker.pear_worker", level="WARNING"),
        ):
            worker.run_liveness_loop()

        worker.active_runtime.abort.assert_called_once_with(lease_error)

    def test_worker_completed_message_uses_distinct_status_line(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task="fix the checkout test",
            tool_results=[
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                }
            ],
        )

        self.assertEqual(completed_message, PEAR_NO_VISIBLE_RESPONSE_MESSAGE)
        self.assertNotIn("Status: done", completed_message)
        self.assertNotIn("Status: working...", completed_message)
        self.assertNotIn("Verification: pytest zerver/tests/test_pear.py", completed_message)

    def test_worker_completed_message_shows_runtime_error(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task="fix the checkout test",
            tool_results=[
                {
                    "label": "Error",
                    "value": "Pear worker agent runtime is disabled.",
                },
            ],
        )

        self.assertEqual(completed_message, "Pear worker agent runtime is disabled.")

    def test_worker_completed_message_preserves_prose_without_duplicate_result(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task="hi",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "Repo: /home/vagrant/zulip\nHi. Ready when you are.",
                }
            ],
            tool_results=[
                {
                    "label": "Result",
                    "value": "Hi. Ready when you are.",
                },
            ],
        )

        self.assertEqual(completed_message, "Repo: /home/vagrant/zulip\nHi. Ready when you are.")
        self.assertNotIn("Result: Hi. Ready when you are.", completed_message)
        self.assertNotIn("Session: Codex thread 123", completed_message)

    def test_worker_completed_message_preserves_multiline_answer(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task='choose between A, B, D, and "Tokenmaxxer"',
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "Repo: /Users/progyan/codack/zulip\nRuntime: Codex/Pear in Zulip topic `devel > topic`\nVerification: Not run; choice-only request.\n\nTokenmaxxer.",
                }
            ],
            tool_results=[
                {
                    "label": "Result",
                    "value": "Tokenmaxxer.",
                },
            ],
        )

        self.assertEqual(
            completed_message,
            "Repo: /Users/progyan/codack/zulip\nRuntime: Codex/Pear in Zulip topic `devel > topic`\nVerification: Not run; choice-only request.\n\nTokenmaxxer.",
        )
        self.assertNotIn("Result: Tokenmaxxer.", completed_message)
        self.assertNotIn('Task: choose between A, B, D, and "Tokenmaxxer"', completed_message)
        self.assertNotIn("Runtime: codex-exec cwd=/home/vagrant/zulip", completed_message)

    def test_worker_completed_message_keeps_only_final_answer(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task="what's up",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I'll check the repo state.",
                    "item_id": "msg-1",
                },
                {
                    "kind": "command",
                    "status": "done",
                    "title": "/bin/bash -lc 'pwd && git status --short --branch'",
                    "body": "",
                    "item_id": "cmd-1",
                },
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "Repo: `/home/vagrant/zulip`\nVerification: `git status --short --branch` completed successfully\nResult: Status checked; ready for the next concrete task.",
                    "item_id": "msg-2",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(
            completed_message,
            "Repo: `/home/vagrant/zulip`\nVerification: `git status --short --branch` completed successfully\nResult: Status checked; ready for the next concrete task.",
        )

    def test_worker_completed_artifact_message_does_not_replay_work_trace(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task="explain the projection",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I’m using the artifact skill because motion clarifies projection.",
                    "item_id": "msg-1",
                },
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "I’m checking the renderer and probing the output now.",
                    "item_id": "msg-2",
                },
                {
                    "kind": "command",
                    "status": "running",
                    "title": "render visual",
                    "body": "",
                    "item_id": "cmd-1",
                },
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "The first poster needs a tighter composition.",
                    "item_id": "msg-retry",
                },
                {
                    "kind": "command",
                    "status": "failed",
                    "title": "inspect first poster",
                    "body": "",
                    "item_id": "cmd-preview",
                },
                {
                    "kind": "command",
                    "status": "done",
                    "title": "render visual",
                    "body": "",
                    "item_id": "cmd-1",
                },
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "Watch the ambient vector rotate into the tangent space.",
                    "item_id": "msg-3",
                },
            ],
            tool_results=[{"label": "Artifact", "value": '{"version":1}'}],
        )

        self.assertEqual(
            completed_message,
            "Watch the ambient vector rotate into the tangent space.",
        )

    def test_worker_completed_message_preserves_final_answer_after_long_tool_details(self) -> None:
        transcript_events = [
            {
                "kind": "assistant",
                "status": "done",
                "title": "Assistant",
                "body": "I'll inspect the runtime state first.",
                "item_id": "msg-1",
            },
            *[
                {
                    "kind": "command",
                    "status": "done",
                    "title": f"/bin/bash -lc 'echo {index} {'x' * 220}'",
                    "body": "",
                    "item_id": f"cmd-{index}",
                }
                for index in range(80)
            ],
            {
                "kind": "assistant",
                "status": "done",
                "title": "Assistant",
                "body": "Final answer: the context bug is fixed.",
                "item_id": "msg-2",
            },
        ]

        completed_message = build_pear_worker_completed_message(
            task="fix context",
            transcript_events=transcript_events,
            tool_results=[],
        )

        self.assertLessEqual(len(completed_message), 5000)
        self.assertNotIn("I'll inspect the runtime state first.", completed_message)
        self.assertIn("Final answer: the context bug is fixed.", completed_message)

    def test_worker_completed_message_chunks_preserve_long_final_answer(self) -> None:
        long_answer = "\n\n".join(
            f"Paragraph {index}: {'answer text ' * 80}" for index in range(12)
        )

        chunks = build_pear_worker_completed_message_chunks(
            task="summarize sources",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Codex",
                    "body": long_answer,
                    "item_id": "answer",
                }
            ],
            tool_results=[],
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 5000 for chunk in chunks))
        joined = "\n\n".join(chunks)
        self.assertIn("Paragraph 0:", joined)
        self.assertIn("Paragraph 11:", joined)
        self.assertNotIn("[truncated]", joined)

    def test_split_pear_persistent_message_splits_oversized_single_line(self) -> None:
        chunks = split_pear_persistent_message("x" * 12050, limit=5000)

        self.assertEqual([len(chunk) for chunk in chunks], [5000, 5000, 2050])

    def test_worker_runtime_refuses_paused_session_without_running_tools(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "@Pear Pause session:\n\nReason: waiting for CI", False),
            ],
            is_paused=True,
        )
        tool_was_called = False

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            nonlocal tool_was_called
            tool_was_called = True
            return []

        result = PearWorkerRuntime(tool_runner).run(
            active_session=active_session,
            task="fix the checkout test",
        )

        self.assertTrue(result["should_post"])
        self.assertFalse(result["worker_can_run"])
        self.assertEqual(result["tool_count"], 0)
        self.assertFalse(tool_was_called)
        self.assertIsNotNone(result["content"])
        self.assertIn("Pear worker is not starting.", result["content"])
        self.assertIn("paused", result["content"])

    def test_codex_prompt_includes_task_and_session_context(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False, "plugyawn"),
                (11, "Verification: pytest zerver/tests/test_pear.py", True, "Pear"),
                (13, "Memory: Prefer small focused PRs", False, "plugyawn"),
                (14, "Routine: Watch PR status for pear/runtime", False, "plugyawn"),
                (15, "Done: Tests pass and draft PR is posted", False, "plugyawn"),
                (16, "Routine cadence: manual", False, "plugyawn"),
                (17, "Memory forgotten: Run terminal tests first", False, "plugyawn"),
                (
                    18,
                    "@Pear Steering update:\n\nRequest: keep the patch small",
                    False,
                    "plugyawn",
                ),
            ],
            workspace_context={
                "repository": "zulip/zulip",
                "remote_url": "https://github.com/zulip/zulip.git",
                "worktree": "devel-pear-efficient-smoke",
                "workdir": "/home/zulipdev/pear-workspaces/devel-pear-efficient-smoke",
                "branch": "devel-pear-efficient-smoke",
                "head": "",
            },
        )

        prompt = build_pear_codex_turn_prompt(active_session, "fix the checkout test")

        self.assertIn("Task: fix the checkout test", prompt)
        self.assertIn(f"Zulip stream: {PEAR_DEFAULT_STREAM}", prompt)
        self.assertIn(f"Zulip topic: {PEAR_DEFAULT_TOPIC}", prompt)
        self.assertIn("@Pear Steering update:", prompt)
        self.assertIn("Request: keep the patch small", prompt)
        self.assertIn("Workspace context:", prompt)
        self.assertIn("- repository: zulip/zulip", prompt)
        self.assertIn("- worktree: devel-pear-efficient-smoke", prompt)
        self.assertIn("- branch: devel-pear-efficient-smoke", prompt)
        self.assertIn(
            "- contract: the Zulip stream maps to the repository, and the Zulip topic/subthread maps to the Git branch.",
            prompt,
        )
        self.assertIn(
            "- privacy: refer to the workspace by branch or worktree name in replies; "
            "never print the absolute workdir path in the topic.",
            prompt,
        )
        self.assertNotIn("Durable topic context lives in README.md", prompt)
        self.assertNotIn("Use Codex-native skills from CODEX_HOME/skills", prompt)
        self.assertNotIn(
            "For casual, conversational, choice-only, or answer-only requests, respond directly.",
            prompt,
        )
        self.assertNotIn(
            "Do not run repository/session checks just to make a casual reply sound grounded.",
            prompt,
        )
        self.assertNotIn(
            "Resolve references like 'above', 'that', 'what did I ask', and 'who is here'",
            prompt,
        )
        self.assertIn("Visible recent human participants:\n- plugyawn", prompt)
        self.assertIn("Recent Zulip topic transcript, oldest to newest:", prompt)
        self.assertIn("#10 plugyawn (human): @Pear fix the checkout test", prompt)
        self.assertIn(
            "#11 Pear (bot): Verification: pytest zerver/tests/test_pear.py",
            prompt,
        )
        self.assertNotIn("Pear agent skill definitions:", prompt)
        self.assertNotIn("\nChannel memory:\n", prompt)
        self.assertNotIn("\nForgotten memory:\n", prompt)
        self.assertNotIn("\nDefinition of done:\n", prompt)
        self.assertNotIn("\nStanding routines:\n", prompt)
        self.assertNotIn("\nRoutine cadence:\n", prompt)
        self.assertNotIn("Verification: <command or check result>", prompt)
        self.assertNotIn("Session: <session record URL or Codex thread reference>", prompt)
        self.assertNotIn("Ticket: <ticket URL, ID, or path>", prompt)
        self.assertNotIn("Issue: <issue URL, ID, or path>", prompt)
        self.assertNotIn("Reviewers: <reviewer handles or teams>", prompt)
        self.assertNotIn("Recent artifacts:", prompt)
        self.assertNotIn("Recent artifacts:\n- Memory", prompt)
        self.assertNotIn("Recent artifacts:\n- Routine", prompt)
        self.assertNotIn("Recent artifacts:\n- Done", prompt)
        self.assertNotIn("Done: <definition of done or success criteria>", prompt)
        self.assertNotIn(
            "if you announce the pear-artifacts skill, you must complete a successful call",
            prompt,
        )
        self.assertNotIn("must not substitute ASCII art or a fenced diagram", prompt)

    def test_codex_developer_instructions_are_byte_stable(self) -> None:
        payload = get_pear_session_payload()
        active_session_a = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix checkout", False)],
        )
        active_session_b = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (20, "@Pear remember this decision", False),
                (21, "Memory: Prefer small PRs", False),
                (22, "Routine: Check the PR every weekday", False),
            ],
        )

        instructions = build_pear_codex_developer_instructions()

        self.assertEqual(instructions, build_pear_codex_developer_instructions())
        self.assertNotIn("Memory: <channel-scoped note Pear should remember>", instructions)
        self.assertNotIn("Routine: <standing follow-up", instructions)
        self.assertNotIn("Repo: <repository path or remote>", instructions)
        self.assertNotIn("Include artifact lines", instructions)
        self.assertIn("Pear never parses your wording into hidden state", instructions)
        self.assertIn("README.md", instructions)
        self.assertNotIn(instructions, build_pear_codex_turn_prompt(active_session_a, "fix"))
        self.assertNotIn(instructions, build_pear_codex_turn_prompt(active_session_b, "fix"))

    def test_resumed_codex_prompt_contains_only_unseen_messages_and_current_request(self) -> None:
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(20, "a new human observation", False, "berry")],
            latest_codex_thread_id="thread-existing",
        )

        prompt = build_pear_codex_turn_prompt(active_session, "continue from that observation")

        self.assertEqual(
            prompt,
            "New Zulip messages since your previous turn:\n- #20 berry (human): a new human observation\n\nCurrent request:\ncontinue from that observation",
        )
        self.assertNotIn("Workspace context:", prompt)
        self.assertNotIn("Recent artifacts:", prompt)

        active_session["recent_messages"] = []
        self.assertEqual(
            build_pear_codex_turn_prompt(active_session, "a direct follow-up"),
            "a direct follow-up",
        )

    def test_codex_skills_are_provisioned_into_codex_home(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as codex_home:
            source_skill = Path(source_root) / "workspace-editing"
            source_skill.mkdir()
            (source_skill / "SKILL.md").write_text(
                "# workspace-editing\n\nEdit workspace files safely.\n",
                encoding="utf-8",
            )
            ignored_dir = Path(source_root) / "not-a-skill"
            ignored_dir.mkdir()
            (ignored_dir / "notes.md").write_text("not copied\n", encoding="utf-8")

            with patch("zerver.lib.pear_worker.settings.PEAR_CODEX_SKILLS_DIR", source_root):
                provisioned = pear_provision_codex_skills(codex_home)

            destination = Path(codex_home) / "skills" / "workspace-editing" / "SKILL.md"
            self.assertEqual(provisioned, ["workspace-editing"])
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                "# workspace-editing\n\nEdit workspace files safely.\n",
            )
            self.assertFalse((Path(codex_home) / "skills" / "not-a-skill").exists())
            self.assertEqual(
                json.loads(
                    (Path(codex_home) / "skills" / ".pear-managed-skills.json").read_text(
                        encoding="utf-8"
                    )
                ),
                {"version": 1, "skills": ["workspace-editing"]},
            )

    def test_agent_text_results_preserve_final_prose_as_one_result(self) -> None:
        final_text = "Verification: pytest zerver/tests/test_pear.py\nDiff: updated Pear runtime"
        results = pear_worker_results_from_final_text(
            final_text,
            output_limit=200,
        )

        self.assertEqual(
            results,
            [
                {
                    "label": "Result",
                    "value": final_text,
                },
            ],
        )

    def test_codex_app_server_tool_runner_runs_under_topic_os_user(self) -> None:
        process_isolation = PearProcessIsolation(
            username="pear-0123456789abcdef01234567",
            home="/home/pear-0123456789abcdef01234567",
            codex_home="/home/pear-0123456789abcdef01234567/.codex",
        )
        runner = PearCodexAppServerToolRunner(
            codex_bin="/srv/zulip-node/bin/codex",
            workdir="/home/zulipdev/pear-workspaces/topic",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            process_isolation=process_isolation,
        )

        self.assertEqual(
            runner.build_command()[:10],
            [
                "sudo",
                "-n",
                "-H",
                "-u",
                "pear-0123456789abcdef01234567",
                "env",
                "HOME=/home/pear-0123456789abcdef01234567",
                "USER=pear-0123456789abcdef01234567",
                "LOGNAME=pear-0123456789abcdef01234567",
                "CODEX_HOME=/home/pear-0123456789abcdef01234567/.codex",
            ],
        )
        self.assertIn("/srv/zulip-node/bin/codex", runner.build_command())

    def test_codex_app_server_resume_does_not_resend_developer_instructions(self) -> None:
        runner = PearCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            service_tier="fast",
            fast_mode_enabled=True,
        )

        self.assertIn("developerInstructions", runner.thread_params())
        resumed_params = runner.thread_params(existing_thread_id="thread-existing")

        self.assertEqual(resumed_params["threadId"], "thread-existing")
        self.assertNotIn("developerInstructions", resumed_params)
        self.assertNotIn("ephemeral", resumed_params)
        self.assertNotIn("dynamicTools", resumed_params)

    def test_gemini_interactions_addressing_router_uses_stateless_json(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                pass

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "status": "completed",
                        "steps": [
                            {
                                "type": "model_output",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            '{"addressee":"pear","action":"run",'
                                            '"presentation":"diff"}'
                                        ),
                                    }
                                ],
                            }
                        ],
                    }
                ).encode()

        def fake_urlopen(request, **kwargs):
            calls.append((request, kwargs))
            return FakeResponse()

        classifier = PearGeminiInteractionsAddressingIntentClassifier(
            api_key="gemini-secret",
            model="gemini-3.1-flash-lite",
            timeout_seconds=7,
            output_limit=200,
            url="https://example.test/interactions",
            thinking_level="minimal",
            urlopen=fake_urlopen,
        )

        self.assertEqual(
            classifier(
                content="hey pear, can you check this?",
                active_session=active_session,
                active_run=False,
                message={
                    "display_recipient": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                bot_handler=object(),
            ),
            {
                "addressee": "pear",
                "action": "run",
                "presentation": "diff",
            },
        )
        self.assertEqual(len(calls), 1)
        request, kwargs = calls[0]
        request_payload = json.loads(request.data.decode())
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(request.full_url, "https://example.test/interactions")
        self.assertEqual(request.headers["Api-revision"], "2026-05-20")
        self.assertEqual(request_payload["model"], "gemini-3.1-flash-lite")
        self.assertIs(request_payload["store"], False)
        self.assertEqual(request_payload["generation_config"]["temperature"], 0)
        self.assertEqual(request_payload["generation_config"]["max_output_tokens"], 200)
        self.assertEqual(request_payload["generation_config"]["thinking_level"], "minimal")
        self.assertEqual(request_payload["response_format"]["mime_type"], "application/json")
        self.assertEqual(
            request_payload["response_format"]["schema"]["required"],
            ["addressee", "action", "presentation"],
        )
        self.assertIn("Do not answer the user.", request_payload["input"])
        self.assertIn("Recent topic messages", request_payload["input"])
        self.assertIn("@Pear fix the checkout test", request_payload["input"])
        self.assertIn("New message:\nhey pear, can you check this?", request_payload["input"])
        self.assertIn("Treat a repeated concrete request as intentional", request_payload["input"])

    def test_parse_pear_addressing_router_output_defaults_to_ignore(self) -> None:
        self.assertEqual(
            parse_pear_addressing_json_output("not json"),
            {
                "addressee": "uncertain",
                "action": "ignore",
                "presentation": "compact",
            },
        )

    def test_build_pear_addressing_router_prompt_includes_session_context(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        prompt = build_pear_addressing_router_prompt(
            content="hey agent, can you check?",
            active_session=active_session,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
        )

        self.assertIn("action: ignore", prompt)
        self.assertIn("addressee=uncertain and action=ignore", prompt)
        self.assertIn("Recent topic messages", prompt)
        self.assertIn(f"Stream: {PEAR_DEFAULT_STREAM}", prompt)
        self.assertIn("New message:\nhey agent, can you check?", prompt)

    def test_normalize_token_usage_handles_app_server_payload(self) -> None:
        usage = pear_normalize_token_usage(
            {
                "tokenUsage": {
                    "last": {
                        "inputTokens": 100,
                        "cachedInputTokens": 60,
                        "outputTokens": 12,
                        "reasoningOutputTokens": 3,
                        "totalTokens": 115,
                    }
                }
            }
        )

        self.assertEqual(
            usage,
            {
                "input_tokens": 100,
                "cached_input_tokens": 60,
                "output_tokens": 12,
                "reasoning_output_tokens": 3,
                "total_tokens": 115,
                "cache_percent": 60.0,
            },
        )

    def test_codex_app_server_tool_runner_reuses_process_and_steers_turn(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []
        processes = []

        class FakeStdin:
            def __init__(self) -> None:
                self.lines: list[dict[str, object]] = []

            def write(self, value: str) -> int:
                self.lines.append(json.loads(value))
                return len(value)

            def flush(self) -> None:
                pass

        class FakeStream:
            def __init__(self, lines: list[str]) -> None:
                self.lines = lines

            def __iter__(self):
                return iter(self.lines)

            def read(self) -> str:
                return ""

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()
                self.stdout = FakeStream(
                    [
                        '{"id":1,"result":{"userAgent":"pear-test"}}\n',
                        '{"id":2,"result":{"thread":{"id":"thread-app"}}}\n',
                        '{"id":3,"result":{"turn":{"id":"turn-app"}}}\n',
                        (
                            '{"method":"turn/started","params":{"threadId":"thread-app",'
                            '"turn":{"id":"turn-app"}}}\n'
                        ),
                        (
                            '{"method":"item/started","params":{"threadId":"thread-app",'
                            '"turnId":"turn-app","item":{"id":"cmd-1",'
                            '"type":"commandExecution","command":"pytest",'
                            '"status":"inProgress"}}}\n'
                        ),
                        '{"id":4,"result":{"turnId":"turn-app"}}\n',
                        (
                            '{"method":"thread/tokenUsage/updated","params":{'
                            '"threadId":"thread-app","turnId":"turn-app",'
                            '"tokenUsage":{"last":{"inputTokens":100,'
                            '"cachedInputTokens":60,"outputTokens":12,'
                            '"reasoningOutputTokens":3,"totalTokens":115},'
                            '"total":{"inputTokens":100,"cachedInputTokens":60,'
                            '"outputTokens":12,"reasoningOutputTokens":3,'
                            '"totalTokens":115}}}}\n'
                        ),
                        (
                            '{"method":"item/completed","params":{"threadId":"thread-app",'
                            '"turnId":"turn-app","item":{"id":"cmd-1",'
                            '"type":"commandExecution","command":"pytest",'
                            '"status":"completed"}}}\n'
                        ),
                        (
                            '{"method":"item/agentMessage/delta","params":{"threadId":"thread-app",'
                            '"turnId":"turn-app","itemId":"msg-1","delta":"Done."}}\n'
                        ),
                        (
                            '{"method":"item/completed","params":{"threadId":"thread-app",'
                            '"turnId":"turn-app","item":{"id":"msg-1",'
                            '"type":"agentMessage","text":"Verification: app-server ok"}}}\n'
                        ),
                        (
                            '{"method":"turn/completed","params":{"threadId":"thread-app",'
                            '"turn":{"id":"turn-app"}}}\n'
                        ),
                    ]
                )
                self.stderr = FakeStream([])

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                pass

        def fake_popen(command, **kwargs):
            calls.append((command, kwargs))
            process = FakeProcess()
            processes.append(process)
            return process

        runner = PearCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="danger-full-access",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            service_tier="fast",
            fast_mode_enabled=True,
            reasoning_effort="medium",
            popen_command=fake_popen,
        )
        streamed_events = []

        def record_event(event) -> None:
            streamed_events.append(event)
            if event["kind"] == "command" and event["status"] == "running":
                self.assertTrue(runner.steer("Make it the 30th prime."))

        results = list(
            runner(
                active_session,
                "fix the checkout test",
                event_callback=record_event,
            )
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][0],
            [
                "codex",
                "app-server",
                "--stdio",
                "--config",
                'service_tier="fast"',
                "--config",
                "features.fast_mode=true",
                "--config",
                "features.memories=true",
                "--config",
                'model_reasoning_effort="medium"',
            ],
        )
        sent_methods = [line["method"] for line in processes[0].stdin.lines]
        self.assertEqual(
            sent_methods,
            ["initialize", "initialized", "thread/start", "turn/start", "turn/steer"],
        )
        thread_start_request = processes[0].stdin.lines[2]
        self.assertIn(
            "developerInstructions",
            thread_start_request["params"],
        )
        self.assertIn("README.md", thread_start_request["params"]["developerInstructions"])
        steering_request = processes[0].stdin.lines[-1]
        self.assertEqual(steering_request["params"]["expectedTurnId"], "turn-app")
        self.assertEqual(
            steering_request["params"]["input"],
            [{"type": "text", "text": "Make it the 30th prime."}],
        )
        self.assertEqual(
            [event["kind"] for event in streamed_events],
            [
                "session",
                "status",
                "command",
                "usage",
                "command",
                "assistant",
                "assistant",
                "status",
            ],
        )
        self.assertEqual(streamed_events[3]["metadata"]["usage"]["cache_percent"], 60.0)
        self.assertIn({"label": "Result", "value": "Verification: app-server ok"}, results)
        self.assertEqual(runner.codex_thread_id(), "thread-app")
        self.assertEqual(runner.loaded_through_trigger_message_id, 10)

    def test_codex_app_server_tool_runner_starts_new_thread_without_session_id(
        self,
    ) -> None:
        payload = get_pear_session_payload()
        first_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the first topic", False)],
        )
        second_session = build_pear_active_session_summary_from_messages(
            pear_session={
                **payload,
                "stream": "engineering",
                "topic": "second topic",
            },
            source="server",
            stream_exists=True,
            messages=[(20, "@Pear fix the second topic", False)],
        )
        calls = []
        processes = []

        class FakeStdin:
            def __init__(self) -> None:
                self.lines: list[dict[str, object]] = []

            def write(self, value: str) -> int:
                self.lines.append(json.loads(value))
                return len(value)

            def flush(self) -> None:
                pass

        class FakeStream:
            def __init__(self, lines: list[str]) -> None:
                self.lines = lines

            def __iter__(self):
                return iter(self.lines)

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()
                self.stdout = FakeStream(
                    [
                        '{"id":1,"result":{}}\n',
                        '{"id":2,"result":{"thread":{"id":"thread-first"}}}\n',
                        '{"id":3,"result":{"turn":{"id":"turn-first"}}}\n',
                        (
                            '{"method":"item/completed","params":{"threadId":"thread-first",'
                            '"turnId":"turn-first","item":{"id":"msg-first",'
                            '"type":"agentMessage","text":"Verification: first ok"}}}\n'
                        ),
                        (
                            '{"method":"turn/completed","params":{"threadId":"thread-first",'
                            '"turn":{"id":"turn-first"}}}\n'
                        ),
                        '{"id":4,"result":{"thread":{"id":"thread-second"}}}\n',
                        '{"id":5,"result":{"turn":{"id":"turn-second"}}}\n',
                        (
                            '{"method":"item/completed","params":{"threadId":"thread-second",'
                            '"turnId":"turn-second","item":{"id":"msg-second",'
                            '"type":"agentMessage","text":"Verification: second ok"}}}\n'
                        ),
                        (
                            '{"method":"turn/completed","params":{"threadId":"thread-second",'
                            '"turn":{"id":"turn-second"}}}\n'
                        ),
                    ]
                )
                self.stderr = FakeStream([])

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                pass

        def fake_popen(command, **kwargs):
            calls.append((command, kwargs))
            process = FakeProcess()
            processes.append(process)
            return process

        runner = PearCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="danger-full-access",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            popen_command=fake_popen,
        )

        first_results = list(runner(first_session, "fix the first topic"))
        first_thread_id = runner.codex_thread_id()
        second_results = list(runner(second_session, "fix the second topic"))

        self.assertEqual(len(calls), 1)
        sent_methods = [line["method"] for line in processes[0].stdin.lines]
        self.assertEqual(
            sent_methods,
            [
                "initialize",
                "initialized",
                "thread/start",
                "turn/start",
                "thread/start",
                "turn/start",
            ],
        )
        self.assertNotEqual(first_results, [])
        self.assertNotEqual(second_results, [])
        self.assertEqual(first_thread_id, "thread-first")
        self.assertEqual(runner.codex_thread_id(), "thread-second")

    def test_disabled_agent_runner_reports_configuration_gap(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )

        results = list(PearDisabledAgentRunner("disabled by admin")(active_session, "fix"))

        self.assertEqual(
            results,
            [
                {"label": "Error", "value": "disabled by admin"},
            ],
        )

    def test_embedded_bot_handler_replies_with_ack(self) -> None:
        sent_replies = self.run_embedded_bot_handler(
            "@**Pear** debug the flaky parser test",
        )

        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertNotIn("Confirm repo, branch, and target runtime", sent_replies[0])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_embedded_bot_handler_runs_active_topic_followup(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (13, "Fold in the failing terminal UI test too.", False),
            ],
            response_policy="ambient",
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []
        classifier_calls: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def addressing_intent_classifier(
            *,
            content: str,
            active_session,
            active_run: bool,
            message,
            bot_handler,
        ) -> PearAmbientDecision:
            classifier_calls.append(f"{content}:{active_run}")
            return {
                "addressee": "pear",
                "action": "steer",
                "presentation": "diff",
            }

        def tool_runner(active_session, task, *, event_callback=None, **kwargs):
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "Active-topic follow-up accepted",
                }
            ]

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[tuple[int, str, str]] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append((message_id, stream, topic))
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        run_store = FakeRunStore()
        message = {
            "id": 204,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "Fold in the failing terminal UI test too.",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
            addressing_intent_classifier=addressing_intent_classifier,
        ).handle_message(message, FakeBotHandler())

        self.assertEqual(
            classifier_calls,
            ["Fold in the failing terminal UI test too.:False"],
        )
        self.assertEqual(run_store.starts, [(204, PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC)])
        self.assertEqual(seen_tasks, ["Fold in the failing terminal UI test too."])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertNotIn("Confirm repo, branch, and target runtime", sent_replies[0])
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_embedded_bot_handler_silences_untagged_followup_without_router(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
            ],
            response_policy="ambient",
        )
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        class FakeRunStore:
            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                raise AssertionError("untagged text must not start without the router")

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
        ).handle_message(
            {
                "id": 204,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "Hi",
            },
            FakeBotHandler(),
        )

        self.assertEqual(sent_replies, [])

    def test_embedded_bot_handler_steers_addressed_message_while_worker_active(
        self,
    ) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
        )
        sent_replies: list[str] = []
        recorded_events: list[tuple[int, str, str, dict[str, object]]] = []
        metadata_updates: list[tuple[int, dict[str, object]]] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[int] = []
                self.active_checks: list[tuple[str, str]] = []

            def active_worker_run_message_id(
                self,
                bot_handler,
                *,
                stream: str,
                topic: str,
            ) -> int:
                self.active_checks.append((stream, topic))
                return 101

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append(message_id)
                raise AssertionError("active steering must not start a new worker")

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                raise AssertionError("active steering must not record a new worker run")

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((message_id, event_type, summary, payload or {}))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                metadata_updates.append((message_id, metadata))

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                raise AssertionError("typed steering must not rewrite the live message")

        run_store = FakeRunStore()
        message = {
            "id": 205,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "sender_id": 9,
            "content": "@Pear can i interrupt? You can stop",
        }

        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(message, FakeBotHandler())

        self.assertEqual(run_store.starts, [])
        self.assertEqual(
            run_store.active_checks,
            [(PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC)],
        )
        self.assertEqual(sent_replies, [])
        self.assertEqual(recorded_events[0][0], 101)
        self.assertEqual(recorded_events[0][1], "steering")
        self.assertEqual(recorded_events[0][2], "can i interrupt? You can stop")
        self.assertEqual(
            recorded_events[0][3],
            {
                "message_id": 205,
                "instruction": "can i interrupt? You can stop",
                "stream": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
            },
        )
        self.assertEqual(metadata_updates, [])

    def test_embedded_bot_handler_records_steering_without_reply(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, PEAR_WORKING_MESSAGE, True),
            ],
        )
        sent_replies: list[str] = []
        recorded_events: list[tuple[int, str, str, dict[str, object]]] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        class FakeRunStore:
            def active_worker_run_message_id(
                self,
                bot_handler,
                *,
                stream: str,
                topic: str,
            ) -> int:
                return 101

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((message_id, event_type, summary, payload or {}))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
        ).handle_message(
            {
                "id": 206,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "sender_id": 9,
                "content": "@Pear make it the 30th",
            },
            FakeBotHandler(),
        )

        self.assertEqual(sent_replies, [])
        self.assertEqual(recorded_events[0][0], 101)
        self.assertEqual(recorded_events[0][1], "steering")
        self.assertEqual(recorded_events[0][2], "make it the 30th")

    def test_embedded_bot_handler_routes_standing_request_to_worker(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task, *, event_callback=None, **kwargs):
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "Standing request routed to Codex",
                },
            ]

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[tuple[int, str, str]] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append((message_id, stream, topic))
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        run_store = FakeRunStore()
        content = "@Pear every weekday at 9am, check the linked PRs and post a one-line status"
        message = {
            "id": 204,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": content,
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(message, FakeBotHandler())

        self.assertEqual(run_store.starts, [(204, PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC)])
        self.assertEqual(
            seen_tasks,
            [
                "every weekday at 9am, check the linked PRs and post a one-line status",
            ],
        )
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_embedded_bot_handler_honors_mentions_only_followup_policy(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
            response_policy="mentions_only",
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task, *, event_callback=None, **kwargs):
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "Active-topic follow-up accepted",
                }
            ]

        def addressing_intent_classifier(**kwargs: object) -> PearAmbientDecision:
            raise AssertionError("quiet topics must not spend a Gemini routing call")

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[int] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append(message_id)
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        run_store = FakeRunStore()
        message = {
            "id": 204,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "Fold in the failing terminal UI test too.",
        }

        handler = PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
            addressing_intent_classifier=addressing_intent_classifier,
        )
        handler.handle_message(message, FakeBotHandler())

        self.assertEqual(run_store.starts, [])
        self.assertEqual(seen_tasks, [])
        self.assertEqual(sent_replies, [])

        message["id"] = 205
        message["content"] = "@Pear continue with the terminal UI test"
        handler.handle_message(message, FakeBotHandler())

        self.assertEqual(run_store.starts, [205])
        self.assertEqual(seen_tasks, ["continue with the terminal UI test"])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_embedded_bot_handler_treats_stripped_work_mention_as_request(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
            response_policy="mentions_only",
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task, *, event_callback=None, **kwargs):
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "Explicit mention accepted",
                }
            ]

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[int] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append(message_id)
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        run_store = FakeRunStore()
        trigger_message_id = 9206
        message = {
            "id": trigger_message_id,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "debug the flaky parser test",
            PEAR_EXPLICIT_MENTION_MESSAGE_KEY: True,
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(message, FakeBotHandler())

        self.assertEqual(run_store.starts, [trigger_message_id])
        self.assertEqual(seen_tasks, ["debug the flaky parser test"])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertNotIn("steering update", sent_replies[0])
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_embedded_bot_handler_starts_worker_for_explicit_generic_chat(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task, *, event_callback=None, **kwargs):
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "Explicit chat accepted",
                }
            ]

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[int] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append(message_id)
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        run_store = FakeRunStore()
        message = {
            "id": 207,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "hey tell me something",
            PEAR_EXPLICIT_MENTION_MESSAGE_KEY: True,
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(message, FakeBotHandler())

        self.assertEqual(run_store.starts, [207])
        self.assertEqual(seen_tasks, ["hey tell me something"])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)
        self.assertNotIn("Confirm repo, branch, and target runtime", sent_replies[0])

    def test_embedded_bot_handler_starts_worker_for_router_addressed_message(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
            ],
            response_policy="ambient",
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []
        classifier_calls: list[tuple[str, str]] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task, *, event_callback=None, **kwargs):
            seen_tasks.append(task)
            return [{"label": "Verification", "value": "Router accepted"}]

        def addressing_intent_classifier(
            *,
            content: str,
            active_session,
            active_run: bool,
            message,
            bot_handler,
        ) -> PearAmbientDecision:
            classifier_calls.append((content, active_session["response_policy"]))
            return {
                "addressee": "pear",
                "action": "run",
                "presentation": "diff",
            }

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[int] = []
                self.metadata: list[tuple[int, dict[str, object]]] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append(message_id)
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def update_metadata(self, bot_handler, message_id: int, **metadata: object) -> None:
                self.metadata.append((message_id, metadata))

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        run_store = FakeRunStore()
        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
            addressing_intent_classifier=addressing_intent_classifier,
        ).handle_message(
            {
                "id": 208,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "hey agent, can you check the failing parser test?",
                PEAR_TRIGGER_MESSAGE_KEY: PEAR_FOLLOWUP_TRIGGER,
            },
            FakeBotHandler(),
        )

        self.assertEqual(
            classifier_calls,
            [("hey agent, can you check the failing parser test?", "ambient")],
        )
        self.assertEqual(run_store.starts, [208])
        self.assertIn((208, {"ambient_triggered": True}), run_store.metadata)
        self.assertEqual(seen_tasks, ["hey agent, can you check the failing parser test?"])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(sent_replies[1], PEAR_NO_VISIBLE_RESPONSE_MESSAGE)

    def test_embedded_bot_handler_silences_router_rejected_followup(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
            ],
            latest_codex_thread_id="thread-existing",
            response_policy="ambient",
        )
        sent_replies: list[str] = []
        warm_calls: list[tuple[int, str, str]] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def addressing_intent_classifier(
            *,
            content: str,
            active_session,
            active_run: bool,
            message,
            bot_handler,
        ) -> PearAmbientDecision:
            return {
                "addressee": "general",
                "action": "ignore",
                "presentation": "compact",
            }

        class FakeRunStore:
            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                raise AssertionError("rejected router follow-up must not start")

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        class FakeDispatcher:
            def warm(
                self,
                bot_handler,
                message: dict[str, object],
                *,
                stream: str,
                topic: str,
            ) -> None:
                warm_calls.append((int(message["id"]), stream, topic))

        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
            worker_dispatcher=FakeDispatcher(),
            addressing_intent_classifier=addressing_intent_classifier,
        ).handle_message(
            {
                "id": 209,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "I pushed the branch after lunch.",
            },
            FakeBotHandler(),
        )

        self.assertEqual(sent_replies, [])
        self.assertEqual(warm_calls, [(209, PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC)])

    def test_embedded_bot_handler_reports_router_failure_once_per_cooldown(self) -> None:
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
            response_policy="ambient",
        )
        sent_replies: list[str] = []

        def addressing_intent_classifier(**kwargs: object) -> PearAmbientDecision:
            raise RuntimeError("Gemini unavailable")

        class FakeBotHandler:
            user_profile = SimpleNamespace(id=99)

            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        handler = PearEmbeddedBotHandler(
            active_session_loader=lambda message, bot_handler: loaded_session,
            addressing_intent_classifier=addressing_intent_classifier,
        )
        message = {
            "id": 210,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "Can you check the build?",
        }

        with self.assertLogs("zerver.lib.pear", level="WARNING"):
            handler.handle_message(message, FakeBotHandler())
            handler.handle_message(message, FakeBotHandler())

        self.assertEqual(len(sent_replies), 1)
        self.assertIn("@Pear", sent_replies[0])

    def test_embedded_bot_worker_dispatches_pear_to_dedicated_queue(self) -> None:
        user_profile = SimpleNamespace(id=99)
        service = SimpleNamespace(name="pear")
        event = {
            "user_profile_id": 99,
            "message": {
                "id": 210,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "Can you check the build?",
            },
            "trigger": PEAR_FOLLOWUP_TRIGGER,
        }
        worker = object.__new__(EmbeddedBotWorker)

        with (
            patch("zerver.worker.embedded_bots.get_user_profile_by_id", return_value=user_profile),
            patch("zerver.worker.embedded_bots.get_bot_services", return_value=[service]),
            patch("zerver.worker.embedded_bots.get_bot_handler") as get_bot_handler,
            patch("zerver.worker.embedded_bots.queue_json_publish_rollback_unsafe") as publish,
            patch("zerver.worker.embedded_bots.do_flag_service_bots_messages_as_processed"),
        ):
            worker.consume(event)

        get_bot_handler.assert_not_called()
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0], PEAR_ADDRESSING_QUEUE_NAME)
        self.assertEqual(publish.call_args.args[1]["message"]["id"], 210)

    def test_addressing_worker_delegates_to_pear_handler(self) -> None:
        user_profile = SimpleNamespace(id=99)
        message = {"id": 210, "content": "Can you check the build?"}
        worker = object.__new__(PearAddressingWorker)
        worker.pear_handler = SimpleNamespace(handle_message=Mock())

        with (
            patch(
                "zerver.worker.pear_addressing.get_user_profile_by_id",
                return_value=user_profile,
            ),
            patch("zerver.worker.pear_addressing.EmbeddedBotHandler") as bot_handler_class,
        ):
            worker.consume({"user_profile_id": 99, "message": message})

        worker.pear_handler.handle_message.assert_called_once_with(
            message,
            bot_handler_class.return_value,
        )

    def test_embedded_bot_handler_routes_untagged_router_message_as_steering(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
        )
        sent_replies: list[str] = []
        recorded_events: list[tuple[int, str, str]] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def addressing_intent_classifier(
            *,
            content: str,
            active_session,
            active_run: bool,
            message,
            bot_handler,
        ) -> PearAmbientDecision:
            return {
                "addressee": "pear",
                "action": "steer",
                "presentation": "status",
            }

        class FakeRunStore:
            def active_worker_run_message_id(
                self,
                bot_handler,
                *,
                stream: str,
                topic: str,
            ) -> int:
                return 101

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                raise AssertionError("active steering must not start a new worker")

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((message_id, event_type, summary))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 400 + len(sent_replies)}

        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
            addressing_intent_classifier=addressing_intent_classifier,
        ).handle_message(
            {
                "id": 210,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "hey agent, you can stop now",
                PEAR_TRIGGER_MESSAGE_KEY: PEAR_FOLLOWUP_TRIGGER,
            },
            FakeBotHandler(),
        )

        self.assertEqual(sent_replies, [])
        self.assertEqual(recorded_events, [(101, "steering", "hey agent, you can stop now")])

    def test_embedded_bot_worker_uses_loaded_topic_history(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
            is_paused=True,
        )
        loaded_messages: list[dict[str, object]] = []

        def active_session_loader(message, bot_handler):
            loaded_messages.append(message)
            return loaded_session

        sent_replies: list[str] = []

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        message = {
            "id": 13,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear resume after deploy",
        }

        PearEmbeddedBotHandler(active_session_loader=active_session_loader).handle_message(
            message,
            FakeBotHandler(),
        )

        self.assertEqual(loaded_messages, [message, message])
        self.assertEqual(len(sent_replies), 2)
        self.assertIn("Pear worker is not starting.", sent_replies[1])
        self.assertIn("paused", sent_replies[1])

    def test_worker_started_message_storage(self) -> None:
        class FakeStorage:
            def __init__(self) -> None:
                self.values: dict[str, object] = {}

            def contains(self, key: str) -> bool:
                return key in self.values

            def get(self, key: str) -> object:
                return self.values[key]

            def put(self, key: str, value: object) -> None:
                self.values[key] = value

        class FakeBotHandler:
            def __init__(self) -> None:
                self.storage = FakeStorage()

        bot_handler = FakeBotHandler()
        record_pear_worker_run(
            bot_handler,
            101,
            "queued",
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
        )
        record_pear_worker_run(
            bot_handler,
            101,
            "done",
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            result_message_id=301,
        )
        record_pear_worker_run(
            bot_handler,
            102,
            "failed",
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            error="tool failed",
        )

        self.assertTrue(pear_worker_already_started(bot_handler, 101))
        self.assertFalse(pear_worker_already_started(bot_handler, 102))
        self.assertFalse(pear_worker_already_started(bot_handler, 103))
        self.assertEqual(pear_worker_run_for_message(bot_handler, 101)["status"], "done")
        self.assertEqual(pear_worker_run_for_message(bot_handler, 101)["result_message_id"], 301)
        self.assertEqual(pear_worker_run_for_message(bot_handler, 102)["status"], "failed")
        self.assertEqual(
            bot_handler.storage.values[PEAR_WORKER_RUNS_STORAGE_KEY],
            [
                {
                    "trigger_message_id": 101,
                    "status": "done",
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "result_message_id": 301,
                    "error": None,
                },
                {
                    "trigger_message_id": 102,
                    "status": "failed",
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "result_message_id": None,
                    "error": "tool failed",
                },
            ],
        )
        self.assertEqual(len(load_pear_worker_run_records(bot_handler)), 2)

    def test_embedded_bot_suppresses_duplicate_worker_post(self) -> None:
        sent_replies: list[str] = []
        updated_replies: list[str] = []

        class FakeStorage:
            def __init__(self) -> None:
                self.values: dict[str, object] = {}

            def contains(self, key: str) -> bool:
                return key in self.values

            def get(self, key: str) -> object:
                return self.values[key]

            def put(self, key: str, value: object) -> None:
                self.values[key] = value

        class FakeBotHandler:
            def __init__(self) -> None:
                self.storage = FakeStorage()

            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 300 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                content = message.get("content")
                assert isinstance(content, str)
                updated_replies.append(content)

        bot_handler = FakeBotHandler()
        message = {
            "id": 201,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        handler = PearEmbeddedBotHandler()
        handler.handle_message(message, bot_handler)
        handler.handle_message(message, bot_handler)

        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(len(updated_replies), 3)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(updated_replies[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(
            updated_replies[1],
            PEAR_NO_VISIBLE_RESPONSE_MESSAGE,
        )
        self.assertEqual(sent_replies[1], PEAR_WORKING_MESSAGE)
        self.assertEqual(
            updated_replies[2],
            "Pear worker is not starting.\n\n"
            "Reason: This request was already handled or is no longer claimable.",
        )
        self.assertEqual(
            bot_handler.storage.values[PEAR_WORKER_RUNS_STORAGE_KEY],
            [
                {
                    "trigger_message_id": 201,
                    "status": "done",
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "result_message_id": 301,
                    "error": None,
                }
            ],
        )

    def test_embedded_bot_uses_injected_worker_run_store(self) -> None:
        sent_replies: list[str] = []
        updated_replies: list[str] = []

        class FakeRunStore:
            def __init__(self) -> None:
                self.starts: list[tuple[int, str, str]] = []
                self.records: list[tuple[int, str, str, str, int | None, str | None]] = []
                self.progress_messages: list[tuple[int, str, str, int | None]] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                self.starts.append((message_id, stream, topic))
                return True

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                self.records.append((message_id, status, stream, topic, result_message_id, error))

            def register_progress_message(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
                progress_message_id: int | None,
            ) -> None:
                self.progress_messages.append((message_id, stream, topic, progress_message_id))

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 300 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                content = message.get("content")
                assert isinstance(content, str)
                updated_replies.append(content)

        run_store = FakeRunStore()
        message = {
            "id": 203,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        PearEmbeddedBotHandler(worker_run_store=run_store).handle_message(
            message,
            FakeBotHandler(),
        )

        self.assertEqual(run_store.starts, [(203, PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC)])
        self.assertEqual(
            run_store.progress_messages,
            [(203, PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC, 301)],
        )
        self.assertEqual(
            run_store.records,
            [
                (203, "running", PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC, None, None),
                (203, "running", PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC, 301, None),
                (203, "done", PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC, 301, None),
            ],
        )
        self.assertEqual(len(sent_replies), 1)
        self.assertEqual(len(updated_replies), 2)
        self.assertEqual(updated_replies[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(
            updated_replies[1],
            PEAR_NO_VISIBLE_RESPONSE_MESSAGE,
        )

    def test_embedded_bot_settles_ack_when_worker_start_claim_fails(self) -> None:
        sent_replies: list[str] = []
        updated_replies: list[dict[str, object]] = []

        class FakeRunStore:
            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                return False

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 311}

            def update_message(self, message: dict[str, object]) -> None:
                updated_replies.append(message)

        class FailingDispatcher:
            def dispatch(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("worker should not be dispatched after a failed start claim")

        message = {
            "id": 204,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        PearEmbeddedBotHandler(
            worker_run_store=FakeRunStore(),
            worker_dispatcher=FailingDispatcher(),
        ).handle_message(
            message,
            FakeBotHandler(),
        )

        self.assertEqual(sent_replies, [PEAR_WORKING_MESSAGE])
        self.assertEqual(
            updated_replies,
            [
                {
                    "message_id": 311,
                    "content": (
                        "Pear worker is not starting.\n\n"
                        "Reason: This request was already handled or is no longer claimable."
                    ),
                    "rendering_only": True,
                }
            ],
        )

    def test_embedded_bot_rejects_direct_message_without_dispatch(self) -> None:
        sent_replies: list[str] = []

        class ChannelOnlyRunStore:
            def start_blocked_reason(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
                requesting_user_id: int | None,
            ) -> str:
                self.stream = stream
                self.topic = topic
                return "Pear Codex runs currently require a channel topic."

            def start(self, *args: object, **kwargs: object) -> bool:
                raise AssertionError("a blocked direct message must not start a run")

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        class FailingDispatcher:
            def dispatch(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("a blocked direct message must not be dispatched")

        run_store = ChannelOnlyRunStore()
        PearEmbeddedBotHandler(
            worker_run_store=run_store,
            worker_dispatcher=FailingDispatcher(),
        ).handle_message(
            {
                "id": 204,
                "type": "private",
                "display_recipient": [],
                "sender_id": 7,
                "content": "@Pear fix the checkout test",
            },
            FakeBotHandler(),
        )

        self.assertEqual(run_store.stream, "")
        self.assertEqual(run_store.topic, "")
        self.assertEqual(len(sent_replies), 1)
        self.assertIn("require a channel topic", sent_replies[0])

    def test_queue_worker_dispatcher_enqueues_pear_worker_event(self) -> None:
        message = {
            "id": 205,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "fix the checkout test",
        }
        bot_handler = SimpleNamespace(user_profile=SimpleNamespace(id=17))

        with patch("zerver.lib.pear_worker.queue_json_publish_rollback_unsafe") as publish:
            PearQueueWorkerDispatcher().dispatch(
                SimpleNamespace(),
                bot_handler,
                message,
                content="fix the checkout test",
                active_session=None,
                message_id=205,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                progress_message_id=610,
            )

        publish.assert_called_once()
        queue_name, event = publish.call_args.args
        self.assertEqual(
            queue_name,
            pear_worker_queue_name(
                17,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            ),
        )
        self.assertEqual(event["user_profile_id"], 17)
        self.assertEqual(event["message_id"], 205)
        self.assertEqual(event["content"], "fix the checkout test")
        self.assertEqual(event["message"], {**message, "content": "fix the checkout test"})
        self.assertEqual(message["content"], "fix the checkout test")
        self.assertEqual(event["progress_message_id"], 610)

    def test_queue_worker_dispatcher_rejects_streamless_shared_run(self) -> None:
        message = {
            "id": 205,
            "type": "private",
            "display_recipient": [],
            "content": "@Pear fix the checkout test",
        }
        bot_handler = SimpleNamespace(user_profile=SimpleNamespace(id=17))

        with (
            patch("zerver.lib.pear_worker.queue_json_publish_rollback_unsafe") as publish,
            self.assertRaisesRegex(AssertionError, "requires a channel topic"),
        ):
            PearQueueWorkerDispatcher().dispatch(
                SimpleNamespace(),
                bot_handler,
                message,
                content="fix the checkout test",
                active_session=None,
                message_id=205,
                stream="",
                topic="",
                progress_message_id=610,
            )

        publish.assert_not_called()

    def test_queue_worker_dispatcher_preserves_normalized_pear_request(
        self,
    ) -> None:
        message = {
            "id": 205,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": "",
            "content": "what's up, bot?",
        }
        bot_handler = SimpleNamespace(user_profile=SimpleNamespace(id=17))

        with patch("zerver.lib.pear_worker.queue_json_publish_rollback_unsafe") as publish:
            PearQueueWorkerDispatcher().dispatch(
                SimpleNamespace(),
                bot_handler,
                message,
                content="@Pear what's up, bot?",
                active_session=None,
                message_id=205,
                stream=PEAR_DEFAULT_STREAM,
                topic="",
                progress_message_id=610,
            )

        _queue_name, event = publish.call_args.args
        self.assertEqual(event["content"], "@Pear what's up, bot?")
        self.assertEqual(event["message"]["content"], "@Pear what's up, bot?")
        self.assertEqual(message["content"], "what's up, bot?")

    def test_queue_worker_dispatcher_enqueues_background_warm(self) -> None:
        message = {
            "id": 206,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "Thanks, that makes sense.",
        }
        bot_handler = SimpleNamespace(user_profile=SimpleNamespace(id=17))

        with patch("zerver.lib.pear_worker.queue_json_publish_rollback_unsafe") as publish:
            PearQueueWorkerDispatcher().warm(
                bot_handler,
                message,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            )

        publish.assert_called_once_with(
            pear_worker_queue_name(
                17,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
            ),
            {
                "warm_only": True,
                "user_profile_id": 17,
                "message_id": 206,
                "stream": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "Thanks, that makes sense.",
                "message": message,
            },
        )

    def test_worker_queue_affinity_is_stable_and_bounded(self) -> None:
        first = pear_worker_queue_name(17, stream="devel", topic="one")
        self.assertEqual(first, pear_worker_queue_name(17, stream="devel", topic="one"))
        self.assertIn(first, PEAR_WORKER_QUEUE_NAMES)

    def test_pear_worker_uses_priority_affinity_and_one_global_inflight_event(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.worker_slot = 1
        worker.q = Mock(spec=SimpleQueueClient)

        with patch.object(worker, "initialize_statistics"):
            worker.start()

        worker.q.start_json_consumers.assert_called_once()
        priorities, callback = worker.q.start_json_consumers.call_args.args
        self.assertEqual(
            priorities, {PEAR_WORKER_QUEUE_NAMES[1]: 10, PEAR_WORKER_QUEUE_NAMES[0]: 0}
        )
        self.assertTrue(worker.q.start_json_consumers.call_args.kwargs["annotate_redelivery"])
        self.assertTrue(callable(callback))

    def test_pear_worker_statistics_are_slot_specific(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.queue_name = "pear_worker"
        worker.worker_num = 1

        self.assertEqual(worker.statistics_file_name(), "pear_worker-1.stats")

    def test_pear_worker_discards_forged_streamless_shared_event(self) -> None:
        worker = object.__new__(pear_worker_module.PearWorker)
        worker.worker_slot = 0
        event = {
            "user_profile_id": 17,
            "message_id": 205,
            "content": "@Pear run this privately",
            "message": {
                "id": 205,
                "type": "private",
                "display_recipient": [],
                "content": "@Pear run this privately",
            },
        }

        with (
            patch.object(
                pear_worker_module,
                "get_user_profile_by_id",
                return_value=SimpleNamespace(id=17),
            ),
            patch.object(pear_worker_module, "EmbeddedBotHandler"),
            self.assertRaisesRegex(QueueEventDiscardError, "no channel context"),
        ):
            worker._consume(event)

    def test_only_the_home_worker_warms_a_topic(self) -> None:
        home_slot = pear_worker_module.pear_worker_home_slot(
            17,
            stream="devel",
            topic="warm",
        )
        self.assertTrue(
            pear_worker_module.pear_worker_slot_should_warm(
                home_slot,
                17,
                stream="devel",
                topic="warm",
            )
        )
        self.assertFalse(
            pear_worker_module.pear_worker_slot_should_warm(
                1 - home_slot,
                17,
                stream="devel",
                topic="warm",
            )
        )

    def test_monitor_worker_keeps_its_dedicated_queue(self) -> None:
        from zerver.worker.pear_monitor_worker import PearMonitorWorker

        worker = object.__new__(PearMonitorWorker)
        worker.q = Mock(spec=SimpleQueueClient)

        with patch.object(pear_worker_module.QueueProcessingWorker, "start") as parent_start:
            worker.start()

        parent_start.assert_called_once_with()
        worker.q.start_json_consumers.assert_not_called()

    def test_run_worker_turn_posts_started_progress_before_tools(self) -> None:
        sent_replies: list[str] = []
        updated_messages: list[dict[str, object]] = []
        visible_status_by_id = {501: "Status: queued"}
        tool_seen_statuses: list[str] = []

        class FakeRunStore:
            def __init__(self) -> None:
                self.records: list[tuple[str, int | None, str | None]] = []

            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                raise AssertionError("run_worker_turn should not claim runs")

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                self.records.append((status, result_message_id, error))

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 700 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                message_id = message.get("message_id")
                content = message.get("content")
                assert isinstance(message_id, int)
                assert isinstance(content, str)
                visible_status_by_id[message_id] = content
                updated_messages.append(message)

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            tool_seen_statuses.append(visible_status_by_id[501])
            return [
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                }
            ]

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        run_store = FakeRunStore()
        message = {
            "id": 205,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=run_store,
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear fix the checkout test",
            active_session=active_session,
            message_id=205,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=501,
        )

        self.assertEqual(sent_replies, [])
        self.assertEqual(len(updated_messages), 2)
        self.assertEqual(updated_messages[0]["message_id"], 501)
        self.assertIs(updated_messages[0]["rendering_only"], True)
        self.assertEqual(updated_messages[0]["content"], PEAR_WORKING_MESSAGE)
        self.assertEqual(tool_seen_statuses, [updated_messages[0]["content"]])
        self.assertEqual(updated_messages[1]["message_id"], 501)
        self.assertIs(updated_messages[1]["rendering_only"], True)
        self.assertEqual(
            updated_messages[1]["content"],
            PEAR_NO_VISIBLE_RESPONSE_MESSAGE,
        )
        self.assertNotIn("Status: working...", updated_messages[1]["content"])
        self.assertEqual(
            run_store.records,
            [
                ("running", None, None),
                ("running", 501, None),
                ("done", 501, None),
            ],
        )

    def test_run_worker_turn_emits_topic_typing_signal(self) -> None:
        typing_signals: list[str] = []

        class FakeRunStore:
            def record(self, bot_handler, message_id, status, **kwargs) -> None:
                pass

            def record_event(self, bot_handler, message_id, event_type, **kwargs) -> None:
                pass

            def update_metadata(self, bot_handler, message_id, **metadata) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message, response) -> dict[str, int]:
                return {"id": 4242}

            def update_message(self, message) -> None:
                pass

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            return [{"label": "Verification", "value": "typing test"}]

        message = {
            "id": 9311,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "subject": PEAR_DEFAULT_TOPIC,
            "sender_id": 21,
            "sender_full_name": "plugyawn",
            "content": "@**Pear** run the typing check",
        }
        handler = PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=FakeRunStore(),
        )

        with patch(
            "zerver.lib.pear.send_pear_worker_typing_notification",
            side_effect=lambda bot_handler, *, stream, topic, operator: (
                typing_signals.append(operator) or True
            ),
        ):
            handler.run_worker_turn(
                message,
                FakeBotHandler(),
                content=str(message["content"]),
                active_session=None,
                message_id=9311,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                post_started_progress=True,
            )

        # The run brackets the topic-level "Pear is working" dots: one
        # start when the runtime begins, one stop when the turn settles.
        self.assertEqual(typing_signals[0], "start")
        self.assertEqual(typing_signals[-1], "stop")
        self.assertEqual(typing_signals.count("stop"), 1)

    def test_run_worker_turn_settles_progress_for_non_posting_result(self) -> None:
        updated_messages: list[dict[str, object]] = []
        recorded_events: list[tuple[str, str]] = []

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((event_type, summary))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(
                self,
                message: dict[str, object],
                response: str,
            ) -> dict[str, int]:
                raise AssertionError("progress message should be edited in place")

            def update_message(self, message: dict[str, object]) -> None:
                if message.get("rendering_only") is not True:
                    raise JsonableError("edit window is closed")
                updated_messages.append(message)

        class NonPostingRuntime(PearWorkerRuntime):
            def run(
                self,
                *,
                active_session: PearActiveSessionSummary,
                task: str = "",
                event_callback: PearWorkerTranscriptCallback | None = None,
            ) -> PearWorkerRunResult:
                if event_callback is not None:
                    event_callback(
                        {
                            "kind": "status",
                            "status": "running",
                            "title": "Started runtime.",
                            "body": "",
                        }
                    )
                return {
                    "should_post": False,
                    "worker_can_run": True,
                    "content": None,
                    "codex_thread_id": "",
                    "tool_count": 0,
                    "tool_results": [],
                }

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear run quietly", False)],
        )
        trigger_message_id = 9207
        message = {
            "id": trigger_message_id,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear run quietly",
        }

        PearEmbeddedBotHandler(
            worker_runtime=NonPostingRuntime(),
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear run quietly",
            active_session=active_session,
            message_id=trigger_message_id,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=502,
        )

        self.assertEqual(updated_messages[0]["content"], PEAR_WORKING_MESSAGE)
        self.assertIs(updated_messages[0]["rendering_only"], True)
        self.assertEqual(
            updated_messages[-1]["content"],
            PEAR_NO_VISIBLE_RESPONSE_MESSAGE,
        )
        self.assertIs(updated_messages[-1]["rendering_only"], True)
        self.assertIn(("result_updated", "Settled worker progress message."), recorded_events)
        live_state = get_pear_live_run_state(trigger_message_id)
        self.assertIsNotNone(live_state)
        assert live_state is not None
        self.assertEqual(live_state["status"], "done")
        self.assertNotIn("content", live_state)

    def test_run_worker_turn_chunks_non_posting_transcript_result(self) -> None:
        sent_replies: list[str] = []
        updated_messages: list[dict[str, object]] = []
        long_answer = "\n\n".join(
            f"Section {index}: {'quiet transcript text ' * 80}" for index in range(12)
        )

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                pass

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 900 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        class QuietTranscriptRuntime(PearWorkerRuntime):
            def run(
                self,
                *,
                active_session: PearActiveSessionSummary,
                task: str = "",
                event_callback: PearWorkerTranscriptCallback | None = None,
            ) -> PearWorkerRunResult:
                if event_callback is not None:
                    event_callback(
                        {
                            "kind": "assistant",
                            "status": "done",
                            "title": "Codex",
                            "body": long_answer,
                            "item_id": "answer",
                        }
                    )
                return {
                    "should_post": False,
                    "worker_can_run": True,
                    "content": None,
                    "codex_thread_id": "",
                    "tool_count": 0,
                    "tool_results": [],
                }

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear run quietly", False)],
        )
        trigger_message_id = 9208
        message = {
            "id": trigger_message_id,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear run quietly",
        }

        PearEmbeddedBotHandler(
            worker_runtime=QuietTranscriptRuntime(),
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear run quietly",
            active_session=active_session,
            message_id=trigger_message_id,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=503,
        )

        self.assertGreater(len(sent_replies), 0)
        all_persisted_text = "\n\n".join([str(updated_messages[-1]["content"]), *sent_replies])
        self.assertIn("Section 0:", all_persisted_text)
        self.assertIn("Section 11:", all_persisted_text)
        self.assertEqual(all_persisted_text.count("Section 0:"), 1)
        self.assertEqual(all_persisted_text.count("Section 11:"), 1)
        self.assertNotIn("[truncated]", all_persisted_text)
        self.assertLessEqual(len(str(updated_messages[-1]["content"])), 5000)
        self.assertTrue(all(len(reply) <= 5000 for reply in sent_replies))

    def test_run_worker_turn_posts_continuation_messages_for_long_result(self) -> None:
        sent_replies: list[str] = []
        updated_messages: list[dict[str, object]] = []
        long_result = "\n\n".join(f"Section {index}: {'result text ' * 90}" for index in range(12))

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 800 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            return [{"label": "Result", "value": long_result}]

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear summarize the links", False)],
        )
        message = {
            "id": 206,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear summarize the links",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear summarize the links",
            active_session=active_session,
            message_id=206,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=503,
        )

        self.assertGreater(len(sent_replies), 0)
        self.assertEqual(updated_messages[-1]["message_id"], 503)
        all_persisted_text = "\n\n".join([str(updated_messages[-1]["content"]), *sent_replies])
        self.assertIn("Section 0:", all_persisted_text)
        self.assertIn("Section 11:", all_persisted_text)
        self.assertNotIn("[truncated]", all_persisted_text)
        self.assertTrue(all(len(reply) <= 5000 for reply in sent_replies))

    def test_run_worker_turn_keeps_streaming_deltas_transient_and_skips_noop_edits(self) -> None:
        updated_messages: list[dict[str, object]] = []
        recorded_events: list[tuple[str, str]] = []

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((event_type, summary))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(
                self,
                message: dict[str, object],
                response: str,
            ) -> dict[str, int]:
                raise AssertionError("progress message should be edited in place")

            def update_message(self, message: dict[str, object]) -> None:
                if message.get("rendering_only") is not True:
                    raise JsonableError("edit window is closed")
                updated_messages.append(message)

        def tool_runner(
            active_session,
            task: str,
            *,
            event_callback: PearWorkerTranscriptCallback | None = None,
        ) -> list[dict[str, str]]:
            assert event_callback is not None
            event_callback(
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": " ",
                    "item_id": "assistant-1",
                    "metadata": {"streaming": True},
                }
            )
            event_callback(
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": "The PDF",
                    "item_id": "assistant-1",
                    "metadata": {"streaming": True},
                }
            )
            event_callback(
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": " compiled",
                    "item_id": "assistant-1",
                    "metadata": {"streaming": True},
                }
            )
            return [{"label": "Result", "value": "The PDF compiled"}]

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear write two letters", False)],
        )
        trigger_message_id = 9206
        message = {
            "id": trigger_message_id,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear write two letters",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear write two letters",
            active_session=active_session,
            message_id=trigger_message_id,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=502,
        )

        self.assertEqual(
            [message["content"] for message in updated_messages],
            [
                PEAR_WORKING_MESSAGE,
                "The PDF",
                "The PDF compiled",
            ],
        )
        self.assertEqual(
            [message.get("rendering_only") for message in updated_messages],
            [True, True, True],
        )
        self.assertFalse(
            any(
                event_type == "transcript" and summary == "Codex"
                for event_type, summary in recorded_events
            )
        )
        self.assertEqual(
            [event_type for event_type, _summary in recorded_events].count("assistant_visible"),
            1,
        )
        live_state = get_pear_live_run_state(trigger_message_id)
        self.assertIsNotNone(live_state)
        assert live_state is not None
        self.assertEqual(live_state["status"], "done")
        self.assertNotIn("content", live_state)

    def test_run_worker_turn_uses_typed_state_instead_of_content_markers(self) -> None:
        updated_messages: list[dict[str, object]] = []

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                pass

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(
                self,
                message: dict[str, object],
                response: str,
            ) -> dict[str, int]:
                raise AssertionError("progress message should be edited in place")

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        def tool_runner(
            active_session,
            task: str,
            *,
            event_callback: PearWorkerTranscriptCallback | None = None,
        ) -> list[dict[str, str]]:
            assert event_callback is not None
            event_callback(
                {
                    "kind": "tool",
                    "status": "running",
                    "title": "Running command",
                    "body": "python script.py",
                }
            )
            return [{"label": "Result", "value": "done"}]

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear run a script", False)],
        )
        message = {
            "id": 9207,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear run a script",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear run a script",
            active_session=active_session,
            message_id=9207,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=502,
        )

        updated_contents = [str(message["content"]) for message in updated_messages]
        self.assertTrue(updated_contents)
        self.assertEqual(updated_contents[0], PEAR_WORKING_MESSAGE)
        self.assertEqual(updated_contents[-1], "done")
        self.assertTrue(all("✦" not in content for content in updated_contents))

    def test_run_worker_turn_drops_final_write_when_completion_lease_is_lost(self) -> None:
        sent_replies: list[str] = []
        updated_messages: list[dict[str, object]] = []
        recorded_statuses: list[str] = []

        class LostCompletion:
            def __enter__(self) -> None:
                raise PearWorkerLeaseLostError("attempt 1 lost its lease")

            def __exit__(self, *args: object) -> bool:
                return False

        class FakeRunStore:
            def record(self, bot_handler, message_id, status, **kwargs) -> None:
                recorded_statuses.append(status)

            def record_event(self, bot_handler, message_id, event_type, **kwargs) -> None:
                pass

            def update_metadata(self, bot_handler, message_id, **metadata) -> None:
                pass

            def worker_completion(self, bot_handler, message_id) -> LostCompletion:
                return LostCompletion()

        class FakeBotHandler:
            def send_reply(self, message, response) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 9001}

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        class CloseableToolRunner:
            def __init__(self) -> None:
                self.closed = False

            def __call__(
                self,
                active_session,
                task: str,
                *,
                event_callback: PearWorkerTranscriptCallback | None = None,
            ) -> list[dict[str, str]]:
                return [{"label": "Result", "value": "must not become visible"}]

            def close(self) -> None:
                self.closed = True

        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear finish the fenced turn", False)],
        )
        message = {
            "id": 9208,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear finish the fenced turn",
        }
        tool_runner = CloseableToolRunner()

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear finish the fenced turn",
            active_session=active_session,
            message_id=9208,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=False,
        )

        self.assertTrue(tool_runner.closed)
        self.assertEqual(sent_replies, [])
        self.assertEqual(updated_messages, [])
        self.assertEqual(recorded_statuses, ["running"])

    def test_run_worker_turn_cancels_runtime_when_cancel_requested(self) -> None:
        updated_messages: list[dict[str, object]] = []
        recorded_statuses: list[tuple[str, int | None, str | None]] = []
        recorded_events: list[tuple[str, str]] = []
        metadata_updates: list[dict[str, object]] = []

        class FakeRunStore:
            def __init__(self) -> None:
                self.cancel_checks = 0

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                recorded_statuses.append((status, result_message_id, error))

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((event_type, summary))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                metadata_updates.append(metadata)

            def cancel_requested(self, bot_handler, message_id: int) -> bool:
                self.cancel_checks += 1
                return self.cancel_checks >= 2

        class FakeBotHandler:
            def send_reply(
                self,
                message: dict[str, object],
                response: str,
            ) -> dict[str, int]:
                raise AssertionError("cancelled run should keep editing the progress message")

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        class InterruptibleToolRunner:
            def __init__(self) -> None:
                self.interrupted = False

            def __call__(
                self,
                active_session,
                task: str,
                *,
                event_callback: PearWorkerTranscriptCallback | None = None,
            ) -> list[dict[str, str]]:
                assert event_callback is not None
                event_callback(
                    {
                        "kind": "tool",
                        "status": "running",
                        "title": "Running command",
                        "body": "python script.py",
                    }
                )
                return [{"label": "Result", "value": "should not complete"}]

            def interrupt(self) -> bool:
                self.interrupted = True
                return True

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear run a script", False)],
        )
        trigger_message_id = 9210
        message = {
            "id": trigger_message_id,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear run a script",
        }
        run_store = FakeRunStore()
        tool_runner = InterruptibleToolRunner()

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            worker_run_store=run_store,
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear run a script",
            active_session=active_session,
            message_id=trigger_message_id,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
            progress_message_id=502,
        )

        self.assertTrue(tool_runner.interrupted)
        self.assertIn(("cancelled", 502, "Cancelled by user."), recorded_statuses)
        self.assertNotIn(("done", 502, None), recorded_statuses)
        self.assertIn(("cancelled", "Cancelled by user."), recorded_events)
        self.assertIn(
            {
                "summary": "Cancelled by user.",
                "current_activity": "Cancelled.",
            },
            metadata_updates,
        )
        self.assertEqual(updated_messages[-1]["message_id"], 502)
        self.assertIs(updated_messages[-1]["rendering_only"], True)
        self.assertIn("Pear worker cancelled.", str(updated_messages[-1]["content"]))
        live_state = get_pear_live_run_state(trigger_message_id)
        self.assertIsNotNone(live_state)
        assert live_state is not None
        self.assertEqual(live_state["status"], "cancelled")

    def test_run_worker_turn_fails_closed_when_active_session_loader_fails(self) -> None:
        sent_replies: list[str] = []
        recorded_statuses: list[tuple[str, int | None, str | None]] = []
        recorded_events: list[tuple[str, str]] = []

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                recorded_statuses.append((status, result_message_id, error))

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((event_type, summary))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 800 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                raise AssertionError("loader failure has no progress message to update")

        def active_session_loader(message, bot_handler) -> NoReturn:
            raise RuntimeError("database unavailable")

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            raise AssertionError("runtime must not run after a loader failure")

        message = {
            "id": 9211,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear run a script",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content="@Pear run a script",
            active_session=None,
            message_id=9211,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=False,
        )

        self.assertEqual(
            recorded_statuses,
            [("failed", None, "Pear active session loader failed.")],
        )
        self.assertEqual(
            recorded_events,
            [("failed", "Pear active session loader failed.")],
        )
        self.assertEqual(len(sent_replies), 1)
        self.assertIn("Pear worker failed.", sent_replies[0])
        self.assertIn("Pear active session loader failed.", sent_replies[0])
        live_state = get_pear_live_run_state(9211)
        self.assertIsNotNone(live_state)
        assert live_state is not None
        self.assertEqual(live_state["status"], "failed")

    def test_run_worker_turn_refreshes_shared_topic_context_before_tools(self) -> None:
        sent_replies: list[str] = []
        updated_messages: list[dict[str, object]] = []
        loaded_message_ids: list[int] = []
        tool_seen_prompts: list[str] = []

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

        class FakeBotHandler:
            def send_reply(
                self,
                message: dict[str, object],
                response: str,
            ) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 700 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        payload = get_pear_session_payload()
        stale_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (
                    157,
                    "only one message? you cant see the previous question i asked about load factors?",
                    False,
                    "berry",
                ),
            ],
        )
        refreshed_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (135, "@Pear set up work in Megatron-LM git", False, "plugyawn"),
                (
                    138,
                    "hello pear. what is the difference between load factor and occupancy?",
                    False,
                    "berry",
                ),
                (140, "Did you hear what berry said?", False, "plugyawn"),
                (
                    157,
                    "only one message? you cant see the previous question i asked about load factors?",
                    False,
                    "berry",
                ),
            ],
        )

        def active_session_loader(message, bot_handler):
            loaded_message_ids.append(message["id"])
            return refreshed_session

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            prompt = build_pear_codex_turn_prompt(active_session, task)
            tool_seen_prompts.append(prompt)
            return [
                {
                    "label": "Verification",
                    "value": "refreshed topic context before running Codex",
                }
            ]

        message = {
            "id": 157,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "only one message? you cant see the previous question i asked about load factors?",
        }

        PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(tool_runner),
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
        ).run_worker_turn(
            message,
            FakeBotHandler(),
            content=str(message["content"]),
            active_session=stale_session,
            message_id=157,
            stream=PEAR_DEFAULT_STREAM,
            topic=PEAR_DEFAULT_TOPIC,
            post_started_progress=True,
        )

        self.assertEqual(loaded_message_ids, [157])
        self.assertEqual(len(tool_seen_prompts), 1)
        self.assertIn("#138 berry (human): hello pear.", tool_seen_prompts[0])
        self.assertIn(
            "#140 plugyawn (human): Did you hear what berry said?",
            tool_seen_prompts[0],
        )
        self.assertIn(
            "Visible recent human participants:\n- plugyawn\n- berry",
            tool_seen_prompts[0],
        )
        self.assertTrue(sent_replies or updated_messages)

    def test_run_worker_turn_drains_queued_steering_as_continuation_segment(self) -> None:
        updated_messages: list[dict[str, object]] = []
        tool_seen_tasks: list[str] = []
        tool_seen_threads: list[str | None] = []
        recorded_events: list[tuple[str, str, dict[str, object]]] = []
        attached_artifacts: list[dict[str, str]] = []

        class FakeRunStore:
            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                pass

            def record_event(
                self,
                bot_handler,
                message_id: int,
                event_type: str,
                *,
                summary: str = "",
                payload: dict[str, object] | None = None,
            ) -> None:
                recorded_events.append((event_type, summary, payload or {}))

            def update_metadata(
                self,
                bot_handler,
                message_id: int,
                **metadata: object,
            ) -> None:
                pass

            def steering_events_after(
                self,
                bot_handler,
                message_id: int,
                *,
                after_event_id: int,
            ) -> list[dict[str, object]]:
                if after_event_id:
                    return []
                return [
                    {
                        "event_id": 17,
                        "message_id": 306,
                        "instruction": "stop and summarize the current result",
                    }
                ]

        class FakeBotHandler:
            def send_reply(
                self,
                message: dict[str, object],
                response: str,
            ) -> dict[str, int]:
                raise AssertionError("progress message should be edited in place")

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        class FakeToolRunner:
            def __call__(
                self, active_session, task: str, *, event_callback=None, **kwargs
            ) -> list[dict[str, str]]:
                tool_seen_tasks.append(task)
                tool_seen_threads.append(active_session["latest_codex_thread_id"])
                if len(tool_seen_tasks) == 1:
                    return [
                        {"label": "Result", "value": "first result"},
                        {
                            "label": "Artifact",
                            "value": '{"blocks":[{"body":"first visual","type":"copy"}],"version":1}',
                        },
                    ]
                return [{"label": "Result", "value": "steered result"}]

            def codex_thread_id(self) -> str:
                return "thread-1"

        def attach_artifact(
            bot_handler: object,
            *,
            message_id: int | None,
            tool_results: list[dict[str, str]],
            workdir: str,
            isolation_username: str | None,
        ) -> bool:
            del bot_handler, message_id
            self.assertEqual(workdir, "")
            self.assertIsNone(isolation_username)
            attached_artifacts.extend(tool_results)
            return True

        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        message = {
            "id": 205,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        with patch("zerver.lib.pear.attach_pear_artifact_to_message", side_effect=attach_artifact):
            PearEmbeddedBotHandler(
                worker_runtime=PearWorkerRuntime(FakeToolRunner()),
                worker_run_store=FakeRunStore(),
            ).run_worker_turn(
                message,
                FakeBotHandler(),
                content="@Pear fix the checkout test",
                active_session=active_session,
                message_id=205,
                stream=PEAR_DEFAULT_STREAM,
                topic=PEAR_DEFAULT_TOPIC,
                post_started_progress=True,
                progress_message_id=501,
            )

        self.assertEqual(
            tool_seen_tasks,
            [
                "fix the checkout test",
                "Steering update:\n- stop and summarize the current result",
            ],
        )
        self.assertEqual(tool_seen_threads, [None, "thread-1"])
        self.assertEqual([result["label"] for result in attached_artifacts], ["Artifact"])
        self.assertEqual(updated_messages[-1]["content"], "steered result")
        self.assertIn(
            (
                "steering_applied",
                "stop and summarize the current result",
                {
                    "segment_index": 1,
                    "steering_events": [
                        {
                            "event_id": 17,
                            "message_id": 306,
                            "instruction": "stop and summarize the current result",
                        }
                    ],
                },
            ),
            recorded_events,
        )
        self.assertEqual(updated_messages[-1]["content"], "steered result")

    def test_embedded_bot_obeys_injected_worker_run_duplicate_claim(self) -> None:
        sent_replies: list[str] = []

        class DuplicateRunStore:
            def start(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
            ) -> bool:
                return False

            def record(
                self,
                bot_handler,
                message_id: int,
                status: str,
                *,
                stream: str,
                topic: str,
                result_message_id: int | None = None,
                error: str | None = None,
            ) -> None:
                raise AssertionError("duplicate runs must not be recorded")

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        message = {
            "id": 204,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        PearEmbeddedBotHandler(worker_run_store=DuplicateRunStore()).handle_message(
            message,
            FakeBotHandler(),
        )

        self.assertEqual(len(sent_replies), 1)
        self.assertEqual(sent_replies[0], PEAR_WORKING_MESSAGE)

    def test_embedded_bot_records_failed_worker_run(self) -> None:
        sent_replies: list[str] = []

        class FakeStorage:
            def __init__(self) -> None:
                self.values: dict[str, object] = {}

            def contains(self, key: str) -> bool:
                return key in self.values

            def get(self, key: str) -> object:
                return self.values[key]

            def put(self, key: str, value: object) -> None:
                self.values[key] = value

        class FakeBotHandler:
            def __init__(self) -> None:
                self.storage = FakeStorage()

            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        def tool_runner(active_session, task: str, *, event_callback=None, **kwargs) -> NoReturn:
            raise RuntimeError("codex adapter failed")

        bot_handler = FakeBotHandler()
        message = {
            "id": 202,
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": "@Pear fix the checkout test",
        }

        PearEmbeddedBotHandler(worker_runtime=PearWorkerRuntime(tool_runner)).handle_message(
            message,
            bot_handler,
        )

        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[1], "Pear worker failed.\n\nError: codex adapter failed")
        self.assertEqual(
            bot_handler.storage.values[PEAR_WORKER_RUNS_STORAGE_KEY],
            [
                {
                    "trigger_message_id": 202,
                    "status": "failed",
                    "stream": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                    "result_message_id": None,
                    "error": "codex adapter failed",
                }
            ],
        )

    def run_embedded_bot_handler(self, content: str) -> list[str]:
        sent_replies: list[tuple[dict[str, object], str]] = []

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append((message, response))

        message = {
            "type": "stream",
            "display_recipient": PEAR_DEFAULT_STREAM,
            "topic": PEAR_DEFAULT_TOPIC,
            "content": content,
        }

        PearEmbeddedBotHandler().handle_message(message, FakeBotHandler())

        self.assertEqual(sent_replies[0][0], message)
        return [response for _message, response in sent_replies]


class PearAppServerProtocolCitizenTest(TestCase):
    """The runner translates or answers everything the engine says —
    nothing silent, nothing raising on engine chatter."""

    def make_translator(self) -> PearCodexAppServerTranscriptTranslator:
        return PearCodexAppServerTranscriptTranslator()

    def test_rate_limit_events_dedupe_unchanged_readings(self) -> None:
        translator = self.make_translator()
        params = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 6, "windowDurationMins": 300},
                "secondary": {"usedPercent": 1},
                "planType": "pro",
            }
        }
        first = translator.events_from_message(
            {"method": "account/rateLimits/updated", "params": params}
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["title"], "Rate limit window at 6% used.")
        repeat = translator.events_from_message(
            {"method": "account/rateLimits/updated", "params": params}
        )
        self.assertEqual(repeat, [])
        changed = translator.events_from_message(
            {
                "method": "account/rateLimits/updated",
                "params": {"rateLimits": {"primary": {"usedPercent": 9}}},
            }
        )
        self.assertEqual(len(changed), 1)

    def test_warnings_surface_and_noise_stays_ignored(self) -> None:
        translator = self.make_translator()
        warned = translator.events_from_message(
            {
                "method": "warning",
                "params": {"message": "recorded with gpt-5.5 but resuming with gpt-5.6-luna"},
            }
        )
        self.assertEqual(len(warned), 1)
        self.assertEqual(warned[0]["title"], "Codex warning.")
        self.assertIn("gpt-5.6-luna", warned[0]["body"])
        for method in ("thread/status/changed", "mcpServer/startupStatus/updated"):
            self.assertEqual(translator.events_from_message({"method": method, "params": {}}), [])

    def test_native_child_thread_identity_and_status_are_preserved(self) -> None:
        translator = self.make_translator()
        [started] = translator.events_from_message(
            {
                "method": "thread/started",
                "params": {
                    "thread": {
                        "id": "child-1",
                        "sessionId": "session-1",
                        "forkedFromId": "fork-1",
                        "parentThreadId": "root-1",
                        "agentNickname": "Scout",
                        "agentRole": "researcher",
                        "source": {
                            "subAgent": {
                                "thread_spawn": {
                                    "parent_thread_id": "root-1",
                                    "depth": 1,
                                    "agent_path": "research/scout",
                                    "agent_nickname": "Scout",
                                    "agent_role": "researcher",
                                }
                            }
                        },
                        "preview": "unused preview",
                        "status": {"type": "active", "activeFlags": ["waiting"]},
                    }
                },
            }
        )
        self.assertEqual(started["metadata"]["codex_thread_id"], "child-1")
        self.assertEqual(started["metadata"]["thread"]["parent_thread_id"], "root-1")
        self.assertEqual(started["metadata"]["thread"]["agent_nickname"], "Scout")
        descriptor = started["metadata"]["thread"]
        for dead_key in (
            "session_id",
            "forked_from_id",
            "source_kind",
            "agent_path",
            "depth",
            "preview",
        ):
            self.assertNotIn(dead_key, descriptor)
        self.assertEqual(descriptor["status"], {"type": "active"})

        [changed] = translator.events_from_message(
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": "child-1",
                    "status": {"type": "idle"},
                },
            }
        )
        self.assertEqual(changed["kind"], "agent")
        self.assertEqual(changed["metadata"]["codex_thread_id"], "child-1")
        self.assertEqual(changed["metadata"]["parent_thread_id"], "root-1")
        self.assertEqual(changed["metadata"]["thread_status"], "idle")

    def test_child_session_sanitizer_keeps_graph_inputs_out_of_browser_contract(
        self,
    ) -> None:
        sanitized = pear_sanitized_child_agent_event(
            {
                "kind": "session",
                "metadata": {
                    "thread": {
                        "id": "child-1",
                        "parent_thread_id": "root-1",
                        "created_at": 42.0,
                        "preview": "unused preview",
                        "status": {"type": "active", "active_flags": ["waiting"]},
                    }
                },
            }
        )

        assert sanitized is not None
        thread = sanitized["metadata"]["thread"]
        self.assertEqual(
            thread,
            {
                "id": "child-1",
                "parent_thread_id": "root-1",
                "created_at": 42.0,
                "status": {"type": "active"},
            },
        )

    def test_native_collaboration_item_retains_bounded_agent_metadata(self) -> None:
        translator = self.make_translator()
        [event] = translator.events_from_message(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "root-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "collab-1",
                        "type": "collabAgentToolCall",
                        "tool": "spawnAgent",
                        "senderThreadId": "root-1",
                        "receiverThreadIds": ["child-1"],
                        "agentsStates": {
                            "child-1": {"status": "running", "message": "Inspecting UI"}
                        },
                        "status": "completed",
                    },
                },
            }
        )
        self.assertEqual(event["kind"], "agent")
        self.assertEqual(event["title"], "Started an agent")
        self.assertEqual(event["metadata"]["codex_thread_id"], "root-1")
        self.assertEqual(event["metadata"]["codex_turn_id"], "turn-1")
        self.assertEqual(event["metadata"]["receiver_thread_ids"], ["child-1"])
        self.assertEqual(event["metadata"]["agent_states"]["child-1"]["status"], "running")

    def test_runtime_reconciliation_restores_native_descendant_threads(self) -> None:
        runner = self.make_runner(isolated=True)
        with patch.object(
            runner,
            "request",
            return_value={
                "data": [
                    {
                        "id": "child-1",
                        "parentThreadId": "root-1",
                        "agentNickname": "Scout",
                        "status": {"type": "idle"},
                        "createdAt": 2,
                    },
                    {
                        "id": "grandchild-1",
                        "parentThreadId": "child-1",
                        "agentNickname": "Verifier",
                        "status": {"type": "active", "activeFlags": []},
                        "createdAt": 3,
                    },
                ],
                "nextCursor": None,
            },
        ) as request:
            runner.reconcile_descendant_threads("root-1")

        params = request.call_args.args[1]
        self.assertEqual(params["ancestorThreadId"], "root-1")
        self.assertIn("subAgent", params["sourceKinds"])
        self.assertTrue(params["useStateDbOnly"])
        self.assertEqual(
            [
                descriptor["id"]
                for descriptor in runner.child_thread_lineage("root-1", "grandchild-1")
            ],
            ["child-1", "grandchild-1"],
        )

    def test_child_notifications_never_mutate_the_root_turn_result(self) -> None:
        runner = self.make_runner(isolated=True)
        runner.active_thread_id = "root-1"
        runner.active_turn_id = "root-turn"
        messages = [
            {
                "method": "turn/started",
                "params": {"threadId": "child-1", "turn": {"id": "child-turn"}},
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "child-1",
                    "turnId": "child-turn",
                    "item": {
                        "id": "child-command",
                        "type": "commandExecution",
                        "command": "inspect child files",
                        "status": "completed",
                    },
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "child-1",
                    "turnId": "child-turn",
                    "item": {
                        "id": "child-answer",
                        "type": "agentMessage",
                        "text": "Child-only answer",
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "child-1", "turn": {"id": "child-turn"}},
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "root-1",
                    "turnId": "root-turn",
                    "item": {
                        "id": "root-answer",
                        "type": "agentMessage",
                        "text": "Root answer",
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "root-1", "turn": {"id": "root-turn"}},
            },
        ]
        streamed: list[dict[str, object]] = []
        with patch.object(runner, "next_message", side_effect=messages):
            result = runner.wait_for_turn(
                thread_id="root-1",
                turn_id="root-turn",
                event_callback=streamed.append,
            )

        self.assertEqual(result, ("Root answer", 0, 0, 0))
        self.assertIn("Child-only answer", [event.get("body") for event in streamed])
        self.assertIsNone(runner.active_turn_id)

    def test_thread_reuse_is_fenced_by_the_durable_trigger_cursor(self) -> None:
        runner = self.make_runner(isolated=True)
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(12, "@Pear continue", False)],
            latest_codex_thread_id="root-1",
            latest_codex_trigger_message_id=11,
            current_trigger_message_id=12,
        )
        runner.loaded_thread_id = "root-1"
        runner.loaded_through_trigger_message_id = 10

        with (
            patch.object(runner, "ensure_started") as ensure_started,
            patch.object(
                runner,
                "request",
                return_value={"thread": {"id": "root-1"}},
            ) as request,
            patch.object(runner, "reconcile_descendant_threads") as reconcile,
        ):
            self.assertEqual(
                runner.ensure_thread(active_session, event_callback=None),
                "root-1",
            )

        ensure_started.assert_called_once_with()
        self.assertEqual(request.call_args.args[0], "thread/resume")
        reconcile.assert_called_once_with("root-1")
        self.assertEqual(runner.loaded_through_trigger_message_id, 11)

        runner.loaded_through_trigger_message_id = 12
        with patch.object(runner, "request") as request:
            self.assertEqual(
                runner.ensure_thread(active_session, event_callback=None),
                "root-1",
            )
        request.assert_not_called()

    def test_reader_generation_cannot_signal_the_replacement_queue(self) -> None:
        runner = self.make_runner(isolated=True)
        old_messages: queue.Queue[dict[str, object] | None] = queue.Queue()
        replacement_messages: queue.Queue[dict[str, object] | None] = queue.Queue()
        process = SimpleNamespace(
            stdout=[json.dumps({"method": "old/generation", "params": {}})],
        )
        runner.messages = replacement_messages  # type: ignore[assignment]

        runner.read_messages(process, old_messages)  # type: ignore[arg-type]

        self.assertEqual(old_messages.get_nowait()["method"], "old/generation")
        self.assertIsNone(old_messages.get_nowait())
        self.assertTrue(replacement_messages.empty())

    def test_historical_descendants_are_not_replayed_into_a_new_run(self) -> None:
        runner = self.make_runner(isolated=True)
        runner.translator.thread_descriptors["child-old"] = {
            "id": "child-old",
            "parent_thread_id": "root-1",
            "agent_nickname": "Old scout",
        }
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(12, "@Pear answer directly", False)],
            latest_codex_thread_id="root-1",
            latest_codex_trigger_message_id=11,
            current_trigger_message_id=12,
        )
        events: list[dict[str, object]] = []
        with (
            patch.object(runner, "ensure_started"),
            patch.object(runner, "ensure_thread", return_value="root-1"),
            patch.object(runner, "request", return_value={"turn": {"id": "turn-1"}}),
            patch.object(runner, "wait_for_turn", return_value=("Done", 0, 0, 0)),
        ):
            runner(active_session, "answer directly", event_callback=events.append)

        self.assertEqual(events, [])

    def test_missing_thread_started_notification_uses_canonical_session_event(self) -> None:
        runner = self.make_runner(isolated=True)
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(12, "@Pear start", False)],
        )
        events: list[dict[str, object]] = []
        with patch.object(
            runner,
            "request",
            return_value={"thread": {"id": "root-new"}},
        ):
            self.assertEqual(
                runner.ensure_thread(active_session, event_callback=events.append),
                "root-new",
            )

        [session_event] = events
        self.assertEqual(session_event["kind"], "session")
        self.assertEqual(session_event["metadata"]["codex_thread_id"], "root-new")
        self.assertIsNone(session_event["metadata"]["parent_thread_id"])

    def test_child_descriptor_is_emitted_lazily_before_current_activity(self) -> None:
        runner = self.make_runner(isolated=True)
        runner.translator.thread_descriptors["child-1"] = {
            "id": "child-1",
            "parent_thread_id": "root-1",
            "agent_nickname": "Scout",
        }
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(12, "@Pear delegate", False)],
            latest_codex_thread_id="root-1",
            latest_codex_trigger_message_id=11,
            current_trigger_message_id=12,
        )
        events: list[dict[str, object]] = []

        def wait_for_turn(**kwargs):
            kwargs["event_callback"](
                {
                    "kind": "assistant",
                    "status": "done",
                    "body": "Child result",
                    "metadata": {
                        "codex_thread_id": "child-1",
                        "parent_thread_id": "root-1",
                    },
                }
            )
            return ("Root result", 0, 0, 0)

        with (
            patch.object(runner, "ensure_started"),
            patch.object(runner, "ensure_thread", return_value="root-1"),
            patch.object(runner, "request", return_value={"turn": {"id": "turn-1"}}),
            patch.object(runner, "wait_for_turn", side_effect=wait_for_turn),
        ):
            runner(active_session, "delegate", event_callback=events.append)

        self.assertEqual([event["kind"] for event in events], ["session", "assistant"])
        self.assertEqual(events[0]["metadata"]["codex_thread_id"], "child-1")

    def test_unrelated_or_unknown_thread_events_do_not_enter_the_current_run(self) -> None:
        runner = self.make_runner(isolated=True)
        runner.translator.thread_descriptors["other-child"] = {
            "id": "other-child",
            "parent_thread_id": "other-root",
            "agent_nickname": "Unrelated",
        }
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(12, "@Pear answer", False)],
            latest_codex_thread_id="root-1",
            latest_codex_trigger_message_id=11,
            current_trigger_message_id=12,
        )
        events: list[dict[str, object]] = []

        def wait_for_turn(**kwargs):
            for event_thread_id in ("other-child", "unknown-child"):
                kwargs["event_callback"](
                    {
                        "kind": "assistant",
                        "status": "done",
                        "body": "Not part of this run",
                        "metadata": {"codex_thread_id": event_thread_id},
                    }
                )
            return ("Root result", 0, 0, 0)

        with (
            patch.object(runner, "ensure_started"),
            patch.object(runner, "ensure_thread", return_value="root-1"),
            patch.object(runner, "request", return_value={"turn": {"id": "turn-1"}}),
            patch.object(runner, "wait_for_turn", side_effect=wait_for_turn),
        ):
            runner(active_session, "answer", event_callback=events.append)

        self.assertEqual(events, [])

    def test_interrupt_uses_the_native_turn_id_field(self) -> None:
        runner = self.make_runner(isolated=True)
        runner.process = SimpleNamespace(poll=lambda: None)
        runner.active_thread_id = "root-1"
        runner.active_turn_id = "turn-1"
        with patch.object(runner, "send_message") as send_message:
            self.assertTrue(runner.interrupt())

        params = send_message.call_args.args[0]["params"]
        self.assertEqual(params, {"threadId": "root-1", "turnId": "turn-1"})
        self.assertNotIn("expectedTurnId", params)

    def test_unknown_methods_surface_once_and_deltas_never(self) -> None:
        translator = self.make_translator()
        first = translator.events_from_message(
            {"method": "thread/somethingNew", "params": {"a": 1}}
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["title"], "Codex signal: thread/somethingNew")
        repeat = translator.events_from_message(
            {"method": "thread/somethingNew", "params": {"a": 2}}
        )
        self.assertEqual(repeat, [])
        delta = translator.events_from_message(
            {"method": "item/reasoning/summaryTextDelta", "params": {}}
        )
        self.assertEqual(delta, [])

    def test_context_compaction_items_become_operation_events(self) -> None:
        translator = self.make_translator()
        started = translator.events_from_message(
            {
                "method": "item/started",
                "params": {"item": {"id": "item_1", "type": "contextCompaction"}},
            }
        )
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["title"], "Compacting context…")
        self.assertEqual(started[0]["metadata"]["operation"], "compaction")
        self.assertEqual(started[0]["metadata"]["phase"], "started")
        completed = translator.events_from_message(
            {
                "method": "item/completed",
                "params": {
                    "item": {"id": "item_1", "type": "contextCompaction", "status": "completed"}
                },
            }
        )
        self.assertEqual(completed[0]["title"], "Context compacted.")
        self.assertEqual(completed[0]["metadata"]["phase"], "completed")

    def test_user_message_items_stay_out_of_the_transcript(self) -> None:
        translator = self.make_translator()
        events = translator.events_from_message(
            {
                "method": "item/completed",
                "params": {"item": {"id": "item_2", "type": "userMessage", "text": "hi"}},
            }
        )
        self.assertEqual(events, [])

    def make_runner(self, *, isolated: bool) -> PearCodexAppServerToolRunner:
        return PearCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=None,
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="on-request",
            timeout_seconds=5,
            output_limit=1000,
            process_isolation=(
                PearProcessIsolation(
                    username="pear-test", home="/home/pear-test", codex_home="/tmp"
                )
                if isolated
                else None
            ),
        )

    def test_escalation_approvals_follow_the_isolation_boundary(self) -> None:
        isolated = self.make_runner(isolated=True)
        result, error, event = isolated.answer_for_server_request(
            "item/commandExecution/requestApproval", {"command": "apt-get install jq"}
        )
        self.assertEqual(result, {"decision": "acceptForSession"})
        self.assertIsNone(error)
        assert event is not None
        self.assertEqual(event["title"], "Approved an escalation request.")

        exposed = self.make_runner(isolated=False)
        result, error, event = exposed.answer_for_server_request(
            "item/fileChange/requestApproval", {"reason": "write outside worktree"}
        )
        self.assertEqual(result, {"decision": "decline"})
        assert event is not None
        self.assertEqual(event["title"], "Declined an escalation request.")

    def test_request_user_input_declines_visibly_for_now(self) -> None:
        runner = self.make_runner(isolated=True)
        result, error, event = runner.answer_for_server_request(
            "item/tool/requestUserInput",
            {"questions": [{"id": "q1", "question": "alpha or beta?"}]},
        )
        self.assertIsNone(result)
        assert error is not None
        self.assertEqual(error["code"], -32601)
        assert event is not None
        self.assertIn("alpha or beta?", event["body"])

    def test_unknown_requests_decline_with_an_event(self) -> None:
        runner = self.make_runner(isolated=True)
        result, error, event = runner.answer_for_server_request(
            "attestation/generate", {"nonce": "x"}
        )
        self.assertIsNone(result)
        assert error is not None
        self.assertEqual(error["code"], -32601)
        assert event is not None
        self.assertEqual(event["title"], "Codex request declined: attestation/generate")

    def test_background_responses_never_fail_a_wait_loop(self) -> None:
        runner = self.make_runner(isolated=True)
        runner.background_request_ids.add(41)
        self.assertTrue(runner.consume_background_response({"id": 41, "error": {"code": 1}}))
        self.assertFalse(runner.consume_background_response({"id": 41, "error": {"code": 1}}))
        self.assertFalse(runner.consume_background_response({"id": 7, "result": {}}))

    def test_abort_wakes_next_message_with_the_lease_error(self) -> None:
        runner = self.make_runner(isolated=True)
        lease_error = PearWorkerLeaseLostError("attempt 1 lost its lease")

        runner.abort(lease_error)

        with self.assertRaises(PearWorkerLeaseLostError) as raised:
            runner.next_message(0)
        self.assertIs(raised.exception, lease_error)

    def test_response_timeout_is_renewed_by_protocol_activity(self) -> None:
        runner = self.make_runner(isolated=True)
        messages = [
            {"method": "thread/status/changed", "params": {}},
            {"method": "account/updated", "params": {}},
            {"id": 7, "result": {"ok": True}},
        ]

        with patch.object(runner, "next_message", side_effect=messages) as next_message:
            self.assertEqual(
                runner.wait_for_response(7, timeout_seconds=2),
                {"ok": True},
            )

        self.assertEqual(
            [record.args for record in next_message.call_args_list],
            [(2,), (2,), (2,)],
        )

    def test_turn_timeout_is_renewed_by_protocol_activity(self) -> None:
        runner = self.make_runner(isolated=True)
        messages = [
            {"method": "thread/status/changed", "params": {}},
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turn": {"id": "turn-1"}},
            },
        ]

        with patch.object(runner, "next_message", side_effect=messages) as next_message:
            self.assertEqual(
                runner.wait_for_turn(
                    thread_id="thread-1",
                    turn_id="turn-1",
                    event_callback=None,
                ),
                ("", 0, 0, 0),
            )

        self.assertEqual([record.args for record in next_message.call_args_list], [(5,), (5,)])

    def test_timeout_reports_the_actual_inactivity_window(self) -> None:
        runner = self.make_runner(isolated=True)
        with (
            patch.object(runner.messages, "get", side_effect=queue.Empty),
            self.assertRaisesRegex(
                PearAgentRuntimeError,
                "Codex app-server produced no activity for 2 seconds",
            ),
        ):
            runner.wait_for_response(7, timeout_seconds=2)


class PearInteractiveTurnTest(TestCase):
    """Native Codex tool interactions."""

    def make_runner(self) -> PearCodexAppServerToolRunner:
        return PearCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=None,
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="on-request",
            timeout_seconds=5,
            output_limit=1000,
        )

    def test_turn_params_contain_only_runtime_turn_configuration(self) -> None:
        runner = self.make_runner()
        params = runner.turn_params("thread_1", "prompt")
        self.assertNotIn("collaborationMode", params)

    def test_thread_params_register_native_artifact_tool(self) -> None:
        params = self.make_runner().thread_params()
        [tool] = params["dynamicTools"]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["name"], "pear_artifact")
        self.assertEqual(
            tool["inputSchema"]["$id"], "https://pear.backpropaganda.net/contracts/artifact-v1.json"
        )

    def test_artifact_dynamic_tool_accepts_and_retains_canonical_document(self) -> None:
        runner = self.make_runner()
        result, error, event = runner.answer_for_server_request(
            "item/tool/call",
            {
                "tool": "pear_artifact",
                "callId": "artifact-call-1",
                "namespace": None,
                "arguments": {
                    "version": 1,
                    "blocks": [{"type": "copy", "body": "A concise explanation."}],
                },
            },
        )
        self.assertIsNone(error)
        assert result is not None
        self.assertTrue(result["success"])
        self.assertEqual(len(runner.pending_artifacts), 1)
        self.assertEqual(json.loads(runner.pending_artifacts[0])["version"], 1)
        assert event is not None
        self.assertEqual(event["title"], "Artifact ready.")
        self.assertEqual(event["item_id"], "artifact-call-1")
        self.assertIs(event["metadata"]["human_tool_result"], True)

    def test_artifact_dynamic_tool_returns_actionable_validation_error(self) -> None:
        runner = self.make_runner()
        result, error, event = runner.answer_for_server_request(
            "item/tool/call",
            {
                "tool": "pear_artifact",
                "arguments": {"version": 1, "blocks": [], "style": "green"},
            },
        )
        self.assertIsNone(error)
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual(runner.pending_artifacts, [])
        assert event is not None
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["body"], "$.blocks: array must contain at least 1 item")
        self.assertIn(
            "$.blocks: array must contain at least 1 item", result["contentItems"][0]["text"]
        )

    def test_request_user_input_answers_through_the_provider(self) -> None:
        runner = self.make_runner()
        asked: list[list[dict[str, object]]] = []

        def provider(questions: list[dict[str, object]]) -> dict[str, list[str]]:
            asked.append(questions)
            return {"q1": ["alpha"]}

        runner.current_user_input_provider = provider
        result, error, event = runner.answer_for_server_request(
            "item/tool/requestUserInput",
            {"questions": [{"id": "q1", "question": "alpha or beta?"}]},
        )
        self.assertIsNone(error)
        self.assertIsNone(event)
        self.assertEqual(result, {"answers": {"q1": {"answers": ["alpha"]}}})
        self.assertEqual(len(asked), 1)

    def test_request_user_input_declines_when_provider_times_out(self) -> None:
        runner = self.make_runner()
        runner.current_user_input_provider = lambda questions: None
        result, error, event = runner.answer_for_server_request(
            "item/tool/requestUserInput",
            {"questions": [{"id": "q1", "question": "alpha or beta?"}]},
        )
        self.assertIsNone(result)
        assert error is not None
        self.assertEqual(error["code"], -32601)
        assert event is not None
        self.assertIn("no answer arrived", event["title"])
