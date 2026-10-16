from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import patch

from typing_extensions import override

from zerver.lib import armada as armada_module
from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ArmadaActiveSessionSummary,
    ArmadaEmbeddedBotHandler,
    ArmadaUserInputProvider,
    ArmadaWorkerRuntime,
    ArmadaWorkerToolResult,
    ArmadaWorkerTranscriptCallback,
    ArmadaWorkerTranscriptEvent,
    build_armada_active_session_summary_from_messages,
    get_armada_session_payload,
)
from zerver.lib.test_classes import ZulipTestCase

TRIGGER_MESSAGE_ID = 9301
PROGRESS_MESSAGE_ID = 9302


class StreamRunStore:
    """The run store a turn talks to, keeping what the turn asked of it."""

    def __init__(self) -> None:
        self.recorded_events: list[str] = []
        self.cancel_reads = 0
        self.steering_reads = 0

    def start(
        self,
        bot_handler: object,
        message_id: int,
        *,
        stream: str,
        topic: str,
        source_message_id: int | None = None,
    ) -> bool:
        return True

    def record(self, bot_handler: object, message_id: int, status: str, **kwargs: object) -> None:
        pass

    def record_event(
        self, bot_handler: object, message_id: int, event_type: str, **kwargs: object
    ) -> None:
        self.recorded_events.append(event_type)

    def update_metadata(self, bot_handler: object, message_id: int, **metadata: object) -> None:
        pass

    def cancel_requested(self, bot_handler: object, message_id: int) -> bool:
        self.cancel_reads += 1
        return False

    def steering_events_after(
        self, bot_handler: object, message_id: int, *, after_event_id: int
    ) -> list[object]:
        self.steering_reads += 1
        return []


class StreamBotHandler:
    """The agent's bot: Onix's message is edited in place, never posted again."""

    def __init__(self) -> None:
        self.edits: list[str] = []

    def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
        raise AssertionError("the progress message is edited in place")

    def update_message(self, message: dict[str, object]) -> None:
        self.edits.append(str(message["content"]))


def streamed(text: str, *, item_id: str = "item-1") -> ArmadaWorkerTranscriptEvent:
    return {
        "kind": "assistant",
        "status": "running",
        "title": "Codex",
        "body": text,
        "item_id": item_id,
        "metadata": {"streaming": True},
    }


def completed(text: str, *, item_id: str = "item-1") -> ArmadaWorkerTranscriptEvent:
    return {
        "kind": "assistant",
        "status": "done",
        "title": "Codex",
        "body": text,
        "item_id": item_id,
    }


class ArmadaStreamTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.clock = 1000.0

    def tick(self, seconds: float) -> None:
        self.clock += seconds

    @contextmanager
    def frozen_clock(self) -> Iterator[None]:
        # The worker's clock is the time module's: the turn reads it as
        # time.monotonic().
        with patch("time.monotonic", lambda: self.clock):
            yield

    def run_turn(
        self,
        script: Callable[[ArmadaWorkerTranscriptCallback], str],
        *,
        store: StreamRunStore | None = None,
        bot_handler: StreamBotHandler | None = None,
    ) -> tuple[StreamRunStore, StreamBotHandler]:
        """Run one turn whose runtime plays `script`, which sends the turn's
        events and returns the answer."""
        store = store or StreamRunStore()
        bot_handler = bot_handler or StreamBotHandler()

        def tool_runner(
            active_session: ArmadaActiveSessionSummary,
            task: str,
            *,
            event_callback: ArmadaWorkerTranscriptCallback | None = None,
            user_input_provider: ArmadaUserInputProvider | None = None,
        ) -> list[ArmadaWorkerToolResult]:
            assert event_callback is not None
            try:
                return [{"label": "Result", "value": script(event_callback)}]
            except AssertionError as error:
                # The turn takes any error for a failed run; the test fails.
                failed.append(error)
                raise

        active_session = build_armada_active_session_summary_from_messages(
            armada_session=get_armada_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(TRIGGER_MESSAGE_ID, "@Onix explain the option groups", False)],
        )
        message = {
            "id": TRIGGER_MESSAGE_ID,
            "type": "stream",
            "display_recipient": ARMADA_DEFAULT_STREAM,
            "topic": ARMADA_DEFAULT_TOPIC,
            "content": "@Onix explain the option groups",
        }
        failed: list[AssertionError] = []
        with self.frozen_clock():
            ArmadaEmbeddedBotHandler(
                worker_runtime=ArmadaWorkerRuntime(tool_runner),
                worker_run_store=store,
            ).run_worker_turn(
                message,
                bot_handler,
                content="@Onix explain the option groups",
                active_session=active_session,
                message_id=TRIGGER_MESSAGE_ID,
                stream=ARMADA_DEFAULT_STREAM,
                topic=ARMADA_DEFAULT_TOPIC,
                post_started_progress=True,
                progress_message_id=PROGRESS_MESSAGE_ID,
            )
        if failed:
            raise failed[0]
        return store, bot_handler

    def stream_words(
        self, words: list[str], *, every: float
    ) -> Callable[[ArmadaWorkerTranscriptCallback], str]:
        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            for word in words:
                self.tick(every)
                event_callback(streamed(word))
            answer = "".join(words)
            event_callback(completed(answer))
            return answer

        return script

    def test_a_delta_does_not_rescan_the_answer(self) -> None:
        words = [f"word{i} " for i in range(400)]
        with patch.object(
            armada_module,
            "armada_visible_assistant_text",
            wraps=armada_module.armada_visible_assistant_text,
        ) as visible_text:
            _store, bot_handler = self.run_turn(self.stream_words(words, every=0.001))

        # The whole answer is read when it is saved, not on each of its
        # 400 deltas: once for the first words, once when it is done.
        self.assertLessEqual(visible_text.call_count, 3)
        self.assertEqual(bot_handler.edits[-1].strip(), "".join(words).strip())
