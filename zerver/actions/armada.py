from typing import Any

from zerver.lib.armada_agent_threads import (
    armada_agent_delta_from_event,
    serialize_armada_agent_threads,
)
from zerver.lib.message import event_recipient_ids_for_action_on_messages
from zerver.lib.stream_subscription import get_user_ids_for_streams
from zerver.models import Message, Stream
from zerver.models.bots import AgentRun, AgentRunEvent, Workspace
from zerver.tornado.django_api import send_event_on_commit


def _armada_run_plan(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    if payload.get("kind") != "plan":
        return None
    metadata = payload.get("metadata")
    steps = metadata.get("steps") if isinstance(metadata, dict) else None
    if not isinstance(steps, list):
        return None
    plan = [
        {"step": str(step["step"]), "status": str(step.get("status", "pending"))}
        for step in steps
        if isinstance(step, dict) and step.get("step")
    ]
    return plan or None


def _armada_run_usage(payload: dict[str, Any]) -> dict[str, int | float | None] | None:
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    usage: dict[str, int | float | None] = {}
    for key in (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        value = raw_usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            usage[key] = value
    cache_percent = raw_usage.get("cache_percent")
    if isinstance(cache_percent, int | float) and not isinstance(cache_percent, bool):
        usage["cache_percent"] = float(cache_percent)
    return usage or None


def _armada_agent_event_needs_snapshot(
    payload: dict[str, Any],
) -> bool:
    # Session/error events can establish or invalidate topology. Ordinary
    # lifecycle and activity events are bounded deltas for an existing child
    # and must not rebuild the full graph on the hot push path.
    return payload.get("kind") in {"error", "session"}


def send_armada_run_event(run: AgentRun, run_event: AgentRunEvent) -> None:
    """Push one reducer-friendly agent run delta to users who can see its trigger."""
    try:
        trigger_message = Message.objects.only("id", "is_channel_message").get(
            id=run.trigger_message_id
        )
    except Message.DoesNotExist:
        return

    if run.session_kind == AgentRun.SIDE and run.requesting_user_id is not None:
        recipient_ids = {run.requesting_user_id}
    else:
        recipient_ids = event_recipient_ids_for_action_on_messages(
            [trigger_message.id], trigger_message.is_channel_message
        )

    payload = run_event.payload if isinstance(run_event.payload, dict) else {}
    overview = payload.get("overview") if run_event.event_type == "presentation" else None
    duration_seconds = (
        round((run.date_finished - run.date_created).total_seconds(), 1)
        if run.date_finished is not None
        else None
    )
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    agent_thread_id = metadata.get("codex_thread_id") if run_event.event_type == "agent" else None
    agents = (
        serialize_armada_agent_threads(run)
        if run_event.event_type == "agent" and _armada_agent_event_needs_snapshot(payload)
        else None
    )
    agent = None if agents is not None else armada_agent_delta_from_event(run_event)
    event: dict[str, Any] = {
        "type": "agent_run",
        "version": 1,
        "sequence": run_event.id,
        "run_id": run.id,
        "event_type": run_event.event_type,
        "status": run.status,
        "trigger_message_id": run.trigger_message_id,
        "result_message_id": run.result_message_id,
        "stream": run.stream_name,
        "topic": run.topic_name,
        "summary": run_event.summary,
        "current_activity": run.current_activity,
        "tool_count": run.tool_count,
        "plan": _armada_run_plan(payload),
        "overview": overview if isinstance(overview, str) else None,
        "has_visible_assistant_text": (
            True if run_event.event_type == "assistant_visible" else None
        ),
        "usage": _armada_run_usage(payload),
        "duration_seconds": duration_seconds,
        "trace_available": run_event.event_type
        in {
            "cancelled",
            "done",
            "failed",
            "result_posted",
            "result_updated",
            "running",
            "steering_applied",
            "tool_results",
            "transcript",
            "agent",
        },
        "run_date_created": run.date_created.timestamp(),
        "date_created": run_event.date_created.timestamp(),
        "agent_thread_id": agent_thread_id if isinstance(agent_thread_id, str) else None,
    }
    if agent is not None:
        event["agent"] = agent
    if agents is not None:
        event["agents"] = agents
    send_event_on_commit(run.realm, event, list(recipient_ids))


def send_armada_workspace_changed_event(node: Workspace, revision: int) -> None:
    """Notify subscribers after a new authoritative Workspace revision exists."""
    stream = Stream.objects.filter(
        realm=node.realm,
        name__iexact=node.stream_name,
        deactivated=False,
    ).first()
    if stream is None:
        return
    recipient_ids = get_user_ids_for_streams({stream.id})[stream.id]
    send_event_on_commit(
        node.realm,
        {
            "type": "workspace_changed",
            "workspace_id": str(node.uuid),
            "revision": revision,
        },
        sorted(recipient_ids),
    )
