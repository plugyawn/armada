import json
import logging
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from zerver.lib.pear import (
    PearActiveSessionSummary,
    PearAmbientAction,
    PearAmbientAddressee,
    PearAmbientDecision,
    PearResponsePresentation,
    PearUserInputProvider,
    PearWorkerToolResult,
    PearWorkerTranscriptCallback,
    PearWorkerTranscriptEvent,
    parse_pear_artifacts,
    pear_is_retired_visible_artifact_line,
    pear_normalize_token_usage,
)
from zerver.lib.pear_artifact import (
    PearArtifactError,
    artifact_tool_result,
    canonicalize_pear_artifact,
    pear_artifact_node_count,
)
from zerver.lib.pear_artifact_contract import PEAR_ARTIFACT_TOOL_SPEC

logger = logging.getLogger(__name__)

PearAgentRuntime = Literal["disabled", "codex-app-server"]
PearSubprocessPopen = Callable[..., subprocess.Popen[str]]
PearUrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class PearProcessIsolation:
    username: str
    home: str
    codex_home: str

    def command_prefix(self) -> list[str]:
        return [
            "sudo",
            "-n",
            "-H",
            "-u",
            self.username,
            "env",
            f"HOME={self.home}",
            f"USER={self.username}",
            f"LOGNAME={self.username}",
            f"CODEX_HOME={self.codex_home}",
            f"XDG_CACHE_HOME={self.home}/.cache",
        ]

    def label(self) -> str:
        return f"os-user:{self.username}"


class PearAgentRuntimeError(RuntimeError):
    pass


def pear_capture_protocol_message(direction: str, message: dict[str, Any]) -> None:
    """Append one wire message to the capture file when
    PEAR_CODEX_PROTOCOL_CAPTURE names a path. Diagnostic-only: the
    surface-diff workflow uses it to see exactly what an app-server
    version emits and asks; never allowed to break a turn."""
    capture_path = os.environ.get("PEAR_CODEX_PROTOCOL_CAPTURE")
    if not capture_path:
        return
    try:
        with open(capture_path, "a") as capture_file:
            capture_file.write(json.dumps({"direction": direction, "message": message}) + "\n")
    except OSError:
        pass


def pear_isolated_command(
    command: Sequence[str],
    process_isolation: PearProcessIsolation | None,
) -> list[str]:
    if process_isolation is None:
        return list(command)
    return [*process_isolation.command_prefix(), *command]


def pear_subprocess_cwd(
    workdir: str | None,
    process_isolation: PearProcessIsolation | None,
) -> str | None:
    if process_isolation is None:
        return workdir
    return "/"


def trim_pear_agent_text(value: str, *, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 15, 0)].rstrip()}... [truncated]"


def build_pear_addressing_router_prompt(
    *,
    content: str,
    active_session: PearActiveSessionSummary | None,
    stream: str = "",
    topic: str = "",
    message_id: int | None = None,
) -> str:
    if active_session is None:
        session_lines = [
            "- active_session: none",
            "- worker_state: unknown",
            "- response_policy: ambient",
        ]
    else:
        session_lines = [
            f"- active_session: {active_session['state']}",
            f"- worker_state: {active_session['worker_state']}",
            f"- worker_can_run: {active_session['worker_can_run']}",
            f"- response_policy: {active_session['response_policy']}",
            f"- request_count: {active_session['request_count']}",
            f"- latest_request_message_id: {active_session['latest_request_message_id'] or 'None'}",
            f"- latest_steering: {active_session['latest_steering_instruction'] or 'None'}",
        ]

    recent_messages = (
        [
            message
            for message in active_session["recent_messages"]
            if message_id is None or message["message_id"] != message_id
        ][-6:]
        if active_session is not None
        else []
    )
    recent_lines = [
        f"- {message['sender']}{' [bot]' if message['sender_is_bot'] else ''}: "
        f"{trim_pear_agent_text(message['content'], limit=240)}"
        for message in recent_messages
    ]
    if not recent_lines:
        recent_lines = ["- none"]

    return "\n".join(
        [
            "You are Pear's low-latency routing layer for a Zulip coding agent.",
            "Classify one new untagged message. Do not answer it.",
            "Do not answer the user. Do not run commands or use tools.",
            "Return only the JSON object required by the response schema.",
            "",
            "addressee: pear only when the request is for Pear, Codex, the agent, or the assistant; other for a named/recent human; general for team chatter; uncertain otherwise.",
            "action: ignore for chatter, ambiguity, acknowledgements, duplicates, and messages for humans; steer only when an active run exists and this changes or continues it; run only for a concrete request when no run is active.",
            "presentation: choose the likely final UI shape: compact, prose, status, explainer, or diff.",
            "duplicate: true only when the new message repeats a recent request without adding information.",
            "When uncertain, use addressee=uncertain and action=ignore.",
            "",
            f"Stream: {stream or 'unknown'}",
            f"Topic: {topic or 'unknown'}",
            "Session:",
            *session_lines,
            "",
            "Recent topic messages (oldest to newest):",
            *recent_lines,
            "",
            "New message:",
            trim_pear_agent_text(content, limit=1000),
        ]
    )


def parse_pear_addressing_json_output(output: str) -> PearAmbientDecision:
    ignored: PearAmbientDecision = {
        "addressee": "uncertain",
        "action": "ignore",
        "presentation": "compact",
        "duplicate": False,
    }
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end < start:
        return ignored
    try:
        parsed = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return ignored
    if not isinstance(parsed, dict):
        return ignored
    addressee = parsed.get("addressee")
    action = parsed.get("action")
    presentation = parsed.get("presentation")
    duplicate = parsed.get("duplicate")
    if (
        addressee not in {"pear", "other", "general", "uncertain"}
        or action not in {"ignore", "steer", "run"}
        or presentation not in {"compact", "prose", "status", "explainer", "diff"}
        or not isinstance(duplicate, bool)
    ):
        return ignored
    return {
        "addressee": cast(PearAmbientAddressee, addressee),
        "action": cast(PearAmbientAction, action),
        "presentation": cast(PearResponsePresentation, presentation),
        "duplicate": duplicate,
    }


