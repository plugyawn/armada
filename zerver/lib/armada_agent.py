import contextlib
import hashlib
import json
import logging
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import urlsplit

import requests

from zerver.lib.armada import (
    ARMADA_AGENT_NAME,
    ArmadaActiveSessionSummary,
    ArmadaAmbientAction,
    ArmadaAmbientAddressee,
    ArmadaAmbientDecision,
    ArmadaRecentMessage,
    ArmadaResponsePresentation,
    ArmadaUserInputProvider,
    ArmadaWorkerToolResult,
    ArmadaWorkerTranscriptCallback,
    ArmadaWorkerTranscriptEvent,
    armada_message_id,
    armada_message_stream,
    armada_message_topic,
    armada_normalize_token_usage,
    armada_turn_idle_hook,
    format_armada_referenced_threads,
)
from zerver.lib.armada_artifact import (
    ArmadaArtifactError,
    armada_artifact_node_count,
    artifact_tool_result,
    canonicalize_armada_artifact,
)
from zerver.lib.armada_artifact_contract import ARMADA_ARTIFACT_TOOL_SPEC
from zerver.lib.armada_later_tools import (
    ARMADA_LATER_TOOL_NAMES,
    ARMADA_LATER_TOOL_SPECS,
    answer_armada_later_tool,
)
from zerver.lib.armada_message_tools import (
    ARMADA_MESSAGE_TOOL_NAMES,
    ArmadaToolTurn,
    answer_armada_message_tool,
    armada_message_tool_specs,
)
from zerver.lib.armada_page_tools import (
    ARMADA_PAGE_TOOL_NAMES,
    ARMADA_PAGE_TOOL_SPECS,
    answer_armada_page_tool,
)
from zerver.lib.armada_plugin_agents import (
    ArmadaPluginServer,
    armada_plugin_config_args,
    armada_plugin_environment,
    armada_plugin_fingerprint,
    armada_plugin_instruction_lines,
    armada_plugin_instructions,
)
from zerver.lib.armada_plugin_tools import (
    ARMADA_PLUGIN_TOOL_NAMES,
    answer_armada_plugin_tool,
    armada_plugin_tool_specs,
)
from zerver.lib.armada_typesafe import (
    armada_typesafe_agent_probability,
    build_armada_typesafe_addressing_request,
)
from zerver.lib.outgoing_http import OutgoingSession

logger = logging.getLogger(__name__)

ArmadaAgentRuntime = Literal["disabled", "codex-app-server"]
ArmadaSubprocessPopen = Callable[..., subprocess.Popen[str]]
ArmadaUrlOpen = Callable[..., Any]
ARMADA_GEMINI_INTERACTIONS_API_REVISION = "2026-05-20"

# The agent's browser.  Codex's own browser comes with the ChatGPT desktop
# app and does not exist on a headless server; for the CLI, Codex's manual
# names an MCP server, and Playwright's ships in the `playwright` package
# (`playwright mcp`).  The server keeps Playwright's name, so its tools reach
# the model as the names it knows (playwright__browser_navigate, ...).
ARMADA_BROWSER_MCP_SERVER: Final = "playwright"
# A desktop Chrome's user agent, of the version of the Chromium that
# puppet's pinned Playwright installs (agent_runtime.pp): the default one
# says HeadlessChrome, which sites block.
ARMADA_BROWSER_USER_AGENT: Final = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class ArmadaProcessIsolation:
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


class ArmadaAgentRuntimeError(RuntimeError):
    pass


class ArmadaRunNoticeError(ArmadaAgentRuntimeError):
    """A turn that Codex ended with an error: the run fails, and its place
    in the conversation says run_notice, in plain words, rather than
    "<agent> failed." and the error's raw text."""

    def __init__(self, run_notice: str) -> None:
        super().__init__(run_notice)
        self.run_notice = run_notice


# "You've hit your usage limit. Try again at Oct 23rd, 2026 2:16 PM."
ARMADA_CODEX_RETRY_AT_RE = re.compile(
    r"Try again at ([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th)?, (\d{4}) (\d{1,2}):(\d{2}) ([AP]M)"
)


def armada_codex_error_notice(words: str, codex_error_info: str | None) -> str:
    """What a run's place says when Codex ended its turn with an error:
    for a spent usage limit, until when, as a global time that each reader
    sees in their own time zone; for any other error, its own words,
    briefly."""
    if codex_error_info == "usageLimitExceeded":
        until = "until it resets"
        retry_at = ARMADA_CODEX_RETRY_AT_RE.search(words)
        if retry_at is not None:
            month, day, year, hour, minute, half = retry_at.groups()
            # Codex writes the time in UTC.
            when = datetime.strptime(
                f"{month} {day} {year} {hour}:{minute} {half}", "%b %d %Y %I:%M %p"
            ).replace(tzinfo=timezone.utc)
            until = f"until <time:{when:%Y-%m-%dT%H:%M:%SZ}>"
        return (
            f"{ARMADA_AGENT_NAME} can't work right now: the Codex account has reached its "
            f"usage limit, {until}."
        )
    words = trim_armada_agent_text(" ".join(words.split()), limit=300)
    if not words:
        return f"{ARMADA_AGENT_NAME} couldn't finish: Codex ended the turn with an error."
    return f"{ARMADA_AGENT_NAME} couldn't finish: {words}"


def armada_capture_protocol_message(direction: str, message: dict[str, Any]) -> None:
    """Append one wire message to the capture file when
    ARMADA_CODEX_PROTOCOL_CAPTURE names a path. Diagnostic-only: the
    surface-diff workflow uses it to see exactly what an app-server
    version emits and asks; never allowed to break a turn."""
    capture_path = os.environ.get("ARMADA_CODEX_PROTOCOL_CAPTURE")
    if not capture_path:
        return
    try:
        with open(capture_path, "a") as capture_file:
            capture_file.write(json.dumps({"direction": direction, "message": message}) + "\n")
    except OSError:
        pass


def armada_isolated_command(
    command: Sequence[str],
    process_isolation: ArmadaProcessIsolation | None,
) -> list[str]:
    if process_isolation is None:
        return list(command)
    return [*process_isolation.command_prefix(), *command]


# The platform packages of Codex's npm distribution, as its launcher
# (bin/codex.js) names them.
ARMADA_CODEX_NPM_PLATFORM_PACKAGES: Final = {
    ("Linux", "x86_64"): ("@openai/codex-linux-x64", "x86_64-unknown-linux-musl"),
    ("Linux", "aarch64"): ("@openai/codex-linux-arm64", "aarch64-unknown-linux-musl"),
    ("Darwin", "x86_64"): ("@openai/codex-darwin-x64", "x86_64-apple-darwin"),
    ("Darwin", "arm64"): ("@openai/codex-darwin-arm64", "aarch64-apple-darwin"),
}


@lru_cache(maxsize=8)
def armada_codex_executable(codex_bin: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """The program to start for codex_bin, and the environment it needs.

    Codex's npm package installs a node launcher, bin/codex.js, which
    finds the platform's native binary, sets two variables and spawns
    it, then stays resident for the binary's whole life only to forward
    signals: about 50 MB per app-server. Run the native binary directly,
    with the same two variables; it finds its bundled bwrap and rg next
    to itself, as under the launcher. Anything else (a native binary, a
    Homebrew cask, a layout this does not know) runs as given. Resolved
    once per process.
    """
    resolved = shutil.which(codex_bin)
    if resolved is None:
        return codex_bin, ()
    launcher = Path(os.path.realpath(resolved))
    if launcher.name != "codex.js" or launcher.parent.name != "bin":
        return codex_bin, ()
    package_root = launcher.parent.parent
    platform_package = ARMADA_CODEX_NPM_PLATFORM_PACKAGES.get(
        (platform.system(), platform.machine())
    )
    if platform_package is None:
        return codex_bin, ()
    package_name, target_triple = platform_package
    # Where the launcher's require.resolve finds the platform package:
    # the package's own node_modules, then each enclosing node_modules,
    # then a vendor directory in the package itself.
    vendor_roots = [package_root / "node_modules" / package_name / "vendor"]
    vendor_roots.extend(
        ancestor / package_name / "vendor"
        for ancestor in package_root.parents
        if ancestor.name == "node_modules"
    )
    vendor_roots.append(package_root / "vendor")
    for vendor_root in vendor_roots:
        native_binary = vendor_root / target_triple / "bin" / "codex"
        if native_binary.is_file() and os.access(native_binary, os.X_OK):
            return str(native_binary), (
                ("CODEX_MANAGED_PACKAGE_ROOT", str(package_root)),
                ("CODEX_MANAGED_BY_NPM", "1"),
            )
    return codex_bin, ()


def armada_subprocess_cwd(
    workdir: str | None,
    process_isolation: ArmadaProcessIsolation | None,
) -> str | None:
    if process_isolation is None:
        return workdir
    return "/"


def trim_armada_agent_text(value: str, *, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 15, 0)].rstrip()}... [truncated]"


# What an addressing router is shown: the topic's newest messages before
# the one it classifies, each cut short, and that message, cut longer.
ARMADA_ADDRESSING_CONTEXT_MESSAGES: Final = 6
ARMADA_ADDRESSING_CONTEXT_MESSAGE_CHARACTERS: Final = 240
ARMADA_ADDRESSING_NEW_MESSAGE_CHARACTERS: Final = 1000


def armada_addressing_context_messages(
    active_session: ArmadaActiveSessionSummary | None,
    *,
    message_id: int | None,
) -> list[ArmadaRecentMessage]:
    if active_session is None:
        return []
    messages = [
        message
        for message in active_session.get("routing_messages", active_session["recent_messages"])
        if message_id is None or message["message_id"] != message_id
    ][-ARMADA_ADDRESSING_CONTEXT_MESSAGES:]
    return [
        {
            "message_id": message["message_id"],
            "sender": message["sender"],
            "sender_is_bot": message["sender_is_bot"],
            "content": trim_armada_agent_text(
                message["content"], limit=ARMADA_ADDRESSING_CONTEXT_MESSAGE_CHARACTERS
            ),
        }
        for message in messages
    ]


def build_armada_addressing_router_prompt(
    *,
    content: str,
    active_session: ArmadaActiveSessionSummary | None,
    active_run: bool = False,
    stream: str = "",
    topic: str = "",
    message_id: int | None = None,
) -> str:
    if active_session is None:
        session_lines = [
            "- active_session: none",
            "- worker_state: unknown",
            f"- active_run: {'yes' if active_run else 'no'}",
            "- response_policy: ambient",
        ]
    else:
        session_lines = [
            f"- active_session: {active_session['state']}",
            f"- worker_state: {active_session['worker_state']}",
            f"- active_run: {'yes' if active_run else 'no'}",
            f"- worker_can_run: {active_session['worker_can_run']}",
            f"- response_policy: {active_session['response_policy']}",
            f"- request_count: {active_session['request_count']}",
            f"- latest_request_message_id: {active_session['latest_request_message_id'] or 'None'}",
        ]

    recent_lines = [
        f"- {message['sender']}{' [bot]' if message['sender_is_bot'] else ''}: {message['content']}"
        for message in armada_addressing_context_messages(active_session, message_id=message_id)
    ]
    if not recent_lines:
        recent_lines = ["- none"]

    return "\n".join(
        [
            f"You are {ARMADA_AGENT_NAME}'s low-latency routing layer for a coding agent in Armada, a team chat.",
            "Classify one new untagged message. Do not answer it.",
            "Do not answer the user. Do not run commands or use tools.",
            "Return only the JSON object required by the response schema.",
            "",
            f"addressee: agent only when the request is for {ARMADA_AGENT_NAME}, Codex, the agent, or the assistant; other for a named/recent human; general for team chatter; uncertain otherwise.",
            f"action: ignore for chatter, ambiguity, acknowledgements, and messages for humans; when active_run=yes, steer if this changes or continues {ARMADA_AGENT_NAME}'s current work; when active_run=no, run for a concrete request or a correction/follow-up to {ARMADA_AGENT_NAME}'s latest reply.",
            "presentation: choose the likely final UI shape: compact, prose, status, explainer, or diff.",
            "Treat a repeated concrete request as intentional. Exact transport duplicates are handled outside this model.",
            "When uncertain, use addressee=uncertain and action=ignore.",
            "",
            f"Channel: {stream or 'unknown'}",
            f"Topic: {topic or 'unknown'}",
            "Session:",
            *session_lines,
            "",
            "Recent topic messages (oldest to newest):",
            *recent_lines,
            "",
            "New message:",
            trim_armada_agent_text(content, limit=ARMADA_ADDRESSING_NEW_MESSAGE_CHARACTERS),
        ]
    )


def parse_armada_addressing_json_output(output: str) -> ArmadaAmbientDecision:
    ignored: ArmadaAmbientDecision = {
        "addressee": "uncertain",
        "action": "ignore",
        "presentation": "compact",
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
    if set(parsed) != {"addressee", "action", "presentation"}:
        return ignored
    addressee = parsed.get("addressee")
    action = parsed.get("action")
    presentation = parsed.get("presentation")
    if (
        addressee not in {"agent", "other", "general", "uncertain"}
        or action not in {"ignore", "steer", "run"}
        or presentation not in {"compact", "prose", "status", "explainer", "diff"}
    ):
        return ignored
    return {
        "addressee": cast(ArmadaAmbientAddressee, addressee),
        "action": cast(ArmadaAmbientAction, action),
        "presentation": cast(ArmadaResponsePresentation, presentation),
    }


def armada_gemini_interaction_output_text(payload: dict[str, Any]) -> str:
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
        text_parts.extend(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return "".join(text_parts)


class ArmadaGeminiInteractionsClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: int,
        output_limit: int,
        url: str,
        urlopen: ArmadaUrlOpen | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.url = url
        self.urlopen = urlopen or urllib.request.urlopen

    def interact(
        self,
        *,
        model: str,
        input_text: str,
        system_instruction: str,
        response_schema: dict[str, Any],
        thinking_level: str | None,
    ) -> str:
        if not self.api_key:
            raise ArmadaAgentRuntimeError("Gemini Interactions API key is not configured.")
        payload: dict[str, Any] = {
            "model": model,
            "store": False,
            "input": input_text,
            "system_instruction": system_instruction,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema,
            },
            "generation_config": {
                "temperature": 0,
                "max_output_tokens": self.output_limit,
            },
        }
        if thinking_level:
            payload["generation_config"]["thinking_level"] = thinking_level.strip()

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
                "Api-Revision": ARMADA_GEMINI_INTERACTIONS_API_REVISION,
            },
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read().decode()
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            raw_error = error.read().decode(errors="replace")
            raise ArmadaAgentRuntimeError(
                "Gemini Interactions API failed with HTTP "
                f"{error.code}: {trim_armada_agent_text(raw_error, limit=self.output_limit)}"
            ) from error
        except (TimeoutError, urllib.error.URLError) as error:
            raise ArmadaAgentRuntimeError(f"Gemini Interactions API failed: {error}") from error

        if status != 200:
            raise ArmadaAgentRuntimeError(
                "Gemini Interactions API failed with HTTP "
                f"{status}: {trim_armada_agent_text(raw_response, limit=self.output_limit)}"
            )
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as error:
            raise ArmadaAgentRuntimeError(
                "Gemini Interactions API returned invalid JSON."
            ) from error
        if not isinstance(parsed, dict):
            return ""
        return armada_gemini_interaction_output_text(parsed)


