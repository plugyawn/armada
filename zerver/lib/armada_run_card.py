"""A run's card: what a run list shows of each run beyond its own
columns (its token usage, its plan, its overview and its sub-agents).

Each is a fact about the run's latest events, so it is kept on the run
(AgentRun.card, with the newest event's id in last_event_id) by
record_agent_run_event, the one way an event is written, in the same
transaction as the event. Listing runs then reads runs, never their
events.
"""

from collections.abc import Iterable
from typing import Any, cast

from django.db.models import F, Func, JSONField, Value
from django.db.models.functions import Greatest
from django.utils.timezone import now as timezone_now

from zerver.lib.armada import (
    ArmadaTokenUsage,
    armada_normalize_token_usage,
    armada_token_usage_summary,
    trim_armada_text,
)
from zerver.lib.armada_agent_threads import (
    apply_armada_agent_event,
    armada_agent_event_metadata,
    serialize_armada_agent_threads,
)
from zerver.lib.armada_presentation import armada_presentation_current_step
from zerver.models.bots import AgentRun, AgentRunEvent


class JSONBMerge(Func):
    """`card || changes`: the changed keys replace the stored ones in one
    UPDATE, and the others stay as they are."""

    template = "(%(expressions)s)"
    arg_joiner = " || "
    output_field = JSONField()


def armada_usage_from_event_payload(payload: dict[str, Any]) -> ArmadaTokenUsage | None:
    usage = armada_normalize_token_usage(payload.get("usage"))
    if usage is not None:
        return usage
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        usage = armada_normalize_token_usage(metadata.get("usage"))
        if usage is not None:
            return usage
    return armada_normalize_token_usage(payload)


def armada_run_plan_from_payload(payload: object) -> list[dict[str, str]] | None:
    """The checklist of a plan_update: the whole list each time, as Codex's
    update_plan sends it."""
    if not isinstance(payload, dict) or payload.get("kind") != "plan":
        return None
    metadata = payload.get("metadata")
    raw_steps = metadata.get("steps") if isinstance(metadata, dict) else None
    if not isinstance(raw_steps, list):
        return None
    steps = [
        {"step": str(step["step"]), "status": str(step.get("status", "pending"))}
        for step in raw_steps
        if isinstance(step, dict) and step.get("step")
    ]
    return steps or None


def armada_run_card_changes(run: AgentRun, event: AgentRunEvent) -> dict[str, Any]:
    """What this event, the run's newest, changes on the run's card."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    changes: dict[str, Any] = {}
    if event.event_type in {"usage", "transcript"}:
        usage = armada_usage_from_event_payload(payload)
        if usage is not None:
            changes["usage"] = {"tokens": usage, "event_id": event.id}
    if event.event_type == "transcript":
        plan = armada_run_plan_from_payload(payload)
        if plan is not None:
            changes["plan"] = plan
    if event.event_type == "presentation":
        changes["presentation"] = {
            "overview": payload.get("overview"),
            "current_step": payload.get("current_step"),
        }
    if event.event_type == "agent":
        if payload.get("kind") in {"error", "session"}:
            # A session or an error can change which threads there are.
            changes["agents"] = serialize_armada_agent_threads(run)
        else:
            thread_id = armada_agent_event_metadata(event).get("codex_thread_id")
            agents = [dict(agent) for agent in run.card.get("agents", [])]
            for agent in agents:
                if agent["thread_id"] == thread_id:
                    apply_armada_agent_event(agent, event)
                    changes["agents"] = agents
                    break
    return changes


def record_agent_run_event(
    run: AgentRun,
    event_type: str,
    *,
    summary: str = "",
    payload: dict[str, Any],
) -> AgentRunEvent:
    """Write one of the run's events, and keep the run's card, its newest
    event's id and its tool count up to date with it, in the caller's
    transaction. The run object is updated to match."""
    event = AgentRunEvent.objects.create(
        run=run, event_type=event_type, summary=summary, payload=payload
    )
    changes = armada_run_card_changes(run, event)
    # Writers can commit out of order; the newest event wins.
    update: dict[str, Any] = {"last_event_id": Greatest(F("last_event_id"), Value(event.id))}
    if changes:
        update["card"] = JSONBMerge(F("card"), Value(changes, output_field=JSONField()))
    tool_count = payload.get("tool_count")
    if event_type == "tool_results" and isinstance(tool_count, int):
        update["tool_count"] = F("tool_count") + tool_count
        run.tool_count += tool_count
    AgentRun.objects.filter(id=run.id).update(**update)
    run.last_event_id = max(run.last_event_id or 0, event.id)
    run.card = {**run.card, **changes}
    return event


def rebuild_armada_run_card(run: AgentRun, events: Iterable[AgentRunEvent]) -> dict[str, Any]:
    """The card that recording these events, the run's own in the order
    they were written, would have built."""
    ordered = sorted(events, key=lambda event: event.id)
    card: dict[str, Any] = {}
    for event in ordered:
        if event.event_type != "agent":
            card.update(armada_run_card_changes(run, event))
    if any(event.event_type == "agent" for event in ordered):
        card["agents"] = serialize_armada_agent_threads(run, ordered)
    return card


def serialize_armada_run_card(run: AgentRun) -> dict[str, object]:
    """The run list's view of the card: usage with its summary and the
    run's duration, the plan, the overview of the plan's current step,
    the tool count and the sub-agents."""
    card = run.card if isinstance(run.card, dict) else {}
    usage = card.get("usage")
    usage_payload: dict[str, object] | None = None
    if isinstance(usage, dict):
        tokens = cast(ArmadaTokenUsage, usage["tokens"])
        duration_seconds = None
        if run.date_finished is not None:
            duration_seconds = round((run.date_finished - run.date_created).total_seconds(), 1)
        elif run.status in {AgentRun.QUEUED, AgentRun.RUNNING}:
            duration_seconds = round((timezone_now() - run.date_created).total_seconds(), 1)
        usage_payload = {
            **tokens,
            "summary": armada_token_usage_summary(tokens),
            "duration_seconds": duration_seconds,
            "event_id": usage["event_id"],
        }
    plan = card.get("plan")
    overview = None
    presentation = card.get("presentation")
    if plan and isinstance(presentation, dict):
        text = presentation.get("overview")
        if (
            isinstance(text, str)
            and text.strip()
            and presentation.get("current_step") == armada_presentation_current_step(plan)
        ):
            overview = trim_armada_text(" ".join(text.split()), limit=72)
    return {
        "usage": usage_payload,
        "tool_count": run.tool_count,
        "plan": plan or None,
        "overview": overview,
        "agents": card.get("agents", []),
    }
