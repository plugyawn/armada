import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from zerver.lib.exceptions import JsonableError
from zerver.lib.pear import (
    PEAR_ACCESS_PROMPT,
    PEAR_ACTIVE_MARKDOWN_PREFIX,
    PEAR_ACTIVE_SESSION_ENDPOINT,
    PEAR_ACTIVE_THINKING_MARKDOWN,
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
    PEAR_OFFICIAL_AGENT_RUNTIME,
    PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
    PEAR_OFFICIAL_CODEX_SDK_PACKAGES,
    PEAR_PAUSE_PROMPT,
    PEAR_PLAN_PROMPT,
    PEAR_RESUME_PROMPT,
    PEAR_REVIEW_PROMPT,
    PEAR_RUNS_ENDPOINT,
    PEAR_SAVE_WORK_PROMPT,
    PEAR_SERVER_CONTRACT_VERSION,
    PEAR_SESSION_CHECKLIST,
    PEAR_SESSION_ENDPOINT,
    PEAR_SIDE_ENDPOINT,
    PEAR_STATUS_CHECK_PROMPT,
    PEAR_STEER_PROMPT,
    PEAR_STEERING_APPLIED_MARKDOWN,
    PEAR_STEERING_QUEUED_MARKDOWN,
    PEAR_TOPIC_PREFIX,
    PEAR_TOPIC_STATE_TOOL_NAMES,
    PEAR_TOPIC_STATE_TOOLS,
    PEAR_TRIGGER_MESSAGE_KEY,
    PEAR_VERIFY_CHANGES_PROMPT,
    PEAR_WORKER_RUNS_STORAGE_KEY,
    PearActiveSessionSummary,
    PearEmbeddedBotHandler,
    PearWorkerRunResult,
    PearWorkerRuntime,
    PearWorkerTranscriptCallback,
    build_pear_access_ack_message,
    build_pear_access_message,
    build_pear_ack_message,
    build_pear_active_session_summary,
    build_pear_active_session_summary_from_messages,
    build_pear_apply_plan_message,
    build_pear_catch_up_message,
    build_pear_checklist_lines,
    build_pear_checkpoint_message,
    build_pear_config_payload,
    build_pear_draft_artifact_message,
    build_pear_find_answer_message,
    build_pear_fix_bug_message,
    build_pear_handoff_message,
    build_pear_pause_ack_message,
    build_pear_pause_message,
    build_pear_plan_message,
    build_pear_resume_ack_message,
    build_pear_resume_message,
    build_pear_review_message,
    build_pear_save_work_message,
    build_pear_seed_message,
    build_pear_status_check_message,
    build_pear_steer_message,
    build_pear_steering_ack_message,
    build_pear_triage_message,
    build_pear_verify_changes_message,
    build_pear_worker_completed_message,
    build_pear_worker_completed_message_chunks,
    build_pear_worker_progress_message,
    clean_pear_task_text,
    get_pear_live_run_state,
    get_pear_session_defaults,
    get_pear_session_payload,
    is_pear_access_request_message,
    is_pear_request_message,
    is_pear_thread_request_message,
    load_pear_worker_run_records,
    parse_pear_artifacts,
    parse_pear_checklist,
    parse_pear_interruption_event,
    parse_pear_steering_event,
    pear_normalize_token_usage,
    pear_session_record_kind,
    pear_session_record_url,
    pear_turn_collaboration_mode,
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
    PearCodexAppServerToolRunner,
    PearCodexAppServerTranscriptTranslator,
    PearCodexExecAddressingIntentClassifier,
    PearDisabledAgentRunner,
    PearGeminiInteractionsAddressingIntentClassifier,
    PearProcessIsolation,
    build_pear_addressing_router_prompt,
    build_pear_codex_developer_instructions,
    build_pear_codex_turn_prompt,
    ensure_pear_runtime_result,
    merge_pear_tool_results,
    parse_pear_addressing_router_output,
    pear_tool_results_from_text,
)
from zerver.lib.pear_worker import (
    PEAR_WORKER_QUEUE_NAME,
    PearQueueWorkerDispatcher,
    pear_provision_codex_skills,
)
from zerver.worker import pear_worker as pear_worker_module
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
                "PEAR_ADDRESSING_ROUTER_GEMINI_API_KEY"
            ]("PEAR_ADDRESSING_ROUTER_GEMINI_API_KEY")

        self.assertEqual(api_key, "gemini-key")

    def test_pear_model_choices_default_to_configured_worker_model(self) -> None:
        with patch.dict("os.environ", {"PEAR_WORKER_MODEL": "gpt-5.4-mini"}, clear=True):
            choices = pear_computed_settings._PEAR_ENV_OVERRIDES["PEAR_WORKER_MODEL_CHOICES"](
                "PEAR_WORKER_MODEL_CHOICES"
            )

        self.assertEqual(choices, ["gpt-5.4-mini"])

    def test_pear_env_bool_rejects_invalid_value(self) -> None:
        with patch.dict("os.environ", {"PEAR_WORKER_FAST_MODE": "sometimes"}, clear=False):
            with self.assertRaisesRegex(ValueError, "PEAR_WORKER_FAST_MODE"):
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
        self.assertEqual(defaults.checklist, PEAR_SESSION_CHECKLIST)
        self.assertEqual(PEAR_BOT_SHORT_NAME, "pear")
        self.assertEqual(PEAR_EMBEDDED_BOT_SERVICE, "pear")
        self.assertEqual(PEAR_STEER_PROMPT, "Steering update:")
        self.assertEqual(PEAR_FIX_BUG_PROMPT, "Fix bug:")

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

    def test_session_record_helpers_classify_openable_sessions(self) -> None:
        self.assertEqual(
            pear_session_record_url("Open https://codex.example/session/thread-123."),
            "https://codex.example/session/thread-123",
        )
        self.assertEqual(
            pear_session_record_kind("Open https://codex.example/session/thread-123"),
            "url",
        )
        self.assertEqual(
            pear_session_record_kind("Codex thread thread-123"),
            "codex_thread",
        )
        self.assertEqual(pear_session_record_kind("local transcript saved"), "text")
        self.assertIsNone(pear_session_record_kind(None))
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
        self.assertEqual(PEAR_ACCESS_PROMPT, "What can you access from this topic?")
        self.assertEqual(PEAR_PAUSE_PROMPT, "Pause session:")
        self.assertEqual(PEAR_RESUME_PROMPT, "Resume session:")

    def test_build_checklist_lines(self) -> None:
        checklist_lines = build_pear_checklist_lines()

        self.assertEqual(len(checklist_lines), len(PEAR_SESSION_CHECKLIST))
        self.assertEqual(
            checklist_lines[0],
            "- [ ] Confirm repo, branch, and target runtime.",
        )
        self.assertTrue(all(line.startswith("- [ ] ") for line in checklist_lines))

    def test_build_seed_message(self) -> None:
        seed_message = build_pear_seed_message("fix the failing checkout test")

        self.assertIn("@**Pear** fix the failing checkout test", seed_message)
        self.assertIn("Confirm repo, branch, and target runtime.", seed_message)
        self.assertIn("Post verification evidence", seed_message)

    def test_build_seed_message_with_empty_task(self) -> None:
        seed_message = build_pear_seed_message()

        self.assertIn("@**Pear** Start the topic Codex thread:", seed_message)

    def test_build_steer_message(self) -> None:
        steer_message = build_pear_steer_message("focus on the failing UI test")

        self.assertIn("@**Pear** Steering update:", steer_message)
        self.assertIn("Request: focus on the failing UI test", steer_message)

    def test_build_fix_bug_message(self) -> None:
        fix_message = build_pear_fix_bug_message("checkout crashes on empty cart")

        self.assertIn("@**Pear** Fix bug:", fix_message)
        self.assertIn("Bug: checkout crashes on empty cart", fix_message)
        self.assertIn("Observed behavior: ", fix_message)
        self.assertIn("Expected behavior: ", fix_message)
        self.assertIn("How to verify: ", fix_message)
        self.assertIn(
            "Workspace context: use the latest Pear workspace artifacts",
            fix_message,
        )
        self.assertFalse(parse_pear_artifacts(fix_message, message_id=1))

    def test_parse_pear_artifacts_recognizes_commit_and_document_labels(self) -> None:
        artifacts = parse_pear_artifacts(
            "Commit: abc1234\nSHA: deadbeef\nUnknown: ignored\nDoc: README.md refreshed",
            message_id=21,
        )

        self.assertEqual(
            [(artifact["kind"], artifact["value"]) for artifact in artifacts],
            [
                ("commit", "abc1234"),
                ("commit", "deadbeef"),
                ("document", "README.md refreshed"),
            ],
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
        self.assertFalse(parse_pear_artifacts(status_message, message_id=1))

    def test_build_handoff_message(self) -> None:
        handoff_message = build_pear_handoff_message("next developer")

        self.assertIn("@**Pear** Handoff summary:", handoff_message)
        self.assertIn("Audience: next developer", handoff_message)
        self.assertIn("Goal and scope: ", handoff_message)
        self.assertIn("Current state: ", handoff_message)
        self.assertIn("Key files and artifacts: ", handoff_message)
        self.assertIn("Open decisions: ", handoff_message)
        self.assertIn("Next steps: ", handoff_message)
        self.assertIn("Checks still needed: ", handoff_message)
        self.assertFalse(parse_pear_artifacts(handoff_message, message_id=1))

    def test_build_catch_up_message(self) -> None:
        catch_up_message = build_pear_catch_up_message("yesterday")

        self.assertIn("@**Pear** Catch up:", catch_up_message)
        self.assertIn("Since: yesterday", catch_up_message)
        self.assertIn("Summarize changes: ", catch_up_message)
        self.assertIn("Decisions made: ", catch_up_message)
        self.assertIn("Artifacts to inspect: ", catch_up_message)
        self.assertIn("Current blockers: ", catch_up_message)
        self.assertIn("Next actions: ", catch_up_message)
        self.assertIn("Use the active topic context", catch_up_message)
        self.assertFalse(parse_pear_artifacts(catch_up_message, message_id=1))

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
        self.assertIn("Durable output:", triage_message)
        self.assertFalse(parse_pear_artifacts(triage_message, message_id=1))

    def test_build_find_answer_message(self) -> None:
        find_answer_message = build_pear_find_answer_message("Where is auth configured?")

        self.assertIn("@**Pear** Find answer:", find_answer_message)
        self.assertIn("Question: Where is auth configured?", find_answer_message)
        self.assertIn("Search scope:", find_answer_message)
        self.assertIn("Evidence to cite:", find_answer_message)
        self.assertIn("Answer policy:", find_answer_message)
        self.assertIn("Durable output:", find_answer_message)
        self.assertFalse(parse_pear_artifacts(find_answer_message, message_id=1))

    def test_build_review_message(self) -> None:
        review_message = build_pear_review_message("current branch")

        self.assertIn("@**Pear** Review changes:", review_message)
        self.assertIn("Scope: current branch", review_message)
        self.assertIn("Review focus: correctness", review_message)
        self.assertIn(
            "Diff or files to inspect: use the latest Pear workspace artifacts", review_message
        )
        self.assertIn("Risk areas: ", review_message)
        self.assertIn("Expected output: prioritized findings", review_message)
        self.assertIn("Verification to consider: ", review_message)
        self.assertFalse(parse_pear_artifacts(review_message, message_id=1))

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
        self.assertFalse(parse_pear_artifacts(plan_message, message_id=1))

    def test_build_checkpoint_message(self) -> None:
        checkpoint_message = build_pear_checkpoint_message("checkout regression")

        self.assertIn("@**Pear** Checkpoint:", checkpoint_message)
        self.assertIn("Focus: checkout regression", checkpoint_message)
        self.assertIn("current branch, diff summary", checkpoint_message)
        self.assertIn("Verification artifact lines", checkpoint_message)
        self.assertIn("wait for confirmation", checkpoint_message)
        self.assertFalse(parse_pear_artifacts(checkpoint_message, message_id=1))

    def test_build_apply_plan_message(self) -> None:
        apply_plan_message = build_pear_apply_plan_message("checkout regression")

        self.assertIn("@**Pear** Apply plan:", apply_plan_message)
        self.assertIn("Scope: checkout regression", apply_plan_message)
        self.assertIn("latest Implementation plan", apply_plan_message)
        self.assertIn("make the scoped edits now", apply_plan_message)
        self.assertIn("planned verification", apply_plan_message)
        self.assertFalse(parse_pear_artifacts(apply_plan_message, message_id=1))

    def test_build_save_work_message(self) -> None:
        save_work_message = build_pear_save_work_message("checkout regression")

        self.assertIn("@**Pear** Save work:", save_work_message)
        self.assertIn("Scope: checkout regression", save_work_message)
        self.assertIn("branch, draft PR, patch", save_work_message)
        self.assertIn("Branch, PR, Diff, or Doc", save_work_message)
        self.assertIn("recoverable from this topic", save_work_message)
        self.assertFalse(parse_pear_artifacts(save_work_message, message_id=1))

    def test_build_verify_changes_message(self) -> None:
        verify_message = build_pear_verify_changes_message("checkout regression")

        self.assertIn("@**Pear** Verify changes:", verify_message)
        self.assertIn("Target: checkout regression", verify_message)
        self.assertIn("Checks to run: use the planned verification", verify_message)
        self.assertIn("Fix policy: if a check fails", verify_message)
        self.assertIn("Evidence to post: commands run", verify_message)
        self.assertFalse(parse_pear_artifacts(verify_message, message_id=1))

    def test_build_draft_artifact_message(self) -> None:
        artifact_message = build_pear_draft_artifact_message("issue for flaky tests")

        self.assertIn("@**Pear** Draft artifact:", artifact_message)
        self.assertIn("Artifact: issue for flaky tests", artifact_message)
        self.assertIn("Source context: use this Zulip topic", artifact_message)
        self.assertIn("Must include: scope, decisions", artifact_message)
        self.assertIn("Doc, Ticket, or Issue artifact line", artifact_message)
        self.assertFalse(parse_pear_artifacts(artifact_message, message_id=1))

    def test_build_access_message(self) -> None:
        access_message = build_pear_access_message()

        self.assertEqual(
            access_message,
            "@**Pear** What can you access from this topic?",
        )

    def test_build_access_ack_message(self) -> None:
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(
                stream="frontend",
                topic="checkout/regression",
            ),
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False, "Ada"),
                (11, "Verification: pytest zerver/tests/test_pear.py", True, "Pear"),
                (12, "PR: https://github.com/example/pear/pull/7", True, "Pear"),
                (13, "Doc: README.md", False, "Grace"),
                (14, "Reviewers: @qa", False, "Grace"),
            ],
            latest_codex_thread_id="codex-thread-7",
            workspace_context={
                "repository": "zulip/zulip",
                "workdir": "/srv/pear-workspaces/frontend-checkout-regression",
                "branch": "checkout/regression",
                "head": "abc1234",
            },
            response_policy="ambient",
        )

        access_ack = build_pear_access_ack_message(active_session)

        self.assertIn("frontend > checkout/regression", access_ack)
        self.assertIn("durable topic context", access_ack)
        self.assertIn("configured Codex runtime", access_ack)
        self.assertIn("zulip-terminal can act on posted artifacts", access_ack)
        self.assertIn("Loaded transcript: 5 recent message(s) from 5 topic message(s)", access_ack)
        self.assertIn("Visible senders: Ada, Pear, Grace", access_ack)
        self.assertIn(
            "Workspace: zulip/zulip at /srv/pear-workspaces/frontend-checkout-regression",
            access_ack,
        )
        self.assertIn("on checkout/regression (abc1234)", access_ack)
        self.assertIn("Codex thread: codex-thread-7", access_ack)
        self.assertIn("Worker state: ready", access_ack)
        self.assertIn("Artifacts seen: 4", access_ack)
        self.assertIn("Response policy: ambient", access_ack)
        self.assertIn("pytest zerver/tests/test_pear.py", access_ack)

    def test_build_steer_message_with_empty_instruction(self) -> None:
        steer_message = build_pear_steer_message()

        self.assertEqual(steer_message, "@**Pear** Steering update:\n\nRequest: ")

    def test_build_pause_and_resume_messages(self) -> None:
        self.assertEqual(
            build_pear_pause_message("waiting for deploy"),
            "@**Pear** Pause session:\n\nReason: waiting for deploy",
        )
        self.assertEqual(
            build_pear_pause_message(),
            "@**Pear** Pause session:\n\nReason: ",
        )
        self.assertEqual(
            build_pear_resume_message("continue from verification"),
            "@**Pear** Resume session:\n\nRequest: continue from verification",
        )
        self.assertEqual(
            build_pear_resume_message(),
            "@**Pear** Resume session:\n\nRequest: ",
        )

    def test_build_ack_message(self) -> None:
        ack_message = build_pear_ack_message("debug the flaky parser test")

        self.assertEqual(ack_message, PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertNotIn("Open the Pear panel", ack_message)
        self.assertNotIn("Confirm repo, branch, and target runtime", ack_message)

    def test_build_ack_message_strips_pear_mention(self) -> None:
        ack_message = build_pear_ack_message("@**Pear** debug the flaky parser test")

        self.assertEqual(ack_message, PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertNotIn("Task: @**Pear**", ack_message)

    def test_build_control_ack_messages(self) -> None:
        steering_ack = build_pear_steering_ack_message("keep the fix minimal")
        pause_ack = build_pear_pause_ack_message("waiting for CI")
        resume_ack = build_pear_resume_ack_message("continue with rollout")

        self.assertEqual(steering_ack, "Steering queued.")
        self.assertIn("Paused.", pause_ack)
        self.assertIn("Reason: waiting for CI", pause_ack)
        self.assertIn("Resuming.", resume_ack)
        self.assertIn("Request: continue with rollout", resume_ack)

    def test_session_payload(self) -> None:
        payload = get_pear_session_payload()

        self.assertEqual(payload["agent_name"], "Pear")
        self.assertEqual(payload["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(payload["topic"], PEAR_DEFAULT_TOPIC)
        self.assertEqual(payload["checklist"], list(PEAR_SESSION_CHECKLIST))
        self.assertEqual(payload["steer_message"], build_pear_steer_message())
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
        self.assertEqual(payload["access_message"], build_pear_access_message())
        self.assertEqual(payload["pause_message"], build_pear_pause_message())
        self.assertEqual(payload["resume_message"], build_pear_resume_message())
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
            payload["pear_topic_state_tools"],
            list(PEAR_TOPIC_STATE_TOOLS),
        )
        self.assertEqual(
            payload["pear_topic_state_tool_names"],
            list(PEAR_TOPIC_STATE_TOOL_NAMES),
        )
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

    def test_parse_pear_checklist(self) -> None:
        content = "\n".join(
            [
                "- [x] Confirm repo, branch, and target runtime.",
                "- [~] State the first concrete edit before changing files.",
                "- [ ] Keep progress visible in this topic.",
            ]
        )

        checklist = parse_pear_checklist(content)

        self.assertEqual(
            checklist,
            [
                {
                    "status": "done",
                    "text": "Confirm repo, branch, and target runtime.",
                },
                {
                    "status": "in_progress",
                    "text": "State the first concrete edit before changing files.",
                },
                {
                    "status": "pending",
                    "text": "Keep progress visible in this topic.",
                },
            ],
        )

    def test_parse_pear_artifacts(self) -> None:
        artifacts = parse_pear_artifacts(
            "\n".join(
                [
                    "Branch: pear/runtime-artifacts",
                    "Repo: /workspace/pear (origin git@github.com:example/pear.git)",
                    "Runtime: codex-agents-mcp cwd=/workspace/pear",
                    "- PR: https://github.com/example/pear/pull/7",
                    "Session: Codex thread thread-123",
                    "* Verification: pytest zerver/tests/test_pear.py",
                    "Reviewers: alice, example/team-reviewers",
                    "Screenshot: https://example.com/screenshot.png",
                    "Routine: Watch PR status for https://github.com/example/pear/pull/7",
                    "Memory: Prefer small focused PRs",
                    "Done: Tests pass and draft PR is posted",
                    "Ambient: mentions only",
                    "Routine cadence: manual",
                    "Docs: pear/ROADMAP.md",
                    "Issue: https://github.com/example/pear/issues/12",
                    "Ticket: PAIR-42",
                ]
            ),
            message_id=81,
        )

        self.assertEqual(
            artifacts,
            [
                {
                    "kind": "branch",
                    "label": "Branch",
                    "value": "pear/runtime-artifacts",
                    "message_id": 81,
                },
                {
                    "kind": "repository",
                    "label": "Repo",
                    "value": "/workspace/pear (origin git@github.com:example/pear.git)",
                    "message_id": 81,
                },
                {
                    "kind": "runtime",
                    "label": "Runtime",
                    "value": "codex-agents-mcp cwd=/workspace/pear",
                    "message_id": 81,
                },
                {
                    "kind": "pull_request",
                    "label": "PR",
                    "value": "https://github.com/example/pear/pull/7",
                    "message_id": 81,
                },
                {
                    "kind": "session_record",
                    "label": "Session",
                    "value": "Codex thread thread-123",
                    "message_id": 81,
                },
                {
                    "kind": "verification",
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                    "message_id": 81,
                },
                {
                    "kind": "reviewers",
                    "label": "Reviewers",
                    "value": "alice, example/team-reviewers",
                    "message_id": 81,
                },
                {
                    "kind": "screenshot",
                    "label": "Screenshot",
                    "value": "https://example.com/screenshot.png",
                    "message_id": 81,
                },
                {
                    "kind": "document",
                    "label": "Docs",
                    "value": "pear/ROADMAP.md",
                    "message_id": 81,
                },
                {
                    "kind": "ticket",
                    "label": "Issue",
                    "value": "https://github.com/example/pear/issues/12",
                    "message_id": 81,
                },
                {
                    "kind": "ticket",
                    "label": "Ticket",
                    "value": "PAIR-42",
                    "message_id": 81,
                },
            ],
        )

    def test_parse_pear_steering_event(self) -> None:
        self.assertEqual(
            parse_pear_steering_event(
                "@**Pear** Steering update:\n\nRequest: keep the fix minimal",
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
        self.assertEqual(
            parse_pear_steering_event(
                "Fold in the failing terminal UI test too.",
                83,
                sender_is_bot=False,
                session_started=True,
            ),
            {
                "instruction": "Fold in the failing terminal UI test too.",
                "message_id": 83,
                "explicitly_addressed": False,
            },
        )
        self.assertIsNone(
            parse_pear_steering_event(
                "- [~] State the first concrete edit before changing files.",
                84,
                sender_is_bot=True,
                session_started=True,
            )
        )
        self.assertIsNone(
            parse_pear_steering_event(
                "@Pear start a new pairing session",
                85,
                sender_is_bot=False,
                session_started=False,
            )
        )

    def test_parse_pear_interruption_event(self) -> None:
        self.assertEqual(
            parse_pear_interruption_event(
                "@**Pear** Pause session:\n\nReason: waiting for CI",
                86,
                sender_is_bot=False,
            ),
            {
                "kind": "pause",
                "instruction": "waiting for CI",
                "message_id": 86,
            },
        )
        self.assertEqual(
            parse_pear_interruption_event(
                "@**Pear** Resume session:\n\nRequest: continue with rollout",
                87,
                sender_is_bot=False,
            ),
            {
                "kind": "resume",
                "instruction": "continue with rollout",
                "message_id": 87,
            },
        )
        self.assertIsNone(
            parse_pear_interruption_event(
                "@**Pear** Pause session:\n\nReason: waiting for CI",
                88,
                sender_is_bot=True,
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
        self.assertFalse(is_pear_thread_request_message("@Pear thanks!"))
        self.assertFalse(is_pear_thread_request_message("@Pear thank you."))
        self.assertFalse(is_pear_thread_request_message("@Pear What can you access?"))
        self.assertFalse(is_pear_thread_request_message("debug the flaky parser test"))

    def test_is_pear_access_request_message(self) -> None:
        self.assertTrue(
            is_pear_access_request_message("@**Pear** What can you access from this topic?")
        )
        self.assertTrue(is_pear_access_request_message("@Pear what can you reach?"))
        self.assertFalse(is_pear_access_request_message("@Pear fix the checkout test"))

    def test_build_active_session_summary(self) -> None:
        payload = get_pear_session_payload()
        checklist = parse_pear_checklist("- [x] Confirm repo, branch, and target runtime.")

        summary = build_pear_active_session_summary(
            pear_session=payload,
            source="server",
            stream_exists=True,
            topic_message_count=3,
            latest_message_id=42,
            checklist=checklist,
        )

        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["source"], "server")
        self.assertEqual(summary["stream"], PEAR_DEFAULT_STREAM)
        self.assertEqual(summary["topic_message_count"], 3)
        self.assertEqual(summary["request_count"], 0)
        self.assertIsNone(summary["latest_request_message_id"])
        self.assertEqual(summary["done_count"], 1)
        self.assertEqual(summary["artifact_count"], 0)
        self.assertIsNone(summary["latest_artifact_message_id"])
        self.assertIsNone(summary["latest_verification"])
        self.assertEqual(summary["session_record_count"], 0)
        self.assertIsNone(summary["latest_session_record_message_id"])
        self.assertIsNone(summary["latest_session_record"])
        self.assertIsNone(summary["latest_session_record_kind"])
        self.assertIsNone(summary["latest_session_record_url"])
        self.assertIsNone(summary["latest_codex_thread_id"])
        self.assertEqual(summary["response_policy"], "ambient")
        self.assertEqual(summary["artifacts"], [])
        self.assertEqual(summary["steering_count"], 0)
        self.assertIsNone(summary["latest_steering_message_id"])
        self.assertIsNone(summary["latest_steering_instruction"])
        self.assertEqual(summary["steering_events"], [])
        self.assertEqual(summary["interruption_count"], 0)
        self.assertIsNone(summary["latest_interruption_kind"])
        self.assertIsNone(summary["latest_interruption_message_id"])
        self.assertIsNone(summary["latest_interruption_instruction"])
        self.assertFalse(summary["is_paused"])
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])
        self.assertIsNone(summary["worker_blocked_reason"])
        self.assertEqual(summary["recent_messages"], [])
        self.assertEqual(summary["interruption_events"], [])

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
                    "\n".join(
                        [
                            "- [x] Confirm repo, branch, and target runtime.",
                            "- [~] State the first concrete edit before changing files.",
                            "Branch: pear/runtime-artifacts",
                            "Session: Codex thread thread-123",
                            "Verification: pytest zerver/tests/test_pear.py",
                        ]
                    ),
                    True,
                ),
                (
                    14,
                    "@**Pear** Steering update:\n\nRequest: keep the fix minimal",
                    False,
                ),
                (
                    15,
                    "@**Pear** Pause session:\n\nReason: waiting for CI",
                    False,
                ),
                (
                    16,
                    "@**Pear** Resume session:\n\nRequest: continue with rollout",
                    False,
                ),
            ],
            response_policy="ambient",
        )

        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["latest_message_id"], 13)
        self.assertEqual(summary["latest_request_message_id"], 10)
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["topic_message_count"], 10)
        self.assertEqual(summary["done_count"], 1)
        self.assertEqual(summary["in_progress_count"], 1)
        self.assertEqual(summary["artifact_count"], 3)
        self.assertEqual(summary["latest_artifact_message_id"], 13)
        self.assertEqual(
            summary["latest_verification"],
            "pytest zerver/tests/test_pear.py",
        )
        self.assertEqual(summary["session_record_count"], 1)
        self.assertEqual(summary["latest_session_record_message_id"], 13)
        self.assertEqual(summary["latest_session_record"], "Codex thread thread-123")
        self.assertEqual(summary["latest_session_record_kind"], "codex_thread")
        self.assertIsNone(summary["latest_session_record_url"])
        self.assertIsNone(summary["latest_codex_thread_id"])
        self.assertEqual(summary["response_policy"], "ambient")
        self.assertEqual(summary["artifacts"][0]["kind"], "branch")
        self.assertEqual(summary["artifacts"][1]["kind"], "session_record")
        self.assertEqual(summary["artifacts"][2]["kind"], "verification")
        self.assertEqual(summary["steering_count"], 2)
        self.assertEqual(summary["latest_steering_message_id"], 14)
        self.assertEqual(summary["latest_steering_instruction"], "keep the fix minimal")
        self.assertFalse(summary["steering_events"][1]["explicitly_addressed"])
        self.assertEqual(summary["interruption_count"], 2)
        self.assertEqual(summary["latest_interruption_kind"], "resume")
        self.assertEqual(summary["latest_interruption_message_id"], 16)
        self.assertEqual(
            summary["latest_interruption_instruction"],
            "continue with rollout",
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

    def test_build_active_session_summary_from_paused_messages(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@**Pear** fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "@**Pear** Pause session:\n\nReason: waiting for CI", False),
            ],
        )

        self.assertEqual(summary["state"], "paused")
        self.assertTrue(summary["is_paused"])
        self.assertEqual(summary["interruption_count"], 1)
        self.assertEqual(summary["latest_interruption_kind"], "pause")
        self.assertEqual(summary["latest_interruption_message_id"], 12)
        self.assertEqual(summary["latest_interruption_instruction"], "waiting for CI")
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["worker_state"], "blocked_paused")
        self.assertFalse(summary["worker_can_run"])
        self.assertIn("paused", summary["worker_blocked_reason"])

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
        self.assertEqual(summary["artifact_count"], 0)
        self.assertEqual(summary["steering_count"], 0)
        self.assertEqual(summary["interruption_count"], 0)
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])
        self.assertIsNone(summary["worker_blocked_reason"])

    def test_access_request_does_not_start_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(13, "@Pear What can you access from this topic?", False)],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertIsNone(summary["latest_request_message_id"])
        self.assertEqual(summary["steering_count"], 0)
        self.assertEqual(summary["worker_state"], "waiting_for_request")
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
        self.assertEqual(summary["pending_count"], 0)
        self.assertEqual(summary["done_count"], 0)
        self.assertEqual(summary["recent_messages"][1]["content"], "ok")
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_worker_should_start_for_active_topic_followup(self) -> None:
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
        self.assertFalse(pear_worker_should_start("@Pear thanks!", active_session))
        self.assertFalse(pear_worker_should_start("thank you @Pear", active_session))
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
        self.assertFalse(
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
            checklist=[],
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

        self.assertEqual(progress_message, PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertNotIn("Status: working...", progress_message)
        self.assertNotIn("Task: fix the checkout test", progress_message)
        self.assertNotIn("Status: queued", progress_message)
        self.assertNotIn("Confirm repo, branch, and target runtime.", progress_message)
        self.assertNotIn("Verification: pytest zerver/tests/test_pear.py", progress_message)

    def test_build_worker_progress_message_suffixes_streaming_answer(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="answer quickly",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "Partial answer",
                }
            ],
            tool_results=[],
        )

        self.assertEqual(progress_message, "Partial answer *✦*")
        self.assertNotIn("Thinking", progress_message)
        self.assertNotIn("Status:", progress_message)

    def test_build_worker_progress_message_retains_intermediate_assistant_text(self) -> None:
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

        self.assertEqual(
            progress_message,
            "I'll check the repo first.\n\nThe answer is almost ready. *✦*",
        )
        self.assertNotIn("private chain of thought", progress_message)
        self.assertNotIn("Status:", progress_message)

    def test_build_worker_progress_message_renders_codex_blocks(self) -> None:
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

        self.assertEqual(
            progress_message,
            "\n\n".join(
                [
                    "I'll check the repo state.",
                    "```spoiler Ran 1 tool\n- · `/bin/bash -lc pwd`\n```",
                    "Status checked. *✦*",
                ]
            ),
        )
        self.assertNotIn("Result: Status checked.", progress_message)

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

        self.assertEqual(
            progress_message,
            "\n\n".join(
                [
                    "I'll run the checks.",
                    "```spoiler Ran 1/2 tools ✦\n"
                    "- · `/bin/bash -lc pwd`\n"
                    "- ✦ `/bin/bash -lc pytest`\n"
                    "```",
                    "*✦*",
                ]
            ),
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

        self.assertEqual(progress_message, "Partial answer *✦*")

    def test_build_worker_message_normalizes_markdown_image_syntax(self) -> None:
        progress_message = build_pear_worker_progress_message(
            task="find images",
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Assistant",
                    "body": "\n".join(
                        [
                            "![Pikachu](https://img.pokemondb.net/artwork/large/pikachu.jpg)",
                            "",
                            "![Bulbasaur](https://img.pokemondb.net/artwork/large/bulbasaur.jpg)",
                        ]
                    ),
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
            "\n".join(
                [
                    "[Pikachu](https://img.pokemondb.net/artwork/large/pikachu.jpg)",
                    "",
                    "[Bulbasaur](https://img.pokemondb.net/artwork/large/bulbasaur.jpg) *✦*",
                ]
            ),
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
        self.assertEqual(result["content"], PEAR_ACTIVE_THINKING_MARKDOWN)

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

        self.assertEqual(completed_message, "Pear worker completed without a visible response.")
        self.assertNotIn("Status: done", completed_message)
        self.assertNotIn("Status: working...", completed_message)
        self.assertNotIn("Verification: pytest zerver/tests/test_pear.py", completed_message)

    def test_worker_completed_message_shows_runtime_error(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task="fix the checkout test",
            tool_results=[
                {
                    "label": "Runtime",
                    "value": "disabled",
                },
                {
                    "label": "Error",
                    "value": "Pear worker agent runtime is disabled.",
                },
            ],
        )

        self.assertEqual(completed_message, "Pear worker agent runtime is disabled.")

    def test_worker_completed_message_suppresses_duplicate_result_artifact(self) -> None:
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
                {
                    "label": "Session",
                    "value": "Codex thread 123",
                },
            ],
        )

        self.assertTrue(completed_message.startswith("Hi. Ready when you are."))
        self.assertNotIn("Repo: /home/vagrant/zulip\nHi. Ready when you are.", completed_message)
        self.assertNotIn("Result: Hi. Ready when you are.", completed_message)
        self.assertNotIn("Session: Codex thread 123", completed_message)

    def test_worker_completed_message_shows_answer_before_artifact_details(self) -> None:
        completed_message = build_pear_worker_completed_message(
            task='choose between A, B, D, and "Tokenmaxxer"',
            transcript_events=[
                {
                    "kind": "assistant",
                    "status": "done",
                    "title": "Assistant",
                    "body": "\n".join(
                        [
                            "Repo: /Users/progyan/codack/zulip",
                            "Runtime: Codex/Pear in Zulip topic `devel > topic`",
                            "Verification: Not run; choice-only request.",
                            "",
                            "Tokenmaxxer.",
                        ]
                    ),
                }
            ],
            tool_results=[
                {
                    "label": "Result",
                    "value": "Tokenmaxxer.",
                },
                {
                    "label": "Runtime",
                    "value": "codex-exec cwd=/home/vagrant/zulip",
                },
            ],
        )

        self.assertTrue(completed_message.startswith("Tokenmaxxer."))
        self.assertNotIn("Repo: /Users/progyan/codack/zulip\nRuntime:", completed_message)
        self.assertNotIn("Result: Tokenmaxxer.", completed_message)
        self.assertNotIn('Task: choose between A, B, D, and "Tokenmaxxer"', completed_message)
        self.assertNotIn("Runtime: codex-exec cwd=/home/vagrant/zulip", completed_message)

    def test_worker_completed_message_keeps_intermediate_blocks_before_final_answer(self) -> None:
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
                    "body": "\n".join(
                        [
                            "Repo: `/home/vagrant/zulip`",
                            "Verification: `git status --short --branch` completed successfully",
                            "Result: Status checked; ready for the next concrete task.",
                        ]
                    ),
                    "item_id": "msg-2",
                },
            ],
            tool_results=[],
        )

        self.assertEqual(
            completed_message,
            "\n\n".join(
                [
                    "I'll check the repo state.",
                    "```spoiler Ran 1 tool\n"
                    "- · `/bin/bash -lc 'pwd && git status --short --branch'`\n"
                    "```",
                    "Status checked; ready for the next concrete task.",
                ]
            ),
        )
        self.assertNotIn("*✦*", completed_message)
        self.assertNotIn("Repo:", completed_message)
        self.assertNotIn("Verification:", completed_message)
        self.assertNotIn("Result:", completed_message)

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
        self.assertIn("[earlier progress details truncated]", completed_message)
        self.assertIn("Final answer: the context bug is fixed.", completed_message)
        self.assertNotIn("*✦*", completed_message)

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
        self.assertNotIn("[earlier progress details truncated]", joined)
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
        self.assertIn("Latest steering: keep the patch small", prompt)
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
        self.assertIn(
            "Recent artifacts:\n- Verification: pytest zerver/tests/test_pear.py",
            prompt,
        )
        self.assertNotIn("Recent artifacts:\n- Memory", prompt)
        self.assertNotIn("Recent artifacts:\n- Routine", prompt)
        self.assertNotIn("Recent artifacts:\n- Done", prompt)
        self.assertNotIn("Done: <definition of done or success criteria>", prompt)
        self.assertIn(
            "if you announce the pear-artifacts skill, you must complete a successful call",
            prompt,
        )
        self.assertIn("must not substitute ASCII art or a fenced diagram", prompt)

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
        self.assertIn("README.md", instructions)
        self.assertNotIn(instructions, build_pear_codex_turn_prompt(active_session_a, "fix"))
        self.assertNotIn(instructions, build_pear_codex_turn_prompt(active_session_b, "fix"))

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

    def test_agent_text_results_parse_artifact_lines(self) -> None:
        results = pear_tool_results_from_text(
            "\n".join(
                [
                    "Verification: pytest zerver/tests/test_pear.py",
                    "Diff: updated Pear runtime",
                    "Memory: Prefer small focused PRs",
                    "Routine cadence: manual",
                ]
            ),
            output_limit=200,
        )

        self.assertEqual(
            results,
            [
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                },
                {
                    "label": "Diff",
                    "value": "updated Pear runtime",
                },
            ],
        )

    def test_ensure_runtime_result_keeps_authoritative_runtime(self) -> None:
        results = ensure_pear_runtime_result(
            [
                {"label": "Runtime", "value": "model-provided-runtime"},
                {"label": "Verification", "value": "pytest zerver/tests/test_pear.py"},
            ],
            "codex-agents-mcp cwd=/workspace sandbox=workspace-write",
        )

        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": "codex-agents-mcp cwd=/workspace sandbox=workspace-write",
                },
                {"label": "Runtime", "value": "model-provided-runtime"},
                {"label": "Verification", "value": "pytest zerver/tests/test_pear.py"},
            ],
        )

    def test_topic_state_tool_lists_only_message_layer_labels(self) -> None:
        self.assertEqual(
            list(PEAR_TOPIC_STATE_TOOLS),
            [
                "Repo",
                "Runtime",
                "Branch",
                "PR",
                "Diff",
                "Verification",
                "Screenshot",
                "Session",
                "Doc",
                "Ticket",
                "Issue",
                "Reviewers",
            ],
        )
        self.assertEqual(
            list(PEAR_TOPIC_STATE_TOOL_NAMES),
            [
                "pear_record_repo",
                "pear_record_runtime",
                "pear_record_branch",
                "pear_record_pr",
                "pear_record_diff",
                "pear_record_verification",
                "pear_record_screenshot",
                "pear_record_session",
                "pear_record_doc",
                "pear_record_ticket",
                "pear_record_issue",
                "pear_record_reviewers",
            ],
        )

    def test_merge_pear_tool_results_deduplicates_artifacts(self) -> None:
        self.assertEqual(
            merge_pear_tool_results(
                [
                    {"label": "Repo", "value": "zulip/zulip"},
                    {"label": "Verification", "value": "Codex completed"},
                ],
                [
                    {"label": "Repo", "value": "zulip/zulip"},
                    {"label": "Diff", "value": "updated worker"},
                ],
            ),
            [
                {"label": "Repo", "value": "zulip/zulip"},
                {"label": "Verification", "value": "Codex completed"},
                {"label": "Diff", "value": "updated worker"},
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

    def test_codex_exec_addressing_router_is_stateless_per_message(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

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
            storage = FakeStorage()

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if len(calls) == 1:
                stdout = (
                    '{"type":"thread.started","thread_id":"router-thread"}\n'
                    '{"type":"item.completed","item":{"type":"agent_message",'
                    '"text":"{\\"addressed\\": true}"}}\n'
                )
            else:
                stdout = (
                    '{"type":"item.completed","item":{"type":"agent_message",'
                    '"text":"{\\"addressed\\": false}"}}\n'
                )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        classifier = PearCodexExecAddressingIntentClassifier(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-router",
            reasoning_effort="low",
            sandbox="read-only",
            approval_policy="never",
            timeout_seconds=5,
            output_limit=200,
            extra_args=["--ignore-rules"],
            run_command=fake_run,
        )

        self.assertTrue(
            classifier(
                content="hey agent, can you check the failing test?",
                active_session=active_session,
                message={
                    "display_recipient": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                bot_handler=FakeBotHandler(),
            )
        )
        self.assertEqual(FakeBotHandler.storage.values, {})
        self.assertFalse(
            classifier(
                content="I pushed the branch",
                active_session=active_session,
                message={
                    "display_recipient": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                bot_handler=FakeBotHandler(),
            )
        )
        expected_command = [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--config",
            'approval_policy="never"',
            "--cd",
            "/workspace",
            "--model",
            "gpt-router",
            "--config",
            'model_reasoning_effort="low"',
            "--ignore-rules",
            "-",
        ]
        self.assertEqual(calls[0][0], expected_command)
        self.assertEqual(calls[1][0], expected_command)
        self.assertIn("Do not run commands or use tools.", calls[0][1]["input"])

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
                                        "text": '{"addressed": true}',
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

        self.assertTrue(
            classifier(
                content="hey pear, can you check this?",
                active_session=active_session,
                message={
                    "display_recipient": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                bot_handler=object(),
            )
        )
        self.assertEqual(len(calls), 1)
        request, kwargs = calls[0]
        request_payload = json.loads(request.data.decode())
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(request.full_url, "https://example.test/interactions")
        self.assertEqual(request_payload["model"], "gemini-3.1-flash-lite")
        self.assertIs(request_payload["store"], False)
        self.assertEqual(request_payload["generation_config"]["temperature"], 0)
        self.assertEqual(request_payload["generation_config"]["thinking_level"], "minimal")
        self.assertEqual(request_payload["response_format"]["mime_type"], "application/json")
        self.assertEqual(request_payload["response_format"]["schema"]["required"], ["addressed"])
        self.assertIn("Do not answer the user.", request_payload["input"])
        self.assertIn("New message:\nhey pear, can you check this?", request_payload["input"])

    def test_gemini_interactions_addressing_router_omits_gemma_thinking_budget(
        self,
    ) -> None:
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
                                        "text": '{"addressed": false}',
                                    }
                                ],
                            }
                        ],
                    }
                ).encode()

        def fake_urlopen(request, **kwargs):
            calls.append(request)
            return FakeResponse()

        classifier = PearGeminiInteractionsAddressingIntentClassifier(
            api_key="gemini-secret",
            model="gemma-4-26b-a4b-it",
            timeout_seconds=7,
            output_limit=200,
            url="https://example.test/interactions",
            thinking_level="minimal",
            urlopen=fake_urlopen,
        )

        self.assertFalse(
            classifier(
                content="I pushed the branch",
                active_session=None,
                message={
                    "display_recipient": PEAR_DEFAULT_STREAM,
                    "topic": PEAR_DEFAULT_TOPIC,
                },
                bot_handler=object(),
            )
        )
        request_payload = json.loads(calls[0].data.decode())
        self.assertEqual(request_payload["model"], "gemma-4-26b-a4b-it")
        self.assertNotIn("thinking_level", request_payload["generation_config"])

    def test_parse_pear_addressing_router_output_defaults_false(self) -> None:
        self.assertEqual(parse_pear_addressing_router_output("not json"), (False, None))
        self.assertEqual(
            parse_pear_addressing_router_output(
                '{"type":"thread.started","thread_id":"router-thread"}\n'
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"{\\"addressed\\": true}"}}\n'
            ),
            (True, "router-thread"),
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

        self.assertIn('{"addressed": true}', prompt)
        self.assertIn("When uncertain, return false.", prompt)
        self.assertIn("addressed to Pear", prompt)
        self.assertIn("Infer the addressee from the conversation", prompt)
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

        def record_event(event):
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
        self.assertIn({"label": "Verification", "value": "app-server ok"}, results)
        self.assertIn(
            {"label": "Session", "value": "Codex thread thread-app"},
            results,
        )

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
        self.assertIn(
            {"label": "Session", "value": "Codex thread thread-first"},
            first_results,
        )
        self.assertIn(
            {"label": "Session", "value": "Codex thread thread-second"},
            second_results,
        )

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
                {"label": "Runtime", "value": "disabled"},
                {"label": "Error", "value": "disabled by admin"},
            ],
        )

    def test_embedded_bot_handler_replies_with_ack(self) -> None:
        sent_replies = self.run_embedded_bot_handler(
            "@**Pear** debug the flaky parser test",
        )

        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertNotIn("Confirm repo, branch, and target runtime", sent_replies[0])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

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
        updated_replies: list[str] = []
        classifier_calls: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def addressing_intent_classifier(
            *,
            content: str,
            active_session,
            message,
            bot_handler,
        ) -> bool:
            classifier_calls.append(content)
            return True

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

        self.assertEqual(classifier_calls, ["Fold in the failing terminal UI test too."])
        self.assertEqual(run_store.starts, [(204, PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC)])
        self.assertEqual(seen_tasks, ["Fold in the failing terminal UI test too."])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertNotIn("Confirm repo, branch, and target runtime", sent_replies[0])
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

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
        updated_messages: list[dict[str, object]] = []
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

            def active_worker_run_status_message(
                self,
                bot_handler,
                *,
                stream: str,
                topic: str,
            ) -> dict[str, object]:
                self.active_checks.append((stream, topic))
                return {
                    "message_id": 401,
                    "content": "I’m checking the failing test.",
                }

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
                updated_messages.append(message)

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
            [
                (PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC),
                (PEAR_DEFAULT_STREAM, PEAR_DEFAULT_TOPIC),
            ],
        )
        self.assertEqual(sent_replies, [])
        self.assertEqual(
            updated_messages,
            [
                {
                    "message_id": 401,
                    "content": (
                        "I’m checking the failing test.\n\n"
                        + PEAR_STEERING_QUEUED_MARKDOWN
                        + " "
                        + PEAR_ACTIVE_MARKDOWN_PREFIX
                    ),
                }
            ],
        )
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
        self.assertEqual(
            metadata_updates,
            [(101, {"latest_steering": "can i interrupt? You can stop"})],
        )

    def test_embedded_bot_handler_records_steering_without_reply_when_status_edit_fails(
        self,
    ) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, PEAR_ACTIVE_THINKING_MARKDOWN, True),
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

            def active_worker_run_status_message(
                self,
                bot_handler,
                *,
                stream: str,
                topic: str,
            ) -> None:
                return None

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
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

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
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

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
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertNotIn("steering update", sent_replies[0])
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

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
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)
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
            response_policy="mentions_only",
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
            message,
            bot_handler,
        ) -> bool:
            classifier_calls.append((content, active_session["response_policy"]))
            return True

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
            [("hey agent, can you check the failing parser test?", "mentions_only")],
        )
        self.assertEqual(run_store.starts, [208])
        self.assertEqual(seen_tasks, ["hey agent, can you check the failing parser test?"])
        self.assertEqual(len(sent_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

    def test_embedded_bot_handler_silences_router_rejected_followup(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
            ],
            response_policy="mentions_only",
        )
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def addressing_intent_classifier(
            *,
            content: str,
            active_session,
            message,
            bot_handler,
        ) -> bool:
            return False

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

        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=FakeRunStore(),
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
            message,
            bot_handler,
        ) -> bool:
            return True

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

    def test_embedded_bot_handler_replies_to_control_messages(self) -> None:
        steering_replies = self.run_embedded_bot_handler(
            "@**Pear** Steering update:\n\nRequest: keep the fix minimal",
        )
        pause_replies = self.run_embedded_bot_handler(
            "@**Pear** Pause session:\n\nReason: waiting for CI",
        )
        resume_replies = self.run_embedded_bot_handler(
            "@**Pear** Resume session:\n\nRequest: continue with rollout",
        )

        self.assertEqual(steering_replies[0], "Steering queued.")
        self.assertNotIn("Confirm repo, branch, and target runtime", steering_replies[0])
        self.assertIn("Paused.", pause_replies[0])
        self.assertIn("Reason: waiting for CI", pause_replies[0])
        self.assertNotIn("Confirm repo, branch, and target runtime", pause_replies[0])
        self.assertIn("Resuming.", resume_replies[0])
        self.assertIn("Request: continue with rollout", resume_replies[0])
        self.assertNotIn("Confirm repo, branch, and target runtime", resume_replies[0])
        self.assertEqual(len(steering_replies), 1)
        self.assertEqual(len(pause_replies), 1)
        self.assertEqual(len(resume_replies), 2)
        self.assertEqual(resume_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

    def test_embedded_bot_handler_replies_to_access_request_without_worker(self) -> None:
        sent_replies = self.run_embedded_bot_handler(
            "@**Pear** What can you access from this topic?",
        )

        self.assertEqual(len(sent_replies), 1)
        self.assertIn("I can work from this Zulip topic", sent_replies[0])
        self.assertIn("durable topic context", sent_replies[0])
        self.assertIn("configured Codex runtime", sent_replies[0])
        self.assertIn("zulip-terminal can act on posted artifacts", sent_replies[0])
        self.assertIn("Worker state:", sent_replies[0])
        self.assertNotIn("Status: working...", sent_replies[0])

    def test_embedded_bot_worker_uses_loaded_topic_history(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "@Pear Pause session:\n\nReason: waiting for CI", False),
            ],
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
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(updated_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(
            updated_replies[1],
            "Pear worker completed without a visible response.",
        )
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)
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
        self.assertEqual(updated_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(
            updated_replies[1],
            "Pear worker completed without a visible response.",
        )

    def test_embedded_bot_settles_ack_when_worker_start_claim_fails(self) -> None:
        sent_replies: list[str] = []
        updated_replies: list[str] = []

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
                content = message.get("content")
                assert isinstance(content, str)
                updated_replies.append(content)

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

        self.assertEqual(sent_replies, [PEAR_ACTIVE_THINKING_MARKDOWN])
        self.assertEqual(
            updated_replies,
            [
                "Pear worker is not starting.\n\n"
                "Reason: This request was already handled or is no longer claimable."
            ],
        )

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
        self.assertEqual(queue_name, PEAR_WORKER_QUEUE_NAME)
        self.assertEqual(event["user_profile_id"], 17)
        self.assertEqual(event["message_id"], 205)
        self.assertEqual(event["content"], "fix the checkout test")
        self.assertEqual(event["message"], {**message, "content": "fix the checkout test"})
        self.assertEqual(message["content"], "fix the checkout test")
        self.assertEqual(event["progress_message_id"], 610)

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
        self.assertEqual(updated_messages[0]["content"], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(tool_seen_statuses, [updated_messages[0]["content"]])
        self.assertEqual(updated_messages[1]["message_id"], 501)
        self.assertNotIn("rendering_only", updated_messages[1])
        self.assertEqual(
            updated_messages[1]["content"],
            "Pear worker completed without a visible response.",
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

        self.assertEqual(updated_messages[0]["content"], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertIs(updated_messages[0]["rendering_only"], True)
        self.assertEqual(
            updated_messages[-1]["content"],
            "Pear worker completed without a visible response.",
        )
        self.assertNotIn("rendering_only", updated_messages[-1])
        self.assertIn(("result_updated", "Settled worker progress message."), recorded_events)
        live_state = get_pear_live_run_state(trigger_message_id)
        self.assertIsNotNone(live_state)
        assert live_state is not None
        self.assertEqual(live_state["status"], "done")
        self.assertEqual(
            live_state["content"],
            "Pear worker completed without a visible response.",
        )

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
        self.assertNotIn("[earlier progress details truncated]", all_persisted_text)
        self.assertNotIn("[truncated]", all_persisted_text)
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
        self.assertNotIn("[earlier progress details truncated]", all_persisted_text)
        self.assertNotIn("[truncated]", all_persisted_text)
        self.assertTrue(all(len(reply) <= 5000 for reply in sent_replies))

    def test_run_worker_turn_keeps_streaming_deltas_transient(self) -> None:
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
                    "body": "A",
                    "item_id": "assistant-1",
                    "metadata": {"streaming": True},
                }
            )
            event_callback(
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": "AB",
                    "item_id": "assistant-1",
                    "metadata": {"streaming": True},
                }
            )
            return [{"label": "Result", "value": "AB"}]

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
                PEAR_ACTIVE_THINKING_MARKDOWN,
                f"A {PEAR_ACTIVE_MARKDOWN_PREFIX}",
                "AB",
            ],
        )
        self.assertEqual(
            [message.get("rendering_only") for message in updated_messages],
            [True, True, None],
        )
        self.assertFalse(
            any(
                event_type == "transcript" and summary == "Codex"
                for event_type, summary in recorded_events
            )
        )
        live_state = get_pear_live_run_state(trigger_message_id)
        self.assertIsNotNone(live_state)
        assert live_state is not None
        self.assertEqual(live_state["status"], "done")
        self.assertEqual(live_state["content"], "AB")

    def test_run_worker_turn_marks_live_message_active_and_settles_clean(self) -> None:
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
            user_profile = object()

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
        # Every in-flight edit carries the active marker so the web client
        # can animate the ✦ liveness glyph on the streaming message; the
        # final settled content must shed it.
        self.assertTrue(
            all(PEAR_ACTIVE_MARKDOWN_PREFIX in content for content in updated_contents[:-1])
        )
        self.assertNotIn(PEAR_ACTIVE_MARKDOWN_PREFIX, updated_contents[-1])

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

        def active_session_loader(message, bot_handler):
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

        def tool_runner(
            active_session, task: str, *, event_callback=None, **kwargs
        ) -> list[dict[str, str]]:
            tool_seen_tasks.append(task)
            tool_seen_threads.append(active_session["latest_codex_thread_id"])
            if len(tool_seen_tasks) == 1:
                return [
                    {"label": "Session", "value": "Codex thread thread-1"},
                    {"label": "Result", "value": "first result"},
                    {
                        "label": "Artifact",
                        "value": '{"blocks":[{"body":"first visual","type":"copy"}],"version":1}',
                    },
                ]
            return [{"label": "Result", "value": "steered result"}]

        def attach_artifact(bot_handler, *, message_id, tool_results) -> bool:
            del bot_handler, message_id
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

        with patch(
            "zerver.lib.pear.attach_pear_artifact_to_message", side_effect=attach_artifact
        ):
            PearEmbeddedBotHandler(
                worker_runtime=PearWorkerRuntime(tool_runner),
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
        self.assertTrue(
            any(
                PEAR_STEERING_APPLIED_MARKDOWN in str(message["content"])
                for message in updated_messages
            )
        )
        self.assertNotIn(PEAR_STEERING_APPLIED_MARKDOWN, str(updated_messages[-1]["content"]))
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
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)

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

        def tool_runner(active_session, task: str, *, event_callback=None, **kwargs):
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


class PearInteractiveTurnTest(TestCase):
    """Plan-mode turns and the request_user_input answer path."""

    def test_plan_prefix_selects_plan_collaboration_mode(self) -> None:
        self.assertEqual(
            pear_turn_collaboration_mode("@**Pear** plan: design the importer"), "plan"
        )
        self.assertEqual(pear_turn_collaboration_mode("plan: sketch the API"), "plan")
        self.assertIsNone(pear_turn_collaboration_mode("@**Pear** make a plan for me"))
        self.assertIsNone(pear_turn_collaboration_mode("planting trees"))
        self.assertIsNone(pear_turn_collaboration_mode(""))

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

    def test_turn_params_carry_collaboration_mode(self) -> None:
        runner = self.make_runner()
        params = runner.turn_params("thread_1", "prompt", collaboration_mode="plan")
        self.assertEqual(params["collaborationMode"], {"mode": "plan"})
        default_params = runner.turn_params("thread_1", "prompt")
        self.assertNotIn("collaborationMode", default_params)

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
