import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from re import IGNORECASE, MULTILINE, compile, finditer, search, sub
from typing import Any, Final, Literal, Protocol, TypedDict

from django.core.cache import cache

from zerver.lib.exceptions import JsonableError
from zerver.lib.pear_constants import (
    PEAR_ARTIFACT_PATTERN,
    PEAR_SERVER_CONTRACT_VERSION,
)

PEAR_AGENT_NAME: Final = "Pear"
PEAR_BOT_SHORT_NAME: Final = "pear"
PEAR_EMBEDDED_BOT_SERVICE: Final = "pear"
PEAR_EXPLICIT_MENTION_MESSAGE_KEY: Final = "pear_explicit_mention"
PEAR_TRIGGER_MESSAGE_KEY: Final = "pear_trigger"
PEAR_FOLLOWUP_TRIGGER: Final = "pear_followup"
PEAR_AGENT_MENTION: Final = "@**Pear**"
PEAR_PLAIN_AGENT_MENTION_RE: Final = compile(r"(?<![\w@])@Pear(?![\w-])", IGNORECASE)
PEAR_DEFAULT_STREAM: Final = "pair-programming"
PEAR_TOPIC_PREFIX: Final = "pear/"
PEAR_DEFAULT_TOPIC: Final = "pear/session"
PEAR_SESSION_ENDPOINT: Final = "/pear/session"
PEAR_ACTIVE_SESSION_ENDPOINT: Final = "/pear/session/active"
PEAR_CONFIG_ENDPOINT: Final = "/pear/config"
PEAR_FIRST_RUN_ENDPOINT: Final = "/pear/first-run"
PEAR_TOPIC_CONTEXT_ENDPOINT: Final = "/pear/topic-context"
PEAR_CANVAS_ENDPOINT: Final = "/pear/canvas"
PEAR_RUNS_ENDPOINT: Final = "/pear/runs"
PEAR_SIDE_ENDPOINT: Final = "/pear/side"
PEAR_CANVAS_ASK_ENDPOINT: Final = "/pear/canvas-ask"
PEAR_FIRST_RUN_STREAM: Final = "pear-sandbox"
PEAR_FIRST_RUN_TOPIC: Final = "first run"
PEAR_FIRST_RUN_STARTER_MESSAGE: Final = (
    f"{PEAR_AGENT_MENTION} Start the sandbox session by saying what you can see "
    "from this topic and the current workspace."
)
PEAR_CANVAS_FILE_NAME: Final = "README.md"
# Older workspaces kept the topic doc in a git-excluded CANVAS.md;
# reads fall through to it until a save migrates the content.
PEAR_LEGACY_CANVAS_FILE_NAME: Final = "CANVAS.md"
PEAR_CANVAS_TEMPLATE: Final = """# {stream} > {topic}

## Definition of done

## Notes

## Pinned context

## Routines
"""
PEAR_OFFICIAL_AGENT_RUNTIME: Final = "codex-app-server"
PEAR_OFFICIAL_AGENT_RUNTIME_LABEL: Final = "Codex app-server"
PEAR_AGENT_RUNTIME_PACKAGES: Final = ("openai-agents", "openai-codex")
PEAR_OFFICIAL_CODEX_SDK_PACKAGES: Final = ("@openai/codex-sdk", "openai-codex")
PEAR_CODEX_MCP_TOOLS: Final = ("codex", "codex-reply")
PEAR_TOPIC_STATE_TOOLS: Final = (
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
)
PEAR_TOPIC_STATE_TOOL_NAMES: Final = (
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
)

PEAR_REASONING_EFFORTS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh")
PEAR_URL_RE: Final = r"https?://[^\s<>]+"
PEAR_STEER_PROMPT: Final = "Steering update:"
PEAR_FIX_BUG_PROMPT: Final = "Fix bug:"
PEAR_STATUS_CHECK_PROMPT: Final = "Status check:"
PEAR_HANDOFF_PROMPT: Final = "Handoff summary:"
PEAR_CATCH_UP_PROMPT: Final = "Catch up:"
PEAR_TRIAGE_PROMPT: Final = "Triage request:"
PEAR_FIND_ANSWER_PROMPT: Final = "Find answer:"
PEAR_REVIEW_PROMPT: Final = "Review changes:"
PEAR_PLAN_PROMPT: Final = "Implementation plan:"
PEAR_CHECKPOINT_PROMPT: Final = "Checkpoint:"
PEAR_APPLY_PLAN_PROMPT: Final = "Apply plan:"
PEAR_SAVE_WORK_PROMPT: Final = "Save work:"
PEAR_VERIFY_CHANGES_PROMPT: Final = "Verify changes:"
PEAR_DRAFT_ARTIFACT_PROMPT: Final = "Draft artifact:"
PEAR_ACCESS_PROMPT: Final = "What can you access from this topic?"
PEAR_PAUSE_PROMPT: Final = "Pause session:"
PEAR_RESUME_PROMPT: Final = "Resume session:"
PEAR_SELECTED_BOT_CONFIG_KEY: Final = "pear_selected"
PEAR_SELECTED_BOT_CONFIG_VALUE: Final = "true"
PEAR_WORKER_RUNS_STORAGE_KEY: Final = "pear_worker_runs"
PEAR_WORKER_RUNS_STORAGE_LIMIT: Final = 50
PEAR_MAX_STEERING_SEGMENTS: Final = 4
PEAR_SESSION_CHECKLIST: Final = (
    "Confirm repo, branch, and target runtime.",
    "State the first concrete edit before changing files.",
    "Keep progress visible in this topic.",
    "Post verification evidence before calling the work done.",
)
PEAR_ACTIVE_MARKDOWN_PREFIX: Final = "*✦*"
PEAR_ACTIVE_THINKING_MARKDOWN: Final = f"*Thinking* {PEAR_ACTIVE_MARKDOWN_PREFIX}"
PEAR_VOLATILE_ASSISTANT_FLUSH_INTERVAL_SECONDS: Final = 0.033
PEAR_PERSISTENT_ASSISTANT_FLUSH_INTERVAL_SECONDS: Final = 0.2
PEAR_BACKGROUND_TRANSCRIPT_FLUSH_INTERVAL_SECONDS: Final = 0.5
PEAR_TYPING_REFRESH_INTERVAL_SECONDS: Final = 10.0
PEAR_LIVE_RUN_CACHE_KEY_PREFIX: Final = "pear:live-run:"
PEAR_LIVE_RUN_CACHE_TTL_SECONDS: Final = 3600
PEAR_MARKDOWN_IMAGE_PATTERN: Final = (
    r"!\[([^\]\n]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)"
)
PEAR_CHECKLIST_PATTERN: Final = r"^\s*[-*]\s+\[([ xX~])\]\s+(.+?)\s*$"
# PEAR_ARTIFACT_PATTERN is imported from zerver.lib.pear_constants (vendored
# from pear/registry.py) so the artifact vocabulary cannot drift per fork.
PEAR_RETIRED_VISIBLE_ARTIFACT_PATTERN: Final = (
    r"^\s*(?:[-*]\s*)?"
    r"(Routine disabled|Disabled routine|Routine off|Routine|Routines|"
    r"Routine cadence|Cadence|Schedule|Watch cadence|"
    r"Memory forgotten|Forgotten memory|Memory off|Memory|Memories|"
    r"Done|Definition of done|Success criteria|Ambient|Response policy|"
    r"Followups|Follow-up mode):"
)
PEAR_VISIBLE_ARTIFACT_FALLBACK_PATTERN: Final = (
    r"^\s*(?:[-*]\s*)?(Result):[ \t]*([^\r\n]*?)[ \t]*$"
)
PEAR_TRANSCRIPT_TOOL_DETAIL_KINDS: Final = frozenset(
    {"command", "file_change", "tool"}
)
PEAR_TRIVIAL_ACKNOWLEDGMENT_MESSAGES: Final = frozenset(
    {
        "ack",
        "acknowledged",
        "awesome",
        "cool",
        "got it",
        "great",
        "nice",
        "ok",
        "okay",
        "perfect",
        "sounds good",
        "thank you",
        "thanks",
        "thx",
        "ty",
    }
)
PearChecklistStatus = Literal["pending", "in_progress", "done"]
PearActiveSessionSource = Literal["server", "cache"]
PearActiveSessionState = Literal["not_started", "requested", "active", "paused"]
PearResponsePolicy = Literal["ambient", "mentions_only"]
PEAR_DEFAULT_RESPONSE_POLICY: Final[PearResponsePolicy] = "ambient"
logger = logging.getLogger(__name__)
PearWorkerState = Literal[
    "ready", "blocked_missing_stream", "blocked_paused", "waiting_for_request"
]
PearWorkerRunStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class PearRunCancelled(Exception):
    pass


PearArtifactKind = Literal[
    "repository",
    "runtime",
    "branch",
    "commit",
    "pull_request",
    "diff",
    "verification",
    "screenshot",
    "session_record",
    "document",
    "ticket",
    "reviewers",
]
PearInterruptionKind = Literal["pause", "resume"]
PEAR_ARTIFACT_KIND_BY_LABEL: Final[dict[str, PearArtifactKind]] = {
    "repo": "repository",
    "repository": "repository",
    "runtime": "runtime",
    "branch": "branch",
    "commit": "commit",
    "sha": "commit",
    "pr": "pull_request",
    "pull request": "pull_request",
    "diff": "diff",
    "test": "verification",
    "tests": "verification",
    "verification": "verification",
    "screenshot": "screenshot",
    "session": "session_record",
    "session record": "session_record",
    "pear session": "session_record",
    "codex session": "session_record",
    "canvas": "document",
    "doc": "document",
    "docs": "document",
    "issue": "ticket",
    "ticket": "ticket",
    "reviewer": "reviewers",
    "reviewers": "reviewers",
}


def pear_is_retired_visible_artifact_line(text: str) -> bool:
    return bool(
        search(
            PEAR_RETIRED_VISIBLE_ARTIFACT_PATTERN,
            text.strip(),
            MULTILINE | IGNORECASE,
        )
    )


@dataclass(frozen=True)
class PearSessionDefaults:
    agent_name: str
    agent_mention: str
    stream: str
    topic: str
    topic_prefix: str
    checklist: tuple[str, ...]


class PearSessionPayload(TypedDict):
    agent_name: str
    agent_mention: str
    stream: str
    topic: str
    topic_prefix: str
    checklist: list[str]
    seed_message: str
    steer_message: str
    fix_bug_message: str
    status_check_message: str
    handoff_message: str
    catch_up_message: str
    triage_message: str
    find_answer_message: str
    review_message: str
    plan_message: str
    checkpoint_message: str
    apply_plan_message: str
    save_work_message: str
    verify_changes_message: str
    draft_artifact_message: str
    access_message: str
    pause_message: str
    resume_message: str
    official_agent_runtime: str
    official_agent_runtime_label: str
    agent_runtime_packages: list[str]
    official_codex_sdk_packages: list[str]
    codex_mcp_tools: list[str]
    pear_topic_state_tools: list[str]
    pear_topic_state_tool_names: list[str]
    server_contract_version: int


class PearChecklistItem(TypedDict):
    status: PearChecklistStatus
    text: str


class PearArtifact(TypedDict):
    kind: PearArtifactKind
    label: str
    value: str
    message_id: int


class PearSteeringEvent(TypedDict):
    instruction: str
    message_id: int
    explicitly_addressed: bool


class PearPendingSteeringEvent(TypedDict):
    event_id: int
    message_id: int
    instruction: str


class PearLiveRunState(TypedDict):
    status: str
    content: str
    updated_at: float


class PearInterruptionEvent(TypedDict):
    kind: PearInterruptionKind
    instruction: str
    message_id: int


class PearRecentMessage(TypedDict):
    message_id: int
    sender: str
    sender_is_bot: bool | None
    content: str


class PearWorkspaceContext(TypedDict, total=False):
    repository: str
    remote_url: str
    worktree: str
    workdir: str
    branch: str
    head: str


class PearActiveSessionSummary(TypedDict):
    state: PearActiveSessionState
    stream: str
    topic: str
    source: PearActiveSessionSource
    stream_exists: bool
    topic_message_count: int
    request_count: int
    latest_request_message_id: int | None
    latest_message_id: int | None
    pending_count: int
    in_progress_count: int
    done_count: int
    artifact_count: int
    latest_artifact_message_id: int | None
    latest_verification: str | None
    session_record_count: int
    latest_session_record_message_id: int | None
    latest_session_record: str | None
    latest_session_record_kind: str | None
    latest_session_record_url: str | None
    latest_codex_thread_id: str | None
    response_policy: PearResponsePolicy
    steering_count: int
    latest_steering_message_id: int | None
    latest_steering_instruction: str | None
    interruption_count: int
    latest_interruption_kind: PearInterruptionKind | None
    latest_interruption_message_id: int | None
    latest_interruption_instruction: str | None
    is_paused: bool
    worker_state: PearWorkerState
    worker_can_run: bool
    worker_blocked_reason: str | None
    recent_messages: list[PearRecentMessage]
    checklist: list[PearChecklistItem]
    artifacts: list[PearArtifact]
    steering_events: list[PearSteeringEvent]
    interruption_events: list[PearInterruptionEvent]
    workspace_context: PearWorkspaceContext


class PearBotIdentity(TypedDict):
    bot_user_id: int
    bot_full_name: str
    bot_email: str
    selected: bool


class PearSetupStep(TypedDict):
    key: str
    label: str
    complete: bool
    detail: str


class PearConfigPayload(TypedDict):
    bot_short_name: str
    embedded_bot_service: str
    embedded_bots_enabled: bool
    configured: bool
    enabled: bool
    worker_agent_runtime: str
    worker_service_tier: str | None
    worker_fast_mode: bool
    worker_runtime_ready: bool
    worker_runtime_auth_ready: bool
    worker_runtime_login_method: str | None
    worker_runtime_login_command: str | None
    worker_runtime_login_target: str | None
    worker_runtime_login_instructions: str | None
    bot_count: int
    bot_user_id: int | None
    bot_full_name: str | None
    bot_email: str | None
    selected_bot_user_id: int | None
    selected_bot_full_name: str | None
    selected_bot_email: str | None
    bot_identities: list[PearBotIdentity]
    setup_steps: list[PearSetupStep]


class PearWorkerToolResult(TypedDict):
    label: str
    value: str


class PearTokenUsage(TypedDict, total=False):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    cache_percent: float


class PearWorkerTranscriptEvent(TypedDict, total=False):
    kind: str
    status: str
    title: str
    body: str
    item_id: str
    metadata: dict[str, Any]


PearWorkerTranscriptCallback = Callable[[PearWorkerTranscriptEvent], None]
PearWorkerActiveSessionLoader = Callable[[dict[str, Any], Any], PearActiveSessionSummary]


class PearWorkerToolRunner(Protocol):
    def __call__(
        self,
        active_session: PearActiveSessionSummary,
        task: str,
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
    ) -> Sequence[PearWorkerToolResult]:
        ...


class PearWorkerRunResult(TypedDict):
    should_post: bool
    worker_can_run: bool
    content: str | None
    tool_count: int
    tool_results: list[PearWorkerToolResult]


class PearWorkerRunRecord(TypedDict):
    trigger_message_id: int
    status: PearWorkerRunStatus
    stream: str
    topic: str
    result_message_id: int | None
    error: str | None


class PearWorkerStatusMessage(TypedDict):
    message_id: int
    content: str


PEAR_PERSISTENT_MESSAGE_LIMIT: Final = 5000


def trim_pear_text(value: str, *, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 15, 0)].rstrip()}... [truncated]"