class ArmadaGeminiInteractionsAddressingIntentClassifier:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: int,
        output_limit: int,
        url: str,
        thinking_level: str | None = "minimal",
        urlopen: ArmadaUrlOpen | None = None,
    ) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self.client = ArmadaGeminiInteractionsClient(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            url=url,
            urlopen=urlopen,
        )

    def __call__(
        self,
        *,
        content: str,
        active_session: ArmadaActiveSessionSummary | None,
        active_run: bool,
        message: dict[str, Any],
        bot_handler: Any,
    ) -> ArmadaAmbientDecision:
        stream = str(message.get("display_recipient") or "")
        topic = str(message.get("topic") or message.get("subject") or "")
        prompt = build_armada_addressing_router_prompt(
            content=content,
            active_session=active_session,
            active_run=active_run,
            stream=stream,
            topic=topic,
            message_id=message.get("id") if isinstance(message.get("id"), int) else None,
        )
        output = self.client.interact(
            model=self.model,
            input_text=prompt,
            system_instruction="Return only the structured JSON object required by the response schema.",
            response_schema={
                "type": "object",
                "properties": {
                    "addressee": {
                        "type": "string",
                        "enum": ["agent", "other", "general", "uncertain"],
                    },
                    "action": {"type": "string", "enum": ["ignore", "steer", "run"]},
                    "presentation": {
                        "type": "string",
                        "enum": ["compact", "prose", "status", "explainer", "diff"],
                    },
                },
                "required": ["addressee", "action", "presentation"],
                "additionalProperties": False,
            },
            thinking_level=self.thinking_level,
        )
        return parse_armada_addressing_json_output(output)


class ArmadaTypeSafeAddressingIntentClassifier:
    """Asks TypeSafe's Jev whether an untagged message is for the agent
    (zerver/lib/armada_typesafe.py says what it is asked), and has the
    agent act only when Jev gives it at least `threshold`.  A failure of
    any kind raises, so that the handler logs it and starts nothing."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        url: str,
        threshold: float,
        timeout_seconds: float,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model
        self.url = url
        self.threshold = threshold
        self.session = OutgoingSession(role="armada_typesafe", timeout=timeout_seconds)

    def __call__(
        self,
        *,
        content: str,
        active_session: ArmadaActiveSessionSummary | None,
        active_run: bool,
        message: dict[str, Any],
        bot_handler: Any,
    ) -> ArmadaAmbientDecision:
        if not self.api_key:
            raise ArmadaAgentRuntimeError(
                "TypeSafe has no API key: set armada_typesafe_api_key in zulip-secrets.conf."
            )
        message_id = armada_message_id(message)
        request = build_armada_typesafe_addressing_request(
            agent_name=ARMADA_AGENT_NAME,
            model=self.model,
            channel=armada_message_stream(message),
            topic=armada_message_topic(message),
            agent_run_in_progress=active_run,
            recent_messages=armada_addressing_context_messages(
                active_session, message_id=message_id or None
            ),
            new_message_sender=str(message.get("sender_full_name") or ""),
            new_message=trim_armada_agent_text(
                content, limit=ARMADA_ADDRESSING_NEW_MESSAGE_CHARACTERS
            ),
        )
        try:
            response = self.session.post(
                self.url, json=request, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        except requests.RequestException as error:
            raise ArmadaAgentRuntimeError(f"TypeSafe did not answer: {error}") from error
        if response.status_code != 200:
            raise ArmadaAgentRuntimeError(
                f"TypeSafe answered HTTP {response.status_code}: "
                f"{trim_armada_agent_text(response.text, limit=500)}"
            )
        try:
            answer = response.json()
            agent_probability = armada_typesafe_agent_probability(answer)
        except ValueError as error:
            raise ArmadaAgentRuntimeError(f"TypeSafe's answer is not usable: {error}") from error
        logger.info(
            "TypeSafe addressing: message_id=%s p_agent=%.2f threshold=%.2f model=%s",
            message_id,
            agent_probability,
            self.threshold,
            answer.get("model"),
        )
        if agent_probability >= self.threshold:
            return {
                "addressee": "agent",
                "action": "steer" if active_run else "run",
                "presentation": "compact",
            }
        return {
            "addressee": "uncertain" if agent_probability >= 0.5 else "other",
            "action": "ignore",
            "presentation": "compact",
        }


def armada_codex_dynamic_tools() -> list[dict[str, Any]]:
    """The tools Armada offers a new Codex thread (answered in
    answer_for_server_request)."""
    return [
        ARMADA_ARTIFACT_TOOL_SPEC,
        *ARMADA_PAGE_TOOL_SPECS,
        *armada_message_tool_specs(),
        *ARMADA_LATER_TOOL_SPECS,
        *armada_plugin_tool_specs(),
    ]


def armada_codex_thread_version(plugin_servers: Sequence[ArmadaPluginServer] = ()) -> str:
    """A fingerprint of what a new Codex thread is given and a resume
    cannot change: the tools it is offered (thread/resume takes none, and
    a resumed thread keeps its old ones), the instructions' version, and
    the team's plugins' lines of them. A topic resumes its thread only
    while this is the version recorded on it, and otherwise starts a
    fresh one: so a plugin with tools installed or removed reaches every
    topic's next thread."""
    given: dict[str, object] = {
        "tools": armada_codex_dynamic_tools(),
        "instructions": ARMADA_CODEX_INSTRUCTIONS_VERSION,
    }
    if plugin_servers:
        given["plugins"] = armada_plugin_instruction_lines(plugin_servers)
    encoded = json.dumps(given, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


# Bump when the developer instructions change in a way existing topics
# need: each topic's next run then starts a fresh Codex thread, which
# gets them (and loses the old thread's memory). Small rewordings that
# can wait for a topic's next fresh thread need no bump.
ARMADA_CODEX_INSTRUCTIONS_VERSION: Final = 7

# Where an alert from armada-ops-check (puppet/armada/files/) sends the
# agent: the runbook is Armada's, not in the #ops channel's repository.
ARMADA_OPS_RUNBOOK_URL: Final = (
    "https://github.com/plugyawn/armada/blob/ui-overhaul/docs/armada/operations.md"
)


# What the agent is told about its browser (browser_config_args), when it
# has one.  The rule on Mac-mini agents is the user's (16 October): the
# headless browser is the default, and a mini only where it fails.
ARMADA_CODEX_BROWSER_INSTRUCTIONS: Final = (
    "You have a browser: a headless Chromium on the server, which you drive with your "
    "playwright tools (browser_navigate, browser_snapshot, browser_click, browser_type, "
    "browser_take_screenshot and the rest). Use it to open pages, to check web apps, "
    "a server you start in your worktree included, and to show people what you see.\n"
    "Your headless browser is the default for every browsing task. Only when it fails "
    "(a site that blocks headless browsers, or a task that needs a real desktop or a login "
    "in a real browser) do you hand the task to a Mac-mini agent: mention it with the task, "
    "and say why the headless browser could not do it.\n"
    "Before a screenshot of a page that is still loading, wait for what you expect to see "
    "(browser_wait_for). Take a screenshot with browser_take_screenshot and no filename: you "
    "see it, and its file is kept outside the worktree at the path the tool reports. Pass "
    "fullPage: true when asked for the whole page.\n"
    "When you show people what you see, send them the screenshot: armada_post with files "
    "set to the path the tool reported, and words saying what it shows.\n"
)


def build_armada_codex_developer_instructions(
    *, browser: bool = False, plugin_servers: Sequence[ArmadaPluginServer] = ()
) -> str:
    """The instructions a new Codex thread starts with; browser says
    whether the agent has its browser (ARMADA_WORKER_BROWSER_DIR), and
    plugin_servers are the team's plugins that offer it tools."""
    agent = f"You are {ARMADA_AGENT_NAME}, a Codex-powered pair-programming agent working from a topic in Armada, the team chat you work in.\nUse the repository and tools available in this runtime to make concrete progress.\nTreat the runtime cwd as the isolated topic worktree; the Armada server checkout is not the user's target repo unless the topic context explicitly pins it.\nREADME.md and other workspace files are ordinary project content; inspect or edit them only when the task calls for it, never as hidden Armada state.\nUse Codex-native skills from CODEX_HOME/skills when a relevant SKILL.md applies. When someone asks for a widget or a plugin, or a change to one, follow the armada-plugin skill.\nBefore your first tool call on any task of more than two steps, make a checklist with your plan tool, and keep it current: mark each step in progress as you start it and completed as you finish it. The checklist renders live for the people in the topic.\nWhen a question blocks the work (a decision, or a choice between options), ask it with your request_user_input tool, not in text: it is posted in the topic, its options become buttons, and the reply comes back to you as the answer.\nTo tell a person something, mention them as @**Full Name**, with their full name exactly as the transcript shows it; that notifies them.\nSeveral people can talk to you; each message is signed with its author's name. Follow the latest instruction. If two people's instructions conflict, say so in one line, mention both, and continue with the latest until one of them answers.\nEveryone in the organization has a personal page of posts. Read pages with armada_page_read when they help. Draft a post with armada_page_draft only when the person asks for one: it lands as a draft on their own page, which only they see until they publish it, and you say in your reply that you drafted it.\nWhen someone asks for a new channel, create it with armada_create_channel, which acts with their rights, and give them its #**name** link.\nAn alert the Ops bot posts in #ops names its section of Armada's operations runbook, which is not in the channel's repository: read it at {ARMADA_OPS_RUNBOOK_URL}.\n"
    browser_lines = ARMADA_CODEX_BROWSER_INSTRUCTIONS if browser else ""
    answer = "Keep changes focused on the user's request and verify them before finishing.\nFor casual, conversational, choice-only, or answer-only requests, respond directly.\nDo not run repository/session checks just to make a casual reply sound grounded.\nUse tools only when the request needs workspace facts, code changes, external lookup, or verification.\nResolve references like 'above', 'that', 'what did I ask', and 'who is here' from the recent topic transcript below.\nDo not claim you cannot see recent topic history when the transcript below contains the answer.\nFinal response requirements:\n- Keep the response concise and suitable for posting back into the topic.\n- Report evidence in ordinary prose; Armada never parses your wording into hidden state.\n- Use registered native tools for structured UI artifacts when they materially help."
    return agent + browser_lines + armada_plugin_instructions(plugin_servers) + answer


def build_armada_codex_turn_prompt(active_session: ArmadaActiveSessionSummary, task: str) -> str:
    task_text = task.strip() or "Continue the topic Codex thread."
    recent_messages = active_session.get("recent_messages", [])
    recent_transcript_lines = [
        (
            f"- #{message['message_id']} {message['sender']} "
            f"({'bot' if message['sender_is_bot'] else 'human'}): {message['content']}"
        )
        for message in recent_messages[-30:]
    ]
    referenced_context_lines = format_armada_referenced_threads(
        active_session.get("referenced_threads", [])
    )
    # The request's id, in the transcript's #id form, so the agent can
    # point at it: reply in its thread, or link it.
    request_message_id = active_session.get("current_trigger_message_id")
    if active_session.get("latest_codex_thread_id"):
        request_lines = (
            [f"Current request (#{request_message_id}):", task_text]
            if request_message_id is not None
            else ["Current request:", task_text]
        )
        if not recent_transcript_lines:
            if request_message_id is None:
                return "\n".join([task_text, *referenced_context_lines])
            return "\n".join([*request_lines, *referenced_context_lines])
        return "\n".join(
            [
                "New messages since your previous turn:",
                *recent_transcript_lines,
                *referenced_context_lines,
                "",
                *request_lines,
            ]
        )

    workspace_context = active_session.get("workspace_context", {})
    workspace_lines = [
        f"- repository: {workspace_context.get('repository') or 'unknown'}",
        f"- remote: {workspace_context.get('remote_url') or 'unknown'}",
        f"- worktree: {workspace_context.get('worktree') or 'unknown'}",
        f"- workdir: {workspace_context.get('workdir') or 'unknown'}",
        f"- branch: {workspace_context.get('branch') or 'unknown'}",
        f"- head: {workspace_context.get('head') or 'unknown'}",
        "- contract: the channel maps to the repository, and the topic/subthread maps to the Git branch.",
        "- privacy: refer to the workspace by branch or worktree name in replies; never print the absolute workdir path in the topic.",
        "- servers and watchers: never start a long-lived process (http.server, watch loops, dev servers) in the foreground — it blocks your turn from ever finishing. Run it detached (nohup ... >/dev/null 2>&1 &) and verify with a follow-up probe.",
        "- math: write math in Armada's dialect — $$...$$ for inline math and ```math fenced blocks for display equations; \\[ \\] and \\( \\) do not render here.",
        "- checklists: your plan tool is the working checklist — its steps render live in the topic rail, so keep it current instead of duplicating it in chat or a workspace document.",
    ]
    recent_human_participants = list(
        dict.fromkeys(
            message["sender"] for message in recent_messages if message["sender_is_bot"] is not True
        )
    )
    recent_participant_lines = [
        f"- {participant}" for participant in recent_human_participants
    ] or ["- None visible in the provided transcript"]
    initial_transcript_lines = recent_transcript_lines or [
        "- No recent topic transcript was provided."
    ]
    return "\n".join(
        [
            f"Task: {task_text}",
            f"Channel: {active_session['stream']}",
            f"Topic: {active_session['topic']}",
            *(
                [f"Request message: #{request_message_id}"]
                if request_message_id is not None
                else []
            ),
            f"Session state: {active_session['state']}",
            "",
            "Workspace context:",
            *workspace_lines,
            "",
            "Visible recent human participants:",
            *recent_participant_lines,
            "",
            "Recent topic transcript, oldest to newest:",
            *initial_transcript_lines,
            *referenced_context_lines,
        ]
    )


def armada_worker_results_from_final_text(
    text: str,
    *,
    output_limit: int,
) -> list[ArmadaWorkerToolResult]:
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
            "value": trim_armada_agent_text(text, limit=output_limit),
        }
    ]


