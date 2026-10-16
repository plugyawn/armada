import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ArmadaBackgroundRead(Generic[T]):
    """An answer from a slow command (the Codex CLI), kept by each process
    and read again in a background thread once it is older than
    max_age_seconds, so that no request waits for the command.

    get() answers at once with what is there: the initial value until the
    first read succeeds. A read that fails keeps the last answer; only
    the first of a run of failures is logged, so that a broken command is
    not logged on every refresh in every process.

    A thread works under both servers: the dev server is threaded, and
    production's uwsgi runs with lazy-apps and enable-threads, so each
    process keeps its own answer."""

    # The refresh thread's name.
    name: str
    # What the answer is, for the log: "Codex's model catalog".
    description: str
    # Runs the command; raises when it could not answer.
    read: Callable[[], T]
    # The last answer, or the initial value until a read succeeds.
    value: T
    max_age_seconds: float
    # Whether there is anything to ask (the command is installed); when
    # not, no thread starts and the value stays as it is.
    available: Callable[[], bool] = lambda: True
    # When the last refresh started (time.monotonic()), or when one was
    # skipped because there was nothing to ask.
    checked_at: float | None = None
    # The refresh running now, if any; at most one runs in a process.
    refresh: threading.Thread | None = None
    # Whether the last refresh failed, so that a run of failures logs once.
    failing: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self) -> T:
        with self.lock:
            value = self.value
            now = time.monotonic()
            if self.refresh is None and (
                self.checked_at is None or now - self.checked_at >= self.max_age_seconds
            ):
                self.checked_at = now
                if self.available():
                    refresh = threading.Thread(target=self.refresh_now, name=self.name, daemon=True)
                    # Recorded once started, so that a thread that cannot
                    # start blocks no later refresh; the thread clears it
                    # only after this lock is released.
                    refresh.start()
                    self.refresh = refresh
        return value

    def refresh_now(self) -> None:
        """The refresh thread's body."""
        try:
            value = self.read()
        except Exception as error:
            with self.lock:
                self.refresh = None
                first_failure = not self.failing
                self.failing = True
            if first_failure:
                logger.warning(
                    "Could not refresh %s; keeping the last one",
                    self.description,
                    exc_info=error,
                )
            return
        with self.lock:
            self.refresh = None
            self.failing = False
            self.value = value
