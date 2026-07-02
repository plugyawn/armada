import inspect
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from re import IGNORECASE, MULTILINE, finditer, search, sub
from typing import Any, Final, Literal, Protocol, TypedDict

from django.core.cache import cache


PEAR_AGENT_NAME: Final = "Pear"
PEAR_BOT_SHORT_NAME: Final = "pear"
PEAR_EMBEDDED_BOT_SERVICE: Final = "pear"
PEAR_EXPLICIT_MENTION_MESSAGE_KEY: Final = "pear_explicit_mention"
PEAR_TRIGGER_MESSAGE_KEY: Final = "pear_trigger"
PEAR_FOLLOWUP_TRIGGER: Final = "pear_followup"
PEAR_AGENT_MENTION: Final = "@**Pear**"
PEAR_DEFAULT_STREAM: Final = "pair-programming"
PEAR_TOPIC_PREFIX: Final = "pear/"
PEAR_DEFAULT_TOPIC: Final = "pear/session"
PEAR_SESSION_ENDPOINT: Final = "/pear/session"
PEAR_ACTIVE_SESSION_ENDPOINT: Final = "/pear/session/active"
PEAR_CONFIG_ENDPOINT: Final = "/pear/config"
PEAR_TOPIC_CONTEXT_ENDPOINT: Final = "/pear/topic-context"
PEAR_RUNS_ENDPOINT: Final = "/pear/runs"
PEAR_SIDE_ENDPOINT: Final = "/pear/side"
PEAR_OFFICIAL_AGENT_RUNTIME: Final = "codex-app-server"
PEAR_OFFICIAL_AGENT_RUNTIME_LABEL: Final = "Codex app-server"
PEAR_AGENT_RUNTIME_PACKAGES: Final = ("openai-agents", "openai-codex")
PEAR_OFFICIAL_CODEX_SDK_PACKAGES: Final = ("@openai/codex-sdk", "openai-codex")
PEAR_CODEX_MCP_TOOLS: Final = ("codex", "codex-reply")
PEAR_TOPIC_STATE_TOOLS: Final = (
    "Repo",
    "Runtime",
    "Verification",
    "Diff",
    "Branch",
    "PR",
    "Session",
    "Ticket",
    "Issue",
    "Reviewers",
    "Routine",
    "Routine cadence",
    "Routine disabled",
    "Memory",
    "Memory forgotten",
    "Done",
    "Doc",
)
PEAR_TOPIC_STATE_TOOL_NAMES: Final = (
    "pear_record_repo",
    "pear_record_runtime",
    "pear_record_verification",
    "pear_record_diff",
    "pear_record_branch",
    "pear_record_pr",
    "pear_record_session",
    "pear_record_ticket",
    "pear_record_issue",
    "pear_record_reviewers",
    "pear_record_routine",
    "pear_record_routine_cadence",
    "pear_record_routine_disabled",
    "pear_record_memory",
    "pear_record_memory_forgotten",
    "pear_record_done",
    "pear_record_doc",
)


def pear_topic_state_tool_name(label: str) -> str:
    slug = "".join(
        character.lower() if character.isalnum() else "_" for character in label
    )
    while "__" in slug:
        slug = slug.replace("__", "_")
    return f"pear_record_{slug.strip('_')}"


PEAR_AGENT_SKILLS: Final = ("standing-routines",)
PEAR_REASONING_EFFORTS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh")
PEAR_AGENT_SKILL_DEFINITIONS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "standing-routines",
        "description": "Use when a Pear request asks for standing work such as a scheduled job, channel watch, or pull-request subscription.",
        "applies_to": [
            "scheduled jobs or recurring digests",
            "channel or topic watches",
            "pull-request subscriptions or follow-up monitors",
        ],
        "instructions": [
            "Do not run the standing work immediately unless the user explicitly asks to run it now.",
            "Preserve the user's request as durable topic state with a Routine artifact line.",
            "Add a Routine cadence artifact only when the user gave an explicit cadence.",
            "Keep the acknowledgement concise and suitable for the Zulip topic.",
            "If the request is ambiguous between a one-off task and standing work, treat it as a normal task and continue the pair-programming workflow.",
        ],
        "artifact_tools": [
            "Routine",
            "Routine cadence",
            "Verification",
        ],
    },
)
PEAR_SERVER_CONTRACT_VERSION: Final = 38
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
PEAR_MEMORY_PROMPT: Final = "Remember:"
PEAR_MEMORY_STATUS_PROMPT: Final = "What do you remember about this topic?"
PEAR_MEMORY_FORGET_PROMPT: Final = "Forget memory:"
PEAR_DONE_PROMPT: Final = "Definition of done:"
PEAR_RESPONSE_POLICY_PROMPT: Final = "Response policy:"
PEAR_ROUTINE_CADENCE_PROMPT: Final = "Routine cadence:"
PEAR_ROUTINE_STATUS_PROMPT: Final = "What routines do you have set up in this topic?"
PEAR_RUN_ROUTINE_PROMPT: Final = "Run routine:"
PEAR_DISABLE_ROUTINE_PROMPT: Final = "Disable routine:"
PEAR_UPDATE_ROUTINE_PROMPT: Final = "Update routine:"
PEAR_PAUSE_PROMPT: Final = "Pause session:"
PEAR_RESUME_PROMPT: Final = "Resume session:"
PEAR_SELECTED_BOT_CONFIG_KEY: Final = "pear_selected"
PEAR_SELECTED_BOT_CONFIG_VALUE: Final = "true"
PEAR_WORKER_RUNS_STORAGE_KEY: Final = "pear_worker_runs"
PEAR_ADDRESSING_ROUTER_STORAGE_KEY: Final = "pear_addressing_router"
PEAR_WORKER_RUNS_STORAGE_LIMIT: Final = 50
PEAR_MAX_STEERING_SEGMENTS: Final = 4
PEAR_WORKER_INTENT_TERMS: Final = (
    "apply",
    "branch",
    "bug",
    "build",
    "catch up",
    "check",
    "checkpoint",
    "ci",
    "code",
    "codex",
    "commit",
    "debug",
    "deploy",
    "diagnose",
    "diff",
    "draft",
    "edit",
    "fix",
    "handoff",
    "implement",
    "investigate",
    "issue",
    "merge",
    "pair",
    "pairing",
    "plan",
    "pr",
    "refactor",
    "repo",
    "review",
    "runtime",
    "server",
    "smoke",
    "status",
    "test",
    "triage",
    "verify",
    "workspace",
)
PEAR_STANDING_WORK_INTENT_TERMS: Final = (
    "cadence",
    "daily",
    "digest",
    "every day",
    "every weekday",
    "follow up",
    "monitor",
    "routine",
    "schedule",
    "subscribe",
    "watch",
    "weekly",
)
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
PEAR_LIVE_RUN_CACHE_KEY_PREFIX: Final = "pear:live-run:"
PEAR_LIVE_RUN_CACHE_TTL_SECONDS: Final = 3600
PEAR_MARKDOWN_IMAGE_PATTERN: Final = (
    r"!\[([^\]\n]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)"
)
PEAR_CHECKLIST_PATTERN: Final = r"^\s*[-*]\s+\[([ xX~])\]\s+(.+?)\s*$"
PEAR_ARTIFACT_PATTERN: Final = (
    r"^\s*(?:[-*]\s*)?"
    r"(Repo|Repository|Runtime|Branch|PR|Pull request|Diff|Test|Tests|Verification|Screenshot|"
    r"Session|Session record|Pear session|Codex session|"
    r"Doc|Docs|Issue|Ticket|Reviewer|Reviewers|Routine disabled|Disabled routine|Routine off|"
    r"Routine|Routines|Routine cadence|Cadence|"
    r"Schedule|Watch cadence|Memory forgotten|Forgotten memory|Memory off|"
    r"Memory|Memories|Done|"
    r"Definition of done|Success criteria|Ambient|Response policy|Followups|Follow-up mode):"
    r"[ \t]*([^\r\n]*?)[ \t]*$"
)
PEAR_VISIBLE_ARTIFACT_FALLBACK_PATTERN: Final = (
    r"^\s*(?:[-*]\s*)?(Result|Done):[ \t]*([^\r\n]*?)[ \t]*$"
)
PEAR_TRANSCRIPT_TOOL_DETAIL_KINDS: Final = frozenset(
    {"command", "file_change", "tool"}
)
PearChecklistStatus = Literal["pending", "in_progress", "done"]
PearActiveSessionSource = Literal["server", "cache"]
PearActiveSessionState = Literal["not_started", "requested", "active", "paused"]
PearResponsePolicy = Literal["ambient", "mentions_only"]
PEAR_DEFAULT_RESPONSE_POLICY: Final[PearResponsePolicy] = "mentions_only"
logger = logging.getLogger(__name__)
PearWorkerState = Literal["ready", "blocked_missing_stream", "blocked_paused"]
PearWorkerRunStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class PearRunCancelled(Exception):
    pass


