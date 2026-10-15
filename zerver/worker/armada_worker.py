# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from django.db import close_old_connections, reset_queries
from typing_extensions import override

from zerver.lib.armada import (
    ArmadaEmbeddedBotHandler,
    ArmadaWorkerLeaseLostError,
    ArmadaWorkerRuntime,
    armada_message_id,
    armada_message_stream,
    armada_message_topic,
)
from zerver.lib.armada_agent import ArmadaProcessIsolation
from zerver.lib.armada_monitor import armada_apply_monitor_setup_result
from zerver.lib.armada_worker import (
    ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS,
    ARMADA_WORKER_QUEUE_NAMES,
    ArmadaDatabaseWorkerRunStore,
    armada_topic_process_isolation,
    armada_worker_home_slot,
    armada_worker_model_for_topic,
    armada_worker_process_isolation_error,
    armada_worker_reasoning_effort_for_topic,
    armada_worker_sandbox_for_workdir,
    armada_worker_workdir_for_topic,
    build_armada_worker_runtime_from_settings,
    load_armada_active_session_from_zulip,
    maintain_armada_worker_run_liveness,
    reap_stale_armada_worker_runs,
    wait_for_redelivered_armada_worker_run,
)
from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.message_cache import MessageDict
from zerver.lib.queue import QUEUE_REDELIVERED_EVENT_KEY, QueueEventDiscardError, SimpleQueueClient
from zerver.models.bots import AgentRun
from zerver.models.messages import Message
from zerver.models.users import UserProfile, get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)

ARMADA_WORKER_RUNTIME_CACHE_LIMIT = 4
ARMADA_WORKER_RUNTIME_IDLE_SECONDS = 30 * 60
ARMADA_SIDE_PUMP_INTERVAL_SECONDS = 0.2
ArmadaWorkerRuntimeKey = tuple[str, str, str]
armada_worker_runtimes: OrderedDict[ArmadaWorkerRuntimeKey, ArmadaWorkerRuntime] = OrderedDict()
armada_worker_runtime_last_used: dict[ArmadaWorkerRuntimeKey, float] = {}


def armada_worker_slot_should_warm(
    worker_slot: int,
    bot_profile_id: int,
    *,
    stream: str,
    topic: str,
) -> bool:
    return worker_slot == armada_worker_home_slot(
        bot_profile_id,
        stream=stream,
        topic=topic,
    )


def close_armada_worker_runtime(runtime: ArmadaWorkerRuntime) -> None:
    try:
        runtime.close()
    except Exception:
        logger.exception("Failed to close Pear worker runtime.")


def close_armada_worker_runtimes() -> None:
    runtimes = list(armada_worker_runtimes.values())
    armada_worker_runtimes.clear()
    armada_worker_runtime_last_used.clear()
    for runtime in runtimes:
        close_armada_worker_runtime(runtime)


def evict_armada_worker_runtime(runtime: ArmadaWorkerRuntime) -> None:
    """Close one runtime and remove every process-local cache reference to it."""
    for runtime_key, candidate in list(armada_worker_runtimes.items()):
        if candidate is not runtime:
            continue
        armada_worker_runtimes.pop(runtime_key, None)
        armada_worker_runtime_last_used.pop(runtime_key, None)
    # Isolation failures are deliberately uncached, but stolen workers still
    # own and must close those app-server processes after the turn.
    close_armada_worker_runtime(runtime)


def cache_armada_worker_runtime(
    runtime_key: ArmadaWorkerRuntimeKey,
    runtime: ArmadaWorkerRuntime,
    *,
    current_time: float | None = None,
) -> None:
    existing_runtime = armada_worker_runtimes.pop(runtime_key, None)
    if existing_runtime is not None and existing_runtime is not runtime:
        close_armada_worker_runtime(existing_runtime)
    armada_worker_runtimes[runtime_key] = runtime
    armada_worker_runtime_last_used[runtime_key] = (
        time.monotonic() if current_time is None else current_time
    )
    while len(armada_worker_runtimes) > ARMADA_WORKER_RUNTIME_CACHE_LIMIT:
        evicted_key, evicted_runtime = armada_worker_runtimes.popitem(last=False)
        armada_worker_runtime_last_used.pop(evicted_key, None)
        close_armada_worker_runtime(evicted_runtime)


