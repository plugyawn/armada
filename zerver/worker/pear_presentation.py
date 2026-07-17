# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from typing_extensions import override

from zerver.actions.pear import send_pear_run_event
from zerver.lib.pear_agent import PearAgentRuntimeError, PearGeminiInteractionsClient
from zerver.lib.pear_presentation import (
    build_pear_presentation_input,
    pear_presentation_current_step,
    pear_presentation_plan,
    request_pear_presentation_overview,
)
from zerver.lib.pear_worker import PEAR_PRESENTATION_QUEUE_NAME, pear_run_is_attended
from zerver.models.bots import PearWorkerRun, PearWorkerRunEvent
from zerver.worker.base import QueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)


@assign_queue(PEAR_PRESENTATION_QUEUE_NAME)
class PearPresentationWorker(QueueProcessingWorker):
    """Produce optional factual UI copy without entering the Codex runtime path."""

    PREFETCH = 1
    MAX_CONSUME_SECONDS = 5

    @override
    def setup(self) -> None:
        super().setup()
        self.client = PearGeminiInteractionsClient(
            api_key=settings.PEAR_GEMINI_INTERACTIONS_API_KEY,
            timeout_seconds=3,
            output_limit=48,
            url=settings.PEAR_GEMINI_INTERACTIONS_URL,
        )

    @override
    def consume(self, event: dict[str, Any]) -> None:
        run_id = event.get("run_id")
        source_event_id = event.get("source_event_id")
        if not isinstance(run_id, int) or not isinstance(source_event_id, int):
            return
        if not self.client.api_key:
            return
        run = PearWorkerRun.objects.filter(id=run_id).first()
        if (
            run is None
            or run.status not in {PearWorkerRun.QUEUED, PearWorkerRun.RUNNING}
            or not pear_run_is_attended(run.id)
        ):
            return
        plan = pear_presentation_plan(run)
        if plan is None:
            return
        current_step = pear_presentation_current_step(plan)
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
        input_text = build_pear_presentation_input(
            plan=plan,
            events=source_events,
        )
        try:
            overview = request_pear_presentation_overview(
                self.client,
                model=settings.PEAR_PRESENTATION_GEMINI_MODEL,
                thinking_level=settings.PEAR_PRESENTATION_GEMINI_THINKING_LEVEL,
                input_text=input_text,
            )
        except PearAgentRuntimeError:
            logger.warning("Pear presentation update failed", exc_info=True)
            return
        if not overview:
            return
        if previous is not None and previous.summary == overview:
            return
        with transaction.atomic(savepoint=False):
            run = PearWorkerRun.objects.select_for_update().get(id=run.id)
            if run.status not in {
                PearWorkerRun.QUEUED,
                PearWorkerRun.RUNNING,
            } or not pear_run_is_attended(run.id):
                return
            latest_plan = pear_presentation_plan(run)
            if latest_plan is None or pear_presentation_current_step(latest_plan) != current_step:
                return
            presentation_event = PearWorkerRunEvent.objects.create(
                run=run,
                event_type="presentation",
                summary=overview,
                payload={
                    "overview": overview,
                    "source_event_id": source_event_id,
                    "current_step": current_step,
                },
            )
        send_pear_run_event(run, presentation_event)