PearArtifactKind = Literal[
    "repository",
    "runtime",
    "branch",
    "pull_request",
    "diff",
    "verification",
    "screenshot",
    "session_record",
    "document",
    "ticket",
    "reviewers",
    "routine",
    "routine_cadence",
    "routine_disabled",
    "memory",
    "memory_forgotten",
    "done_criteria",
    "response_policy",
]
PearInterruptionKind = Literal["pause", "resume"]
PEAR_ARTIFACT_KIND_BY_LABEL: Final[dict[str, PearArtifactKind]] = {
    "repo": "repository",
    "repository": "repository",
    "runtime": "runtime",
    "branch": "branch",
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
    "doc": "document",
    "docs": "document",
    "issue": "ticket",
    "ticket": "ticket",
    "reviewer": "reviewers",
    "reviewers": "reviewers",
    "routine disabled": "routine_disabled",
    "disabled routine": "routine_disabled",
    "routine off": "routine_disabled",
    "routine": "routine",
    "routines": "routine",
    "routine cadence": "routine_cadence",
    "cadence": "routine_cadence",
    "schedule": "routine_cadence",
    "watch cadence": "routine_cadence",
    "memory forgotten": "memory_forgotten",
    "forgotten memory": "memory_forgotten",
    "memory off": "memory_forgotten",
    "memory": "memory",
    "memories": "memory",
    "done": "done_criteria",
    "definition of done": "done_criteria",
    "success criteria": "done_criteria",
    "ambient": "response_policy",
    "response policy": "response_policy",
    "followups": "response_policy",
    "follow-up mode": "response_policy",
}


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
    memory_message: str
    memory_status_message: str
    memory_forget_message: str
    done_message: str
    response_policy_message: str
    routine_cadence_message: str
    routine_status_message: str
    disable_routine_message: str
    update_routine_message: str
    pause_message: str
    resume_message: str
    official_agent_runtime: str
    official_agent_runtime_label: str
    agent_runtime_packages: list[str]
    official_codex_sdk_packages: list[str]
    codex_mcp_tools: list[str]
    pear_topic_state_tools: list[str]
    pear_topic_state_tool_names: list[str]
    pear_agent_skills: list[str]
    pear_agent_skill_definitions: list[dict[str, object]]
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
    routine_count: int
    latest_routine_message_id: int | None
    latest_routine: str | None
    memory_count: int
    latest_memory_message_id: int | None
    latest_memory: str | None
    memory_forgotten_count: int
    latest_memory_forgotten_message_id: int | None
    latest_memory_forgotten: str | None
    done_criteria_count: int
    latest_done_criteria_message_id: int | None
    latest_done_criteria: str | None
    response_policy_count: int
    latest_response_policy_message_id: int | None
    latest_response_policy: str | None
    response_policy: PearResponsePolicy
    routine_cadence_count: int
    latest_routine_cadence_message_id: int | None
    latest_routine_cadence: str | None
    routine_disabled_count: int
    latest_routine_disabled_message_id: int | None
    latest_routine_disabled: str | None
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
PearWorkerToolRunner = Callable[..., Sequence[PearWorkerToolResult]]
PearWorkerActiveSessionLoader = Callable[[dict[str, Any], Any], PearActiveSessionSummary]


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
            "Possible duplicate or related work: check this topic's artifacts, memory, routines, linked PRs, and linked issues.",
            "Answer if already known: summarize the settled answer or decision from this topic.",
            "Suggested owner or reviewer: infer from Reviewers, Routine, PR, Ticket, or Issue artifacts when possible.",
            "Suggested scope: classify as bug, feature, verification, docs, or follow-up work.",
            "Priority and risk: ",
            "Next action: ",
            "Durable output: include Ticket, Issue, Reviewers, Routine, or Done artifact lines only when they are concrete.",
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


def build_pear_memory_message(memory: str = "") -> str:
    memory = memory.strip()
    if memory:
        return f"{PEAR_AGENT_MENTION} {PEAR_MEMORY_PROMPT}\n\nMemory: {memory}"
    return f"{PEAR_AGENT_MENTION} {PEAR_MEMORY_PROMPT}\n\nMemory: "


def build_pear_memory_status_message() -> str:
    return f"{PEAR_AGENT_MENTION} {PEAR_MEMORY_STATUS_PROMPT}"


def build_pear_memory_forget_message(memory: str = "") -> str:
    memory = memory.strip()
    if memory:
        return f"{PEAR_AGENT_MENTION} {PEAR_MEMORY_FORGET_PROMPT}\n\nMemory forgotten: {memory}"
    return f"{PEAR_AGENT_MENTION} {PEAR_MEMORY_FORGET_PROMPT}\n\nMemory forgotten: "


def build_pear_done_message(done_criteria: str = "") -> str:
    done_criteria = done_criteria.strip()
    if done_criteria:
        return f"{PEAR_AGENT_MENTION} {PEAR_DONE_PROMPT}\n\nDone: {done_criteria}"
    return f"{PEAR_AGENT_MENTION} {PEAR_DONE_PROMPT}\n\nDone: "


def build_pear_response_policy_message(policy: str = "mentions only") -> str:
    policy = policy.strip()
    if policy:
        return f"{PEAR_AGENT_MENTION} {PEAR_RESPONSE_POLICY_PROMPT}\n\nAmbient: {policy}"
    return f"{PEAR_AGENT_MENTION} {PEAR_RESPONSE_POLICY_PROMPT}\n\nAmbient: "


def build_pear_routine_cadence_message(cadence: str = "manual") -> str:
    cadence = cadence.strip()
    if cadence:
        return f"{PEAR_AGENT_MENTION} {PEAR_ROUTINE_CADENCE_PROMPT}\n\nRoutine cadence: {cadence}"
    return f"{PEAR_AGENT_MENTION} {PEAR_ROUTINE_CADENCE_PROMPT}\n\nRoutine cadence: "


def build_pear_routine_status_message() -> str:
    return f"{PEAR_AGENT_MENTION} {PEAR_ROUTINE_STATUS_PROMPT}"


def build_pear_disable_routine_message(routine: str = "") -> str:
    routine = routine.strip()
    if routine:
        return f"{PEAR_AGENT_MENTION} {PEAR_DISABLE_ROUTINE_PROMPT}\n\nRoutine disabled: {routine}"
    return f"{PEAR_AGENT_MENTION} {PEAR_DISABLE_ROUTINE_PROMPT}\n\nRoutine disabled: "


def build_pear_update_routine_message(
    old_routine: str = "",
    updated_routine: str = "",
) -> str:
    old_routine = old_routine.strip()
    updated_routine = updated_routine.strip()
    return "\n".join(
        [
            f"{PEAR_AGENT_MENTION} {PEAR_UPDATE_ROUTINE_PROMPT}",
            "",
            f"Routine disabled: {old_routine}",
            f"Routine: {updated_routine}",
        ]
    )


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
        return "✓"
    if status in {"failed", "error"}:
        return "!"
    return "…"


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
        label = "Ran" if status in {"done", "completed"} else "Running"
        if status in {"failed", "error"}:
            label = "Failed"
        return f"- {mark} {label}: `{subject}`"
    if kind == "file_change":
        label = "File changed" if status in {"done", "completed"} else "Changing file"
        if status in {"failed", "error"}:
            label = "File change failed"
        return f"- {mark} {label}: {subject or 'file change'}"
    if kind == "tool":
        label = "Tool"
        if status in {"running", "in_progress"}:
            label = "Tool running"
        elif status in {"done", "completed"}:
            label = "Tool completed"
        elif status in {"failed", "error"}:
            label = "Tool failed"
        return f"- {mark} {label}: {subject}"
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
        return f"Ran {completed_count}/{tool_count} {pear_pluralize_tool(tool_count)}"
    if running_count:
        return f"Running {tool_count} {pear_pluralize_tool(tool_count)}"
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


