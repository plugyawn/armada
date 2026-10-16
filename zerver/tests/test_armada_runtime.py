"""The agent runtime's processes and threads: which program an app-server
runs, how it stops, and how the monitor tick thread survives a dropped
database connection."""

import os
import platform
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from django.db import connection
from django.db.utils import InterfaceError
from typing_extensions import override

from zerver.lib import armada_agent
from zerver.lib.armada_agent import (
    ARMADA_CODEX_NPM_PLATFORM_PACKAGES,
    ArmadaCodexAppServerToolRunner,
    armada_codex_executable,
    armada_process_group_exists,
)
from zerver.lib.armada_monitor import armada_run_due_monitor_ticks
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
