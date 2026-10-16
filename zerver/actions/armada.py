import threading
import time
from collections import OrderedDict
from typing import Any

from zerver.lib.armada_agent_threads import armada_agent_delta_from_event
from zerver.lib.armada_run_card import armada_run_plan_from_payload
from zerver.lib.message import event_recipient_ids_for_action_on_messages
from zerver.lib.stream_subscription import get_user_ids_for_streams
from zerver.models import Message, Stream
from zerver.models.bots import AgentRun, AgentRunEvent, Workspace
from zerver.tornado.django_api import send_event_on_commit


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


# A live run pushes an event per transcript item, and its audience
# hardly changes while it runs, so its recipients are reused for a few
# seconds instead of costing 4 queries an event. A subscription change
# reaches a live run's events within that time. Only live runs are kept,
# and never more than the limit.
ARMADA_RUN_RECIPIENTS_SECONDS = 5
ARMADA_RUN_RECIPIENTS_LIMIT = 256

armada_run_recipients: OrderedDict[int, tuple[float, list[int]]] = OrderedDict()
armada_run_recipients_lock = threading.Lock()


def _armada_run_recipient_ids_now(run: AgentRun) -> list[int]:
    try:
        trigger_message = Message.objects.only("id", "is_channel_message").get(
            id=run.trigger_message_id
        )
    except Message.DoesNotExist:
        return []
    if run.session_kind == AgentRun.SIDE and run.requesting_user_id is not None:
        return [run.requesting_user_id]
    return sorted(
        event_recipient_ids_for_action_on_messages(
            [trigger_message.id], trigger_message.is_channel_message
        )
    )


def armada_run_recipient_ids(run: AgentRun) -> list[int]:
    """The people who receive this run's events: those who can see its
    trigger message, or only its requester for a side run; none once the
    trigger message is gone."""
    if run.status not in {AgentRun.QUEUED, AgentRun.RUNNING}:
        with armada_run_recipients_lock:
            armada_run_recipients.pop(run.id, None)
        return _armada_run_recipient_ids_now(run)
    now = time.monotonic()
    with armada_run_recipients_lock:
        kept = armada_run_recipients.get(run.id)
        if kept is not None and now < kept[0]:
            return list(kept[1])
    user_ids = _armada_run_recipient_ids_now(run)
    with armada_run_recipients_lock:
        armada_run_recipients[run.id] = (now + ARMADA_RUN_RECIPIENTS_SECONDS, user_ids)
        armada_run_recipients.move_to_end(run.id)
        while len(armada_run_recipients) > ARMADA_RUN_RECIPIENTS_LIMIT:
            armada_run_recipients.popitem(last=False)
    return user_ids


def send_armada_run_event(run: AgentRun, run_event: AgentRunEvent) -> None:
    """Push one reducer-friendly agent run delta to users who can see its trigger."""
    recipient_ids = armada_run_recipient_ids(run)
    if not recipient_ids:
        return

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
    # The run's card already holds the sub-agents as of this event.
    agents = (
        run.card.get("agents", [])
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
        "plan": armada_run_plan_from_payload(payload),
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
    send_event_on_commit(run.realm, event, recipient_ids)


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
