import io
import os
import shutil
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
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
        # The file the alerts link, on the branch they link it on.
        runbook = ops_check.RUNBOOK_URL.removeprefix(
            "https://github.com/plugyawn/armada/blob/ui-overhaul/"
        )
        self.assertEqual(runbook, "docs/armada/operations.md")
        with open(os.path.join(ROOT_DIR, runbook)) as f:
            sections = {line.removeprefix("### ").strip() for line in f if line.startswith("### ")}

        def broken() -> object:
            raise ValueError("unexpected output")

        with mock.patch.dict(ops_check.CHECKS, dict.fromkeys(self.real_checks, broken), clear=True):
            ops_check.main()
        self.assertEqual([check for check, _, _ in self.posts], self.real_checks)
        self.assertLessEqual(set(self.runbooks), sections)

    def test_the_addressing_router_warns_when_messages_cannot_be_routed(self) -> None:
        now = datetime.now(timezone.utc).astimezone()

        def logged(minutes_ago: float, level: str, message: str) -> str:
            stamp = (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S.000")
            return f"{stamp} {level} [zr] {message}\n"

        failure = logged(1, "WARN", "Addressing router failed.")
        decision = logged(
            1,
            "INFO",
            "Ambient router decision: message_id=7 stream='web-app' topic='lint' "
            "addressee=uncertain requested_action=ignore effective_action=ignore",
        )
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "events_armada_addressing.log")
            absent = os.path.join(directory, "events.log")
            with open(log, "w") as f:
                # Failures older than the window, and a traceback, do not count.
                f.write(logged(20, "WARN", "Addressing router failed.") * 5)
                f.write("Traceback (most recent call last):\n")
                f.write((failure + decision) * 2 + decision)
            with mock.patch.object(ops_check, "ADDRESSING_LOGS", (log, absent)):
                self.assertEqual(
                    ops_check.check_addressing_router(),
                    ops_check.Result(
                        ops_check.OK,
                        "3 messages routed in 15 minutes, 2 failed",
                        "addressing-router",
                    ),
                )
                with open(log, "a") as f:
                    f.write(failure + decision)
                self.assertEqual(
                    ops_check.check_addressing_router(),
                    ops_check.Result(
                        ops_check.WARN,
                        "3 of 4 messages in 15 minutes could not be routed",
                        "addressing-router",
                    ),
                )
                # Only the end of a long log is read.
                tail = decision + failure + decision
                with mock.patch.object(ops_check, "LOG_TAIL_BYTES", len(tail)):
                    self.assertEqual(
                        ops_check.check_addressing_router().detail,
                        "2 messages routed in 15 minutes, 1 failed",
                    )

    def test_disk_thresholds(self) -> None:
        usage = mock.Mock(total=100, used=85)
        with mock.patch.object(ops_check.shutil, "disk_usage", return_value=usage):
            self.assertEqual(ops_check.check_disk().status, ops_check.WARN)
            usage.used = 91
            self.assertEqual(ops_check.check_disk().status, ops_check.CRITICAL)
            usage.used = 40
            self.assertEqual(ops_check.check_disk().status, ops_check.OK)


