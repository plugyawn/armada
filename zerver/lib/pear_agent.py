import asyncio
import inspect
import json
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from zerver.lib.pear import (
    PEAR_CANVAS_FILE_NAME,
    PearActiveSessionSummary,
    PearWorkerToolResult,
    PearWorkerTranscriptCallback,
    PearWorkerTranscriptEvent,
    pear_is_retired_visible_artifact_line,
    pear_normalize_token_usage,
    parse_pear_artifacts,
)

PearAgentRuntime = Literal["disabled", "codex-app-server"]
PearSubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]
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

    return "\n".join(
        [
            "You are Pear's message-addressing router for a Zulip coding agent.",
            "Decide whether the new untagged Zulip message is addressed to Pear.",
            "Do not answer the user. Do not run commands or use tools.",
            'Return exactly one JSON object: {"addressed": true} or {"addressed": false}.',
            "",
            "Return true when the message is addressed to Pear, Codex, the agent, or the assistant in this topic.",
            "Infer the addressee from the conversation, the active session state, and the new message.",
            "Return true when an active Pear run exists and the message is clearly steering that run, even if it says only 'you' or 'agent'.",
            "Return false for ordinary team chat, human-to-human discussion, status narration, ambiguous comments, or third-person mentions that do not ask Pear to act.",
            "When uncertain, return false.",
            "",
            f"Stream: {stream or 'unknown'}",
            f"Topic: {topic or 'unknown'}",
            "Session:",
            *session_lines,
            "",
            "New message:",
            content.strip(),
        ]
    )


def parse_pear_addressing_router_output(
    output: str,
) -> tuple[bool, str | None]:
    thread_id = None
    final_text = ""
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            final_text = line
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
            continue
        if event_type != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            final_text = item["text"]

    start = final_text.find("{")
    end = final_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return False, thread_id
    try:
        parsed = json.loads(final_text[start : end + 1])
    except json.JSONDecodeError:
        return False, thread_id
    if not isinstance(parsed, dict):
        return False, thread_id
    return parsed.get("addressed") is True, thread_id


def parse_pear_addressing_json_output(output: str) -> bool:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end < start:
        return False
    try:
        parsed = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("addressed") is True


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


