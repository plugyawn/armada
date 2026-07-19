"""The wall monitors' own queue consumer.

Queue names map to zerver.worker module names, so this must live in its
own module. Monitor setup/rebuild turns are minutes-long Codex turns; on
their own consumer they can never queue ahead of a chat run.
"""

import logging
import threading

from django.db import reset_queries
from typing_extensions import override

from zerver.lib.pear_monitor import pear_run_due_monitor_ticks
from zerver.worker.base import assign_queue
from zerver.worker.pear_worker import PearWorker

logger = logging.getLogger(__name__)


@assign_queue("pear_monitor_worker")
class PearMonitorWorker(PearWorker):
    OWNED_SESSION_KINDS = ("monitor",)
    # This queue still uses QueueProcessingWorker's ordinary consume/ack
    # contract; only the two manual-ack chat slots rethrow for broker retry.
    RERAISE_CONSUME_EXCEPTIONS = False
    # Monitor work deliberately stays on its dedicated queue; it must never
    # participate in the chat worker pool inherited from PearWorker.
    POOL_QUEUE_NAMES: tuple[str, ...] = ()

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
