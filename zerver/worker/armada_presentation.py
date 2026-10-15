# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from typing_extensions import override

from zerver.actions.armada import send_armada_run_event
from zerver.lib.armada_agent import ArmadaAgentRuntimeError, ArmadaGeminiInteractionsClient
from zerver.lib.armada_presentation import (
    armada_presentation_current_step,
    armada_presentation_plan,
    build_armada_presentation_input,
    request_armada_presentation_overview,
)
from zerver.lib.armada_worker import ARMADA_PRESENTATION_QUEUE_NAME, armada_run_is_attended
from zerver.models.bots import AgentRun, AgentRunEvent
from zerver.worker.base import QueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)


@assign_queue(ARMADA_PRESENTATION_QUEUE_NAME)
class ArmadaPresentationWorker(QueueProcessingWorker):
    """Produce optional factual UI copy without entering the Codex runtime path."""

    PREFETCH = 1
    MAX_CONSUME_SECONDS = 5

    @override
    def setup(self) -> None:
        super().setup()
        self.client = ArmadaGeminiInteractionsClient(
            api_key=settings.ARMADA_GEMINI_INTERACTIONS_API_KEY,
            timeout_seconds=3,
            output_limit=48,
            url=settings.ARMADA_GEMINI_INTERACTIONS_URL,
        )

    @override
    def consume(self, event: dict[str, Any]) -> None:
        run_id = event.get("run_id")
        source_event_id = event.get("source_event_id")
        if not isinstance(run_id, int) or not isinstance(source_event_id, int):
            return
        if not self.client.api_key:
            return
        run = AgentRun.objects.filter(id=run_id).first()
        if (
            run is None
            or run.status not in {AgentRun.QUEUED, AgentRun.RUNNING}
            or not armada_run_is_attended(run.id)
        ):
            return
        plan = armada_presentation_plan(run)
        if plan is None:
            return
        current_step = armada_presentation_current_step(plan)
        previous = run.events.filter(event_type="presentation").order_by("-id").first()
        previous_payload = previous.payload if previous is not None else {}
        previous_source_id = (
            previous_payload.get("source_event_id") if isinstance(previous_payload, dict) else None
        )
        if isinstance(previous_source_id, int) and previous_source_id >= source_event_id:
            return
        source_events = list(
            reversed(list(run.events.filter(id__lte=source_event_id).order_by("-id")[:50]))
        )
        input_text = build_armada_presentation_input(
            plan=plan,
            events=source_events,
        )
        try:
            overview = request_armada_presentation_overview(
                self.client,
                model=settings.ARMADA_PRESENTATION_GEMINI_MODEL,
                thinking_level=settings.ARMADA_PRESENTATION_GEMINI_THINKING_LEVEL,
                input_text=input_text,
            )
        except ArmadaAgentRuntimeError:
            logger.warning("Presentation update failed", exc_info=True)
            return
        if not overview:
            return
        if previous is not None and previous.summary == overview:
            return
        with transaction.atomic(savepoint=False):
            run = AgentRun.objects.select_for_update().get(id=run.id)
            if run.status not in {
                AgentRun.QUEUED,
                AgentRun.RUNNING,
            } or not armada_run_is_attended(run.id):
                return
            latest_plan = armada_presentation_plan(run)
            if latest_plan is None or armada_presentation_current_step(latest_plan) != current_step:
                return
            presentation_event = AgentRunEvent.objects.create(
                run=run,
                event_type="presentation",
                summary=overview,
                payload={
                    "overview": overview,
                    "source_event_id": source_event_id,
                    "current_step": current_step,
                },
            )
        send_armada_run_event(run, presentation_event)
