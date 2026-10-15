import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.cache import cache

from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ARMADA_EMBEDDED_BOT_SERVICE,
)
from zerver.lib.armada_presentation import (
    ARMADA_PRESENTATION_OVERVIEW_MAX_CHARS,
    build_armada_presentation_input,
    parse_armada_presentation_output,
)
from zerver.lib.armada_worker import (
    ARMADA_PRESENTATION_CADENCE_SECONDS,
    ARMADA_PRESENTATION_QUEUE_NAME,
    mark_armada_run_attended,
    maybe_enqueue_armada_run_presentation,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile
from zerver.models.bots import AgentRun, AgentRunEvent
from zerver.worker.armada_presentation import ArmadaPresentationWorker


class ArmadaPresentationTest(ZulipTestCase):
    def create_run(self) -> AgentRun:
        owner = self.example_user("hamlet")
        armada_bot = self.create_test_bot(
            "armada-presentation",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        return AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=armada_bot,
            requesting_user=owner,
            trigger_message_id=7001,
            result_message_id=7002,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.RUNNING,
            current_activity="Reading files",
        )

    def test_presentation_input_contains_only_visible_bounded_events(self) -> None:
        events = [
            SimpleNamespace(
                event_type="transcript",
                summary="Reasoning",
                payload={"kind": "reasoning", "body": "hidden"},
            ),
            SimpleNamespace(
                event_type="transcript",
                summary="Old command",
                payload={"kind": "command", "title": "Reading stale files", "body": "raw"},
            ),
            SimpleNamespace(
                event_type="transcript",
                summary="Plan updated",
                payload={
                    "kind": "plan",
                    "metadata": {"steps": [{"step": "Inspect", "status": "in_progress"}]},
                },
            ),
            SimpleNamespace(
                event_type="transcript",
                summary="Command",
                payload={"kind": "command", "title": "Reading four files"},
            ),
            SimpleNamespace(
                event_type="transcript",
                summary="Codex",
                payload={"kind": "assistant", "body": "I’m comparing the two edits."},
            ),
        ]

        input_text = build_armada_presentation_input(
            plan=[{"step": "Inspect", "status": "in_progress"}],
            events=events,  # type: ignore[arg-type]
        )

        presentation_input = json.loads(input_text)
        self.assertEqual(
            presentation_input["recent_events"],
            [
                {"kind": "command", "text": "Reading four files"},
                {"kind": "assistant_update", "text": "I’m comparing the two edits."},
            ],
        )
        self.assertNotIn("hidden", input_text)
        self.assertNotIn("raw", input_text)
        self.assertNotIn("stale", input_text)
        self.assertLessEqual(len(input_text), 3000)
        self.assertEqual(presentation_input["current_step"], "Inspect")
        self.assertEqual(
            parse_armada_presentation_output(json.dumps({"overview": "  Waiting   for tests.\n"})),
            "Waiting for tests",
        )
        self.assertEqual(
            parse_armada_presentation_output('{"overview":"fine","title":"Invented"}'),
            "",
        )
        clipped = parse_armada_presentation_output(
            json.dumps({"overview": "Compiling " + "a very long document " * 10})
        )
        self.assertLessEqual(len(clipped), ARMADA_PRESENTATION_OVERVIEW_MAX_CHARS)
        self.assertTrue(clipped.endswith("…"))
        self.assertEqual(
            parse_armada_presentation_output(
                json.dumps(
                    {"overview": "Counting tracked TypeScript files and comparing their totals"}
                )
            ),
            "Counting tracked TypeScript files and comparing…",
        )

    def test_presentation_queue_is_attended_and_cadence_gated(self) -> None:
        self.assertEqual(ARMADA_PRESENTATION_CADENCE_SECONDS, 5)
        cache.clear()
        with patch("zerver.lib.armada_worker.queue_json_publish_rollback_unsafe") as publish:
            self.assertFalse(maybe_enqueue_armada_run_presentation(17, 30))
            mark_armada_run_attended(17)
            self.assertTrue(maybe_enqueue_armada_run_presentation(17, 30))
            self.assertFalse(maybe_enqueue_armada_run_presentation(17, 31))

        publish.assert_called_once_with(
            ARMADA_PRESENTATION_QUEUE_NAME,
            {"run_id": 17, "source_event_id": 30},
        )

    def test_worker_publishes_overview_only_for_active_run_with_real_plan(self) -> None:
        run = self.create_run()
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Plan updated",
            payload={
                "kind": "plan",
                "metadata": {"steps": [{"step": "Inspect", "status": "in_progress"}]},
            },
        )
        source_event = AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Read files",
            payload={"kind": "command", "title": "Reading four files"},
        )
        mark_armada_run_attended(run.id)
        worker = object.__new__(ArmadaPresentationWorker)
        worker.client = Mock()
        worker.client.api_key = "test-key"
        worker.client.interact.return_value = '{"overview":"Reading four files."}'

        with patch("zerver.worker.armada_presentation.send_armada_run_event") as send_event:
            worker.consume({"run_id": run.id, "source_event_id": source_event.id})
            presentation_request = worker.client.interact.call_args.kwargs
            self.assertIn(
                "Never repeat or paraphrase current_step",
                presentation_request["system_instruction"],
            )
            self.assertEqual(
                json.loads(presentation_request["input_text"])["recent_events"],
                [{"kind": "command", "text": "Reading four files"}],
            )
            repeated_source_event = AgentRunEvent.objects.create(
                run=run,
                event_type="transcript",
                summary="Read files again",
                payload={"kind": "command", "title": "Reading four files"},
            )
            worker.consume({"run_id": run.id, "source_event_id": repeated_source_event.id})

        presentation = AgentRunEvent.objects.get(run=run, event_type="presentation")
        self.assertEqual(presentation.payload["overview"], "Reading four files")
        self.assertEqual(presentation.payload["source_event_id"], source_event.id)
        self.assertEqual(presentation.payload["current_step"], "Inspect")
        send_event.assert_called_once_with(run, presentation)

    def test_worker_drops_overview_if_plan_advances_during_request(self) -> None:
        run = self.create_run()
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Plan updated",
            payload={
                "kind": "plan",
                "metadata": {"steps": [{"step": "Inspect", "status": "in_progress"}]},
            },
        )
        source_event = AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Read files",
            payload={"kind": "command", "title": "Reading four files"},
        )
        mark_armada_run_attended(run.id)
        worker = object.__new__(ArmadaPresentationWorker)
        worker.client = Mock()
        worker.client.api_key = "test-key"

        def advance_plan(**kwargs: object) -> str:
            AgentRunEvent.objects.create(
                run=run,
                event_type="transcript",
                summary="Plan updated",
                payload={
                    "kind": "plan",
                    "metadata": {"steps": [{"step": "Summarize", "status": "in_progress"}]},
                },
            )
            return '{"overview":"Reading four files."}'

        worker.client.interact.side_effect = advance_plan
        with patch("zerver.worker.armada_presentation.send_armada_run_event") as send_event:
            worker.consume({"run_id": run.id, "source_event_id": source_event.id})

        self.assertFalse(AgentRunEvent.objects.filter(run=run, event_type="presentation").exists())
        send_event.assert_not_called()

    def test_worker_drops_overview_if_run_finishes_during_request(self) -> None:
        run = self.create_run()
        AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Plan updated",
            payload={
                "kind": "plan",
                "metadata": {"steps": [{"step": "Inspect", "status": "in_progress"}]},
            },
        )
        source_event = AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Read files",
            payload={"kind": "command", "title": "Reading four files"},
        )
        mark_armada_run_attended(run.id)
        worker = object.__new__(ArmadaPresentationWorker)
        worker.client = Mock()
        worker.client.api_key = "test-key"

        def finish_run(**kwargs: object) -> str:
            AgentRun.objects.filter(id=run.id).update(status=AgentRun.DONE)
            return '{"overview":"Reading four files."}'

        worker.client.interact.side_effect = finish_run
        with patch("zerver.worker.armada_presentation.send_armada_run_event") as send_event:
            worker.consume({"run_id": run.id, "source_event_id": source_event.id})

        self.assertFalse(AgentRunEvent.objects.filter(run=run, event_type="presentation").exists())
        send_event.assert_not_called()

    def test_worker_does_not_call_gemini_without_a_plan(self) -> None:
        run = self.create_run()
        source_event = AgentRunEvent.objects.create(
            run=run,
            event_type="transcript",
            summary="Read files",
            payload={"kind": "command", "title": "Reading four files"},
        )
        mark_armada_run_attended(run.id)
        worker = object.__new__(ArmadaPresentationWorker)
        worker.client = Mock()
        worker.client.api_key = "test-key"

        worker.consume({"run_id": run.id, "source_event_id": source_event.id})

        worker.client.interact.assert_not_called()
        self.assertFalse(AgentRunEvent.objects.filter(run=run, event_type="presentation").exists())