def reap_idle_armada_worker_runtimes(*, current_time: float | None = None) -> None:
    now = time.monotonic() if current_time is None else current_time
    for runtime_key, last_used in list(armada_worker_runtime_last_used.items()):
        if now - last_used < ARMADA_WORKER_RUNTIME_IDLE_SECONDS:
            continue
        runtime = armada_worker_runtimes.pop(runtime_key, None)
        armada_worker_runtime_last_used.pop(runtime_key, None)
        if runtime is not None:
            close_armada_worker_runtime(runtime)


def get_armada_worker_runtime(
    model: str,
    workdir: str,
    reasoning_effort: str,
    process_isolation: ArmadaProcessIsolation | None,
    isolation_error: str | None,
) -> ArmadaWorkerRuntime:
    sandbox = armada_worker_sandbox_for_workdir(workdir)
    isolation_label = process_isolation.label() if process_isolation is not None else ""
    runtime_key = (workdir, sandbox, isolation_label)
    current_time = time.monotonic()
    reap_idle_armada_worker_runtimes(current_time=current_time)
    if isolation_error is None:
        runtime = armada_worker_runtimes.get(runtime_key)
        if runtime is not None:
            armada_worker_runtimes.move_to_end(runtime_key)
            armada_worker_runtime_last_used[runtime_key] = current_time
            runtime.configure_turn(model=model, reasoning_effort=reasoning_effort)
            return runtime
    runtime = build_armada_worker_runtime_from_settings(
        model_override=model,
        workdir_override=workdir,
        reasoning_effort_override=reasoning_effort,
        sandbox_override=sandbox,
        process_isolation_override=process_isolation,
        process_isolation_error=isolation_error,
    )
    runtime.configure_turn(model=model, reasoning_effort=reasoning_effort)
    if isolation_error is None:
        cache_armada_worker_runtime(runtime_key, runtime, current_time=current_time)
    return runtime