def pear_gemini_interaction_output_text(payload: dict[str, Any]) -> str:
    text_parts: list[str] = []
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return ""
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
    return "".join(text_parts)


class PearGeminiInteractionsAddressingIntentClassifier:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: int,
        output_limit: int,
        url: str,
        thinking_level: str | None = "minimal",
        urlopen: PearUrlOpen | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.url = url
        self.thinking_level = thinking_level.strip() if thinking_level else None
        self.urlopen = urlopen or urllib.request.urlopen

    def __call__(
        self,
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message: dict[str, Any],
        bot_handler: Any,
    ) -> PearAmbientDecision:
        if not self.api_key:
            raise PearAgentRuntimeError("Gemini addressing router API key is not configured.")

        stream = str(message.get("display_recipient") or "")
        topic = str(message.get("topic") or message.get("subject") or "")
        prompt = build_pear_addressing_router_prompt(
            content=content,
            active_session=active_session,
            stream=stream,
            topic=topic,
            message_id=message.get("id") if isinstance(message.get("id"), int) else None,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "input": prompt,
            "system_instruction": (
                "Return only the structured JSON object required by the response schema."
            ),
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "addressee": {
                            "type": "string",
                            "enum": ["pear", "other", "general", "uncertain"],
                        },
                        "action": {"type": "string", "enum": ["ignore", "steer", "run"]},
                        "presentation": {
                            "type": "string",
                            "enum": ["compact", "prose", "status", "explainer", "diff"],
                        },
                        "duplicate": {"type": "boolean"},
                    },
                    "required": ["addressee", "action", "presentation", "duplicate"],
                    "additionalProperties": False,
                },
            },
            "generation_config": {"temperature": 0},
        }
        if self.thinking_level:
            payload["generation_config"]["thinking_level"] = self.thinking_level

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read().decode()
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            raw_error = error.read().decode(errors="replace")
            raise PearAgentRuntimeError(
                "Gemini addressing router failed with HTTP "
                f"{error.code}: {trim_pear_agent_text(raw_error, limit=self.output_limit)}"
            ) from error
        except (TimeoutError, urllib.error.URLError) as error:
            raise PearAgentRuntimeError(f"Gemini addressing router failed: {error}") from error

        if status != 200:
            raise PearAgentRuntimeError(
                "Gemini addressing router failed with HTTP "
                f"{status}: {trim_pear_agent_text(raw_response, limit=self.output_limit)}"
            )
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as error:
            raise PearAgentRuntimeError(
                "Gemini addressing router returned invalid JSON."
            ) from error
        if not isinstance(parsed, dict):
            return parse_pear_addressing_json_output("")
        return parse_pear_addressing_json_output(pear_gemini_interaction_output_text(parsed))


def build_pear_codex_developer_instructions() -> str:
    return "\n".join(
        [
            "You are Pear, a Codex-powered pair-programming agent working from a Zulip topic.",
            "Use the repository and tools available in this runtime to make concrete progress.",
            (
                "Treat the runtime cwd as the isolated topic worktree; the Pear/Zulip "
                "server checkout is not the user's target repo unless the topic context "
                "explicitly pins it."
            ),
            (
                "README.md and other workspace files are ordinary project content; inspect "
                "or edit them only when the task calls for it, never as hidden Pear state."
            ),
            "Use Codex-native skills from CODEX_HOME/skills when a relevant SKILL.md applies.",
            (
                "Track multi-step work with your plan tool and update step statuses as "
                "you go — the plan renders live for the people in the topic."
            ),
            "Keep changes focused on the user's request and verify them before finishing.",
            "For casual, conversational, choice-only, or answer-only requests, respond directly.",
            "Do not run repository/session checks just to make a casual reply sound grounded.",
            "Use tools only when the request needs workspace facts, code changes, external lookup, or verification.",
            "Resolve references like 'above', 'that', 'what did I ask', and 'who is here' from the recent Zulip transcript below.",
            "Do not claim you cannot see recent topic history when the transcript below contains the answer.",
            "Final response requirements:",
            "- Keep the response concise and suitable for posting back into the Zulip topic.",
            "- Include artifact lines using these exact prefixes where applicable:",
            "  Repo: <repository path or remote>",
            "  Runtime: <agent runtime context>",
            "  Verification: <command or check result>",
            "  Diff: <local diff or summary>",
            "  Branch: <branch name>",
            "  PR: <pull request URL>",
            "  Session: <session record URL or Codex thread reference>",
            "  Ticket: <ticket URL, ID, or path>",
            "  Issue: <issue URL, ID, or path>",
            "  Reviewers: <reviewer handles or teams>",
            "  Doc: <document or thread reference>",
        ]
    )


