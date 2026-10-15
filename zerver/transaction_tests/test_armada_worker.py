import threading
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

from django.db import OperationalError, connection, connections, transaction
from typing_extensions import override

from zerver.lib.armada import PearWorkerLeaseLostError
from zerver.lib.armada_worker import PearDatabaseWorkerRunStore
from zerver.lib.test_classes import ZulipTransactionTestCase
from zerver.models import AgentRun, Workspace


class RacingThread(threading.Thread):
    def __init__(self, operation: Callable[[], None]) -> None:
        super().__init__()
        self.operation = operation
        self.error: BaseException | None = None

    @override
    def run(self) -> None:
        try:
            self.operation()
        except BaseException as error:
            self.error = error
        finally:
            connections.close_all()


class PearWorkerCompletionRaceTest(ZulipTransactionTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.bot_handler = SimpleNamespace(user_profile=self.owner)
        self.created_run_ids: list[int] = []
        self.created_workspace_ids: list[int] = []

    @override
    def tearDown(self) -> None:
        AgentRun.objects.filter(id__in=self.created_run_ids).delete()
        Workspace.objects.filter(id__in=self.created_workspace_ids).delete()
        super().tearDown()

    def create_claimed_run(
        self,
        *,
        message_id: int,
        topic: str,
    ) -> tuple[PearDatabaseWorkerRunStore, AgentRun]:
        workspace = Workspace.objects.create(
            realm=self.owner.realm,
            stream_name="pear-lock-race",
            topic_name=topic,
        )
        run = AgentRun.objects.create(
            realm=self.owner.realm,
            bot_profile=self.owner,
            requesting_user=self.owner,
            trigger_message_id=message_id,
            stream_name=workspace.stream_name,
            topic_name=workspace.topic_name,
            workspace_node=workspace,
            status=AgentRun.RUNNING,
            attempts=1,
        )
        store = PearDatabaseWorkerRunStore()
        store._remember_claimed_attempt(run)
        self.created_workspace_ids.append(workspace.id)
        self.created_run_ids.append(run.id)
        return store, run

    def assert_thread_finished(self, thread: RacingThread) -> None:
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(thread.error, repr(thread.error))

    @mock.patch("zerver.lib.armada_worker.refresh_pear_workspace_after_terminal_run")
    def test_cancel_and_completion_have_one_terminal_winner(
        self,
        _refresh_workspace: mock.Mock,
    ) -> None:
        completion_store, completion_run = self.create_claimed_run(
            message_id=2_000_000_001,
            topic="completion-first",
        )
        completion_locked = threading.Event()
        release_completion = threading.Event()

        def hold_completion_lock() -> None:
            with completion_store.worker_completion(
                self.bot_handler,
                completion_run.trigger_message_id,
            ):
                completion_locked.set()
                if not release_completion.wait(timeout=5):
                    raise AssertionError("completion lock was not released")
                completion_store.record(
                    self.bot_handler,
                    completion_run.trigger_message_id,
                    AgentRun.DONE,
                    stream=completion_run.stream_name,
                    topic=completion_run.topic_name,
                )

        completion_thread = RacingThread(hold_completion_lock)
        completion_thread.start()
        try:
            self.assertTrue(completion_locked.wait(timeout=5))
            with self.assertRaises(OperationalError), transaction.atomic(savepoint=False):
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '250ms'")
                locked_run = AgentRun.objects.select_for_update().get(id=completion_run.id)
                PearDatabaseWorkerRunStore().cancel_locked_run(
                    locked_run,
                    summary="Cancelled.",
                )
        finally:
            release_completion.set()

        self.assert_thread_finished(completion_thread)
        completion_run.refresh_from_db()
        self.assertEqual(completion_run.status, AgentRun.DONE)
        with transaction.atomic(savepoint=False):
            locked_run = AgentRun.objects.select_for_update().get(id=completion_run.id)
            self.assertFalse(
                PearDatabaseWorkerRunStore().cancel_locked_run(
                    locked_run,
                    summary="Cancelled.",
                )
            )

        cancelled_store, cancelled_run = self.create_claimed_run(
            message_id=2_000_000_002,
            topic="cancel-first",
        )
        cancel_locked = threading.Event()
        release_cancel = threading.Event()

        def hold_cancel_lock() -> None:
            with transaction.atomic(savepoint=False):
                locked_run = AgentRun.objects.select_for_update().get(id=cancelled_run.id)
                if not PearDatabaseWorkerRunStore().cancel_locked_run(
                    locked_run,
                    summary="Cancelled.",
                ):
                    raise AssertionError("cancellation did not win")
                cancel_locked.set()
                if not release_cancel.wait(timeout=5):
                    raise AssertionError("cancel lock was not released")

        cancel_thread = RacingThread(hold_cancel_lock)
        cancel_thread.start()
        try:
            self.assertTrue(cancel_locked.wait(timeout=5))
            with self.assertRaises(OperationalError), transaction.atomic(savepoint=False):
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '250ms'")
                with cancelled_store.worker_completion(
                    self.bot_handler,
                    cancelled_run.trigger_message_id,
                ):
                    self.fail("completion entered while cancellation held the row")
        finally:
            release_cancel.set()

        self.assert_thread_finished(cancel_thread)
        entered_completion = False
        with (
            self.assertRaisesRegex(PearWorkerLeaseLostError, "lost attempt 1"),
            cancelled_store.worker_completion(
                self.bot_handler,
                cancelled_run.trigger_message_id,
            ),
        ):
            entered_completion = True
        self.assertFalse(entered_completion)
        cancelled_run.refresh_from_db()
        self.assertEqual(cancelled_run.status, AgentRun.CANCELLED)
