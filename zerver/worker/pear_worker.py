# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from django.db import close_old_connections, reset_queries
from typing_extensions import override

from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.pear import (
    PearEmbeddedBotHandler,
    PearWorkerLeaseLostError,
    PearWorkerRuntime,
    pear_message_id,
    pear_message_stream,
    pear_message_topic,
)
from zerver.lib.pear_agent import PearProcessIsolation
from zerver.lib.pear_monitor import pear_apply_monitor_setup_result
from zerver.lib.pear_worker import (
    PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS,
    PEAR_WORKER_QUEUE_NAMES,
    PearDatabaseWorkerRunStore,
    build_pear_worker_runtime_from_settings,
    load_pear_active_session_from_zulip,
    maintain_pear_worker_run_liveness,
    pear_topic_process_isolation,
    pear_worker_home_slot,
    pear_worker_model_for_topic,
    pear_worker_process_isolation_error,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_sandbox_for_workdir,
    pear_worker_workdir_for_topic,
    reap_stale_pear_worker_runs,
    wait_for_redelivered_pear_worker_run,
)
from zerver.lib.queue import QUEUE_REDELIVERED_EVENT_KEY, QueueEventDiscardError, SimpleQueueClient
from zerver.models.bots import PearWorkerRun
from zerver.models.users import UserProfile, get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)

PEAR_WORKER_RUNTIME_CACHE_LIMIT = 4
PEAR_WORKER_RUNTIME_IDLE_SECONDS = 30 * 60
PearWorkerRuntimeKey = tuple[str, str, str]
pear_worker_runtimes: OrderedDict[PearWorkerRuntimeKey, PearWorkerRuntime] = OrderedDict()
pear_worker_runtime_last_used: dict[PearWorkerRuntimeKey, float] = {}


def pear_worker_slot_should_warm(
    worker_slot: int,
    bot_profile_id: int,
    *,
    stream: str,
    topic: str,
) -> bool:
    return worker_slot == pear_worker_home_slot(
        bot_profile_id,
        stream=stream,
        topic=topic,
    )


def close_pear_worker_runtime(runtime: PearWorkerRuntime) -> None:
    try:
        runtime.close()
    except Exception:
        logger.exception("Failed to close Pear worker runtime.")


def close_pear_worker_runtimes() -> None:
    runtimes = list(pear_worker_runtimes.values())
    pear_worker_runtimes.clear()
    pear_worker_runtime_last_used.clear()
    for runtime in runtimes:
        close_pear_worker_runtime(runtime)


def evict_pear_worker_runtime(runtime: PearWorkerRuntime) -> None:
    """Close one runtime and remove every process-local cache reference to it."""
    for runtime_key, candidate in list(pear_worker_runtimes.items()):
        if candidate is not runtime:
            continue
        pear_worker_runtimes.pop(runtime_key, None)
        pear_worker_runtime_last_used.pop(runtime_key, None)
    # Isolation failures are deliberately uncached, but stolen workers still
    # own and must close those app-server processes after the turn.
    close_pear_worker_runtime(runtime)


def cache_pear_worker_runtime(
    runtime_key: PearWorkerRuntimeKey,
    runtime: PearWorkerRuntime,
    *,
    current_time: float | None = None,
) -> None:
    existing_runtime = pear_worker_runtimes.pop(runtime_key, None)
    if existing_runtime is not None and existing_runtime is not runtime:
        close_pear_worker_runtime(existing_runtime)
    pear_worker_runtimes[runtime_key] = runtime
    pear_worker_runtime_last_used[runtime_key] = (
        time.monotonic() if current_time is None else current_time
    )
    while len(pear_worker_runtimes) > PEAR_WORKER_RUNTIME_CACHE_LIMIT:
        evicted_key, evicted_runtime = pear_worker_runtimes.popitem(last=False)
        pear_worker_runtime_last_used.pop(evicted_key, None)
        close_pear_worker_runtime(evicted_runtime)


def reap_idle_pear_worker_runtimes(*, current_time: float | None = None) -> None:
    now = time.monotonic() if current_time is None else current_time
    for runtime_key, last_used in list(pear_worker_runtime_last_used.items()):
        if now - last_used < PEAR_WORKER_RUNTIME_IDLE_SECONDS:
            continue
        runtime = pear_worker_runtimes.pop(runtime_key, None)
        pear_worker_runtime_last_used.pop(runtime_key, None)
        if runtime is not None:
            close_pear_worker_runtime(runtime)


def get_pear_worker_runtime(
    model: str,
    workdir: str,
    reasoning_effort: str,
    process_isolation: PearProcessIsolation | None,
    isolation_error: str | None,
) -> PearWorkerRuntime:
    sandbox = pear_worker_sandbox_for_workdir(workdir)
    isolation_label = process_isolation.label() if process_isolation is not None else ""
    runtime_key = (workdir, sandbox, isolation_label)
    current_time = time.monotonic()
    reap_idle_pear_worker_runtimes(current_time=current_time)
    if isolation_error is None:
        runtime = pear_worker_runtimes.get(runtime_key)
        if runtime is not None:
            pear_worker_runtimes.move_to_end(runtime_key)
            pear_worker_runtime_last_used[runtime_key] = current_time
            runtime.configure_turn(model=model, reasoning_effort=reasoning_effort)
            return runtime
    runtime = build_pear_worker_runtime_from_settings(
        model_override=model,
        workdir_override=workdir,
        reasoning_effort_override=reasoning_effort,
        sandbox_override=sandbox,
        process_isolation_override=process_isolation,
        process_isolation_error=isolation_error,
    )
    runtime.configure_turn(model=model, reasoning_effort=reasoning_effort)
    if isolation_error is None:
        cache_pear_worker_runtime(runtime_key, runtime, current_time=current_time)
    return runtime


