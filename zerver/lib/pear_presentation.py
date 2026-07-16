import json
from typing import Any

from zerver.lib.pear import trim_pear_text
from zerver.lib.pear_agent import PearGeminiInteractionsClient
from zerver.models.bots import PearWorkerRun, PearWorkerRunEvent

PEAR_PRESENTATION_OVERVIEW_MAX_CHARS = 72


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


def pear_presentation_event_line(event: PearWorkerRunEvent) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.event_type == "transcript":
        kind = payload.get("kind")
        if kind == "assistant":
            body = payload.get("body")
            return trim_pear_text(str(body), limit=800) if isinstance(body, str) else None
        if kind in {"command", "file_change", "status", "tool"}:
            return str(payload.get("title") or event.summary)
        return None
    if event.event_type == "tool_results":
        count = payload.get("tool_count")
        return f"Completed {count} tools." if isinstance(count, int) else "Tools completed."
    if event.event_type == "steering_applied":
        return f"Steering applied: {event.summary}"
    return None


def build_pear_presentation_input(
    *,
    plan: list[dict[str, str]],
    events: list[PearWorkerRunEvent],
) -> str:
    current_step = next(
        (item for item in plan if item.get("status") == "in_progress"),
        next((item for item in plan if item.get("status") == "pending"), plan[-1]),
    )
    event_lines = [line for event in events if (line := pear_presentation_event_line(event))]
    bounded_event_lines = [trim_pear_text(line, limit=120) for line in event_lines[-4:]]
    return json.dumps(
        {
            "current_step": trim_pear_text(str(current_step["step"]), limit=120),
            "recent_events": bounded_event_lines,
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
    if len(words) > 8:
        overview = f"{' '.join(words[:8])}…"
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
            "Write one live activity label from only the supplied current step and recent events. "
            "Use 3 to 8 words, begin with a present-progressive verb, and omit the subject and "
            "final punctuation. Example: 'Compiling the three-page manifold PDF'. Never invent "
            "reasoning, intent, progress, completion, or tool details."
        ),
        response_schema={
            "type": "object",
            "properties": {
                "overview": {
                    "type": "string",
                    "description": "A factual 3 to 8 word live activity label.",
                }
            },
            "required": ["overview"],
            "additionalProperties": False,
        },
        thinking_level=thinking_level,
    )
    return parse_pear_presentation_output(output)