def build_armada_runtime_value(
    runtime: ArmadaAgentRuntime,
    *,
    workdir: str | None = None,
    sandbox: str | None = None,
    approval_policy: str | None = None,
    model: str | None = None,
    service_tier: str | None = None,
    reasoning_effort: str | None = None,
    process_isolation: ArmadaProcessIsolation | None = None,
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


def armada_codex_item_text(item: dict[str, Any]) -> str:
    for key in ("text", "message", "summary", "output", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def armada_codex_item_title(item: dict[str, Any]) -> str:
    for key in ("command", "name", "tool_name", "path", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return armada_codex_item_text(item)


# "**Exploring dynamic programming options**\n\nI'm looking into …": how
# the model opens each part of a reasoning summary.
ARMADA_REASONING_TITLE_RE = re.compile(r"\s*\*\*([^*\n]+)\*\*")


def armada_reasoning_title(summary_part: str) -> str | None:
    """The bold title a part of a reasoning summary opens with, if it has
    one: what the model is thinking about, in its own words."""
    title = ARMADA_REASONING_TITLE_RE.match(summary_part)
    if title is None:
        return None
    return " ".join(title.group(1).split()) or None


def armada_reasoning_item_title(item: dict[str, Any]) -> str | None:
    """The title of a reasoning item's newest summary part that has one."""
    summary = item.get("summary")
    if not isinstance(summary, list):
        return None
    for part in reversed(summary):
        if isinstance(part, str) and (title := armada_reasoning_title(part)):
            return title
    return None


def armada_codex_plan_steps(item: dict[str, Any]) -> list[dict[str, str]]:
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


def armada_codex_item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("id", "type", "status", "command", "name", "tool_name", "path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = trim_armada_agent_text(value, limit=500)
    if item.get("type") == "collab_agent_tool_call":
        for source_key, target_key in (
            ("senderThreadId", "sender_thread_id"),
            ("prompt", "prompt"),
            ("model", "model"),
            ("reasoningEffort", "reasoning_effort"),
        ):
            value = item.get(source_key)
            if isinstance(value, str) and value.strip():
                metadata[target_key] = trim_armada_agent_text(value, limit=1000)
        receiver_thread_ids = item.get("receiverThreadIds")
        if isinstance(receiver_thread_ids, list):
            metadata["receiver_thread_ids"] = [
                thread_id
                for thread_id in receiver_thread_ids[:16]
                if isinstance(thread_id, str) and thread_id.strip()
            ]
        agent_states = item.get("agentsStates")
        if isinstance(agent_states, dict):
            metadata["agent_states"] = {
                str(thread_id): {
                    "status": str(state.get("status") or "unknown"),
                    "message": trim_armada_agent_text(str(state.get("message") or ""), limit=500),
                }
                for thread_id, state in list(agent_states.items())[:16]
                if isinstance(thread_id, str) and isinstance(state, dict)
            }
    elif item.get("type") == "sub_agent_activity":
        for source_key, target_key in (
            ("kind", "activity_kind"),
            ("agentThreadId", "agent_thread_id"),
            ("agentPath", "agent_path"),
        ):
            value = item.get(source_key)
            if isinstance(value, str) and value.strip():
                metadata[target_key] = trim_armada_agent_text(value, limit=500)
    return metadata


def armada_codex_event_delta_text(event: dict[str, Any]) -> str:
    for key in ("delta", "text_delta", "content_delta"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            text = armada_codex_item_text(value)
            if text:
                return text

    item = event.get("item")
    if isinstance(item, dict):
        for key in ("delta", "text_delta", "content_delta"):
            value = item.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                text = armada_codex_item_text(value)
                if text:
                    return text

    data = event.get("data")
    if isinstance(data, dict):
        return armada_codex_event_delta_text(data)
    return ""


def armada_codex_event_item_id(event: dict[str, Any]) -> str:
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


def armada_trace_text(value: object, *, limit: int = 60) -> str:
    """A value quoted in a line of a run's trace, on one line and clipped."""
    text = " ".join(str(value).split()) if value is not None else ""
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def armada_trace_url(url: object) -> str:
    """A URL as a line of a run's trace names it: host and path, without
    the scheme, query or fragment."""
    if not isinstance(url, str) or not url.strip():
        return "a page"
    parts = urlsplit(url.strip())
    if parts.scheme in {"http", "https"} and parts.netloc:
        return armada_trace_text(parts.netloc + parts.path.rstrip("/"), limit=80)
    return armada_trace_text(url, limit=80)


# The browser's tools whose line in the trace needs nothing from their
# arguments (Playwright 1.62.1's MCP server, with the vision tools).
ARMADA_BROWSER_ACTION_TITLES: Final = {
    "browser_navigate_back": "Went back",
    "browser_snapshot": "Read the page",
    "browser_fill_form": "Filled in a form",
    "browser_file_upload": "Uploaded files",
    "browser_drop": "Dropped files on the page",
    "browser_evaluate": "Ran code in the page",
    "browser_run_code_unsafe": "Ran code in the browser",
    "browser_console_messages": "Read the console",
    "browser_network_requests": "Read the network requests",
    "browser_network_request": "Read a network request",
    "browser_resize": "Resized the window",
    "browser_close": "Closed the browser",
    "browser_mouse_move_xy": "Moved the pointer",
    "browser_mouse_drag_xy": "Dragged the pointer",
    "browser_mouse_down": "Pressed the mouse button",
    "browser_mouse_up": "Released the mouse button",
    "browser_mouse_wheel": "Scrolled",
}


def armada_browser_action_title(tool: str, arguments: dict[str, Any]) -> str:
    """A browser action as people read it in a run's trace ("Opened
    example.com", "Took a screenshot") rather than the tool's name.  What
    the agent typed or filled in is not repeated."""
    element = armada_trace_text(arguments.get("element") or "an element")
    if tool == "browser_navigate":
        return f"Opened {armada_trace_url(arguments.get('url'))}"
    if tool == "browser_take_screenshot":
        if arguments.get("element"):
            return f"Took a screenshot of {element}"
        return "Took a full-page screenshot" if arguments.get("fullPage") else "Took a screenshot"
    if tool == "browser_click":
        return f"{'Double-clicked' if arguments.get('doubleClick') else 'Clicked'} {element}"
    if tool == "browser_mouse_click_xy":
        return f"Clicked at {arguments.get('x')}, {arguments.get('y')}"
    if tool == "browser_hover":
        return f"Pointed at {element}"
    if tool == "browser_type":
        return f"Typed into {element}"
    if tool == "browser_select_option":
        return f"Chose an option in {element}"
    if tool == "browser_press_key":
        return f"Pressed {armada_trace_text(arguments.get('key'))}"
    if tool == "browser_drag":
        start = armada_trace_text(arguments.get("startElement") or "an element")
        end = armada_trace_text(arguments.get("endElement") or "another")
        return f"Dragged {start} to {end}"
    if tool == "browser_find":
        return f"Searched the page for “{armada_trace_text(arguments.get('text') or arguments.get('regex'))}”"
    if tool == "browser_wait_for":
        if arguments.get("text"):
            return f"Waited for “{armada_trace_text(arguments['text'])}”"
        if arguments.get("textGone"):
            return f"Waited for “{armada_trace_text(arguments['textGone'])}” to go"
        return "Waited for the page"
    if tool == "browser_tabs":
        return {
            "new": "Opened a tab",
            "close": "Closed a tab",
            "select": "Switched tabs",
        }.get(str(arguments.get("action")), "Listed the tabs")
    if tool == "browser_handle_dialog":
        return "Accepted a dialog" if arguments.get("accept") else "Dismissed a dialog"
    return ARMADA_BROWSER_ACTION_TITLES.get(
        tool, f"Browser: {tool.removeprefix('browser_').replace('_', ' ')}"
    )


def armada_web_search_title(item: dict[str, Any]) -> str:
    """Codex's own web tool (web.run), which searches and reads pages as
    text: its action is known only once the item completes."""
    action = item.get("action")
    if not isinstance(action, dict):
        return "Searched the web"
    if action.get("type") == "search":
        query = action.get("query") or item.get("query")
        return f"Searched the web for “{armada_trace_text(query)}”" if query else "Searched the web"
    if action.get("type") == "openPage":
        return f"Read {armada_trace_url(action.get('url'))}"
    if action.get("type") == "findInPage":
        pattern = armada_trace_text(action.get("pattern"))
        return f"Searched {armada_trace_url(action.get('url'))} for “{pattern}”"
    return "Searched the web"


def armada_transcript_events_from_codex_json_event(
    event: dict[str, Any],
) -> list[ArmadaWorkerTranscriptEvent]:
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
    title = trim_armada_agent_text(armada_codex_item_title(item), limit=1000)
    raw_body = armada_codex_item_text(item)
    body = (
        raw_body
        if item_type == "agent_message" and status == "done"
        else trim_armada_agent_text(raw_body, limit=4000)
    )
    base_event: ArmadaWorkerTranscriptEvent = {
        "status": status,
        "title": title,
        "body": body,
        "metadata": armada_codex_item_metadata(item),
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
        # Only the summary's title is kept, never its text.
        reasoning_title = armada_reasoning_item_title(item)
        if reasoning_title is None:
            return [{**base_event, "kind": "reasoning", "title": title or "Reasoning"}]
        return [
            {
                **base_event,
                "kind": "reasoning",
                "title": reasoning_title,
                "overview": reasoning_title,
            }
        ]
    if item_type == "command_execution":
        return [{**base_event, "kind": "command", "title": title or "command"}]
    if item_type == "file_change":
        return [{**base_event, "kind": "file_change", "title": title or "file change"}]
    if item_type == "plan_update":
        steps = armada_codex_plan_steps(item)
        return [
            {
                **base_event,
                "kind": "plan",
                "title": "Plan",
                "metadata": {**armada_codex_item_metadata(item), "steps": steps},
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
                    **armada_codex_item_metadata(item),
                    "operation": "compaction",
                    "phase": phase,
                },
            }
        ]
    if item_type == "user_message":
        # The turn prompt echoed back as an item; the topic already shows it.
        return []
    if item_type == "collab_agent_tool_call":
        tool = str(item.get("tool") or "agent")
        title = {
            "spawnAgent": "Started an agent",
            "sendInput": "Sent input to an agent",
            "resumeAgent": "Resumed an agent",
            "wait": "Waited for agents",
            "closeAgent": "Closed an agent",
        }.get(tool, "Agent activity")
        return [{**base_event, "kind": "agent", "title": title}]
    if item_type == "sub_agent_activity":
        activity_kind = str(item.get("kind") or "activity")
        return [
            {
                **base_event,
                "kind": "agent",
                "title": f"Agent {activity_kind}",
            }
        ]
    plugin = item.get("plugin")
    if item_type == "mcp_tool_call" and isinstance(plugin, dict):
        # A tool of one of the team's plugins, named with the plugin.
        return [
            {
                **base_event,
                "kind": "tool",
                "title": f"{plugin['name']}: {item.get('tool') or 'tool'}",
                "metadata": {**armada_codex_item_metadata(item), "plugin": plugin},
            }
        ]
    if item_type == "mcp_tool_call" and item.get("server") == ARMADA_BROWSER_MCP_SERVER:
        raw_arguments = item.get("arguments")
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        tool = str(item.get("tool") or "")
        browser_event: ArmadaWorkerTranscriptEvent = {
            **base_event,
            "kind": "tool",
            "title": armada_browser_action_title(tool, arguments),
        }
        if tool == "browser_navigate" and isinstance(arguments.get("url"), str):
            browser_event["body"] = trim_armada_agent_text(arguments["url"], limit=1000)
        return [browser_event]
    if item_type == "web_search":
        return [{**base_event, "kind": "tool", "title": armada_web_search_title(item)}]
    if item_type == "image_view":
        # The model looking at an image file with Codex's view_image: named
        # by the file alone, never the absolute path.
        path = item.get("path")
        name = Path(path).name if isinstance(path, str) and path.strip() else "an image"
        return [{**base_event, "kind": "tool", "title": f"Looked at {name}"}]
    if isinstance(item_type, str) and "tool" in item_type:
        return [{**base_event, "kind": "tool", "title": title or item_type}]
    if isinstance(item_type, str):
        return [{**base_event, "kind": "status", "title": title or item_type}]
    return []


class ArmadaCodexJsonTranscriptTranslator:
    def events_from_json_event(self, event: dict[str, Any]) -> list[ArmadaWorkerTranscriptEvent]:
        event_type = event.get("type")
        if isinstance(event_type, str) and "delta" in event_type:
            delta_text = armada_codex_event_delta_text(event)
            if not delta_text:
                return []
            item_id = armada_codex_event_item_id(event)
            return [
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": delta_text,
                    "item_id": item_id,
                    "metadata": {"streaming": True, "delta": True},
                }
            ]

        return armada_transcript_events_from_codex_json_event(event)


ARMADA_APP_SERVER_ITEM_TYPE_MAP: dict[str, str] = {
    "agentMessage": "agent_message",
    "commandExecution": "command_execution",
    "fileChange": "file_change",
    "mcpToolCall": "mcp_tool_call",
    "dynamicToolCall": "dynamic_tool_call",
    "plan": "plan_update",
    "webSearch": "web_search",
    "imageView": "image_view",
    "contextCompaction": "context_compaction",
    "userMessage": "user_message",
    "collabAgentToolCall": "collab_agent_tool_call",
    "subAgentActivity": "sub_agent_activity",
}

# Engine chatter that never belongs in a run transcript. turn/diff joins
# the translated set with the live-diff surface.
ARMADA_APP_SERVER_IGNORED_METHODS = frozenset(
    {
        # A summary part's text arrives as summaryTextDelta.
        "item/reasoning/summaryPartAdded",
        "mcpServer/startupStatus/updated",
        "remoteControl/status/changed",
        "thread/goal/cleared",
        "thread/goal/updated",
        "turn/diff/updated",
    }
)


def armada_app_server_thread_id_from_result(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return None
    thread_id = thread.get("id")
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id.strip()
    return None


def armada_app_server_turn_id_from_result(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    turn = result.get("turn")
    if not isinstance(turn, dict):
        return None
    turn_id = turn.get("id")
    if isinstance(turn_id, str) and turn_id.strip():
        return turn_id.strip()
    return None


def armada_app_server_normalized_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    item_type = normalized.get("type")
    if isinstance(item_type, str):
        normalized["type"] = ARMADA_APP_SERVER_ITEM_TYPE_MAP.get(item_type, item_type)
    tool = normalized.get("tool")
    if isinstance(tool, str):
        normalized.setdefault("tool_name", tool)
        normalized.setdefault("name", tool)
    return normalized


def armada_app_server_thread_descriptor(thread: dict[str, Any]) -> dict[str, Any] | None:
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        return None
    descriptor: dict[str, Any] = {"id": thread_id.strip()}
    for source_key, target_key in (
        ("parentThreadId", "parent_thread_id"),
        ("agentNickname", "agent_nickname"),
        ("agentRole", "agent_role"),
        ("name", "name"),
    ):
        value = thread.get(source_key)
        if isinstance(value, str):
            descriptor[target_key] = trim_armada_agent_text(value, limit=1000)
        elif value is None and source_key in {
            "parentThreadId",
            "agentNickname",
            "agentRole",
            "name",
        }:
            descriptor[target_key] = None
    for source_key, target_key in (("createdAt", "created_at"), ("updatedAt", "updated_at")):
        value = thread.get(source_key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            descriptor[target_key] = float(value)
    status = thread.get("status")
    if isinstance(status, dict) and isinstance(status.get("type"), str):
        descriptor["status"] = {"type": status["type"]}
    source = thread.get("source")
    sub_agent = source.get("subAgent") if isinstance(source, dict) else None
    thread_source = thread.get("threadSource")
    if isinstance(thread_source, str):
        descriptor["thread_source"] = trim_armada_agent_text(thread_source, limit=100)
    elif isinstance(sub_agent, dict):
        descriptor["thread_source"] = "subagent"
    thread_spawn = sub_agent.get("thread_spawn") if isinstance(sub_agent, dict) else None
    if isinstance(thread_spawn, dict):
        for source_key, target_key in (
            ("agent_nickname", "agent_nickname"),
            ("agent_role", "agent_role"),
        ):
            value = thread_spawn.get(source_key)
            if descriptor.get(target_key) is None and isinstance(value, str):
                descriptor[target_key] = trim_armada_agent_text(value, limit=500)
    return descriptor


def armada_thread_session_event(
    descriptor: dict[str, Any], *, codex_method: str = "thread/started"
) -> ArmadaWorkerTranscriptEvent:
    thread_id = str(descriptor["id"])
    return {
        "kind": "session",
        "status": "done",
        "title": f"Codex thread {thread_id}",
        "body": thread_id,
        "metadata": {
            "codex_method": codex_method,
            "codex_thread_id": thread_id,
            "parent_thread_id": descriptor.get("parent_thread_id"),
            "agent_nickname": descriptor.get("agent_nickname"),
            "agent_role": descriptor.get("agent_role"),
            "thread": descriptor,
        },
    }


def armada_app_server_usage_event(params: dict[str, Any]) -> ArmadaWorkerTranscriptEvent | None:
    usage = armada_normalize_token_usage(params)
    if usage is None:
        return None
    return {
        "kind": "usage",
        "status": "done",
        "title": "Token usage updated.",
        "metadata": {"usage": usage},
    }


class ArmadaCodexAppServerTranscriptTranslator:
    def __init__(self, *, plugin_servers: Sequence[ArmadaPluginServer] = ()) -> None:
        self.json_translator = ArmadaCodexJsonTranscriptTranslator()
        # The plugins' MCP servers, by name, so that the trace names the
        # plugin a tool call went to.
        self.plugin_servers = {server.server: server for server in plugin_servers}
        self.last_rate_limit_key: tuple[object, ...] | None = None
        self.reported_unknown_methods: set[str] = set()
        self.thread_descriptors: dict[str, dict[str, Any]] = {}
        self.latest_direct_assistant_body: dict[str, str] = {}
        # Each streaming reasoning summary part, by item and index, until its
        # title is read; None once it has been.
        self.reasoning_summary_parts: dict[tuple[str, int], str | None] = {}

    def collaboration_thread_events(
        self, method: str, params: dict[str, Any], item: dict[str, Any]
    ) -> list[ArmadaWorkerTranscriptEvent]:
        """Project native collaboration receiver identities as child sessions.

        Some app-server builds announce a spawned child only through the
        collaboration item's receiverThreadIds/agentsStates before it becomes
        visible to thread/list.  Those are still the native Codex thread IDs;
        preserving them here closes the live projection gap without inventing
        a second agent model.
        """

        if item.get("type") != "collab_agent_tool_call":
            return []
        sender_thread_id = item.get("senderThreadId")
        if not isinstance(sender_thread_id, str) or not sender_thread_id.strip():
            sender_thread_id = params.get("threadId")
        if not isinstance(sender_thread_id, str) or not sender_thread_id.strip():
            return []
        receiver_thread_ids = item.get("receiverThreadIds")
        agent_states = item.get("agentsStates")
        if not isinstance(receiver_thread_ids, list) or not isinstance(agent_states, dict):
            return []

        events: list[ArmadaWorkerTranscriptEvent] = []
        for raw_thread_id in receiver_thread_ids[:16]:
            if not isinstance(raw_thread_id, str) or not raw_thread_id.strip():
                continue
            thread_id = raw_thread_id.strip()
            if thread_id == sender_thread_id:
                continue
            raw_state = agent_states.get(raw_thread_id)
            if not isinstance(raw_state, dict):
                continue
            raw_status = str(raw_state.get("status") or "").strip().lower()
            if raw_status in {
                "active",
                "busy",
                "inprogress",
                "pendinginit",
                "running",
                "working",
            }:
                status_type = "active"
            elif raw_status in {
                "cancelled",
                "error",
                "errored",
                "failed",
                "interrupted",
                "notfound",
                "systemerror",
            }:
                status_type = "failed"
            else:
                status_type = "idle"

            previous = self.thread_descriptors.get(thread_id)
            descriptor: dict[str, Any] = (
                dict(previous) if previous is not None else {"id": thread_id}
            )
            descriptor.setdefault("parent_thread_id", sender_thread_id.strip())
            descriptor.setdefault("agent_nickname", None)
            descriptor.setdefault("agent_role", None)
            descriptor.setdefault("name", None)
            descriptor["thread_source"] = "subagent"
            next_status = {"type": status_type}
            status_changed = previous is None or previous.get("status") != next_status
            descriptor["status"] = next_status
            self.thread_descriptors[thread_id] = descriptor
            if status_changed:
                events.append(armada_thread_session_event(descriptor, codex_method=method))
            message = raw_state.get("message")
            if isinstance(message, str) and message.strip():
                normalized_message = trim_armada_agent_text(message, limit=4000)
                if self.latest_direct_assistant_body.get(thread_id) == normalized_message:
                    continue
                events.append(
                    {
                        "kind": "error" if status_type == "failed" else "assistant",
                        "status": "failed" if status_type == "failed" else "done",
                        "title": "Agent failed." if status_type == "failed" else "Agent reply",
                        "body": normalized_message,
                        "metadata": {
                            "codex_method": method,
                            "codex_thread_id": thread_id,
                            "parent_thread_id": descriptor.get("parent_thread_id"),
                            "thread_status": status_type,
                        },
                    }
                )
        return events

    def contextualize_events(
        self,
        events: list[ArmadaWorkerTranscriptEvent],
        *,
        method: str,
        params: dict[str, Any],
    ) -> list[ArmadaWorkerTranscriptEvent]:
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        turn = params.get("turn")
        if not isinstance(turn_id, str) and isinstance(turn, dict):
            raw_turn_id = turn.get("id")
            if isinstance(raw_turn_id, str):
                turn_id = raw_turn_id
        descriptor = self.thread_descriptors.get(thread_id) if isinstance(thread_id, str) else None
        for event in events:
            metadata = event.get("metadata")
            event_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            event_metadata["codex_method"] = method
            if isinstance(thread_id, str):
                event_metadata["codex_thread_id"] = thread_id
            if isinstance(turn_id, str):
                event_metadata["codex_turn_id"] = turn_id
            if descriptor is not None:
                event_metadata["parent_thread_id"] = descriptor.get("parent_thread_id")
                event_metadata["agent_nickname"] = descriptor.get("agent_nickname")
                event_metadata["agent_role"] = descriptor.get("agent_role")
            event["metadata"] = event_metadata
        return events

    def rate_limit_events(self, params: dict[str, Any]) -> list[ArmadaWorkerTranscriptEvent]:
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

    def reasoning_title_events(self, params: dict[str, Any]) -> list[ArmadaWorkerTranscriptEvent]:
        """A reasoning summary part's title, once, as soon as it has
        streamed in: the item itself completes only when the model has
        finished thinking."""
        item_id = params.get("itemId")
        part_index = params.get("summaryIndex")
        delta = params.get("delta")
        if (
            not isinstance(item_id, str)
            or not isinstance(part_index, int)
            or not isinstance(delta, str)
        ):
            return []
        key = (item_id, part_index)
        part = self.reasoning_summary_parts.get(key, "")
        if part is None:
            return []
        part += delta
        title = armada_reasoning_title(part)
        opening = part.lstrip()
        if title is None and "**".startswith(opening[:2]) and "\n" not in opening:
            # Its bold title is still streaming in.
            self.reasoning_summary_parts[key] = part
            return []
        self.reasoning_summary_parts[key] = None
        if title is None:
            return []
        return [
            {
                "kind": "reasoning",
                "status": "running",
                "title": title,
                "item_id": item_id,
                "overview": title,
            }
        ]

    def unknown_method_events(
        self, method: str, params: dict[str, Any]
    ) -> list[ArmadaWorkerTranscriptEvent]:
        """Tolerant reader: an untranslated engine signal surfaces once per
        runtime instead of vanishing — the codexification surface-diff
        feeds on these logs after every Codex upgrade."""
        if method in self.reported_unknown_methods or "elta" in method:
            return []
        self.reported_unknown_methods.add(method)
        logger.info("Observed untranslated app-server method: %s", method)
        return [
            {
                "kind": "status",
                "status": "done",
                "title": f"Codex signal: {method}",
                "metadata": {"method": method, "param_keys": sorted(params)[:8]},
            }
        ]

    def events_from_message(self, message: dict[str, Any]) -> list[ArmadaWorkerTranscriptEvent]:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return []

        if method == "thread/started":
            thread = params.get("thread")
            descriptor = (
                armada_app_server_thread_descriptor(thread) if isinstance(thread, dict) else None
            )
            if descriptor is None:
                return []
            thread_id = descriptor["id"]
            assert isinstance(thread_id, str)
            self.thread_descriptors[thread_id] = descriptor
            return [armada_thread_session_event(descriptor)]
        if method == "thread/status/changed":
            thread_id = params.get("threadId")
            raw_status = params.get("status")
            if not isinstance(thread_id, str) or not isinstance(raw_status, dict):
                return []
            status_type = str(raw_status.get("type") or "unknown")
            descriptor = self.thread_descriptors.get(thread_id)
            if descriptor is not None:
                descriptor["status"] = {"type": status_type}
            if descriptor is None or not isinstance(descriptor.get("parent_thread_id"), str):
                # Root thread liveness already has a typed agent run state.
                # Only child status belongs in the native-agent surface.
                return []
            return self.contextualize_events(
                [
                    {
                        "kind": "agent",
                        "status": "running" if status_type == "active" else "done",
                        "title": f"Agent {status_type}",
                        "metadata": {"thread_status": status_type},
                    }
                ],
                method=method,
                params=params,
            )
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
            return self.contextualize_events(
                [
                    {
                        "kind": "plan",
                        "status": "done",
                        "title": "Plan",
                        "metadata": {"steps": steps},
                    }
                ],
                method=method,
                params=params,
            )
        if method == "turn/started":
            thread_id = params.get("threadId")
            descriptor = (
                self.thread_descriptors.get(thread_id) if isinstance(thread_id, str) else None
            )
            if descriptor is not None:
                descriptor["status"] = {"type": "active"}
            return self.contextualize_events(
                [
                    {
                        "kind": "status",
                        "status": "running",
                        "title": "Codex turn started.",
                    }
                ],
                method=method,
                params=params,
            )
        if method == "turn/completed":
            thread_id = params.get("threadId")
            descriptor = (
                self.thread_descriptors.get(thread_id) if isinstance(thread_id, str) else None
            )
            turn = params.get("turn")
            raw_turn_status = turn.get("status") if isinstance(turn, dict) else None
            turn_status = str(raw_turn_status or "completed")
            if descriptor is not None:
                descriptor["status"] = {
                    "type": "failed" if turn_status in {"failed", "interrupted"} else "idle"
                }
            return self.contextualize_events(
                [
                    {
                        "kind": "status",
                        "status": "done",
                        "title": "Codex turn completed.",
                        # The native Turn object contains its complete item
                        # history. Persist only the terminal reducer input;
                        # command bodies already have their own bounded rows.
                        "metadata": {"turn_status": turn_status},
                    }
                ],
                method=method,
                params=params,
            )
        if method == "thread/tokenUsage/updated":
            usage_event = armada_app_server_usage_event(params)
            return (
                self.contextualize_events([usage_event], method=method, params=params)
                if usage_event is not None
                else []
            )
        if method == "error":
            # The app-server's error: {"error": {"message", "codexErrorInfo",
            # ...}, "willRetry", ...}; an older shape has "message" alone.
            error = params.get("error")
            words = error.get("message") if isinstance(error, dict) else None
            codex_error_info = error.get("codexErrorInfo") if isinstance(error, dict) else None
            return self.contextualize_events(
                [
                    {
                        "kind": "error",
                        "status": "failed",
                        "title": "Codex runtime error.",
                        "body": trim_armada_agent_text(
                            str(words or params.get("message", "") or error or ""),
                            limit=1000,
                        ),
                        "metadata": {
                            "codex_error_info": codex_error_info
                            if isinstance(codex_error_info, str)
                            else None,
                            "will_retry": params.get("willRetry") is True,
                        },
                    }
                ],
                method=method,
                params=params,
            )
        if method == "item/agentMessage/delta":
            return self.contextualize_events(
                self.json_translator.events_from_json_event(
                    {
                        "type": "item.delta",
                        "item_id": params.get("itemId"),
                        "delta": params.get("delta", ""),
                    }
                ),
                method=method,
                params=params,
            )
        if method == "item/reasoning/summaryTextDelta":
            return self.contextualize_events(
                self.reasoning_title_events(params),
                method=method,
                params=params,
            )
        if method == "account/rateLimits/updated":
            return self.rate_limit_events(params)
        if method in {"warning", "configWarning"}:
            body = str(params.get("message") or params.get("summary") or "").strip()
            if not body:
                return []
            return self.contextualize_events(
                [
                    {
                        "kind": "status",
                        "status": "done",
                        "title": "Codex warning.",
                        "body": trim_armada_agent_text(body, limit=600),
                    }
                ],
                method=method,
                params=params,
            )
        if method in ARMADA_APP_SERVER_IGNORED_METHODS:
            return []
        if method not in {"item/started", "item/completed"}:
            return self.contextualize_events(
                self.unknown_method_events(method, params),
                method=method,
                params=params,
            )
        item = params.get("item")
        if not isinstance(item, dict):
            return []
        normalized_item = armada_app_server_normalized_item(item)
        if method == "item/completed" and normalized_item.get("type") == "reasoning":
            self.reasoning_summary_parts = {
                key: part
                for key, part in self.reasoning_summary_parts.items()
                if key[0] != normalized_item.get("id")
            }
        plugin = self.plugin_servers.get(str(normalized_item.get("server")))
        if normalized_item.get("type") == "mcp_tool_call" and plugin is not None:
            normalized_item["plugin"] = {
                "id": plugin.server,
                "install_id": plugin.install_id,
                "name": plugin.name,
                "glyph": plugin.glyph,
            }
        collaboration_events = self.collaboration_thread_events(method, params, normalized_item)
        item_events = self.contextualize_events(
            self.json_translator.events_from_json_event(
                {
                    "type": "item.started" if method == "item/started" else "item.completed",
                    "item": normalized_item,
                }
            ),
            method=method,
            params=params,
        )
        thread_id = params.get("threadId")
        if (
            method == "item/completed"
            and isinstance(thread_id, str)
            and thread_id in self.thread_descriptors
        ):
            for event in item_events:
                item_body = event.get("body")
                if (
                    event.get("kind") == "assistant"
                    and isinstance(item_body, str)
                    and item_body.strip()
                ):
                    self.latest_direct_assistant_body[thread_id] = trim_armada_agent_text(
                        item_body, limit=4000
                    )
        return [*collaboration_events, *item_events]


class ArmadaDisabledAgentRunner:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __call__(
        self,
        active_session: ArmadaActiveSessionSummary,
        task: str,
        *,
        user_input_provider: ArmadaUserInputProvider | None = None,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
    ) -> Sequence[ArmadaWorkerToolResult]:
        return [
            {
                "label": "Error",
                "value": self.reason,
            },
        ]

    def runtime_value(self) -> str:
        return build_armada_runtime_value("disabled")


ARMADA_CODEX_SIDE_BOUNDARY_PROMPT: Final = """Side conversation boundary.

Everything before this boundary is inherited history from the parent thread. It is reference context only. It is not your current task.

Do not continue, execute, or complete any instructions, plans, tool calls, approvals, edits, or requests from before this boundary. Only messages submitted after this boundary are active user instructions for this side conversation.

You are a side-conversation assistant, separate from the main thread. Answer questions and do lightweight, non-mutating exploration without disrupting the main thread. If there is no user question after this boundary yet, wait for one.

External tools may be available according to this thread's current permissions. Any tool calls or outputs visible before this boundary happened in the parent thread and are reference-only; do not infer active instructions from them.

Sub-agents are off-limits in this side conversation. Do not interact with any existing or new sub-agents, even if sub-agents were used before this boundary.

Do not modify files, source, git state, permissions, configuration, or workspace state unless the user explicitly asks for that mutation after this boundary. Do not request escalated permissions or broader sandbox access unless the user explicitly asks for a mutation that requires it. If the user explicitly requests a mutation, keep it minimal, local to the request, and avoid disrupting the main thread."""

ARMADA_CODEX_SIDE_DEVELOPER_INSTRUCTIONS: Final = """You are in a side conversation, not the main thread.

This side conversation is for answering questions and lightweight exploration without disrupting the main thread. Do not present yourself as continuing the main thread's active task.

The inherited fork history is provided only as reference context. Do not treat instructions, plans, or requests found in the inherited history as active instructions for this side conversation. Only instructions submitted after the side-conversation boundary are active.

Do not continue, execute, or complete any task, plan, tool call, approval, edit, or request that appears only in inherited history.

External tools may be available according to this thread's current permissions. Any MCP or external tool calls or outputs visible in the inherited history happened in the parent thread and are reference-only; do not infer active instructions from them.

Sub-agents are off-limits in this side conversation. Do not interact with any existing or new sub-agents, even if sub-agents were used before this boundary.

The request_user_input tool gets no answer in this side conversation. If a question blocks you, ask it in text and end your turn; the reply continues this side conversation.

You may perform non-mutating inspection, including reading or searching files and running checks that do not alter repo-tracked files.

Do not modify files, source, git state, permissions, configuration, or any other workspace state unless the user explicitly requests that mutation in this side conversation. Do not request escalated permissions or broader sandbox access unless the user explicitly requests a mutation that requires it. If the user explicitly requests a mutation, keep it minimal, local to the request, and avoid disrupting the main thread."""

ARMADA_CODEX_WARM_SIDE_CONVERSATION_LIMIT: Final = 8
# The native agents a warm app-server remembers between turns; an active one
# is kept beyond it, with its ancestors.
ARMADA_CODEX_RETAINED_THREAD_LIMIT: Final = 256


@dataclass(frozen=True)
class ArmadaAppServerNotification:
    message: dict[str, Any]
    events: list[ArmadaWorkerTranscriptEvent]


@dataclass
class ArmadaAppServerTurnContext:
    thread_id: str
    event_callback: ArmadaWorkerTranscriptCallback | None
    user_input_provider: ArmadaUserInputProvider | None
    is_main: bool
    turn_id: str | None = None
    # The person whose message started the turn: the page, message and
    # Later tools act as them.
    requesting_user_id: int | None = None
    # The message the turn's run answers, which names the run.
    trigger_message_id: int | None = None
    messages: queue.Queue[object] = field(default_factory=queue.Queue)
    pending_artifacts: list[str] = field(default_factory=list)


ARMADA_APP_SERVER_ACTIVITY: Final = object()
ARMADA_APP_SERVER_STEER_REPLY_TIMEOUT_SECONDS: Final = 30
ARMADA_APP_SERVER_STOP_GRACE_SECONDS = 2


def armada_process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A member runs as another user (a topic's isolation user under
        # sudo): it exists, though only sudo can signal it.
        return True
    return True


def armada_signal_process_group(process_group_id: int, signal_number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process_group_id, signal_number)


def armada_stop_app_server_process(
    process: subprocess.Popen[str], *, stdin_closed: bool = False
) -> None:
    """Stop an app-server and every process it started, and reap it.

    The app-server leads its own process group, so one signal reaches
    Codex's native binary and whatever it runs, even when the direct
    child is a launcher or sudo, which cannot pass SIGKILL on: before,
    a SIGKILL to the launcher orphaned the binary. SIGTERM first, so
    Codex can finish writing its rollout; SIGKILL to the whole group for
    whatever is left after the grace period. Before any signal, a
    caller that closed stdin lets Codex exit by itself: it does so at
    once when its stdio transport ends.
    """
    process_group_id = getattr(process, "pid", None)
    if not isinstance(process_group_id, int):
        # A scripted transport in tests has no OS process to signal.
        process.terminate()
        return
    grace_seconds = ARMADA_APP_SERVER_STOP_GRACE_SECONDS
    if stdin_closed:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=grace_seconds)
    deadline = time.monotonic() + grace_seconds
    if process.poll() is None or armada_process_group_exists(process_group_id):
        armada_signal_process_group(process_group_id, signal.SIGTERM)
    while armada_process_group_exists(process_group_id):
        # Reap the leader as soon as it exits, or it stays a zombie member
        # of its own group.
        process.poll()
        if time.monotonic() >= deadline:
            armada_signal_process_group(process_group_id, signal.SIGKILL)
            break
        time.sleep(0.02)
    if process.poll() is None:
        # Not a group leader after all (a popen_command that made no new
        # session): it at least must not survive.
        process.kill()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        logger.warning("Codex app-server %s did not exit after SIGKILL.", process_group_id)


class ArmadaCodexAppServerToolRunner:
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
        memories_enabled: bool = False,
        reasoning_effort: str | None = None,
        ephemeral: bool = False,
        process_isolation: ArmadaProcessIsolation | None = None,
        browser_dir: str | None = None,
        plugin_servers: Sequence[ArmadaPluginServer] = (),
        popen_command: ArmadaSubprocessPopen = subprocess.Popen,
    ) -> None:
        self.codex_bin = codex_bin
        self.workdir = workdir
        self.model = model
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        # This is a maximum silence window, not a total turn-duration cap.
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.service_tier = service_tier
        self.fast_mode_enabled = fast_mode_enabled
        self.memories_enabled = memories_enabled
        self.reasoning_effort = reasoning_effort
        self.ephemeral = ephemeral
        self.process_isolation = process_isolation
        self.browser_dir = browser_dir
        self.plugin_servers = tuple(plugin_servers)
        # What the worker compares with the realm's plugins between turns.
        self.plugin_fingerprint = armada_plugin_fingerprint(self.plugin_servers)
        self.popen_command = popen_command
        self.process: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.reader_thread: threading.Thread | None = None
        self.transport_abort_error: Exception | None = None
        self.next_request_id = 0
        self.lock = threading.RLock()
        self.main_turn_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.pending_responses: dict[int, queue.Queue[object]] = {}
        self.early_responses: dict[int, dict[str, Any]] = {}
        self.turn_contexts_by_thread_id: dict[str, ArmadaAppServerTurnContext] = {}
        self.turn_contexts_by_turn_id: dict[str, ArmadaAppServerTurnContext] = {}
        self.unrouted_notifications: dict[str, list[ArmadaAppServerNotification]] = {}
        self.side_thread_ids: set[str] = set()
        # Native /btw conversations are ephemeral, but not one-shot.  Keep
        # their app-server identity for as long as this warm worktree runtime
        # lives so repeated replies to the agent under one durable Zulip root remain
        # one native side conversation.  The map deliberately dies with the
        # app-server generation; Zulip messages remain durable, while Armada
        # adds no replay or parallel memory layer for ephemeral Codex state.
        # Like the active-context maps above, this is owned by state_lock.
        self.side_thread_ids_by_conversation_key: dict[str, str] = {}
        self.loaded_thread_id: str | None = None
        self.loaded_through_trigger_message_id: int | None = None
        self.active_thread_id: str | None = None
        self.active_turn_id: str | None = None
        self.background_request_ids: set[int] = set()
        self.current_user_input_provider: ArmadaUserInputProvider | None = None
        self.pending_artifacts: list[str] = []
        self.translator = ArmadaCodexAppServerTranscriptTranslator(
            plugin_servers=self.plugin_servers
        )

    def configure_turn(self, *, model: str, reasoning_effort: str) -> None:
        """Keep one app-server per worktree; model and effort belong to the turn."""
        self.model = model
        self.reasoning_effort = reasoning_effort

    def config_args(self) -> list[str]:
        args: list[str] = []
        if self.service_tier:
            args.extend(["--config", f'service_tier="{self.service_tier}"'])
        if self.fast_mode_enabled:
            args.extend(["--config", "features.fast_mode=true"])
        # Explicit either way, so a config.toml in CODEX_HOME cannot turn
        # memories on behind the setting.
        memories = "true" if self.memories_enabled else "false"
        args.extend(["--config", f"features.memories={memories}"])
        # The plan tool (`update_plan`) is the source of turn/plan/updated,
        # the checklist the topic shows live; the instructions ask for it.
        # Explicit, so a config.toml in CODEX_HOME cannot take it away.
        args.extend(["--config", "tools.update_plan={enabled=true}"])
        # The reasoning summary's titles are the run's overview, what it is
        # doing now; gpt-6.1-sol sends no summary unless asked.
        args.extend(["--config", 'model_reasoning_summary="auto"'])
        # The question tool (`request_user_input`, the source of
        # item/tool/requestUserInput) is refused in Default collaboration
        # mode unless this feature is on; `tools.experimental_request_user_input`
        # does not lift that. The feature is under development, and Codex
        # warns about it on every thread, which would reach the transcript.
        args.extend(["--config", "features.default_mode_request_user_input=true"])
        args.extend(["--config", "suppress_unstable_features_warning=true"])
        if self.browser_dir:
            args.extend(self.browser_config_args(self.browser_dir))
        args.extend(armada_plugin_config_args(self.plugin_servers))
        if self.reasoning_effort:
            args.extend(["--config", f'model_reasoning_effort="{self.reasoning_effort}"'])
        return args

    def browser_config_args(self, browser_dir: str) -> list[str]:
        """The browser, an MCP server Codex starts beside each thread: a
        headless Chromium from the install in browser_dir.  Its tools are
        approved here, since Armada answers no approval request for them;
        MCP servers run outside Codex's sandbox, so it reaches the network.
        Values are TOML, which JSON's strings and arrays of strings are."""
        server = f"mcp_servers.{ARMADA_BROWSER_MCP_SERVER}"
        playwright_args = [
            "mcp",
            "--headless",
            # Playwright's own Chromium (Chrome for Testing), in new
            # headless mode, which renders as desktop Chrome does.
            "--browser",
            "chromium",
            "--isolated",
            # Clicks by coordinates too, for what a page snapshot cannot
            # name (canvas, maps).
            "--caps",
            "vision",
            "--viewport-size",
            "1440x900",
            "--user-agent",
            ARMADA_BROWSER_USER_AGENT,
            # How long to wait after each action for the work it set off,
            # so that a screenshot shows the page settled.
            "--timeout-settle",
            "1000",
            "--output-dir",
            self.browser_output_dir(),
            "--output-max-size",
            str(100 * 1024 * 1024),
        ]
        browsers_path = json.dumps(f"{browser_dir}/browsers")
        return [
            "--config",
            f"{server}.command={json.dumps(f'{browser_dir}/bin/playwright')}",
            "--config",
            f"{server}.args={json.dumps(playwright_args)}",
            "--config",
            f"{server}.env={{PLAYWRIGHT_BROWSERS_PATH={browsers_path}}}",
            "--config",
            f'{server}.default_tools_approval_mode="approve"',
            # Above the browser's own 60-second navigation timeout, so a
            # slow page reports its own error.
            "--config",
            f"{server}.tool_timeout_sec=120",
        ]

    def browser_output_dir(self) -> str:
        """Where the browser keeps screenshots and page snapshots: the
        agent's cache, a directory per worktree, outside the repository so
        that they never show among the topic's changes."""
        home = (
            self.process_isolation.home if self.process_isolation is not None else str(Path.home())
        )
        worktree = Path(self.workdir).name if self.workdir else "default"
        return f"{home}/.cache/armada-browser/{worktree}"

    def build_command(self) -> list[str]:
        executable, environment = armada_codex_executable(self.codex_bin)
        command = [
            executable,
            "app-server",
            "--stdio",
            *self.config_args(),
        ]
        if environment:
            # env execs the binary in its place; no process stays resident.
            command = ["env", *(f"{name}={value}" for name, value in environment), *command]
        return armada_isolated_command(command, self.process_isolation)

    def ensure_started(self) -> None:
        if (
            self.process is not None
            and self.process.poll() is None
            and self.reader_thread is not None
            and self.transport_abort_error is None
        ):
            return
        if (
            self.process is not None
            or self.reader_thread is not None
            or self.loaded_thread_id is not None
        ):
            self.reset_transport()
        # The plugins' tokens, which Codex reads from the variables its
        # flags name, are in the process's environment alone.
        plugin_environment = armada_plugin_environment(self.plugin_servers)
        popen_environment = (
            {"env": {**os.environ, **plugin_environment}} if plugin_environment else {}
        )
        try:
            process = self.popen_command(
                self.build_command(),
                cwd=armada_subprocess_cwd(self.workdir, self.process_isolation),
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                # Its own process group, so stopping it reaches every
                # process it started (armada_stop_app_server_process).
                start_new_session=True,
                **popen_environment,
            )
        except FileNotFoundError as error:
            raise ArmadaAgentRuntimeError(
                f"Codex executable not found: {self.codex_bin}"
            ) from error

        messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.process = process
        self.messages = messages
        reader_thread = threading.Thread(
            target=self.read_messages,
            args=(process, messages),
            name="armada-codex-app-server-reader",
            daemon=True,
        )
        self.reader_thread = reader_thread
        reader_thread.start()
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "armada",
                    "title": "Armada",
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

    def read_messages(
        self,
        process: subprocess.Popen[str],
        messages: queue.Queue[dict[str, Any] | None],
    ) -> None:
        if process.stdout is None:
            messages.put(None)
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
                    armada_capture_protocol_message("recv", message)
                    if messages is self.messages:
                        self.dispatch_message(message)
                    else:
                        # A retired reader may finish after a replacement
                        # process starts. Its generation-local queue remains
                        # useful to the transport-fencing test, but it must
                        # never signal the replacement transport.
                        messages.put(message)
        finally:
            messages.put(None)
            if messages is self.messages:
                self.signal_transport_closed()

    def signal_transport_closed(self) -> None:
        with self.state_lock:
            waiters = list(self.pending_responses.values())
            contexts = list(self.turn_contexts_by_thread_id.values())
        for waiter in waiters:
            waiter.put(None)
        for context in contexts:
            context.messages.put(None)

    def broadcast_protocol_activity(self, *, except_request_id: int | None = None) -> None:
        for request_id, waiter in self.pending_responses.items():
            if request_id != except_request_id:
                waiter.put(ARMADA_APP_SERVER_ACTIVITY)

    def thread_id_from_message(self, message: dict[str, Any]) -> str | None:
        params = message.get("params")
        if not isinstance(params, dict):
            return None
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
        thread = params.get("thread")
        if isinstance(thread, dict):
            thread_id = thread.get("id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
        return None

    def context_for_thread_locked(self, thread_id: str) -> ArmadaAppServerTurnContext | None:
        exact = self.turn_contexts_by_thread_id.get(thread_id)
        if exact is not None:
            return exact
        current_thread_id = thread_id
        seen: set[str] = set()
        while current_thread_id not in seen:
            seen.add(current_thread_id)
            descriptor = self.translator.thread_descriptors.get(current_thread_id)
            if descriptor is None or not self.is_native_agent_descriptor(descriptor):
                return None
            parent_thread_id = descriptor.get("parent_thread_id")
            if not isinstance(parent_thread_id, str):
                return None
            parent_context = self.turn_contexts_by_thread_id.get(parent_thread_id)
            if parent_context is not None:
                return parent_context
            current_thread_id = parent_thread_id
        return None

    def context_for_params_locked(
        self, params: dict[str, Any]
    ) -> ArmadaAppServerTurnContext | None:
        turn_id = params.get("turnId")
        turn_context = None
        if isinstance(turn_id, str):
            turn_context = self.turn_contexts_by_turn_id.get(turn_id)
        thread_id = params.get("threadId")
        thread_context = None
        if isinstance(thread_id, str):
            thread_context = self.context_for_thread_locked(thread_id)
        if (
            turn_context is not None
            and thread_context is not None
            and turn_context is not thread_context
        ):
            # Never let one request combine the ownership of two live turns.
            return None
        context = turn_context or thread_context
        if context is not None:
            return context
        active_contexts = list(self.turn_contexts_by_thread_id.values())
        # Older app-server methods can omit ownership fields. That fallback is
        # safe only when there is exactly one possible turn; choosing the main
        # context during a concurrent /btw turn would cross-contaminate input,
        # artifacts, callbacks, and accounting.
        return active_contexts[0] if len(active_contexts) == 1 else None

    @staticmethod
    def is_server_request(message: dict[str, Any]) -> bool:
        return (
            "id" in message
            and "method" in message
            and "result" not in message
            and "error" not in message
        )

    def dispatch_message(self, message: dict[str, Any]) -> None:
        server_request = self.is_server_request(message)
        server_request_context: ArmadaAppServerTurnContext | None = None
        if server_request:
            raw_params = message.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
            with self.state_lock:
                self.broadcast_protocol_activity()
                server_request_context = self.context_for_params_locked(params)
                if server_request_context is not None:
                    server_request_context.messages.put(ARMADA_APP_SERVER_ACTIVITY)
            threading.Thread(
                target=self.handle_server_request,
                args=(message,),
                kwargs={"turn_context": server_request_context},
                name="armada-codex-app-server-request",
                daemon=True,
            ).start()
            return

        if "method" not in message:
            message_id = message.get("id")
            with self.state_lock:
                if isinstance(message_id, int) and message_id in self.background_request_ids:
                    self.consume_background_response(message)
                    self.broadcast_protocol_activity(except_request_id=message_id)
                    return
                waiter = (
                    self.pending_responses.pop(message_id, None)
                    if isinstance(message_id, int)
                    else None
                )
                self.broadcast_protocol_activity(except_request_id=message_id)
                if waiter is None:
                    if isinstance(message_id, int):
                        # Some deterministic fake transports used by the
                        # focused suite emit their complete script as soon as
                        # the reader starts. Real responses cannot precede the
                        # request; retaining a bounded early result keeps that
                        # harness useful without weakening production routing.
                        if len(self.early_responses) >= 64:
                            self.early_responses.pop(next(iter(self.early_responses)))
                        self.early_responses[message_id] = message
                    return
            waiter.put(message)
            return

        with self.state_lock:
            self.broadcast_protocol_activity()
            events = self.handle_notification(message, event_callback=None)
            notification = ArmadaAppServerNotification(message=message, events=events)
            thread_id = self.thread_id_from_message(message)
            context = self.context_for_thread_locked(thread_id) if thread_id is not None else None
            if context is None and thread_id is None:
                context = next(
                    (
                        candidate
                        for candidate in self.turn_contexts_by_thread_id.values()
                        if candidate.is_main
                    ),
                    None,
                )
            if context is None:
                if thread_id is not None:
                    if thread_id in self.side_thread_ids_by_conversation_key.values():
                        # A retained /btw thread has no consumer between its
                        # turns. Late notifications from the completed turn
                        # must not leak into the next turn or accumulate.
                        return
                    backlog = self.unrouted_notifications.setdefault(thread_id, [])
                    backlog.append(notification)
                    del backlog[:-128]
                return
            if thread_id is not None:
                for pending in self.unrouted_notifications.pop(thread_id, []):
                    context.messages.put(pending)
            context.messages.put(notification)

    def reset_transport(self) -> None:
        """Retire one app-server generation without contaminating the next.

        Every reader owns the process and queue it was created with.  This
        makes a late EOF from an old process harmless after a stale-thread
        fence forces a native resume in a fresh app-server.
        """
        process = self.process
        reader_thread = self.reader_thread
        self.process = None
        self.reader_thread = None
        self.messages = queue.Queue()
        self.transport_abort_error = None
        self.loaded_thread_id = None
        self.loaded_through_trigger_message_id = None
        self.active_thread_id = None
        self.active_turn_id = None
        self.background_request_ids.clear()
        with self.state_lock:
            response_waiters = list(self.pending_responses.values())
            turn_contexts = list(self.turn_contexts_by_thread_id.values())
            self.pending_responses.clear()
            self.early_responses.clear()
            self.turn_contexts_by_thread_id.clear()
            self.turn_contexts_by_turn_id.clear()
            self.unrouted_notifications.clear()
            self.side_thread_ids.clear()
            self.side_thread_ids_by_conversation_key.clear()
        self.current_user_input_provider = None
        self.pending_artifacts = []
        self.translator = ArmadaCodexAppServerTranscriptTranslator(
            plugin_servers=self.plugin_servers
        )
        for response_waiter in response_waiters:
            response_waiter.put(None)
        for turn_context in turn_contexts:
            turn_context.messages.put(None)
        if process is not None:
            armada_stop_app_server_process(process, stdin_closed=self.close_process_stdin(process))
        if (
            reader_thread is not None
            and reader_thread is not threading.current_thread()
            and reader_thread.is_alive()
        ):
            reader_thread.join(timeout=ARMADA_APP_SERVER_STOP_GRACE_SECONDS)
            if reader_thread.is_alive():
                logger.warning("The Codex app-server's reader thread outlived its process.")

    def close_process_stdin(self, process: subprocess.Popen[str]) -> bool:
        """End Codex's stdio transport, its own signal to exit. A write
        blocked on a full pipe holds the send lock; then the signals in
        armada_stop_app_server_process do the work instead."""
        stdin = getattr(process, "stdin", None)
        close = getattr(stdin, "close", None)
        if not callable(close) or not self.send_lock.acquire(
            timeout=ARMADA_APP_SERVER_STOP_GRACE_SECONDS
        ):
            return False
        try:
            close()
        except (OSError, ValueError):
            pass
        finally:
            self.send_lock.release()
        return True

    def next_id(self) -> int:
        with self.state_lock:
            self.next_request_id += 1
            return self.next_request_id

    def send_message(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise ArmadaAgentRuntimeError("Codex app-server is not running.")
        armada_capture_protocol_message("send", message)
        with self.send_lock:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send_message({"method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        request_id = self.next_id()
        response_queue: queue.Queue[object] = queue.Queue()
        with self.state_lock:
            early_response = self.early_responses.pop(request_id, None)
            if early_response is None:
                self.pending_responses[request_id] = response_queue
            else:
                response_queue.put(early_response)
        try:
            self.send_message({"id": request_id, "method": method, "params": params})
        except Exception:
            with self.state_lock:
                self.pending_responses.pop(request_id, None)
            raise
        try:
            return self.wait_for_response(
                request_id,
                event_callback=event_callback,
                timeout_seconds=timeout_seconds,
                response_queue=response_queue,
            )
        finally:
            # Responses normally remove their waiter in dispatch_message.
            # Transport closure and aborts do not, so make request ownership
            # explicit here as well and never retain a dead waiter.
            with self.state_lock:
                self.pending_responses.pop(request_id, None)

    def wait_for_response(
        self,
        request_id: int,
        *,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
        timeout_seconds: int | None = None,
        response_queue: queue.Queue[object] | None = None,
    ) -> Any:
        idle_timeout_seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if response_queue is not None:
            while True:
                try:
                    queued = response_queue.get(timeout=idle_timeout_seconds)
                except queue.Empty as error:
                    with self.state_lock:
                        self.pending_responses.pop(request_id, None)
                    raise ArmadaAgentRuntimeError(
                        "Codex app-server produced no activity for "
                        f"{idle_timeout_seconds:g} seconds."
                    ) from error
                abort_error = self.transport_abort_error
                if abort_error is not None:
                    raise abort_error
                if queued is ARMADA_APP_SERVER_ACTIVITY:
                    continue
                if queued is None:
                    raise ArmadaAgentRuntimeError("Codex app-server stopped unexpectedly.")
                assert isinstance(queued, dict)
                if "error" in queued:
                    raise ArmadaAgentRuntimeError(
                        "Codex app-server failed: "
                        f"{trim_armada_agent_text(str(queued['error']), limit=self.output_limit)}"
                    )
                return queued.get("result")
        while True:
            message = self.next_message(idle_timeout_seconds)
            if message is None:
                raise ArmadaAgentRuntimeError("Codex app-server stopped unexpectedly.")
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
                raise ArmadaAgentRuntimeError(
                    "Codex app-server failed: "
                    f"{trim_armada_agent_text(str(message['error']), limit=self.output_limit)}"
                )
            return message.get("result")

    def next_message(self, timeout: float) -> dict[str, Any] | None:
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty as error:
            raise ArmadaAgentRuntimeError(
                f"Codex app-server produced no activity for {timeout:g} seconds."
            ) from error
        abort_error = self.transport_abort_error
        if abort_error is not None:
            raise abort_error
        return message

    def handle_server_request(
        self,
        message: dict[str, Any],
        *,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
        turn_context: ArmadaAppServerTurnContext | None = None,
    ) -> bool:
        if "id" not in message or "method" not in message:
            return False
        if "result" in message or "error" in message:
            return False
        request_id = message["id"]
        method = str(message.get("method", "unknown"))
        raw_params = message.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        result, error, event = self.answer_for_server_request(
            method, params, turn_context=turn_context
        )
        contextualized_event: ArmadaWorkerTranscriptEvent | None = None
        if event is not None:
            with self.state_lock:
                [contextualized_event] = self.translator.contextualize_events(
                    [event],
                    method=method,
                    params=params,
                )
            if turn_context is not None:
                # Server requests are answered off the transport reader so a
                # blocking requestUserInput cannot stall the parent or a side
                # turn. Put its visible event back onto the owning turn queue
                # before replying; app-server cannot emit turn/completed until
                # it receives this response, so callback order stays exact and
                # ArmadaWorkerTurn sees all events on one thread.
                turn_context.messages.put(
                    ArmadaAppServerNotification(
                        message=message,
                        events=[contextualized_event],
                    )
                )
        reply: dict[str, Any] = {"id": request_id}
        if error is not None:
            reply["error"] = error
        else:
            reply["result"] = result
        self.send_message(reply)
        callback = turn_context.event_callback if turn_context is not None else event_callback
        if contextualized_event is not None and turn_context is None and callback is not None:
            callback(contextualized_event)
        return True

    def answer_armada_tool_call(
        self,
        params: dict[str, Any],
        *,
        turn_context: ArmadaAppServerTurnContext | None,
    ) -> tuple[dict[str, Any], None, ArmadaWorkerTranscriptEvent]:
        tool_name = str(params["tool"])
        call_id = params.get("callId")
        requesting_user_id = turn_context.requesting_user_id if turn_context is not None else None
        if tool_name in ARMADA_PAGE_TOOL_NAMES:
            answer = answer_armada_page_tool(requesting_user_id, tool_name, params.get("arguments"))
        elif tool_name in ARMADA_LATER_TOOL_NAMES:
            answer = answer_armada_later_tool(
                requesting_user_id,
                turn_context.trigger_message_id if turn_context is not None else None,
                tool_name,
                params.get("arguments"),
            )
        else:
            turn = ArmadaToolTurn(
                requesting_user_id=requesting_user_id,
                trigger_message_id=(
                    turn_context.trigger_message_id if turn_context is not None else None
                ),
                workdir=self.workdir,
                isolation_username=(
                    self.process_isolation.username if self.process_isolation is not None else None
                ),
                # A run's provider (ArmadaRunAsking) also waits on its person.
                person_wait=getattr(
                    turn_context.user_input_provider if turn_context is not None else None,
                    "wait_on_person",
                    None,
                ),
            )
            if tool_name in ARMADA_PLUGIN_TOOL_NAMES:
                answer = answer_armada_plugin_tool(turn, tool_name, params.get("arguments"))
            else:
                answer = answer_armada_message_tool(turn, tool_name, params.get("arguments"))
        return (
            {
                "success": answer["success"],
                "contentItems": [{"type": "inputText", "text": answer["text"]}],
            },
            None,
            {
                "kind": "tool",
                "status": "done" if answer["success"] else "failed",
                "title": answer["title"],
                **({"item_id": call_id} if isinstance(call_id, str) else {}),
                "metadata": {"tool_name": tool_name, "human_tool_result": True},
            },
        )

    def answer_for_server_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        turn_context: ArmadaAppServerTurnContext | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, ArmadaWorkerTranscriptEvent | None]:
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
            request_detail = params.get("command") or params.get("reason") or method
            event: ArmadaWorkerTranscriptEvent = {
                "kind": "status",
                "status": "done",
                "title": (
                    "Approved an escalation request."
                    if isolated
                    else "Declined an escalation request."
                ),
                "body": trim_armada_agent_text(str(request_detail), limit=300),
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
            provider = (
                turn_context.user_input_provider
                if turn_context is not None
                else self.current_user_input_provider
            )
            answers: dict[str, list[str]] | None = None
            if provider is not None and questions:
                try:
                    answers = provider(questions)
                except Exception:
                    logger.exception("User-input provider failed.")
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
                "body": trim_armada_agent_text(str(question_text), limit=300),
                "metadata": {"method": method},
            }
            return (
                None,
                {"code": -32601, "message": "No user answer was available."},
                event,
            )
        if (
            method == "item/tool/call"
            and params.get("tool")
            in ARMADA_PAGE_TOOL_NAMES
            | ARMADA_MESSAGE_TOOL_NAMES
            | ARMADA_LATER_TOOL_NAMES
            | ARMADA_PLUGIN_TOOL_NAMES
            and params.get("namespace") in {None, ""}
        ):
            return self.answer_armada_tool_call(params, turn_context=turn_context)
        if method == "item/tool/call":
            tool_name = params.get("tool")
            call_id = params.get("callId")
            if tool_name != "armada_artifact" or params.get("namespace") not in {None, ""}:
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
                document, encoded = canonicalize_armada_artifact(
                    arguments, allow_unresolved_media=True
                )
            except ArmadaArtifactError as error:
                detail = trim_armada_agent_text(str(error), limit=500)
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
                        **({"item_id": call_id} if isinstance(call_id, str) else {}),
                        "metadata": {
                            "tool_name": "armada_artifact",
                            "human_tool_result": True,
                        },
                    },
                )
            pending_artifacts = (
                turn_context.pending_artifacts
                if turn_context is not None
                else self.pending_artifacts
            )
            pending_artifacts.append(encoded)
            return (
                {
                    "success": True,
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": "Artifact accepted and attached to the final message.",
                        }
                    ],
                },
                None,
                {
                    "kind": "tool",
                    "status": "done",
                    "title": "Artifact ready.",
                    **({"item_id": call_id} if isinstance(call_id, str) else {}),
                    "metadata": {
                        "tool_name": "armada_artifact",
                        "human_tool_result": True,
                        "artifact_version": document["version"],
                        "artifact_bytes": len(encoded.encode()),
                        "artifact_blocks": len(document["blocks"]),
                        "artifact_nodes": armada_artifact_node_count(document),
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
                "message": f"Armada does not implement app-server request {method}.",
            },
            event,
        )

    def handle_notification(
        self,
        message: dict[str, Any],
        *,
        event_callback: ArmadaWorkerTranscriptCallback | None,
    ) -> list[ArmadaWorkerTranscriptEvent]:
        method = message.get("method")
        params = message.get("params")
        if method == "turn/started" and isinstance(params, dict):
            thread_id = params.get("threadId")
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if (
                isinstance(thread_id, str)
                and isinstance(turn_id, str)
                and thread_id == self.active_thread_id
            ):
                self.active_turn_id = turn_id
        if method == "turn/completed" and isinstance(params, dict):
            thread_id = params.get("threadId")
            if isinstance(thread_id, str) and thread_id == self.active_thread_id:
                self.active_turn_id = None
        events = self.translator.events_from_message(message)
        if event_callback is not None:
            for event in events:
                event_callback(event)
        return events

    def register_turn_context(self, context: ArmadaAppServerTurnContext) -> None:
        with self.state_lock:
            if context.thread_id in self.turn_contexts_by_thread_id:
                raise ArmadaAgentRuntimeError(
                    f"Codex thread {context.thread_id} already has an active agent turn."
                )
            self.turn_contexts_by_thread_id[context.thread_id] = context
            if not context.is_main:
                self.side_thread_ids.add(context.thread_id)
            for notification in self.unrouted_notifications.pop(context.thread_id, []):
                context.messages.put(notification)

    def bind_turn_context(self, context: ArmadaAppServerTurnContext, turn_id: str | None) -> None:
        if turn_id is None:
            return
        with self.state_lock:
            context.turn_id = turn_id
            self.turn_contexts_by_turn_id[turn_id] = context
            if context.is_main and context.thread_id == self.active_thread_id:
                self.active_turn_id = turn_id

    def unregister_turn_context(self, context: ArmadaAppServerTurnContext) -> None:
        with self.state_lock:
            if self.turn_contexts_by_thread_id.get(context.thread_id) is context:
                self.turn_contexts_by_thread_id.pop(context.thread_id, None)
            if (
                context.turn_id is not None
                and self.turn_contexts_by_turn_id.get(context.turn_id) is context
            ):
                self.turn_contexts_by_turn_id.pop(context.turn_id, None)
            if not context.is_main:
                self.side_thread_ids.discard(context.thread_id)

    def is_native_agent_descriptor(self, descriptor: dict[str, Any]) -> bool:
        return descriptor.get("thread_source") == "subagent" or (
            descriptor.get("agent_nickname") is not None
            and descriptor.get("parent_thread_id") is not None
        )

    def thread_params(self, *, existing_thread_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "approvalPolicy": self.approval_policy,
            "cwd": self.workdir,
            "ephemeral": self.ephemeral,
            "serviceTier": self.service_tier,
            "model": self.model,
            "sandbox": self.sandbox,
            "personality": "pragmatic",
            "dynamicTools": armada_codex_dynamic_tools(),
        }
        if existing_thread_id is not None:
            params["threadId"] = existing_thread_id
            params.pop("ephemeral", None)
            params.pop("dynamicTools", None)
            # Only the thread's id is read back. Without this, Codex sends
            # the thread's whole history, which it deprecates for paginated
            # threads.
            params["excludeTurns"] = True
        else:
            params["developerInstructions"] = build_armada_codex_developer_instructions(
                browser=bool(self.browser_dir), plugin_servers=self.plugin_servers
            )
        return {key: value for key, value in params.items() if value is not None}

    def turn_params(
        self, thread_id: str, prompt: str, *, client_user_message_id: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "clientUserMessageId": client_user_message_id,
            "approvalPolicy": self.approval_policy,
            "cwd": self.workdir,
            "serviceTier": self.service_tier,
            "effort": self.reasoning_effort,
            "model": self.model,
            "personality": "pragmatic",
        }
        return {key: value for key, value in params.items() if value is not None}

    def side_fork_params(self, parent_thread_id: str) -> dict[str, Any]:
        instructions = build_armada_codex_developer_instructions(
            browser=bool(self.browser_dir), plugin_servers=self.plugin_servers
        )
        developer_instructions = f"{instructions}\n\n{ARMADA_CODEX_SIDE_DEVELOPER_INSTRUCTIONS}"
        params: dict[str, Any] = {
            "threadId": parent_thread_id,
            "approvalPolicy": self.approval_policy,
            "cwd": self.workdir,
            "developerInstructions": developer_instructions,
            "ephemeral": True,
            # Codex 0.163.0 refuses an ephemeral fork that would send the
            # parent's history back ("ephemeral paginated thread/fork
            # requires excludeTurns: true"); only the fork's id is read.
            "excludeTurns": True,
            "serviceTier": self.service_tier,
            "model": self.model,
            "sandbox": self.sandbox,
        }
        return {key: value for key, value in params.items() if value is not None}

    def retire_side_thread(
        self,
        side_thread_id: str,
        *,
        conversation_key: str | None,
    ) -> Exception | None:
        cleanup_error: Exception | None = None
        try:
            self.request(
                "thread/unsubscribe",
                {"threadId": side_thread_id},
                event_callback=None,
            )
        except Exception as error:
            cleanup_error = error
        with self.state_lock:
            self.side_thread_ids.discard(side_thread_id)
            self.translator.thread_descriptors.pop(side_thread_id, None)
            self.unrouted_notifications.pop(side_thread_id, None)
            if conversation_key is not None:
                self.side_thread_ids_by_conversation_key.pop(conversation_key, None)
        return cleanup_error

    def evict_lru_side_conversation(self) -> None:
        with self.state_lock:
            retained_side_threads = list(self.side_thread_ids_by_conversation_key.items())
        for conversation_key, side_thread_id in retained_side_threads:
            with self.state_lock:
                if side_thread_id in self.turn_contexts_by_thread_id:
                    continue
            cleanup_error = self.retire_side_thread(
                side_thread_id,
                conversation_key=conversation_key,
            )
            if cleanup_error is not None:
                logger.warning(
                    "Could not unsubscribe evicted Codex side thread %s: %s",
                    side_thread_id,
                    cleanup_error,
                )
            return
        raise ArmadaAgentRuntimeError("Too many active native side conversations.")

    def run_side(
        self,
        active_session: ArmadaActiveSessionSummary,
        task: str,
        *,
        conversation_key: str | None = None,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
        user_input_provider: ArmadaUserInputProvider | None = None,
    ) -> Sequence[ArmadaWorkerToolResult]:
        """Run one turn in a native Codex ``/btw`` conversation.

        A stable key reuses the same ephemeral side thread across turns; no
        key preserves the one-shot behavior used by isolated callers.  Both
        paths share the parent's warm app-server transport. Request IDs and
        native thread/turn IDs isolate responses, transcript, tools, and
        artifacts while the parent remains live and steerable.
        """
        side_context: ArmadaAppServerTurnContext | None = None
        side_thread_id: str | None = None
        retain_side_thread = conversation_key is not None
        side_turn_completed = False
        created_side_thread = False
        with self.lock:
            self.ensure_started()
            # A registered main context is the live native parent regardless
            # of how far its durable Zulip cursor has advanced. Calling the
            # ordinary resume reconciliation here can mistake that temporary
            # cursor gap for a stale thread and reset_transport(), killing the
            # exact parent this /btw is meant to accompany.
            with self.state_lock:
                active_parent_thread_id = next(
                    (
                        context.thread_id
                        for context in self.turn_contexts_by_thread_id.values()
                        if context.is_main
                    ),
                    None,
                )
                in_memory_parent_thread_id = self.active_thread_id
            latest_thread_id = active_session.get("latest_codex_thread_id")
            if active_parent_thread_id is not None:
                parent_thread_id = active_parent_thread_id
            elif isinstance(latest_thread_id, str) and latest_thread_id.strip():
                # With no live main context, resolve an exact historical reply
                # root through the normal durable-session reconciliation.
                parent_thread_id = self.ensure_thread(active_session, event_callback=None)
            else:
                # The first parent can finish its native turn a moment before
                # its run persists the Codex id. Its process-local identity is
                # still canonical during that handoff window.
                parent_thread_id = in_memory_parent_thread_id or self.ensure_thread(
                    active_session,
                    event_callback=None,
                )
            with self.state_lock:
                side_thread_id = (
                    self.side_thread_ids_by_conversation_key.pop(conversation_key, None)
                    if conversation_key is not None
                    else None
                )
                if side_thread_id is not None and conversation_key is not None:
                    # Plain dict insertion order is the warm side-thread LRU.
                    self.side_thread_ids_by_conversation_key[conversation_key] = side_thread_id
            if side_thread_id is None:
                with self.state_lock:
                    side_conversation_limit_reached = (
                        conversation_key is not None
                        and len(self.side_thread_ids_by_conversation_key)
                        >= ARMADA_CODEX_WARM_SIDE_CONVERSATION_LIMIT
                    )
                if side_conversation_limit_reached:
                    self.evict_lru_side_conversation()
                fork_result = self.request(
                    "thread/fork",
                    self.side_fork_params(parent_thread_id),
                    event_callback=None,
                )
                side_thread_id = armada_app_server_thread_id_from_result(fork_result)
                if side_thread_id is None:
                    raise ArmadaAgentRuntimeError(
                        "Codex app-server did not return an ephemeral side thread id."
                    )
                created_side_thread = True
                if conversation_key is not None:
                    with self.state_lock:
                        self.side_thread_ids_by_conversation_key[conversation_key] = side_thread_id
            side_context = ArmadaAppServerTurnContext(
                thread_id=side_thread_id,
                event_callback=event_callback,
                user_input_provider=user_input_provider,
                is_main=False,
                requesting_user_id=active_session.get("requesting_user_id"),
                trigger_message_id=active_session.get("current_trigger_message_id"),
            )
            self.register_turn_context(side_context)
        assert side_context is not None
        try:
            if created_side_thread:
                self.request(
                    "thread/inject_items",
                    {
                        "threadId": side_thread_id,
                        "items": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": ARMADA_CODEX_SIDE_BOUNDARY_PROMPT,
                                    }
                                ],
                            }
                        ],
                    },
                    event_callback=None,
                )
            result = self.request(
                "turn/start",
                self.turn_params(side_thread_id, task),
                event_callback=event_callback,
            )
            turn_id = armada_app_server_turn_id_from_result(result)
            self.bind_turn_context(side_context, turn_id)
            final_text, _command_count, _file_change_count, _tool_call_count = self.wait_for_turn(
                thread_id=side_thread_id,
                turn_id=turn_id,
                event_callback=event_callback,
                turn_context=side_context,
            )
            results = armada_worker_results_from_final_text(
                final_text, output_limit=self.output_limit
            )
            results.extend(
                artifact_tool_result(encoded) for encoded in side_context.pending_artifacts
            )
            side_turn_completed = True
            return results
        finally:
            should_close_side_thread = not retain_side_thread or not side_turn_completed
            self.unregister_turn_context(side_context)
            cleanup_error = (
                self.retire_side_thread(
                    side_thread_id,
                    conversation_key=conversation_key,
                )
                if should_close_side_thread
                else None
            )
            if cleanup_error is not None:
                logger.warning(
                    "Could not unsubscribe ephemeral Codex side thread %s: %s",
                    side_thread_id,
                    cleanup_error,
                )
                if event_callback is not None:
                    event_callback(
                        {
                            "kind": "status",
                            "status": "done",
                            "title": "Side conversation cleanup needs attention.",
                            "body": trim_armada_agent_text(str(cleanup_error), limit=300),
                            "metadata": {
                                "codex_thread_id": side_thread_id,
                                "method": "thread/unsubscribe",
                            },
                        }
                    )

    def ensure_thread(
        self,
        active_session: ArmadaActiveSessionSummary,
        *,
        event_callback: ArmadaWorkerTranscriptCallback | None,
    ) -> str:
        latest_thread_id = active_session.get("latest_codex_thread_id")
        existing_thread_id = (
            latest_thread_id.strip()
            if isinstance(latest_thread_id, str) and latest_thread_id.strip()
            else None
        )
        predecessor_trigger_id = active_session.get("latest_codex_trigger_message_id")
        current_trigger_id = active_session.get("current_trigger_message_id")
        if not isinstance(predecessor_trigger_id, int) or isinstance(predecessor_trigger_id, bool):
            predecessor_trigger_id = None
        if not isinstance(current_trigger_id, int) or isinstance(current_trigger_id, bool):
            current_trigger_id = None
        thread_id = existing_thread_id
        if thread_id is not None and thread_id == self.loaded_thread_id:
            if self.loaded_through_trigger_message_id in {
                predecessor_trigger_id,
                current_trigger_id,
            }:
                return thread_id
            # Another worker advanced this durable thread.  A native resume in
            # a new app-server is the only safe way to refresh its in-memory
            # thread state; replaying transcript context would duplicate Codex.
            self.reset_transport()
            self.ensure_started()
        if thread_id is not None:
            result = self.request(
                "thread/resume",
                self.thread_params(existing_thread_id=thread_id),
                event_callback=event_callback,
            )
            resumed_thread_id = armada_app_server_thread_id_from_result(result) or thread_id
            self.loaded_thread_id = resumed_thread_id
            self.loaded_through_trigger_message_id = predecessor_trigger_id
            self.reconcile_descendant_threads(resumed_thread_id)
            return resumed_thread_id
        result = self.request(
            "thread/start",
            self.thread_params(),
            event_callback=event_callback,
        )
        started_thread_id = armada_app_server_thread_id_from_result(result)
        if started_thread_id is None:
            raise ArmadaAgentRuntimeError("Codex app-server did not return a thread id.")
        self.loaded_thread_id = started_thread_id
        self.loaded_through_trigger_message_id = None
        if event_callback is not None and self.thread_descriptor(started_thread_id) is None:
            event_callback(
                armada_thread_session_event(
                    {
                        "id": started_thread_id,
                        "parent_thread_id": None,
                        "agent_nickname": None,
                        "agent_role": None,
                        "name": None,
                    }
                )
            )
        return started_thread_id

    def reconcile_descendant_threads(
        self,
        root_thread_id: str,
        *,
        thread_ids: set[str] | None = None,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
    ) -> None:
        """Restore native child identities after an app-server restart.

        This is one runtime reconciliation request, never a frontend poller.
        The durable run event log remains the browser's source of truth.
        """
        result = self.request(
            "thread/list",
            {
                "ancestorThreadId": root_thread_id,
                "limit": 100,
                "sourceKinds": [
                    "subAgent",
                    "subAgentReview",
                    "subAgentCompact",
                    "subAgentThreadSpawn",
                    "subAgentOther",
                ],
                "useStateDbOnly": True,
            },
            event_callback=None,
        )
        threads = result.get("data") if isinstance(result, dict) else None
        if not isinstance(threads, list):
            return
        selected_thread_ids: set[str] | None = None
        if thread_ids is not None:
            selected_thread_ids = set(thread_ids)
            threads_by_id = {
                raw_thread_id: thread
                for thread in threads
                if isinstance(thread, dict) and isinstance((raw_thread_id := thread.get("id")), str)
            }
            pending = list(selected_thread_ids)
            while pending:
                selected_thread_id = pending.pop()
                selected_thread = threads_by_id.get(selected_thread_id)
                if selected_thread is None:
                    continue
                parent_thread_id = selected_thread.get("parentThreadId")
                if (
                    isinstance(parent_thread_id, str)
                    and parent_thread_id != root_thread_id
                    and parent_thread_id not in selected_thread_ids
                ):
                    selected_thread_ids.add(parent_thread_id)
                    pending.append(parent_thread_id)
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            thread_id = thread.get("id")
            if selected_thread_ids is not None and thread_id not in selected_thread_ids:
                continue
            with self.state_lock:
                events = self.translator.events_from_message(
                    {"method": "thread/started", "params": {"thread": thread}}
                )
            if event_callback is not None:
                for event in events:
                    event_callback(event)

    def thread_descriptor(self, thread_id: str) -> dict[str, Any] | None:
        with self.state_lock:
            return self.translator.thread_descriptors.get(thread_id)

    def child_thread_lineage(
        self, root_thread_id: str, child_thread_id: str
    ) -> list[dict[str, Any]]:
        with self.state_lock:
            lineage: list[dict[str, Any]] = []
            current_thread_id = child_thread_id
            seen: set[str] = set()
            while current_thread_id != root_thread_id and current_thread_id not in seen:
                seen.add(current_thread_id)
                descriptor = self.translator.thread_descriptors.get(current_thread_id)
                if descriptor is None:
                    return []
                lineage.append(descriptor)
                parent_thread_id = descriptor.get("parent_thread_id")
                if not isinstance(parent_thread_id, str):
                    return []
                current_thread_id = parent_thread_id
            if current_thread_id != root_thread_id:
                return []
            lineage.reverse()
            return lineage

    def prune_thread_maps_locked(self) -> None:
        """Forget, after a turn, the threads no later turn of this app-server
        can use: those outside the lineage of the loaded thread, the retained
        side conversations and any live turn, and beyond
        ARMADA_CODEX_RETAINED_THREAD_LIMIT the oldest idle ones. A busy
        topic's runtime otherwise kept every sub-agent it ever saw, with its
        last reply and its notifications nobody claimed."""
        descriptors = self.translator.thread_descriptors
        roots = {
            thread_id
            for thread_id in (
                self.loaded_thread_id,
                *self.side_thread_ids_by_conversation_key.values(),
                *self.turn_contexts_by_thread_id,
            )
            if thread_id
        }

        def ancestry(thread_id: str) -> list[str] | None:
            # The thread and its ancestors up to a root, or None when the
            # chain ends elsewhere.
            chain: list[str] = []
            current: str | None = thread_id
            while current is not None and current not in chain:
                chain.append(current)
                if current in roots:
                    return chain
                parent = descriptors.get(current, {}).get("parent_thread_id")
                current = parent if isinstance(parent, str) else None
            return None

        lineages = {
            thread_id: chain
            for thread_id in descriptors
            if (chain := ancestry(thread_id)) is not None
        }
        kept = list(lineages)
        if len(kept) > ARMADA_CODEX_RETAINED_THREAD_LIMIT:
            needed = set(roots)
            for thread_id, chain in lineages.items():
                status = descriptors[thread_id].get("status")
                if isinstance(status, dict) and status.get("type") == "active":
                    needed.update(chain)
            idle = [thread_id for thread_id in kept if thread_id not in needed]
            dropped = set(idle[: len(kept) - ARMADA_CODEX_RETAINED_THREAD_LIMIT])
            # A dropped thread takes its descendants with it, so no kept
            # thread loses the ancestry that routes its events.
            kept = [
                thread_id
                for thread_id in kept
                if not any(ancestor in dropped for ancestor in lineages[thread_id])
            ]
        kept_ids = set(kept)
        self.translator.thread_descriptors = {
            thread_id: descriptors[thread_id] for thread_id in kept
        }
        self.translator.latest_direct_assistant_body = {
            thread_id: body
            for thread_id, body in self.translator.latest_direct_assistant_body.items()
            if thread_id in kept_ids
        }
        # Notifications wait here for a thread no turn has claimed yet; those
        # of a forgotten thread never will be, and the rest are bounded.
        for thread_id in set(descriptors) - kept_ids:
            self.unrouted_notifications.pop(thread_id, None)
        waiting = list(self.unrouted_notifications)
        for thread_id in waiting[: max(len(waiting) - ARMADA_CODEX_RETAINED_THREAD_LIMIT, 0)]:
            del self.unrouted_notifications[thread_id]

    def active_descendant_thread_ids(self, root_thread_id: str) -> set[str]:
        with self.state_lock:
            active: set[str] = set()
            for thread_id, descriptor in self.translator.thread_descriptors.items():
                if thread_id == root_thread_id or not self.is_native_agent_descriptor(descriptor):
                    continue
                lineage = self.child_thread_lineage(root_thread_id, thread_id)
                if not lineage or any(
                    lineage_descriptor.get("id") in self.side_thread_ids
                    for lineage_descriptor in lineage
                ):
                    # /btw forks and anything they happen to spawn belong to
                    # the side turn. They must never extend the parent turn's
                    # native-agent completion barrier.
                    continue
                status = descriptor.get("status")
                status_type = status.get("type") if isinstance(status, dict) else None
                if status_type == "active":
                    active.add(thread_id)
            return active

    def next_turn_message(self, turn_context: ArmadaAppServerTurnContext) -> object:
        """The turn's next queued message. While it waits, the turn's own work
        falls due on time (armada_turn_idle_hook: Onix's words held back to
        pace the stream); Codex's silence still counts from the start."""
        idle_hook = armada_turn_idle_hook.get()
        silent_until = time.monotonic() + self.timeout_seconds
        while True:
            wait = silent_until - time.monotonic()
            due = idle_hook.idle_due_in() if idle_hook is not None else None
            try:
                return turn_context.messages.get(
                    timeout=max(0.0, wait if due is None else min(wait, due))
                )
            except queue.Empty as error:
                if idle_hook is None or time.monotonic() >= silent_until:
                    raise ArmadaAgentRuntimeError(
                        "Codex app-server produced no activity for "
                        f"{self.timeout_seconds:g} seconds."
                    ) from error
                idle_hook.on_idle()

    def wait_for_turn(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        event_callback: ArmadaWorkerTranscriptCallback | None,
        turn_context: ArmadaAppServerTurnContext | None = None,
    ) -> tuple[str, int, int, int]:
        final_text = ""
        command_count = 0
        file_change_count = 0
        tool_call_count = 0
        root_turn_completed = False
        # The error Codex reported for this thread's turn, unless it says
        # it will retry: what the run's place says if the turn ends on it.
        turn_error: ArmadaWorkerTranscriptEvent | None = None
        while True:
            if turn_context is not None:
                queued = self.next_turn_message(turn_context)
                abort_error = self.transport_abort_error
                if abort_error is not None:
                    raise abort_error
                if queued is ARMADA_APP_SERVER_ACTIVITY:
                    continue
                if queued is None:
                    raise ArmadaAgentRuntimeError("Codex app-server stopped unexpectedly.")
                assert isinstance(queued, ArmadaAppServerNotification)
                message = queued.message
                events = queued.events
                if event_callback is not None:
                    for event in events:
                        event_callback(event)
                if self.is_server_request(message):
                    # Server-request events are human-facing results for an
                    # interaction that the app-server also reports through its
                    # normal item lifecycle. The legacy synchronous path below
                    # returns early after handle_server_request for the same
                    # reason: keep the event visible without double-accounting
                    # it as a completed command/file/tool action.
                    continue
            else:
                next_message = self.next_message(self.timeout_seconds)
                if next_message is None:
                    raise ArmadaAgentRuntimeError("Codex app-server stopped unexpectedly.")
                message = next_message
                if self.handle_server_request(message, event_callback=event_callback):
                    continue
                if "method" not in message:
                    if self.consume_background_response(message):
                        continue
                    if "error" in message:
                        raise ArmadaAgentRuntimeError(
                            "Codex app-server failed: "
                            f"{trim_armada_agent_text(str(message['error']), limit=self.output_limit)}"
                        )
                    continue
                events = self.handle_notification(message, event_callback=event_callback)
            for event in events:
                metadata = event.get("metadata")
                event_thread_id = (
                    metadata.get("codex_thread_id") if isinstance(metadata, dict) else None
                )
                if isinstance(event_thread_id, str) and event_thread_id != thread_id:
                    continue
                if (
                    event.get("kind") == "error"
                    and isinstance(metadata, dict)
                    and metadata.get("codex_method") == "error"
                    and metadata.get("will_retry") is not True
                ):
                    turn_error = event
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
            if root_turn_completed and not self.active_descendant_thread_ids(thread_id):
                return final_text, command_count, file_change_count, tool_call_count
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
            if thread_id == self.active_thread_id:
                with self.state_lock:
                    self.active_turn_id = None
            completed_status = (
                completed_turn.get("status") if isinstance(completed_turn, dict) else None
            )
            if completed_status == "failed" or (
                completed_status != "interrupted" and turn_error is not None and not final_text
            ):
                # The turn ended on Codex's error: the run fails, saying
                # why, rather than ending done with no words.
                error_metadata = turn_error.get("metadata") if turn_error is not None else None
                raise ArmadaRunNoticeError(
                    armada_codex_error_notice(
                        str(turn_error.get("body", "")) if turn_error is not None else "",
                        error_metadata.get("codex_error_info")
                        if isinstance(error_metadata, dict)
                        else None,
                    )
                )
            root_turn_completed = True
            if not self.active_descendant_thread_ids(thread_id):
                return final_text, command_count, file_change_count, tool_call_count

    def __call__(
        self,
        active_session: ArmadaActiveSessionSummary,
        task: str,
        *,
        event_callback: ArmadaWorkerTranscriptCallback | None = None,
        user_input_provider: ArmadaUserInputProvider | None = None,
    ) -> Sequence[ArmadaWorkerToolResult]:
        with self.main_turn_lock:
            with self.lock:
                self.ensure_started()
                bootstrap_events: list[ArmadaWorkerTranscriptEvent] = []
                thread_id = self.ensure_thread(
                    active_session,
                    event_callback=bootstrap_events.append,
                )
                self.active_thread_id = thread_id
                self.active_turn_id = None
            persisted_child_thread_ids: set[str] = set()
            native_agent_thread_ids: set[str] = set()

            def run_event_callback(event: ArmadaWorkerTranscriptEvent) -> None:
                metadata = event.get("metadata")
                event_thread_id = (
                    metadata.get("codex_thread_id") if isinstance(metadata, dict) else None
                )
                receiver_thread_ids = (
                    metadata.get("receiver_thread_ids") if isinstance(metadata, dict) else None
                )
                if isinstance(event_thread_id, str) and event_thread_id != thread_id:
                    native_agent_thread_ids.add(event_thread_id)
                if isinstance(receiver_thread_ids, list):
                    native_agent_thread_ids.update(
                        receiver_thread_id
                        for receiver_thread_id in receiver_thread_ids
                        if isinstance(receiver_thread_id, str) and receiver_thread_id != thread_id
                    )
                if event_callback is None:
                    return
                if not isinstance(event_thread_id, str) or event_thread_id == thread_id:
                    event_callback(event)
                    return
                lineage = self.child_thread_lineage(thread_id, event_thread_id)
                if not lineage:
                    # The app-server process can hold other resumed roots. An
                    # event is part of this run only when its complete native
                    # parent chain reaches the active root.
                    return
                for descriptor in lineage:
                    descriptor_thread_id = descriptor.get("id")
                    if (
                        not isinstance(descriptor_thread_id, str)
                        or descriptor_thread_id in persisted_child_thread_ids
                    ):
                        continue
                    is_current_session_event = (
                        event.get("kind") == "session" and descriptor_thread_id == event_thread_id
                    )
                    if not is_current_session_event:
                        event_callback(armada_thread_session_event(descriptor))
                    persisted_child_thread_ids.add(descriptor_thread_id)
                event_callback(event)

            turn_context = ArmadaAppServerTurnContext(
                thread_id=thread_id,
                event_callback=run_event_callback,
                user_input_provider=user_input_provider,
                is_main=True,
                requesting_user_id=active_session.get("requesting_user_id"),
                trigger_message_id=active_session.get("current_trigger_message_id"),
            )
            self.register_turn_context(turn_context)
            try:
                # Resume/list requests can emit child notifications before the
                # active-turn router exists. Reconciliation above has now rebuilt
                # ancestry, so replay those bounded events through the same lineage
                # gate as live turn traffic.
                for bootstrap_event in bootstrap_events:
                    run_event_callback(bootstrap_event)

                prompt = build_armada_codex_turn_prompt(active_session, task)
                result = self.request(
                    "turn/start",
                    self.turn_params(
                        thread_id,
                        prompt,
                        client_user_message_id=active_session.get("client_user_message_id"),
                    ),
                    event_callback=run_event_callback,
                )
                turn_id = armada_app_server_turn_id_from_result(result)
                self.bind_turn_context(turn_context, turn_id)
                final_text, _command_count, _file_change_count, _tool_call_count = (
                    self.wait_for_turn(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        event_callback=run_event_callback,
                        turn_context=turn_context,
                    )
                )
                if native_agent_thread_ids:
                    self.reconcile_descendant_threads(
                        thread_id,
                        thread_ids=native_agent_thread_ids,
                        event_callback=run_event_callback,
                    )
            finally:
                self.unregister_turn_context(turn_context)
                with self.state_lock:
                    self.prune_thread_maps_locked()
            results = armada_worker_results_from_final_text(
                final_text, output_limit=self.output_limit
            )
            results.extend(
                artifact_tool_result(encoded) for encoded in turn_context.pending_artifacts
            )
            current_trigger_id = active_session.get("current_trigger_message_id")
            if isinstance(current_trigger_id, int) and not isinstance(current_trigger_id, bool):
                self.loaded_through_trigger_message_id = current_trigger_id
            return results

    def consume_background_response(self, message: dict[str, Any]) -> bool:
        """Swallow responses to fire-and-forget requests (interrupt):
        a late error reply must not fail an unrelated wait loop."""
        message_id = message.get("id")
        with self.state_lock:
            if not isinstance(message_id, int) or message_id not in self.background_request_ids:
                return False
            self.background_request_ids.discard(message_id)
        if "error" in message:
            logger.info(
                "Background app-server request %s failed: %s",
                message_id,
                trim_armada_agent_text(str(message["error"]), limit=300),
            )
        return True

    def steer(self, instruction: str, *, client_user_message_id: str | None = None) -> bool:
        """Steer the live turn, and say whether Codex took the input.

        Codex answers `turn/steer` with the turn's id, or with an error
        when the turn has just ended or cannot be steered. The caller
        keeps a refused instruction and gives it to the next turn, so it
        is never lost."""
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return False
            if self.active_thread_id is None or self.active_turn_id is None:
                return False
            params: dict[str, Any] = {
                "threadId": self.active_thread_id,
                "expectedTurnId": self.active_turn_id,
                "input": [{"type": "text", "text": instruction}],
            }
        if client_user_message_id is not None:
            params["clientUserMessageId"] = client_user_message_id
        # The reply arrives through the reader thread; turn notifications
        # wait on the turn's own queue meanwhile, in order.
        try:
            self.request(
                "turn/steer",
                params,
                timeout_seconds=ARMADA_APP_SERVER_STEER_REPLY_TIMEOUT_SECONDS,
            )
        except ArmadaAgentRuntimeError as error:
            logger.info("Codex refused a steer: %s", trim_armada_agent_text(str(error), limit=300))
            return False
        return True

    def interrupt(self) -> bool:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return False
            if self.active_thread_id is None or self.active_turn_id is None:
                return False
            request_id = self.next_id()
            with self.state_lock:
                self.background_request_ids.add(request_id)
            self.send_message(
                {
                    "id": request_id,
                    "method": "turn/interrupt",
                    "params": {
                        "threadId": self.active_thread_id,
                        "turnId": self.active_turn_id,
                    },
                }
            )
            return True

    def close(self) -> None:
        with self.lock:
            self.reset_transport()

    def abort(self, error: Exception) -> None:
        """Wake a blocked turn and stop its process without waiting on the turn lock."""
        messages = self.messages
        process = self.process
        self.transport_abort_error = error
        messages.put(None)
        self.signal_transport_closed()
        if process is not None:
            # Stopping waits up to the grace period; the caller must not.
            # The process is reaped there, never left a zombie until this
            # runtime is next used.
            threading.Thread(
                target=armada_stop_app_server_process,
                args=(process,),
                name="armada-codex-app-server-stop",
                daemon=True,
            ).start()

    def codex_thread_id(self) -> str:
        return self.loaded_thread_id or ""

    def runtime_value(self) -> str:
        return build_armada_runtime_value(
            "codex-app-server",
            workdir=self.workdir,
            sandbox=self.sandbox,
            approval_policy=self.approval_policy,
            model=self.model,
            service_tier=self.service_tier,
            reasoning_effort=self.reasoning_effort,
            process_isolation=self.process_isolation,
        )