def build_pear_codex_turn_prompt(active_session: PearActiveSessionSummary, task: str) -> str:
    steering = active_session["latest_steering_instruction"] or "None"
    latest_verification = active_session["latest_verification"] or "None"
    workspace_context = active_session.get("workspace_context", {})
    workspace_lines = [
        f"- repository: {workspace_context.get('repository') or 'unknown'}",
        f"- remote: {workspace_context.get('remote_url') or 'unknown'}",
        f"- worktree: {workspace_context.get('worktree') or 'unknown'}",
        f"- workdir: {workspace_context.get('workdir') or 'unknown'}",
        f"- branch: {workspace_context.get('branch') or 'unknown'}",
        f"- head: {workspace_context.get('head') or 'unknown'}",
        "- contract: the Zulip stream maps to the repository, and the Zulip topic/subthread maps to the Git branch.",
        "- privacy: refer to the workspace by branch or worktree name in replies; never print the absolute workdir path in the topic.",
        "- artifacts: call pear_artifact when a native diagram, compact explainer, status display, or local interaction answers better than prose; if you announce the pear-artifacts skill, you must complete a successful call and must not substitute ASCII art or a fenced diagram. Provide meaning and data only because Pear owns all styling, and do not print artifact JSON in the reply.",
        "- servers and watchers: never start a long-lived process (http.server, watch loops, dev servers) in the foreground — it blocks your turn from ever finishing. Run it detached (nohup ... >/dev/null 2>&1 &) and verify with a follow-up probe.",
        "- math: write math in Zulip's dialect — $$...$$ for inline math and ```math fenced blocks for display equations; \\[ \\] and \\( \\) do not render here.",
        "- checklists: your plan tool is the working checklist — its steps render live in the topic rail, so keep it current instead of duplicating it in chat or a workspace document.",
    ]
    artifacts = active_session["artifacts"][:5]
    artifact_lines = [f"- {artifact['label']}: {artifact['value']}" for artifact in artifacts] or [
        "- None"
    ]
    recent_messages = active_session.get("recent_messages", [])
    recent_human_participants = list(
        dict.fromkeys(
            message["sender"] for message in recent_messages if message["sender_is_bot"] is not True
        )
    )
    recent_participant_lines = [
        f"- {participant}" for participant in recent_human_participants
    ] or ["- None visible in the provided transcript"]
    recent_transcript_lines = [
        (
            f"- #{message['message_id']} {message['sender']} "
            f"({'bot' if message['sender_is_bot'] else 'human'}): {message['content']}"
        )
        for message in recent_messages[-30:]
    ] or ["- No recent topic transcript was provided."]
    return "\n".join(
        [
            f"Task: {task.strip() or 'Continue the topic Codex thread.'}",
            f"Zulip stream: {active_session['stream']}",
            f"Zulip topic: {active_session['topic']}",
            f"Session state: {active_session['state']}",
            f"Latest steering: {steering}",
            f"Latest verification: {latest_verification}",
            "",
            "Workspace context:",
            *workspace_lines,
            "",
            "Visible recent human participants:",
            *recent_participant_lines,
            "",
            "Recent Zulip topic transcript, oldest to newest:",
            *recent_transcript_lines,
            "",
            "Recent artifacts:",
            *artifact_lines,
        ]
    )


def pear_tool_results_from_text(
    text: str,
    *,
    output_limit: int,
) -> list[PearWorkerToolResult]:
    artifacts = parse_pear_artifacts(text, message_id=0)
    if artifacts:
        prose = "\n".join(
            line
            for line in text.splitlines()
            if not parse_pear_artifacts(line, message_id=0)
            and not pear_is_retired_visible_artifact_line(line)
        ).strip()
        results: list[PearWorkerToolResult] = []
        if prose:
            results.append(
                {
                    "label": "Result",
                    "value": trim_pear_agent_text(prose, limit=output_limit),
                }
            )
        results.extend(
            {
                "label": artifact["label"],
                "value": trim_pear_agent_text(artifact["value"], limit=output_limit),
            }
            for artifact in artifacts
        )
        return results
    if not text.strip():
        return [
            {
                "label": "Verification",
                "value": "Codex completed without a final text response.",
            }
        ]
    return [
        {
            "label": "Result",
            "value": trim_pear_agent_text(text, limit=output_limit),
        }
    ]


def build_pear_runtime_value(
    runtime: PearAgentRuntime,
    *,
    workdir: str | None = None,
    sandbox: str | None = None,
    approval_policy: str | None = None,
    model: str | None = None,
    service_tier: str | None = None,
    reasoning_effort: str | None = None,
    process_isolation: PearProcessIsolation | None = None,
) -> str:
    parts: list[str] = [runtime]
    if workdir is not None:
        parts.append(f"cwd={workdir}")
    if sandbox is not None:
        parts.append(f"sandbox={sandbox}")
    if approval_policy is not None:
        parts.append(f"approval_policy={approval_policy}")
    if model is not None:
        parts.append(f"model={model}")
    if service_tier is not None:
        parts.append(f"tier={service_tier}")
    if reasoning_effort is not None:
        parts.append(f"effort={reasoning_effort}")
    if process_isolation is not None:
        parts.append(f"isolation={process_isolation.label()}")
    return " ".join(parts)


def ensure_pear_runtime_result(
    results: list[PearWorkerToolResult],
    runtime_value: str,
) -> list[PearWorkerToolResult]:
    if any(
        result["label"].lower() == "runtime" and result["value"] == runtime_value
        for result in results
    ):
        return results
    return [{"label": "Runtime", "value": runtime_value}, *results]


