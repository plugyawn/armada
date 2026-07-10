"""The Wall: persistent monitor blocks.

A monitor is a small standing subroutine attached to a topic. A Codex
agent runs ONCE at setup (its own sibling thread — never the topic's
shared thread) and writes a spec: which wall components to render and a
shell recipe that refreshes them. After that the worker's tick thread
re-runs the recipe at cadence with no model involved, so a 20-second
cadence costs zero tokens. The agent only returns for a rebuild after
repeated recipe failures.
"""

import json
import logging
import random
import re
import shlex
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

from django.utils.timezone import now as timezone_now

from zerver.lib.pear_agent import PearProcessIsolation, pear_isolated_command
from zerver.models import PearMonitor, UserProfile

logger = logging.getLogger(__name__)

PEAR_MONITOR_MAX_PER_TOPIC: Final = 8
PEAR_MONITOR_MAX_PER_REALM: Final = 32
PEAR_MONITOR_MIN_CADENCE_SECONDS: Final = 5
PEAR_MONITOR_MAX_CADENCE_SECONDS: Final = 86400
PEAR_MONITOR_FAILURE_LIMIT: Final = 5
PEAR_MONITOR_RECIPE_TIMEOUT_SECONDS: Final = 8
PEAR_MONITOR_RECIPE_OUTPUT_LIMIT: Final = 64_000
PEAR_MONITOR_TICK_BATCH_LIMIT: Final = 16
PEAR_MONITOR_HISTORY_LIMIT: Final = 64
PEAR_MONITOR_SPEC_DIR: Final = ".pear/monitors"
PEAR_MONITOR_SETUP_STALE_SECONDS: Final = 1800

# Component catalog: prop names the validator accepts and the contract
# teaches. The client renders defensively, so prop VALUES are only
# lightly coerced here (numbers to float, strings trimmed).
PEAR_MONITOR_COMPONENTS: Final[dict[str, frozenset[str]]] = {
    "meter": frozenset({"label", "value", "total", "rate", "eta", "unit"}),
    "stat": frozenset({"label", "value", "delta", "unit"}),
    "sparkline": frozenset({"label", "value", "unit"}),
    "logtail": frozenset({"label", "lines", "limit"}),
    "badge": frozenset({"label", "state", "text"}),
    "clock": frozenset({"label", "timezone"}),
    "countdown": frozenset({"label", "target"}),
    "calendar": frozenset({"label", "highlights"}),
    "keyvals": frozenset({"label", "pairs"}),
}
PEAR_MONITOR_MAX_COMPONENTS: Final = 4
PEAR_MONITOR_PARSE_MODES: Final = frozenset({"text", "regex", "json", "static"})

# Client-ticked components need no recipe execution to stay alive.
PEAR_MONITOR_STATIC_COMPONENTS: Final = frozenset({"clock", "countdown", "calendar"})

PEAR_MONITOR_CONTRACT_PREFIX: Final = """\
You are setting up a Pear wall monitor: a small standing display block \
that keeps one thing visible next to a Zulip topic. You run once, now, \
to write its spec; after that a dumb scheduler re-runs your recipe on a \
cadence with no AI involved, so the recipe must do all the work.

Component catalog (the only block types that exist — never invent one):
- meter: progress toward a total. Props: label, value (number), total \
(number, optional; omit if value is already 0-100), rate (string), eta \
(string), unit.
- stat: one big number. Props: label, value, delta (signed number, \
optional), unit.
- sparkline: a numeric series over time; the scheduler keeps history \
automatically from the bound value. Props: label, value (number), unit.
- logtail: the last lines of a stream. Props: label, lines (list of \
strings), limit (number, default 8).
- badge: a status lamp. Props: label, state (ok|warn|err), text.
- clock: current time, ticks client-side. Props: label, timezone (IANA).
- countdown: time until a moment, ticks client-side. Props: label, \
target (ISO 8601 datetime).
- calendar: a month grid. Props: label, highlights (list of ISO dates).
- keyvals: small label:value rows. Props: label, pairs (list of \
[key, value] pairs).

Spec file schema (JSON):
{
  "title": "<wall label, at most 24 chars, lowercase preferred>",
  "cadence_seconds": <5..86400 — as slow as the data allows>,
  "block": {"components": [{"type": "<catalog type>", "props": {...}}]},
  "recipe": {
    "command": "<shell command run from the topic worktree, finishes \
fast, read-only in spirit>",
    "parse": {"mode": "text" | "regex" | "json" | "static",
              "pattern": "<regex with named groups, regex mode only>"},
    "bindings": {"<componentIndex.propName>": "<source>"}
  }
}
Binding sources: "group:<name>" (regex mode), "path:<dotted.json.path>" \
(json mode), "text" (entire output), "lines:<n>" (last n output lines, \
for logtail), "literal:<value>".

Rules:
1. Explore first (read-only) to find where the signal actually lives — \
a log file, a command, an API. Test your command and pattern against \
real output before writing the spec.
2. Prefer ONE component; use at most %(max_components)s. Labels stay \
under 16 characters.
3. The recipe command must be idempotent, finish within \
%(timeout_seconds)s seconds, write nothing, and print small output. For \
clock/countdown/calendar-only blocks use parse mode "static" and a \
slow cadence (they tick in the client).
4. Static props you know now go in block.components[].props; bindings \
overwrite props with fresh values every tick.
5. Write the spec as valid JSON to exactly this file, creating parent \
directories if needed: %(spec_path)s
6. Do not modify any other file. Do not commit. Do not post the spec \
in your reply — reply with one short sentence about what the monitor \
watches.
"""


