"""The agent runtime's processes and threads: which program an app-server
runs, how it stops, and how the monitor tick thread survives a dropped
database connection."""

import json
import os
import platform
import queue
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from subprocess import Popen
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest import TestCase
from unittest.mock import Mock, patch

from django.db import connection
from django.db.utils import InterfaceError
from typing_extensions import override

from zerver.lib import armada_agent
from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_EMBEDDED_BOT_SERVICE,
    build_armada_active_session_summary_from_messages,
    get_armada_session_payload,
)
from zerver.lib.armada_agent import (
    ARMADA_CODEX_NPM_PLATFORM_PACKAGES,
    ARMADA_CODEX_RETAINED_THREAD_LIMIT,
    ArmadaCodexAppServerToolRunner,
    armada_codex_executable,
    armada_process_group_exists,
)
from zerver.lib.armada_monitor import armada_run_due_monitor_ticks
from zerver.lib.armada_worker import (
    ArmadaDatabaseWorkerRunStore,
    armada_git_current_branch_name,
    armada_worker_sandbox_for_workdir,
    armada_worker_workdir_for_topic,
    build_armada_workspace_context,
    ensure_armada_workspace_node,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRun, UserProfile
from zerver.worker.armada_monitor_worker import run_armada_monitor_tick_pass

# A Codex stand-in that answers every request. With "wedged" in its file
# name it also ignores SIGTERM and stdin's end, and starts a child that
# ignores SIGTERM too, as a native binary busy writing its rollout would:
# only a SIGKILL to the whole group stops both.
ARMADA_FAKE_APP_SERVER = """#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
wedged = "wedged" in os.path.basename(__file__)
if wedged:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"],
        stdin=subprocess.DEVNULL,
    )
    with open(os.path.join(here, "child.pid"), "w") as pid_file:
        pid_file.write(str(child.pid))
for line in sys.stdin:
    message = json.loads(line)
    if "id" in message and "method" in message:
        print(json.dumps({"id": message["id"], "result": {}}), flush=True)
if wedged:
    time.sleep(600)
"""


def armada_pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def armada_eventually(condition: Callable[[], bool], *, seconds: float = 5) -> bool:
    # A killed child of the app-server is a zombie until init, its new
    # parent, reaps it.
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


class ArmadaAppServerStopTest(TestCase):
    def start_runner(self, directory: str, name: str) -> ArmadaCodexAppServerToolRunner:
        script = Path(directory) / name
        script.write_text(ARMADA_FAKE_APP_SERVER)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin=str(script),
            workdir=directory,
            model="gpt-6-astra",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=10,
            output_limit=200,
        )
        runner.ensure_started()
        return runner

    def test_stop_leaves_no_process_of_the_group(self) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.object(armada_agent, "ARMADA_APP_SERVER_STOP_GRACE_SECONDS", 0.3),
        ):
            runner = self.start_runner(directory, "wedged-codex")
            process = runner.process
            assert process is not None
            child_pid_file = Path(directory) / "child.pid"
            deadline = time.monotonic() + 10
            while not child_pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            child_pid = int(child_pid_file.read_text())
            self.assertTrue(armada_pid_exists(child_pid))

            runner.close()

            # The leader is reaped, not left a zombie, and its child, which
            # ignored SIGTERM, did not outlive it as an orphan.
            self.assertIsNotNone(process.returncode)
            self.assertTrue(armada_eventually(lambda: not armada_pid_exists(child_pid)))
            self.assertTrue(armada_eventually(lambda: not armada_process_group_exists(process.pid)))
            self.assertIsNone(runner.process)

    def test_stop_ends_a_cooperative_app_server_by_closing_stdin(self) -> None:
        with TemporaryDirectory() as directory:
            runner = self.start_runner(directory, "codex")
            process = runner.process
            assert process is not None

            with patch.object(armada_agent, "armada_signal_process_group") as signal_group:
                started = time.monotonic()
                runner.close()

            # Codex exits as soon as its stdio transport ends; no signal is
            # needed, and nothing waits out the grace period.
            self.assertEqual(process.returncode, 0)
            signal_group.assert_not_called()
            self.assertLess(
                time.monotonic() - started, armada_agent.ARMADA_APP_SERVER_STOP_GRACE_SECONDS
            )

    def test_abort_reaps_its_process_without_waiting(self) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.object(armada_agent, "ARMADA_APP_SERVER_STOP_GRACE_SECONDS", 0.3),
        ):
            runner = self.start_runner(directory, "wedged-codex")
            process = runner.process
            assert process is not None

            started = time.monotonic()
            runner.abort(RuntimeError("The worker lost its lease."))
            self.assertLess(time.monotonic() - started, 0.3)

            # Reaped by the stop thread, not left a zombie until the runtime
            # is next used.
            self.assertTrue(armada_eventually(lambda: process.returncode is not None))
            self.assertTrue(armada_eventually(lambda: not armada_process_group_exists(process.pid)))
            runner.close()