@assign_queue("armada_worker")
class ArmadaWorker(QueueProcessingWorker):
    # Codex turns can legitimately take minutes; the embedded_bots queue should
    # only dispatch to this worker, not own the runtime budget.
    MAX_CONSUME_SECONDS = None
    RERAISE_CONSUME_EXCEPTIONS = True
    PREFETCH = 1
    POOL_QUEUE_NAMES: tuple[str, ...] = ARMADA_WORKER_QUEUE_NAMES

    # The session kinds this consumer owns; boot reaping must never
    # touch the other consumer's RUNNING rows.
    OWNED_SESSION_KINDS: tuple[str, ...] = ("shared", "side")

    @override
    def statistics_file_name(self) -> str:
        if not self.POOL_QUEUE_NAMES:
            return super().statistics_file_name()
        worker_slot = self.worker_num if self.worker_num is not None else 0
        return f"{self.queue_name}-{worker_slot}.stats"

    @override
    def setup(self) -> None:
        if self.POOL_QUEUE_NAMES:
            worker_slot = self.worker_num if self.worker_num is not None else 0
            if worker_slot < 0 or worker_slot >= len(self.POOL_QUEUE_NAMES):
                raise ValueError(f"invalid Pear worker slot: {worker_slot}")
            self.worker_slot = worker_slot
        super().setup()
        # The two worker slots share ownership.  Startup therefore obeys the
        # heartbeat lease instead of declaring another slot's fresh run dead.
        reap_stale_armada_worker_runs(session_kinds=self.OWNED_SESSION_KINDS)
        self.active_run_id: int | None = None
        self.active_run_attempt: int | None = None
        self.active_side_run_id: int | None = None
        self.active_side_run_attempt: int | None = None
        self.active_runtime: ArmadaWorkerRuntime | None = None
        self.side_pump_stop: threading.Event | None = None
        self.side_pump_thread: threading.Thread | None = None
        self.run_liveness_stop = threading.Event()
        self.run_liveness_thread = threading.Thread(
            target=self.run_liveness_loop,
            name=f"{self.queue_name}-run-liveness",
            daemon=True,
        )
        self.run_liveness_thread.start()

    @override
    def start(self) -> None:  # nocoverage
        if not self.POOL_QUEUE_NAMES:
            super().start()
            return
        assert isinstance(self.q, SimpleQueueClient)
        own_queue = self.POOL_QUEUE_NAMES[self.worker_slot]
        priorities = {
            own_queue: 10,
            **{queue_name: 0 for queue_name in self.POOL_QUEUE_NAMES if queue_name != own_queue},
        }
        self.initialize_statistics()
        self.q.start_json_consumers(
            priorities,
            lambda events: self.consume_single_event(events[0]),
            annotate_redelivery=True,
        )

    def run_liveness_pass(self) -> int:
        reaped_count = maintain_armada_worker_run_liveness(
            active_run_id=self.active_run_id,
            active_run_attempt=self.active_run_attempt,
            session_kinds=(),
        )
        side_run_id = getattr(self, "active_side_run_id", None)
        side_run_attempt = getattr(self, "active_side_run_attempt", None)
        try:
            maintain_armada_worker_run_liveness(
                active_run_id=side_run_id,
                active_run_attempt=side_run_attempt,
                session_kinds=(),
            )
        except ArmadaWorkerLeaseLostError as error:
            # A side turn has an independent durable lease. Its fenced run
            # store will suppress every stale write when the turn unwinds;
            # never abort the parent app-server merely because one /btw lost
            # ownership.
            if self.active_side_run_id == side_run_id:
                self.active_side_run_id = None
                self.active_side_run_attempt = None
            logger.warning("Pear side-run lease was lost: %s", error)
        return reaped_count + reap_stale_armada_worker_runs(session_kinds=self.OWNED_SESSION_KINDS)

    def run_liveness_loop(self) -> None:  # nocoverage
        close_old_connections()
        try:
            while not self.run_liveness_stop.wait(ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    self.run_liveness_pass()
                except ArmadaWorkerLeaseLostError as error:
                    runtime = self.active_runtime
                    if runtime is not None:
                        runtime.abort(error)
                    logger.warning(
                        "Pear worker lease was lost; aborting its Codex runtime: %s", error
                    )
                except Exception:
                    logger.exception("Pear run liveness pass failed.")
                finally:
                    reset_queries()
                    close_old_connections()
        finally:
            close_old_connections()

    def stop_liveness_loop(self) -> None:
        if not hasattr(self, "run_liveness_stop") or not hasattr(self, "run_liveness_thread"):
            return
        self.run_liveness_stop.set()
        self.run_liveness_thread.join(timeout=ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS)
        if self.run_liveness_thread.is_alive():
            logger.warning("Pear run liveness thread did not stop before worker shutdown.")

    def stop_side_pump(self, *, wait: bool, timeout: float | None = None) -> None:
        stop = getattr(self, "side_pump_stop", None)
        thread = getattr(self, "side_pump_thread", None)
        if stop is not None:
            stop.set()
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if timeout is not None and thread.is_alive():
                logger.warning("Pear side-turn pump did not stop before worker shutdown.")
        if thread is None or not thread.is_alive():
            self.side_pump_stop = None
            self.side_pump_thread = None

    @override
    def stop(self) -> None:  # nocoverage
        self.stop_side_pump(wait=False)
        self.stop_liveness_loop()
        runtime = getattr(self, "active_runtime", None)
        try:
            if runtime is not None:
                # An isolation failure can leave the active runtime uncached,
                # so closing the warm-runtime cache alone is not a complete
                # shutdown. Abort wakes both the parent and any native /btw
                # fork without waiting for either turn lock.
                runtime.abort(RuntimeError("Pear worker is shutting down."))
        finally:
            try:
                close_armada_worker_runtimes()
            finally:
                try:
                    self.stop_side_pump(wait=True, timeout=2)
                finally:
                    super().stop()

    @override
    def consume(self, event: dict[str, Any]) -> None:
        self.stop_side_pump(wait=True)
        self.active_run_id = None
        self.active_run_attempt = None
        self.active_side_run_id = None
        self.active_side_run_attempt = None
        self.active_runtime = None
        try:
            self._consume(event)
        finally:
            self.active_run_id = None
            self.active_run_attempt = None
            self.active_side_run_id = None
            self.active_side_run_attempt = None
            self.active_runtime = None

    def _consume(
        self,
        event: dict[str, Any],
        *,
        side_runtime: ArmadaWorkerRuntime | None = None,
    ) -> None:
        redelivered = event.pop(QUEUE_REDELIVERED_EVENT_KEY, False) is True
        user_profile_id = event.get("user_profile_id")
        if (
            not isinstance(user_profile_id, int)
            or isinstance(user_profile_id, bool)
            or user_profile_id <= 0
        ):
            raise QueueEventDiscardError("Pear worker event has no valid bot user id.")
        try:
            user_profile = get_user_profile_by_id(user_profile_id)
        except UserProfile.DoesNotExist as error:
            raise QueueEventDiscardError(
                f"Pear worker event references deleted bot user {user_profile_id}."
            ) from error
        bot_handler = EmbeddedBotHandler(user_profile)
        message = event.get("message")
        if not isinstance(message, dict):
            raise QueueEventDiscardError("Pear worker event has no message object.")
        content = str(event.get("content", "")).strip()
        message_id = event.get("message_id")
        if not isinstance(message_id, int):
            message_id = armada_message_id(message)
        if message_id <= 0:
            raise QueueEventDiscardError("Pear worker event has no valid trigger message id.")
        stream = str(event.get("stream") or armada_message_stream(message))
        topic = str(event.get("topic") or armada_message_topic(message))
        requesting_user_id = event.get("requesting_user_id")
        if not isinstance(requesting_user_id, int):
            requesting_user_id = None
        session_kind = str(event.get("session_kind") or "shared")
        if session_kind not in self.OWNED_SESSION_KINDS:
            raise QueueEventDiscardError(
                f"Pear worker queue cannot consume session kind {session_kind!r}."
            )
        source_stream = str(event.get("source_stream") or "")
        source_topic = str(event.get("source_topic") or "")
        source_message_id = event.get("source_message_id")
        if not isinstance(source_message_id, int):
            source_message_id = None
        progress_message_id = event.get("progress_message_id")
        if not isinstance(progress_message_id, int):
            progress_message_id = None
        context_stream = stream or source_stream
        context_topic = topic or source_topic
        if not context_stream:
            raise QueueEventDiscardError("Pear worker event has no channel context.")
        if session_kind == AgentRun.SIDE and side_runtime is None:
            # Never let an orphaned shared lease suppress this delivery. If
            # the parent really is live, its in-process pump owns the durable
            # side row and will use the exact same app-server instance.
            reap_stale_armada_worker_runs(
                bot_profile=user_profile,
                stream=context_stream,
                topic=context_topic,
                session_kinds=(AgentRun.SHARED,),
            )
            if AgentRun.objects.filter(
                bot_profile=user_profile,
                stream_name__iexact=context_stream,
                topic_name__iexact=context_topic,
                session_kind=AgentRun.SHARED,
                status=AgentRun.RUNNING,
            ).exists():
                # A competing Rabbit consumer may acknowledge its duplicate
                # delivery, but must not create a second process-local runtime
                # for the same worktree. The parent's still-unacked delivery
                # is the crash-recovery wakeup for this durable side row.
                return
        worker_run_store = ArmadaDatabaseWorkerRunStore()
        if event.get("warm_only") is True:
            # Low-priority consumers may steal real turns to avoid head-of-line
            # blocking.  A speculative warm has no latency value once stolen,
            # and would create a duplicate process-local app-server instead.
            if not armada_worker_slot_should_warm(
                self.worker_slot,
                user_profile.id,
                stream=context_stream,
                topic=context_topic,
            ):
                return
            model = armada_worker_model_for_topic(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            reasoning_effort = armada_worker_reasoning_effort_for_topic(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            workdir = armada_worker_workdir_for_topic(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            process_isolation = armada_topic_process_isolation(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            isolation_error = armada_worker_process_isolation_error(
                process_isolation,
                workdir=workdir,
            )
            runtime = get_armada_worker_runtime(
                model,
                workdir,
                reasoning_effort,
                process_isolation,
                isolation_error,
            )
            active_session = load_armada_active_session_from_zulip(message, bot_handler)
            try:
                runtime.warm(active_session)
            except Exception:
                logger.warning(
                    "Failed to warm Pear Codex thread for %s > %s.",
                    context_stream,
                    context_topic,
                    exc_info=True,
                )
            return
        if redelivered:
            wait_for_redelivered_armada_worker_run(
                bot_profile_id=user_profile.id,
                trigger_message_id=message_id,
            )
        if not worker_run_store.claim_for_run(
            bot_handler,
            message_id,
            stream=stream,
            topic=topic,
            redelivered=redelivered,
        ):
            return
        active_run_id, active_run_attempt = (
            # claim_for_run just committed the row protected by this model's
            # (bot_profile, trigger_message_id) uniqueness constraint.
            AgentRun.objects.filter(
                bot_profile=user_profile,
                trigger_message_id=message_id,
                status=AgentRun.RUNNING,
            )
            .values_list("id", "attempts")
            .get()
        )
        # Publish the attempt first so the liveness thread can never observe a
        # run id without its fencing token.
        if side_runtime is None:
            self.active_run_attempt = active_run_attempt
            self.active_run_id = active_run_id
        else:
            self.active_side_run_attempt = active_run_attempt
            self.active_side_run_id = active_run_id
        model = armada_worker_model_for_topic(
            user_profile, stream=context_stream, topic=context_topic
        )
        reasoning_effort = armada_worker_reasoning_effort_for_topic(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        workdir = armada_worker_workdir_for_topic(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        process_isolation = armada_topic_process_isolation(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        isolation_error = armada_worker_process_isolation_error(
            process_isolation,
            workdir=workdir,
        )
        runtime = side_runtime or get_armada_worker_runtime(
            model,
            workdir,
            reasoning_effort,
            process_isolation,
            isolation_error,
        )
        if side_runtime is None:
            self.active_runtime = runtime
        keep_runtime_warm = (
            side_runtime is not None
            or not self.POOL_QUEUE_NAMES
            or (
                armada_worker_slot_should_warm(
                    self.worker_slot,
                    user_profile.id,
                    stream=context_stream,
                    topic=context_topic,
                )
            )
        )
        armada_handler = ArmadaEmbeddedBotHandler(
            worker_runtime=runtime,
            active_session_loader=load_armada_active_session_from_zulip,
            worker_run_store=worker_run_store,
        )

        def run_parent_turn() -> None:
            active_session = armada_handler.load_active_session(message, bot_handler, content)
            armada_handler.run_worker_turn(
                message,
                bot_handler,
                content=content,
                active_session=active_session,
                message_id=message_id,
                stream=stream,
                topic=topic,
                post_started_progress=True,
                requesting_user_id=requesting_user_id,
                session_kind=session_kind,
                source_stream=source_stream,
                source_topic=source_topic,
                source_message_id=source_message_id,
                progress_message_id=progress_message_id,
            )
            if session_kind == "monitor":
                # The setup agent wrote its spec file in the worktree; read
                # it and bring the monitor live (or record why not).
                try:
                    armada_apply_monitor_setup_result(
                        user_profile,
                        message_id,
                        workdir=workdir,
                        isolation_username=(
                            process_isolation.username if process_isolation is not None else None
                        ),
                    )
                except Exception:
                    logger.exception("Pear monitor setup application failed.")

        try:
            if session_kind == AgentRun.SHARED and side_runtime is None:
                self.run_parent_with_side_pump(
                    runtime=runtime,
                    bot_profile_id=user_profile.id,
                    stream=context_stream,
                    topic=context_topic,
                    parent_turn=run_parent_turn,
                )
            else:
                run_parent_turn()
        finally:
            if side_runtime is not None:
                self.active_side_run_id = None
                self.active_side_run_attempt = None
            if not keep_runtime_warm:
                # A stolen turn may resume the native thread on this slot, but
                # only its stable home slot is allowed to retain a warm process.
                evict_armada_worker_runtime(runtime)

    def next_queued_side_run(
        self,
        *,
        bot_profile_id: int,
        stream: str,
        topic: str,
    ) -> AgentRun | None:
        return (
            AgentRun.objects.filter(
                bot_profile_id=bot_profile_id,
                stream_name__iexact=stream,
                topic_name__iexact=topic,
                session_kind=AgentRun.SIDE,
                status=AgentRun.QUEUED,
            )
            .order_by("date_created", "id")
            .first()
        )

    def side_event_for_run(self, run: AgentRun) -> dict[str, Any] | None:
        message = Message.objects.filter(id=run.trigger_message_id).first()
        if message is None:
            error = f"Pear side run trigger message {run.trigger_message_id} no longer exists."
            logger.warning(
                "Pear side run %s references missing trigger message %s; settling it.",
                run.id,
                run.trigger_message_id,
            )
            ArmadaDatabaseWorkerRunStore().fail_unreconstructable_side_run(
                run.id,
                error=error,
            )
            return None
        return {
            "user_profile_id": run.bot_profile_id,
            "message_id": run.trigger_message_id,
            "stream": run.stream_name,
            "topic": run.topic_name,
            "content": message.content,
            "message": MessageDict.wide_dict(message),
            "requesting_user_id": run.requesting_user_id,
            "session_kind": AgentRun.SIDE,
            "source_stream": run.source_stream_name,
            "source_topic": run.source_topic_name,
            "source_message_id": run.source_message_id,
            "progress_message_id": run.result_message_id,
        }

    def run_side_pump(
        self,
        *,
        stop: threading.Event,
        runtime: ArmadaWorkerRuntime,
        bot_profile_id: int,
        stream: str,
        topic: str,
        propagate_errors: bool = False,
    ) -> None:
        close_old_connections()
        try:
            while True:
                run = self.next_queued_side_run(
                    bot_profile_id=bot_profile_id,
                    stream=stream,
                    topic=topic,
                )
                if run is None:
                    if stop.is_set():
                        return
                    stop.wait(ARMADA_SIDE_PUMP_INTERVAL_SECONDS)
                    continue
                event = self.side_event_for_run(run)
                if event is None:
                    # The durable row was settled while reconstructing it.
                    continue
                try:
                    self._consume(event, side_runtime=runtime)
                except Exception:
                    logger.exception("Pear native side turn failed in the shared runtime pump.")
                    if propagate_errors:
                        raise
                    return
                finally:
                    reset_queries()
                    close_old_connections()
        finally:
            close_old_connections()

    def start_side_pump(
        self,
        *,
        runtime: ArmadaWorkerRuntime,
        bot_profile_id: int,
        stream: str,
        topic: str,
    ) -> None:
        self.stop_side_pump(wait=True)
        stop = threading.Event()
        thread = threading.Thread(
            target=self.run_side_pump,
            kwargs={
                "stop": stop,
                "runtime": runtime,
                "bot_profile_id": bot_profile_id,
                "stream": stream,
                "topic": topic,
            },
            name=f"{self.queue_name}-side-pump",
            daemon=True,
        )
        self.side_pump_stop = stop
        self.side_pump_thread = thread
        thread.start()

    def run_parent_with_side_pump(
        self,
        *,
        runtime: ArmadaWorkerRuntime,
        bot_profile_id: int,
        stream: str,
        topic: str,
        parent_turn: Callable[[], None],
    ) -> None:
        self.start_side_pump(
            runtime=runtime,
            bot_profile_id=bot_profile_id,
            stream=stream,
            topic=topic,
        )
        try:
            parent_turn()
        finally:
            # Drain every side row which became durable before the parent
            # settled, then release the shared runtime. This closes the race
            # between the final parent event and Rabbit delivery.
            stop = self.side_pump_stop
            self.stop_side_pump(wait=True)
            if stop is not None:
                # A broker consumer may already have acknowledged the side
                # delivery after seeing this active parent. Do one synchronous
                # source-of-truth drain after joining the pump; if DB access or
                # pre-turn setup still fails, fail the parent delivery too so
                # Rabbit redelivery can recover both durable rows.
                self.run_side_pump(
                    stop=stop,
                    runtime=runtime,
                    bot_profile_id=bot_profile_id,
                    stream=stream,
                    topic=topic,
                    propagate_errors=True,
                )