class CodexQuotaTests(TestCase):
    """The Codex account's usage limits, read through the app-server
    without spending a turn. On runix.chat the account reached its limit
    and runs failed until it reset; nothing said so before."""

    # The app-server's answer to account/rateLimits/read, as read on 17
    # October 2026, with the use the test asks for.
    @staticmethod
    def reading(used: float, *, allowed: bool = True) -> dict[str, object]:
        window = {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": 1792764987}
        return {
            "ordinaryUsageAllowed": allowed,
            "rateLimits": {"limitId": "codex", "primary": window, "secondary": None},
            "rateLimitsByLimitId": {"codex": {"limitId": "codex", "primary": window}},
        }

    def test_warns_at_80_percent_and_is_critical_at_100(self) -> None:
        self.assertEqual(
            ops_check.quota_result(self.reading(100, allowed=False)),
            ops_check.Result(
                ops_check.CRITICAL,
                "Codex has used 100% of its 7-day limit; it resets Fri 23 Oct, 14:16 UTC",
                "codex-quota",
            ),
        )
        self.assertEqual(ops_check.quota_result(self.reading(85)).status, ops_check.WARN)
        self.assertEqual(ops_check.quota_result(self.reading(42.4)).status, ops_check.OK)
        self.assertEqual(
            ops_check.quota_result(self.reading(42.4)).detail,
            "Codex has used 42% of its 7-day limit; it resets Fri 23 Oct, 14:16 UTC",
        )
        # A five-hour window more used than the week's is the one named.
        reading = self.reading(30)
        reading["rateLimitsByLimitId"] = {
            "codex": {"primary": {"usedPercent": 90, "windowDurationMins": 300}}
        }
        self.assertEqual(
            ops_check.quota_result(reading).detail, "Codex has used 90% of its 5-hour limit"
        )
        # No window reported: what Codex says of ordinary use decides.
        self.assertEqual(
            ops_check.quota_result({"ordinaryUsageAllowed": False}).status, ops_check.CRITICAL
        )
        self.assertEqual(ops_check.quota_result({}).status, ops_check.OK)
        self.assertEqual(ops_check.window_name(90), "90-minute")
        self.assertEqual(ops_check.window_name(None), "usage")

    def fake_codex(self, replies: str) -> str:
        """A stand-in for codex app-server: it reads the requests and
        writes `replies`, a Python expression of the lines it answers."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = os.path.join(directory, "codex")
        with open(path, "w") as f:
            f.write(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "requests = [json.loads(sys.stdin.readline()) for _ in range(3)]\n"
                "assert requests[2]['method'] == 'account/rateLimits/read', requests\n"
                f"for line in {replies}: print(json.dumps(line), flush=True)\n"
                "sys.stdin.read()\n"
            )
        os.chmod(path, 0o700)
        return path

    def test_reads_the_limits_through_the_app_server(self) -> None:
        reading = self.reading(85)
        codex = self.fake_codex(
            f"[{{'id': 1, 'result': {{}}}}, {{'method': 'remoteControl/status/changed'}}, "
            f"{{'id': 2, 'result': {reading!r}}}]"
        )
        with (
            mock.patch.object(ops_check, "CODEX", codex),
            mock.patch.object(ops_check, "as_user", lambda user, *command: list(command)),
        ):
            self.assertEqual(ops_check.read_codex_limits(), reading)
            self.assertEqual(ops_check.check_codex_quota().status, ops_check.WARN)

    def test_a_refusal_or_silence_is_an_error(self) -> None:
        refusing = self.fake_codex("[{'id': 2, 'error': {'message': 'not signed in'}}]")
        silent = self.fake_codex("[{'id': 1, 'result': {}}]")
        ended = self.fake_codex("[]")
        with open(ended) as f:
            # It closes its output at once rather than wait for its input.
            source = f.read().replace("sys.stdin.read()", "sys.exit(0)")
        with open(ended, "w") as f:
            f.write(source)
        with (
            mock.patch.object(ops_check, "as_user", lambda user, *command: list(command)),
            mock.patch.object(ops_check, "CODEX_QUOTA_TIMEOUT", 1),
        ):
            for codex, refusal in (
                (refusing, "not signed in"),
                (silent, "did not report its limits"),
                (ended, "did not report its limits"),
            ):
                with (
                    mock.patch.object(ops_check, "CODEX", codex),
                    self.assertRaisesRegex(RuntimeError, refusal),
                ):
                    ops_check.read_codex_limits()
        with mock.patch.object(ops_check, "CODEX", "/nonexistent/codex"):
            self.assertEqual(ops_check.check_codex_quota().status, ops_check.WARN)

    def test_runs_at_most_every_fifteen_minutes(self) -> None:
        reads: list[int] = []

        def quota() -> object:
            reads.append(1)
            return ops_check.Result(
                ops_check.OK, "Codex has used 10% of its 7-day limit", "codex-quota"
            )

        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        with (
            mock.patch.object(ops_check, "STATE_FILE", os.path.join(directory, "state.json")),
            mock.patch.dict(ops_check.CHECKS, {"codex-quota": quota}, clear=True),
            mock.patch.object(sys, "argv", ["armada-ops-check"]),
        ):
            for now, expected in ((1000.0, 1), (1890.0, 1), (1910.0, 2)):
                with mock.patch.object(ops_check.time, "time", return_value=now):
                    ops_check.main()
                self.assertEqual(sum(reads), expected)


class OpsPostTests(TestCase):
    """What the ops bot posts in #ops."""

    def posted(self, result: object, previous: str) -> dict[str, list[str]]:
        conf = {
            ("armada", "ops_bot_email"): "ops-bot@example.com",
            ("armada", "ops_host"): "chat.example.com",
        }
        secrets = mock.Mock()
        secrets.get.return_value = "api-key"
        urlopen = mock.MagicMock()
        ops_check.external_host.cache_clear()
        self.addCleanup(ops_check.external_host.cache_clear)
        with (
            mock.patch.object(ops_check, "zulip_conf_get", lambda *key: conf.get(key, "")),
            mock.patch.object(ops_check, "secrets_conf", secrets),
            mock.patch.object(ops_check.urllib.request, "urlopen", urlopen),
        ):
            ops_check.post("file-backup", result, previous)
        [request] = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(request.full_url, "https://chat.example.com/api/v1/messages")
        return urllib.parse.parse_qs(request.data.decode())

    def test_an_alert_links_its_runbook_section(self) -> None:
        # The agent asked about it in #ops works in that channel's own
        # repository, so the alert links the runbook rather than name a path.
        result = ops_check.Result(
            ops_check.CRITICAL,
            "no file snapshot in 2 hours; no nightly backup in 26 hours",
            "file-backup",
        )
        posted = self.posted(result, ops_check.OK)
        self.assertEqual((posted["to"], posted["topic"]), (["ops"], ["file-backup"]))
        self.assertEqual(
            posted["content"],
            [
                "**Critical**: no file snapshot in 2 hours; no nightly backup in 26 hours.\n"
                "Runbook: [file-backup](https://github.com/plugyawn/armada/blob/ui-overhaul/"
                "docs/armada/operations.md#file-backup)"
            ],
        )

    def test_a_recovery_says_what_it_was(self) -> None:
        result = ops_check.Result(ops_check.OK, "last snapshot 5 minutes ago", "file-backup")
        self.assertEqual(
            self.posted(result, ops_check.CRITICAL)["content"],
            ["**Recovered** (critical before): last snapshot 5 minutes ago."],
        )
