import io
import os
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from types import ModuleType
from unittest import TestCase, mock

from typing_extensions import override

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARMADA_FILES = os.path.join(ROOT_DIR, "puppet/armada/files")


def load_script(name: str) -> ModuleType:
    """The scripts have no .py suffix, since puppet installs them as commands."""
    loader = SourceFileLoader(name.replace("-", "_"), os.path.join(ARMADA_FILES, name))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


settings_apply = load_script("armada-settings-apply")
ops_check = load_script("armada-ops-check")


class SettingsBlockTests(TestCase):
    def test_block_is_appended_once_and_replaced_in_place(self) -> None:
        operator = "EXTERNAL_HOST = 'chat.example.com'\n"
        first = settings_apply.with_block(operator, "ARMADA_WORKER_MODEL = 'a'\n")
        self.assertTrue(first.startswith(operator))
        self.assertIn(
            settings_apply.BEGIN + "ARMADA_WORKER_MODEL = 'a'\n" + settings_apply.END, first
        )
        self.assertEqual(settings_apply.with_block(first, "ARMADA_WORKER_MODEL = 'a'\n"), first)

        edited = first + "SOCIAL_AUTH_GITHUB_KEY = 'k'\n"
        second = settings_apply.with_block(edited, "ARMADA_WORKER_MODEL = 'b'\n")
        self.assertEqual(second.count(settings_apply.BEGIN), 1)
        self.assertIn("ARMADA_WORKER_MODEL = 'b'", second)
        self.assertNotIn("ARMADA_WORKER_MODEL = 'a'", second)
        self.assertTrue(second.startswith(operator))
        self.assertTrue(second.endswith("SOCIAL_AUTH_GITHUB_KEY = 'k'\n"))

    def test_check_mode_reports_a_missing_or_stale_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            block = os.path.join(directory, "block")
            settings = os.path.join(directory, "settings.py")
            with open(block, "w") as f:
                f.write("ARMADA_WORKER_MODEL = 'a'\n")
            with open(settings, "w") as f:
                f.write("EXTERNAL_HOST = 'chat.example.com'\n")

            def run(*args: str) -> int:
                with mock.patch.object(sys, "argv", ["armada-settings-apply", *args]):
                    status: int = settings_apply.main()
                    return status

            self.assertEqual(run("--check", block, settings), 1)
            self.assertEqual(run(block, settings), 0)
            self.assertEqual(run("--check", block, settings), 0)
            with open(block, "w") as f:
                f.write("ARMADA_WORKER_MODEL = 'b'\n")
            self.assertEqual(run("--check", block, settings), 1)


class OpsCheckTests(TestCase):
    @override
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        state_file = os.path.join(self.directory.name, "state.json")
        self.status = {"disk": ops_check.OK, "codex-login": ops_check.OK}
        self.real_checks = list(ops_check.CHECKS)
        self.codex_runs = 0

        def disk() -> object:
            return ops_check.Result(self.status["disk"], "/ 50% used", "disk")

        def codex_login() -> object:
            self.codex_runs += 1
            return ops_check.Result(self.status["codex-login"], "Logged in", "codex-login")

        self.posts: list[tuple[str, str, str]] = []
        self.runbooks: list[str] = []
        self.post_fails = False

        def post(check: str, result: object, previous: str) -> None:
            if self.post_fails:
                raise OSError("the server is down")
            self.posts.append((check, result.status, previous))  # type: ignore[attr-defined]
            self.runbooks.append(result.runbook)  # type: ignore[attr-defined]

        for patch in (
            mock.patch.object(ops_check, "STATE_FILE", state_file),
            mock.patch.dict(
                ops_check.CHECKS, {"disk": disk, "codex-login": codex_login}, clear=True
            ),
            mock.patch.object(ops_check, "post", post),
            mock.patch.object(sys, "argv", ["armada-ops-check"]),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_posts_only_when_a_status_changes(self) -> None:
        self.assertEqual(ops_check.main(), 0)
        self.assertEqual(self.posts, [])

        self.status["disk"] = ops_check.CRITICAL
        ops_check.main()
        ops_check.main()
        self.assertEqual(self.posts, [("disk", ops_check.CRITICAL, ops_check.OK)])

        self.status["disk"] = ops_check.OK
        ops_check.main()
        self.assertEqual(
            self.posts,
            [
                ("disk", ops_check.CRITICAL, ops_check.OK),
                ("disk", ops_check.OK, ops_check.CRITICAL),
            ],
        )

    def test_a_failed_post_is_tried_again(self) -> None:
        self.status["disk"] = ops_check.WARN
        self.post_fails = True
        ops_check.main()
        self.assertEqual(self.posts, [])
        self.post_fails = False
        ops_check.main()
        self.assertEqual(self.posts, [("disk", ops_check.WARN, ops_check.OK)])

    def test_slow_checks_run_at_most_every_ten_minutes(self) -> None:
        with mock.patch.object(ops_check.time, "time", return_value=1000.0):
            ops_check.main()
        with mock.patch.object(ops_check.time, "time", return_value=1300.0):
            ops_check.main()
        self.assertEqual(self.codex_runs, 1)
        with mock.patch.object(ops_check.time, "time", return_value=1700.0):
            ops_check.main()
        self.assertEqual(self.codex_runs, 2)

    def test_a_broken_check_warns_and_the_others_still_run(self) -> None:
        def broken() -> object:
            raise ValueError("unexpected output")

        with mock.patch.dict(ops_check.CHECKS, {"disk": broken}):
            ops_check.main()
        self.assertEqual(self.posts, [("disk", ops_check.WARN, ops_check.OK)])
        self.assertEqual(self.codex_runs, 1)

    def test_every_check_names_a_runbook_section(self) -> None:
        with open(os.path.join(ROOT_DIR, "docs/armada/operations.md")) as f:
            sections = {line.removeprefix("### ").strip() for line in f if line.startswith("### ")}

        def broken() -> object:
            raise ValueError("unexpected output")

        with mock.patch.dict(ops_check.CHECKS, dict.fromkeys(self.real_checks, broken), clear=True):
            ops_check.main()
        self.assertEqual([check for check, _, _ in self.posts], self.real_checks)
        self.assertLessEqual(set(self.runbooks), sections)

    def test_disk_thresholds(self) -> None:
        usage = mock.Mock(total=100, used=85)
        with mock.patch.object(ops_check.shutil, "disk_usage", return_value=usage):
            self.assertEqual(ops_check.check_disk().status, ops_check.WARN)
            usage.used = 91
            self.assertEqual(ops_check.check_disk().status, ops_check.CRITICAL)
            usage.used = 40
            self.assertEqual(ops_check.check_disk().status, ops_check.OK)