@assign_queue("pear_worker")
class PearWorker(QueueProcessingWorker):
    # Codex turns can legitimately take minutes; the embedded_bots queue should
    # only dispatch to this worker, not own the runtime budget.
    MAX_CONSUME_SECONDS = None
    RERAISE_CONSUME_EXCEPTIONS = True
    PREFETCH = 1
    POOL_QUEUE_NAMES: tuple[str, ...] = PEAR_WORKER_QUEUE_NAMES

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
        reap_stale_pear_worker_runs(session_kinds=self.OWNED_SESSION_KINDS)
        self.active_run_id: int | None = None
        self.active_run_attempt: int | None = None
        self.active_runtime: PearWorkerRuntime | None = None
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
        return maintain_pear_worker_run_liveness(
            active_run_id=self.active_run_id,
            active_run_attempt=self.active_run_attempt,
            session_kinds=self.OWNED_SESSION_KINDS,
        )

    def run_liveness_loop(self) -> None:  # nocoverage
        close_old_connections()
        try:
            while not self.run_liveness_stop.wait(PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    self.run_liveness_pass()
                except PearWorkerLeaseLostError as error:
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
        self.run_liveness_thread.join(timeout=PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS)
        if self.run_liveness_thread.is_alive():
            logger.warning("Pear run liveness thread did not stop before worker shutdown.")

    @override
    def stop(self) -> None:  # nocoverage
        self.stop_liveness_loop()
        try:
            close_pear_worker_runtimes()
        finally:
            super().stop()

    @override
    def consume(self, event: dict[str, Any]) -> None:
        self.active_run_id = None
        self.active_run_attempt = None
        self.active_runtime = None
        try:
            self._consume(event)
        finally:
            self.active_run_id = None
            self.active_run_attempt = None
            self.active_runtime = None

    def _consume(self, event: dict[str, Any]) -> None:
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
            message_id = pear_message_id(message)
        if message_id <= 0:
            raise QueueEventDiscardError("Pear worker event has no valid trigger message id.")
        stream = str(event.get("stream") or pear_message_stream(message))
        topic = str(event.get("topic") or pear_message_topic(message))
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
        worker_run_store = PearDatabaseWorkerRunStore()
        if event.get("warm_only") is True:
            # Low-priority consumers may steal real turns to avoid head-of-line
            # blocking.  A speculative warm has no latency value once stolen,
            # and would create a duplicate process-local app-server instead.
            if not pear_worker_slot_should_warm(
                self.worker_slot,
                user_profile.id,
                stream=context_stream,
                topic=context_topic,
            ):
                return
            model = pear_worker_model_for_topic(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            reasoning_effort = pear_worker_reasoning_effort_for_topic(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            workdir = pear_worker_workdir_for_topic(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            process_isolation = pear_topic_process_isolation(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            isolation_error = pear_worker_process_isolation_error(
                process_isolation,
                workdir=workdir,
            )
            runtime = get_pear_worker_runtime(
                model,
                workdir,
                reasoning_effort,
                process_isolation,
                isolation_error,
            )
            active_session = load_pear_active_session_from_zulip(message, bot_handler)
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
            wait_for_redelivered_pear_worker_run(
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
            PearWorkerRun.objects.filter(
                bot_profile=user_profile,
                trigger_message_id=message_id,
                status=PearWorkerRun.RUNNING,
            )
            .values_list("id", "attempts")
            .get()
        )
        # Publish the attempt first so the liveness thread can never observe a
        # run id without its fencing token.
        self.active_run_attempt = active_run_attempt
        self.active_run_id = active_run_id
        model = pear_worker_model_for_topic(
            user_profile, stream=context_stream, topic=context_topic
        )
        reasoning_effort = pear_worker_reasoning_effort_for_topic(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        workdir = pear_worker_workdir_for_topic(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        process_isolation = pear_topic_process_isolation(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        isolation_error = pear_worker_process_isolation_error(
            process_isolation,
            workdir=workdir,
        )
        runtime = get_pear_worker_runtime(
            model,
            workdir,
            reasoning_effort,
            process_isolation,
            isolation_error,
        )
        self.active_runtime = runtime
        keep_runtime_warm = not self.POOL_QUEUE_NAMES or pear_worker_slot_should_warm(
            self.worker_slot,
            user_profile.id,
            stream=context_stream,
            topic=context_topic,
        )
        pear_handler = PearEmbeddedBotHandler(
            worker_runtime=runtime,
            active_session_loader=load_pear_active_session_from_zulip,
            worker_run_store=worker_run_store,
        )
        try:
            active_session = pear_handler.load_active_session(message, bot_handler, content)
            pear_handler.run_worker_turn(
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
                    pear_apply_monitor_setup_result(
                        user_profile,
                        message_id,
                        workdir=workdir,
                        isolation_username=(
                            process_isolation.username if process_isolation is not None else None
                        ),
                    )
                except Exception:
                    logger.exception("Pear monitor setup application failed.")
        finally:
            if not keep_runtime_warm:
                # A stolen turn may resume the native thread on this slot, but
                # only its stable home slot is allowed to retain a warm process.
                evict_pear_worker_runtime(runtime)