def pear_monitor_spec_relative_path(monitor_id: int) -> str:
    return f"{PEAR_MONITOR_SPEC_DIR}/monitor-{monitor_id}.json"


def build_pear_monitor_contract(
    *,
    monitor_id: int,
    ask: str,
    stream: str,
    topic: str,
    repair_error: str = "",
) -> str:
    prefix = PEAR_MONITOR_CONTRACT_PREFIX % {
        "max_components": PEAR_MONITOR_MAX_COMPONENTS,
        "timeout_seconds": PEAR_MONITOR_RECIPE_TIMEOUT_SECONDS,
        "spec_path": pear_monitor_spec_relative_path(monitor_id),
    }
    lines = [
        prefix,
        f"Topic: {stream} > {topic}",
        f"Monitor request: {ask}",
    ]
    if repair_error:
        lines.append(
            "This monitor already existed but its recipe keeps failing; "
            f"diagnose and write a corrected spec. Last failure: {repair_error}"
        )
    return "\n".join(lines)


class PearMonitorSpecError(Exception):
    pass


def _clean_string(value: Any, *, limit: int) -> str:
    if not isinstance(value, str | int | float):
        raise PearMonitorSpecError("Expected a string value.")
    return str(value).strip()[:limit]


def pear_validate_monitor_spec(raw: Any) -> dict[str, Any]:
    """Validate an agent-written spec into (title, cadence, block, recipe)."""
    if not isinstance(raw, dict):
        raise PearMonitorSpecError("Spec must be a JSON object.")
    title = _clean_string(raw.get("title", ""), limit=24)
    cadence = raw.get("cadence_seconds", 20)
    if not isinstance(cadence, int | float):
        raise PearMonitorSpecError("cadence_seconds must be a number.")
    cadence = int(
        min(max(cadence, PEAR_MONITOR_MIN_CADENCE_SECONDS), PEAR_MONITOR_MAX_CADENCE_SECONDS)
    )

    block = raw.get("block")
    if not isinstance(block, dict) or not isinstance(block.get("components"), list):
        raise PearMonitorSpecError("block.components must be a list.")
    components = block["components"]
    if not 1 <= len(components) <= PEAR_MONITOR_MAX_COMPONENTS:
        raise PearMonitorSpecError(
            f"Between 1 and {PEAR_MONITOR_MAX_COMPONENTS} components required."
        )
    clean_components: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, dict):
            raise PearMonitorSpecError("Each component must be an object.")
        component_type = component.get("type")
        if component_type not in PEAR_MONITOR_COMPONENTS:
            raise PearMonitorSpecError(f"Unknown component type: {component_type!r}.")
        props = component.get("props", {})
        if not isinstance(props, dict):
            raise PearMonitorSpecError("Component props must be an object.")
        allowed = PEAR_MONITOR_COMPONENTS[component_type]
        unknown = set(props) - allowed
        if unknown:
            raise PearMonitorSpecError(
                f"Unknown props for {component_type}: {sorted(unknown)!r}."
            )
        clean_components.append({"type": component_type, "props": props})

    recipe = raw.get("recipe")
    if not isinstance(recipe, dict):
        raise PearMonitorSpecError("recipe must be an object.")
    command = _clean_string(recipe.get("command", ""), limit=2000)
    parse = recipe.get("parse")
    if not isinstance(parse, dict):
        raise PearMonitorSpecError("recipe.parse must be an object.")
    mode = parse.get("mode")
    if mode not in PEAR_MONITOR_PARSE_MODES:
        raise PearMonitorSpecError(f"Unknown parse mode: {mode!r}.")
    if mode != "static" and not command:
        raise PearMonitorSpecError("recipe.command is required unless parse mode is static.")
    pattern = ""
    if mode == "regex":
        pattern = _clean_string(parse.get("pattern", ""), limit=1000)
        try:
            re.compile(pattern)
        except re.error as error:
            raise PearMonitorSpecError(f"Invalid regex pattern: {error}") from error
    bindings = recipe.get("bindings", {})
    if not isinstance(bindings, dict):
        raise PearMonitorSpecError("recipe.bindings must be an object.")
    clean_bindings: dict[str, str] = {}
    for key, source in bindings.items():
        if not re.fullmatch(r"\d+\.[a-z_]+", str(key)):
            raise PearMonitorSpecError(f"Binding key must be index.prop: {key!r}.")
        index, prop = str(key).split(".", 1)
        if int(index) >= len(clean_components):
            raise PearMonitorSpecError(f"Binding index out of range: {key!r}.")
        component_type = clean_components[int(index)]["type"]
        if prop not in PEAR_MONITOR_COMPONENTS[component_type]:
            raise PearMonitorSpecError(f"Binding prop unknown for {component_type}: {prop!r}.")
        if not isinstance(source, str) or not re.fullmatch(
            r"(group:[A-Za-z_][A-Za-z0-9_]*|path:[\w.\[\]-]+|text|lines:\d+|literal:.*)",
            source,
        ):
            raise PearMonitorSpecError(f"Bad binding source: {source!r}.")
        clean_bindings[f"{index}.{prop}"] = source

    return {
        "title": title,
        "cadence_seconds": cadence,
        "block_spec": {"components": clean_components},
        "recipe": {
            "command": command,
            "parse": {"mode": mode, "pattern": pattern},
            "bindings": clean_bindings,
        },
    }