def pear_token_usage_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def pear_normalize_token_usage(value: object) -> PearTokenUsage | None:
    if not isinstance(value, dict):
        return None
    raw_usage: object = value
    token_usage = value.get("tokenUsage")
    if isinstance(token_usage, dict):
        raw_usage = token_usage
    if isinstance(raw_usage, dict):
        last_usage = raw_usage.get("last")
        if isinstance(last_usage, dict):
            raw_usage = last_usage
    if not isinstance(raw_usage, dict):
        return None

    field_names = {
        "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens"),
        "output_tokens": ("output_tokens", "outputTokens", "completion_tokens"),
        "reasoning_output_tokens": (
            "reasoning_output_tokens",
            "reasoningOutputTokens",
        ),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    usage: PearTokenUsage = {}
    for normalized_key, source_keys in field_names.items():
        for source_key in source_keys:
            token_value = pear_token_usage_int(raw_usage.get(source_key))
            if token_value is not None:
                usage[normalized_key] = token_value
                break
    input_tokens = usage.get("input_tokens", 0)
    cached_input_tokens = usage.get("cached_input_tokens", 0)
    if input_tokens > 0:
        usage["cache_percent"] = round(cached_input_tokens * 100 / input_tokens, 1)
    if not usage:
        return None
    return usage


def pear_token_usage_summary(usage: PearTokenUsage) -> str:
    input_tokens = usage.get("input_tokens", 0)
    cached_input_tokens = usage.get("cached_input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    reasoning_output_tokens = usage.get("reasoning_output_tokens", 0)
    cache_percent = usage.get("cache_percent")
    if cache_percent is None and input_tokens > 0:
        cache_percent = round(cached_input_tokens * 100 / input_tokens, 1)
    cache_label = (
        f"{cached_input_tokens:,}/{input_tokens:,} cached ({cache_percent:.1f}%)"
        if cache_percent is not None
        else f"{cached_input_tokens:,}/{input_tokens:,} cached"
    )
    return (
        f"Token usage: {cache_label}; "
        f"{output_tokens:,} output; {reasoning_output_tokens:,} reasoning."
    )


class PearWorkerRunStore(Protocol):
    def start(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
    ) -> bool:
        ...

    def record(
        self,
        bot_handler: Any,
        message_id: int,
        status: PearWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        ...


class PearWorkerDispatcher(Protocol):
    def dispatch(
        self,
        pear_handler: Any,
        bot_handler: Any,
        message: dict[str, Any],
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message_id: int,
        stream: str,
        topic: str,
        requesting_user_id: int | None = None,
        session_kind: str = "shared",
        source_stream: str = "",
        source_topic: str = "",
        source_message_id: int | None = None,
        progress_message_id: int | None = None,
    ) -> None:
        ...


class PearAddressingIntentClassifier(Protocol):
    def __call__(
        self,
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message: dict[str, Any],
        bot_handler: Any,
    ) -> bool:
        ...


def get_pear_session_defaults() -> PearSessionDefaults:
    return PearSessionDefaults(
        agent_name=PEAR_AGENT_NAME,
        agent_mention=PEAR_AGENT_MENTION,
        stream=PEAR_DEFAULT_STREAM,
        topic=PEAR_DEFAULT_TOPIC,
        topic_prefix=PEAR_TOPIC_PREFIX,
        checklist=PEAR_SESSION_CHECKLIST,
    )


def build_pear_checklist_lines() -> list[str]:
    return [f"- [ ] {item}" for item in PEAR_SESSION_CHECKLIST]


def build_pear_seed_message(task: str = "") -> str:
    task = task.strip()
    task_line = task if task else "Start the topic Codex thread:"
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {task_line}",
            "",
            *build_pear_checklist_lines(),
        ]
    )


def build_pear_steer_message(instruction: str = "") -> str:
    instruction = instruction.strip()
    if instruction:
        return f"{PEAR_AGENT_MENTION} {PEAR_STEER_PROMPT}\n\nRequest: {instruction}"
    return f"{PEAR_AGENT_MENTION} {PEAR_STEER_PROMPT}\n\nRequest: "


def build_pear_fix_bug_message(summary: str = "") -> str:
    summary = summary.strip()
    bug_line = f"Bug: {summary}" if summary else "Bug: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_FIX_BUG_PROMPT}",
            "",
            bug_line,
            "Observed behavior: ",
            "Expected behavior: ",
            "Useful files: ",
            "How to verify: ",
            "Workspace context: use the latest Pear workspace artifacts in this topic, or ask if repo/branch/runtime is missing.",
        ]
    )


def build_pear_status_check_message(focus: str = "") -> str:
    focus = focus.strip()
    focus_line = f"Focus: {focus}" if focus else "Focus: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_STATUS_CHECK_PROMPT}",
            "",
            focus_line,
            "Completed since last update: ",
            "Still in progress: ",
            "Blocked or needs decision: ",
            "Next concrete step: ",
            "Checks to run: ",
        ]
    )


def build_pear_handoff_message(audience: str = "") -> str:
    audience = audience.strip()
    audience_line = f"Audience: {audience}" if audience else "Audience: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_HANDOFF_PROMPT}",
            "",
            audience_line,
            "Goal and scope: ",
            "Current state: ",
            "Key files and artifacts: ",
            "Open decisions: ",
            "Next steps: ",
            "Checks still needed: ",
        ]
    )


def build_pear_catch_up_message(since: str = "") -> str:
    since = since.strip()
    since_line = f"Since: {since}" if since else "Since: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_CATCH_UP_PROMPT}",
            "",
            since_line,
            "Summarize changes: ",
            "Decisions made: ",
            "Artifacts to inspect: ",
            "Current blockers: ",
            "Next actions: ",
            "Use the active topic context and latest Pear workspace artifacts.",
        ]
    )


def build_pear_triage_message(request: str = "") -> str:
    request = request.strip()
    request_line = f"Request: {request}" if request else "Request: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_TRIAGE_PROMPT}",
            "",
            request_line,
            "Possible duplicate or related work: check this topic's artifacts, canvas, linked PRs, and linked issues.",
            "Answer if already known: summarize the settled answer or decision from this topic.",
            "Suggested owner or reviewer: infer from Reviewers, PR, Ticket, or Issue artifacts when possible.",
            "Suggested scope: classify as bug, feature, verification, docs, or follow-up work.",
            "Priority and risk: ",
            "Next action: ",
            "Durable output: include Ticket, Issue, Reviewers, Doc, or Verification artifact lines only when they are concrete.",
        ]
    )


def build_pear_find_answer_message(question: str = "") -> str:
    question = question.strip()
    question_line = f"Question: {question}" if question else "Question: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_FIND_ANSWER_PROMPT}",
            "",
            question_line,
            "Search scope: use this topic first, then durable Pear artifacts, linked docs, tickets, PRs, and repo files when workspace context is available.",
            "Evidence to cite: include message links, Doc, Ticket, Issue, PR, file paths, or commands only when inspected.",
            "Answer policy: if the evidence is incomplete, say what is missing instead of guessing.",
            "Durable output: include Doc, Ticket, Issue, PR, or Verification artifact lines only when they point to concrete evidence.",
        ]
    )


def build_pear_review_message(scope: str = "") -> str:
    scope = scope.strip()
    scope_line = f"Scope: {scope}" if scope else "Scope: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_REVIEW_PROMPT}",
            "",
            scope_line,
            "Review focus: correctness, regressions, test coverage, and integration risk.",
            "Diff or files to inspect: use the latest Pear workspace artifacts unless specified here.",
            "Risk areas: ",
            "Expected output: prioritized findings with file paths, then residual risk.",
            "Verification to consider: ",
        ]
    )


def build_pear_plan_message(goal: str = "") -> str:
    goal = goal.strip()
    goal_line = f"Goal: {goal}" if goal else "Goal: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_PLAN_PROMPT}",
            "",
            goal_line,
            "Constraints: ",
            "Files to inspect: ",
            "Proposed steps: ",
            "Risks and unknowns: ",
            "Verification plan: ",
            "Do not edit files yet; propose the plan and wait for confirmation unless this topic already asks you to implement.",
        ]
    )


def build_pear_checkpoint_message(focus: str = "") -> str:
    focus = focus.strip()
    focus_line = f"Focus: {focus}" if focus else "Focus: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_CHECKPOINT_PROMPT}",
            "",
            focus_line,
            "Post: current branch, diff summary, changed files, verification run, blockers, and next step.",
            "If code changed, include Diff and Verification artifact lines in the reply.",
            "Continue policy: wait for confirmation unless this topic explicitly says to keep going.",
        ]
    )


def build_pear_apply_plan_message(scope: str = "") -> str:
    scope = scope.strip()
    scope_line = f"Scope: {scope}" if scope else "Scope: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_APPLY_PLAN_PROMPT}",
            "",
            scope_line,
            "Plan source: use the latest Implementation plan in this topic unless a different plan is pasted here.",
            "Implementation: make the scoped edits now, keep the change focused, and preserve existing behavior outside scope.",
            "Before finishing: run the planned verification or explain the blocker.",
            "Post: branch, diff summary, verification evidence, and next step.",
        ]
    )


def build_pear_save_work_message(scope: str = "") -> str:
    scope = scope.strip()
    scope_line = f"Scope: {scope}" if scope else "Scope: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_SAVE_WORK_PROMPT}",
            "",
            scope_line,
            "Persist: preserve the current work as a branch, draft PR, patch, or explicit diff summary that this Zulip topic can recover from.",
            "Before continuing: post Branch, PR, Diff, or Doc artifact lines for the saved state.",
            "Latest check to include: the verification that proves the saved state is usable, or explain the blocker.",
            "Continue policy: keep going only after the work is recoverable from this topic.",
        ]
    )


def build_pear_verify_changes_message(target: str = "") -> str:
    target = target.strip()
    target_line = f"Target: {target}" if target else "Target: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_VERIFY_CHANGES_PROMPT}",
            "",
            target_line,
            "Checks to run: use the planned verification or the most relevant local test, lint, typecheck, or smoke command.",
            "Fix policy: if a check fails, diagnose it, make focused fixes, and rerun the smallest useful check.",
            "Evidence to post: commands run, pass/fail result, remaining blockers, and diff summary.",
        ]
    )


def build_pear_draft_artifact_message(artifact: str = "") -> str:
    artifact = artifact.strip()
    artifact_line = f"Artifact: {artifact}" if artifact else "Artifact: "
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_DRAFT_ARTIFACT_PROMPT}",
            "",
            artifact_line,
            "Source context: use this Zulip topic, the latest Pear artifacts, and current workspace context.",
            "Audience: ",
            "Must include: scope, decisions, open questions, owners, and next action.",
            "Posting policy: post the draft here, or create/update the artifact if tool access is available.",
            "Durable output: include a Doc, Ticket, or Issue artifact line when a URL or path exists.",
        ]
    )


def build_pear_access_message() -> str:
    return f"{PEAR_AGENT_MENTION} {PEAR_ACCESS_PROMPT}"


def build_pear_pause_message(reason: str = "") -> str:
    reason = reason.strip()
    if reason:
        return f"{PEAR_AGENT_MENTION} {PEAR_PAUSE_PROMPT}\n\nReason: {reason}"
    return f"{PEAR_AGENT_MENTION} {PEAR_PAUSE_PROMPT}\n\nReason: "


def build_pear_resume_message(instruction: str = "") -> str:
    instruction = instruction.strip()
    if instruction:
        return f"{PEAR_AGENT_MENTION} {PEAR_RESUME_PROMPT}\n\nRequest: {instruction}"
    return f"{PEAR_AGENT_MENTION} {PEAR_RESUME_PROMPT}\n\nRequest: "


def pear_worker_task_line(task: str = "") -> str:
    task = clean_pear_task_text(task)
    return task if task else "Continue the topic Codex thread."


def pear_transcript_status_label(status: str) -> str:
    if status == "queued":
        return "queued"
    if status == "running":
        return "working..."
    if status == "done":
        return "done"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return status or "working..."


def pear_transcript_checkbox(status: str) -> str:
    if status in {"done", "completed"}:
        return "[x]"
    return "[ ]"


def pear_transcript_tool_mark(status: str) -> str:
    if status in {"done", "completed"}:
        return "·"
    if status in {"failed", "error"}:
        return "✷"
    if status in {"running", "in_progress"}:
        return "✦"
    return "⋅"


def pear_pluralize_tool(count: int) -> str:
    return "tool" if count == 1 else "tools"


def pear_transcript_event_summary(event: PearWorkerTranscriptEvent) -> str:
    kind = event.get("kind", "")
    status = event.get("status", "")
    title = event.get("title", "").strip()
    body = event.get("body", "").strip()
    if kind == "plan":
        return ""
    if kind == "session":
        return f"- {pear_transcript_checkbox(status)} Session: {title or body}"
    if kind == "command":
        label = "Ran" if status in {"done", "completed"} else "Running"
        if status in {"failed", "error"}:
            label = "Failed"
        return f"- {pear_transcript_checkbox(status)} {label}: `{trim_pear_text(title or body, limit=160)}`"
    if kind == "file_change":
        return f"- {pear_transcript_checkbox(status)} File: {trim_pear_text(title or body, limit=160)}"
    if kind == "tool":
        label = "Tool"
        if status in {"running", "in_progress"}:
            label = "Tool running"
        elif status in {"failed", "error"}:
            label = "Tool failed"
        return f"- {pear_transcript_checkbox(status)} {label}: {trim_pear_text(title or body, limit=160)}"
    if kind == "reasoning":
        return f"- {pear_transcript_checkbox(status)} Thinking: {trim_pear_text(title or body, limit=160)}"
    if kind == "error":
        error_text = trim_pear_text(body or title, limit=2000)
        if title and body and title != body:
            return f"{title}\n\nError: {error_text}"
        return error_text or "Pear worker failed."
    if kind == "status":
        return f"- {pear_transcript_checkbox(status)} {trim_pear_text(title or body, limit=180)}"
    return f"- {pear_transcript_checkbox(status)} {trim_pear_text(title or body, limit=180)}"


def pear_transcript_event_key(
    event: PearWorkerTranscriptEvent,
    *,
    anonymous_index: int,
) -> str:
    item_id = event.get("item_id")
    if isinstance(item_id, str) and item_id.strip():
        return f"item:{item_id.strip()}"
    return f"event:{anonymous_index}"


def pear_transcript_event_is_tool_detail(event: PearWorkerTranscriptEvent) -> bool:
    return event.get("kind", "") in PEAR_TRANSCRIPT_TOOL_DETAIL_KINDS


def pear_transcript_tool_detail_line(event: PearWorkerTranscriptEvent) -> str:
    kind = event.get("kind", "")
    status = event.get("status", "")
    title = event.get("title", "").strip()
    body = event.get("body", "").strip()
    mark = pear_transcript_tool_mark(status)
    subject = trim_pear_text(title or body, limit=220)

    if kind == "command":
        return f"- {mark} `{subject or 'command'}`"
    if kind == "file_change":
        return f"- {mark} {subject or 'file change'}"
    if kind == "tool":
        return f"- {mark} {subject or 'tool'}"
    if kind == "error":
        return f"- {mark} Error: {subject or 'Pear worker failed.'}"
    return pear_transcript_event_summary(event)


def pear_transcript_tool_group_title(
    events: Sequence[PearWorkerTranscriptEvent],
) -> str:
    tool_count = len(events)
    completed_count = sum(
        1 for event in events if event.get("status", "") in {"done", "completed"}
    )
    running_count = sum(
        1 for event in events if event.get("status", "") in {"running", "in_progress"}
    )
    failed_count = sum(
        1 for event in events if event.get("status", "") in {"failed", "error"}
    )

    if failed_count:
        return f"{failed_count} failed {pear_pluralize_tool(failed_count)}"
    if running_count and completed_count:
        return f"Ran {completed_count}/{tool_count} {pear_pluralize_tool(tool_count)} ✦"
    if running_count:
        return f"Running {tool_count} {pear_pluralize_tool(tool_count)} ✦"
    return f"Ran {tool_count} {pear_pluralize_tool(tool_count)}"


