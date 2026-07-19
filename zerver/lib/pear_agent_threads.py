from collections.abc import Iterable

from django.db.models import Case, CharField, F, Q, Value, When, Window
from django.db.models.functions import RowNumber

from zerver.lib.event_types import PearRunAgent, PearRunAgentDelta
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models.bots import PearWorkerRun, PearWorkerRunEvent

PEAR_RUN_AGENT_THREAD_LIMIT = 16
PEAR_RUN_AGENT_SESSION_CANDIDATE_LIMIT = 64
PEAR_AGENT_LIFECYCLE_METHODS = (
    "thread/status/changed",
    "turn/started",
    "turn/completed",
)
PEAR_AGENT_TURN_METHODS = ("turn/started", "turn/completed")
PEAR_AGENT_ACTIVITY_KINDS = (
    "agent",
    "command",
    "error",
    "file_change",
    "plan",
    "tool",
)


def pear_agent_event_metadata(event: PearWorkerRunEvent) -> dict[str, object]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def normalize_pear_agent_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:limit] or None


def _normalized_timestamp(value: object, *, fallback: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return fallback


def normalize_pear_agent_status(value: object) -> str:
    if not isinstance(value, str):
        return "idle"
    normalized = value.strip().lower()
    if normalized in {"active", "busy", "inprogress", "running", "working"}:
        return "active"
    if normalized in {"cancelled", "error", "failed", "systemerror"}:
        return "failed"
    return "idle"


def pear_agent_delta_from_event(event: PearWorkerRunEvent) -> dict[str, object] | None:
    if event.event_type != "agent":
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    metadata = pear_agent_event_metadata(event)
    thread_id = normalize_pear_agent_text(metadata.get("codex_thread_id"), limit=200)
    if thread_id is None:
        return None

    values: dict[str, object] = {"thread_id": thread_id}
    method = metadata.get("codex_method")
    kind = payload.get("kind")
    if method == "thread/status/changed":
        values["status"] = normalize_pear_agent_status(metadata.get("thread_status"))
    elif method == "turn/started":
        values["status"] = "active"
    elif method == "turn/completed":
        turn_status = metadata.get("turn_status")
        values["status"] = (
            "failed" if normalize_pear_agent_status(turn_status) == "failed" else "idle"
        )
    if kind == "error":
        values["status"] = "failed"
    if kind in PEAR_AGENT_ACTIVITY_KINDS:
        activity = normalize_pear_agent_text(payload.get("title"), limit=180)
        if activity is not None:
            values["current_activity"] = activity
    return PearRunAgentDelta.model_validate(values).model_dump(exclude_unset=True)


def load_pear_agent_thread_events(run_ids: Iterable[int]) -> list[PearWorkerRunEvent]:
    """Load bounded descriptors and the latest reducer inputs for each child.

    Sessions establish the child graph.  The second query is partitioned by
    run, native thread, and reducer role so activity from a noisy sibling can
    never evict another child's terminal lifecycle event.
    """

    normalized_run_ids = list(dict.fromkeys(run_ids))
    if not normalized_run_ids:
        return []
    sessions = list(
        PearWorkerRunEvent.objects.filter(
            run_id__in=normalized_run_ids,
            event_type="agent",
            payload__kind="session",
        )
        .annotate(
            summary_rank=Window(
                expression=RowNumber(),
                partition_by=(F("run_id"),),
                order_by=(F("date_created").desc(), F("id").desc()),
            )
        )
        .filter(summary_rank__lte=PEAR_RUN_AGENT_SESSION_CANDIDATE_LIMIT)
        .only("id", "run_id", "event_type", "payload", "date_created")
    )
    thread_ids = {
        thread_id
        for event in sessions
        if isinstance((thread_id := pear_agent_event_metadata(event).get("codex_thread_id")), str)
    }
    if not thread_ids:
        return sessions

    reducer_events = list(
        PearWorkerRunEvent.objects.filter(
            Q(payload__metadata__codex_method__in=PEAR_AGENT_LIFECYCLE_METHODS)
            | Q(payload__kind__in=PEAR_AGENT_ACTIVITY_KINDS),
            run_id__in=normalized_run_ids,
            event_type="agent",
            payload__metadata__codex_thread_id__in=thread_ids,
        )
        .annotate(
            agent_reducer_role=Case(
                When(payload__kind="error", then=Value("error")),
                When(
                    payload__metadata__codex_method="thread/status/changed",
                    then=Value("thread_status"),
                ),
                When(
                    payload__metadata__codex_method__in=PEAR_AGENT_TURN_METHODS,
                    then=Value("turn"),
                ),
                default=Value("activity"),
                output_field=CharField(),
            )
        )
        .annotate(
            summary_rank=Window(
                expression=RowNumber(),
                partition_by=(
                    F("run_id"),
                    F("payload__metadata__codex_thread_id"),
                    F("agent_reducer_role"),
                ),
                order_by=(F("date_created").desc(), F("id").desc()),
            )
        )
        .filter(summary_rank=1)
        .only("id", "run_id", "event_type", "payload", "date_created")
    )
    return [*sessions, *reducer_events]


def _complete_visible_descriptor_ids(
    descriptors: dict[str, dict[str, object]], root_thread_id: str
) -> set[str]:
    """Choose newest descendants without returning an orphaned nested child."""

    selected: set[str] = set()
    newest_first = sorted(
        descriptors.values(),
        key=lambda descriptor: (
            float(descriptor.get("date_created") or 0),
            str(descriptor["thread_id"]),
        ),
        reverse=True,
    )
    for descriptor in newest_first:
        chain: list[str] = []
        thread_id = str(descriptor["thread_id"])
        seen: set[str] = set()
        while thread_id not in seen:
            seen.add(thread_id)
            current = descriptors.get(thread_id)
            if current is None:
                chain = []
                break
            chain.append(thread_id)
            parent_id = current["parent_thread_id"]
            if parent_id == root_thread_id:
                break
            if not isinstance(parent_id, str):
                chain = []
                break
            thread_id = parent_id
        else:
            chain = []
        if not chain:
            continue
        new_ids = set(chain) - selected
        if len(selected) + len(new_ids) > PEAR_RUN_AGENT_THREAD_LIMIT:
            continue
        selected.update(new_ids)
    return selected


def serialize_pear_agent_threads(
    run: PearWorkerRun,
    events: Iterable[PearWorkerRunEvent] | None = None,
) -> list[dict[str, object]]:
    """Reconstruct the bounded native Codex child graph from durable events."""

    candidates = list(events) if events is not None else load_pear_agent_thread_events([run.id])
    candidates = sorted(
        (event for event in candidates if event.event_type == "agent"),
        key=lambda event: event.id,
    )

    descriptors: dict[str, dict[str, object]] = {}
    for event in candidates:
        payload = event.payload if isinstance(event.payload, dict) else {}
        metadata = pear_agent_event_metadata(event)
        raw_thread = metadata.get("thread")
        event_timestamp = datetime_to_timestamp(event.date_created)
        if payload.get("kind") == "session" and isinstance(raw_thread, dict):
            thread_id = normalize_pear_agent_text(raw_thread.get("id"), limit=200)
            parent_thread_id = normalize_pear_agent_text(
                raw_thread.get("parent_thread_id"), limit=200
            )
            if (
                thread_id is not None
                and thread_id != run.codex_thread_id
                and parent_thread_id is not None
            ):
                status = raw_thread.get("status")
                raw_status = status.get("type") if isinstance(status, dict) else None
                descriptors[thread_id] = {
                    "thread_id": thread_id,
                    "parent_thread_id": parent_thread_id,
                    "nickname": normalize_pear_agent_text(
                        raw_thread.get("agent_nickname"), limit=100
                    ),
                    "role": normalize_pear_agent_text(raw_thread.get("agent_role"), limit=100),
                    "name": normalize_pear_agent_text(raw_thread.get("name"), limit=180),
                    "status": normalize_pear_agent_status(raw_status),
                    "current_activity": "",
                    "date_created": _normalized_timestamp(
                        raw_thread.get("created_at"), fallback=event_timestamp
                    ),
                }

        thread_id = metadata.get("codex_thread_id")
        descriptor = descriptors.get(thread_id) if isinstance(thread_id, str) else None
        if descriptor is None:
            continue
        method = metadata.get("codex_method")
        kind = payload.get("kind")
        if method == "thread/status/changed":
            thread_status = normalize_pear_agent_status(metadata.get("thread_status"))
            if thread_status != "idle" or descriptor["status"] != "failed":
                descriptor["status"] = thread_status
        elif method == "turn/started":
            descriptor["status"] = "active"
        elif method == "turn/completed":
            turn_status = metadata.get("turn_status")
            descriptor["status"] = (
                "failed" if normalize_pear_agent_status(turn_status) == "failed" else "idle"
            )
        if kind == "error":
            descriptor["status"] = "failed"
        title = payload.get("title")
        if kind in PEAR_AGENT_ACTIVITY_KINDS and isinstance(title, str):
            descriptor["current_activity"] = normalize_pear_agent_text(title, limit=180) or ""

    selected_ids = _complete_visible_descriptor_ids(descriptors, run.codex_thread_id)
    visible = [descriptor for key, descriptor in descriptors.items() if key in selected_ids]
    visible.sort(
        key=lambda descriptor: (
            float(descriptor.get("date_created") or 0),
            str(descriptor["thread_id"]),
        )
    )
    return [
        PearRunAgent.model_validate(
            {
                key: descriptor[key]
                for key in (
                    "thread_id",
                    "nickname",
                    "role",
                    "name",
                    "status",
                    "current_activity",
                )
            }
        ).model_dump()
        for descriptor in visible
    ]