def pear_transcript_plan_lines(event: PearWorkerTranscriptEvent) -> list[str]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return []
    steps = metadata.get("steps")
    if not isinstance(steps, list):
        return []
    lines = ["Plan:"]
    for step in steps[:8]:
        if not isinstance(step, dict):
            continue
        text = str(step.get("step", "")).strip()
        if not text:
            continue
        status = str(step.get("status", "pending"))
        suffix = " _(in progress)_" if status == "in_progress" else ""
        lines.append(f"- {pear_transcript_checkbox(status)} {text}{suffix}")
    return lines


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
    for label in ("result", "done"):
        if label in fallback_by_label:
            value = normalize_pear_visible_markdown(fallback_by_label[label])
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
        if search(PEAR_ARTIFACT_PATTERN, stripped, MULTILINE | IGNORECASE):
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
        return "\n".join(pear_transcript_plan_lines(event))
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


def pear_append_active_marker(text: str) -> str:
    text = text.rstrip()
    if not text:
        return PEAR_ACTIVE_THINKING_MARKDOWN
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
    else:
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
    routine_count = active_session["routine_count"] if active_session is not None else 0
    memory_count = active_session["memory_count"] if active_session is not None else 0
    done_criteria_count = (
        active_session["done_criteria_count"] if active_session is not None else 0
    )
    routine_cadence_count = (
        active_session["routine_cadence_count"] if active_session is not None else 0
    )
    response_policy = (
        active_session["response_policy"] if active_session is not None else "unknown"
    )
    latest_routine = (
        active_session["latest_routine"] or "none"
        if active_session is not None
        else "none"
    )
    latest_memory = (
        active_session["latest_memory"] or "none"
        if active_session is not None
        else "none"
    )
    latest_done_criteria = (
        active_session["latest_done_criteria"] or "none"
        if active_session is not None
        else "none"
    )
    latest_routine_cadence = (
        active_session["latest_routine_cadence"] or "manual"
        if active_session is not None
        else "manual"
    )
    latest_verification = (
        active_session["latest_verification"] or "none"
        if active_session is not None
        else "none"
    )
    return "\n".join(
        [
            f"I can work from this Zulip topic: {stream} > {topic}.",
            "",
            "Pear can read the durable topic context, including the request, "
            "checklist, steering replies, pause/resume controls, and artifact "
            "lines such as Repo, Runtime, Branch, PR, Diff, Verification, "
            "Screenshot, Doc, Reviewers, Routine, Routine cadence, Memory, "
            "Done, and Ambient.",
            "When the server worker is enabled, Pear runs the configured Codex "
            "runtime with the server's explicit workdir, sandbox, approval "
            "policy, and model settings.",
            "zulip-terminal can act on posted artifacts: open links, copy values, "
            "check out branches, open compares, create draft PRs, mark PRs "
            "ready, post PR status, request reviewers, record PR-watch routines, "
            "and record or recall topic memory.",
            "",
            f"Worker state: {worker_state}",
            f"Worker access: {worker_access}",
            f"Artifacts seen: {artifact_count}",
            f"Routines seen: {routine_count}",
            f"Latest routine: {latest_routine}",
            f"Routine cadence records seen: {routine_cadence_count}",
            f"Latest routine cadence: {latest_routine_cadence}",
            f"Memories seen: {memory_count}",
            f"Latest memory: {latest_memory}",
            f"Done criteria seen: {done_criteria_count}",
            f"Latest done criteria: {latest_done_criteria}",
            f"Response policy: {response_policy}",
            f"Latest verification: {latest_verification}",
        ]
    )


def build_pear_memory_ack_message(memory: str = "") -> str:
    memory = memory.strip()
    lines = [
        "Saved. I'll treat this as Pear memory for this topic.",
    ]
    if memory:
        lines.extend(["", f"Memory: {memory}"])
    return "\n".join(lines)