def pear_spoiler_block(title: str, body: str) -> str:
    safe_title = title.replace("`", "'").replace("~", "-").strip() or "Details"
    safe_body = body.replace("```", "` ` `").strip()
    return f"```spoiler {safe_title}\n{safe_body}\n```"


def pear_transcript_tool_group_block(
    events: Sequence[PearWorkerTranscriptEvent],
) -> str:
    if not events:
        return ""
    return pear_spoiler_block(
        pear_transcript_tool_group_title(events),
        "\n".join(pear_transcript_tool_detail_line(event) for event in events),
    )


def pear_transcript_event_from_tool_result(
    result: PearWorkerToolResult,
) -> PearWorkerTranscriptEvent:
    label = result["label"].strip()
    value = result["value"].strip()
    kind = "verification" if label.lower() == "verification" else "status"
    if label.lower() == "session":
        kind = "session"
    return {
        "kind": kind,
        "status": "done",
        "title": label,
        "body": value,
    }


def pear_visible_assistant_artifact_fallback_text(
    text: str,
    *,
    limit: int | None = PEAR_PERSISTENT_MESSAGE_LIMIT,
) -> str:
    fallback_by_label: dict[str, str] = {}
    for match in finditer(
        PEAR_VISIBLE_ARTIFACT_FALLBACK_PATTERN,
        text,
        MULTILINE | IGNORECASE,
    ):
        label = str(match.group(1)).strip().lower()
        value = str(match.group(2)).strip()
        if value:
            fallback_by_label[label] = value
    if "result" in fallback_by_label:
        value = normalize_pear_visible_markdown(fallback_by_label["result"])
        if limit is None:
            return value
        return trim_pear_text(value, limit=limit)
    return ""


def normalize_pear_visible_markdown(text: str) -> str:
    def replacement(match: Any) -> str:
        label = str(match.group(1)).strip() or "image"
        url = str(match.group(2)).strip()
        return f"[{label}]({url})"

    return sub(PEAR_MARKDOWN_IMAGE_PATTERN, replacement, text)


def pear_visible_assistant_text(
    text: str,
    *,
    limit: int | None = PEAR_PERSISTENT_MESSAGE_LIMIT,
) -> str:
    visible_lines: list[str] = []
    previous_blank = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if visible_lines and not previous_blank:
                visible_lines.append("")
            previous_blank = True
            continue
        if search(
            PEAR_ARTIFACT_PATTERN, stripped, MULTILINE | IGNORECASE
        ) or search(
            PEAR_VISIBLE_ARTIFACT_FALLBACK_PATTERN,
            stripped,
            MULTILINE | IGNORECASE,
        ) or pear_is_retired_visible_artifact_line(stripped):
            previous_blank = True
            continue
        visible_lines.append(line.rstrip())
        previous_blank = False

    while visible_lines and visible_lines[-1] == "":
        visible_lines.pop()
    visible_text = normalize_pear_visible_markdown("\n".join(visible_lines).strip())
    if limit is not None:
        visible_text = trim_pear_text(visible_text, limit=limit)
    if visible_text:
        return visible_text
    return pear_visible_assistant_artifact_fallback_text(text, limit=limit)


def pear_visible_transcript_event_block(
    event: PearWorkerTranscriptEvent,
    *,
    limit: int | None = PEAR_PERSISTENT_MESSAGE_LIMIT,
) -> str:
    kind = event.get("kind", "")
    if kind in {"reasoning", "session", "status", "usage"}:
        return ""
    if kind == "steering":
        return PEAR_STEERING_APPLIED_MARKDOWN
    if kind == "assistant":
        body = event.get("body", "")
        if not body.strip():
            return ""
        return pear_visible_assistant_text(body, limit=limit)
    if kind == "plan":
        # The plan is run-scoped process state, not prose: it renders
        # in dedicated UI (the rail's plan card, from the run payload),
        # never into the durable message record — matching how the
        # codex TUI keeps its checklist out of the final answer.
        return ""
    return pear_transcript_event_summary(event)


def pear_visible_transcript_blocks(
    transcript_events: Sequence[PearWorkerTranscriptEvent],
    *,
    include_transient_status: bool = True,
    block_limit: int | None = PEAR_PERSISTENT_MESSAGE_LIMIT,
) -> list[str]:
    block_by_key: dict[str, str] = {}
    block_keys: list[str] = []
    anonymous_index = 0
    tool_event_by_key: dict[str, PearWorkerTranscriptEvent] = {}
    tool_event_keys: list[str] = []

    def flush_tool_events() -> None:
        nonlocal tool_event_by_key, tool_event_keys
        if not tool_event_keys:
            return
        block = pear_transcript_tool_group_block(
            [tool_event_by_key[key] for key in tool_event_keys]
        )
        if block:
            key = f"tool-group:{len(block_keys)}"
            block_keys.append(key)
            block_by_key[key] = block
        tool_event_by_key = {}
        tool_event_keys = []

    for event in transcript_events:
        if not include_transient_status and event.get("kind") == "steering":
            continue
        if pear_transcript_event_is_tool_detail(event):
            key = pear_transcript_event_key(event, anonymous_index=len(tool_event_keys))
            if key not in tool_event_by_key:
                tool_event_keys.append(key)
            tool_event_by_key[key] = event
            continue

        block = pear_visible_transcript_event_block(event, limit=block_limit).strip()
        if not block:
            if not isinstance(event.get("item_id"), str):
                anonymous_index += 1
            continue
        flush_tool_events()
        key = pear_transcript_event_key(event, anonymous_index=anonymous_index)
        if key.startswith("event:"):
            anonymous_index += 1
        if key in block_by_key:
            block_by_key[key] = block
            continue
        if block_keys and block_by_key[block_keys[-1]] == block:
            continue
        block_keys.append(key)
        block_by_key[key] = block
    flush_tool_events()
    return [block_by_key[key] for key in block_keys]


PEAR_IMAGE_EMBED_LIMIT: Final = 3
PEAR_IMAGE_EMBED_MAX_BYTES: Final = 4_000_000
PEAR_IMAGE_EMBED_PATTERN: Final = compile(
    r"`([A-Za-z0-9_\-./]+\.(?:png|jpe?g|gif|webp))`", IGNORECASE
)
PEAR_IMAGE_CONTENT_TYPES: Final = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def pear_embed_workspace_images(
    content: str,
    *,
    workdir: str,
    isolation_username: str | None,
    bot_profile: Any,
) -> str:
    """Upload worktree images the reply references and append preview
    links, so plots actually render in the topic instead of being
    unreachable filesystem paths. Bounded and best-effort."""
    if not workdir or "`" not in content:
        return content
    # Lazy imports: pear_worker imports this module at load time.
    from pathlib import Path

    from zerver.lib.pear_worker import pear_read_bytes_path, pear_workspace_relative_path
    from zerver.lib.upload import upload_message_attachment

    seen: set[str] = set()
    links: list[str] = []
    for match in PEAR_IMAGE_EMBED_PATTERN.finditer(content):
        relative_path = match.group(1)
        if relative_path in seen:
            continue
        seen.add(relative_path)
        if len(links) >= PEAR_IMAGE_EMBED_LIMIT:
            break
        resolved = pear_workspace_relative_path(workdir, relative_path)
        if resolved is None:
            continue
        try:
            data = pear_read_bytes_path(
                resolved,
                isolation_username=isolation_username,
                max_bytes=PEAR_IMAGE_EMBED_MAX_BYTES,
            )
        except OSError:
            continue
        if not data or len(data) > PEAR_IMAGE_EMBED_MAX_BYTES:
            continue
        file_name = Path(relative_path).name
        content_type = PEAR_IMAGE_CONTENT_TYPES.get(Path(file_name).suffix.lower())
        if content_type is None:
            continue
        try:
            url, uploaded_name = upload_message_attachment(
                file_name, content_type, data, bot_profile
            )
        except Exception:
            logger.warning("Pear image embed upload failed for %s.", relative_path)
            continue
        links.append(f"[{uploaded_name}]({url})")
    if links:
        return content.rstrip() + "\n\n" + "\n".join(links)
    return content


def pear_append_active_marker(text: str) -> str:
    text = text.rstrip()
    if not text:
        return PEAR_ACTIVE_THINKING_MARKDOWN
    if text.endswith(PEAR_ACTIVE_MARKDOWN_PREFIX):
        return text
    # A streaming snapshot can end inside an unclosed code fence;
    # appending the marker there would glue a ✦ into the code (or
    # math) source. Skip the marker for that tick - the tool chips
    # still carry liveness.
    if len(re.findall(r"^\s{0,3}(?:```|~~~)", text, MULTILINE)) % 2 == 1:
        return text
    if text.endswith("```"):
        return f"{text}\n\n{PEAR_ACTIVE_MARKDOWN_PREFIX}"
    return f"{text} {PEAR_ACTIVE_MARKDOWN_PREFIX}"


PEAR_STEERING_QUEUED_MARKDOWN: Final = "*Steering queued.*"
PEAR_STEERING_APPLIED_MARKDOWN: Final = "*Steering applied.*"
PEAR_LEGACY_STEERING_QUEUED_MARKDOWN: Final = "_Steering queued._"
PEAR_LEGACY_STEERING_APPLIED_MARKDOWN: Final = "_Steering applied._"
PEAR_STEERING_STATUS_MARKDOWN: Final = frozenset(
    (
        PEAR_STEERING_QUEUED_MARKDOWN,
        PEAR_STEERING_APPLIED_MARKDOWN,
        PEAR_LEGACY_STEERING_QUEUED_MARKDOWN,
        PEAR_LEGACY_STEERING_APPLIED_MARKDOWN,
    )
)


def pear_without_active_marker(text: str) -> str:
    text = text.rstrip()
    if text.endswith(PEAR_ACTIVE_MARKDOWN_PREFIX):
        return text[: -len(PEAR_ACTIVE_MARKDOWN_PREFIX)].rstrip()
    return text


def pear_without_steering_status(text: str) -> str:
    lines = []
    for line in text.splitlines():
        clean_line = pear_without_active_marker(line).strip()
        if clean_line in PEAR_STEERING_STATUS_MARKDOWN:
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).rstrip()


def pear_with_steering_status(content: str, status_markdown: str) -> str:
    base_content = pear_without_active_marker(pear_without_steering_status(content))
    if base_content:
        return pear_append_active_marker(f"{base_content}\n\n{status_markdown}")
    return pear_append_active_marker(status_markdown)


def pear_result_tool_text(
    tool_results: Sequence[PearWorkerToolResult],
    *,
    limit: int | None = PEAR_PERSISTENT_MESSAGE_LIMIT,
) -> str:
    for result in tool_results:
        if result["label"].strip().lower() == "result":
            value = normalize_pear_visible_markdown(result["value"].strip())
            if limit is None:
                return value
            return trim_pear_text(value, limit=limit)
    return ""


def pear_diagnostic_tool_text(tool_results: Sequence[PearWorkerToolResult]) -> str:
    for result in tool_results:
        if result["label"].strip().lower() not in {"error", "diagnostic"}:
            continue
        value = trim_pear_text(result["value"].strip(), limit=5000)
        if value:
            return value
    for result in tool_results:
        if result["label"].strip().lower() != "verification":
            continue
        value = trim_pear_text(result["value"].strip(), limit=5000)
        normalized = value.lower()
        if (
            "runtime is disabled" in normalized
            or "missing credentials" in normalized
            or "without a final text response" in normalized
        ):
            return value
    return ""


def pear_visible_assistant_texts(
    transcript_events: Sequence[PearWorkerTranscriptEvent],
) -> list[str]:
    visible_text_by_key: dict[str, str] = {}
    visible_text_keys: list[str] = []
    anonymous_index = 0
    for event in transcript_events:
        if event.get("kind") != "assistant":
            continue
        body = event.get("body", "")
        if not body.strip():
            continue
        visible_text = pear_visible_assistant_text(body)
        if not visible_text:
            continue
        item_id = event.get("item_id")
        if isinstance(item_id, str) and item_id.strip():
            key = f"item:{item_id.strip()}"
            if key not in visible_text_by_key:
                visible_text_keys.append(key)
            visible_text_by_key[key] = visible_text
            continue
        if visible_text_keys and visible_text_by_key[visible_text_keys[-1]] == visible_text:
            continue
        key = f"event:{anonymous_index}"
        anonymous_index += 1
        visible_text_keys.append(key)
        visible_text_by_key[key] = visible_text
    return [visible_text_by_key[key] for key in visible_text_keys]


def pear_visible_response_text(
    *,
    status: PearWorkerRunStatus,
    assistant_texts: Sequence[str],
    tool_results: Sequence[PearWorkerToolResult],
) -> str:
    assistant_text = trim_pear_text("\n\n".join(assistant_texts).strip(), limit=5000)
    if assistant_text:
        return assistant_text
    result_text = pear_result_tool_text(tool_results)
    if result_text:
        return result_text
    diagnostic_text = pear_diagnostic_tool_text(tool_results)
    if diagnostic_text:
        return diagnostic_text
    if status == "failed":
        return "Pear worker failed."
    if status == "cancelled":
        return "Pear worker cancelled."
    if status == "done":
        return "Pear worker completed without a visible response."
    return ""


def pear_join_visible_transcript_blocks(
    blocks: Sequence[str],
    *,
    preserve_recent: bool,
    limit: int = 5000,
) -> str:
    clean_blocks = [block.strip() for block in blocks if block.strip()]
    text = "\n\n".join(clean_blocks)
    if len(text) <= limit:
        return text
    if not preserve_recent:
        return trim_pear_text(text, limit=limit)

    omitted_marker = "[earlier progress details truncated]"
    selected_reversed: list[str] = []
    for block in reversed(clean_blocks):
        candidate_blocks = [omitted_marker, block, *reversed(selected_reversed)]
        candidate = "\n\n".join(candidate_blocks)
        if len(candidate) <= limit:
            selected_reversed.append(block)
            continue
        if not selected_reversed:
            available = max(limit - len(omitted_marker) - 2, 120)
            selected_reversed.append(trim_pear_text(block, limit=available))
        break

    return "\n\n".join([omitted_marker, *reversed(selected_reversed)])


def build_pear_live_transcript_message(
    *,
    task: str,
    status: PearWorkerRunStatus,
    transcript_events: Sequence[PearWorkerTranscriptEvent] = (),
    tool_results: Sequence[PearWorkerToolResult] = (),
) -> str:
    transcript_blocks = pear_visible_transcript_blocks(
        transcript_events,
        include_transient_status=status in {"queued", "running"},
    )
    if transcript_blocks:
        if status not in {"queued", "running"}:
            result_text = pear_result_tool_text(tool_results)
            if result_text and result_text not in transcript_blocks:
                transcript_blocks.append(result_text)
        visible_response = pear_join_visible_transcript_blocks(
            transcript_blocks,
            preserve_recent=status not in {"queued", "running"},
        )
        if status in {"queued", "running"}:
            return pear_append_active_marker(visible_response)
        return visible_response
    assistant_texts = pear_visible_assistant_texts(transcript_events)
    visible_response = pear_visible_response_text(
        status=status,
        assistant_texts=assistant_texts,
        tool_results=tool_results,
    )
    if status in {"queued", "running"}:
        if visible_response:
            return pear_append_active_marker(visible_response)
        return PEAR_ACTIVE_THINKING_MARKDOWN
    lines = [visible_response]
    return "\n".join(line for line in lines if line)


def pear_live_run_cache_key(trigger_message_id: int) -> str:
    return f"{PEAR_LIVE_RUN_CACHE_KEY_PREFIX}{trigger_message_id}"


def write_pear_live_run_state(
    trigger_message_id: int,
    *,
    status: PearWorkerRunStatus,
    content: str,
) -> None:
    state: PearLiveRunState = {
        "status": status,
        "content": content,
        "updated_at": time.time(),
    }
    cache.set(
        pear_live_run_cache_key(trigger_message_id),
        state,
        timeout=PEAR_LIVE_RUN_CACHE_TTL_SECONDS,
    )