def merge_pear_tool_results(
    *result_groups: Sequence[PearWorkerToolResult],
) -> list[PearWorkerToolResult]:
    merged: list[PearWorkerToolResult] = []
    seen: set[tuple[str, str]] = set()
    for result_group in result_groups:
        for result in result_group:
            key = (result["label"], result["value"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)
    return merged


def pear_codex_item_text(item: dict[str, Any]) -> str:
    for key in ("text", "message", "summary", "output", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def pear_codex_item_title(item: dict[str, Any]) -> str:
    for key in ("command", "name", "tool_name", "path", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return pear_codex_item_text(item)


def pear_codex_plan_steps(item: dict[str, Any]) -> list[dict[str, str]]:
    raw_steps = item.get("plan") or item.get("steps")
    if not isinstance(raw_steps, list):
        return []
    steps: list[dict[str, str]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        step = raw_step.get("step") or raw_step.get("text") or raw_step.get("description")
        status = raw_step.get("status") or raw_step.get("state") or "pending"
        if not isinstance(step, str) or not step.strip():
            continue
        steps.append({"step": step.strip(), "status": str(status)})
    return steps


def pear_codex_item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("id", "type", "status", "command", "name", "tool_name", "path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = trim_pear_agent_text(value, limit=500)
    return metadata


def pear_codex_event_delta_text(event: dict[str, Any]) -> str:
    for key in ("delta", "text_delta", "content_delta"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            text = pear_codex_item_text(value)
            if text:
                return text

    item = event.get("item")
    if isinstance(item, dict):
        for key in ("delta", "text_delta", "content_delta"):
            value = item.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                text = pear_codex_item_text(value)
                if text:
                    return text

    data = event.get("data")
    if isinstance(data, dict):
        return pear_codex_event_delta_text(data)
    return ""


def pear_codex_event_item_id(event: dict[str, Any]) -> str:
    for key in ("item_id", "id", "output_index", "message_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    item = event.get("item")
    if isinstance(item, dict):
        value = item.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "assistant"


def pear_transcript_events_from_codex_json_event(
    event: dict[str, Any],
) -> list[PearWorkerTranscriptEvent]:
    event_type = event.get("type")
    if event_type == "thread.started" and isinstance(event.get("thread_id"), str):
        thread_id = event["thread_id"]
        return [
            {
                "kind": "session",
                "status": "done",
                "title": f"Codex thread {thread_id}",
                "body": thread_id,
                "metadata": {"thread_id": thread_id},
            }
        ]

    if event_type == "turn.started":
        return [
            {
                "kind": "status",
                "status": "running",
                "title": "Codex turn started.",
            }
        ]
    if event_type == "turn.completed":
        return [
            {
                "kind": "status",
                "status": "done",
                "title": "Codex turn completed.",
                "metadata": {"usage": event.get("usage")},
            }
        ]
    if event_type == "turn.failed":
        return [
            {
                "kind": "error",
                "status": "failed",
                "title": "Codex turn failed.",
                "body": str(event.get("error", "") or event.get("message", "")),
            }
        ]
    if event_type == "error":
        return [
            {
                "kind": "error",
                "status": "failed",
                "title": "Codex runtime error.",
                "body": str(event.get("message", "") or event.get("error", "")),
            }
        ]

    if event_type not in {"item.started", "item.completed"}:
        return []
    item = event.get("item")
    if not isinstance(item, dict):
        return []

    item_type = item.get("type")
    item_id = item.get("id")
    status = "running" if event_type == "item.started" else "done"
    raw_status = item.get("status")
    if isinstance(raw_status, str):
        if raw_status in {"failed", "error"}:
            status = "failed"
        elif raw_status in {"completed", "done"}:
            status = "done"
        elif raw_status in {"in_progress", "running"}:
            status = "running"
    title = trim_pear_agent_text(pear_codex_item_title(item), limit=1000)
    raw_body = pear_codex_item_text(item)
    body = (
        raw_body
        if item_type == "agent_message" and status == "done"
        else trim_pear_agent_text(raw_body, limit=4000)
    )
    base_event: PearWorkerTranscriptEvent = {
        "status": status,
        "title": title,
        "body": body,
        "metadata": pear_codex_item_metadata(item),
    }
    if isinstance(item_id, str):
        base_event["item_id"] = item_id

    if item_type == "agent_message":
        return [
            {
                **base_event,
                "kind": "assistant",
                "title": "Codex",
                "body": body,
            }
        ]
    if item_type == "reasoning":
        return [{**base_event, "kind": "reasoning", "title": title or "Reasoning"}]
    if item_type == "command_execution":
        return [{**base_event, "kind": "command", "title": title or "command"}]
    if item_type == "file_change":
        return [{**base_event, "kind": "file_change", "title": title or "file change"}]
    if item_type == "plan_update":
        steps = pear_codex_plan_steps(item)
        return [
            {
                **base_event,
                "kind": "plan",
                "title": "Plan",
                "metadata": {**pear_codex_item_metadata(item), "steps": steps},
            }
        ]
    if item_type == "context_compaction":
        # The engine compacts its own context; surface it as a first-class
        # moment (the client renders a loader while it runs).
        phase = "completed" if status == "done" else "started"
        return [
            {
                **base_event,
                "kind": "status",
                "title": ("Context compacted." if phase == "completed" else "Compacting context…"),
                "metadata": {
                    **pear_codex_item_metadata(item),
                    "operation": "compaction",
                    "phase": phase,
                },
            }
        ]
    if item_type == "user_message":
        # The turn prompt echoed back as an item; the topic already shows it.
        return []
    if isinstance(item_type, str) and ("tool" in item_type or item_type == "web_search"):
        return [{**base_event, "kind": "tool", "title": title or item_type}]
    if isinstance(item_type, str):
        return [{**base_event, "kind": "status", "title": title or item_type}]
    return []


class PearCodexJsonTranscriptTranslator:
    def __init__(self) -> None:
        self.assistant_text_by_item_id: dict[str, str] = {}

    def events_from_json_event(self, event: dict[str, Any]) -> list[PearWorkerTranscriptEvent]:
        event_type = event.get("type")
        if isinstance(event_type, str) and "delta" in event_type:
            delta_text = pear_codex_event_delta_text(event)
            if not delta_text:
                return []
            item_id = pear_codex_event_item_id(event)
            body = self.assistant_text_by_item_id.get(item_id, "") + delta_text
            self.assistant_text_by_item_id[item_id] = body
            return [
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": body[-4000:],
                    "item_id": item_id,
                    "metadata": {"streaming": True},
                }
            ]

        transcript_events = pear_transcript_events_from_codex_json_event(event)
        for transcript_event in transcript_events:
            if transcript_event.get("kind") != "assistant":
                continue
            transcript_item_id = transcript_event.get("item_id")
            transcript_body = transcript_event.get("body")
            if isinstance(transcript_item_id, str) and isinstance(transcript_body, str):
                self.assistant_text_by_item_id[transcript_item_id] = transcript_body
        return transcript_events


PEAR_APP_SERVER_ITEM_TYPE_MAP: dict[str, str] = {
    "agentMessage": "agent_message",
    "commandExecution": "command_execution",
    "fileChange": "file_change",
    "mcpToolCall": "mcp_tool_call",
    "dynamicToolCall": "dynamic_tool_call",
    "plan": "plan_update",
    "webSearch": "web_search",
    "contextCompaction": "context_compaction",
    "userMessage": "user_message",
}

# Engine chatter that never belongs in a run transcript. turn/diff joins
# the translated set with the live-diff surface.
PEAR_APP_SERVER_IGNORED_METHODS = frozenset(
    {
        "thread/status/changed",
        "mcpServer/startupStatus/updated",
        "remoteControl/status/changed",
        "thread/goal/cleared",
        "thread/goal/updated",
        "turn/diff/updated",
    }
)


def pear_app_server_thread_id_from_result(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return None
    thread_id = thread.get("id")
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id.strip()
    return None


def pear_app_server_turn_id_from_result(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    turn = result.get("turn")
    if not isinstance(turn, dict):
        return None
    turn_id = turn.get("id")
    if isinstance(turn_id, str) and turn_id.strip():
        return turn_id.strip()
    return None


def pear_app_server_normalized_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    item_type = normalized.get("type")
    if isinstance(item_type, str):
        normalized["type"] = PEAR_APP_SERVER_ITEM_TYPE_MAP.get(item_type, item_type)
    tool = normalized.get("tool")
    if isinstance(tool, str):
        normalized.setdefault("tool_name", tool)
        normalized.setdefault("name", tool)
    return normalized


def pear_app_server_usage_event(params: dict[str, Any]) -> PearWorkerTranscriptEvent | None:
    usage = pear_normalize_token_usage(params)
    if usage is None:
        return None
    return {
        "kind": "usage",
        "status": "done",
        "title": "Token usage updated.",
        "metadata": {"usage": usage},
    }


class PearCodexAppServerTranscriptTranslator:
    def __init__(self) -> None:
        self.json_translator = PearCodexJsonTranscriptTranslator()
        self.last_rate_limit_key: tuple[object, ...] | None = None
        self.reported_unknown_methods: set[str] = set()

    def rate_limit_events(self, params: dict[str, Any]) -> list[PearWorkerTranscriptEvent]:
        limits = params.get("rateLimits")
        if not isinstance(limits, dict):
            return []
        raw_primary = limits.get("primary")
        primary = raw_primary if isinstance(raw_primary, dict) else {}
        raw_secondary = limits.get("secondary")
        secondary = raw_secondary if isinstance(raw_secondary, dict) else {}
        key = (
            primary.get("usedPercent"),
            secondary.get("usedPercent"),
            limits.get("planType"),
        )
        # The engine re-announces limits after every command; only a
        # changed reading is worth a transcript row.
        if key == self.last_rate_limit_key:
            return []
        self.last_rate_limit_key = key
        used = primary.get("usedPercent")
        title = (
            f"Rate limit window at {used:.0f}% used."
            if isinstance(used, int | float)
            else "Rate limits updated."
        )
        return [
            {
                "kind": "status",
                "status": "done",
                "title": title,
                "metadata": {"rate_limits": limits},
            }
        ]

    def unknown_method_events(
        self, method: str, params: dict[str, Any]
    ) -> list[PearWorkerTranscriptEvent]:
        """Tolerant reader: an untranslated engine signal surfaces once per
        runtime instead of vanishing — the codexification surface-diff
        feeds on these logs after every Codex upgrade."""
        if method in self.reported_unknown_methods or "elta" in method:
            return []
        self.reported_unknown_methods.add(method)
        logger.info("Pear observed untranslated app-server method: %s", method)
        return [
            {
                "kind": "status",
                "status": "done",
                "title": f"Codex signal: {method}",
                "metadata": {"method": method, "param_keys": sorted(params)[:8]},
            }
        ]

    def events_from_message(self, message: dict[str, Any]) -> list[PearWorkerTranscriptEvent]:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return []

        if method == "thread/started":
            thread = params.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str):
                return []
            return [
                {
                    "kind": "session",
                    "status": "done",
                    "title": f"Codex thread {thread_id}",
                    "body": thread_id,
                    "metadata": {"thread_id": thread_id},
                }
            ]
        if method == "turn/plan/updated":
            # The app-server emits the model's update_plan checklist as
            # its own notification (not a thread item): full-state
            # replacement per update, statuses in camelCase.
            raw_steps = params.get("plan")
            steps: list[dict[str, str]] = []
            if isinstance(raw_steps, list):
                for raw_step in raw_steps:
                    if not isinstance(raw_step, dict):
                        continue
                    step = raw_step.get("step")
                    status = str(raw_step.get("status", "pending"))
                    status = {"inProgress": "in_progress"}.get(status, status)
                    if isinstance(step, str) and step.strip():
                        steps.append({"step": step.strip(), "status": status})
            if not steps:
                return []
            return [
                {
                    "kind": "plan",
                    "status": "done",
                    "title": "Plan",
                    "metadata": {"steps": steps},
                }
            ]
        if method == "turn/started":
            return [
                {
                    "kind": "status",
                    "status": "running",
                    "title": "Codex turn started.",
                }
            ]
        if method == "turn/completed":
            return [
                {
                    "kind": "status",
                    "status": "done",
                    "title": "Codex turn completed.",
                    "metadata": {"turn": params.get("turn")},
                }
            ]
        if method == "thread/tokenUsage/updated":
            usage_event = pear_app_server_usage_event(params)
            return [usage_event] if usage_event is not None else []
        if method == "error":
            return [
                {
                    "kind": "error",
                    "status": "failed",
                    "title": "Codex runtime error.",
                    "body": str(params.get("message", "") or params.get("error", "")),
                    "metadata": {"error": params},
                }
            ]
        if method == "item/agentMessage/delta":
            return self.json_translator.events_from_json_event(
                {
                    "type": "item.delta",
                    "item_id": params.get("itemId"),
                    "delta": params.get("delta", ""),
                }
            )
        if method == "account/rateLimits/updated":
            return self.rate_limit_events(params)
        if method in {"warning", "configWarning"}:
            body = str(params.get("message") or params.get("summary") or "").strip()
            if not body:
                return []
            return [
                {
                    "kind": "status",
                    "status": "done",
                    "title": "Codex warning.",
                    "body": trim_pear_agent_text(body, limit=600),
                    "metadata": {"method": method},
                }
            ]
        if method in PEAR_APP_SERVER_IGNORED_METHODS:
            return []
        if method not in {"item/started", "item/completed"}:
            return self.unknown_method_events(method, params)
        item = params.get("item")
        if not isinstance(item, dict):
            return []
        return self.json_translator.events_from_json_event(
            {
                "type": "item.started" if method == "item/started" else "item.completed",
                "item": pear_app_server_normalized_item(item),
            }
        )


def append_pear_codex_thread_artifact(
    results: list[PearWorkerToolResult], thread_id: str | None
) -> list[PearWorkerToolResult]:
    if thread_id is None:
        return results
    thread_value = f"Codex thread {thread_id}"
    if any(
        result["label"]
        in {
            "Session",
            "Session record",
            "Pear session",
            "Codex session",
            "Doc",
            "Docs",
        }
        and result["value"] == thread_value
        for result in results
    ):
        return results
    return [*results, {"label": "Session", "value": thread_value}]


class PearDisabledAgentRunner:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __call__(
        self,
        active_session: PearActiveSessionSummary,
        task: str,
        *,
        collaboration_mode: str | None = None,
        user_input_provider: PearUserInputProvider | None = None,
        event_callback: PearWorkerTranscriptCallback | None = None,
    ) -> Sequence[PearWorkerToolResult]:
        return [
            {
                "label": "Runtime",
                "value": build_pear_runtime_value("disabled"),
            },
            {
                "label": "Error",
                "value": self.reason,
            },
        ]

    def runtime_value(self) -> str:
        return build_pear_runtime_value("disabled")


class PearCodexAppServerToolRunner:
    def __init__(
        self,
        *,
        codex_bin: str,
        workdir: str | None,
        model: str | None,
        sandbox: str,
        approval_policy: str,
        timeout_seconds: int,
        output_limit: int,
        service_tier: str | None = None,
        fast_mode_enabled: bool = False,
        reasoning_effort: str | None = None,
        ephemeral: bool = False,
        process_isolation: PearProcessIsolation | None = None,
        popen_command: PearSubprocessPopen = subprocess.Popen,
    ) -> None:
        self.codex_bin = codex_bin
        self.workdir = workdir
        self.model = model
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.service_tier = service_tier
        self.fast_mode_enabled = fast_mode_enabled
        self.reasoning_effort = reasoning_effort
        self.ephemeral = ephemeral
        self.process_isolation = process_isolation
        self.popen_command = popen_command
        self.process: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.reader_thread: threading.Thread | None = None
        self.next_request_id = 0
        self.lock = threading.RLock()
        self.loaded_thread_id: str | None = None
        self.active_thread_id: str | None = None
        self.active_turn_id: str | None = None
        self.background_request_ids: set[int] = set()
        self.current_user_input_provider: PearUserInputProvider | None = None
        self.pending_artifacts: list[str] = []
        self.translator = PearCodexAppServerTranscriptTranslator()

    def config_args(self) -> list[str]:
        args: list[str] = []
        if self.service_tier:
            args.extend(["--config", f'service_tier="{self.service_tier}"'])
        if self.fast_mode_enabled:
            args.extend(["--config", "features.fast_mode=true"])
        args.extend(["--config", "features.memories=true"])
        if self.reasoning_effort:
            args.extend(["--config", f'model_reasoning_effort="{self.reasoning_effort}"'])
        return args

    def build_command(self) -> list[str]:
        command = [
            self.codex_bin,
            "app-server",
            "--stdio",
            *self.config_args(),
        ]
        return pear_isolated_command(command, self.process_isolation)

    def ensure_started(self) -> None:
        if (
            self.process is not None
            and self.process.poll() is None
            and self.reader_thread is not None
        ):
            return
        try:
            self.process = self.popen_command(
                self.build_command(),
                cwd=pear_subprocess_cwd(self.workdir, self.process_isolation),
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
            )
        except FileNotFoundError as error:
            raise PearAgentRuntimeError(f"Codex executable not found: {self.codex_bin}") from error

        self.messages = queue.Queue()
        self.reader_thread = threading.Thread(
            target=self.read_messages,
            name="pear-codex-app-server-reader",
            daemon=True,
        )
        self.reader_thread.start()
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "pear",
                    "title": "Pear",
                    "version": "0.1.0",
                },
                # experimentalApi unlocks the interactive tool surface
                # (item/tool/requestUserInput is EXPERIMENTAL in the
                # app-server protocol); unknown experimental payloads
                # degrade to generic transcript events by design.
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized", {})

    def read_messages(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            self.messages.put(None)
            return
        try:
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    pear_capture_protocol_message("recv", message)
                    self.messages.put(message)
        finally:
            self.messages.put(None)

    def next_id(self) -> int:
        self.next_request_id += 1
        return self.next_request_id

    def send_message(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise PearAgentRuntimeError("Codex app-server is not running.")
        pear_capture_protocol_message("send", message)
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send_message({"method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        request_id = self.next_id()
        self.send_message({"id": request_id, "method": method, "params": params})
        return self.wait_for_response(
            request_id,
            event_callback=event_callback,
            timeout_seconds=timeout_seconds,
        )

    def wait_for_response(
        self,
        request_id: int,
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        while True:
            timeout = max(deadline - time.monotonic(), 0)
            if timeout == 0:
                raise PearAgentRuntimeError(
                    f"Codex app-server timed out after {timeout_seconds or self.timeout_seconds} seconds."
                )
            message = self.next_message(timeout)
            if message is None:
                raise PearAgentRuntimeError("Codex app-server stopped unexpectedly.")
            if self.handle_server_request(message, event_callback=event_callback):
                continue
            if "method" in message:
                self.handle_notification(message, event_callback=event_callback)
                continue
            if self.consume_background_response(message):
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise PearAgentRuntimeError(
                    "Codex app-server failed: "
                    f"{trim_pear_agent_text(str(message['error']), limit=self.output_limit)}"
                )
            return message.get("result")

    def next_message(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty as error:
            raise PearAgentRuntimeError(
                f"Codex app-server timed out after {self.timeout_seconds} seconds."
            ) from error

    def handle_server_request(
        self,
        message: dict[str, Any],
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
    ) -> bool:
        if "id" not in message or "method" not in message:
            return False
        if "result" in message or "error" in message:
            return False
        request_id = message["id"]
        method = str(message.get("method", "unknown"))
        raw_params = message.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        result, error, event = self.answer_for_server_request(method, params)
        reply: dict[str, Any] = {"id": request_id}
        if error is not None:
            reply["error"] = error
        else:
            reply["result"] = result
        self.send_message(reply)
        if event is not None and event_callback is not None:
            event_callback(event)
        return True

    def answer_for_server_request(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, PearWorkerTranscriptEvent | None]:
        """Answer a server-initiated request → (result, error, event).

        Escalation approvals are granted when OS-user isolation is the
        enforcement boundary — the isolation user cannot escape its home
        and worktree, so the engine's prompt is a formality there. Without
        isolation the decline preserves the sandbox."""
        isolated = self.process_isolation is not None
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = "acceptForSession" if isolated else "decline"
            subject = params.get("command") or params.get("reason") or method
            event: PearWorkerTranscriptEvent = {
                "kind": "status",
                "status": "done",
                "title": (
                    "Approved an escalation request."
                    if isolated
                    else "Declined an escalation request."
                ),
                "body": trim_pear_agent_text(str(subject), limit=300),
                "metadata": {"method": method, "decision": decision},
            }
            return {"decision": decision}, None, event
        if method == "item/tool/requestUserInput":
            raw_questions = params.get("questions")
            questions = (
                [question for question in raw_questions if isinstance(question, dict)]
                if isinstance(raw_questions, list)
                else []
            )
            provider = self.current_user_input_provider
            answers: dict[str, list[str]] | None = None
            if provider is not None and questions:
                try:
                    answers = provider(questions)
                except Exception:
                    logger.exception("Pear user-input provider failed.")
                    answers = None
            if answers is not None:
                return (
                    {
                        "answers": {
                            question_id: {"answers": answer_list}
                            for question_id, answer_list in answers.items()
                        }
                    },
                    None,
                    None,
                )
            first = questions[0] if questions else {}
            question_text = first.get("question", "")
            event = {
                "kind": "status",
                "status": "done",
                "title": "Codex asked a question and no answer arrived.",
                "body": trim_pear_agent_text(str(question_text), limit=300),
                "metadata": {"method": method},
            }
            return (
                None,
                {"code": -32601, "message": "No user answer was available."},
                event,
            )
        if method == "item/tool/call":
            tool_name = params.get("tool")
            if tool_name != "pear_artifact" or params.get("namespace") not in {None, ""}:
                return (
                    {"success": False, "contentItems": []},
                    None,
                    {
                        "kind": "tool",
                        "status": "failed",
                        "title": f"Unsupported dynamic tool: {tool_name}",
                    },
                )
            arguments = params.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as error:
                    arguments = {"invalid_json": str(error)}
            try:
                document, encoded = canonicalize_pear_artifact(arguments)
            except PearArtifactError as error:
                detail = trim_pear_agent_text(str(error), limit=500)
                return (
                    {
                        "success": False,
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": f"Artifact rejected: {detail}",
                            }
                        ],
                    },
                    None,
                    {
                        "kind": "tool",
                        "status": "failed",
                        "title": "Artifact rejected.",
                        "body": detail,
                        "metadata": {"tool_name": "pear_artifact"},
                    },
                )
            self.pending_artifacts.append(encoded)
            return (
                {
                    "success": True,
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": "Artifact accepted and attached to the final Pear message.",
                        }
                    ],
                },
                None,
                {
                    "kind": "tool",
                    "status": "done",
                    "title": "Artifact ready.",
                    "metadata": {
                        "tool_name": "pear_artifact",
                        "artifact_version": document["version"],
                        "artifact_bytes": len(encoded.encode()),
                        "artifact_blocks": len(document["blocks"]),
                        "artifact_nodes": pear_artifact_node_count(document),
                    },
                },
            )
        event = {
            "kind": "status",
            "status": "done",
            "title": f"Codex request declined: {method}",
            "metadata": {"method": method, "param_keys": sorted(params)[:8]},
        }
        return (
            None,
            {
                "code": -32601,
                "message": f"Pear does not implement app-server request {method}.",
            },
            event,
        )

    def handle_notification(
        self,
        message: dict[str, Any],
        *,
        event_callback: PearWorkerTranscriptCallback | None,
    ) -> list[PearWorkerTranscriptEvent]:
        method = message.get("method")
        params = message.get("params")
        if method == "turn/started" and isinstance(params, dict):
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if isinstance(turn_id, str):
                self.active_turn_id = turn_id
        if method == "turn/completed":
            self.active_turn_id = None
        events = self.translator.events_from_message(message)
        if event_callback is not None:
            for event in events:
                event_callback(event)
        return events

    def thread_params(self, *, existing_thread_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "approvalPolicy": self.approval_policy,
            "cwd": self.workdir,
            "ephemeral": self.ephemeral,
            "serviceTier": self.service_tier,
            "model": self.model,
            "sandbox": self.sandbox,
            "personality": "pragmatic",
            "dynamicTools": [PEAR_ARTIFACT_TOOL_SPEC],
        }
        if existing_thread_id is not None:
            params["threadId"] = existing_thread_id
            params.pop("ephemeral", None)
            params.pop("dynamicTools", None)
        else:
            params["developerInstructions"] = build_pear_codex_developer_instructions()
        return {key: value for key, value in params.items() if value is not None}

    def turn_params(
        self, thread_id: str, prompt: str, *, collaboration_mode: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": self.approval_policy,
            "cwd": self.workdir,
            "serviceTier": self.service_tier,
            "effort": self.reasoning_effort,
            "model": self.model,
            "personality": "pragmatic",
        }
        if collaboration_mode is not None:
            params["collaborationMode"] = {"mode": collaboration_mode}
        return {key: value for key, value in params.items() if value is not None}

    def ensure_thread(
        self,
        active_session: PearActiveSessionSummary,
        *,
        event_callback: PearWorkerTranscriptCallback | None,
    ) -> str:
        latest_thread_id = active_session.get("latest_codex_thread_id")
        existing_thread_id = (
            latest_thread_id.strip()
            if isinstance(latest_thread_id, str) and latest_thread_id.strip()
            else None
        )
        thread_id = existing_thread_id
        if thread_id is not None and thread_id == self.loaded_thread_id:
            return thread_id
        if thread_id is not None:
            result = self.request(
                "thread/resume",
                self.thread_params(existing_thread_id=thread_id),
                event_callback=event_callback,
            )
            resumed_thread_id = pear_app_server_thread_id_from_result(result) or thread_id
            self.loaded_thread_id = resumed_thread_id
            return resumed_thread_id
        result = self.request(
            "thread/start",
            self.thread_params(),
            event_callback=event_callback,
        )
        started_thread_id = pear_app_server_thread_id_from_result(result)
        if started_thread_id is None:
            raise PearAgentRuntimeError("Codex app-server did not return a thread id.")
        self.loaded_thread_id = started_thread_id
        if event_callback is not None:
            event_callback(
                {
                    "kind": "session",
                    "status": "done",
                    "title": f"Codex thread {started_thread_id}",
                    "body": started_thread_id,
                    "metadata": {"thread_id": started_thread_id},
                }
            )
        return started_thread_id

    def wait_for_turn(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        event_callback: PearWorkerTranscriptCallback | None,
    ) -> tuple[str, int, int, int]:
        final_text = ""
        command_count = 0
        file_change_count = 0
        tool_call_count = 0
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            timeout = max(deadline - time.monotonic(), 0)
            if timeout == 0:
                raise PearAgentRuntimeError(
                    f"Codex app-server timed out after {self.timeout_seconds} seconds."
                )
            message = self.next_message(timeout)
            if message is None:
                raise PearAgentRuntimeError("Codex app-server stopped unexpectedly.")
            if self.handle_server_request(message, event_callback=event_callback):
                continue
            if "method" not in message:
                if self.consume_background_response(message):
                    continue
                if "error" in message:
                    raise PearAgentRuntimeError(
                        "Codex app-server failed: "
                        f"{trim_pear_agent_text(str(message['error']), limit=self.output_limit)}"
                    )
                continue
            events = self.handle_notification(message, event_callback=event_callback)
            for event in events:
                if event.get("kind") == "assistant":
                    body = event.get("body")
                    if isinstance(body, str):
                        final_text = body
                if event.get("status") != "done":
                    continue
                kind = event.get("kind")
                if kind == "command":
                    command_count += 1
                elif kind == "file_change":
                    file_change_count += 1
                elif kind == "tool":
                    tool_call_count += 1
            method = message.get("method")
            params = message.get("params")
            if method != "turn/completed" or not isinstance(params, dict):
                continue
            if params.get("threadId") != thread_id:
                continue
            completed_turn = params.get("turn")
            completed_turn_id = (
                completed_turn.get("id") if isinstance(completed_turn, dict) else None
            )
            if turn_id is not None and completed_turn_id != turn_id:
                continue
            self.active_turn_id = None
            return final_text, command_count, file_change_count, tool_call_count

    def __call__(
        self,
        active_session: PearActiveSessionSummary,
        task: str,
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
        collaboration_mode: str | None = None,
        user_input_provider: PearUserInputProvider | None = None,
    ) -> Sequence[PearWorkerToolResult]:
        with self.lock:
            self.ensure_started()
            self.pending_artifacts = []
            self.current_user_input_provider = user_input_provider
            thread_id = self.ensure_thread(active_session, event_callback=event_callback)
            self.active_thread_id = thread_id
            prompt = build_pear_codex_turn_prompt(active_session, task)
            result = self.request(
                "turn/start",
                self.turn_params(thread_id, prompt, collaboration_mode=collaboration_mode),
                event_callback=event_callback,
            )
            turn_id = pear_app_server_turn_id_from_result(result)
            if turn_id is not None:
                self.active_turn_id = turn_id
            try:
                final_text, command_count, file_change_count, tool_call_count = self.wait_for_turn(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event_callback=event_callback,
                )
            finally:
                self.current_user_input_provider = None
            results = pear_tool_results_from_text(final_text, output_limit=self.output_limit)
            results.extend(artifact_tool_result(encoded) for encoded in self.pending_artifacts)
            summary_parts = []
            if command_count:
                summary_parts.append(f"{command_count} command event(s)")
            if file_change_count:
                summary_parts.append(f"{file_change_count} file change event(s)")
            if tool_call_count:
                summary_parts.append(f"{tool_call_count} tool call event(s)")
            if summary_parts:
                results.insert(
                    0,
                    {
                        "label": "Verification",
                        "value": f"Codex app-server completed with {', '.join(summary_parts)}.",
                    },
                )
            results = ensure_pear_runtime_result(results, self.runtime_value())
            return append_pear_codex_thread_artifact(results, thread_id)

    def consume_background_response(self, message: dict[str, Any]) -> bool:
        """Swallow responses to fire-and-forget requests (steer/interrupt):
        a late error reply must not fail an unrelated wait loop."""
        message_id = message.get("id")
        if not isinstance(message_id, int) or message_id not in self.background_request_ids:
            return False
        self.background_request_ids.discard(message_id)
        if "error" in message:
            logger.info(
                "Pear background app-server request %s failed: %s",
                message_id,
                trim_pear_agent_text(str(message["error"]), limit=300),
            )
        return True

    def steer(self, instruction: str) -> bool:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return False
            if self.active_thread_id is None or self.active_turn_id is None:
                return False
            request_id = self.next_id()
            self.background_request_ids.add(request_id)
            self.send_message(
                {
                    "id": request_id,
                    "method": "turn/steer",
                    "params": {
                        "threadId": self.active_thread_id,
                        "expectedTurnId": self.active_turn_id,
                        "input": [{"type": "text", "text": instruction}],
                    },
                }
            )
            return True

    def interrupt(self) -> bool:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return False
            if self.active_thread_id is None or self.active_turn_id is None:
                return False
            request_id = self.next_id()
            self.background_request_ids.add(request_id)
            self.send_message(
                {
                    "id": request_id,
                    "method": "turn/interrupt",
                    "params": {
                        "threadId": self.active_thread_id,
                        "expectedTurnId": self.active_turn_id,
                    },
                }
            )
            return True

    def close(self) -> None:
        process = self.process
        self.process = None
        self.active_thread_id = None
        self.active_turn_id = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()

    def runtime_value(self) -> str:
        return build_pear_runtime_value(
            "codex-app-server",
            workdir=self.workdir,
            sandbox=self.sandbox,
            approval_policy=self.approval_policy,
            model=self.model,
            service_tier=self.service_tier,
            reasoning_effort=self.reasoning_effort,
            process_isolation=self.process_isolation,
        )