def pear_monitor_run_recipe_command(
    command: str,
    *,
    workdir: str,
    process_isolation: PearProcessIsolation | None,
) -> str:
    """Run a recipe command, isolation-aware, with a stripped environment
    so worker secrets never leak into agent-authored shell. The chdir
    happens inside the wrapped shell: isolation worktrees are mode-700,
    so a subprocess cwd= would EACCES before sudo ever runs."""
    wrapped = f"cd {shlex.quote(workdir)} && ({command})"
    argv = pear_isolated_command(["/usr/bin/env", "bash", "-c", wrapped], process_isolation)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": process_isolation.home if process_isolation is not None else str(Path.home()),
        "LANG": "C.UTF-8",
    }
    completed = subprocess.run(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PEAR_MONITOR_RECIPE_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise OSError(f"recipe exited {completed.returncode}: {detail[:300]}")
    return completed.stdout[:PEAR_MONITOR_RECIPE_OUTPUT_LIMIT]


def _json_path_lookup(data: Any, path: str) -> Any:
    current = data
    for part in re.split(r"\.|\[|\]", path):
        if part == "":
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            try:
                current = current[int(part)]
            except IndexError:
                return None
        else:
            return None
    return current


def pear_monitor_bind_props(recipe: dict[str, Any], output: str) -> dict[str, Any]:
    """Evaluate bindings against recipe output → {"<idx>.<prop>": value}."""
    parse = recipe.get("parse", {})
    mode = parse.get("mode", "text")
    groups: dict[str, str] = {}
    parsed_json: Any = None
    if mode == "regex":
        matches = list(re.finditer(parse.get("pattern", ""), output))
        if not matches:
            raise OSError("recipe output did not match the pattern")
        groups = {
            name: value
            for name, value in matches[-1].groupdict().items()
            if value is not None
        }
    elif mode == "json":
        try:
            parsed_json = json.loads(output)
        except json.JSONDecodeError as error:
            raise OSError(f"recipe output is not JSON: {error}") from error

    def coerce(value: Any) -> Any:
        if isinstance(value, str):
            trimmed = value.strip()
            try:
                return float(trimmed) if "." in trimmed else int(trimmed)
            except ValueError:
                return trimmed[:500]
        return value

    bound: dict[str, Any] = {}
    lines = output.splitlines()
    for key, source in recipe.get("bindings", {}).items():
        if source == "text":
            bound[key] = output.strip()[:2000]
        elif source.startswith("lines:"):
            count = min(int(source.removeprefix("lines:") or "8"), 50)
            bound[key] = [line[:300] for line in lines[-count:]]
        elif source.startswith("literal:"):
            bound[key] = source.removeprefix("literal:")
        elif source.startswith("group:"):
            bound[key] = coerce(groups.get(source.removeprefix("group:")))
        elif source.startswith("path:"):
            bound[key] = coerce(_json_path_lookup(parsed_json, source.removeprefix("path:")))
    return bound


def _advance_history(monitor: PearMonitor, bound: dict[str, Any]) -> list[float]:
    history = monitor.last_sample.get("history", [])
    if not isinstance(history, list):
        history = []
    components = monitor.block_spec.get("components", [])
    for index, component in enumerate(components):
        if component.get("type") == "sparkline":
            value = bound.get(f"{index}.value")
            if isinstance(value, int | float):
                history.append(float(value))
            break
    return history[-PEAR_MONITOR_HISTORY_LIMIT:]


def pear_monitor_apply_tick(
    monitor: PearMonitor,
    *,
    workdir: str,
    process_isolation: PearProcessIsolation | None,
) -> None:
    """Run one tick for a live monitor and persist the outcome."""
    now = timezone_now()
    recipe = monitor.recipe or {}
    try:
        if recipe.get("parse", {}).get("mode") == "static":
            bound: dict[str, Any] = {}
        else:
            output = pear_monitor_run_recipe_command(
                recipe.get("command", ""),
                workdir=workdir,
                process_isolation=process_isolation,
            )
            bound = pear_monitor_bind_props(recipe, output)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        monitor.consecutive_failures += 1
        monitor.last_error = str(error)[:500]
        if monitor.consecutive_failures >= PEAR_MONITOR_FAILURE_LIMIT:
            monitor.status = PearMonitor.ERROR
            monitor.next_due_at = None
        else:
            monitor.next_due_at = now + timedelta(seconds=monitor.cadence_seconds)
        monitor.date_updated = now
        monitor.save(
            update_fields=[
                "consecutive_failures",
                "last_error",
                "status",
                "next_due_at",
                "date_updated",
            ]
        )
        return

    monitor.last_sample = {
        "props": bound,
        "history": _advance_history(monitor, bound),
    }
    monitor.version += 1
    monitor.consecutive_failures = 0
    monitor.last_error = ""
    monitor.last_ok_at = now
    jitter = random.uniform(0, min(monitor.cadence_seconds * 0.1, 5))
    monitor.next_due_at = now + timedelta(seconds=monitor.cadence_seconds + jitter)
    monitor.date_updated = now
    monitor.save(
        update_fields=[
            "last_sample",
            "version",
            "consecutive_failures",
            "last_error",
            "last_ok_at",
            "next_due_at",
            "date_updated",
        ]
    )


def pear_run_due_monitor_ticks() -> int:
    """One scheduler pass; returns how many monitors ticked. Runs on the
    worker's tick thread — model-free by construction."""
    from zerver.lib.pear_worker import (
        pear_topic_context_is_paused,
        pear_topic_process_isolation,
        pear_worker_workdir_for_topic,
    )

    now = timezone_now()
    stale_setup_cutoff = now - timedelta(seconds=PEAR_MONITOR_SETUP_STALE_SECONDS)
    PearMonitor.objects.filter(
        status=PearMonitor.PROVISIONING, date_created__lt=stale_setup_cutoff
    ).update(status=PearMonitor.ERROR, last_error="Setup never completed.", date_updated=now)

    due = list(
        PearMonitor.objects.select_related("bot_profile")
        .filter(status=PearMonitor.LIVE, next_due_at__lte=now)
        .order_by("next_due_at")[:PEAR_MONITOR_TICK_BATCH_LIMIT]
    )
    ticked = 0
    for monitor in due:
        try:
            if pear_topic_context_is_paused(
                monitor.bot_profile, stream=monitor.stream_name, topic=monitor.topic_name
            ):
                monitor.next_due_at = now + timedelta(seconds=monitor.cadence_seconds)
                monitor.save(update_fields=["next_due_at"])
                continue
            workdir = pear_worker_workdir_for_topic(
                monitor.bot_profile, stream=monitor.stream_name, topic=monitor.topic_name
            )
            process_isolation = pear_topic_process_isolation(
                monitor.bot_profile, stream=monitor.stream_name, topic=monitor.topic_name
            )
            pear_monitor_apply_tick(
                monitor, workdir=workdir, process_isolation=process_isolation
            )
            ticked += 1
        except Exception:
            logger.exception("Pear monitor tick failed for monitor %s", monitor.id)
    return ticked


def pear_apply_monitor_setup_result(
    bot_profile: UserProfile,
    anchor_message_id: int,
    *,
    workdir: str,
    isolation_username: str | None,
) -> None:
    """Post-turn hook: after a MONITOR run settles, read the spec file
    the agent wrote in the worktree and bring the monitor live."""
    from zerver.lib.pear_worker import pear_read_bytes_path
    from zerver.models import PearWorkerRun

    monitor = PearMonitor.objects.filter(
        bot_profile=bot_profile, anchor_message_id=anchor_message_id
    ).first()
    if monitor is None:
        return
    now = timezone_now()
    spec_path = Path(workdir) / pear_monitor_spec_relative_path(monitor.id)
    try:
        raw_bytes = pear_read_bytes_path(
            spec_path, isolation_username=isolation_username, max_bytes=100_000
        )
        if not raw_bytes:
            raise PearMonitorSpecError("The agent did not write the spec file.")
        spec = pear_validate_monitor_spec(json.loads(raw_bytes))
    except (OSError, json.JSONDecodeError, PearMonitorSpecError) as error:
        monitor.status = PearMonitor.ERROR
        monitor.last_error = f"Setup failed: {error}"[:500]
        monitor.date_updated = now
        monitor.save(update_fields=["status", "last_error", "date_updated"])
        return

    run = PearWorkerRun.objects.filter(
        bot_profile=bot_profile, trigger_message_id=anchor_message_id
    ).first()
    monitor.title = spec["title"] or monitor.title
    monitor.cadence_seconds = spec["cadence_seconds"]
    monitor.block_spec = spec["block_spec"]
    monitor.recipe = spec["recipe"]
    monitor.status = PearMonitor.LIVE
    monitor.consecutive_failures = 0
    monitor.last_error = ""
    monitor.next_due_at = now
    if run is not None and run.codex_thread_id:
        monitor.codex_thread_id = run.codex_thread_id
    monitor.date_updated = now
    monitor.save(
        update_fields=[
            "title",
            "cadence_seconds",
            "block_spec",
            "recipe",
            "status",
            "consecutive_failures",
            "last_error",
            "next_due_at",
            "codex_thread_id",
            "date_updated",
        ]
    )


def pear_create_monitor_from_spec(
    *,
    realm: Any,
    bot_profile: UserProfile,
    creator: UserProfile,
    stream_name: str,
    topic_name: str,
    ask: str,
    raw_spec: Any,
    position: int,
) -> PearMonitor:
    """Create a LIVE monitor directly from a validated spec — the
    pin-to-wall path: a widget the agent already composed in chat needs
    no setup run, no DM anchor, no provisioning state."""
    spec = pear_validate_monitor_spec(raw_spec)
    return PearMonitor.objects.create(
        realm=realm,
        bot_profile=bot_profile,
        creator=creator,
        stream_name=stream_name,
        topic_name=topic_name,
        title=spec["title"] or ask[:24],
        ask=ask[:500],
        block_spec=spec["block_spec"],
        recipe=spec["recipe"],
        cadence_seconds=spec["cadence_seconds"],
        status=PearMonitor.LIVE,
        next_due_at=timezone_now(),
        position=position,
    )


def serialize_pear_monitor(monitor: PearMonitor) -> dict[str, Any]:
    return {
        "id": monitor.id,
        "title": monitor.title,
        "ask": monitor.ask,
        "status": monitor.status,
        "version": monitor.version,
        "cadence_seconds": monitor.cadence_seconds,
        "block_spec": monitor.block_spec,
        "props": monitor.last_sample.get("props", {}),
        "history": monitor.last_sample.get("history", []),
        "last_ok_at": monitor.last_ok_at.isoformat() if monitor.last_ok_at else None,
        "last_error": monitor.last_error,
        "position": monitor.position,
    }