class ArmadaCodexExecutableTest(TestCase):
    @override
    def setUp(self) -> None:
        armada_codex_executable.cache_clear()
        self.addCleanup(armada_codex_executable.cache_clear)

    def make_npm_install(self, root: Path, *, with_native_binary: bool = True) -> Path:
        """Codex as `npm install -g --prefix root @openai/codex` lays it out."""
        package_root = root / "lib" / "node_modules" / "@openai" / "codex"
        launcher = package_root / "bin" / "codex.js"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/usr/bin/env node\n")
        launcher.chmod(0o755)
        (root / "bin").mkdir()
        (root / "bin" / "codex").symlink_to("../lib/node_modules/@openai/codex/bin/codex.js")
        package_name, target_triple = ARMADA_CODEX_NPM_PLATFORM_PACKAGES[
            (platform.system(), platform.machine())
        ]
        native_binary = (
            package_root
            / "node_modules"
            / package_name
            / "vendor"
            / target_triple
            / "bin"
            / "codex"
        )
        if with_native_binary:
            native_binary.parent.mkdir(parents=True)
            native_binary.write_text("#!/bin/sh\n")
            native_binary.chmod(0o755)
        return native_binary

    def test_npm_launcher_resolves_to_its_native_binary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            native_binary = self.make_npm_install(root)
            package_root = os.path.realpath(root / "lib" / "node_modules" / "@openai" / "codex")

            executable, environment = armada_codex_executable(str(root / "bin" / "codex"))

            self.assertEqual(os.path.realpath(executable), os.path.realpath(native_binary))
            # The two variables the launcher sets for the binary.
            self.assertEqual(
                dict(environment),
                {"CODEX_MANAGED_PACKAGE_ROOT": package_root, "CODEX_MANAGED_BY_NPM": "1"},
            )
            runner = ArmadaCodexAppServerToolRunner(
                codex_bin=str(root / "bin" / "codex"),
                workdir=directory,
                model="gpt-6-astra",
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=10,
                output_limit=200,
            )
            self.assertEqual(
                runner.build_command()[:6],
                [
                    "env",
                    f"CODEX_MANAGED_PACKAGE_ROOT={package_root}",
                    "CODEX_MANAGED_BY_NPM=1",
                    executable,
                    "app-server",
                    "--stdio",
                ],
            )

    def test_anything_else_runs_as_given(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_npm_install(root, with_native_binary=False)
            launcher = str(root / "bin" / "codex")
            # A launcher whose platform package is missing: it explains that
            # itself when run.
            self.assertEqual(armada_codex_executable(launcher), (launcher, ()))

            native = root / "codex-native"
            native.write_text("#!/bin/sh\n")
            native.chmod(0o755)
            self.assertEqual(armada_codex_executable(str(native)), (str(native), ()))
            self.assertEqual(armada_codex_executable("no-such-codex"), ("no-such-codex", ()))


class ArmadaMonitorTickConnectionTest(TestCase):
    """The monitor tick thread outlives a dropped database connection. On
    the hosted beta one drop failed every pass, every 5 seconds, until the
    worker was restarted."""

    def run_on_own_thread(self, work: Callable[[], None]) -> None:
        # The tick loop runs on its own thread, with its own connection.
        failures: list[BaseException] = []

        def run() -> None:
            try:
                work()
            except BaseException as error:  # nocoverage
                failures.append(error)
            finally:
                connection.close()

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        if failures:  # nocoverage
            raise failures[0]

    def drop_connection(self) -> None:
        # What a Postgres restart leaves behind: Django still holds the
        # connection object, but its socket is gone.
        connection.ensure_connection()
        raw_connection = connection.connection
        assert raw_connection is not None
        raw_connection.close()

    def test_a_pass_after_the_drop_runs_on_a_new_connection(self) -> None:
        def work() -> None:
            run_armada_monitor_tick_pass()
            self.drop_connection()
            self.assertEqual(run_armada_monitor_tick_pass(), 0)

        self.run_on_own_thread(work)

    def test_the_pass_after_a_failed_one_recovers(self) -> None:
        def work() -> None:
            self.drop_connection()
            # The pass that met the drop failed and left the connection
            # marked broken; the next one must not fail too.
            with self.assertRaises(InterfaceError):
                armada_run_due_monitor_ticks()
            self.assertEqual(run_armada_monitor_tick_pass(), 0)

        self.run_on_own_thread(work)


class ArmadaTurnProvisioningTest(ZulipTestCase):
    """A turn provisions its topic's worktree once, when it starts. Before,
    every transcript event provisioned it three times while holding the
    run's row lock: about 42 git processes an event, 21,000 for a run of
    500 events."""

    def git_spawns(self, spawn: Mock) -> int:
        return sum(
            1
            for spawn_call in spawn.call_args_list
            if "git" in [str(argument) for argument in spawn_call.args[0]]
        )

    def test_a_run_spawns_git_only_to_provision_its_worktree(self) -> None:
        owner = self.example_user("hamlet")
        bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(bot, ARMADA_DEFAULT_STREAM)
        bot_handler = SimpleNamespace(user_profile=bot)
        topic = "provision once"

        with (
            TemporaryDirectory() as topic_root,
            self.settings(ARMADA_TOPIC_WORKDIR_ROOT=topic_root, ARMADA_WORKER_MODEL="gpt-6-astra"),
            patch("subprocess.run", wraps=subprocess.run) as spawn,
        ):
            # An earlier turn made the channel's repository and the worktree.
            ensure_armada_workspace_node(bot, stream=ARMADA_DEFAULT_STREAM, topic=topic)
            spawn.reset_mock()

            store = ArmadaDatabaseWorkerRunStore()
            self.assertTrue(
                store.claim_for_run(bot_handler, 9876, stream=ARMADA_DEFAULT_STREAM, topic=topic)
            )
            self.assertEqual(self.git_spawns(spawn), 0)

            # The turn's one provisioning.
            workspace_node = ensure_armada_workspace_node(
                bot, stream=ARMADA_DEFAULT_STREAM, topic=topic
            )
            provisioning_spawns = self.git_spawns(spawn)
            self.assertLessEqual(provisioning_spawns, 10)
            spawn.reset_mock()

            build_armada_workspace_context(bot, stream=ARMADA_DEFAULT_STREAM, topic=topic)
            for event in range(500):
                store.update_metadata(bot_handler, 9876, current_activity=f"Ran command {event}.")
            store.record(bot_handler, 9876, "done", stream=ARMADA_DEFAULT_STREAM, topic=topic)
            self.assertEqual(self.git_spawns(spawn), 0)

        run = AgentRun.objects.get(bot_profile=bot, trigger_message_id=9876)
        self.assertEqual(run.status, AgentRun.DONE)
        self.assertEqual(run.current_activity, "Ran command 499.")
        self.assertEqual(run.model, "gpt-6-astra")
        self.assertEqual(run.workdir, workspace_node.workdir)
        self.assertEqual(run.sandbox, armada_worker_sandbox_for_workdir(workspace_node.workdir))

    def test_an_agent_may_switch_branches_during_its_turn(self) -> None:
        owner = self.example_user("hamlet")
        bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(bot, ARMADA_DEFAULT_STREAM)
        bot_handler = SimpleNamespace(user_profile=bot)
        topic = "agent branches"

        with (
            TemporaryDirectory() as topic_root,
            self.settings(ARMADA_TOPIC_WORKDIR_ROOT=topic_root),
        ):
            store = ArmadaDatabaseWorkerRunStore()
            self.assertTrue(
                store.claim_for_run(bot_handler, 9877, stream=ARMADA_DEFAULT_STREAM, topic=topic)
            )
            workdir = ensure_armada_workspace_node(
                bot, stream=ARMADA_DEFAULT_STREAM, topic=topic
            ).workdir
            # Codex checks out a branch of its own mid-turn; the next events
            # once switched the worktree back under it.
            subprocess.run(
                ["git", "-C", workdir, "switch", "--quiet", "-c", "codex/experiment"],
                check=True,
            )
            store.update_metadata(bot_handler, 9877, current_activity="Ran tests.")
            self.assertEqual(armada_git_current_branch_name(Path(workdir)), "codex/experiment")

    def test_a_monitor_tick_provisions_only_a_missing_worktree(self) -> None:
        owner = self.example_user("hamlet")
        bot = self.create_test_bot(
            "onix",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(bot, ARMADA_DEFAULT_STREAM)

        with (
            TemporaryDirectory() as topic_root,
            self.settings(ARMADA_TOPIC_WORKDIR_ROOT=topic_root),
            patch("subprocess.run", wraps=subprocess.run) as spawn,
        ):
            workdir = armada_worker_workdir_for_topic(
                bot, stream=ARMADA_DEFAULT_STREAM, topic="wall"
            )
            self.assertTrue(os.path.isdir(workdir))
            self.assertGreater(self.git_spawns(spawn), 0)
            spawn.reset_mock()

            # Every later tick, every few seconds: one stat, no git.
            for _tick in range(3):
                self.assertEqual(
                    armada_worker_workdir_for_topic(
                        bot, stream=ARMADA_DEFAULT_STREAM, topic="wall"
                    ),
                    workdir,
                )
            self.assertEqual(self.git_spawns(spawn), 0)


class ArmadaThreadMapTest(ZulipTestCase):
    def make_runner(self) -> ArmadaCodexAppServerToolRunner:
        return ArmadaCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-6-astra",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=10,
            output_limit=200,
        )

    def test_a_turn_leaves_only_the_threads_later_turns_can_use(self) -> None:
        runner = self.make_runner()
        translator = runner.translator

        def remember(thread_id: str, parent: str | None, status: str = "idle") -> None:
            translator.thread_descriptors[thread_id] = {
                "id": thread_id,
                "parent_thread_id": parent,
                "thread_source": "subagent" if parent else None,
                "status": {"type": status},
            }
            translator.latest_direct_assistant_body[thread_id] = f"{thread_id} is done."

        remember("root", None)
        for index in range(300):
            remember(f"agent-{index}", "root")
        # Still working, under an idle parent: both stay.
        remember("grandchild", "agent-0", status="active")
        # A /btw conversation retained for its next reply, and its agent.
        remember("side", None)
        remember("side-agent", "side")
        runner.side_thread_ids_by_conversation_key["root-message-7"] = "side"
        # A thread this app-server resumed before the topic's current one.
        remember("old-root", None)
        remember("old-agent", "old-root")
        for thread_id in ("old-agent", "agent-299", "unknown"):
            runner.unrouted_notifications[thread_id] = []
        runner.loaded_thread_id = "root"

        with runner.state_lock:
            runner.prune_thread_maps_locked()

        kept = set(translator.thread_descriptors)
        self.assert_length(kept, ARMADA_CODEX_RETAINED_THREAD_LIMIT)
        self.assertTrue({"root", "agent-0", "grandchild", "side", "side-agent"} <= kept)
        self.assertFalse({"old-root", "old-agent"} & kept)
        # The oldest idle agents went first.
        self.assertNotIn("agent-1", kept)
        self.assertIn("agent-299", kept)
        self.assertEqual(set(translator.latest_direct_assistant_body), kept)
        # A thread no turn has claimed yet keeps its notifications.
        self.assertEqual(set(runner.unrouted_notifications), {"agent-299", "unknown"})

    def test_every_turn_prunes_when_it_ends(self) -> None:
        class ScriptedProcess:
            def __init__(self) -> None:
                self.output: queue.Queue[str | None] = queue.Queue()
                self.stdin = self
                self.stdout = iter(self.output.get, None)

            def write(self, line: str) -> int:
                message = json.loads(line)
                if "id" not in message:
                    return len(line)
                result: dict[str, object] = {}
                if message["method"] == "thread/start":
                    result = {"thread": {"id": "thread-1"}}
                elif message["method"] == "turn/start":
                    result = {"turn": {"id": "turn-1"}}
                self.output.put(json.dumps({"id": message["id"], "result": result}) + "\n")
                if message["method"] == "turn/start":
                    completed = {"threadId": "thread-1", "turn": {"id": "turn-1"}}
                    self.output.put(
                        json.dumps({"method": "turn/completed", "params": completed}) + "\n"
                    )
                return len(line)

            def flush(self) -> None:
                pass

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.output.put(None)

        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/workspace",
            model="gpt-6-astra",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=10,
            output_limit=200,
            popen_command=lambda command, **kwargs: cast(Popen[str], ScriptedProcess()),
        )
        runner.translator.thread_descriptors["stale-root"] = {"id": "stale-root"}
        active_session = build_armada_active_session_summary_from_messages(
            armada_session=get_armada_session_payload(),
            source="server",
            stream_exists=True,
            messages=[(10, "@**Onix** check the build", False)],
        )

        runner(active_session, "Check the build.")

        self.assertNotIn("stale-root", runner.translator.thread_descriptors)
        runner.close()
