"""The agent runtime's processes: how an app-server is stopped."""

import os
import stat
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from zerver.lib import armada_agent
from zerver.lib.armada_agent import ArmadaCodexAppServerToolRunner, armada_process_group_exists

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
