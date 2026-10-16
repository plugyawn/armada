from collections.abc import Callable, Iterator
from contextlib import contextmanager
from itertools import pairwise
from typing import Any
from unittest.mock import patch

from typing_extensions import override

from zerver.lib import armada as armada_module
from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ArmadaActiveSessionSummary,
    ArmadaAnswerStreamSender,
    ArmadaEmbeddedBotHandler,
    ArmadaUserInputProvider,
    ArmadaWorkerRuntime,
    ArmadaWorkerToolResult,
    ArmadaWorkerTranscriptCallback,
    ArmadaWorkerTranscriptEvent,
    armada_turn_idle_hook,
    build_armada_active_session_summary_from_messages,
    get_armada_session_payload,
)
from zerver.lib.armada_agent import (
    ArmadaAgentRuntimeError,
    ArmadaAppServerTurnContext,
    ArmadaCodexAppServerToolRunner,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTestCase

TRIGGER_MESSAGE_ID = 9301
PROGRESS_MESSAGE_ID = 9302


class StreamRunStore:
    """The run store a turn talks to, keeping what the turn asked of it, and
    the words it streamed, in order with the run's events in `log`."""

    def __init__(self, clock: Callable[[], float] = lambda: 0.0) -> None:
        self.clock = clock
        self.log: list[str] = []
        self.streamed: list[dict[str, Any]] = []
        self.recorded_events: list[str] = []
        self.cancel = False
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
        self.log.append(event_type)

    def answer_stream_sender(
        self, bot_handler: object, message_id: int
    ) -> ArmadaAnswerStreamSender:
        def send(*, message_id: int, item: int, offset: int, text: str) -> None:
            self.streamed.append(
                {
                    "at": self.clock(),
                    "message_id": message_id,
                    "item": item,
                    "offset": offset,
                    "text": text,
                }
            )
            self.log.append("agent_stream")

        return send

    def update_metadata(self, bot_handler: object, message_id: int, **metadata: object) -> None:
        pass

    def cancel_requested(self, bot_handler: object, message_id: int) -> bool:
        self.cancel_reads += 1
        return self.cancel

    def steering_events_after(
        self, bot_handler: object, message_id: int, *, after_event_id: int
    ) -> list[object]:
        self.steering_reads += 1
        return []


class StreamBotHandler:
    """The agent's bot: Onix's message is edited in place, never posted again."""

    def __init__(self, clock: Callable[[], float] = lambda: 0.0) -> None:
        self.clock = clock
        self.edits: list[str] = []
        self.edited_at: list[float] = []

    def send_reply(self, message: dict[str, object], response: str) -> dict[str, int]:
        raise AssertionError("the progress message is edited in place")

    def update_message(self, message: dict[str, object]) -> None:
        self.edits.append(str(message["content"]))
        self.edited_at.append(self.clock())


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
        store = store or StreamRunStore(lambda: self.clock)
        bot_handler = bot_handler or StreamBotHandler(lambda: self.clock)

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

    def test_cancel_and_steering_are_read_twice_a_second_while_words_stream(self) -> None:
        words = [f"word{i} " for i in range(400)]
        store, _bot_handler = self.run_turn(self.stream_words(words, every=0.005))

        # 2 seconds of words: a read every 0.5 s, and one for each event
        # that is not a delta (the run's start, the item's end).
        self.assertLessEqual(store.cancel_reads, 8)
        self.assertLessEqual(store.steering_reads, 8)

    def test_an_event_that_is_not_a_delta_reads_cancel_at_once(self) -> None:
        store = StreamRunStore()

        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            event_callback(streamed("Reading "))
            reads = store.cancel_reads
            event_callback(
                {"kind": "command", "status": "running", "title": "git status", "body": ""}
            )
            self.assertEqual(store.cancel_reads, reads + 1)
            return "Reading done."

        self.run_turn(script, store=store)

    def test_a_cancel_stops_the_stream_within_half_a_second(self) -> None:
        store = StreamRunStore()
        sent = 0

        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            nonlocal sent
            for i in range(400):
                self.tick(0.005)
                if i == 100:
                    store.cancel = True
                event_callback(streamed(f"word{i} "))
                sent += 1
            raise AssertionError("the cancel was never read")

        _store, bot_handler = self.run_turn(script, store=store)

        self.assertGreaterEqual(sent, 100)
        self.assertLessEqual(sent - 100, 100)
        self.assertEqual(bot_handler.edits[-1], "Stopped.")

    def test_words_stream_as_new_characters_thirty_times_a_second(self) -> None:
        # 400 tokens a second for 3 seconds.
        words = [f"x{i % 10} " for i in range(1200)]
        store, bot_handler = self.run_turn(self.stream_words(words, every=0.0025))

        answer = "".join(words)
        self.assertEqual("".join(event["text"] for event in store.streamed), answer)
        self.assertEqual({event["message_id"] for event in store.streamed}, {PROGRESS_MESSAGE_ID})
        self.assertEqual({event["item"] for event in store.streamed}, {0})
        # Each event says where its characters go, and none is lost or doubled.
        offset = 0
        for event in store.streamed:
            self.assertEqual(event["offset"], offset)
            offset += len(event["text"])
        # The first words leave with the first delta; then at most one event
        # every 33 ms.
        self.assertEqual(store.streamed[0]["text"], words[0])
        self.assertAlmostEqual(store.streamed[0]["at"], 1000.0025)
        # The last goes with the item's end, whenever that comes.
        gaps = [
            b["at"] - a["at"] for a, b in zip(store.streamed, store.streamed[1:-1], strict=False)
        ]
        self.assertGreaterEqual(min(gaps), 0.033 - 1e-9)
        self.assertLessEqual(len(store.streamed), 3 * 30 + 2)
        self.assertEqual(bot_handler.edits[-1].strip(), answer.strip())

    def test_held_words_go_before_the_runs_next_event(self) -> None:
        store = StreamRunStore(lambda: self.clock)

        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            event_callback(streamed("Running "))
            self.tick(0.001)
            event_callback(streamed("the tests"))
            event_callback({"kind": "command", "status": "running", "title": "pytest", "body": ""})
            return "Running the tests"

        store, _bot_handler = self.run_turn(script, store=store)

        self.assertEqual([event["text"] for event in store.streamed], ["Running ", "the tests"])
        self.assertLess(
            store.log.index("agent_stream", store.log.index("agent_stream") + 1),
            store.log.index("transcript"),
        )

    def test_a_new_item_starts_its_text_again(self) -> None:
        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            event_callback(streamed("Checking ", item_id="a"))
            self.tick(0.1)
            event_callback(streamed("the parser.", item_id="a"))
            event_callback(completed("Checking the parser.", item_id="a"))
            self.tick(0.1)
            event_callback(streamed("Fixed", item_id="b"))
            self.tick(0.1)
            event_callback(completed("Fixed it.", item_id="b"))
            return "Fixed it."

        store, _bot_handler = self.run_turn(script)

        self.assertEqual(
            [(event["item"], event["offset"], event["text"]) for event in store.streamed],
            [
                (0, 0, "Checking "),
                (0, 9, "the parser."),
                (1, 0, "Fixed"),
                # Codex's whole text of the item is not what streamed: it
                # replaces the item's text.
                (1, 0, "Fixed it."),
            ],
        )

    def test_offsets_count_what_a_browser_counts(self) -> None:
        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            event_callback(streamed("Done \U0001f389"))
            self.tick(0.1)
            event_callback(streamed(" and pushed."))
            return "Done \U0001f389 and pushed."

        store, _bot_handler = self.run_turn(script)

        # The party popper is one character in Python and two in JavaScript.
        self.assertEqual([event["offset"] for event in store.streamed], [0, 7])

    def test_the_wait_for_codex_wakes_for_held_words(self) -> None:
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=5,
            output_limit=200,
        )
        context = ArmadaAppServerTurnContext(
            thread_id="thread-1", event_callback=None, user_input_provider=None, is_main=True
        )

        class Turn:
            def __init__(self) -> None:
                self.due: float | None = 0.01
                self.woken = 0

            def idle_due_in(self) -> float | None:
                return self.due

            def on_idle(self) -> None:
                self.woken += 1
                self.due = None
                context.messages.put("next")

        turn = Turn()
        token = armada_turn_idle_hook.set(turn)
        try:
            self.assertEqual(runner.next_turn_message(context), "next")
        finally:
            armada_turn_idle_hook.reset(token)
        self.assertEqual(turn.woken, 1)

    def test_the_wait_for_codex_still_ends_when_codex_is_silent(self) -> None:
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=0,
            output_limit=200,
        )
        context = ArmadaAppServerTurnContext(
            thread_id="thread-1", event_callback=None, user_input_provider=None, is_main=True
        )

        class Turn:
            woken = 0

            def idle_due_in(self) -> float | None:
                return 0.0

            def on_idle(self) -> None:
                self.woken += 1

        turn = Turn()
        token = armada_turn_idle_hook.set(turn)
        try:
            with self.assertRaises(ArmadaAgentRuntimeError):
                runner.next_turn_message(context)
        finally:
            armada_turn_idle_hook.reset(token)

    def test_the_message_is_saved_every_two_seconds_while_words_stream(self) -> None:
        # 400 tokens a second for 6 seconds (4,800 characters: one message).
        words = [f"{i % 10} " for i in range(2400)]
        words[-1] = "end."
        _store, bot_handler = self.run_turn(self.stream_words(words, every=0.0025))

        # The placeholder, the first words at once (the message stops
        # saying it is working), then a save every 2 s, then the end.
        self.assertEqual(bot_handler.edits[0], "· Working")
        self.assertEqual(bot_handler.edits[1], words[0].strip())
        saves = bot_handler.edited_at[1:-1]
        self.assert_length(saves, 3)
        gaps = [b - a for a, b in pairwise(saves)]
        self.assertGreaterEqual(min(gaps), 2.0)
        self.assertEqual(bot_handler.edits[-1].strip(), "".join(words).strip())

    def test_words_not_saved_yet_are_saved_when_codex_goes_quiet(self) -> None:
        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            event_callback(streamed("First words"))
            self.tick(0.5)
            event_callback(streamed(" and the rest of them."))
            # Codex says nothing more for a while: the turn's wait wakes when
            # the save is due.
            hook = armada_turn_idle_hook.get()
            assert hook is not None
            due = hook.idle_due_in()
            self.assertIsNotNone(due)
            assert due is not None
            self.tick(due)
            hook.on_idle()
            self.assertEqual(bot_handler.edits[-1], "First words and the rest of them.")
            self.assertIsNone(hook.idle_due_in())
            return "First words and the rest of them."

        bot_handler = StreamBotHandler(lambda: self.clock)
        self.run_turn(script, bot_handler=bot_handler)

    def test_a_refused_save_waits_its_turn(self) -> None:
        class RefusingBotHandler(StreamBotHandler):
            @override
            def update_message(self, message: dict[str, object]) -> None:
                super().update_message(message)
                if message["content"] == "First words":
                    raise JsonableError("Not now.")

        bot_handler = RefusingBotHandler(lambda: self.clock)

        def script(event_callback: ArmadaWorkerTranscriptCallback) -> str:
            event_callback(streamed("First words"))
            tries = len(bot_handler.edits)
            hook = armada_turn_idle_hook.get()
            assert hook is not None
            # Not again at once, however often the turn's wait wakes.
            for _ in range(5):
                hook.on_idle()
            self.assert_length(bot_handler.edits, tries)
            self.assertAlmostEqual(hook.idle_due_in() or 0, 2.0)
            return "First words"

        with self.assertLogs("zerver.lib.armada", level="WARNING"):
            self.run_turn(script, bot_handler=bot_handler)