def get_pear_live_run_state(trigger_message_id: int) -> PearLiveRunState | None:
    state = cache.get(pear_live_run_cache_key(trigger_message_id))
    if not isinstance(state, dict):
        return None
    status = state.get("status")
    content = state.get("content")
    updated_at = state.get("updated_at")
    if not isinstance(status, str) or not isinstance(content, str):
        return None
    if not isinstance(updated_at, (int, float)):
        return None
    return {
        "status": status,
        "content": content,
        "updated_at": float(updated_at),
    }


def build_pear_ack_message(task: str = "") -> str:
    return "\n".join(
        [
            build_pear_live_transcript_message(
                task=task,
                status="queued",
            )
        ]
    )


def build_pear_steering_ack_message(_instruction: str = "") -> str:
    return "Steering queued."


def build_pear_steering_task(
    steering_events: Sequence[PearPendingSteeringEvent],
) -> str:
    instructions = [
        event["instruction"].strip()
        for event in steering_events
        if event["instruction"].strip()
    ]
    if not instructions:
        return "Steering update: continue the active task."
    return "\n".join(["Steering update:", *[f"- {instruction}" for instruction in instructions]])


def build_pear_pause_ack_message(reason: str = "") -> str:
    reason = reason.strip()
    lines = [
        "Paused. I'll treat this Pear session as paused until someone resumes it here.",
    ]
    if reason:
        lines.extend(["", f"Reason: {reason}"])
    return "\n".join(lines)


def build_pear_resume_ack_message(instruction: str = "") -> str:
    instruction = instruction.strip()
    lines = [
        "Resuming. I'll continue the active Pear session from this topic.",
    ]
    if instruction:
        lines.extend(["", f"Request: {instruction}"])
    return "\n".join(lines)


def pear_access_participants(active_session: PearActiveSessionSummary | None) -> str:
    if active_session is None:
        return "unknown until the topic transcript loads"
    participants: list[str] = []
    for message in active_session["recent_messages"]:
        sender = message["sender"].strip()
        if sender and sender not in participants:
            participants.append(sender)
    if not participants:
        return "none in the loaded recent transcript"
    if len(participants) > 8:
        visible_participants = ", ".join(participants[:8])
        return f"{visible_participants}, and {len(participants) - 8} more"
    return ", ".join(participants)


def pear_access_workspace_line(
    active_session: PearActiveSessionSummary | None,
) -> str:
    if active_session is None:
        return "Workspace: unknown until a Pear request starts"
    workspace_context = active_session["workspace_context"]
    if not workspace_context:
        return "Workspace: no topic workspace has been pinned or loaded yet"
    repository = workspace_context.get("repository") or "unlabeled"
    workdir = workspace_context.get("workdir") or workspace_context.get("worktree") or "unknown"
    branch = workspace_context.get("branch") or "unknown"
    head = workspace_context.get("head") or "unknown"
    return f"Workspace: {repository} at {workdir} on {branch} ({head})"


