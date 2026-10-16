"""The wall monitors' own queue consumer.

Queue names map to zerver.worker module names, so this must live in its
own module. Monitor setup/rebuild turns are minutes-long Codex turns; on
their own consumer they can never queue ahead of a chat run.
"""

import logging
import threading

from django.db import close_old_connections, connection, reset_queries
from django.db.utils import InterfaceError, OperationalError
from typing_extensions import override

from zerver.lib.armada_monitor import armada_run_due_monitor_ticks
from zerver.lib.armada_worktree_retention import maybe_reap_retired_armada_worktrees
from zerver.worker.armada_worker import ArmadaWorker
from zerver.worker.base import assign_queue

logger = logging.getLogger(__name__)


def run_armada_monitor_tick_pass() -> int:
    """One pass of the wall's ticks, on the tick thread's own database
    connection, which must outlive a dropped connection (a Postgres
    restart, a network blip). Django keeps a dead connection object, so
    without this every later pass failed until the worker restarted."""
    # Drops a connection an earlier pass found broken.
    close_old_connections()
    try:
        try:
            ticked = armada_run_due_monitor_ticks()
        except (InterfaceError, OperationalError):
            # Dropped since the last pass: close it and run the pass again
            # on a new connection.
            connection.close()
            ticked = armada_run_due_monitor_ticks()
        # The housekeeping of worktrees rides this thread, at most once an
        # hour; a failure there must not stop the ticks.
        try:
            maybe_reap_retired_armada_worktrees()
        except Exception:
            logger.exception("Worktree retention pass failed.")
        return ticked
    finally:
        reset_queries()


@assign_queue("armada_monitor_worker")
class ArmadaMonitorWorker(ArmadaWorker):
    OWNED_SESSION_KINDS = ("monitor",)
    # This queue still uses QueueProcessingWorker's ordinary consume/ack
    # contract; only the chat slots, which acknowledge by hand, rethrow for
    # broker retry.
    RERAISE_CONSUME_EXCEPTIONS = False
    # Monitor work deliberately stays on its dedicated queue; it must never
    # participate in the chat worker pool inherited from ArmadaWorker.
    IN_SLOT_POOL = False

    @override
    def setup(self) -> None:
        super().setup()
        # Ticks are model-free shell recipes on a cadence, on their own
        # thread because consume() blocks for minutes inside a turn.
        self.monitor_tick_stop = threading.Event()
        self.monitor_tick_thread = threading.Thread(
            target=self.run_monitor_tick_loop,
            name="armada-monitor-ticks",
            daemon=True,
        )
        self.monitor_tick_thread.start()

    def run_monitor_tick_loop(self) -> None:  # nocoverage
        try:
            while not self.monitor_tick_stop.wait(5):
                try:
                    run_armada_monitor_tick_pass()
                except Exception:
                    logger.exception("Monitor tick pass failed.")
        finally:
            close_old_connections()

    @override
    def stop(self) -> None:  # nocoverage
        if hasattr(self, "monitor_tick_stop"):
            self.monitor_tick_stop.set()
        super().stop()