def build_pear_memory_status_ack_message(
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    if active_session is None:
        return "\n".join(
            [
                "I cannot read Pear memory for this topic yet.",
                "",
                "Reason: topic context is unavailable.",
            ]
        )

    memory_artifacts = active_pear_memory_artifacts(active_session["artifacts"])
    if not memory_artifacts:
        return "\n".join(
            [
                f"I do not have Pear memory recorded for {active_session['stream']} > {active_session['topic']} yet.",
                "",
                "Use `@Pear Remember:` or zulip-terminal Meta M to add one. If memory was forgotten, repost a newer `Memory:` line to restore it.",
            ]
        )

    memory_lines = [
        f"- {artifact['value']}" for artifact in memory_artifacts[:10]
    ]
    return "\n".join(
        [
            f"I remember these notes for {active_session['stream']} > {active_session['topic']}:",
            "",
            *memory_lines,
            "",
            f"Forgotten memory records seen: {active_session['memory_forgotten_count']}",
        ]
    )


def build_pear_memory_forget_ack_message(
    memory: str = "",
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    memory = memory.strip()
    if not memory and active_session is not None and active_session["latest_memory"]:
        memory = active_session["latest_memory"]
    if not memory:
        return "\n".join(
            [
                "I do not know which Pear memory to forget yet.",
                "",
                "Use `@Pear Forget memory:` with a `Memory forgotten:` line, or record memory first and ask `@Pear forget latest memory`.",
            ]
        )
    return "\n".join(
        [
            "Noted. Pear will treat this memory as forgotten in this topic.",
            "",
            f"Memory forgotten: {memory}",
        ]
    )


def parse_pear_done_instruction(content: str) -> str:
    done_criteria = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if done_criteria.startswith(mention):
            done_criteria = done_criteria[len(mention) :].strip()
            break
    if done_criteria.startswith(PEAR_DONE_PROMPT):
        done_criteria = done_criteria[len(PEAR_DONE_PROMPT) :].strip()
    if done_criteria.lower().startswith("done:"):
        done_criteria = done_criteria[len("done:") :].strip()
    return done_criteria


def parse_pear_response_policy_instruction(content: str) -> str:
    response_policy_artifacts = [
        artifact
        for artifact in parse_pear_artifacts(content, message_id=0)
        if artifact["kind"] == "response_policy"
    ]
    if response_policy_artifacts:
        return response_policy_artifacts[0]["value"]
    if not is_pear_response_policy_template_message(content):
        policy_value = pear_response_policy_request_value(content)
        if policy_value is not None:
            return policy_value

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    if instruction.startswith(PEAR_RESPONSE_POLICY_PROMPT):
        instruction = instruction[len(PEAR_RESPONSE_POLICY_PROMPT) :].strip()
    if instruction.lower().startswith("ambient:"):
        instruction = instruction[len("ambient:") :].strip()
    return instruction


def parse_pear_routine_cadence_instruction(content: str) -> str:
    routine_cadence_artifacts = [
        artifact
        for artifact in parse_pear_artifacts(content, message_id=0)
        if artifact["kind"] == "routine_cadence"
    ]
    if routine_cadence_artifacts:
        return routine_cadence_artifacts[0]["value"]

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    if instruction.startswith(PEAR_ROUTINE_CADENCE_PROMPT):
        instruction = instruction[len(PEAR_ROUTINE_CADENCE_PROMPT) :].strip()
    if instruction.lower().startswith("routine cadence:"):
        instruction = instruction[len("routine cadence:") :].strip()
    return instruction


def parse_pear_disable_routine_instruction(
    content: str,
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    routine_disabled_artifacts = [
        artifact
        for artifact in parse_pear_artifacts(content, message_id=0)
        if artifact["kind"] == "routine_disabled"
    ]
    if routine_disabled_artifacts:
        return routine_disabled_artifacts[0]["value"]

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    normalized = " ".join(
        instruction.lower().replace("?", " ").replace("-", " ").split()
    )
    if (
        active_session is not None
        and active_session["latest_routine"]
        and (
            normalized in {"disable latest routine", "disable the latest routine"}
            or normalized in {"stop latest routine", "stop the latest routine"}
            or normalized in {"turn off latest routine", "turn off the latest routine"}
        )
    ):
        return active_session["latest_routine"]
    if instruction.startswith(PEAR_DISABLE_ROUTINE_PROMPT):
        instruction = instruction[len(PEAR_DISABLE_ROUTINE_PROMPT) :].strip()
    for prefix in ("routine disabled:", "disabled routine:", "routine off:"):
        if instruction.lower().startswith(prefix):
            instruction = instruction[len(prefix) :].strip()
            break
    return instruction


def parse_pear_update_routine_instruction(
    content: str,
    active_session: PearActiveSessionSummary | None = None,
) -> tuple[str, str]:
    artifacts = parse_pear_artifacts(content, message_id=0)
    old_routines = [
        artifact["value"] for artifact in artifacts if artifact["kind"] == "routine_disabled"
    ]
    updated_routines = [
        artifact["value"] for artifact in artifacts if artifact["kind"] == "routine"
    ]
    old_routine = old_routines[0] if old_routines else ""
    updated_routine = updated_routines[0] if updated_routines else ""
    if not old_routine and active_session is not None and active_session["latest_routine"]:
        old_routine = active_session["latest_routine"]
    return old_routine, updated_routine


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


def build_pear_done_ack_message(done_criteria: str = "") -> str:
    done_criteria = done_criteria.strip()
    lines = [
        "Noted. I'll use this definition of done for the active Pear session.",
    ]
    if done_criteria:
        lines.extend(["", f"Done: {done_criteria}"])
    return "\n".join(lines)


def build_pear_response_policy_ack_message(policy: str = "") -> str:
    response_policy = normalize_pear_response_policy(policy)
    if response_policy == "mentions_only":
        return "\n".join(
            [
                "Noted. Pear will only start follow-up worker turns when mentioned in this topic.",
                "",
                "Ambient: mentions only",
            ]
        )
    return "\n".join(
        [
            "Noted. Pear will run on active-topic follow-up replies in this topic.",
            "",
            "Ambient: on",
        ]
    )


def build_pear_routine_cadence_ack_message(cadence: str = "") -> str:
    cadence = cadence.strip() or "manual"
    return "\n".join(
        [
            "Noted. Pear will treat this as the routine cadence for this topic.",
            "",
            f"Routine cadence: {cadence}",
        ]
    )


def build_pear_routine_status_ack_message(
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    if active_session is None:
        return "\n".join(
            [
                "I cannot read Pear routines for this topic yet.",
                "",
                "Reason: topic context is unavailable.",
            ]
        )

    routine_artifacts = active_pear_routine_artifacts(active_session["artifacts"])
    if not routine_artifacts:
        return "\n".join(
            [
                f"I do not see any Pear routines recorded for {active_session['stream']} > {active_session['topic']} yet.",
                "",
                "Use `Routine:` or zulip-terminal Meta C to add one. If routines were disabled, repost a newer `Routine:` to re-enable one.",
            ]
        )

    routine_lines = [
        f"- {artifact['value']}" for artifact in routine_artifacts[:10]
    ]
    remaining_count = len(routine_artifacts) - len(routine_lines)
    if remaining_count > 0:
        routine_lines.append(f"- ...and {remaining_count} more")
    return "\n".join(
        [
            f"Pear routines recorded for {active_session['stream']} > {active_session['topic']}:",
            "",
            *routine_lines,
            "",
            f"Latest routine cadence: {active_session['latest_routine_cadence'] or 'manual'}",
            f"Disabled routine records seen: {active_session['routine_disabled_count']}",
        ]
    )


def build_pear_disable_routine_ack_message(
    routine: str = "",
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    routine = routine.strip()
    if not routine and active_session is not None and active_session["latest_routine"]:
        routine = active_session["latest_routine"]
    if not routine:
        return "\n".join(
            [
                "I do not know which Pear routine to disable yet.",
                "",
                "Use `@Pear Disable routine:` with a `Routine disabled:` line, or record a routine first and ask `@Pear disable latest routine`.",
            ]
        )
    return "\n".join(
        [
            "Noted. Pear will treat this routine as disabled in this topic.",
            "",
            f"Routine disabled: {routine}",
        ]
    )


def build_pear_update_routine_ack_message(
    old_routine: str = "",
    updated_routine: str = "",
) -> str:
    old_routine = old_routine.strip()
    updated_routine = updated_routine.strip()
    if not updated_routine:
        return "\n".join(
            [
                "I need the updated Pear routine before I can record the change.",
                "",
                "Use `@Pear Update routine:` with both `Routine disabled:` and `Routine:` lines.",
            ]
        )
    lines = [
        "Updated. Pear will treat this as the current routine in this topic.",
        "",
    ]
    if old_routine:
        lines.append(f"Routine disabled: {old_routine}")
    lines.append(f"Routine: {updated_routine}")
    return "\n".join(lines)


def build_pear_run_routine_ack_message(
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    if active_session is None:
        return "\n".join(
            [
                "I cannot run a Pear routine for this topic yet.",
                "",
                "Reason: topic context is unavailable.",
            ]
        )
    if not active_session["latest_routine"]:
        return "\n".join(
            [
                "I do not see a Pear routine recorded for this topic yet.",
                "",
                "Use `Routine:` or zulip-terminal Meta C to record one first.",
            ]
        )
    if not active_session["worker_can_run"]:
        reason = active_session["worker_blocked_reason"] or "Pear worker is not ready."
        return "\n".join(
            [
                "I found the latest Pear routine, but I cannot run it yet.",
                "",
                f"Reason: {reason}",
            ]
        )
    return "\n".join(
        [
            "Running the latest Pear routine for this topic.",
            "",
            f"Routine: {active_session['latest_routine']}",
            f"Routine cadence: {active_session['latest_routine_cadence'] or 'manual'}",
        ]
    )


def clean_pear_task_text(content: str = "") -> str:
    task = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if task.startswith(mention):
            task = task[len(mention) :].strip()
            break
    return task


def normalize_pear_explicit_request_content(
    content: str = "",
    *,
    explicitly_addressed: bool = False,
) -> str:
    content = content.strip()
    if explicitly_addressed and not is_pear_request_message(content):
        return f"@Pear {content}".strip()
    return content


def normalized_pear_request_text(content: str) -> str:
    return " ".join(
        content.lower()
        .replace("?", " ")
        .replace(":", " ")
        .replace("-", " ")
        .replace("'", " ")
        .split()
    )


def is_pear_thread_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    if is_pear_interruption_template_message(content):
        return PEAR_RESUME_PROMPT in content
    if is_pear_run_routine_request_message(content):
        return True
    if (
        is_pear_steering_template_message(content)
        or is_pear_access_request_message(content)
        or is_pear_memory_status_request_message(content)
        or is_pear_memory_forget_request_message(content)
        or is_pear_memory_template_message(content)
        or is_pear_done_template_message(content)
        or is_pear_response_policy_template_message(content)
        or is_pear_response_policy_request_message(content)
        or is_pear_routine_cadence_template_message(content)
        or is_pear_routine_status_request_message(content)
        or is_pear_disable_routine_request_message(content)
        or is_pear_update_routine_request_message(content)
    ):
        return False
    return True


def is_pear_task_request_message(content: str) -> bool:
    return is_pear_thread_request_message(content) and not is_pear_interruption_template_message(
        content
    )


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


def is_pear_run_routine_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    normalized = normalized_pear_request_text(content)
    return (
        "run routine" in normalized
        or "run the routine" in normalized
        or "run latest routine" in normalized
        or "run the latest routine" in normalized
        or "check routine" in normalized
        or PEAR_RUN_ROUTINE_PROMPT.lower().rstrip(":") in normalized
    )


def is_pear_disable_routine_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    normalized = normalized_pear_request_text(content)
    return (
        PEAR_DISABLE_ROUTINE_PROMPT.lower().rstrip(":") in normalized
        or "disable routine" in normalized
        or "disable latest routine" in normalized
        or "disable the latest routine" in normalized
        or "stop routine" in normalized
        or "stop latest routine" in normalized
        or "stop the latest routine" in normalized
        or "turn off routine" in normalized
        or "turn off latest routine" in normalized
        or "turn off the latest routine" in normalized
    )


def is_pear_update_routine_request_message(content: str) -> bool:
    return search(r"\bupdate routine\s*:", content, IGNORECASE) is not None


def is_pear_memory_forget_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    normalized = normalized_pear_request_text(content)
    return (
        PEAR_MEMORY_FORGET_PROMPT.lower().rstrip(":") in normalized
        or "forget memory" in normalized
        or "forget latest memory" in normalized
        or "forget the latest memory" in normalized
        or "remove memory" in normalized
        or "remove latest memory" in normalized
        or "remove the latest memory" in normalized
        or "drop memory" in normalized
        or "drop latest memory" in normalized
        or "drop the latest memory" in normalized
    )


def pear_worker_task_for_message(
    content: str,
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    if (
        active_session is not None
        and is_pear_run_routine_request_message(content)
        and active_session["latest_routine"]
    ):
        return "\n".join(
            [
                f"Run the latest Pear routine: {active_session['latest_routine']}",
                f"Routine cadence: {active_session['latest_routine_cadence'] or 'manual'}",
            ]
        )
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
        or is_pear_memory_status_request_message(content)
        or is_pear_memory_forget_request_message(content)
        or is_pear_memory_template_message(content)
        or is_pear_done_template_message(content)
        or is_pear_response_policy_template_message(content)
        or is_pear_response_policy_request_message(content)
        or is_pear_routine_cadence_template_message(content)
        or is_pear_routine_status_request_message(content)
        or is_pear_disable_routine_request_message(content)
        or is_pear_update_routine_request_message(content)
        or PEAR_PAUSE_PROMPT in content
        or parse_pear_checklist(content)
        or parse_pear_artifacts(content, message_id=0)
    ):
        return False
    if is_pear_run_routine_request_message(content):
        return (
            active_session is not None
            and active_session["latest_routine"] is not None
            and active_session["worker_can_run"]
        )
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


def update_pear_worker_status_message(
    bot_handler: Any,
    *,
    progress_message_id: int | None,
    content: str,
) -> bool:
    if progress_message_id is None:
        return False
    update_message = getattr(bot_handler, "update_message", None)
    if not callable(update_message):
        return False
    try:
        update_message(
            {
                "message_id": progress_message_id,
                "content": content,
            }
        )
    except Exception:
        return False
    return True


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
            if event_callback is not None:
                try:
                    signature = inspect.signature(self.tool_runner)
                    parameters = signature.parameters.values()
                    accepts_event_callback = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        or parameter.name == "event_callback"
                        for parameter in parameters
                    )
                except (TypeError, ValueError):
                    accepts_event_callback = False
                if accepts_event_callback:
                    return self.tool_runner(
                        active_session,
                        task,
                        event_callback=event_callback,
                    )
            return self.tool_runner(active_session, task)
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


def normalize_pear_routine_value(value: str) -> str:
    return " ".join(value.lower().split())


def normalize_pear_memory_value(value: str) -> str:
    return " ".join(value.lower().split())


def active_pear_memory_artifacts(artifacts: Sequence[PearArtifact]) -> list[PearArtifact]:
    forgotten_memories: set[str] = set()
    active_memories: list[PearArtifact] = []
    for artifact in sorted(
        artifacts, key=lambda artifact: artifact["message_id"], reverse=True
    ):
        if artifact["kind"] == "memory_forgotten":
            normalized = normalize_pear_memory_value(artifact["value"])
            if normalized:
                forgotten_memories.add(normalized)
            continue
        if artifact["kind"] != "memory":
            continue
        normalized = normalize_pear_memory_value(artifact["value"])
        if normalized and normalized not in forgotten_memories:
            active_memories.append(artifact)
    return active_memories


def active_pear_routine_artifacts(artifacts: Sequence[PearArtifact]) -> list[PearArtifact]:
    disabled_routines: set[str] = set()
    active_routines: list[PearArtifact] = []
    for artifact in sorted(
        artifacts, key=lambda artifact: artifact["message_id"], reverse=True
    ):
        if artifact["kind"] == "routine_disabled":
            normalized = normalize_pear_routine_value(artifact["value"])
            if normalized:
                disabled_routines.add(normalized)
            continue
        if artifact["kind"] != "routine":
            continue
        normalized = normalize_pear_routine_value(artifact["value"])
        if normalized and normalized not in disabled_routines:
            active_routines.append(artifact)
    return active_routines


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


def is_pear_memory_status_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    normalized = " ".join(content.lower().replace("?", " ").split())
    return (
        "what do you remember" in normalized
        or "what does pear remember" in normalized
        or "what memory do you have" in normalized
        or "list pear memory" in normalized
        or PEAR_MEMORY_STATUS_PROMPT.lower().rstrip("?") in normalized
    )


def is_pear_routine_status_request_message(content: str) -> bool:
    if not is_pear_request_message(content):
        return False
    normalized = " ".join(
        content.lower()
        .replace("?", " ")
        .replace(":", " ")
        .replace("-", " ")
        .split()
    )
    return (
        "what routines do you have" in normalized
        or "what routines are set up" in normalized
        or "what routines are configured" in normalized
        or "list routines" in normalized
        or "list pear routines" in normalized
        or "show routines" in normalized
        or PEAR_ROUTINE_STATUS_PROMPT.lower().rstrip("?") in normalized
    )


def is_pear_interruption_template_message(content: str) -> bool:
    return PEAR_PAUSE_PROMPT in content or PEAR_RESUME_PROMPT in content


def is_pear_memory_template_message(content: str) -> bool:
    return PEAR_MEMORY_PROMPT in content or pear_memory_request_value(content) is not None


def is_pear_done_template_message(content: str) -> bool:
    return PEAR_DONE_PROMPT in content


def is_pear_response_policy_template_message(content: str) -> bool:
    return PEAR_RESPONSE_POLICY_PROMPT in content


def pear_memory_request_value(content: str) -> str | None:
    instruction = clean_pear_task_text(content)
    lowered_instruction = instruction.lower()
    for prefix in (
        "remember for this channel",
        "remember for the channel",
        "remember in this channel",
        "remember for this stream",
        "remember for the stream",
        "remember in this stream",
        "remember for this topic",
        "remember in this topic",
        "remember for this thread",
        "remember in this thread",
        "remember for this conversation",
        "remember in this conversation",
    ):
        prefix_index = lowered_instruction.find(prefix)
        if prefix_index < 0:
            continue
        if prefix_index > 0 and not is_pear_request_message(content):
            continue
        value = instruction[prefix_index + len(prefix) :].strip()
        if value.startswith(":"):
            value = value[1:].strip()
        if value.lower().startswith("that "):
            value = value[5:].strip()
        return value
    return None


def pear_response_policy_request_value(content: str) -> str | None:
    if not is_pear_request_message(content):
        return None
    if pear_memory_request_value(content) is not None:
        return None
    normalized = normalized_pear_request_text(content)
    mentions_only_phrases = (
        "only respond when",
        "only reply when",
        "only answer when",
        "respond only when",
        "reply only when",
        "answer only when",
        "respond only if",
        "reply only if",
        "answer only if",
        "do not respond unless",
        "do not reply unless",
        "don t respond unless",
        "don t reply unless",
        "dont respond unless",
        "dont reply unless",
        "stop following this thread",
        "stop following this topic",
        "stay quiet",
        "be quiet",
        "quiet this thread",
        "quiet this topic",
        "quiet down",
        "mentions only",
        "mention only",
        "tag only",
        "tagged only",
        "ambient off",
        "followups off",
        "follow ups off",
        "follow up replies off",
        "turn off followups",
        "turn off follow ups",
        "unprompted replies off",
    )
    if any(phrase in normalized for phrase in mentions_only_phrases) and (
        "mention" in normalized
        or "tag" in normalized
        or "quiet" in normalized
        or "follow" in normalized
        or "ambient" in normalized
        or "unprompted" in normalized
    ):
        return "mentions only"

    ambient_phrases = (
        "ambient on",
        "followups on",
        "follow ups on",
        "followups back on",
        "follow ups back on",
        "follow up replies on",
        "turn on followups",
        "turn on follow ups",
        "turn followups on",
        "turn follow ups on",
        "turn followups back on",
        "turn follow ups back on",
        "resume followups",
        "resume follow ups",
        "respond to follow up replies",
        "run on follow up replies",
        "respond without mention",
        "respond without being mentioned",
        "unprompted replies on",
    )
    if any(phrase in normalized for phrase in ambient_phrases):
        return "on"
    return None


def is_pear_response_policy_request_message(content: str) -> bool:
    return pear_response_policy_request_value(content) is not None


def is_pear_routine_cadence_template_message(content: str) -> bool:
    return PEAR_ROUTINE_CADENCE_PROMPT in content


def is_pear_control_template_message(content: str) -> bool:
    return (
        is_pear_steering_template_message(content)
        or is_pear_interruption_template_message(content)
        or is_pear_memory_status_request_message(content)
        or is_pear_memory_forget_request_message(content)
        or is_pear_routine_status_request_message(content)
        or is_pear_disable_routine_request_message(content)
        or is_pear_update_routine_request_message(content)
        or is_pear_memory_template_message(content)
        or is_pear_done_template_message(content)
        or is_pear_response_policy_template_message(content)
        or is_pear_response_policy_request_message(content)
        or is_pear_routine_cadence_template_message(content)
    )


def parse_pear_memory_instruction(content: str) -> str:
    memory_artifacts = [
        artifact
        for artifact in parse_pear_artifacts(content, message_id=0)
        if artifact["kind"] == "memory"
    ]
    if memory_artifacts:
        return memory_artifacts[0]["value"]

    memory_request_value = pear_memory_request_value(content)
    if memory_request_value is not None:
        return memory_request_value

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    if instruction.startswith(PEAR_MEMORY_PROMPT):
        instruction = instruction[len(PEAR_MEMORY_PROMPT) :].strip()
    return instruction


def parse_pear_memory_forget_instruction(
    content: str,
    active_session: PearActiveSessionSummary | None = None,
) -> str:
    memory_forgotten_artifacts = [
        artifact
        for artifact in parse_pear_artifacts(content, message_id=0)
        if artifact["kind"] == "memory_forgotten"
    ]
    if memory_forgotten_artifacts:
        return memory_forgotten_artifacts[0]["value"]

    instruction = content.strip()
    for mention in (PEAR_AGENT_MENTION, "@Pear"):
        if instruction.startswith(mention):
            instruction = instruction[len(mention) :].strip()
            break
    normalized = " ".join(
        instruction.lower().replace("?", " ").replace("-", " ").split()
    )
    if (
        active_session is not None
        and active_session["latest_memory"]
        and (
            normalized in {"forget latest memory", "forget the latest memory"}
            or normalized in {"remove latest memory", "remove the latest memory"}
            or normalized in {"drop latest memory", "drop the latest memory"}
        )
    ):
        return active_session["latest_memory"]
    if instruction.startswith(PEAR_MEMORY_FORGET_PROMPT):
        instruction = instruction[len(PEAR_MEMORY_FORGET_PROMPT) :].strip()
    for prefix in ("memory forgotten:", "forgotten memory:", "memory off:"):
        if instruction.lower().startswith(prefix):
            instruction = instruction[len(prefix) :].strip()
            break
    return instruction


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
        or is_pear_memory_status_request_message(content)
        or is_pear_memory_forget_request_message(content)
        or is_pear_routine_status_request_message(content)
        or is_pear_disable_routine_request_message(content)
        or is_pear_update_routine_request_message(content)
        or is_pear_run_routine_request_message(content)
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
    return PEAR_AGENT_MENTION in content or "@Pear" in content


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
    routine_artifacts = active_pear_routine_artifacts(artifact_list)
    latest_routine = routine_artifacts[0] if routine_artifacts else None
    routine_disabled_artifacts = [
        artifact for artifact in artifact_list if artifact["kind"] == "routine_disabled"
    ]
    latest_routine_disabled = (
        routine_disabled_artifacts[0] if routine_disabled_artifacts else None
    )
    memory_artifacts = active_pear_memory_artifacts(artifact_list)
    latest_memory = memory_artifacts[0] if memory_artifacts else None
    memory_forgotten_artifacts = [
        artifact for artifact in artifact_list if artifact["kind"] == "memory_forgotten"
    ]
    latest_memory_forgotten = (
        memory_forgotten_artifacts[0] if memory_forgotten_artifacts else None
    )
    done_criteria_artifacts = [
        artifact for artifact in artifact_list if artifact["kind"] == "done_criteria"
    ]
    latest_done_criteria = (
        done_criteria_artifacts[0] if done_criteria_artifacts else None
    )
    response_policy_artifacts = [
        artifact for artifact in artifact_list if artifact["kind"] == "response_policy"
    ]
    latest_response_policy = (
        response_policy_artifacts[0] if response_policy_artifacts else None
    )
    normalized_response_policy = normalize_pear_response_policy(
        response_policy
        if response_policy is not None
        else (latest_response_policy["value"] if latest_response_policy is not None else None)
    )
    routine_cadence_artifacts = [
        artifact for artifact in artifact_list if artifact["kind"] == "routine_cadence"
    ]
    latest_routine_cadence = (
        routine_cadence_artifacts[0] if routine_cadence_artifacts else None
    )
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
    else:
        worker_state = "ready"
        worker_blocked_reason = None
    worker_can_run = worker_state == "ready"

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
        "routine_count": len(routine_artifacts),
        "latest_routine_message_id": latest_routine["message_id"]
        if latest_routine is not None
        else None,
        "latest_routine": latest_routine["value"] if latest_routine is not None else None,
        "memory_count": len(memory_artifacts),
        "latest_memory_message_id": latest_memory["message_id"]
        if latest_memory is not None
        else None,
        "latest_memory": latest_memory["value"] if latest_memory is not None else None,
        "memory_forgotten_count": len(memory_forgotten_artifacts),
        "latest_memory_forgotten_message_id": latest_memory_forgotten["message_id"]
        if latest_memory_forgotten is not None
        else None,
        "latest_memory_forgotten": latest_memory_forgotten["value"]
        if latest_memory_forgotten is not None
        else None,
        "done_criteria_count": len(done_criteria_artifacts),
        "latest_done_criteria_message_id": latest_done_criteria["message_id"]
        if latest_done_criteria is not None
        else None,
        "latest_done_criteria": latest_done_criteria["value"]
        if latest_done_criteria is not None
        else None,
        "response_policy_count": len(response_policy_artifacts),
        "latest_response_policy_message_id": latest_response_policy["message_id"]
        if latest_response_policy is not None
        else None,
        "latest_response_policy": latest_response_policy["value"]
        if latest_response_policy is not None
        else None,
        "response_policy": normalized_response_policy,
        "routine_cadence_count": len(routine_cadence_artifacts),
        "latest_routine_cadence_message_id": latest_routine_cadence["message_id"]
        if latest_routine_cadence is not None
        else None,
        "latest_routine_cadence": latest_routine_cadence["value"]
        if latest_routine_cadence is not None
        else None,
        "routine_disabled_count": len(routine_disabled_artifacts),
        "latest_routine_disabled_message_id": latest_routine_disabled["message_id"]
        if latest_routine_disabled is not None
        else None,
        "latest_routine_disabled": latest_routine_disabled["value"]
        if latest_routine_disabled is not None
        else None,
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
        if latest_message_id is None:
            message_checklist = parse_pear_checklist(content)
            if not message_checklist:
                continue
            latest_message_id = message_id
            checklist = message_checklist

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
    supported_worker_runtime = worker_agent_runtime in {
        "codex-app-server",
        "codex-agents-mcp",
        "codex-sdk",
        "codex-exec",
    }
    if worker_runtime_available is None:
        worker_runtime_available = supported_worker_runtime
    if worker_runtime_detail is None:
        worker_runtime_detail = (
            worker_agent_runtime
            if supported_worker_runtime
            else "Set PEAR_WORKER_AGENT_RUNTIME to codex-app-server, codex-agents-mcp, codex-sdk, or codex-exec."
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
        if worker_agent_runtime == "codex-agents-mcp":
            worker_runtime_login_method = "codex-cli-and-openai-api-key"
        else:
            worker_runtime_login_method = "codex-cli"
    if worker_runtime_login_target is None and supported_worker_runtime:
        worker_runtime_login_target = "worker-environment"
    if worker_runtime_login_instructions is None:
        if worker_agent_runtime == "codex-agents-mcp":
            worker_runtime_login_instructions = (
                "Run Codex CLI login inside the worker environment and provide "
                "OPENAI_API_KEY or OPENAI_ADMIN_KEY for the OpenAI Agents SDK."
            )
        elif worker_agent_runtime in {"codex-app-server", "codex-exec", "codex-sdk"}:
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
        elif is_pear_memory_status_request_message(content):
            response = build_pear_memory_status_ack_message(active_session)
        elif is_pear_memory_forget_request_message(content):
            response = build_pear_memory_forget_ack_message(
                parse_pear_memory_forget_instruction(content, active_session),
                active_session,
            )
        elif is_pear_routine_status_request_message(content):
            response = build_pear_routine_status_ack_message(active_session)
        elif is_pear_disable_routine_request_message(content):
            response = build_pear_disable_routine_ack_message(
                parse_pear_disable_routine_instruction(content, active_session),
                active_session,
            )
        elif is_pear_update_routine_request_message(content):
            old_routine, updated_routine = parse_pear_update_routine_instruction(
                content,
                active_session,
            )
            response = build_pear_update_routine_ack_message(
                old_routine,
                updated_routine,
            )
        elif is_pear_memory_template_message(content):
            response = build_pear_memory_ack_message(
                parse_pear_memory_instruction(content)
            )
        elif is_pear_done_template_message(content):
            response = build_pear_done_ack_message(
                parse_pear_done_instruction(content)
            )
        elif is_pear_response_policy_template_message(
            content
        ) or is_pear_response_policy_request_message(content):
            response_policy = parse_pear_response_policy_instruction(content)
            self.set_topic_response_policy(
                bot_handler,
                stream=stream,
                topic=topic,
                response_policy=response_policy,
            )
            if active_session is not None:
                active_session["response_policy"] = normalize_pear_response_policy(response_policy)
            response = build_pear_response_policy_ack_message(response_policy)
        elif is_pear_routine_cadence_template_message(content):
            response = build_pear_routine_cadence_ack_message(
                parse_pear_routine_cadence_instruction(content)
            )
        elif is_pear_run_routine_request_message(content):
            response = build_pear_run_routine_ack_message(active_session)
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

        result = None
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
            if self.update_active_worker_steering_status(
                bot_handler,
                stream=stream,
                topic=topic,
                status_markdown=PEAR_STEERING_QUEUED_MARKDOWN,
            ):
                return
            return
        result = bot_handler.send_reply(message, response)
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
        started_progress_posted = False
        result_message_id = progress_message_id
        transcript_events: list[PearWorkerTranscriptEvent] = []
        task = pear_worker_task_line(content)
        last_transcript_flush_at = 0.0
        last_volatile_transcript_flush_at = 0.0
        assistant_persistent_flush_seen = False
        assistant_volatile_flush_seen = False
        steering_events_after = None
        last_steering_event_id = 0
        steering_segment_count = 0

        def raise_if_cancel_requested() -> None:
            if not self.worker_run_cancel_requested(bot_handler, message_id):
                return
            self.worker_runtime.interrupt()
            raise PearRunCancelled("Cancelled by user.")

        def update_live_transcript(
            status: PearWorkerRunStatus,
            *,
            tool_results: Sequence[PearWorkerToolResult] = (),
            force: bool = False,
            flush_interval: float = PEAR_BACKGROUND_TRANSCRIPT_FLUSH_INTERVAL_SECONDS,
        ) -> bool:
            nonlocal last_transcript_flush_at, result_message_id
            target_message_id = result_message_id or progress_message_id
            if target_message_id is None:
                return False
            current_time = time.monotonic()
            if not force and current_time - last_transcript_flush_at < flush_interval:
                return False
            content_to_update = build_pear_live_transcript_message(
                task=task,
                status=status,
                transcript_events=transcript_events,
                tool_results=tool_results,
            )
            updated = update_pear_worker_status_message(
                bot_handler,
                progress_message_id=target_message_id,
                content=content_to_update,
            )
            if updated:
                result_message_id = target_message_id
                last_transcript_flush_at = current_time
            return updated

        def update_volatile_transcript(
            status: PearWorkerRunStatus,
            *,
            tool_results: Sequence[PearWorkerToolResult] = (),
            force: bool = False,
            flush_interval: float = PEAR_VOLATILE_ASSISTANT_FLUSH_INTERVAL_SECONDS,
        ) -> bool:
            nonlocal last_volatile_transcript_flush_at
            current_time = time.monotonic()
            if not force and current_time - last_volatile_transcript_flush_at < flush_interval:
                return False
            content_to_update = build_pear_live_transcript_message(
                task=task,
                status=status,
                transcript_events=transcript_events,
                tool_results=tool_results,
            )
            write_pear_live_run_state(
                message_id,
                status=status,
                content=content_to_update,
            )
            last_volatile_transcript_flush_at = current_time
            return True

        def maybe_apply_same_turn_steering() -> None:
            nonlocal last_steering_event_id, steering_segment_count
            if (
                active_session is None
                or not callable(steering_events_after)
                or steering_segment_count >= PEAR_MAX_STEERING_SEGMENTS
            ):
                return
            pending_steering_events = steering_events_after(
                bot_handler,
                message_id,
                after_event_id=last_steering_event_id,
            )
            if not pending_steering_events:
                return
            latest_steering = "; ".join(
                event["instruction"] for event in pending_steering_events
            )
            if not self.worker_runtime.steer(latest_steering):
                return
            last_steering_event_id = pending_steering_events[-1]["event_id"]
            steering_segment_count += 1
            active_session["latest_steering_instruction"] = latest_steering
            self.record_worker_event(
                bot_handler,
                message_id,
                "steering_applied",
                summary=trim_pear_text(latest_steering, limit=500),
                payload={
                    "segment_index": steering_segment_count,
                    "steering_events": pending_steering_events,
                    "same_turn": True,
                },
            )
            transcript_events.append(
                {
                    "kind": "steering",
                    "status": "done",
                    "title": "Steering applied",
                    "body": latest_steering,
                }
            )
            update_volatile_transcript("running", force=True)
            update_live_transcript("running", force=True)

        def handle_transcript_event(event: PearWorkerTranscriptEvent) -> None:
            nonlocal assistant_persistent_flush_seen, assistant_volatile_flush_seen
            raise_if_cancel_requested()
            transcript_events.append(event)
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
                self.record_worker_event(
                    bot_handler,
                    message_id,
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
                self.record_worker_event(
                    bot_handler,
                    message_id,
                    "usage",
                    summary=pear_token_usage_summary(usage),
                    payload={"usage": usage},
                )
            if summary and event_kind != "usage" and not streaming_assistant:
                self.update_worker_metadata(
                    bot_handler,
                    message_id,
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
                if update_volatile_transcript(
                    "running",
                    force=not assistant_volatile_flush_seen,
                    flush_interval=PEAR_VOLATILE_ASSISTANT_FLUSH_INTERVAL_SECONDS,
                ):
                    assistant_volatile_flush_seen = True
                if update_live_transcript(
                    "running",
                    force=not assistant_persistent_flush_seen,
                    flush_interval=PEAR_PERSISTENT_ASSISTANT_FLUSH_INTERVAL_SECONDS,
                ):
                    assistant_persistent_flush_seen = True
            else:
                update_volatile_transcript("running", force=sparse_event)
                update_live_transcript("running", force=sparse_event)
            if event_kind in {"assistant", "command", "file_change", "plan", "tool"}:
                maybe_apply_same_turn_steering()

        try:
            if active_session is None and session_kind == "side":
                active_session = build_pear_active_session_summary(
                    pear_session=get_pear_session_payload(
                        stream=source_stream or stream,
                        topic=source_topic or topic,
                    ),
                    source="server",
                    stream_exists=True,
                    topic_message_count=1,
                    request_count=1,
                    latest_request_message_id=message_id,
                    latest_message_id=None,
                    checklist=[],
                    artifacts=[],
                )
            else:
                loaded_active_session = None
                if session_kind == "shared" and self.active_session_loader is not None:
                    try:
                        loaded_active_session = self.active_session_loader(
                            message,
                            bot_handler,
                        )
                    except Exception as error:
                        raise RuntimeError("Pear active session loader failed.") from error
                active_session = (
                    loaded_active_session
                    or active_session
                    or build_trigger_pear_active_session(
                        message,
                        content,
                    )
            )
            task = pear_worker_task_for_message(content, active_session)
            runtime_label = self.worker_runtime.runtime_value()
            if not callable(getattr(self.worker_run_store, "claim_for_run", None)):
                self.worker_run_store.record(
                    bot_handler,
                    message_id,
                    "running",
                    stream=stream,
                    topic=topic,
                )
            self.update_worker_metadata(
                bot_handler,
                message_id,
                requesting_user_id=requesting_user_id,
                session_kind=session_kind,
                source_stream=source_stream,
                source_topic=source_topic,
                source_message_id=source_message_id,
                summary="Running Pear worker.",
                current_activity=task,
                runtime_label=runtime_label,
            )
            self.record_worker_event(
                bot_handler,
                message_id,
                "running",
                summary="Started Pear worker runtime.",
                payload={
                    "task": task,
                    "runtime": runtime_label,
                    "stream": stream,
                    "topic": topic,
                    "session_kind": session_kind,
                },
            )
            if active_session["worker_can_run"]:
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
                    task=task,
                    tool_results=initial_tool_results,
                    transcript_events=transcript_events,
                )
                update_volatile_transcript(
                    "running",
                    tool_results=initial_tool_results,
                    force=True,
                )
                progress_updated = update_live_transcript(
                    "running",
                    tool_results=initial_tool_results,
                    force=True,
                )
                result = None
                if progress_updated:
                    result_message_id = result_message_id or progress_message_id
                elif post_started_progress:
                    result = bot_handler.send_reply(message, progress_content)
                started_progress_posted = progress_updated or result is not None
                if isinstance(result, dict) and isinstance(result.get("id"), int):
                    result_message_id = result["id"]
                if result_message_id is not None:
                    self.worker_run_store.record(
                        bot_handler,
                        message_id,
                        "running",
                        stream=stream,
                        topic=topic,
                        result_message_id=result_message_id,
                    )
                    self.record_worker_event(
                        bot_handler,
                        message_id,
                        "progress_posted",
                        summary=(
                            "Updated started-progress message."
                            if progress_updated
                            else "Posted started-progress message."
                        ),
                        payload={"result_message_id": result_message_id},
                    )
            record_topic_context_results = getattr(
                self.worker_run_store,
                "record_topic_context_results",
                None,
            )
            steering_events_after = getattr(
                self.worker_run_store,
                "steering_events_after",
                None,
            )

            def run_runtime_segment(segment_task: str, *, segment_index: int) -> dict[str, Any]:
                raise_if_cancel_requested()
                segment_result = self.worker_runtime.run(
                    active_session=active_session,
                    task=segment_task,
                    event_callback=handle_transcript_event,
                )
                raise_if_cancel_requested()
                if callable(record_topic_context_results):
                    record_topic_context_results(
                        bot_handler,
                        stream=stream,
                        topic=topic,
                        tool_results=segment_result["tool_results"],
                    )
                segment_summary = pear_worker_summary_from_tool_results(
                    segment_result["tool_results"]
                )
                self.record_worker_event(
                    bot_handler,
                    message_id,
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

            worker_result = run_runtime_segment(task, segment_index=0)
            codex_thread_id = pear_codex_thread_id_from_tool_results(
                worker_result["tool_results"]
            )
            while (
                worker_result["worker_can_run"]
                and callable(steering_events_after)
                and steering_segment_count < PEAR_MAX_STEERING_SEGMENTS
            ):
                pending_steering_events = steering_events_after(
                    bot_handler,
                    message_id,
                    after_event_id=last_steering_event_id,
                )
                if not pending_steering_events:
                    break
                last_steering_event_id = pending_steering_events[-1]["event_id"]
                steering_segment_count += 1
                steering_task = build_pear_steering_task(pending_steering_events)
                latest_steering = "; ".join(
                    event["instruction"] for event in pending_steering_events
                )
                active_session["latest_steering_instruction"] = latest_steering
                if codex_thread_id:
                    active_session["latest_codex_thread_id"] = codex_thread_id
                self.update_worker_metadata(
                    bot_handler,
                    message_id,
                    current_activity=trim_pear_text(
                        f"Applying steering: {latest_steering}",
                        limit=500,
                    ),
                )
                self.record_worker_event(
                    bot_handler,
                    message_id,
                    "steering_applied",
                    summary=trim_pear_text(latest_steering, limit=500),
                    payload={
                        "segment_index": steering_segment_count,
                        "steering_events": pending_steering_events,
                    },
                )
                transcript_events.append(
                    {
                        "kind": "steering",
                        "status": "done",
                        "title": "Steering applied",
                        "body": latest_steering,
                    }
                )
                update_volatile_transcript("running", force=True)
                update_live_transcript("running", force=True)
                worker_result = run_runtime_segment(
                    steering_task,
                    segment_index=steering_segment_count,
                )
                codex_thread_id = pear_codex_thread_id_from_tool_results(
                    worker_result["tool_results"]
                ) or codex_thread_id

            result_summary = pear_worker_summary_from_tool_results(
                worker_result["tool_results"]
            )
            if worker_result["should_post"] and worker_result["content"] is not None:
                content_to_post = worker_result["content"]
                content_chunks = split_pear_persistent_message(content_to_post)
                if started_progress_posted or transcript_events:
                    content_chunks = build_pear_worker_completed_message_chunks(
                        task=task,
                        tool_results=worker_result["tool_results"],
                        transcript_events=transcript_events,
                    )
                    content_to_post = content_chunks[0] if content_chunks else ""
                result_updated = update_pear_worker_status_message(
                    bot_handler,
                    progress_message_id=result_message_id or progress_message_id,
                    content=content_to_post,
                )
                result = None
                if not result_updated:
                    result = bot_handler.send_reply(message, content_to_post)
                for continuation_content in content_chunks[1:]:
                    bot_handler.send_reply(message, continuation_content)
                write_pear_live_run_state(
                    message_id,
                    status="done",
                    content=content_to_post,
                )
                if isinstance(result, dict) and isinstance(result.get("id"), int):
                    result_message_id = result["id"]
                if result_message_id is not None:
                    self.record_worker_event(
                        bot_handler,
                        message_id,
                        "result_updated" if result_updated else "result_posted",
                        summary=(
                            "Updated worker result message."
                            if result_updated
                            else "Posted worker result message."
                        ),
                        payload={"result_message_id": result_message_id},
                    )
            self.worker_run_store.record(
                bot_handler,
                message_id,
                "done",
                stream=stream,
                topic=topic,
                result_message_id=result_message_id,
            )
            self.update_worker_metadata(
                bot_handler,
                message_id,
                summary=result_summary or "Completed.",
                current_activity="Completed.",
                codex_thread_id=codex_thread_id,
            )
            self.record_worker_event(
                bot_handler,
                message_id,
                "done",
                summary=result_summary or "Completed.",
                payload={"result_message_id": result_message_id},
            )
        except PearRunCancelled as error:
            self.worker_run_store.record(
                bot_handler,
                message_id,
                "cancelled",
                stream=stream,
                topic=topic,
                result_message_id=result_message_id,
                error=str(error),
            )
            self.update_worker_metadata(
                bot_handler,
                message_id,
                summary=str(error),
                current_activity="Cancelled.",
            )
            self.record_worker_event(
                bot_handler,
                message_id,
                "cancelled",
                summary=str(error),
                payload={"result_message_id": result_message_id},
            )
            transcript_events.append(
                {
                    "kind": "status",
                    "status": "cancelled",
                    "title": "Pear worker cancelled.",
                    "body": str(error),
                }
            )
            cancelled_message = build_pear_live_transcript_message(
                task=task,
                status="cancelled",
                transcript_events=transcript_events,
            )
            write_pear_live_run_state(
                message_id,
                status="cancelled",
                content=cancelled_message,
            )
            update_pear_worker_status_message(
                bot_handler,
                progress_message_id=result_message_id or progress_message_id,
                content=cancelled_message,
            )
        except Exception as error:
            self.worker_run_store.record(
                bot_handler,
                message_id,
                "failed",
                stream=stream,
                topic=topic,
                result_message_id=result_message_id,
                error=str(error),
            )
            self.update_worker_metadata(
                bot_handler,
                message_id,
                summary=str(error),
                current_activity="Failed.",
            )
            self.record_worker_event(
                bot_handler,
                message_id,
                "failed",
                summary=str(error),
                payload={"result_message_id": result_message_id},
            )
            transcript_events.append(
                {
                    "kind": "error",
                    "status": "failed",
                    "title": "Pear worker failed.",
                    "body": str(error),
                }
            )
            failure_message = build_pear_live_transcript_message(
                task=task,
                status="failed",
                transcript_events=transcript_events,
            )
            write_pear_live_run_state(
                message_id,
                status="failed",
                content=failure_message,
            )
            if not update_pear_worker_status_message(
                bot_handler,
                progress_message_id=result_message_id or progress_message_id,
                content=failure_message,
            ):
                bot_handler.send_reply(message, failure_message)


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
        "memory_message": build_pear_memory_message(),
        "memory_status_message": build_pear_memory_status_message(),
        "memory_forget_message": build_pear_memory_forget_message(),
        "done_message": build_pear_done_message(),
        "response_policy_message": build_pear_response_policy_message(),
        "routine_cadence_message": build_pear_routine_cadence_message(),
        "routine_status_message": build_pear_routine_status_message(),
        "disable_routine_message": build_pear_disable_routine_message(),
        "update_routine_message": build_pear_update_routine_message(),
        "pause_message": build_pear_pause_message(),
        "resume_message": build_pear_resume_message(),
        "official_agent_runtime": PEAR_OFFICIAL_AGENT_RUNTIME,
        "official_agent_runtime_label": PEAR_OFFICIAL_AGENT_RUNTIME_LABEL,
        "agent_runtime_packages": list(PEAR_AGENT_RUNTIME_PACKAGES),
        "official_codex_sdk_packages": list(PEAR_OFFICIAL_CODEX_SDK_PACKAGES),
        "codex_mcp_tools": list(PEAR_CODEX_MCP_TOOLS),
        "pear_topic_state_tools": list(PEAR_TOPIC_STATE_TOOLS),
        "pear_topic_state_tool_names": list(PEAR_TOPIC_STATE_TOOL_NAMES),
        "pear_agent_skills": list(PEAR_AGENT_SKILLS),
        "pear_agent_skill_definitions": [
            {
                "name": skill["name"],
                "description": skill["description"],
                "applies_to": list(skill["applies_to"]),
                "instructions": list(skill["instructions"]),
                "artifact_tools": list(skill["artifact_tools"]),
            }
            for skill in PEAR_AGENT_SKILL_DEFINITIONS
        ],
        "server_contract_version": PEAR_SERVER_CONTRACT_VERSION,
    }