def build_pear_access_ack_message(
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    stream = active_session["stream"] if active_session is not None else PEAR_DEFAULT_STREAM
    topic = active_session["topic"] if active_session is not None else PEAR_DEFAULT_TOPIC
    worker_state = (
        active_session["worker_state"] if active_session is not None else "unknown"
    )
    if active_session is not None and active_session["worker_can_run"]:
        worker_access = "ready"
    elif active_session is not None and active_session["worker_blocked_reason"]:
        worker_access = active_session["worker_blocked_reason"]
    else:
        worker_access = "unknown until a Pear request starts"
    artifact_count = active_session["artifact_count"] if active_session is not None else 0
    response_policy = (
        active_session["response_policy"] if active_session is not None else "unknown"
    )
    latest_verification = (
        active_session["latest_verification"] or "none"
        if active_session is not None
        else "none"
    )
    topic_message_count = (
        active_session["topic_message_count"] if active_session is not None else 0
    )
    recent_message_count = (
        len(active_session["recent_messages"]) if active_session is not None else 0
    )
    codex_thread_id = (
        active_session["latest_codex_thread_id"] or "none yet"
        if active_session is not None
        else "unknown"
    )
    return "\n".join(
        [
            f"I can work from this Zulip topic: {stream} > {topic}.",
            "",
            "Pear can read the durable topic context, including the request, "
            "checklist, recent topic transcript, steering replies, pause/resume controls, "
            "the topic canvas, Codex-native memories and skills, and artifact "
            "lines such as Repo, Runtime, Branch, PR, Diff, Verification, "
            "Screenshot, Doc, Ticket, Issue, Reviewers, and Session.",
            "When the server worker is enabled, Pear runs the configured Codex "
            "runtime with the server's explicit workdir, sandbox, approval "
            "policy, and model settings.",
            "zulip-terminal can act on posted artifacts: open links, copy values, "
            "check out branches, open compares, create draft PRs, mark PRs "
            "ready, post PR status, and request reviewers.",
            "",
            f"Loaded transcript: {recent_message_count} recent message(s) from {topic_message_count} topic message(s).",
            f"Visible senders: {pear_access_participants(active_session)}",
            pear_access_workspace_line(active_session),
            f"Codex thread: {codex_thread_id}",
            f"Worker state: {worker_state}",
            f"Worker access: {worker_access}",
            f"Artifacts seen: {artifact_count}",
            f"Response policy: {response_policy}",
            f"Latest verification: {latest_verification}",
        ]
    )


def normalize_pear_response_policy(value: str | None) -> PearResponsePolicy:
    if value is None:
        return PEAR_DEFAULT_RESPONSE_POLICY
    normalized = " ".join(value.lower().replace("-", " ").split())
    if any(
        phrase in normalized
        for phrase in (
            "mention",
            "tagged",
            "tag only",
            "quiet",
            "off",
            "disabled",
            "false",
            "manual",
        )
    ):
        return "mentions_only"
    return "ambient"


def clean_pear_task_text(content: str = "") -> str:
    task = content.strip()
    task = task.replace(PEAR_AGENT_MENTION, " ")
    task = PEAR_PLAIN_AGENT_MENTION_RE.sub(" ", task)
    return " ".join(task.split())


def normalize_pear_explicit_request_content(
    content: str = "",
    *,
    explicitly_addressed: bool = False,
) -> str:
    content = content.strip()
    if explicitly_addressed and not is_pear_request_message(content):
        return f"@Pear {content}".strip()
    return content


def is_pear_thread_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    if is_pear_interruption_template_message(content):
        return PEAR_RESUME_PROMPT in content
    if (
        is_pear_steering_template_message(content)
        or is_pear_access_request_message(content)
    ):
        return False
    if is_pear_trivial_acknowledgment_message(content):
        return False
    return True


def is_pear_task_request_message(content: str) -> bool:
    return is_pear_thread_request_message(content) and not is_pear_interruption_template_message(
        content
    )


def is_pear_trivial_acknowledgment_message(content: str) -> bool:
    normalized = sub(r"[^a-z0-9]+", " ", clean_pear_task_text(content).lower()).strip()
    normalized = " ".join(normalized.split())
    return normalized in PEAR_TRIVIAL_ACKNOWLEDGMENT_MESSAGES


def pear_session_record_url(value: str | None) -> str | None:
    if value is None:
        return None
    match = search(PEAR_URL_RE, value)
    if match is None:
        return None
    return match.group(0).rstrip(".,);]")


def pear_session_record_kind(value: str | None) -> str | None:
    if value is None:
        return None
    if pear_session_record_url(value) is not None:
        return "url"
    if value.lower().startswith("codex thread "):
        return "codex_thread"
    return "text"


def pear_worker_task_for_message(
    content: str,
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    del active_session
    return clean_pear_task_text(content)


def pear_worker_should_start(
    content: str,
    active_session: PearActiveSessionSummary | None = None,
    *,
    explicitly_addressed: bool = False,
    response_policy: str | None = None,
) -> bool:
    synthetic_explicit_request = explicitly_addressed and not is_pear_request_message(
        content
    )
    content = normalize_pear_explicit_request_content(
        content,
        explicitly_addressed=explicitly_addressed,
    )
    if PEAR_RESUME_PROMPT in content:
        return is_pear_request_message(content)
    if (
        is_pear_steering_template_message(content)
        or is_pear_access_request_message(content)
        or PEAR_PAUSE_PROMPT in content
        or parse_pear_checklist(content)
        or parse_pear_artifacts(content, message_id=0)
    ):
        return False
    if is_pear_request_message(content):
        if (
            synthetic_explicit_request
            and response_policy is not None
            and normalize_pear_response_policy(response_policy) != "ambient"
        ):
            return False
        return is_pear_thread_request_message(content)
    if not explicitly_addressed:
        return False
    if active_session is None:
        return False
    effective_response_policy = normalize_pear_response_policy(
        response_policy if response_policy is not None else active_session["response_policy"]
    )
    if effective_response_policy != "ambient":
        return False
    return active_session["state"] in {"requested", "active"} and active_session[
        "worker_can_run"
    ]


def build_pear_worker_blocked_message(active_session: PearActiveSessionSummary) -> str:
    reason = active_session["worker_blocked_reason"] or "Pear worker is not ready."
    return "\n".join(
        [
            "Pear worker is not starting.",
            "",
            f"Reason: {reason}",
        ]
    )


def build_pear_worker_start_blocked_message(reason: str) -> str:
    return "\n".join(
        [
            "Pear worker is not starting.",
            "",
            f"Reason: {reason}",
        ]
    )


def build_pear_worker_failed_message(error: str) -> str:
    return "\n".join(
        [
            "Pear worker failed before posting progress.",
            "",
            f"Error: {error}",
        ]
    )


def build_pear_worker_failed_after_progress_message(error: str) -> str:
    return "\n".join(
        [
            "Pear worker failed after posting progress.",
            "",
            f"Error: {error}",
        ]
    )


def build_pear_worker_progress_message(
    *,
    task: str,
    tool_results: Sequence[PearWorkerToolResult],
    transcript_events: Sequence[PearWorkerTranscriptEvent] = (),
) -> str:
    return build_pear_live_transcript_message(
        task=task,
        status="running",
        transcript_events=transcript_events,
        tool_results=tool_results,
    )


def build_pear_worker_completed_message(
    *,
    task: str,
    tool_results: Sequence[PearWorkerToolResult],
    transcript_events: Sequence[PearWorkerTranscriptEvent] = (),
) -> str:
    return build_pear_live_transcript_message(
        task=task,
        status="done",
        transcript_events=transcript_events,
        tool_results=tool_results,
    )


def split_pear_persistent_message(
    content: str,
    *,
    limit: int = PEAR_PERSISTENT_MESSAGE_LIMIT,
) -> list[str]:
    clean_content = content.strip()
    if not clean_content:
        return []
    if len(clean_content) <= limit:
        return [clean_content]

    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    def append_unit(unit: str) -> None:
        nonlocal current
        unit = unit.strip()
        if not unit:
            return
        separator = "\n\n" if current else ""
        candidate = f"{current}{separator}{unit}"
        if len(candidate) <= limit:
            current = candidate
            return
        flush_current()
        if len(unit) <= limit:
            current = unit
            return
        for line in unit.splitlines():
            line = line.rstrip()
            if not line:
                flush_current()
                continue
            if len(line) <= limit:
                separator = "\n" if current else ""
                candidate = f"{current}{separator}{line}"
                if len(candidate) <= limit:
                    current = candidate
                else:
                    flush_current()
                    current = line
                continue
            flush_current()
            start = 0
            while start < len(line):
                chunks.append(line[start : start + limit].rstrip())
                start += limit

    for block in clean_content.split("\n\n"):
        append_unit(block)
    flush_current()
    return chunks


def build_pear_worker_completed_message_chunks(
    *,
    task: str,
    tool_results: Sequence[PearWorkerToolResult],
    transcript_events: Sequence[PearWorkerTranscriptEvent] = (),
) -> list[str]:
    transcript_blocks = pear_visible_transcript_blocks(
        transcript_events,
        include_transient_status=False,
        block_limit=None,
    )
    if transcript_blocks:
        result_text = pear_result_tool_text(tool_results, limit=None)
        if result_text and result_text not in transcript_blocks:
            transcript_blocks.append(result_text)
        return split_pear_persistent_message("\n\n".join(transcript_blocks))

    result_text = pear_result_tool_text(tool_results, limit=None)
    if result_text:
        return split_pear_persistent_message(result_text)

    return [build_pear_worker_completed_message(
        task=task,
        tool_results=tool_results,
        transcript_events=transcript_events,
    )]


PEAR_MATH_CODE_SEGMENT_PATTERN = re.compile(
    r"(```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|`[^`\n]*`)", re.DOTALL
)
PEAR_MATH_DISPLAY_PATTERN = re.compile(r"\\\[\s*(.*?)\s*\\\]", re.DOTALL)
PEAR_MATH_INLINE_PATTERN = re.compile(r"\\\((.*?)\\\)", re.DOTALL)


PEAR_TASK_MARKER_PATTERN = re.compile(
    r"^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]\s?", flags=re.MULTILINE
)


def normalize_pear_task_markers(content: str) -> str:
    """Rewrite markdown task-list markers into glyphs Zulip renders.

    Zulip has no task-list markdown (its checklists are the /todo
    widget), so "- [ ] step" arrives as literal brackets. Swap the
    markers for ballot-box glyphs, leaving fenced code untouched.
    """
    if "[ ]" not in content and "[x]" not in content and "[X]" not in content:
        return content
    parts = PEAR_MATH_CODE_SEGMENT_PATTERN.split(content)
    for index, part in enumerate(parts):
        if index % 2 == 1:
            continue

        def replace_marker(match: re.Match[str]) -> str:
            glyph = "\u2610" if match.group(2) == " " else "\u2611"
            return f"{match.group(1)}{glyph} "

        parts[index] = PEAR_TASK_MARKER_PATTERN.sub(replace_marker, part)
    return "".join(parts)


def normalize_pear_math_delimiters(content: str) -> str:
    """Rewrite LaTeX \\[ \\] / \\( \\) math into Zulip's dialect.

    Models habitually emit LaTeX's native delimiters, which Zulip's
    markdown passes through as literal text. Convert display math to
    ```math fences and inline math to $$...$$, leaving fenced code
    blocks and inline code spans untouched. Idempotent: converted
    content contains no LaTeX delimiters to re-match.
    """
    if "\\[" not in content and "\\(" not in content:
        return content
    parts = PEAR_MATH_CODE_SEGMENT_PATTERN.split(content)
    for index, part in enumerate(parts):
        if index % 2 == 1:
            continue
        def replace_display(match: re.Match[str]) -> str:
            body = match.group(1).strip()
            # Inside list items an indented ```math fence misses
            # Zulip's math path entirely; $$...$$ renders anywhere.
            if "\n" not in body and len(body) <= 200:
                return "$$" + body + "$$"
            return "\n```math\n" + body + "\n```\n"

        part = PEAR_MATH_DISPLAY_PATTERN.sub(replace_display, part)
        part = PEAR_MATH_INLINE_PATTERN.sub(
            lambda match: "$$" + match.group(1).strip() + "$$", part
        )
        parts[index] = part
    return "".join(parts)


def update_pear_worker_status_message(
    bot_handler: Any,
    *,
    progress_message_id: int | None,
    content: str,
    rendering_only: bool = False,
) -> bool:
    if progress_message_id is None:
        return False
    update_message = getattr(bot_handler, "update_message", None)
    if not callable(update_message):
        return False
    try:
        update_payload: dict[str, object] = {
            "message_id": progress_message_id,
            "content": normalize_pear_task_markers(normalize_pear_math_delimiters(content)),
        }
        if rendering_only:
            update_payload["rendering_only"] = True
        update_message(update_payload)
    except JsonableError as error:
        logger.warning(
            "Pear progress message update was rejected for message %s: %s",
            progress_message_id,
            error.msg,
        )
        return False
    except Exception:
        logger.exception(
            "Pear progress message update failed for message %s.",
            progress_message_id,
        )
        return False
    return True



def send_pear_worker_typing_notification(
    bot_handler: Any,
    *,
    stream: str,
    topic: str,
    operator: str,
) -> bool:
    """Best-effort topic-level "Pear is working" signal (design R1).

    Emits a stream-typing start/stop from the Pear bot so every client
    shows Zulip's native typing dots for the topic while a run is live.
    Typing is decoration: failures are logged and never affect the run.
    """
    try:
        user_profile = getattr(bot_handler, "user_profile", None)
        if user_profile is None:
            return False
        # Imported lazily: this module is loaded by the embedded-bot
        # runtime and must not import action modules at import time.
        from zerver.actions.typing import do_send_stream_typing_notification
        from zerver.models.streams import get_stream

        stream_object = get_stream(stream, user_profile.realm)
        do_send_stream_typing_notification(user_profile, operator, stream_object, topic)
        return True
    except Exception:
        logger.warning("Pear typing notification failed.", exc_info=True)
        return False

def pear_worker_summary_from_tool_results(
    tool_results: Sequence[PearWorkerToolResult],
) -> str:
    for preferred_label in ("Result", "Verification", "Diff", "PR", "Session"):
        for result in tool_results:
            if result["label"] == preferred_label and result["value"].strip():
                return result["value"].strip()
    for result in tool_results:
        if result["value"].strip():
            return result["value"].strip()
    return ""


def pear_codex_thread_id_from_tool_results(
    tool_results: Sequence[PearWorkerToolResult],
) -> str:
    for result in tool_results:
        if result["label"] != "Session":
            continue
        value = result["value"].strip()
        if value.startswith("Codex thread "):
            return value[len("Codex thread ") :].strip()
    return ""


class PearWorkerRuntime:
    def __init__(self, tool_runner: PearWorkerToolRunner | None = None) -> None:
        self.tool_runner = tool_runner

    def run(
        self,
        *,
        active_session: PearActiveSessionSummary,
        task: str = "",
        event_callback: PearWorkerTranscriptCallback | None = None,
    ) -> PearWorkerRunResult:
        if not active_session["worker_can_run"]:
            return {
                "should_post": True,
                "worker_can_run": False,
                "content": build_pear_worker_blocked_message(active_session),
                "tool_count": 0,
                "tool_results": [],
            }

        tool_results = list(self.run_tools(active_session, task, event_callback=event_callback))
        return {
            "should_post": True,
            "worker_can_run": True,
            "content": build_pear_worker_progress_message(
                task=task,
                tool_results=tool_results,
            ),
            "tool_count": len(tool_results),
            "tool_results": tool_results,
        }

    def run_tools(
        self,
        active_session: PearActiveSessionSummary,
        task: str,
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
    ) -> Sequence[PearWorkerToolResult]:
        if self.tool_runner is not None:
            return self.tool_runner(
                active_session,
                task,
                event_callback=event_callback,
            )
        return [
            {
                "label": "Verification",
                "value": "Pear worker runtime accepted this session.",
            }
        ]

    def steer(self, instruction: str) -> bool:
        if self.tool_runner is None:
            return False
        steer = getattr(self.tool_runner, "steer", None)
        if not callable(steer):
            return False
        return bool(steer(instruction))

    def interrupt(self) -> bool:
        if self.tool_runner is None:
            return False
        interrupt = getattr(self.tool_runner, "interrupt", None)
        if not callable(interrupt):
            return False
        return bool(interrupt())

    def close(self) -> None:
        if self.tool_runner is None:
            return
        close = getattr(self.tool_runner, "close", None)
        if callable(close):
            close()

    def runtime_value(self) -> str:
        if self.tool_runner is None:
            return "built-in"
        runtime_value = getattr(self.tool_runner, "runtime_value", None)
        if callable(runtime_value):
            return str(runtime_value())
        return self.tool_runner.__class__.__name__


def pear_message_topic(message: dict[str, Any]) -> str:
    topic = message.get("topic", message.get("subject", ""))
    return str(topic)


def pear_message_stream(message: dict[str, Any]) -> str:
    display_recipient = message.get("display_recipient", "")
    if isinstance(display_recipient, str):
        return display_recipient
    return ""


def pear_message_id(message: dict[str, Any]) -> int:
    message_id_value = message.get("id", 0)
    return message_id_value if isinstance(message_id_value, int) else 0


def build_trigger_pear_active_session(
    message: dict[str, Any],
    content: str,
) -> PearActiveSessionSummary:
    return build_pear_active_session_summary_from_messages(
        pear_session=get_pear_session_payload(
            stream=pear_message_stream(message),
            topic=pear_message_topic(message),
        ),
        source="server",
        stream_exists=True,
        messages=[(pear_message_id(message), content, False)],
        topic_message_count=1,
    )


def build_pear_worker_run_record(
    *,
    trigger_message_id: int,
    status: PearWorkerRunStatus,
    stream: str,
    topic: str,
    result_message_id: int | None = None,
    error: str | None = None,
) -> PearWorkerRunRecord:
    return {
        "trigger_message_id": trigger_message_id,
        "status": status,
        "stream": stream,
        "topic": topic,
        "result_message_id": result_message_id,
        "error": error,
    }


def normalize_pear_worker_run_record(value: object) -> PearWorkerRunRecord | None:
    if isinstance(value, int):
        return build_pear_worker_run_record(
            trigger_message_id=value,
            status="done",
            stream="",
            topic="",
        )
    if not isinstance(value, dict):
        return None
    message_id = value.get("trigger_message_id")
    status = value.get("status")
    if not isinstance(message_id, int) or status not in {"queued", "running", "done", "failed"}:
        return None
    stream = value.get("stream", "")
    topic = value.get("topic", "")
    result_message_id = value.get("result_message_id")
    error = value.get("error")
    return build_pear_worker_run_record(
        trigger_message_id=message_id,
        status=status,
        stream=stream if isinstance(stream, str) else "",
        topic=topic if isinstance(topic, str) else "",
        result_message_id=result_message_id if isinstance(result_message_id, int) else None,
        error=error if isinstance(error, str) else None,
    )


def load_pear_worker_run_records(bot_handler: Any) -> list[PearWorkerRunRecord]:
    storage = getattr(bot_handler, "storage", None)
    if storage is None or not storage.contains(PEAR_WORKER_RUNS_STORAGE_KEY):
        return []
    stored_value = storage.get(PEAR_WORKER_RUNS_STORAGE_KEY)
    if not isinstance(stored_value, list):
        return []
    records = []
    for value in stored_value:
        record = normalize_pear_worker_run_record(value)
        if record is not None:
            records.append(record)
    return records


def pear_worker_run_for_message(
    bot_handler: Any,
    message_id: int,
) -> PearWorkerRunRecord | None:
    for record in reversed(load_pear_worker_run_records(bot_handler)):
        if record["trigger_message_id"] == message_id:
            return record
    return None


def pear_worker_already_started(bot_handler: Any, message_id: int) -> bool:
    if message_id <= 0:
        return False
    record = pear_worker_run_for_message(bot_handler, message_id)
    return record is not None and record["status"] in {"queued", "running", "done"}


def record_pear_worker_run(
    bot_handler: Any,
    message_id: int,
    status: PearWorkerRunStatus,
    *,
    stream: str,
    topic: str,
    result_message_id: int | None = None,
    error: str | None = None,
) -> None:
    if message_id <= 0:
        return
    storage = getattr(bot_handler, "storage", None)
    if storage is None:
        return
    records = [
        record
        for record in load_pear_worker_run_records(bot_handler)
        if record["trigger_message_id"] != message_id
    ]
    records.append(
        build_pear_worker_run_record(
            trigger_message_id=message_id,
            status=status,
            stream=stream,
            topic=topic,
            result_message_id=result_message_id,
            error=error,
        )
    )
    storage.put(
        PEAR_WORKER_RUNS_STORAGE_KEY,
        records[-PEAR_WORKER_RUNS_STORAGE_LIMIT:],
    )


class PearStorageWorkerRunStore:
    def start(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
    ) -> bool:
        if pear_worker_already_started(bot_handler, message_id):
            return False
        record_pear_worker_run(
            bot_handler,
            message_id,
            "queued",
            stream=stream,
            topic=topic,
        )
        return True

    def record(
        self,
        bot_handler: Any,
        message_id: int,
        status: PearWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        record_pear_worker_run(
            bot_handler,
            message_id,
            status,
            stream=stream,
            topic=topic,
            result_message_id=result_message_id,
            error=error,
        )


class PearInlineWorkerDispatcher:
    def dispatch(
        self,
        pear_handler: Any,
        bot_handler: Any,
        message: dict[str, Any],
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message_id: int,
        stream: str,
        topic: str,
        requesting_user_id: int | None = None,
        session_kind: str = "shared",
        source_stream: str = "",
        source_topic: str = "",
        source_message_id: int | None = None,
        progress_message_id: int | None = None,
    ) -> None:
        pear_handler.run_worker_turn(
            message,
            bot_handler,
            content=content,
            active_session=active_session,
            message_id=message_id,
            stream=stream,
            topic=topic,
            post_started_progress=False,
            requesting_user_id=requesting_user_id,
            session_kind=session_kind,
            source_stream=source_stream,
            source_topic=source_topic,
            source_message_id=source_message_id,
            progress_message_id=progress_message_id,
        )


def parse_pear_checklist(content: str) -> list[PearChecklistItem]:
    checklist: list[PearChecklistItem] = []
    for match in finditer(PEAR_CHECKLIST_PATTERN, content, MULTILINE):
        marker = match.group(1)
        if marker in ("x", "X"):
            status: PearChecklistStatus = "done"
        elif marker == "~":
            status = "in_progress"
        else:
            status = "pending"
        checklist.append({"status": status, "text": match.group(2).strip()})
    return checklist


def parse_pear_artifacts(content: str, message_id: int) -> list[PearArtifact]:
    artifacts: list[PearArtifact] = []
    for match in finditer(PEAR_ARTIFACT_PATTERN, content, MULTILINE | IGNORECASE):
        raw_label = match.group(1).strip()
        value = match.group(2).strip()
        if not value:
            continue
        label = raw_label.lower()
        kind = PEAR_ARTIFACT_KIND_BY_LABEL[label]
        artifacts.append(
            {
                "kind": kind,
                "label": raw_label,
                "value": value,
                "message_id": message_id,
            }
        )
    return artifacts


def is_pear_steering_template_message(content: str) -> bool:
    return PEAR_STEER_PROMPT in content


def is_pear_access_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    normalized = " ".join(content.lower().replace("?", " ").split())
    return (
        "what can you access" in normalized
        or "what can pear access" in normalized
        or "what can you reach" in normalized
        or PEAR_ACCESS_PROMPT.lower().rstrip("?") in normalized
    )


def is_pear_interruption_template_message(content: str) -> bool:
    return PEAR_PAUSE_PROMPT in content or PEAR_RESUME_PROMPT in content


def is_pear_control_template_message(content: str) -> bool:
    return (
        is_pear_steering_template_message(content)
        or is_pear_interruption_template_message(content)
    )


def parse_pear_interruption_event(
    content: str,
    message_id: int,
    *,
    sender_is_bot: bool | None = None,
) -> PearInterruptionEvent | None:
    if sender_is_bot is True:
        return None

    if PEAR_PAUSE_PROMPT in content:
        kind: PearInterruptionKind = "pause"
        prompt = PEAR_PAUSE_PROMPT
        value_prefix = "reason:"
    elif PEAR_RESUME_PROMPT in content:
        kind = "resume"
        prompt = PEAR_RESUME_PROMPT
        value_prefix = "request:"
    else:
        return None

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    if instruction.startswith(prompt):
        instruction = instruction[len(prompt) :].strip()
    if instruction.lower().startswith(value_prefix):
        instruction = instruction[len(value_prefix) :].strip()

    return {
        "kind": kind,
        "instruction": instruction,
        "message_id": message_id,
    }


def parse_pear_steering_event(
    content: str,
    message_id: int,
    *,
    sender_is_bot: bool | None = None,
    session_started: bool = False,
) -> PearSteeringEvent | None:
    if sender_is_bot is True:
        return None

    explicitly_addressed = is_pear_request_message(content)
    if not session_started and (
        not explicitly_addressed or not is_pear_steering_template_message(content)
    ):
        return None
    if parse_pear_checklist(content) or parse_pear_artifacts(content, message_id):
        return None
    if (
        is_pear_interruption_template_message(content)
        or is_pear_access_request_message(content)
        or (
            is_pear_control_template_message(content)
            and not is_pear_steering_template_message(content)
        )
    ):
        return None

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    if instruction.startswith(PEAR_STEER_PROMPT):
        instruction = instruction[len(PEAR_STEER_PROMPT) :].strip()
    if instruction.lower().startswith("request:"):
        instruction = instruction[len("request:") :].strip()
    if not instruction:
        return None

    return {
        "instruction": instruction,
        "message_id": message_id,
        "explicitly_addressed": explicitly_addressed,
    }


def is_pear_request_message(content: str) -> bool:
    return PEAR_AGENT_MENTION in content or is_pear_plain_mention_alias_message(content)


def is_pear_plain_mention_alias_message(content: str) -> bool:
    return PEAR_PLAIN_AGENT_MENTION_RE.search(content) is not None


PearMessage = (
    tuple[int, str]
    | tuple[int, str, bool | None]
    | tuple[int, str, bool | None, str | None]
)


def normalize_pear_message(message: PearMessage) -> tuple[int, str, bool | None, str | None]:
    if len(message) == 2:
        message_id, content = message
        return message_id, content, None, None
    if len(message) == 3:
        message_id, content, sender_is_bot = message
        return message_id, content, sender_is_bot, None
    message_id, content, sender_is_bot, sender_name = message
    return message_id, content, sender_is_bot, sender_name


def build_pear_recent_messages(
    messages: Sequence[tuple[int, str, bool | None, str | None]],
    *,
    limit: int = 30,
) -> list[PearRecentMessage]:
    newest_messages = sorted(messages, key=lambda message: message[0], reverse=True)[:limit]
    recent_messages: list[PearRecentMessage] = []
    for message_id, content, sender_is_bot, sender_name in reversed(newest_messages):
        clean_content = trim_pear_text(content, limit=1200)
        if not clean_content:
            continue
        clean_sender = (sender_name or "").strip()
        if not clean_sender:
            clean_sender = "Pear" if sender_is_bot else "User"
        recent_messages.append(
            {
                "message_id": message_id,
                "sender": trim_pear_text(clean_sender, limit=120),
                "sender_is_bot": sender_is_bot,
                "content": clean_content,
            }
        )
    return recent_messages


def build_pear_active_session_summary(
    *,
    pear_session: PearSessionPayload,
    source: PearActiveSessionSource,
    stream_exists: bool,
    topic_message_count: int,
    latest_message_id: int | None,
    checklist: list[PearChecklistItem],
    request_count: int = 0,
    latest_request_message_id: int | None = None,
    artifacts: list[PearArtifact] | None = None,
    latest_verification: str | None = None,
    latest_codex_thread_id: str | None = None,
    steering_events: list[PearSteeringEvent] | None = None,
    interruption_events: list[PearInterruptionEvent] | None = None,
    recent_messages: list[PearRecentMessage] | None = None,
    workspace_context: PearWorkspaceContext | None = None,
    is_paused: bool = False,
    response_policy: str | None = None,
) -> PearActiveSessionSummary:
    artifact_list = list(artifacts or [])
    normalized_response_policy = normalize_pear_response_policy(response_policy)
    steering_event_list = list(steering_events or [])
    interruption_event_list = list(interruption_events or [])
    latest_interruption = interruption_event_list[0] if interruption_event_list else None

    if is_paused or (latest_interruption is not None and latest_interruption["kind"] == "pause"):
        state: PearActiveSessionState = "paused"
    elif latest_message_id is not None:
        state = "active"
    elif latest_request_message_id is not None or steering_event_list or interruption_event_list:
        state = "requested"
    else:
        state = "not_started"

    if not stream_exists:
        worker_state: PearWorkerState = "blocked_missing_stream"
        worker_blocked_reason = "Pear stream is not accessible."
    elif state == "paused":
        worker_state = "blocked_paused"
        worker_blocked_reason = "Pear session is paused until a resume control is posted."
    elif state == "not_started":
        # A fresh topic is not blocked — the worker runs as soon as the
        # first request arrives — but showing "ready" for a topic with no
        # request yet contradicted the terminal's cache-path summary.
        worker_state = "waiting_for_request"
        worker_blocked_reason = "No Pear request has been posted in this topic."
    else:
        worker_state = "ready"
        worker_blocked_reason = None
    worker_can_run = worker_state in ("ready", "waiting_for_request")

    if latest_verification is None:
        latest_verification = next(
            (
                artifact["value"]
                for artifact in artifact_list
                if artifact["kind"] == "verification"
            ),
            None,
        )
    session_record_artifacts = [
        artifact for artifact in artifact_list if artifact["kind"] == "session_record"
    ]
    latest_session_record = (
        session_record_artifacts[0] if session_record_artifacts else None
    )
    latest_session_record_value = (
        latest_session_record["value"] if latest_session_record is not None else None
    )

    return {
        "state": state,
        "stream": pear_session["stream"],
        "topic": pear_session["topic"],
        "source": source,
        "stream_exists": stream_exists,
        "topic_message_count": topic_message_count,
        "request_count": request_count,
        "latest_request_message_id": latest_request_message_id,
        "latest_message_id": latest_message_id,
        "pending_count": len([item for item in checklist if item["status"] == "pending"]),
        "in_progress_count": len([item for item in checklist if item["status"] == "in_progress"]),
        "done_count": len([item for item in checklist if item["status"] == "done"]),
        "artifact_count": len(artifact_list),
        "latest_artifact_message_id": artifact_list[0]["message_id"] if artifact_list else None,
        "latest_verification": latest_verification,
        "session_record_count": len(session_record_artifacts),
        "latest_session_record_message_id": latest_session_record["message_id"]
        if latest_session_record is not None
        else None,
        "latest_session_record": latest_session_record_value,
        "latest_session_record_kind": pear_session_record_kind(
            latest_session_record_value
        ),
        "latest_session_record_url": pear_session_record_url(
            latest_session_record_value
        ),
        "latest_codex_thread_id": latest_codex_thread_id,
        "response_policy": normalized_response_policy,
        "steering_count": len(steering_event_list),
        "latest_steering_message_id": steering_event_list[0]["message_id"]
        if steering_event_list
        else None,
        "latest_steering_instruction": steering_event_list[0]["instruction"]
        if steering_event_list
        else None,
        "interruption_count": len(interruption_event_list),
        "latest_interruption_kind": latest_interruption["kind"]
        if latest_interruption is not None
        else None,
        "latest_interruption_message_id": latest_interruption["message_id"]
        if latest_interruption is not None
        else None,
        "latest_interruption_instruction": latest_interruption["instruction"]
        if latest_interruption is not None
        else None,
        "is_paused": state == "paused",
        "worker_state": worker_state,
        "worker_can_run": worker_can_run,
        "worker_blocked_reason": worker_blocked_reason,
        "recent_messages": list(recent_messages or []),
        "checklist": checklist,
        "artifacts": artifact_list,
        "steering_events": steering_event_list,
        "interruption_events": interruption_event_list,
        "workspace_context": dict(workspace_context or {}),
    }


def build_pear_active_session_summary_from_messages(
    *,
    pear_session: PearSessionPayload,
    source: PearActiveSessionSource,
    stream_exists: bool,
    messages: Sequence[PearMessage],
    topic_message_count: int | None = None,
    latest_codex_thread_id: str | None = None,
    workspace_context: PearWorkspaceContext | None = None,
    is_paused: bool = False,
    response_policy: str | None = None,
) -> PearActiveSessionSummary:
    latest_message_id: int | None = None
    latest_checklist_message_id: int | None = None
    latest_bot_reply_message_id: int | None = None
    latest_request_message_id: int | None = None
    request_count = 0
    checklist: list[PearChecklistItem] = []
    artifacts: list[PearArtifact] = []
    steering_events: list[PearSteeringEvent] = []
    interruption_events: list[PearInterruptionEvent] = []
    normalized_messages = [normalize_pear_message(message) for message in messages]
    sorted_messages = sorted(normalized_messages, key=lambda message: message[0], reverse=True)
    for message_id, content, _sender_is_bot, _sender_name in sorted_messages:
        artifacts.extend(parse_pear_artifacts(content, message_id))
        if is_pear_task_request_message(content):
            request_count += 1
            if latest_request_message_id is None:
                latest_request_message_id = message_id
        if latest_checklist_message_id is None:
            message_checklist = parse_pear_checklist(content)
            if message_checklist:
                latest_checklist_message_id = message_id
                checklist = message_checklist
        if (
            latest_bot_reply_message_id is None
            and _sender_is_bot is True
            and content.strip()
        ):
            latest_bot_reply_message_id = message_id

    if latest_request_message_id is not None:
        if (
            latest_bot_reply_message_id is not None
            and latest_bot_reply_message_id > latest_request_message_id
        ):
            latest_message_id = latest_bot_reply_message_id
        else:
            latest_message_id = latest_checklist_message_id
    else:
        latest_message_id = latest_checklist_message_id

    session_started = False
    for message_id, content, sender_is_bot, _sender_name in reversed(sorted_messages):
        interruption_event = parse_pear_interruption_event(
            content,
            message_id,
            sender_is_bot=sender_is_bot,
        )
        if interruption_event is not None:
            interruption_events.append(interruption_event)
        steering_event = parse_pear_steering_event(
            content,
            message_id,
            sender_is_bot=sender_is_bot,
            session_started=session_started,
        )
        if steering_event is not None:
            steering_events.append(steering_event)
        if is_pear_task_request_message(content) or parse_pear_checklist(content):
            session_started = True

    return build_pear_active_session_summary(
        pear_session=pear_session,
        source=source,
        stream_exists=stream_exists,
        topic_message_count=topic_message_count
        if topic_message_count is not None
        else len(sorted_messages),
        request_count=request_count,
        latest_request_message_id=latest_request_message_id,
        latest_message_id=latest_message_id,
        checklist=checklist,
        artifacts=artifacts,
        latest_codex_thread_id=latest_codex_thread_id,
        steering_events=list(reversed(steering_events)),
        interruption_events=list(reversed(interruption_events)),
        recent_messages=build_pear_recent_messages(sorted_messages),
        workspace_context=workspace_context,
        is_paused=is_paused,
        response_policy=response_policy,
    )


def build_pear_config_payload(
    *,
    embedded_bots_enabled: bool,
    bot_count: int,
    bot_user_id: int | None = None,
    bot_full_name: str | None = None,
    bot_email: str | None = None,
    selected_bot_user_id: int | None = None,
    selected_bot_full_name: str | None = None,
    selected_bot_email: str | None = None,
    bot_identities: list[PearBotIdentity] | None = None,
    worker_agent_runtime: str = "disabled",
    worker_runtime_available: bool | None = None,
    worker_runtime_detail: str | None = None,
    worker_runtime_auth_available: bool | None = None,
    worker_runtime_auth_detail: str | None = None,
    worker_runtime_login_method: str | None = None,
    worker_runtime_login_command: str | None = None,
    worker_runtime_login_target: str | None = None,
    worker_runtime_login_instructions: str | None = None,
    worker_service_tier: str | None = None,
    worker_fast_mode: bool = False,
) -> PearConfigPayload:
    if selected_bot_user_id is None:
        selected_bot_user_id = bot_user_id
        selected_bot_full_name = bot_full_name
        selected_bot_email = bot_email
    configured = bot_user_id is not None
    bot_selected = selected_bot_user_id is not None
    supported_worker_runtime = worker_agent_runtime == "codex-app-server"
    if worker_runtime_available is None:
        worker_runtime_available = supported_worker_runtime
    if worker_runtime_detail is None:
        worker_runtime_detail = (
            worker_agent_runtime
            if supported_worker_runtime
            else "Set PEAR_WORKER_AGENT_RUNTIME to codex-app-server."
        )
    if worker_runtime_auth_available is None:
        worker_runtime_auth_available = supported_worker_runtime
    if worker_runtime_auth_detail is None:
        worker_runtime_auth_detail = (
            f"{worker_agent_runtime} auth is configured."
            if supported_worker_runtime
            else "Select a supported worker runtime before logging in."
        )
    if worker_runtime_login_method is None and supported_worker_runtime:
        worker_runtime_login_method = "codex-cli"
    if worker_runtime_login_target is None and supported_worker_runtime:
        worker_runtime_login_target = "worker-environment"
    if worker_runtime_login_instructions is None:
        if worker_agent_runtime == "codex-app-server":
            worker_runtime_login_instructions = (
                "Run Codex CLI login inside the worker environment used by Pear."
            )
        else:
            worker_runtime_login_instructions = None
    worker_runtime_dependencies_ready = supported_worker_runtime and worker_runtime_available
    worker_runtime_ready = (
        embedded_bots_enabled
        and worker_runtime_dependencies_ready
        and worker_runtime_auth_available
        and bot_selected
    )
    setup_steps: list[PearSetupStep] = [
        {
            "key": "embedded_bots",
            "label": "Embedded bots enabled",
            "complete": embedded_bots_enabled,
            "detail": "Zulip embedded bot services are enabled."
            if embedded_bots_enabled
            else "Enable EMBEDDED_BOTS_ENABLED before Pear can receive topic events.",
        },
        {
            "key": "pear_bot",
            "label": "Pear bot selected",
            "complete": bot_selected,
            "detail": f"{selected_bot_full_name} ({selected_bot_user_id})"
            if bot_selected
            else "Provision or select a Pear embedded bot.",
        },
        {
            "key": "worker_runtime",
            "label": "Worker runtime dependencies",
            "complete": worker_runtime_dependencies_ready,
            "detail": worker_runtime_detail,
        },
        {
            "key": "worker_auth",
            "label": "Worker runtime login",
            "complete": supported_worker_runtime and worker_runtime_auth_available,
            "detail": worker_runtime_auth_detail,
        },
    ]
    return {
        "bot_short_name": PEAR_BOT_SHORT_NAME,
        "embedded_bot_service": PEAR_EMBEDDED_BOT_SERVICE,
        "embedded_bots_enabled": embedded_bots_enabled,
        "configured": configured,
        "enabled": embedded_bots_enabled and configured,
        "worker_agent_runtime": worker_agent_runtime,
        "worker_service_tier": worker_service_tier,
        "worker_fast_mode": worker_fast_mode,
        "worker_runtime_ready": worker_runtime_ready,
        "worker_runtime_auth_ready": supported_worker_runtime
        and worker_runtime_auth_available,
        "worker_runtime_login_method": worker_runtime_login_method,
        "worker_runtime_login_command": worker_runtime_login_command,
        "worker_runtime_login_target": worker_runtime_login_target,
        "worker_runtime_login_instructions": worker_runtime_login_instructions,
        "bot_count": bot_count,
        "bot_user_id": bot_user_id,
        "bot_full_name": bot_full_name,
        "bot_email": bot_email,
        "selected_bot_user_id": selected_bot_user_id,
        "selected_bot_full_name": selected_bot_full_name,
        "selected_bot_email": selected_bot_email,
        "bot_identities": list(bot_identities or []),
        "setup_steps": setup_steps,
    }


class PearEmbeddedBotHandler:
    def __init__(
        self,
        worker_runtime: PearWorkerRuntime | None = None,
        active_session_loader: PearWorkerActiveSessionLoader | None = None,
        worker_run_store: PearWorkerRunStore | None = None,
        worker_dispatcher: PearWorkerDispatcher | None = None,
        addressing_intent_classifier: PearAddressingIntentClassifier | None = None,
    ) -> None:
        self.worker_runtime = worker_runtime or PearWorkerRuntime()
        self.active_session_loader = active_session_loader
        self.worker_run_store = worker_run_store or PearStorageWorkerRunStore()
        self.worker_dispatcher = worker_dispatcher or PearInlineWorkerDispatcher()
        self.addressing_intent_classifier = addressing_intent_classifier

    def load_active_session(
        self,
        message: dict[str, Any],
        bot_handler: Any,
        content: str,
    ) -> PearActiveSessionSummary | None:
        if message.get("type") != "stream":
            return None
        try:
            return (
                self.active_session_loader(message, bot_handler)
                if self.active_session_loader is not None
                else build_trigger_pear_active_session(message, content)
            )
        except Exception:
            return None

    def worker_start_blocked_reason(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        requesting_user_id: int | None,
    ) -> str | None:
        start_blocked_reason = getattr(self.worker_run_store, "start_blocked_reason", None)
        if callable(start_blocked_reason):
            reason = start_blocked_reason(
                bot_handler,
                message_id,
                stream=stream,
                topic=topic,
                requesting_user_id=requesting_user_id,
            )
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
        return None

    def active_worker_run_message_id(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
    ) -> int | None:
        active_run_message_id = getattr(
            self.worker_run_store,
            "active_worker_run_message_id",
            None,
        )
        if not callable(active_run_message_id):
            return None
        message_id = active_run_message_id(
            bot_handler,
            stream=stream,
            topic=topic,
        )
        return message_id if isinstance(message_id, int) and message_id > 0 else None

    def active_worker_run_status_message(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
    ) -> PearWorkerStatusMessage | None:
        active_status_message = getattr(
            self.worker_run_store,
            "active_worker_run_status_message",
            None,
        )
        if not callable(active_status_message):
            return None
        status_message = active_status_message(
            bot_handler,
            stream=stream,
            topic=topic,
        )
        if not isinstance(status_message, dict):
            return None
        message_id = status_message.get("message_id")
        content = status_message.get("content")
        if not isinstance(message_id, int) or message_id <= 0 or not isinstance(content, str):
            return None
        return {"message_id": message_id, "content": content}

    def update_active_worker_steering_status(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
        status_markdown: str,
    ) -> bool:
        status_message = self.active_worker_run_status_message(
            bot_handler,
            stream=stream,
            topic=topic,
        )
        if status_message is None:
            return False
        return update_pear_worker_status_message(
            bot_handler,
            progress_message_id=status_message["message_id"],
            content=pear_with_steering_status(status_message["content"], status_markdown),
        )

    def is_implicitly_addressed(
        self,
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message: dict[str, Any],
        bot_handler: Any,
    ) -> bool:
        if self.addressing_intent_classifier is None:
            return False
        try:
            return self.addressing_intent_classifier(
                content=content,
                active_session=active_session,
                message=message,
                bot_handler=bot_handler,
            )
        except Exception:
            logger.warning("Pear addressing router failed.", exc_info=True)
            return False

    def record_worker_event(
        self,
        bot_handler: Any,
        message_id: int,
        event_type: str,
        *,
        summary: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        record_event = getattr(self.worker_run_store, "record_event", None)
        if callable(record_event):
            record_event(
                bot_handler,
                message_id,
                event_type,
                summary=summary,
                payload=payload or {},
            )

    def update_worker_metadata(
        self,
        bot_handler: Any,
        message_id: int,
        **metadata: Any,
    ) -> None:
        update_metadata = getattr(self.worker_run_store, "update_metadata", None)
        if callable(update_metadata):
            update_metadata(bot_handler, message_id, **metadata)

    def worker_run_cancel_requested(
        self,
        bot_handler: Any,
        message_id: int,
    ) -> bool:
        cancel_requested = getattr(self.worker_run_store, "cancel_requested", None)
        if not callable(cancel_requested):
            return False
        return bool(cancel_requested(bot_handler, message_id))

    def set_topic_paused(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
        is_paused: bool,
    ) -> None:
        set_topic_paused = getattr(self.worker_run_store, "set_topic_paused", None)
        if callable(set_topic_paused):
            set_topic_paused(
                bot_handler,
                stream=stream,
                topic=topic,
                is_paused=is_paused,
            )

    def topic_response_policy(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
    ) -> str | None:
        topic_response_policy = getattr(self.worker_run_store, "topic_response_policy", None)
        if not callable(topic_response_policy):
            return None
        response_policy = topic_response_policy(
            bot_handler,
            stream=stream,
            topic=topic,
        )
        return normalize_pear_response_policy(response_policy)

    def set_topic_response_policy(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
        response_policy: str,
    ) -> None:
        set_topic_response_policy = getattr(
            self.worker_run_store,
            "set_topic_response_policy",
            None,
        )
        if callable(set_topic_response_policy):
            set_topic_response_policy(
                bot_handler,
                stream=stream,
                topic=topic,
                response_policy=response_policy,
            )

    def register_worker_progress_message(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        progress_message_id: int | None,
    ) -> None:
        if progress_message_id is None:
            return
        register_progress_message = getattr(
            self.worker_run_store,
            "register_progress_message",
            None,
        )
        if callable(register_progress_message):
            register_progress_message(
                bot_handler,
                message_id,
                stream=stream,
                topic=topic,
                progress_message_id=progress_message_id,
            )

    def handle_message(self, message: dict[str, Any], bot_handler: Any) -> None:
        raw_content = str(message.get("content", "")).strip()
        explicitly_addressed = bool(
            message.get(PEAR_EXPLICIT_MENTION_MESSAGE_KEY)
        ) or is_pear_request_message(raw_content)
        content = normalize_pear_explicit_request_content(
            raw_content,
            explicitly_addressed=explicitly_addressed,
        )
        active_session = self.load_active_session(message, bot_handler, content)
        message_id = pear_message_id(message)
        stream = pear_message_stream(message)
        topic = pear_message_topic(message)
        db_response_policy = (
            self.topic_response_policy(
                bot_handler,
                stream=stream,
                topic=topic,
            )
            if message.get("type") == "stream"
            else None
        )
        if active_session is not None and db_response_policy is not None:
            active_session["response_policy"] = db_response_policy
        requesting_user_id_value = message.get("sender_id")
        requesting_user_id = (
            requesting_user_id_value if isinstance(requesting_user_id_value, int) else None
        )
        active_run_message_id = (
            self.active_worker_run_message_id(
                bot_handler,
                stream=stream,
                topic=topic,
            )
            if message.get("type") == "stream"
            else None
        )
        should_route_untagged_message = (
            message.get("type") == "stream"
            and not explicitly_addressed
            and self.addressing_intent_classifier is not None
        )
        if should_route_untagged_message:
            explicitly_addressed = self.is_implicitly_addressed(
                content=raw_content,
                active_session=active_session,
                message=message,
                bot_handler=bot_handler,
            )
            content = normalize_pear_explicit_request_content(
                raw_content,
                explicitly_addressed=explicitly_addressed,
            )
            if not explicitly_addressed:
                return
        interruption_event = parse_pear_interruption_event(
            content,
            message_id=0,
            sender_is_bot=False,
        )
        steering_event = parse_pear_steering_event(
            content,
            message_id=message_id,
            sender_is_bot=False,
            session_started=active_run_message_id is not None
            or (
                active_session is not None
                and active_session["state"] in {"requested", "active"}
            ),
        )
        should_start_worker = message.get("type") == "stream" and pear_worker_should_start(
            raw_content,
            active_session,
            explicitly_addressed=explicitly_addressed,
            response_policy=db_response_policy,
        )
        should_steer_active_run = (
            explicitly_addressed
            and active_run_message_id is not None
            and steering_event is not None
        )
        if should_steer_active_run:
            should_start_worker = False
        if not explicitly_addressed and not should_start_worker and not should_steer_active_run:
            return
        if is_pear_access_request_message(content):
            response = build_pear_access_ack_message(active_session)
        elif interruption_event is not None:
            if interruption_event["kind"] == "pause":
                response = build_pear_pause_ack_message(interruption_event["instruction"])
            else:
                response = build_pear_resume_ack_message(interruption_event["instruction"])
            self.set_topic_paused(
                bot_handler,
                stream=stream,
                topic=topic,
                is_paused=interruption_event["kind"] == "pause",
            )
        elif should_steer_active_run and steering_event is not None:
            response = build_pear_steering_ack_message(steering_event["instruction"])
        elif is_pear_steering_template_message(content):
            response = build_pear_steering_ack_message(
                steering_event["instruction"] if steering_event is not None else ""
            )
        elif explicitly_addressed and not should_start_worker:
            response = build_pear_ack_message(content)
            should_start_worker = True
        else:
            response = build_pear_ack_message(content)

        if should_start_worker:
            blocked_reason = self.worker_start_blocked_reason(
                bot_handler,
                message_id,
                stream=stream,
                topic=topic,
                requesting_user_id=requesting_user_id,
            )
            if blocked_reason is not None:
                response = build_pear_worker_start_blocked_message(blocked_reason)
                should_start_worker = False

        if should_steer_active_run and active_run_message_id is not None:
            assert steering_event is not None
            self.record_worker_event(
                bot_handler,
                active_run_message_id,
                "steering",
                summary=trim_pear_text(steering_event["instruction"], limit=500),
                payload={
                    "message_id": message_id,
                    "instruction": steering_event["instruction"],
                    "stream": stream,
                    "topic": topic,
                },
            )
            self.update_worker_metadata(
                bot_handler,
                active_run_message_id,
                latest_steering=trim_pear_text(steering_event["instruction"], limit=500),
            )
            self.update_active_worker_steering_status(
                bot_handler,
                stream=stream,
                topic=topic,
                status_markdown=PEAR_STEERING_QUEUED_MARKDOWN,
            )
            return
        result = bot_handler.send_reply(
            message, normalize_pear_task_markers(normalize_pear_math_delimiters(response))
        )
        progress_message_id = None
        if isinstance(result, dict) and isinstance(result.get("id"), int):
            progress_message_id = result["id"]
        if should_start_worker:
            if not self.worker_run_store.start(
                bot_handler,
                message_id,
                stream=stream,
                topic=topic,
            ):
                stale_ack_message = build_pear_worker_start_blocked_message(
                    "This request was already handled or is no longer claimable."
                )
                update_pear_worker_status_message(
                    bot_handler,
                    progress_message_id=progress_message_id,
                    content=stale_ack_message,
                )
                return
            self.register_worker_progress_message(
                bot_handler,
                message_id,
                stream=stream,
                topic=topic,
                progress_message_id=progress_message_id,
            )
            self.worker_dispatcher.dispatch(
                self,
                bot_handler,
                message,
                content=content,
                active_session=active_session,
                message_id=message_id,
                stream=stream,
                topic=topic,
                requesting_user_id=requesting_user_id,
                progress_message_id=progress_message_id,
            )

    def run_worker_turn(
        self,
        message: dict[str, Any],
        bot_handler: Any,
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message_id: int,
        stream: str,
        topic: str,
        post_started_progress: bool,
        requesting_user_id: int | None = None,
        session_kind: str = "shared",
        source_stream: str = "",
        source_topic: str = "",
        source_message_id: int | None = None,
        progress_message_id: int | None = None,
    ) -> None:
        PearWorkerTurn(
            self,
            message,
            bot_handler,
            content=content,
            active_session=active_session,
            message_id=message_id,
            stream=stream,
            topic=topic,
            post_started_progress=post_started_progress,
            requesting_user_id=requesting_user_id,
            session_kind=session_kind,
            source_stream=source_stream,
            source_topic=source_topic,
            source_message_id=source_message_id,
            progress_message_id=progress_message_id,
        ).run()


class PearWorkerTurn:
    """A single Pear worker run: transcript streaming, same-turn steering
    segments, and settlement for one request.

    Extracted from the former 600-line run_worker_turn closure nest; the
    shared mutable state the closures threaded through ``nonlocal`` lives
    on the instance, and ``handler`` is the embedded-bot handler whose
    stores and runtime the turn drives.
    """

    def __init__(
        self,
        handler: Any,
        message: dict[str, Any],
        bot_handler: Any,
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message_id: int,
        stream: str,
        topic: str,
        post_started_progress: bool,
        requesting_user_id: int | None = None,
        session_kind: str = "shared",
        source_stream: str = "",
        source_topic: str = "",
        source_message_id: int | None = None,
        progress_message_id: int | None = None,
    ) -> None:
        self.handler = handler
        self.message = message
        self.bot_handler = bot_handler
        self.content = content
        self.active_session = active_session
        self.message_id = message_id
        self.stream = stream
        self.topic = topic
        self.post_started_progress = post_started_progress
        self.requesting_user_id = requesting_user_id
        self.session_kind = session_kind
        self.source_stream = source_stream
        self.source_topic = source_topic
        self.source_message_id = source_message_id
        self.progress_message_id = progress_message_id

        self.started_progress_posted = False
        self.result_message_id = progress_message_id
        self.transcript_events: list[PearWorkerTranscriptEvent] = []
        self.task = pear_worker_task_line(content)
        self.last_transcript_flush_at = 0.0
        self.last_volatile_transcript_flush_at = 0.0
        self.assistant_persistent_flush_seen = False
        self.assistant_volatile_flush_seen = False
        self.steering_events_after: Any = None
        self.last_steering_event_id = 0
        self.steering_segment_count = 0
        self.worker_result: dict[str, Any] = {}
        self.codex_thread_id: str | None = None
        self.result_summary = ""
        self.typing_started = False
        self.last_typing_signal_at = 0.0

    def signal_typing(self, operator: str, *, force: bool = True) -> None:
        """Topic-level "Pear is working" dots (design R1), throttled below
        the server's typing-start expiry so long runs keep the signal
        alive without spamming events."""
        if operator == "stop" and not self.typing_started:
            return
        if operator == "start" and not force:
            current_time = time.monotonic()
            if current_time - self.last_typing_signal_at < PEAR_TYPING_REFRESH_INTERVAL_SECONDS:
                return
        sent = send_pear_worker_typing_notification(
            self.bot_handler,
            stream=self.stream,
            topic=self.topic,
            operator=operator,
        )
        if sent and operator == "start":
            self.typing_started = True
            self.last_typing_signal_at = time.monotonic()
        if operator == "stop":
            self.typing_started = False

    def raise_if_cancel_requested(self) -> None:
        if not self.handler.worker_run_cancel_requested(self.bot_handler, self.message_id):
            return
        self.handler.worker_runtime.interrupt()
        raise PearRunCancelled("Cancelled by user.")

    def update_live_transcript(
        self,
        status: PearWorkerRunStatus,
        *,
        tool_results: Sequence[PearWorkerToolResult] = (),
        force: bool = False,
        flush_interval: float = PEAR_BACKGROUND_TRANSCRIPT_FLUSH_INTERVAL_SECONDS,
    ) -> bool:
        target_message_id = self.result_message_id or self.progress_message_id
        if target_message_id is None:
            return False
        current_time = time.monotonic()
        if not force and current_time - self.last_transcript_flush_at < flush_interval:
            return False
        content_to_update = build_pear_live_transcript_message(
            task=self.task,
            status=status,
            transcript_events=self.transcript_events,
            tool_results=tool_results,
        )
        updated = update_pear_worker_status_message(
            self.bot_handler,
            progress_message_id=target_message_id,
            content=content_to_update,
            rendering_only=True,
        )
        if updated:
            self.result_message_id = target_message_id
            self.last_transcript_flush_at = current_time
        return updated

    def update_volatile_transcript(
        self,
        status: PearWorkerRunStatus,
        *,
        tool_results: Sequence[PearWorkerToolResult] = (),
        force: bool = False,
        flush_interval: float = PEAR_VOLATILE_ASSISTANT_FLUSH_INTERVAL_SECONDS,
    ) -> bool:
        current_time = time.monotonic()
        if not force and current_time - self.last_volatile_transcript_flush_at < flush_interval:
            return False
        content_to_update = build_pear_live_transcript_message(
            task=self.task,
            status=status,
            transcript_events=self.transcript_events,
            tool_results=tool_results,
        )
        write_pear_live_run_state(
            self.message_id,
            status=status,
            content=content_to_update,
        )
        self.last_volatile_transcript_flush_at = current_time
        return True

    def maybe_apply_same_turn_steering(self) -> None:
        if (
            self.active_session is None
            or not callable(self.steering_events_after)
            or self.steering_segment_count >= PEAR_MAX_STEERING_SEGMENTS
        ):
            return
        pending_steering_events = self.steering_events_after(
            self.bot_handler,
            self.message_id,
            after_event_id=self.last_steering_event_id,
        )
        if not pending_steering_events:
            return
        latest_steering = "; ".join(
            event["instruction"] for event in pending_steering_events
        )
        if not self.handler.worker_runtime.steer(latest_steering):
            return
        self.last_steering_event_id = pending_steering_events[-1]["event_id"]
        self.steering_segment_count += 1
        self.active_session["latest_steering_instruction"] = latest_steering
        self.handler.record_worker_event(
            self.bot_handler,
            self.message_id,
            "steering_applied",
            summary=trim_pear_text(latest_steering, limit=500),
            payload={
                "segment_index": self.steering_segment_count,
                "steering_events": pending_steering_events,
                "same_turn": True,
            },
        )
        self.transcript_events.append(
            {
                "kind": "steering",
                "status": "done",
                "title": "Steering applied",
                "body": latest_steering,
            }
        )
        self.update_volatile_transcript("running", force=True)
        self.update_live_transcript("running", force=True)

    def handle_transcript_event(self, event: PearWorkerTranscriptEvent) -> None:
        self.raise_if_cancel_requested()
        self.signal_typing("start", force=False)
        self.transcript_events.append(event)
        title = event.get("title", "").strip()
        body = event.get("body", "").strip()
        summary = title or body or "Codex event."
        event_kind = event.get("kind")
        metadata = event.get("metadata")
        streaming_assistant = (
            event_kind == "assistant"
            and event.get("status") == "running"
            and isinstance(metadata, dict)
            and metadata.get("streaming") is True
        )
        if not streaming_assistant:
            self.handler.record_worker_event(
                self.bot_handler,
                self.message_id,
                "transcript",
                summary=trim_pear_text(summary, limit=500),
                payload=dict(event),
            )
        usage = (
            pear_normalize_token_usage(metadata.get("usage"))
            if isinstance(metadata, dict)
            else None
        )
        if usage is not None:
            self.handler.record_worker_event(
                self.bot_handler,
                self.message_id,
                "usage",
                summary=pear_token_usage_summary(usage),
                payload={"usage": usage},
            )
        if summary and event_kind != "usage" and not streaming_assistant:
            self.handler.update_worker_metadata(
                self.bot_handler,
                self.message_id,
                current_activity=trim_pear_text(summary, limit=500),
            )
        sparse_event = event_kind in {
            "command",
            "error",
            "file_change",
            "plan",
            "session",
            "status",
            "tool",
        }
        if event_kind == "assistant":
            if self.update_volatile_transcript(
                "running",
                force=not self.assistant_volatile_flush_seen,
                flush_interval=PEAR_VOLATILE_ASSISTANT_FLUSH_INTERVAL_SECONDS,
            ):
                self.assistant_volatile_flush_seen = True
            if self.update_live_transcript(
                "running",
                force=not self.assistant_persistent_flush_seen,
                flush_interval=PEAR_PERSISTENT_ASSISTANT_FLUSH_INTERVAL_SECONDS,
            ):
                self.assistant_persistent_flush_seen = True
        else:
            self.update_volatile_transcript("running", force=sparse_event)
            self.update_live_transcript("running", force=sparse_event)
        if event_kind in {"assistant", "command", "file_change", "plan", "tool"}:
            self.maybe_apply_same_turn_steering()


    def run_runtime_segment(self, segment_task: str, *, segment_index: int) -> dict[str, Any]:
        self.raise_if_cancel_requested()
        segment_result = self.handler.worker_runtime.run(
            active_session=self.active_session,
            task=segment_task,
            event_callback=self.handle_transcript_event,
        )
        self.raise_if_cancel_requested()
        segment_summary = pear_worker_summary_from_tool_results(
            segment_result["tool_results"]
        )
        self.handler.record_worker_event(
            self.bot_handler,
            self.message_id,
            "tool_results",
            summary=segment_summary or "Pear worker returned results.",
            payload={
                "segment_index": segment_index,
                "tool_count": segment_result["tool_count"],
                "tool_results": segment_result["tool_results"],
                "worker_can_run": segment_result["worker_can_run"],
            },
        )
        return segment_result


    def embed_result_images(self, content: str) -> str:
        workspace_context = self.active_session.get("workspace_context") or {}
        workdir = str(workspace_context.get("workdir") or "").strip()
        if not workdir.startswith("/"):
            return content
        from zerver.lib.pear_worker import (
            pear_topic_isolation_user_exists,
            pear_topic_isolation_username,
        )

        isolation_username: str | None = pear_topic_isolation_username(
            self.bot_handler.user_profile,
            stream=self.stream,
            topic=self.topic,
        )
        if isolation_username is not None and not pear_topic_isolation_user_exists(
            isolation_username
        ):
            isolation_username = None
        return pear_embed_workspace_images(
            content,
            workdir=workdir,
            isolation_username=isolation_username,
            bot_profile=self.bot_handler.user_profile,
        )

    def run(self) -> None:
        try:
            if self.active_session is None and self.session_kind == "side":
                self.active_session = build_pear_active_session_summary(
                    pear_session=get_pear_session_payload(
                        stream=self.source_stream or self.stream,
                        topic=self.source_topic or self.topic,
                    ),
                    source="server",
                    stream_exists=True,
                    topic_message_count=1,
                    request_count=1,
                    latest_request_message_id=self.message_id,
                    latest_message_id=None,
                    checklist=[],
                    artifacts=[],
                )
            else:
                loaded_active_session = None
                if self.session_kind == "shared" and self.handler.active_session_loader is not None:
                    try:
                        loaded_active_session = self.handler.active_session_loader(
                            self.message,
                            self.bot_handler,
                        )
                    except Exception as error:
                        raise RuntimeError("Pear active session loader failed.") from error
                self.active_session = (
                    loaded_active_session
                    or self.active_session
                    or build_trigger_pear_active_session(
                        self.message,
                        self.content,
                    )
            )
            self.task = pear_worker_task_for_message(self.content, self.active_session)
            runtime_label = self.handler.worker_runtime.runtime_value()
            if not callable(getattr(self.handler.worker_run_store, "claim_for_run", None)):
                self.handler.worker_run_store.record(
                    self.bot_handler,
                    self.message_id,
                    "running",
                    stream=self.stream,
                    topic=self.topic,
                )
            self.handler.update_worker_metadata(
                self.bot_handler,
                self.message_id,
                requesting_user_id=self.requesting_user_id,
                session_kind=self.session_kind,
                source_stream=self.source_stream,
                source_topic=self.source_topic,
                source_message_id=self.source_message_id,
                summary="Running Pear worker.",
                current_activity=self.task,
                runtime_label=runtime_label,
            )
            self.handler.record_worker_event(
                self.bot_handler,
                self.message_id,
                "running",
                summary="Started Pear worker runtime.",
                payload={
                    "task": self.task,
                    "runtime": runtime_label,
                    "stream": self.stream,
                    "topic": self.topic,
                    "session_kind": self.session_kind,
                },
            )
            self.signal_typing("start")
            if self.active_session["worker_can_run"]:
                initial_tool_results: list[PearWorkerToolResult] = [
                    {
                        "label": "Runtime",
                        "value": runtime_label,
                    },
                    {
                        "label": "Verification",
                        "value": "Pear worker accepted this request and started the agent runtime.",
                    },
                ]
                progress_content = build_pear_worker_progress_message(
                    task=self.task,
                    tool_results=initial_tool_results,
                    transcript_events=self.transcript_events,
                )
                self.update_volatile_transcript(
                    "running",
                    tool_results=initial_tool_results,
                    force=True,
                )
                progress_updated = self.update_live_transcript(
                    "running",
                    tool_results=initial_tool_results,
                    force=True,
                )
                result = None
                if progress_updated:
                    self.result_message_id = self.result_message_id or self.progress_message_id
                elif self.post_started_progress:
                    result = self.bot_handler.send_reply(self.message, progress_content)
                self.started_progress_posted = progress_updated or result is not None
                if isinstance(result, dict) and isinstance(result.get("id"), int):
                    self.result_message_id = result["id"]
                if self.result_message_id is not None:
                    self.handler.worker_run_store.record(
                        self.bot_handler,
                        self.message_id,
                        "running",
                        stream=self.stream,
                        topic=self.topic,
                        result_message_id=self.result_message_id,
                    )
                    self.handler.record_worker_event(
                        self.bot_handler,
                        self.message_id,
                        "progress_posted",
                        summary=(
                            "Updated started-progress message."
                            if progress_updated
                            else "Posted started-progress message."
                        ),
                        payload={"result_message_id": self.result_message_id},
                    )
            self.steering_events_after = getattr(
                self.handler.worker_run_store,
                "steering_events_after",
                None,
            )

            self.worker_result = self.run_runtime_segment(self.task, segment_index=0)
            self.codex_thread_id = pear_codex_thread_id_from_tool_results(
                self.worker_result["tool_results"]
            )
            while (
                self.worker_result["worker_can_run"]
                and callable(self.steering_events_after)
                and self.steering_segment_count < PEAR_MAX_STEERING_SEGMENTS
            ):
                pending_steering_events = self.steering_events_after(
                    self.bot_handler,
                    self.message_id,
                    after_event_id=self.last_steering_event_id,
                )
                if not pending_steering_events:
                    break
                self.last_steering_event_id = pending_steering_events[-1]["event_id"]
                self.steering_segment_count += 1
                steering_task = build_pear_steering_task(pending_steering_events)
                latest_steering = "; ".join(
                    event["instruction"] for event in pending_steering_events
                )
                self.active_session["latest_steering_instruction"] = latest_steering
                if self.codex_thread_id:
                    self.active_session["latest_codex_thread_id"] = self.codex_thread_id
                self.handler.update_worker_metadata(
                    self.bot_handler,
                    self.message_id,
                    current_activity=trim_pear_text(
                        f"Applying steering: {latest_steering}",
                        limit=500,
                    ),
                )
                self.handler.record_worker_event(
                    self.bot_handler,
                    self.message_id,
                    "steering_applied",
                    summary=trim_pear_text(latest_steering, limit=500),
                    payload={
                        "segment_index": self.steering_segment_count,
                        "steering_events": pending_steering_events,
                    },
                )
                self.transcript_events.append(
                    {
                        "kind": "steering",
                        "status": "done",
                        "title": "Steering applied",
                        "body": latest_steering,
                    }
                )
                self.update_volatile_transcript("running", force=True)
                self.update_live_transcript("running", force=True)
                self.worker_result = self.run_runtime_segment(
                    steering_task,
                    segment_index=self.steering_segment_count,
                )
                self.codex_thread_id = pear_codex_thread_id_from_tool_results(
                    self.worker_result["tool_results"]
                ) or self.codex_thread_id

            self.result_summary = pear_worker_summary_from_tool_results(
                self.worker_result["tool_results"]
            )
            if self.worker_result["should_post"] and self.worker_result["content"] is not None:
                content_to_post = self.worker_result["content"]
                content_chunks = split_pear_persistent_message(content_to_post)
                if self.started_progress_posted or self.transcript_events:
                    content_chunks = build_pear_worker_completed_message_chunks(
                        task=self.task,
                        tool_results=self.worker_result["tool_results"],
                        transcript_events=self.transcript_events,
                    )
                    content_to_post = content_chunks[0] if content_chunks else ""
                content_to_post = self.embed_result_images(content_to_post)
                result_updated = update_pear_worker_status_message(
                    self.bot_handler,
                    progress_message_id=self.result_message_id or self.progress_message_id,
                    content=content_to_post,
                )
                result = None
                if not result_updated:
                    result = self.bot_handler.send_reply(
                        self.message,
                        normalize_pear_task_markers(
                            normalize_pear_math_delimiters(content_to_post)
                        ),
                    )
                for continuation_content in content_chunks[1:]:
                    self.bot_handler.send_reply(
                        self.message,
                        normalize_pear_task_markers(
                            normalize_pear_math_delimiters(continuation_content)
                        ),
                    )
                write_pear_live_run_state(
                    self.message_id,
                    status="done",
                    content=content_to_post,
                )
                if isinstance(result, dict) and isinstance(result.get("id"), int):
                    self.result_message_id = result["id"]
                if self.result_message_id is not None:
                    self.handler.record_worker_event(
                        self.bot_handler,
                        self.message_id,
                        "result_updated" if result_updated else "result_posted",
                        summary=(
                            "Updated worker result message."
                            if result_updated
                            else "Posted worker result message."
                        ),
                        payload={"result_message_id": self.result_message_id},
                    )
            elif self.result_message_id is not None or self.progress_message_id is not None:
                content_chunks = build_pear_worker_completed_message_chunks(
                    task=self.task,
                    tool_results=self.worker_result["tool_results"],
                    transcript_events=self.transcript_events,
                )
                content_to_post = content_chunks[0] if content_chunks else ""
                content_to_post = self.embed_result_images(content_to_post)
                target_message_id = self.result_message_id or self.progress_message_id
                result_updated = update_pear_worker_status_message(
                    self.bot_handler,
                    progress_message_id=target_message_id,
                    content=content_to_post,
                )
                if result_updated:
                    for continuation_content in content_chunks[1:]:
                        self.bot_handler.send_reply(
                        self.message,
                        normalize_pear_task_markers(
                            normalize_pear_math_delimiters(continuation_content)
                        ),
                    )
                write_pear_live_run_state(
                    self.message_id,
                    status="done",
                    content=content_to_post,
                )
                if result_updated:
                    self.result_message_id = target_message_id
                    self.handler.record_worker_event(
                        self.bot_handler,
                        self.message_id,
                        "result_updated",
                        summary="Settled worker progress message.",
                        payload={"result_message_id": self.result_message_id},
                    )
            self.handler.worker_run_store.record(
                self.bot_handler,
                self.message_id,
                "done",
                stream=self.stream,
                topic=self.topic,
                result_message_id=self.result_message_id,
            )
            self.handler.update_worker_metadata(
                self.bot_handler,
                self.message_id,
                summary=self.result_summary or "Completed.",
                current_activity="Completed.",
                codex_thread_id=self.codex_thread_id,
            )
            self.handler.record_worker_event(
                self.bot_handler,
                self.message_id,
                "done",
                summary=self.result_summary or "Completed.",
                payload={"result_message_id": self.result_message_id},
            )
        except PearRunCancelled as error:
            self.handler.worker_run_store.record(
                self.bot_handler,
                self.message_id,
                "cancelled",
                stream=self.stream,
                topic=self.topic,
                result_message_id=self.result_message_id,
                error=str(error),
            )
            self.handler.update_worker_metadata(
                self.bot_handler,
                self.message_id,
                summary=str(error),
                current_activity="Cancelled.",
            )
            self.handler.record_worker_event(
                self.bot_handler,
                self.message_id,
                "cancelled",
                summary=str(error),
                payload={"result_message_id": self.result_message_id},
            )
            self.transcript_events.append(
                {
                    "kind": "status",
                    "status": "cancelled",
                    "title": "Pear worker cancelled.",
                    "body": str(error),
                }
            )
            cancelled_message = build_pear_live_transcript_message(
                task=self.task,
                status="cancelled",
                transcript_events=self.transcript_events,
            )
            write_pear_live_run_state(
                self.message_id,
                status="cancelled",
                content=cancelled_message,
            )
            update_pear_worker_status_message(
                self.bot_handler,
                progress_message_id=self.result_message_id or self.progress_message_id,
                content=cancelled_message,
            )
        except Exception as error:
            self.handler.worker_run_store.record(
                self.bot_handler,
                self.message_id,
                "failed",
                stream=self.stream,
                topic=self.topic,
                result_message_id=self.result_message_id,
                error=str(error),
            )
            self.handler.update_worker_metadata(
                self.bot_handler,
                self.message_id,
                summary=str(error),
                current_activity="Failed.",
            )
            self.handler.record_worker_event(
                self.bot_handler,
                self.message_id,
                "failed",
                summary=str(error),
                payload={"result_message_id": self.result_message_id},
            )
            self.transcript_events.append(
                {
                    "kind": "error",
                    "status": "failed",
                    "title": "Pear worker failed.",
                    "body": str(error),
                }
            )
            failure_message = build_pear_live_transcript_message(
                task=self.task,
                status="failed",
                transcript_events=self.transcript_events,
            )
            write_pear_live_run_state(
                self.message_id,
                status="failed",
                content=failure_message,
            )
            if not update_pear_worker_status_message(
                self.bot_handler,
                progress_message_id=self.result_message_id or self.progress_message_id,
                content=failure_message,
            ):
                self.bot_handler.send_reply(self.message, failure_message)
        finally:
            self.signal_typing("stop")


def clean_pear_session_target(
    value: str | None,
    default: str,
    *,
    allow_empty: bool = False,
) -> str:
    if value is None:
        return default
    value = value.strip()
    if allow_empty:
        return value
    return value if value else default


def get_pear_session_payload(
    *,
    stream: str | None = None,
    topic: str | None = None,
) -> PearSessionPayload:
    defaults = get_pear_session_defaults()
    stream_name = clean_pear_session_target(stream, defaults.stream)
    topic_name = clean_pear_session_target(topic, defaults.topic, allow_empty=True)
    return {
        "agent_name": defaults.agent_name,
        "agent_mention": defaults.agent_mention,
        "stream": stream_name,
        "topic": topic_name,
        "topic_prefix": defaults.topic_prefix,
        "checklist": list(defaults.checklist),
        "seed_message": build_pear_seed_message(),
        "steer_message": build_pear_steer_message(),
        "fix_bug_message": build_pear_fix_bug_message(),
        "status_check_message": build_pear_status_check_message(),
        "handoff_message": build_pear_handoff_message(),
        "catch_up_message": build_pear_catch_up_message(),
        "triage_message": build_pear_triage_message(),
        "find_answer_message": build_pear_find_answer_message(),
        "review_message": build_pear_review_message(),
        "plan_message": build_pear_plan_message(),
        "checkpoint_message": build_pear_checkpoint_message(),
        "apply_plan_message": build_pear_apply_plan_message(),
        "save_work_message": build_pear_save_work_message(),
        "verify_changes_message": build_pear_verify_changes_message(),
        "draft_artifact_message": build_pear_draft_artifact_message(),
        "access_message": build_pear_access_message(),
        "pause_message": build_pear_pause_message(),
        "resume_message": build_pear_resume_message(),
        "official_agent_runtime": PEAR_OFFICIAL_AGENT_RUNTIME,
        "official_agent_runtime_label": PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
        "agent_runtime_packages": list(PEAR_AGENT_RUNTIME_PACKAGES),
        "official_codex_sdk_packages": list(PEAR_OFFICIAL_CODEX_SDK_PACKAGES),
        "codex_mcp_tools": list(PEAR_CODEX_MCP_TOOLS),
        "pear_topic_state_tools": list(PEAR_TOPIC_STATE_TOOLS),
        "pear_topic_state_tool_names": list(PEAR_TOPIC_STATE_TOOL_NAMES),
        "server_contract_version": PEAR_SERVER_CONTRACT_VERSION,
    }
