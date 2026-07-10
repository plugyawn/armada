# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
import threading
from collections import OrderedDict
from typing import Any

from django.db import reset_queries
from typing_extensions import override

from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.pear import (
    PearEmbeddedBotHandler,
    PearWorkerRuntime,
    pear_message_id,
    pear_message_stream,
    pear_message_topic,
)
from zerver.lib.pear_agent import PearProcessIsolation
from zerver.lib.pear_monitor import (
    pear_apply_monitor_setup_result,
    pear_run_due_monitor_ticks,
)
from zerver.lib.pear_worker import (
    PearDatabaseWorkerRunStore,
    build_pear_worker_runtime_from_settings,
    load_pear_active_session_from_zulip,
    pear_topic_process_isolation,
    pear_worker_process_isolation_error,
    pear_worker_model_for_topic,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_sandbox_for_workdir,
    pear_worker_workdir_for_topic,
    reap_stale_pear_worker_runs,
)
from zerver.models.users import get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)

PEAR_WORKER_RUNTIME_CACHE_LIMIT = 8
PearWorkerRuntimeKey = tuple[str, str, str, str, str]
pear_worker_runtimes: OrderedDict[PearWorkerRuntimeKey, PearWorkerRuntime] = OrderedDict()


def close_pear_worker_runtime(runtime: PearWorkerRuntime) -> None:
    try:
        runtime.close()
    except Exception:
        logger.exception("Failed to close Pear worker runtime.")


def close_pear_worker_runtimes() -> None:
    runtimes = list(pear_worker_runtimes.values())
    pear_worker_runtimes.clear()
    for runtime in runtimes:
        close_pear_worker_runtime(runtime)


def cache_pear_worker_runtime(
    runtime_key: PearWorkerRuntimeKey, runtime: PearWorkerRuntime
) -> None:
    existing_runtime = pear_worker_runtimes.pop(runtime_key, None)
    if existing_runtime is not None and existing_runtime is not runtime:
        close_pear_worker_runtime(existing_runtime)
    pear_worker_runtimes[runtime_key] = runtime
    while len(pear_worker_runtimes) > PEAR_WORKER_RUNTIME_CACHE_LIMIT:
        _evicted_key, evicted_runtime = pear_worker_runtimes.popitem(last=False)
        close_pear_worker_runtime(evicted_runtime)


def get_pear_worker_runtime(
    model: str,
    workdir: str,
    reasoning_effort: str,
    process_isolation: PearProcessIsolation | None,
    isolation_error: str | None,
) -> PearWorkerRuntime:
    sandbox = pear_worker_sandbox_for_workdir(workdir)
    isolation_label = process_isolation.label() if process_isolation is not None else ""
    runtime_key = (model, workdir, reasoning_effort, sandbox, isolation_label)
    if isolation_error is None:
        runtime = pear_worker_runtimes.get(runtime_key)
        if runtime is not None:
            pear_worker_runtimes.move_to_end(runtime_key)
            return runtime
    runtime = build_pear_worker_runtime_from_settings(
        model_override=model,
        workdir_override=workdir,
        reasoning_effort_override=reasoning_effort,
        sandbox_override=sandbox,
        process_isolation_override=process_isolation,
        process_isolation_error=isolation_error,
    )
    if isolation_error is None:
        cache_pear_worker_runtime(runtime_key, runtime)
    return runtime


@assign_queue("pear_worker")
class PearWorker(QueueProcessingWorker):
    # Codex turns can legitimately take minutes; the embedded_bots queue should
    # only dispatch to this worker, not own the runtime budget.
    MAX_CONSUME_SECONDS = None

    # The session kinds this consumer owns; boot reaping must never
    # touch the other consumer's RUNNING rows.
    OWNED_SESSION_KINDS: tuple[str, ...] = ("shared", "side", "canvas_ask")

    @override
    def setup(self) -> None:
        super().setup()
        # Each pear queue is single-consumer: at boot, any RUNNING run of
        # our kinds was orphaned by a restart mid-turn. Reap them so
        # zombies never block the deploy health gate.
        reap_stale_pear_worker_runs(
            assume_orphaned=True, session_kinds=self.OWNED_SESSION_KINDS
        )

    @override
    def stop(self) -> None:  # nocoverage
        try:
            close_pear_worker_runtimes()
        finally:
            super().stop()

    @override
    def consume(self, event: dict[str, Any]) -> None:
        user_profile = get_user_profile_by_id(event["user_profile_id"])
        bot_handler = EmbeddedBotHandler(user_profile)
        message = event["message"]
        content = str(event.get("content", "")).strip()
        message_id = event.get("message_id")
        if not isinstance(message_id, int):
            message_id = pear_message_id(message)
        stream = str(event.get("stream") or pear_message_stream(message))
        topic = str(event.get("topic") or pear_message_topic(message))
        requesting_user_id = event.get("requesting_user_id")
        if not isinstance(requesting_user_id, int):
            requesting_user_id = None
        session_kind = str(event.get("session_kind") or "shared")
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
        worker_run_store = PearDatabaseWorkerRunStore()
        if not worker_run_store.claim_for_run(
            bot_handler,
            message_id,
            stream=stream,
            topic=topic,
        ):
            return
        model = pear_worker_model_for_topic(user_profile, stream=context_stream, topic=context_topic)
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
        pear_handler = PearEmbeddedBotHandler(
            worker_runtime=get_pear_worker_runtime(
                model,
                workdir,
                reasoning_effort,
                process_isolation,
                isolation_error,
            ),
            active_session_loader=load_pear_active_session_from_zulip,
            worker_run_store=worker_run_store,
        )
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


@assign_queue("pear_monitor_worker")
class PearMonitorWorker(PearWorker):
    """Wall monitor setup/rebuild turns plus the tick loop, on their own
    queue and consumer: a provisioning monitor is a minutes-long Codex
    turn, and it must never queue ahead of a chat run."""

    OWNED_SESSION_KINDS = ("monitor",)

    @override
    def setup(self) -> None:
        super().setup()
        # Ticks are model-free shell recipes on a cadence, on their own
        # thread because consume() blocks for minutes inside a turn.
        self.monitor_tick_stop = threading.Event()
        self.monitor_tick_thread = threading.Thread(
            target=self.run_monitor_tick_loop,
            name="pear-monitor-ticks",
            daemon=True,
        )
        self.monitor_tick_thread.start()

    def run_monitor_tick_loop(self) -> None:  # nocoverage
        while not self.monitor_tick_stop.wait(5):
            try:
                pear_run_due_monitor_ticks()
            except Exception:
                logger.exception("Pear monitor tick pass failed.")
            finally:
                reset_queries()

    @override
    def stop(self) -> None:  # nocoverage
        if hasattr(self, "monitor_tick_stop"):
            self.monitor_tick_stop.set()
        super().stop()
