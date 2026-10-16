import threading
from unittest.mock import patch

from zerver.lib.armada_background_task import ArmadaBackgroundTask
from zerver.lib.test_classes import ZulipTestCase


class HeldWork:
    """Work that notes each key it was given and returns only once
    released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.keys: list[int] = []

    def __call__(self, key: int) -> None:
        self.keys.append(key)
        self.release.wait(timeout=10)


class ArmadaBackgroundTaskTest(ZulipTestCase):
    def test_one_at_a_time_for_a_key_and_once_more_when_asked_again(self) -> None:
        work = HeldWork()
        self.addCleanup(work.release.set)
        task = ArmadaBackgroundTask[int]("armada-test-task", work)

        task.start(7)
        thread = task.running[7]
        self.assertEqual(thread.name, "armada-test-task")
        task.start(7)
        task.start(7)
        self.assertIs(task.running[7], thread)
        self.assertEqual(task.again, {7})
        # Another key runs beside it.
        task.start(8)
        other = task.running[8]
        self.assertIsNot(other, thread)

        work.release.set()
        thread.join(timeout=10)
        other.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(work.keys), [7, 7, 8])
        self.assertEqual(task.running, {})
        self.assertEqual(task.again, set())

    def test_request_starts_once_the_transaction_commits(self) -> None:
        task = ArmadaBackgroundTask[int]("armada-test-task", HeldWork())
        with patch.object(task, "start") as start:
            with self.captureOnCommitCallbacks(execute=True):
                task.request(7)
                start.assert_not_called()
            start.assert_called_once_with(7)

    def test_failed_work_is_logged_and_the_connection_closed(self) -> None:
        def failing(key: int) -> None:
            raise RuntimeError("git crashed")

        task = ArmadaBackgroundTask[int]("armada-test-task", failing)
        # The thread's body, run here where coverage can see it.
        task.running[7] = threading.current_thread()
        with (
            patch("zerver.lib.armada_background_task.connection.close") as close,
            self.assertLogs("zerver.lib.armada_background_task", level="ERROR") as logs,
        ):
            task.run(7)
        self.assertEqual(
            logs.output[0].splitlines()[0],
            "ERROR:zerver.lib.armada_background_task:armada-test-task failed for 7",
        )
        close.assert_called_once()
        self.assertEqual(task.running, {})

    def test_a_thread_that_cannot_start_is_dropped(self) -> None:
        task = ArmadaBackgroundTask[int]("armada-test-task", HeldWork())
        with (
            patch(
                "zerver.lib.armada_background_task.threading.Thread.start",
                side_effect=RuntimeError,
            ),
            self.assertLogs("zerver.lib.armada_background_task", level="WARNING") as logs,
        ):
            task.start(7)
        self.assertEqual(
            logs.output,
            ["WARNING:zerver.lib.armada_background_task:Could not start armada-test-task for 7"],
        )
        self.assertEqual(task.running, {})
