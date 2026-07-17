import json
from typing import Any

from zerver.lib.pear import trim_pear_text
from zerver.lib.pear_agent import PearGeminiInteractionsClient
from zerver.models.bots import PearWorkerRun, PearWorkerRunEvent

PEAR_PRESENTATION_OVERVIEW_MAX_CHARS = 48


def pear_presentation_plan(run: PearWorkerRun) -> list[dict[str, str]] | None:
    for event in run.events.filter(event_type="transcript", payload__kind="plan").order_by("-id")[
        :1
    ]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("kind") != "plan":
            continue
        metadata = payload.get("metadata")
        steps = metadata.get("steps") if isinstance(metadata, dict) else None
        if not isinstance(steps, list):
            continue
        plan = [
            {"step": str(step["step"]), "status": str(step.get("status", "pending"))}
            for step in steps
            if isinstance(step, dict) and step.get("step")
        ]
        if plan:
            return plan
    return None


def pear_presentation_current_step(plan: list[dict[str, str]]) -> str:
    current_step = next(
        (item for item in plan if item.get("status") == "in_progress"),
        next((item for item in plan if item.get("status") == "pending"), plan[-1]),
    )
    return trim_pear_text(str(current_step["step"]), limit=120)


def pear_presentation_event(event: PearWorkerRunEvent) -> dict[str, str] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.event_type == "transcript":
        kind = payload.get("kind")
        if kind == "assistant":
            body = payload.get("body")
            return (
                {"kind": "assistant_update", "text": trim_pear_text(body, limit=120)}
                if isinstance(body, str) and body.strip()
                else None
            )
        if kind in {"command", "file_change", "status", "tool"}:
            return {"kind": str(kind), "text": str(payload.get("title") or event.summary)}
        return None
    if event.event_type == "tool_results":
        count = payload.get("tool_count")
        return {
            "kind": "tool_result",
            "text": f"Completed {count} tools." if isinstance(count, int) else "Tools completed.",
        }
    if event.event_type == "steering_applied":
        return {"kind": "steering", "text": f"Steering applied: {event.summary}"}
    return None


def build_pear_presentation_input(
    *,
    plan: list[dict[str, str]],
    events: list[PearWorkerRunEvent],
) -> str:
    # A plan update changes the visible headline immediately. Events from the
    # previous step must not leak underneath it while the new step has not yet
    # done anything concrete.
    latest_plan_index = max(
        (
            index
            for index, event in enumerate(events)
            if event.event_type == "transcript"
            and isinstance(event.payload, dict)
            and event.payload.get("kind") == "plan"
        ),
        default=-1,
    )
    event_items = [
        item
        for event in events[latest_plan_index + 1 :]
        if (item := pear_presentation_event(event))
    ]
    bounded_events = [
        {"kind": item["kind"], "text": trim_pear_text(item["text"], limit=120)}
        for item in event_items[-4:]
    ]
    return json.dumps(
        {
            "current_step": pear_presentation_current_step(plan),
            "recent_events": bounded_events,
        },
        ensure_ascii=False,
    )


def parse_pear_presentation_output(output: str) -> str:
    try:
        payload: Any = json.loads(output)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict) or set(payload) != {"overview"}:
        return ""
    overview = payload.get("overview")
    if not isinstance(overview, str):
        return ""
    overview = " ".join(overview.split()).rstrip(".!?")
    words = overview.split()
    if len(words) > 6:
        overview = f"{' '.join(words[:6])}…"
    if len(overview) <= PEAR_PRESENTATION_OVERVIEW_MAX_CHARS:
        return overview
    clipped = overview[: PEAR_PRESENTATION_OVERVIEW_MAX_CHARS - 1].rsplit(" ", 1)[0]
    return f"{clipped or overview[: PEAR_PRESENTATION_OVERVIEW_MAX_CHARS - 1]}…"


def request_pear_presentation_overview(
    client: PearGeminiInteractionsClient,
    *,
    model: str,
    thinking_level: str | None,
    input_text: str,
) -> str:
    output = client.interact(
        model=model,
        input_text=input_text,
        system_instruction=(
            "The current step is already visible in the UI. Write only a compact drill-down of "
            "what is concretely happening now, using the newest supplied event. Prefer structured "
            "command, file_change, tool, or tool_result events over assistant_update prose. Never "
            "repeat or paraphrase current_step. Use 2 to 6 words, begin with a present-progressive "
            "verb, omit the subject and final punctuation, and return an empty overview when the "
            "events add no concrete detail. Good: 'Counting tracked TypeScript files'. Bad: "
            "'Inspecting the largest TypeScript files'. Never invent reasoning, intent, progress, "
            "completion, or tool details."
        ),
        response_schema={
            "type": "object",
            "properties": {
                "overview": {
                    "type": "string",
                    "description": (
                        "A factual 2 to 6 word current-operation label, or empty when no event "
                        "adds detail beyond the visible plan step."
                    ),
                }
            },
            "required": ["overview"],
            "additionalProperties": False,
        },
        thinking_level=thinking_level,
    )
    return parse_pear_presentation_output(output)
