import logging
import threading
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

from django.db import connection, transaction

logger = logging.getLogger(__name__)

K = TypeVar("K", bound=Hashable)


class ArmadaBackgroundTask(Generic[K]):
    """Work that a request or a worker asks for but does not wait for, run
    in a thread of this process: at most one at a time for each key, and
    once more if it was asked for again while it ran.

    request() starts it when the asking transaction commits, so the work
    sees what that transaction wrote, and a rolled-back request asks for
    nothing. A thread works under both servers: the dev server is
    threaded, and production's uwsgi runs with lazy-apps and
    enable-threads."""

    def __init__(self, name: str, work: Callable[[K], object]) -> None:
        # The threads' name.
        self.name = name
        self.work = work
        # The threads running now, by key, and the keys asked for again
        # while their thread ran.
        self.running: dict[K, threading.Thread] = {}
        self.again: set[K] = set()
        self.lock = threading.Lock()

    def request(self, key: K) -> None:
        transaction.on_commit(lambda: self.start(key))

    def start(self, key: K) -> None:
        with self.lock:
            if key in self.running:
                self.again.add(key)
                return
            thread = threading.Thread(target=self.run, args=(key,), name=self.name, daemon=True)
            try:
                thread.start()
            except RuntimeError:
                # The process cannot start another thread; the next
                # request asks again.
                logger.warning("Could not start %s for %s", self.name, key)
                return
            self.running[key] = thread

    def run(self, key: K) -> None:
        """The thread's body: do the work until nobody asked again."""
        try:
            while True:
                try:
                    self.work(key)
                except Exception:
                    logger.exception("%s failed for %s", self.name, key)
                with self.lock:
                    if key not in self.again:
                        self.running.pop(key, None)
                        return
                    self.again.discard(key)
        finally:
            # The thread's own database connection; left open, every run
            # would leave an idle connection behind.
            connection.close()
