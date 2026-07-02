import json
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from zerver.lib.pear import (
    PEAR_ACCESS_PROMPT,
    PEAR_ACTIVE_MARKDOWN_PREFIX,
    PEAR_ACTIVE_SESSION_ENDPOINT,
    PEAR_ACTIVE_THINKING_MARKDOWN,
    PEAR_AGENT_MENTION,
    PEAR_AGENT_RUNTIME_PACKAGES,
    PEAR_AGENT_SKILL_DEFINITIONS,
    PEAR_AGENT_SKILLS,
    PEAR_APPLY_PLAN_PROMPT,
    PEAR_BOT_SHORT_NAME,
    PEAR_CATCH_UP_PROMPT,
    PEAR_CHECKPOINT_PROMPT,
    PEAR_CODEX_MCP_TOOLS,
    PEAR_CONFIG_ENDPOINT,
    PEAR_DEFAULT_STREAM,
    PEAR_DEFAULT_TOPIC,
    PEAR_DISABLE_ROUTINE_PROMPT,
    PEAR_DONE_PROMPT,
    PEAR_DRAFT_ARTIFACT_PROMPT,
    PEAR_EMBEDDED_BOT_SERVICE,
    PEAR_EXPLICIT_MENTION_MESSAGE_KEY,
    PEAR_FIND_ANSWER_PROMPT,
    PEAR_FIX_BUG_PROMPT,
    PEAR_FOLLOWUP_TRIGGER,
    PEAR_HANDOFF_PROMPT,
    PEAR_MEMORY_FORGET_PROMPT,
    PEAR_MEMORY_PROMPT,
    PEAR_MEMORY_STATUS_PROMPT,
    PEAR_OFFICIAL_AGENT_RUNTIME,
    PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
    PEAR_OFFICIAL_CODEX_SDK_PACKAGES,
    PEAR_PAUSE_PROMPT,
    PEAR_PLAN_PROMPT,
    PEAR_RESPONSE_POLICY_PROMPT,
    PEAR_RESUME_PROMPT,
    PEAR_REVIEW_PROMPT,
    PEAR_ROUTINE_CADENCE_PROMPT,
    PEAR_ROUTINE_STATUS_PROMPT,
    PEAR_RUN_ROUTINE_PROMPT,
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
    PEAR_UPDATE_ROUTINE_PROMPT,
    PEAR_VERIFY_CHANGES_PROMPT,
    PEAR_WORKER_RUNS_STORAGE_KEY,
    PearEmbeddedBotHandler,
    PearWorkerRuntime,
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
    build_pear_disable_routine_ack_message,
    build_pear_disable_routine_message,
    build_pear_done_ack_message,
    build_pear_done_message,
    build_pear_draft_artifact_message,
    build_pear_find_answer_message,
    build_pear_fix_bug_message,
    build_pear_handoff_message,
    build_pear_memory_ack_message,
    build_pear_memory_forget_ack_message,
    build_pear_memory_forget_message,
    build_pear_memory_message,
    build_pear_memory_status_ack_message,
    build_pear_memory_status_message,
    build_pear_pause_ack_message,
    build_pear_pause_message,
    build_pear_plan_message,
    build_pear_response_policy_ack_message,
    build_pear_response_policy_message,
    build_pear_resume_ack_message,
    build_pear_resume_message,
    build_pear_review_message,
    build_pear_routine_cadence_ack_message,
    build_pear_routine_cadence_message,
    build_pear_routine_status_ack_message,
    build_pear_routine_status_message,
    build_pear_run_routine_ack_message,
    build_pear_save_work_message,
    build_pear_seed_message,
    build_pear_status_check_message,
    build_pear_steer_message,
    build_pear_steering_ack_message,
    build_pear_triage_message,
    build_pear_update_routine_ack_message,
    build_pear_update_routine_message,
    build_pear_verify_changes_message,
    build_pear_worker_completed_message,
    build_pear_worker_completed_message_chunks,
    build_pear_worker_progress_message,
    get_pear_live_run_state,
    get_pear_session_defaults,
    get_pear_session_payload,
    is_pear_access_request_message,
    is_pear_disable_routine_request_message,
    is_pear_done_template_message,
    is_pear_memory_forget_request_message,
    is_pear_memory_status_request_message,
    is_pear_memory_template_message,
    is_pear_request_message,
    is_pear_response_policy_request_message,
    is_pear_response_policy_template_message,
    is_pear_routine_cadence_template_message,
    is_pear_routine_status_request_message,
    is_pear_run_routine_request_message,
    is_pear_thread_request_message,
    is_pear_update_routine_request_message,
    load_pear_worker_run_records,
    normalize_pear_response_policy,
    parse_pear_artifacts,
    parse_pear_checklist,
    parse_pear_disable_routine_instruction,
    parse_pear_done_instruction,
    parse_pear_interruption_event,
    parse_pear_memory_forget_instruction,
    parse_pear_memory_instruction,
    parse_pear_response_policy_instruction,
    parse_pear_routine_cadence_instruction,
    parse_pear_steering_event,
    parse_pear_update_routine_instruction,
    pear_normalize_token_usage,
    pear_memory_request_value,
    pear_response_policy_request_value,
    pear_session_record_kind,
    pear_session_record_url,
    pear_worker_already_started,
    pear_worker_run_for_message,
    pear_worker_should_start,
    pear_worker_task_for_message,
    record_pear_worker_run,
    split_pear_persistent_message,
)
from zerver.lib.pear_agent import (
    PearAgentRuntimeError,
    PearCodexAgentsMcpToolRunner,
    PearCodexAppServerToolRunner,
    PearCodexExecAddressingIntentClassifier,
    PearCodexExecToolRunner,
    PearCodexSdkToolRunner,
    PearDisabledAgentRunner,
    PearGeminiInteractionsAddressingIntentClassifier,
    PearProcessIsolation,
    build_pear_addressing_router_prompt,
    build_pear_codex_prompt,
    build_pear_topic_state_agent_tools,
    ensure_pear_runtime_result,
    merge_pear_tool_results,
    parse_pear_addressing_router_output,
    pear_agents_result_used_codex_mcp_tool,
    pear_codex_thread_id_from_active_session,
    pear_codex_thread_id_from_agents_objects,
    pear_tool_results_from_codex_jsonl,
    pear_tool_results_from_text,
    pear_topic_state_tool_name,
)
from zerver.lib.pear_worker import PEAR_WORKER_QUEUE_NAME, PearQueueWorkerDispatcher


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
        self.assertEqual(PEAR_MEMORY_PROMPT, "Remember:")
        self.assertEqual(
            PEAR_MEMORY_STATUS_PROMPT,
            "What do you remember about this topic?",
        )
        self.assertEqual(PEAR_MEMORY_FORGET_PROMPT, "Forget memory:")
        self.assertEqual(PEAR_DONE_PROMPT, "Definition of done:")
        self.assertEqual(PEAR_RESPONSE_POLICY_PROMPT, "Response policy:")
        self.assertEqual(PEAR_ROUTINE_CADENCE_PROMPT, "Routine cadence:")
        self.assertEqual(
            PEAR_ROUTINE_STATUS_PROMPT,
            "What routines do you have set up in this topic?",
        )
        self.assertEqual(PEAR_RUN_ROUTINE_PROMPT, "Run routine:")
        self.assertEqual(PEAR_DISABLE_ROUTINE_PROMPT, "Disable routine:")
        self.assertEqual(PEAR_UPDATE_ROUTINE_PROMPT, "Update routine:")
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
        self.assertIn("Diff or files to inspect: use the latest Pear workspace artifacts", review_message)
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

    def test_build_memory_message(self) -> None:
        memory_message = build_pear_memory_message("Prefer small focused PRs")

        self.assertEqual(
            memory_message,
            "@**Pear** Remember:\n\nMemory: Prefer small focused PRs",
        )
        self.assertEqual(build_pear_memory_message(), "@**Pear** Remember:\n\nMemory: ")

    def test_build_memory_status_message(self) -> None:
        self.assertEqual(
            build_pear_memory_status_message(),
            "@**Pear** What do you remember about this topic?",
        )

    def test_build_memory_forget_message(self) -> None:
        self.assertEqual(
            build_pear_memory_forget_message("Prefer small focused PRs"),
            "@**Pear** Forget memory:\n\nMemory forgotten: Prefer small focused PRs",
        )
        self.assertEqual(
            build_pear_memory_forget_message(),
            "@**Pear** Forget memory:\n\nMemory forgotten: ",
        )

    def test_build_done_message(self) -> None:
        self.assertEqual(
            build_pear_done_message("All tests and a draft PR are posted"),
            "@**Pear** Definition of done:\n\nDone: All tests and a draft PR are posted",
        )
        self.assertEqual(build_pear_done_message(), "@**Pear** Definition of done:\n\nDone: ")

    def test_build_response_policy_message(self) -> None:
        self.assertEqual(
            build_pear_response_policy_message(),
            "@**Pear** Response policy:\n\nAmbient: mentions only",
        )
        self.assertEqual(
            build_pear_response_policy_message("on"),
            "@**Pear** Response policy:\n\nAmbient: on",
        )

    def test_build_routine_cadence_message(self) -> None:
        self.assertEqual(
            build_pear_routine_cadence_message(),
            "@**Pear** Routine cadence:\n\nRoutine cadence: manual",
        )
        self.assertEqual(
            build_pear_routine_cadence_message("every 30 minutes"),
            "@**Pear** Routine cadence:\n\nRoutine cadence: every 30 minutes",
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
                (10, "@Pear fix the checkout test", False),
                (11, "Verification: pytest zerver/tests/test_pear.py", True),
                (
                    12,
                    "Routine: Watch PR status for https://github.com/example/pear/pull/7",
                    True,
                ),
                (13, "Memory: Prefer small focused PRs", False),
                (14, "Done: Tests pass and draft PR is posted", False),
                (15, "Ambient: mentions only", False),
                (16, "Routine cadence: manual", False),
            ],
        )

        access_ack = build_pear_access_ack_message(active_session)

        self.assertIn("frontend > checkout/regression", access_ack)
        self.assertIn("durable topic context", access_ack)
        self.assertIn("configured Codex runtime", access_ack)
        self.assertIn("zulip-terminal can act on posted artifacts", access_ack)
        self.assertIn("Worker state: ready", access_ack)
        self.assertIn("Artifacts seen: 6", access_ack)
        self.assertIn("Routines seen: 1", access_ack)
        self.assertIn("Routine cadence records seen: 1", access_ack)
        self.assertIn("Latest routine cadence: manual", access_ack)
        self.assertIn("Memories seen: 1", access_ack)
        self.assertIn("Latest memory: Prefer small focused PRs", access_ack)
        self.assertIn("Done criteria seen: 1", access_ack)
        self.assertIn("Latest done criteria: Tests pass and draft PR is posted", access_ack)
        self.assertIn("Response policy: mentions_only", access_ack)
        self.assertIn(
            "Latest routine: Watch PR status for https://github.com/example/pear/pull/7",
            access_ack,
        )
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
        memory_ack = build_pear_memory_ack_message("Prefer small focused PRs")
        memory_forget_ack = build_pear_memory_forget_ack_message(
            "Prefer small focused PRs"
        )
        done_ack = build_pear_done_ack_message("Tests pass and draft PR is posted")
        response_policy_ack = build_pear_response_policy_ack_message("mentions only")
        routine_cadence_ack = build_pear_routine_cadence_ack_message("manual")

        self.assertEqual(steering_ack, "Steering queued.")
        self.assertIn("Paused.", pause_ack)
        self.assertIn("Reason: waiting for CI", pause_ack)
        self.assertIn("Resuming.", resume_ack)
        self.assertIn("Request: continue with rollout", resume_ack)
        self.assertIn("Saved.", memory_ack)
        self.assertIn("Memory: Prefer small focused PRs", memory_ack)
        self.assertIn("forgotten", memory_forget_ack)
        self.assertIn("Memory forgotten: Prefer small focused PRs", memory_forget_ack)
        self.assertIn("definition of done", done_ack)
        self.assertIn("Done: Tests pass and draft PR is posted", done_ack)
        self.assertIn("only start follow-up worker turns", response_policy_ack)
        self.assertIn("Ambient: mentions only", response_policy_ack)
        self.assertIn("routine cadence", routine_cadence_ack)
        self.assertIn("Routine cadence: manual", routine_cadence_ack)

    def test_build_run_routine_ack_message(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "Routine cadence: manual", False),
            ],
        )

        ack = build_pear_run_routine_ack_message(active_session)

        self.assertIn("Running the latest Pear routine", ack)
        self.assertIn("Routine: Watch PR status for pear/runtime", ack)
        self.assertIn("Routine cadence: manual", ack)
        self.assertIn("topic context is unavailable", build_pear_run_routine_ack_message())

    def test_build_routine_status_ack_message(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "Routine: Refresh mergeability after review", False),
                (14, "Routine cadence: every 30 minutes", False),
            ],
        )

        ack = build_pear_routine_status_ack_message(active_session)

        self.assertIn("Pear routines recorded", ack)
        self.assertIn("- Watch PR status for pear/runtime", ack)
        self.assertIn("- Refresh mergeability after review", ack)
        self.assertIn("Latest routine cadence: every 30 minutes", ack)
        self.assertIn(
            "topic context is unavailable",
            build_pear_routine_status_ack_message(),
        )
        self.assertIn(
            "do not see any Pear routines",
            build_pear_routine_status_ack_message(
                build_pear_active_session_summary_from_messages(
                    pear_session=payload,
                    source="server",
                    stream_exists=True,
                    messages=[],
                )
            ),
        )

    def test_build_disable_routine_ack_message(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
            ],
        )

        ack = build_pear_disable_routine_ack_message("", active_session)

        self.assertIn("Routine disabled: Watch PR status for pear/runtime", ack)
        self.assertIn(
            "Routine disabled: Refresh mergeability",
            build_pear_disable_routine_ack_message("Refresh mergeability"),
        )
        self.assertIn(
            "do not know which Pear routine to disable",
            build_pear_disable_routine_ack_message(),
        )

    def test_build_update_routine_message(self) -> None:
        self.assertEqual(
            build_pear_update_routine_message(
                "Watch PR status for pear/runtime",
                "Watch PR status for pear/runtime every weekday",
            ),
            "@**Pear** Update routine:\n\n"
            "Routine disabled: Watch PR status for pear/runtime\n"
            "Routine: Watch PR status for pear/runtime every weekday",
        )
        self.assertEqual(
            build_pear_update_routine_message(),
            "@**Pear** Update routine:\n\nRoutine disabled: \nRoutine: ",
        )

    def test_build_update_routine_ack_message(self) -> None:
        ack = build_pear_update_routine_ack_message(
            "Watch PR status for pear/runtime",
            "Watch PR status for pear/runtime every weekday",
        )

        self.assertIn("Updated.", ack)
        self.assertIn("Routine disabled: Watch PR status for pear/runtime", ack)
        self.assertIn(
            "Routine: Watch PR status for pear/runtime every weekday",
            ack,
        )
        self.assertIn(
            "I need the updated Pear routine",
            build_pear_update_routine_ack_message("Watch PR status for pear/runtime"),
        )

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
        self.assertEqual(payload["memory_message"], build_pear_memory_message())
        self.assertEqual(
            payload["memory_status_message"],
            build_pear_memory_status_message(),
        )
        self.assertEqual(
            payload["memory_forget_message"],
            build_pear_memory_forget_message(),
        )
        self.assertEqual(payload["done_message"], build_pear_done_message())
        self.assertEqual(
            payload["response_policy_message"],
            build_pear_response_policy_message(),
        )
        self.assertEqual(
            payload["routine_cadence_message"],
            build_pear_routine_cadence_message(),
        )
        self.assertEqual(
            payload["routine_status_message"],
            build_pear_routine_status_message(),
        )
        self.assertEqual(
            payload["disable_routine_message"],
            build_pear_disable_routine_message(),
        )
        self.assertEqual(
            payload["update_routine_message"],
            build_pear_update_routine_message(),
        )
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
        self.assertEqual(payload["pear_agent_skills"], list(PEAR_AGENT_SKILLS))
        self.assertEqual(
            payload["pear_agent_skill_definitions"],
            [
                {
                    "name": skill["name"],
                    "description": skill["description"],
                    "applies_to": list(skill["applies_to"]),
                    "instructions": list(skill["instructions"]),
                    "artifact_tools": list(skill["artifact_tools"]),
                }
                for skill in PEAR_AGENT_SKILL_DEFINITIONS
            ],
        )
        self.assertEqual(
            payload["server_contract_version"],
            PEAR_SERVER_CONTRACT_VERSION,
        )
        self.assertEqual(PEAR_SESSION_ENDPOINT, "/pear/session")
        self.assertEqual(PEAR_ACTIVE_SESSION_ENDPOINT, "/pear/session/active")
        self.assertEqual(PEAR_CONFIG_ENDPOINT, "/pear/config")
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
            worker_agent_runtime="codex-agents-mcp",
            worker_runtime_available=True,
            worker_runtime_detail="codex mcp-server via /usr/local/bin/codex; openai-agents installed.",
            worker_runtime_auth_available=True,
            worker_runtime_auth_detail="OpenAI Agents SDK credentials present; Codex CLI is logged in.",
            worker_runtime_login_command="codex login",
        )

        self.assertTrue(config["configured"])
        self.assertTrue(config["enabled"])
        self.assertEqual(config["worker_agent_runtime"], "codex-agents-mcp")
        self.assertTrue(config["worker_runtime_ready"])
        self.assertTrue(config["worker_runtime_auth_ready"])
        self.assertEqual(
            config["worker_runtime_login_method"], "codex-cli-and-openai-api-key"
        )
        self.assertEqual(config["worker_runtime_login_command"], "codex login")
        self.assertEqual(config["worker_runtime_login_target"], "worker-environment")
        self.assertIn(
            "OPENAI_API_KEY",
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
            "codex mcp-server via /usr/local/bin/codex; openai-agents installed.",
        )
        self.assertEqual(
            config["setup_steps"][3]["detail"],
            "OpenAI Agents SDK credentials present; Codex CLI is logged in.",
        )
        self.assertTrue(all(step["complete"] for step in config["setup_steps"]))

    def test_build_config_payload_requires_runtime_dependency(self) -> None:
        config = build_pear_config_payload(
            embedded_bots_enabled=True,
            bot_count=1,
            bot_user_id=7,
            bot_full_name="Pear",
            bot_email="pear-bot@example.com",
            worker_agent_runtime="codex-agents-mcp",
            worker_runtime_available=False,
            worker_runtime_detail="Missing openai-agents package.",
        )

        self.assertFalse(config["worker_runtime_ready"])
        self.assertTrue(config["worker_runtime_auth_ready"])
        self.assertEqual(config["setup_steps"][2]["label"], "Worker runtime dependencies")
        self.assertFalse(config["setup_steps"][2]["complete"])
        self.assertEqual(
            config["setup_steps"][2]["detail"],
            "Missing openai-agents package.",
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
                    "kind": "routine",
                    "label": "Routine",
                    "value": "Watch PR status for https://github.com/example/pear/pull/7",
                    "message_id": 81,
                },
                {
                    "kind": "memory",
                    "label": "Memory",
                    "value": "Prefer small focused PRs",
                    "message_id": 81,
                },
                {
                    "kind": "done_criteria",
                    "label": "Done",
                    "value": "Tests pass and draft PR is posted",
                    "message_id": 81,
                },
                {
                    "kind": "response_policy",
                    "label": "Ambient",
                    "value": "mentions only",
                    "message_id": 81,
                },
                {
                    "kind": "routine_cadence",
                    "label": "Routine cadence",
                    "value": "manual",
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
        self.assertFalse(is_pear_request_message("@**Other** fix the checkout test"))

    def test_is_pear_thread_request_message(self) -> None:
        self.assertTrue(is_pear_thread_request_message("@Pear debug the flaky parser test"))
        self.assertTrue(is_pear_thread_request_message("@Pear implement the login view"))
        self.assertTrue(is_pear_thread_request_message("@Pear start a new pairing session"))
        self.assertTrue(
            is_pear_thread_request_message(
                "@Pear every weekday at 9am, check the linked PRs"
            )
        )
        self.assertTrue(is_pear_thread_request_message("@Pear hi"))
        self.assertTrue(is_pear_thread_request_message("@Pear Hello."))
        self.assertTrue(is_pear_thread_request_message("@Pear What's up!"))
        self.assertTrue(is_pear_thread_request_message("@Pear hey tell me something"))
        self.assertTrue(is_pear_thread_request_message("@Pear tell me a story"))
        self.assertTrue(is_pear_thread_request_message("@Pear find answer to question"))
        self.assertFalse(is_pear_thread_request_message("@Pear What can you access?"))
        self.assertFalse(is_pear_thread_request_message("debug the flaky parser test"))

    def test_is_pear_run_routine_request_message(self) -> None:
        self.assertTrue(is_pear_run_routine_request_message("@Pear run routine"))
        self.assertTrue(
            is_pear_run_routine_request_message("@**Pear** Run routine:")
        )
        self.assertTrue(is_pear_run_routine_request_message("@Pear check routine"))
        self.assertFalse(
            is_pear_run_routine_request_message(
                "@Pear Routine cadence:\n\nRoutine cadence: manual"
            )
        )
        self.assertFalse(is_pear_run_routine_request_message("run routine"))

    def test_is_pear_routine_status_request_message(self) -> None:
        self.assertTrue(
            is_pear_routine_status_request_message(
                "@Pear what routines do you have set up in this topic?"
            )
        )
        self.assertTrue(is_pear_routine_status_request_message("@Pear list routines"))
        self.assertTrue(
            is_pear_routine_status_request_message("@**Pear** show routines")
        )
        self.assertFalse(is_pear_routine_status_request_message("list routines"))
        self.assertFalse(is_pear_routine_status_request_message("@Pear run routine"))

    def test_is_pear_disable_routine_request_message(self) -> None:
        self.assertTrue(
            is_pear_disable_routine_request_message("@Pear Disable routine:")
        )
        self.assertTrue(
            is_pear_disable_routine_request_message("@Pear disable latest routine")
        )
        self.assertTrue(is_pear_disable_routine_request_message("@Pear stop routine"))
        self.assertTrue(
            is_pear_disable_routine_request_message("@Pear turn off the latest routine")
        )
        self.assertFalse(is_pear_disable_routine_request_message("disable routine"))
        self.assertFalse(is_pear_disable_routine_request_message("@Pear run routine"))

    def test_is_pear_update_routine_request_message(self) -> None:
        self.assertTrue(
            is_pear_update_routine_request_message("@Pear Update routine:")
        )
        self.assertTrue(is_pear_update_routine_request_message("Update routine:"))
        self.assertTrue(
            is_pear_update_routine_request_message(
                "@**Pear** Update routine:\n\nRoutine disabled: old\nRoutine: new"
            )
        )
        self.assertFalse(is_pear_update_routine_request_message("@Pear change routine"))
        self.assertFalse(is_pear_update_routine_request_message("update routine"))
        self.assertFalse(is_pear_update_routine_request_message("@Pear run routine"))

    def test_parse_pear_disable_routine_instruction(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
            ],
        )

        self.assertEqual(
            parse_pear_disable_routine_instruction(
                "@Pear Disable routine:\n\nRoutine disabled: Refresh mergeability",
                active_session,
            ),
            "Refresh mergeability",
        )
        self.assertEqual(
            parse_pear_disable_routine_instruction(
                "@Pear disable latest routine",
                active_session,
            ),
            "Watch PR status for pear/runtime",
        )

    def test_parse_pear_update_routine_instruction(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Routine: Watch PR status for pear/runtime", False),
            ],
        )

        self.assertEqual(
            parse_pear_update_routine_instruction(
                "@Pear Update routine:\n\n"
                "Routine disabled: Watch PR status for pear/runtime\n"
                "Routine: Watch PR status for pear/runtime every weekday",
                active_session,
            ),
            (
                "Watch PR status for pear/runtime",
                "Watch PR status for pear/runtime every weekday",
            ),
        )
        self.assertEqual(
            parse_pear_update_routine_instruction(
                "@Pear Update routine:\n\nRoutine disabled: \n"
                "Routine: Watch PR status for pear/runtime every weekday",
                active_session,
            ),
            (
                "Watch PR status for pear/runtime",
                "Watch PR status for pear/runtime every weekday",
            ),
        )

    def test_is_pear_access_request_message(self) -> None:
        self.assertTrue(
            is_pear_access_request_message(
                "@**Pear** What can you access from this topic?"
            )
        )
        self.assertTrue(is_pear_access_request_message("@Pear what can you reach?"))
        self.assertFalse(is_pear_access_request_message("@Pear fix the checkout test"))

    def test_pear_memory_template_message(self) -> None:
        content = "@**Pear** Remember:\n\nMemory: Prefer small focused PRs"

        self.assertTrue(is_pear_memory_template_message(content))
        self.assertEqual(
            parse_pear_memory_instruction(content),
            "Prefer small focused PRs",
        )
        self.assertEqual(
            parse_pear_memory_instruction("@Pear Remember: prefer pytest first"),
            "prefer pytest first",
        )
        self.assertTrue(
            is_pear_memory_template_message(
                "@Pear remember for this channel: only respond when someone @-mentions you directly."
            )
        )
        self.assertEqual(
            pear_memory_request_value(
                "@Pear remember for this channel: only respond when someone @-mentions you directly."
            ),
            "only respond when someone @-mentions you directly.",
        )
        self.assertEqual(
            pear_memory_request_value(
                '<span class="user-mention">@Pear</span> remember for this channel: only respond when someone @-mentions you directly.'
            ),
            "only respond when someone @-mentions you directly.",
        )
        self.assertEqual(
            pear_memory_request_value(
                "remember for this channel: only respond when someone @-mentions you directly."
            ),
            "only respond when someone @-mentions you directly.",
        )
        self.assertEqual(
            parse_pear_memory_instruction(
                "@Pear remember for this topic that prefer terminal smoke before handoff"
            ),
            "prefer terminal smoke before handoff",
        )
        self.assertFalse(is_pear_request_message("Memory: Prefer small focused PRs"))

    def test_pear_memory_status_request_message(self) -> None:
        self.assertTrue(
            is_pear_memory_status_request_message(
                "@**Pear** What do you remember about this topic?"
            )
        )
        self.assertTrue(is_pear_memory_status_request_message("@Pear list Pear memory"))
        self.assertFalse(
            is_pear_memory_status_request_message("@Pear Remember: prefer pytest first")
        )

    def test_pear_memory_forget_request_message(self) -> None:
        self.assertTrue(is_pear_memory_forget_request_message("@Pear Forget memory:"))
        self.assertTrue(
            is_pear_memory_forget_request_message("@Pear forget latest memory")
        )
        self.assertTrue(
            is_pear_memory_forget_request_message("@Pear remove the latest memory")
        )
        self.assertFalse(is_pear_memory_forget_request_message("forget memory"))
        self.assertFalse(is_pear_memory_forget_request_message("@Pear Remember: foo"))

    def test_parse_pear_memory_forget_instruction(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(11, "Memory: Prefer small focused PRs", False)],
        )

        self.assertEqual(
            parse_pear_memory_forget_instruction(
                "@Pear Forget memory:\n\nMemory forgotten: Prefer small focused PRs",
                active_session,
            ),
            "Prefer small focused PRs",
        )
        self.assertEqual(
            parse_pear_memory_forget_instruction(
                "@Pear forget latest memory",
                active_session,
            ),
            "Prefer small focused PRs",
        )

    def test_pear_done_template_message(self) -> None:
        content = "@**Pear** Definition of done:\n\nDone: Tests pass and draft PR is posted"

        self.assertTrue(is_pear_done_template_message(content))
        self.assertEqual(
            parse_pear_done_instruction(content),
            "Tests pass and draft PR is posted",
        )
        self.assertEqual(
            parse_pear_done_instruction("@Pear Definition of done: PR posted"),
            "PR posted",
        )

    def test_pear_response_policy_template_message(self) -> None:
        content = "@**Pear** Response policy:\n\nAmbient: mentions only"

        self.assertTrue(is_pear_response_policy_template_message(content))
        self.assertTrue(
            is_pear_response_policy_request_message(
                "@Pear only respond when I @-mention you"
            )
        )
        self.assertTrue(
            is_pear_response_policy_request_message(
                "@Pear stay quiet in this topic unless tagged"
            )
        )
        self.assertTrue(
            is_pear_response_policy_request_message(
                "@Pear turn follow-ups back on"
            )
        )
        self.assertEqual(parse_pear_response_policy_instruction(content), "mentions only")
        self.assertEqual(
            parse_pear_response_policy_instruction("@Pear Response policy: off"),
            "off",
        )
        self.assertEqual(
            parse_pear_response_policy_instruction(
                "@Pear only respond when I @-mention you"
            ),
            "mentions only",
        )
        self.assertEqual(
            parse_pear_response_policy_instruction("@Pear turn follow-ups back on"),
            "on",
        )
        self.assertEqual(
            pear_response_policy_request_value("@Pear debug the flaky parser test"),
            None,
        )
        self.assertEqual(
            pear_response_policy_request_value(
                "@Pear remember for this channel: only respond when someone @-mentions you directly."
            ),
            None,
        )
        self.assertEqual(normalize_pear_response_policy("mentions only"), "mentions_only")
        self.assertEqual(normalize_pear_response_policy("off"), "mentions_only")
        self.assertEqual(normalize_pear_response_policy("on"), "ambient")
        self.assertEqual(normalize_pear_response_policy("ambient"), "ambient")

    def test_pear_routine_cadence_template_message(self) -> None:
        content = "@**Pear** Routine cadence:\n\nRoutine cadence: manual"

        self.assertTrue(is_pear_routine_cadence_template_message(content))
        self.assertEqual(parse_pear_routine_cadence_instruction(content), "manual")
        self.assertEqual(
            parse_pear_routine_cadence_instruction(
                "@Pear Routine cadence: every 30 minutes"
            ),
            "every 30 minutes",
        )

    def test_build_memory_status_ack_message(self) -> None:
        payload = get_pear_session_payload(stream="frontend", topic="checkout/regression")
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Memory: Prefer small focused PRs", False),
                (12, "Memory: Run terminal tests before server smoke", False),
            ],
        )

        response = build_pear_memory_status_ack_message(active_session)

        self.assertIn("frontend > checkout/regression", response)
        self.assertIn("- Run terminal tests before server smoke", response)
        self.assertIn("- Prefer small focused PRs", response)

    def test_build_memory_status_ack_message_without_memory(self) -> None:
        payload = get_pear_session_payload(stream="frontend", topic="checkout/regression")
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )

        response = build_pear_memory_status_ack_message(active_session)

        self.assertIn("do not have Pear memory recorded", response)
        self.assertIn("Meta M", response)

    def test_build_memory_forget_ack_message(self) -> None:
        payload = get_pear_session_payload(stream="frontend", topic="checkout/regression")
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(11, "Memory: Prefer small focused PRs", False)],
        )

        ack = build_pear_memory_forget_ack_message("", active_session)

        self.assertIn("Memory forgotten: Prefer small focused PRs", ack)
        self.assertIn(
            "Memory forgotten: Run terminal tests first",
            build_pear_memory_forget_ack_message("Run terminal tests first"),
        )
        self.assertIn(
            "which Pear memory to forget",
            build_pear_memory_forget_ack_message(),
        )

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
        self.assertEqual(summary["routine_count"], 0)
        self.assertIsNone(summary["latest_routine_message_id"])
        self.assertIsNone(summary["latest_routine"])
        self.assertEqual(summary["memory_count"], 0)
        self.assertIsNone(summary["latest_memory_message_id"])
        self.assertIsNone(summary["latest_memory"])
        self.assertEqual(summary["memory_forgotten_count"], 0)
        self.assertIsNone(summary["latest_memory_forgotten_message_id"])
        self.assertIsNone(summary["latest_memory_forgotten"])
        self.assertEqual(summary["done_criteria_count"], 0)
        self.assertIsNone(summary["latest_done_criteria_message_id"])
        self.assertIsNone(summary["latest_done_criteria"])
        self.assertEqual(summary["response_policy_count"], 0)
        self.assertIsNone(summary["latest_response_policy_message_id"])
        self.assertIsNone(summary["latest_response_policy"])
        self.assertEqual(summary["response_policy"], "mentions_only")
        self.assertEqual(summary["routine_cadence_count"], 0)
        self.assertIsNone(summary["latest_routine_cadence_message_id"])
        self.assertIsNone(summary["latest_routine_cadence"])
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
                            "Routine: Watch PR status for pear/runtime-artifacts",
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
                (
                    17,
                    "@**Pear** Remember:\n\nMemory: Prefer small focused PRs",
                    False,
                ),
                (
                    18,
                    "@**Pear** Definition of done:\n\nDone: Tests pass and draft PR is posted",
                    False,
                ),
                (
                    19,
                    "@**Pear** Response policy:\n\nAmbient: mentions only",
                    False,
                ),
                (
                    20,
                    "@**Pear** Routine cadence:\n\nRoutine cadence: manual",
                    False,
                ),
            ],
        )

        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["latest_message_id"], 13)
        self.assertEqual(summary["latest_request_message_id"], 10)
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["topic_message_count"], 10)
        self.assertEqual(summary["done_count"], 1)
        self.assertEqual(summary["in_progress_count"], 1)
        self.assertEqual(summary["artifact_count"], 8)
        self.assertEqual(summary["latest_artifact_message_id"], 20)
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
        self.assertEqual(summary["routine_count"], 1)
        self.assertEqual(summary["latest_routine_message_id"], 13)
        self.assertEqual(
            summary["latest_routine"],
            "Watch PR status for pear/runtime-artifacts",
        )
        self.assertEqual(summary["memory_count"], 1)
        self.assertEqual(summary["latest_memory_message_id"], 17)
        self.assertEqual(summary["latest_memory"], "Prefer small focused PRs")
        self.assertEqual(summary["memory_forgotten_count"], 0)
        self.assertIsNone(summary["latest_memory_forgotten_message_id"])
        self.assertIsNone(summary["latest_memory_forgotten"])
        self.assertEqual(summary["done_criteria_count"], 1)
        self.assertEqual(summary["latest_done_criteria_message_id"], 18)
        self.assertEqual(
            summary["latest_done_criteria"],
            "Tests pass and draft PR is posted",
        )
        self.assertEqual(summary["response_policy_count"], 1)
        self.assertEqual(summary["latest_response_policy_message_id"], 19)
        self.assertEqual(summary["latest_response_policy"], "mentions only")
        self.assertEqual(summary["response_policy"], "mentions_only")
        self.assertEqual(summary["routine_cadence_count"], 1)
        self.assertEqual(summary["latest_routine_cadence_message_id"], 20)
        self.assertEqual(summary["latest_routine_cadence"], "manual")
        self.assertEqual(summary["routine_disabled_count"], 0)
        self.assertIsNone(summary["latest_routine_disabled_message_id"])
        self.assertIsNone(summary["latest_routine_disabled"])
        self.assertEqual(summary["artifacts"][0]["kind"], "routine_cadence")
        self.assertEqual(summary["artifacts"][1]["kind"], "response_policy")
        self.assertEqual(summary["artifacts"][2]["kind"], "done_criteria")
        self.assertEqual(summary["artifacts"][3]["kind"], "memory")
        self.assertEqual(summary["artifacts"][4]["kind"], "branch")
        self.assertEqual(summary["artifacts"][5]["kind"], "session_record")
        self.assertEqual(summary["artifacts"][6]["kind"], "routine")
        self.assertEqual(summary["artifacts"][7]["kind"], "verification")
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

    def test_disabled_routine_is_not_latest_active_routine(self) -> None:
        payload = get_pear_session_payload()
        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "Routine disabled: Watch PR status for pear/runtime", False),
            ],
        )

        self.assertEqual(summary["routine_count"], 0)
        self.assertIsNone(summary["latest_routine"])
        self.assertEqual(summary["routine_disabled_count"], 1)
        self.assertEqual(summary["latest_routine_disabled_message_id"], 13)
        self.assertEqual(
            summary["latest_routine_disabled"],
            "Watch PR status for pear/runtime",
        )
        self.assertFalse(pear_worker_should_start("@Pear run routine", summary))

    def test_update_routine_message_replaces_active_routine(self) -> None:
        payload = get_pear_session_payload()
        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (
                    13,
                    "Routine disabled: Watch PR status for pear/runtime\n"
                    "Routine: Watch PR status for pear/runtime every weekday",
                    False,
                ),
            ],
        )

        self.assertEqual(summary["routine_count"], 1)
        self.assertEqual(
            summary["latest_routine"],
            "Watch PR status for pear/runtime every weekday",
        )
        self.assertEqual(summary["routine_disabled_count"], 1)
        self.assertEqual(
            summary["latest_routine_disabled"],
            "Watch PR status for pear/runtime",
        )

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

    def test_memory_note_does_not_start_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (13, "@Pear Remember:\n\nMemory: Prefer small focused PRs", False)
            ],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertEqual(summary["memory_count"], 1)
        self.assertEqual(summary["latest_memory"], "Prefer small focused PRs")
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_forgotten_memory_is_not_latest_active_memory(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (11, "Memory: Prefer small focused PRs", False),
                (12, "Memory forgotten: Prefer small focused PRs", False),
            ],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(summary["artifact_count"], 2)
        self.assertEqual(summary["memory_count"], 0)
        self.assertIsNone(summary["latest_memory"])
        self.assertEqual(summary["memory_forgotten_count"], 1)
        self.assertEqual(summary["latest_memory_forgotten_message_id"], 12)
        self.assertEqual(
            summary["latest_memory_forgotten"],
            "Prefer small focused PRs",
        )

    def test_memory_forget_note_does_not_start_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (
                    13,
                    "@Pear Forget memory:\n\nMemory forgotten: Prefer small focused PRs",
                    False,
                )
            ],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertEqual(summary["memory_forgotten_count"], 1)
        self.assertEqual(summary["latest_memory_forgotten"], "Prefer small focused PRs")
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_done_note_does_not_start_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (
                    13,
                    "@Pear Definition of done:\n\nDone: Tests pass and draft PR is posted",
                    False,
                )
            ],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertEqual(summary["done_criteria_count"], 1)
        self.assertEqual(
            summary["latest_done_criteria"],
            "Tests pass and draft PR is posted",
        )
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_response_policy_note_does_not_start_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (
                    13,
                    "@Pear Response policy:\n\nAmbient: mentions only",
                    False,
                )
            ],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertEqual(summary["response_policy_count"], 1)
        self.assertEqual(summary["latest_response_policy"], "mentions only")
        self.assertEqual(summary["response_policy"], "mentions_only")
        self.assertEqual(summary["worker_state"], "ready")
        self.assertTrue(summary["worker_can_run"])

    def test_routine_cadence_note_does_not_start_active_session(self) -> None:
        payload = get_pear_session_payload()

        summary = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (
                    13,
                    "@Pear Routine cadence:\n\nRoutine cadence: manual",
                    False,
                )
            ],
        )

        self.assertEqual(summary["state"], "not_started")
        self.assertEqual(summary["request_count"], 0)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertEqual(summary["routine_cadence_count"], 1)
        self.assertEqual(summary["latest_routine_cadence"], "manual")
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
                (12, "Ambient: on", False),
                (13, "Fold in the failing terminal UI test too.", False),
            ],
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
        self.assertTrue(pear_worker_should_start("@Pear What's up!", active_session))
        self.assertTrue(
            pear_worker_should_start(
                "Hi, agent, how are you?",
                active_session,
                explicitly_addressed=True,
            )
        )
        self.assertTrue(
            pear_worker_should_start("@Pear hey tell me something", active_session)
        )
        self.assertTrue(pear_worker_should_start("@Pear tell me a story", active_session))
        self.assertTrue(
            pear_worker_should_start("@Pear find answer to question", active_session)
        )
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
        self.assertFalse(pear_worker_should_start("Memory: Prefer pytest", active_session))
        self.assertFalse(
            pear_worker_should_start(
                "@Pear Forget memory:\n\nMemory forgotten: Prefer pytest",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start("@Pear forget latest memory", active_session)
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear Definition of done:\n\nDone: tests pass",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear Routine cadence:\n\nRoutine cadence: manual",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear only respond when I @-mention you",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear turn follow-ups back on",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear what routines do you have set up in this topic?",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear Disable routine:\n\nRoutine disabled: Watch PR status for pear/runtime",
                active_session,
            )
        )
        self.assertFalse(
            pear_worker_should_start(
                "@Pear Update routine:\n\n"
                "Routine disabled: Watch PR status for pear/runtime\n"
                "Routine: Watch PR status for pear/runtime every weekday",
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
                (12, "Ambient: mentions only", False),
            ],
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

    def test_worker_should_start_for_run_routine_request_with_routine(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "Routine cadence: every 30 minutes", False),
            ],
        )
        empty_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
            ],
        )
        paused_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "@Pear Pause session:\n\nReason: waiting for CI", False),
            ],
        )

        self.assertTrue(pear_worker_should_start("@Pear run routine", active_session))
        self.assertTrue(
            pear_worker_should_start(
                "run routine",
                active_session,
                explicitly_addressed=True,
            )
        )
        self.assertFalse(pear_worker_should_start("@Pear run routine", empty_session))
        self.assertFalse(pear_worker_should_start("@Pear run routine", paused_session))
        self.assertEqual(
            pear_worker_task_for_message("@Pear run routine", active_session),
            "Run the latest Pear routine: Watch PR status for pear/runtime\n"
            "Routine cadence: every 30 minutes",
        )

    def test_routine_status_request_is_not_steering(self) -> None:
        self.assertIsNone(
            parse_pear_steering_event(
                "@Pear what routines do you have set up in this topic?",
                44,
                sender_is_bot=False,
                session_started=True,
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
                    "body": "Done: Status checked.",
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
                    "```spoiler Ran 1 tool\n- ✓ Ran: `/bin/bash -lc pwd`\n```",
                    "Status checked. *✦*",
                ]
            ),
        )
        self.assertNotIn("Done: Status checked.", progress_message)

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
                    "```spoiler Ran 1/2 tools\n"
                    "- ✓ Ran: `/bin/bash -lc pwd`\n"
                    "- … Running: `/bin/bash -lc pytest`\n"
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

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
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
        self.assertNotIn("Task: choose between A, B, D, and \"Tokenmaxxer\"", completed_message)
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
                            "Done: Status checked; ready for the next concrete task.",
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
                    "- ✓ Ran: `/bin/bash -lc 'pwd && git status --short --branch'`\n"
                    "```",
                    "Status checked; ready for the next concrete task.",
                ]
            ),
        )
        self.assertNotIn("*✦*", completed_message)
        self.assertNotIn("Repo:", completed_message)
        self.assertNotIn("Verification:", completed_message)

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

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
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
                (
                    12,
                    "@Pear Steering update:\n\nRequest: keep the patch small",
                    False,
                    "plugyawn",
                ),
                (13, "Memory: Prefer small focused PRs", False, "plugyawn"),
                (14, "Routine: Watch PR status for pear/runtime", False, "plugyawn"),
                (15, "Done: Tests pass and draft PR is posted", False, "plugyawn"),
                (16, "Routine cadence: manual", False, "plugyawn"),
                (17, "Memory forgotten: Run terminal tests first", False, "plugyawn"),
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

        prompt = build_pear_codex_prompt(active_session, "fix the checkout test")

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
        self.assertIn("Treat channel memory as durable context", prompt)
        self.assertIn(
            "For casual, conversational, choice-only, or answer-only requests, respond directly.",
            prompt,
        )
        self.assertIn(
            "Do not run repository/session checks just to make a casual reply sound grounded.",
            prompt,
        )
        self.assertIn(
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
        self.assertIn("Pear agent skill definitions:", prompt)
        self.assertIn("Skill: standing-routines", prompt)
        self.assertIn("scheduled jobs or recurring digests", prompt)
        self.assertIn("channel or topic watches", prompt)
        self.assertIn("pull-request subscriptions or follow-up monitors", prompt)
        self.assertIn("Preserve the user's request as durable topic state", prompt)
        self.assertIn("Channel memory:\n- Prefer small focused PRs", prompt)
        self.assertIn("Forgotten memory:\n- Run terminal tests first", prompt)
        self.assertIn("Definition of done:\n- Tests pass and draft PR is posted", prompt)
        self.assertIn("Standing routines:\n- Watch PR status for pear/runtime", prompt)
        self.assertIn("Routine cadence:\n- manual", prompt)
        self.assertIn("Verification: <command or check result>", prompt)
        self.assertIn(
            "Session: <session record URL or Codex thread reference>", prompt
        )
        self.assertIn("Ticket: <ticket URL, ID, or path>", prompt)
        self.assertIn("Issue: <issue URL, ID, or path>", prompt)
        self.assertIn("Reviewers: <reviewer handles or teams>", prompt)
        self.assertIn("Routine: <standing follow-up or watch instruction>", prompt)
        self.assertIn("Routine cadence: <manual or watch cadence>", prompt)
        self.assertIn("Memory: <channel-scoped note Pear should remember>", prompt)
        self.assertIn(
            "Memory forgotten: <channel-scoped note Pear should ignore>", prompt
        )
        self.assertIn("Done: <definition of done or success criteria>", prompt)

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
                {
                    "label": "Memory",
                    "value": "Prefer small focused PRs",
                },
                {
                    "label": "Routine cadence",
                    "value": "manual",
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

    def test_topic_state_agent_tools_record_artifacts(self) -> None:
        recorded_results: list[dict[str, str]] = []
        created_tools = {}

        def fake_function_tool(func, **kwargs):
            created_tools[kwargs["name_override"]] = {
                "func": func,
                "description": kwargs["description_override"],
                "use_docstring_info": kwargs["use_docstring_info"],
            }
            return SimpleNamespace(name=kwargs["name_override"])

        tools = build_pear_topic_state_agent_tools(
            fake_function_tool,
            recorded_results,
            output_limit=200,
        )

        self.assertEqual(len(tools), len(PEAR_TOPIC_STATE_TOOLS))
        self.assertEqual(
            list(PEAR_TOPIC_STATE_TOOL_NAMES),
            [pear_topic_state_tool_name(label) for label in PEAR_TOPIC_STATE_TOOLS],
        )
        self.assertIn("pear_record_routine", created_tools)
        self.assertIn("pear_record_routine_cadence", created_tools)
        self.assertEqual(
            pear_topic_state_tool_name("Memory forgotten"),
            "pear_record_memory_forgotten",
        )
        self.assertIn("`Routine:`", created_tools["pear_record_routine"]["description"])
        self.assertFalse(created_tools["pear_record_routine"]["use_docstring_info"])
        self.assertEqual(
            created_tools["pear_record_routine"]["func"]("Watch PR status every weekday"),
            "Routine: Watch PR status every weekday",
        )
        self.assertEqual(
            recorded_results,
            [{"label": "Routine", "value": "Watch PR status every weekday"}],
        )

    def test_merge_pear_tool_results_deduplicates_artifacts(self) -> None:
        self.assertEqual(
            merge_pear_tool_results(
                [
                    {"label": "Routine", "value": "Watch PR status"},
                    {"label": "Verification", "value": "Codex completed"},
                ],
                [
                    {"label": "Routine", "value": "Watch PR status"},
                    {"label": "Diff", "value": "updated worker"},
                ],
            ),
            [
                {"label": "Routine", "value": "Watch PR status"},
                {"label": "Verification", "value": "Codex completed"},
                {"label": "Diff", "value": "updated worker"},
            ],
        )

    def test_codex_jsonl_results_include_runtime_events_and_thread(self) -> None:
        results = pear_tool_results_from_codex_jsonl(
            "\n".join(
                [
                    '{"type":"thread.started","thread_id":"thread-123"}',
                    '{"type":"item.completed","item":{"type":"command_execution"}}',
                    '{"type":"item.completed","item":{"type":"file_change"}}',
                    (
                        '{"type":"item.completed","item":{"type":"agent_message",'
                        '"text":"Verification: pytest zerver/tests/test_pear.py"}}'
                    ),
                ]
            ),
            output_limit=200,
        )

        self.assertEqual(
            results,
            [
                {
                    "label": "Verification",
                    "value": (
                        "Codex runtime completed with 1 command event(s), "
                        "1 file change event(s)."
                    ),
                },
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                },
                {
                    "label": "Session",
                    "value": "Codex thread thread-123",
                },
            ],
        )

    def test_codex_exec_tool_runner_uses_official_json_runtime(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\n".join(
                    [
                        '{"type":"thread.started","thread_id":"thread-456"}',
                        (
                            '{"type":"item.completed","item":{"type":"agent_message",'
                            '"text":"Verification: pytest zerver/tests/test_pear.py"}}'
                        ),
                    ]
                ),
                stderr="",
            )

        runner = PearCodexExecToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            service_tier="fast",
            fast_mode_enabled=True,
            extra_args=["--ignore-rules"],
            run_command=fake_run,
        )

        results = list(runner(active_session, "fix the checkout test"))

        self.assertEqual(
            calls[0][0],
            [
                "codex",
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--config",
                'approval_policy="never"',
                "--config",
                'service_tier="fast"',
                "--config",
                "features.fast_mode=true",
                "--cd",
                "/workspace",
                "--model",
                "gpt-5.5",
                "--ignore-rules",
                "-",
            ],
        )
        self.assertIn("Task: fix the checkout test", calls[0][1]["input"])
        self.assertEqual(calls[0][1]["cwd"], "/workspace")
        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": (
                        "codex-exec cwd=/workspace sandbox=workspace-write "
                        "approval_policy=never model=gpt-5.5 tier=fast"
                    ),
                },
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                },
                {
                    "label": "Session",
                    "value": "Codex thread thread-456",
                },
            ],
        )

    def test_codex_exec_tool_runner_resumes_thread_artifact(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Doc: Codex thread thread-existing", True),
            ],
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"type":"item.completed","item":{"type":"agent_message",'
                    '"text":"Verification: resumed existing thread"}}'
                ),
                stderr="",
            )

        runner = PearCodexExecToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            service_tier="fast",
            fast_mode_enabled=True,
            extra_args=["--ignore-rules"],
            run_command=fake_run,
        )

        results = list(runner(active_session, "continue the checkout fix"))

        self.assertEqual(
            pear_codex_thread_id_from_active_session(active_session),
            "thread-existing",
        )
        self.assertEqual(
            calls[0][0],
            [
                "codex",
                "exec",
                "resume",
                "thread-existing",
                "--json",
                "--config",
                'approval_policy="never"',
                "--config",
                'service_tier="fast"',
                "--config",
                "features.fast_mode=true",
                "--config",
                'sandbox_mode="workspace-write"',
                "--model",
                "gpt-5.5",
                "--ignore-rules",
                "-",
            ],
        )
        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": (
                        "codex-exec cwd=/workspace sandbox=workspace-write "
                        "approval_policy=never model=gpt-5.5 tier=fast"
                    ),
                },
                {"label": "Verification", "value": "resumed existing thread"},
            ],
        )

    def test_codex_exec_tool_runner_runs_under_topic_os_user(self) -> None:
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear inspect", False)],
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"type":"item.completed","item":{"type":"agent_message","text":"Done."}}',
                stderr="",
            )

        process_isolation = PearProcessIsolation(
            username="pear-0123456789abcdef01234567",
            home="/home/pear-0123456789abcdef01234567",
            codex_home="/home/pear-0123456789abcdef01234567/.codex",
        )
        runner = PearCodexExecToolRunner(
            codex_bin="/srv/zulip-node/bin/codex",
            workdir="/home/zulipdev/pear-workspaces/topic",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            process_isolation=process_isolation,
            run_command=fake_run,
        )

        results = list(runner(active_session, "inspect"))

        self.assertEqual(
            calls[0][0][:10],
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
        self.assertEqual(calls[0][1]["cwd"], "/")
        self.assertIn("/srv/zulip-node/bin/codex", calls[0][0])
        self.assertIn("--cd", calls[0][0])
        self.assertIn("/home/zulipdev/pear-workspaces/topic", calls[0][0])
        self.assertIn(
            "isolation=os-user:pear-0123456789abcdef01234567",
            results[0]["value"],
        )

    def test_codex_thread_id_from_active_session_prefers_runtime_state(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Doc: Codex thread thread-visible", True),
            ],
            latest_codex_thread_id="thread-runtime",
        )

        self.assertEqual(
            pear_codex_thread_id_from_active_session(active_session),
            "thread-runtime",
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

    def test_codex_exec_addressing_router_uses_persisted_thread(self) -> None:
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
        self.assertEqual(
            FakeBotHandler.storage.values,
            {"pear_addressing_router": {"thread_id": "router-thread"}},
        )
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
        self.assertEqual(
            calls[0][0],
            [
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
            ],
        )
        self.assertEqual(
            calls[1][0],
            [
                "codex",
                "exec",
                "resume",
                "router-thread",
                "--json",
                "--config",
                'approval_policy="never"',
                "--config",
                'sandbox_mode="read-only"',
                "--model",
                "gpt-router",
                "--config",
                'model_reasoning_effort="low"',
                "--ignore-rules",
                "-",
            ],
        )
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
        self.assertEqual(
            request_payload["generation_config"]["thinking_level"], "minimal"
        )
        self.assertEqual(
            request_payload["response_format"]["mime_type"], "application/json"
        )
        self.assertEqual(
            request_payload["response_format"]["schema"]["required"], ["addressed"]
        )
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

    def test_codex_exec_tool_runner_streams_json_events(self) -> None:
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
                self.value = ""
                self.closed = False

            def write(self, value: str) -> int:
                self.value += value
                return len(value)

            def close(self) -> None:
                self.closed = True

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
                        '{"type":"thread.started","thread_id":"thread-stream"}\n',
                        (
                            '{"type":"item.started","item":{"id":"cmd-1",'
                            '"type":"command_execution","command":"pytest",'
                            '"status":"in_progress"}}\n'
                        ),
                        (
                            '{"type":"item.completed","item":{"id":"cmd-1",'
                            '"type":"command_execution","command":"pytest",'
                            '"status":"completed"}}\n'
                        ),
                        (
                            '{"type":"item.delta","item_id":"msg-1",'
                            '"delta":"Partial "}\n'
                        ),
                        (
                            '{"type":"item.delta","item_id":"msg-1",'
                            '"delta":"answer"}\n'
                        ),
                        (
                            '{"type":"item.completed","item":{"id":"msg-1",'
                            '"type":"agent_message",'
                            '"text":"Partial answer\\nVerification: pytest passed"}}\n'
                        ),
                        '{"type":"turn.completed","usage":{"output_tokens":12}}\n',
                    ]
                )
                self.stderr = FakeStream([])
                self.killed = False

            def wait(self) -> int:
                return 0

            def kill(self) -> None:
                self.killed = True

        def fake_popen(command, **kwargs):
            calls.append((command, kwargs))
            process = FakeProcess()
            processes.append(process)
            return process

        runner = PearCodexExecToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            popen_command=fake_popen,
        )
        streamed_events = []

        results = list(
            runner(
                active_session,
                "fix the checkout test",
                event_callback=streamed_events.append,
            )
        )

        self.assertEqual(calls[0][1]["cwd"], "/workspace")
        self.assertIn("Task: fix the checkout test", processes[0].stdin.value)
        self.assertTrue(processes[0].stdin.closed)
        self.assertEqual(
            [event["kind"] for event in streamed_events],
            ["session", "command", "command", "assistant", "assistant", "assistant", "status"],
        )
        self.assertEqual(streamed_events[1]["status"], "running")
        self.assertEqual(streamed_events[2]["status"], "done")
        self.assertEqual(streamed_events[3]["status"], "running")
        self.assertEqual(streamed_events[3]["body"], "Partial ")
        self.assertEqual(streamed_events[4]["status"], "running")
        self.assertEqual(streamed_events[4]["body"], "Partial answer")
        self.assertEqual(streamed_events[5]["status"], "done")
        self.assertEqual(streamed_events[5]["body"], "Partial answer\nVerification: pytest passed")
        self.assertIn({"label": "Verification", "value": "pytest passed"}, results)
        self.assertIn(
            {"label": "Session", "value": "Codex thread thread-stream"},
            results,
        )

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
                'model_reasoning_effort="medium"',
            ],
        )
        sent_methods = [line["method"] for line in processes[0].stdin.lines]
        self.assertEqual(
            sent_methods,
            ["initialize", "initialized", "thread/start", "turn/start", "turn/steer"],
        )
        steering_request = processes[0].stdin.lines[-1]
        self.assertEqual(steering_request["params"]["expectedTurnId"], "turn-app")
        self.assertEqual(
            steering_request["params"]["input"],
            [{"type": "text", "text": "Make it the 30th prime."}],
        )
        self.assertEqual(
            [event["kind"] for event in streamed_events],
            ["session", "status", "command", "usage", "command", "assistant", "assistant", "status"],
        )
        self.assertEqual(streamed_events[3]["metadata"]["usage"]["cache_percent"], 60.0)
        self.assertIn({"label": "Verification", "value": "app-server ok"}, results)
        self.assertIn(
            {"label": "Session", "value": "Codex thread thread-app"},
            results,
        )

    def test_codex_exec_tool_runner_raises_for_failed_runtime(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="auth failed")

        runner = PearCodexExecToolRunner(
            codex_bin="codex",
            workdir=None,
            model=None,
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=900,
            output_limit=200,
            run_command=fake_run,
        )

        with self.assertRaisesRegex(PearAgentRuntimeError, "exit code 2"):
            runner(active_session, "fix the checkout test")

    def test_codex_agents_mcp_tool_runner_uses_official_mcp_runtime(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        class FakeMCPServerStdio:
            def __init__(self, **kwargs):
                calls.append(("mcp", kwargs))

            async def __aenter__(self):
                calls.append(("mcp_enter",))
                return "codex-mcp-server"

            async def __aexit__(self, exc_type, exc, traceback):
                calls.append(("mcp_exit", exc_type))

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                calls.append(("agent", kwargs))

        class FakeRunner:
            @staticmethod
            async def run(agent, prompt):
                calls.append(("runner_run", agent.kwargs, prompt))
                routine_tool = next(
                    tool
                    for tool in agent.kwargs["tools"]
                    if tool.name == "pear_record_routine"
                )
                self.assertEqual(
                    routine_tool.func("Watch PR status every weekday"),
                    "Routine: Watch PR status every weekday",
                )
                return SimpleNamespace(
                    final_output="Verification: Codex MCP completed",
                    structuredContent={"threadId": "thread-mcp-123"},
                )

        class FakeTrace:
            def __init__(self, name):
                calls.append(("trace", name))

            def __enter__(self):
                calls.append(("trace_enter",))

            def __exit__(self, exc_type, exc, traceback):
                calls.append(("trace_exit", exc_type))

        def fake_function_tool(func, **kwargs):
            return SimpleNamespace(name=kwargs["name_override"], func=func)

        agents_module = ModuleType("agents")
        agents_module.Agent = FakeAgent
        agents_module.Runner = FakeRunner
        agents_module.function_tool = fake_function_tool
        agents_module.trace = FakeTrace
        agents_mcp_module = ModuleType("agents.mcp")
        agents_mcp_module.MCPServerStdio = FakeMCPServerStdio
        agents_module.mcp = agents_mcp_module

        missing = object()
        original_agents = sys.modules.get("agents", missing)
        original_agents_mcp = sys.modules.get("agents.mcp", missing)
        sys.modules["agents"] = agents_module
        sys.modules["agents.mcp"] = agents_mcp_module
        try:
            runner = PearCodexAgentsMcpToolRunner(
                codex_bin="codex",
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=900,
                output_limit=200,
            )

            results = list(runner(active_session, "fix the checkout test"))
        finally:
            if original_agents is missing:
                del sys.modules["agents"]
            else:
                sys.modules["agents"] = original_agents
            if original_agents_mcp is missing:
                del sys.modules["agents.mcp"]
            else:
                sys.modules["agents.mcp"] = original_agents_mcp

        self.assertEqual(
            calls[0],
            (
                "mcp",
                {
                    "name": "Codex CLI",
                    "params": {"command": "codex", "args": ["mcp-server"]},
                    "client_session_timeout_seconds": 900,
                },
            ),
        )
        agent_kwargs = calls[2][1]
        self.assertEqual(agent_kwargs["mcp_servers"], ["codex-mcp-server"])
        self.assertIn(
            "pear_record_routine",
            [tool.name for tool in agent_kwargs["tools"]],
        )
        self.assertIn("Codex CLI MCP server", agent_kwargs["instructions"])
        self.assertIn("`codex`", agent_kwargs["instructions"])
        self.assertIn("connected MCP tool is the runtime boundary", agent_kwargs["instructions"])
        self.assertIn("`pear_record_*` tools", agent_kwargs["instructions"])
        self.assertIn("`pear_record_routine`", agent_kwargs["instructions"])
        self.assertIn("Set `approval-policy` to `never`", agent_kwargs["instructions"])
        self.assertIn("Set `sandbox` to `workspace-write`", agent_kwargs["instructions"])
        self.assertEqual(calls[3], ("trace", "Pear Codex MCP worker"))
        self.assertEqual(calls[4], ("trace_enter",))
        self.assertIn("Task: fix the checkout test", calls[5][2])
        self.assertIn("Codex MCP tool arguments:", calls[5][2])
        self.assertIn("Use codex to start a new Codex thread.", calls[5][2])
        self.assertIn("approval-policy: never", calls[5][2])
        self.assertIn("sandbox: workspace-write", calls[5][2])
        self.assertEqual(calls[6], ("trace_exit", None))
        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": (
                        "codex-agents-mcp cwd=/workspace sandbox=workspace-write "
                        "approval_policy=never model=gpt-5.5"
                    ),
                },
                {"label": "Routine", "value": "Watch PR status every weekday"},
                {"label": "Verification", "value": "Codex MCP completed"},
                {"label": "Session", "value": "Codex thread thread-mcp-123"},
            ],
        )

    def test_codex_agents_mcp_tool_runner_resumes_thread_artifact(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Doc: Codex thread thread-existing", True),
            ],
        )
        calls = []

        class FakeMCPServerStdio:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return "codex-mcp-server"

            async def __aexit__(self, exc_type, exc, traceback):
                pass

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                calls.append(("agent", kwargs))

        class FakeRunner:
            @staticmethod
            async def run(agent, prompt):
                calls.append(("runner_run", agent.kwargs, prompt))
                return SimpleNamespace(
                    final_output="Verification: Codex MCP resumed",
                    new_items=[SimpleNamespace(raw_item={"name": "codex-reply"})],
                )

        class FakeTrace:
            def __init__(self, name):
                calls.append(("trace", name))

            def __enter__(self):
                calls.append(("trace_enter",))

            def __exit__(self, exc_type, exc, traceback):
                calls.append(("trace_exit", exc_type))

        def fake_function_tool(func, **kwargs):
            return SimpleNamespace(name=kwargs["name_override"], func=func)

        agents_module = ModuleType("agents")
        agents_module.Agent = FakeAgent
        agents_module.Runner = FakeRunner
        agents_module.function_tool = fake_function_tool
        agents_module.trace = FakeTrace
        agents_mcp_module = ModuleType("agents.mcp")
        agents_mcp_module.MCPServerStdio = FakeMCPServerStdio
        agents_module.mcp = agents_mcp_module

        missing = object()
        original_agents = sys.modules.get("agents", missing)
        original_agents_mcp = sys.modules.get("agents.mcp", missing)
        sys.modules["agents"] = agents_module
        sys.modules["agents.mcp"] = agents_mcp_module
        try:
            runner = PearCodexAgentsMcpToolRunner(
                codex_bin="codex",
                workdir="/workspace",
                model=None,
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=900,
                output_limit=200,
            )

            results = list(runner(active_session, "continue the checkout fix"))
        finally:
            if original_agents is missing:
                del sys.modules["agents"]
            else:
                sys.modules["agents"] = original_agents
            if original_agents_mcp is missing:
                del sys.modules["agents.mcp"]
            else:
                sys.modules["agents.mcp"] = original_agents_mcp

        self.assertIn("`codex-reply`", calls[0][1]["instructions"])
        self.assertIn(
            "pear_record_routine",
            [tool.name for tool in calls[0][1]["tools"]],
        )
        self.assertIn("threadId `thread-existing`", calls[0][1]["instructions"])
        self.assertIn("accepts only `prompt` and `threadId`", calls[0][1]["instructions"])
        self.assertEqual(calls[1], ("trace", "Pear Codex MCP worker"))
        self.assertEqual(calls[2], ("trace_enter",))
        self.assertIn("threadId: thread-existing", calls[3][2])
        self.assertIn("Do not pass approval-policy", calls[3][2])
        self.assertEqual(calls[4], ("trace_exit", None))
        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": (
                        "codex-agents-mcp cwd=/workspace sandbox=workspace-write "
                        "approval_policy=never"
                    ),
                },
                {"label": "Verification", "value": "Codex MCP resumed"},
                {"label": "Session", "value": "Codex thread thread-existing"},
            ],
        )

    def test_codex_agents_mcp_tool_runner_requires_mcp_tool_evidence(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )

        class FakeMCPServerStdio:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return "codex-mcp-server"

            async def __aexit__(self, exc_type, exc, traceback):
                pass

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeRunner:
            @staticmethod
            async def run(agent, prompt):
                return SimpleNamespace(final_output="Verification: skipped tool")

        class FakeTrace:
            def __init__(self, name):
                pass

            def __enter__(self):
                pass

            def __exit__(self, exc_type, exc, traceback):
                pass

        def fake_function_tool(func, **kwargs):
            return SimpleNamespace(name=kwargs["name_override"], func=func)

        agents_module = ModuleType("agents")
        agents_module.Agent = FakeAgent
        agents_module.Runner = FakeRunner
        agents_module.function_tool = fake_function_tool
        agents_module.trace = FakeTrace
        agents_mcp_module = ModuleType("agents.mcp")
        agents_mcp_module.MCPServerStdio = FakeMCPServerStdio
        agents_module.mcp = agents_mcp_module

        missing = object()
        original_agents = sys.modules.get("agents", missing)
        original_agents_mcp = sys.modules.get("agents.mcp", missing)
        sys.modules["agents"] = agents_module
        sys.modules["agents.mcp"] = agents_mcp_module
        try:
            runner = PearCodexAgentsMcpToolRunner(
                codex_bin="codex",
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=900,
                output_limit=200,
            )

            with self.assertRaisesRegex(PearAgentRuntimeError, "without evidence"):
                runner(active_session, "fix the checkout test")
        finally:
            if original_agents is missing:
                del sys.modules["agents"]
            else:
                sys.modules["agents"] = original_agents
            if original_agents_mcp is missing:
                del sys.modules["agents.mcp"]
            else:
                sys.modules["agents.mcp"] = original_agents_mcp

    def test_codex_agents_mcp_tool_runner_reports_missing_agents_sdk(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )

        missing = object()
        original_agents = sys.modules.get("agents", missing)
        original_agents_mcp = sys.modules.get("agents.mcp", missing)
        sys.modules["agents"] = None
        sys.modules["agents.mcp"] = None
        try:
            runner = PearCodexAgentsMcpToolRunner(
                codex_bin="codex",
                workdir=None,
                model=None,
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=900,
                output_limit=200,
            )
            with self.assertRaisesRegex(PearAgentRuntimeError, "openai-agents"):
                runner(active_session, "fix the checkout test")
        finally:
            if original_agents is missing:
                del sys.modules["agents"]
            else:
                sys.modules["agents"] = original_agents
            if original_agents_mcp is missing:
                del sys.modules["agents.mcp"]
            else:
                sys.modules["agents.mcp"] = original_agents_mcp

    def test_codex_agents_thread_id_extractor_reads_structured_content(self) -> None:
        result = SimpleNamespace(
            new_items=[
                SimpleNamespace(
                    raw_item={
                        "structuredContent": {
                            "threadId": "thread-from-structured-content"
                        }
                    }
                )
            ]
        )

        self.assertEqual(
            pear_codex_thread_id_from_agents_objects(result),
            "thread-from-structured-content",
        )

    def test_codex_agents_mcp_tool_detector_reads_tool_evidence(self) -> None:
        result = SimpleNamespace(
            new_items=[
                SimpleNamespace(
                    raw_item={
                        "name": "codex",
                        "structuredContent": {"threadId": "thread-mcp-123"},
                    }
                )
            ]
        )

        self.assertTrue(
            pear_agents_result_used_codex_mcp_tool(result, expected_tool="codex")
        )
        self.assertFalse(
            pear_agents_result_used_codex_mcp_tool(
                SimpleNamespace(final_output="Verification: no tool call"),
                expected_tool="codex",
            )
        )

    def test_codex_sdk_tool_runner_uses_official_thread_api(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeThread:
            def run(self, prompt):
                calls.append(("run", prompt))
                return SimpleNamespace(
                    final_response="Verification: pytest zerver/tests/test_pear.py"
                )

        class FakeCodex:
            def __init__(self):
                calls.append(("init",))

            def __enter__(self):
                calls.append(("enter",))
                return self

            def __exit__(self, exc_type, exc, traceback):
                calls.append(("exit", exc_type))

            def thread_start(self, **kwargs):
                calls.append(("thread_start", kwargs))
                return FakeThread()

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex, Sandbox=FakeSandbox
        )
        try:
            runner = PearCodexSdkToolRunner(
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="never",
                output_limit=200,
            )

            results = list(runner(active_session, "fix the checkout test"))
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

        self.assertEqual(calls[0], ("init",))
        self.assertEqual(
            calls[1],
            (
                "thread_start",
                {
                    "sandbox": "workspace-write",
                    "cwd": "/workspace",
                    "model": "gpt-5.5",
                },
            ),
        )
        self.assertEqual(calls[2][0], "run")
        self.assertIn("Task: fix the checkout test", calls[2][1])
        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": (
                        "codex-sdk cwd=/workspace sandbox=workspace-write "
                        "approval_policy=never model=gpt-5.5"
                    ),
                },
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                }
            ],
        )

    def test_codex_sdk_tool_runner_rejects_os_user_isolation(self) -> None:
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=get_pear_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear inspect", False)],
        )
        process_isolation = PearProcessIsolation(
            username="pear-0123456789abcdef01234567",
            home="/home/pear-0123456789abcdef01234567",
            codex_home="/home/pear-0123456789abcdef01234567/.codex",
        )
        runner = PearCodexSdkToolRunner(
            codex_bin="codex",
            workdir="/home/zulipdev/pear-workspaces/topic",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            output_limit=200,
            process_isolation=process_isolation,
        )

        with self.assertRaisesRegex(PearAgentRuntimeError, "OS-user isolation"):
            runner(active_session, "inspect")

    def test_codex_sdk_tool_runner_reuses_app_server_client(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeThread:
            def run(self, prompt):
                calls.append(("run", prompt))
                return SimpleNamespace(final_response="Verification: SDK completed")

        class FakeCodex:
            def __init__(self):
                calls.append(("init",))

            def thread_start(self, **kwargs):
                calls.append(("thread_start", kwargs))
                return FakeThread()

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex, Sandbox=FakeSandbox
        )
        try:
            runner = PearCodexSdkToolRunner(
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="never",
                output_limit=200,
            )

            list(runner(active_session, "first task"))
            list(runner(active_session, "second task"))
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

        self.assertEqual([call[0] for call in calls].count("init"), 1)
        self.assertEqual([call[0] for call in calls].count("thread_start"), 2)
        self.assertEqual([call[0] for call in calls].count("run"), 2)

    def test_codex_sdk_tool_runner_filters_unsupported_thread_kwargs(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeThread:
            def run(self, prompt):
                calls.append(("run", prompt))
                return SimpleNamespace(final_response="Verification: SDK completed")

        class FakeCodex:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                pass

            def thread_start(self, *, sandbox, model):
                calls.append(("thread_start", sandbox, model))
                return FakeThread()

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex, Sandbox=FakeSandbox
        )
        try:
            runner = PearCodexSdkToolRunner(
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="never",
                output_limit=200,
            )

            results = list(runner(active_session, "fix the checkout test"))
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

        self.assertEqual(
            calls[0],
            ("thread_start", "workspace-write", "gpt-5.5"),
        )
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(results[1], {"label": "Verification", "value": "SDK completed"})

    def test_codex_sdk_tool_runner_passes_runtime_knobs(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )
        calls = []

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeApprovalMode:
            deny_all = "deny_all"
            auto_review = "auto_review"

        class FakeCodexConfig:
            def __init__(self, **kwargs):
                calls.append(("config", kwargs))

        class FakeThread:
            def run(self, prompt):
                calls.append(("run", prompt))
                return SimpleNamespace(final_response="Verification: SDK completed")

        class FakeCodex:
            def __init__(self, *, config=None):
                calls.append(("init", config))

            def thread_start(self, **kwargs):
                calls.append(("thread_start", kwargs))
                return FakeThread()

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex,
            CodexConfig=FakeCodexConfig,
            Sandbox=FakeSandbox,
            ApprovalMode=FakeApprovalMode,
        )
        try:
            runner = PearCodexSdkToolRunner(
                codex_bin="/opt/codex",
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="never",
                service_tier="fast",
                fast_mode_enabled=True,
                output_limit=200,
            )

            list(runner(active_session, "fix the checkout test"))
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

        self.assertEqual(
            calls[0],
            (
                "config",
                {
                    "codex_bin": "/opt/codex",
                    "config_overrides": ("features.fast_mode=true",),
                },
            ),
        )
        self.assertEqual(calls[1][0], "init")
        self.assertEqual(
            calls[2],
            (
                "thread_start",
                {
                    "sandbox": "workspace-write",
                    "cwd": "/workspace",
                    "model": "gpt-5.5",
                    "approval_mode": "deny_all",
                    "service_tier": "fast",
                },
            ),
        )
        self.assertEqual(calls[3][0], "run")

    def test_codex_sdk_tool_runner_posts_thread_artifact(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[(10, "@Pear fix the checkout test", False)],
        )

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeThread:
            thread_id = "thread-sdk-123"

            def run(self, prompt):
                return SimpleNamespace(
                    final_response="Verification: pytest zerver/tests/test_pear.py"
                )

        class FakeCodex:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                pass

            def thread_start(self, **kwargs):
                return FakeThread()

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex, Sandbox=FakeSandbox
        )
        try:
            runner = PearCodexSdkToolRunner(
                workdir=None,
                model=None,
                sandbox="workspace-write",
                approval_policy="never",
                output_limit=200,
            )

            results = list(runner(active_session, "fix the checkout test"))
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": "codex-sdk sandbox=workspace-write approval_policy=never",
                },
                {
                    "label": "Verification",
                    "value": "pytest zerver/tests/test_pear.py",
                },
                {
                    "label": "Session",
                    "value": "Codex thread thread-sdk-123",
                },
            ],
        )

    def test_codex_sdk_tool_runner_resumes_thread_artifact(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Doc: Codex thread thread-existing", True),
            ],
        )
        calls = []

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeThread:
            def run(self, prompt):
                calls.append(("run", prompt))
                return SimpleNamespace(final_response="Verification: SDK resumed thread")

        class FakeCodex:
            def __init__(self):
                calls.append(("init",))

            def __enter__(self):
                calls.append(("enter",))
                return self

            def __exit__(self, exc_type, exc, traceback):
                calls.append(("exit", exc_type))

            def thread_resume(self, thread_id, **kwargs):
                calls.append(("thread_resume", thread_id, kwargs))
                return FakeThread()

            def thread_start(self, **kwargs):
                calls.append(("thread_start", kwargs))
                return FakeThread()

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex, Sandbox=FakeSandbox
        )
        try:
            runner = PearCodexSdkToolRunner(
                workdir="/workspace",
                model="gpt-5.5",
                sandbox="workspace-write",
                approval_policy="on-request",
                output_limit=200,
            )

            results = list(runner(active_session, "continue the checkout fix"))
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

        self.assertEqual(calls[0], ("init",))
        self.assertEqual(
            calls[1],
            (
                "thread_resume",
                "thread-existing",
                {
                    "sandbox": "workspace-write",
                    "cwd": "/workspace",
                    "model": "gpt-5.5",
                },
            ),
        )
        self.assertEqual(calls[2][0], "run")
        self.assertIn("Task: continue the checkout fix", calls[2][1])
        self.assertEqual(
            results,
            [
                {
                    "label": "Runtime",
                    "value": (
                        "codex-sdk cwd=/workspace sandbox=workspace-write "
                        "approval_policy=on-request model=gpt-5.5"
                    ),
                },
                {
                    "label": "Verification",
                    "value": "SDK resumed thread",
                },
                {
                    "label": "Session",
                    "value": "Codex thread thread-existing",
                },
            ],
        )

    def test_codex_sdk_tool_runner_requires_resume_api_for_thread_artifact(self) -> None:
        payload = get_pear_session_payload()
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Doc: Codex thread thread-existing", True),
            ],
        )

        class FakeSandbox:
            read_only = "read-only"
            workspace_write = "workspace-write"
            full_access = "danger-full-access"

        class FakeCodex:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                pass

        original_module = sys.modules.get("openai_codex")
        sys.modules["openai_codex"] = SimpleNamespace(
            Codex=FakeCodex, Sandbox=FakeSandbox
        )
        try:
            runner = PearCodexSdkToolRunner(
                workdir=None,
                model=None,
                sandbox="workspace-write",
                approval_policy="never",
                output_limit=200,
            )
            with self.assertRaisesRegex(PearAgentRuntimeError, "cannot resume"):
                runner(active_session, "continue the checkout fix")
        finally:
            if original_module is None:
                del sys.modules["openai_codex"]
            else:
                sys.modules["openai_codex"] = original_module

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
                (12, "Ambient: on", False),
                (13, "Fold in the failing terminal UI test too.", False),
            ],
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

        def tool_runner(active_session, task):
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
                (11, "Ambient: on", False),
            ],
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
                    "content": "I’m checking the failing test. *✦*",
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
                        f"{PEAR_STEERING_QUEUED_MARKDOWN} *✦*"
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

    def test_embedded_bot_handler_routes_natural_routine_setup_to_worker(self) -> None:
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

        def tool_runner(active_session, task):
            seen_tasks.append(task)
            return [
                {
                    "label": "Routine",
                    "value": "every weekday at 9am, check the linked PRs and post a one-line status",
                },
                {
                    "label": "Routine cadence",
                    "value": "every weekday at 9am",
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
        content = (
            "@Pear every weekday at 9am, check the linked PRs and post a "
            "one-line status"
        )
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

    def test_embedded_bot_handler_runs_latest_routine_request(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "Routine cadence: manual", False),
            ],
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task):
            seen_tasks.append(task)
            return [
                {
                    "label": "Verification",
                    "value": "Routine run accepted",
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
            "content": "@Pear run routine",
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
                "Run the latest Pear routine: Watch PR status for pear/runtime\n"
                "Routine cadence: manual"
            ],
        )
        self.assertEqual(len(sent_replies), 2)
        self.assertIn("Running the latest Pear routine", sent_replies[0])
        self.assertIn("Routine: Watch PR status for pear/runtime", sent_replies[0])
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)

    def test_embedded_bot_handler_lists_routines_without_worker(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
                (13, "Routine cadence: manual", False),
            ],
        )
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

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
                return {"id": 300 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                content = message.get("content")
                assert isinstance(content, str)
                updated_replies.append(content)

        run_store = FakeRunStore()
        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(
            {
                "id": 205,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "@Pear what routines do you have set up in this topic?",
            },
            FakeBotHandler(),
        )

        self.assertEqual(run_store.starts, [])
        self.assertEqual(len(sent_replies), 1)
        self.assertIn("Pear routines recorded", sent_replies[0])
        self.assertIn("- Watch PR status for pear/runtime", sent_replies[0])
        self.assertIn("Latest routine cadence: manual", sent_replies[0])

    def test_embedded_bot_handler_disables_latest_routine_without_worker(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
            ],
        )
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

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
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        run_store = FakeRunStore()
        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(
            {
                "id": 206,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "@Pear disable latest routine",
            },
            FakeBotHandler(),
        )

        self.assertEqual(run_store.starts, [])
        self.assertEqual(len(sent_replies), 1)
        self.assertIn("Routine disabled: Watch PR status for pear/runtime", sent_replies[0])

    def test_embedded_bot_handler_updates_routine_without_worker(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Routine: Watch PR status for pear/runtime", False),
            ],
        )
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

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
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        run_store = FakeRunStore()
        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(
            {
                "id": 207,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "@Pear Update routine:\n\n"
                "Routine disabled: Watch PR status for pear/runtime\n"
                "Routine: Watch PR status for pear/runtime every weekday",
            },
            FakeBotHandler(),
        )

        self.assertEqual(run_store.starts, [])
        self.assertEqual(len(sent_replies), 1)
        self.assertIn("Updated.", sent_replies[0])
        self.assertIn("Routine disabled: Watch PR status for pear/runtime", sent_replies[0])
        self.assertIn(
            "Routine: Watch PR status for pear/runtime every weekday",
            sent_replies[0],
        )

    def test_embedded_bot_handler_forgets_latest_memory_without_worker(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Memory: Prefer small focused PRs", False),
            ],
        )
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

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
            def send_reply(self, message: dict[str, object], response: str) -> None:
                sent_replies.append(response)

        run_store = FakeRunStore()
        PearEmbeddedBotHandler(
            active_session_loader=active_session_loader,
            worker_run_store=run_store,
        ).handle_message(
            {
                "id": 207,
                "type": "stream",
                "display_recipient": PEAR_DEFAULT_STREAM,
                "topic": PEAR_DEFAULT_TOPIC,
                "content": "@Pear forget latest memory",
            },
            FakeBotHandler(),
        )

        self.assertEqual(run_store.starts, [])
        self.assertEqual(len(sent_replies), 1)
        self.assertIn("Memory forgotten: Prefer small focused PRs", sent_replies[0])

    def test_embedded_bot_handler_honors_mentions_only_followup_policy(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "- [~] State the first concrete edit before changing files.", True),
                (12, "Ambient: mentions only", False),
            ],
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task):
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
                (12, "Ambient: mentions only", False),
            ],
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task):
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

        def tool_runner(active_session, task):
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
                (11, "Ambient: mentions only", False),
            ],
        )
        seen_tasks: list[str] = []
        sent_replies: list[str] = []
        classifier_calls: list[tuple[str, str]] = []

        def active_session_loader(message, bot_handler):
            return loaded_session

        def tool_runner(active_session, task):
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

        self.assertEqual(classifier_calls, [("hey agent, can you check the failing parser test?", "mentions_only")])
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
                (11, "Ambient: mentions only", False),
            ],
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
        memory_replies = self.run_embedded_bot_handler(
            "@**Pear** Remember:\n\nMemory: Prefer small focused PRs",
        )
        channel_memory_replies = self.run_embedded_bot_handler(
            "@Pear remember for this channel: only respond when someone @-mentions you directly.",
        )
        done_replies = self.run_embedded_bot_handler(
            "@**Pear** Definition of done:\n\nDone: Tests pass and draft PR is posted",
        )
        response_policy_replies = self.run_embedded_bot_handler(
            "@**Pear** Response policy:\n\nAmbient: mentions only",
        )
        natural_quiet_replies = self.run_embedded_bot_handler(
            "@Pear only respond when I @-mention you",
        )
        natural_ambient_replies = self.run_embedded_bot_handler(
            "@Pear turn follow-ups back on",
        )
        routine_cadence_replies = self.run_embedded_bot_handler(
            "@**Pear** Routine cadence:\n\nRoutine cadence: manual",
        )

        self.assertEqual(steering_replies[0], "Steering queued.")
        self.assertNotIn("Confirm repo, branch, and target runtime", steering_replies[0])
        self.assertIn("Paused.", pause_replies[0])
        self.assertIn("Reason: waiting for CI", pause_replies[0])
        self.assertNotIn("Confirm repo, branch, and target runtime", pause_replies[0])
        self.assertIn("Resuming.", resume_replies[0])
        self.assertIn("Request: continue with rollout", resume_replies[0])
        self.assertNotIn("Confirm repo, branch, and target runtime", resume_replies[0])
        self.assertIn("Saved.", memory_replies[0])
        self.assertIn("Memory: Prefer small focused PRs", memory_replies[0])
        self.assertNotIn("Confirm repo, branch, and target runtime", memory_replies[0])
        self.assertIn("Saved.", channel_memory_replies[0])
        self.assertIn(
            "Memory: only respond when someone @-mentions you directly.",
            channel_memory_replies[0],
        )
        self.assertNotIn(
            "Ambient: mentions only",
            channel_memory_replies[0],
        )
        self.assertIn("definition of done", done_replies[0])
        self.assertIn("Done: Tests pass and draft PR is posted", done_replies[0])
        self.assertNotIn("Confirm repo, branch, and target runtime", done_replies[0])
        self.assertIn(
            "only start follow-up worker turns",
            response_policy_replies[0],
        )
        self.assertIn("Ambient: mentions only", response_policy_replies[0])
        self.assertIn("Ambient: mentions only", natural_quiet_replies[0])
        self.assertIn("Ambient: on", natural_ambient_replies[0])
        self.assertNotIn(
            "Confirm repo, branch, and target runtime",
            response_policy_replies[0],
        )
        self.assertIn("routine cadence", routine_cadence_replies[0])
        self.assertIn("Routine cadence: manual", routine_cadence_replies[0])
        self.assertNotIn(
            "Confirm repo, branch, and target runtime",
            routine_cadence_replies[0],
        )
        self.assertEqual(len(steering_replies), 1)
        self.assertEqual(len(pause_replies), 1)
        self.assertEqual(len(resume_replies), 2)
        self.assertEqual(len(memory_replies), 1)
        self.assertEqual(len(channel_memory_replies), 1)
        self.assertEqual(len(done_replies), 1)
        self.assertEqual(len(response_policy_replies), 1)
        self.assertEqual(len(natural_quiet_replies), 1)
        self.assertEqual(len(natural_ambient_replies), 1)
        self.assertEqual(len(routine_cadence_replies), 1)
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

    def test_embedded_bot_handler_replies_to_memory_status_without_worker(self) -> None:
        payload = get_pear_session_payload()
        loaded_session = build_pear_active_session_summary_from_messages(
            pear_session=payload,
            source="server",
            stream_exists=True,
            messages=[
                (10, "@Pear fix the checkout test", False),
                (11, "Memory: Prefer small focused PRs", False),
            ],
        )

        def active_session_loader(message, bot_handler):
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
            "content": "@Pear What do you remember about this topic?",
        }

        PearEmbeddedBotHandler(active_session_loader=active_session_loader).handle_message(
            message,
            FakeBotHandler(),
        )

        self.assertEqual(len(sent_replies), 1)
        self.assertIn("I remember these notes", sent_replies[0])
        self.assertIn("- Prefer small focused PRs", sent_replies[0])
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
        self.assertEqual(len(updated_replies), 2)
        self.assertEqual(sent_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(updated_replies[0], PEAR_ACTIVE_THINKING_MARKDOWN)
        self.assertEqual(
            updated_replies[1],
            "Pear worker completed without a visible response.",
        )
        self.assertEqual(sent_replies[1], PEAR_ACTIVE_THINKING_MARKDOWN)
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
                self.records.append(
                    (message_id, status, stream, topic, result_message_id, error)
                )

            def register_progress_message(
                self,
                bot_handler,
                message_id: int,
                *,
                stream: str,
                topic: str,
                progress_message_id: int | None,
            ) -> None:
                self.progress_messages.append(
                    (message_id, stream, topic, progress_message_id)
                )

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

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
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
        self.assertNotIn("rendering_only", updated_messages[0])
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

    def test_run_worker_turn_posts_continuation_messages_for_long_result(self) -> None:
        sent_replies: list[str] = []
        updated_messages: list[dict[str, object]] = []
        long_result = "\n\n".join(
            f"Section {index}: {'result text ' * 90}" for index in range(12)
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

        class FakeBotHandler:
            def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
                sent_replies.append(response)
                return {"id": 800 + len(sent_replies)}

            def update_message(self, message: dict[str, object]) -> None:
                updated_messages.append(message)

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
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
        all_persisted_text = "\n\n".join(
            [str(updated_messages[-1]["content"]), *sent_replies]
        )
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

        def tool_runner(active_session, task: str, *, event_callback) -> list[dict[str, str]]:
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
            [PEAR_ACTIVE_THINKING_MARKDOWN, f"A {PEAR_ACTIVE_MARKDOWN_PREFIX}", "AB"],
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

            def __call__(self, active_session, task: str, *, event_callback):
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

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
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

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
            prompt = build_pear_codex_prompt(active_session, task)
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

        def tool_runner(active_session, task: str) -> list[dict[str, str]]:
            tool_seen_tasks.append(task)
            tool_seen_threads.append(active_session["latest_codex_thread_id"])
            if len(tool_seen_tasks) == 1:
                return [
                    {"label": "Session", "value": "Codex thread thread-1"},
                    {"label": "Result", "value": "first result"},
                ]
            return [{"label": "Result", "value": "steered result"}]

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

        def tool_runner(active_session, task: str):
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