class PearCodexExecAddressingIntentClassifier:
    def __init__(
        self,
        *,
        codex_bin: str,
        workdir: str | None,
        model: str | None,
        reasoning_effort: str | None,
        sandbox: str,
        approval_policy: str,
        timeout_seconds: int,
        output_limit: int,
        extra_args: Sequence[str] = (),
        run_command: PearSubprocessRunner | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.workdir = workdir
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.extra_args = list(extra_args)
        self.run_command = run_command or subprocess.run

    def __call__(
        self,
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message: dict[str, Any],
        bot_handler: Any,
    ) -> bool:
        stream = str(message.get("display_recipient") or "")
        topic = str(message.get("topic") or message.get("subject") or "")
        command = self.build_command()
        prompt = build_pear_addressing_router_prompt(
            content=content,
            active_session=active_session,
            stream=stream,
            topic=topic,
        )
        try:
            completed = self.run_command(
                command,
                input=prompt,
                cwd=self.workdir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as error:
            raise PearAgentRuntimeError(f"Codex executable not found: {self.codex_bin}") from error
        except subprocess.TimeoutExpired as error:
            raise PearAgentRuntimeError(
                f"Codex addressing router timed out after {self.timeout_seconds} seconds."
            ) from error

        if completed.returncode != 0:
            output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
            raise PearAgentRuntimeError(
                "Codex addressing router failed with exit code "
                f"{completed.returncode}: "
                f"{trim_pear_agent_text(output, limit=self.output_limit)}"
            )
        addressed, _thread_id = parse_pear_addressing_router_output(completed.stdout)
        return addressed

    def build_command(self) -> list[str]:
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--sandbox",
            self.sandbox,
            "--config",
            f'approval_policy="{self.approval_policy}"',
        ]
        if self.workdir is not None:
            command.extend(["--cd", self.workdir])
        if self.model:
            command.extend(["--model", self.model])
        if self.reasoning_effort:
            command.extend(
                ["--config", f'model_reasoning_effort="{self.reasoning_effort}"']
            )
        command.extend(self.extra_args)
        command.append("-")
        return command


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
    ) -> bool:
        if not self.api_key:
            raise PearAgentRuntimeError("Gemini addressing router API key is not configured.")

        stream = str(message.get("display_recipient") or "")
        topic = str(message.get("topic") or message.get("subject") or "")
        prompt = build_pear_addressing_router_prompt(
            content=content,
            active_session=active_session,
            stream=stream,
            topic=topic,
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
                    "properties": {"addressed": {"type": "boolean"}},
                    "required": ["addressed"],
                    "additionalProperties": False,
                },
            },
            "generation_config": {"temperature": 0},
        }
        if self.thinking_level and not self.model.startswith("gemma-"):
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
            raise PearAgentRuntimeError(
                f"Gemini addressing router failed: {error}"
            ) from error

        if status != 200:
            raise PearAgentRuntimeError(
                "Gemini addressing router failed with HTTP "
                f"{status}: {trim_pear_agent_text(raw_response, limit=self.output_limit)}"
            )
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as error:
            raise PearAgentRuntimeError("Gemini addressing router returned invalid JSON.") from error
        if not isinstance(parsed, dict):
            return False
        return parse_pear_addressing_json_output(
            pear_gemini_interaction_output_text(parsed)
        )


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
                f"Durable topic context lives in {PEAR_CANVAS_FILE_NAME}; read or edit it "
                "with file tools when the task depends on standing notes, pinned context, "
                "or long-lived constraints."
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
        "- math: write math in Zulip's dialect — $$...$$ for inline math and ```math fenced blocks for display equations; \\[ \\] and \\( \\) do not render here.",
        f"- checklists: your plan tool is the working checklist — its steps render live in the topic rail, so keep it current instead of duplicating it in chat. Durable checklists belong in {PEAR_CANVAS_FILE_NAME}; - [ ] / - [x] markers render as ☐/☑.",
    ]
    artifacts = active_session["artifacts"][:5]
    artifact_lines = [
        f"- {artifact['label']}: {artifact['value']}" for artifact in artifacts
    ] or ["- None"]
    recent_messages = active_session.get("recent_messages", [])
    recent_human_participants = list(
        dict.fromkeys(
            message["sender"]
            for message in recent_messages
            if message["sender_is_bot"] is not True
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
            "",
            f"Canvas: durable topic context is in {PEAR_CANVAS_FILE_NAME}; inspect it when this turn depends on saved notes, pinned context, or long-lived constraints.",
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
}


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
        if method not in {"item/started", "item/completed"}:
            return []
        item = params.get("item")
        if not isinstance(item, dict):
            return []
        return self.json_translator.events_from_json_event(
            {
                "type": "item.started" if method == "item/started" else "item.completed",
                "item": pear_app_server_normalized_item(item),
            }
        )


def pear_codex_thread_id_from_active_session(
    active_session: PearActiveSessionSummary,
) -> str | None:
    latest_codex_thread_id = active_session.get("latest_codex_thread_id")
    if isinstance(latest_codex_thread_id, str) and latest_codex_thread_id.strip():
        return latest_codex_thread_id.strip()

    for artifact in active_session["artifacts"]:
        if artifact["kind"] not in {"session_record", "document"}:
            continue
        value = artifact["value"].strip()
        if value.startswith("Codex thread "):
            thread_id = value[len("Codex thread ") :].strip()
            return thread_id or None
    return None


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
        self.translator = PearCodexAppServerTranscriptTranslator()

    def config_args(self) -> list[str]:
        args: list[str] = []
        if self.service_tier:
            args.extend(["--config", f'service_tier="{self.service_tier}"'])
        if self.fast_mode_enabled:
            args.extend(["--config", "features.fast_mode=true"])
        args.extend(["--config", "features.memories=true"])
        if self.reasoning_effort:
            args.extend(
                ["--config", f'model_reasoning_effort="{self.reasoning_effort}"']
            )
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
                "capabilities": {},
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
            if self.handle_server_request(message):
                continue
            if "method" in message:
                self.handle_notification(message, event_callback=event_callback)
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

    def handle_server_request(self, message: dict[str, Any]) -> bool:
        if "id" not in message or "method" not in message:
            return False
        if "result" in message or "error" in message:
            return False
        request_id = message["id"]
        method = str(message.get("method", "unknown"))
        self.send_message(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Pear does not implement app-server request {method}.",
                },
            }
        )
        return True

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
        }
        if existing_thread_id is not None:
            params["threadId"] = existing_thread_id
            params.pop("ephemeral", None)
        else:
            params["developerInstructions"] = build_pear_codex_developer_instructions()
        return {key: value for key, value in params.items() if value is not None}

    def turn_params(self, thread_id: str, prompt: str) -> dict[str, Any]:
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
        return {key: value for key, value in params.items() if value is not None}

    def ensure_thread(
        self,
        active_session: PearActiveSessionSummary,
        *,
        event_callback: PearWorkerTranscriptCallback | None,
    ) -> str:
        existing_thread_id = pear_codex_thread_id_from_active_session(active_session)
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
            if self.handle_server_request(message):
                continue
            if "method" not in message:
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
    ) -> Sequence[PearWorkerToolResult]:
        with self.lock:
            self.ensure_started()
            thread_id = self.ensure_thread(active_session, event_callback=event_callback)
            self.active_thread_id = thread_id
            prompt = build_pear_codex_turn_prompt(active_session, task)
            result = self.request(
                "turn/start",
                self.turn_params(thread_id, prompt),
                event_callback=event_callback,
            )
            turn_id = pear_app_server_turn_id_from_result(result)
            if turn_id is not None:
                self.active_turn_id = turn_id
            final_text, command_count, file_change_count, tool_call_count = self.wait_for_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                event_callback=event_callback,
            )
            results = pear_tool_results_from_text(final_text, output_limit=self.output_limit)
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
                        "value": "Codex app-server completed with "
                        f"{', '.join(summary_parts)}.",
                    },
                )
            results = ensure_pear_runtime_result(results, self.runtime_value())
            return append_pear_codex_thread_artifact(results, thread_id)

    def steer(self, instruction: str) -> bool:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return False
            if self.active_thread_id is None or self.active_turn_id is None:
                return False
            request_id = self.next_id()
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
