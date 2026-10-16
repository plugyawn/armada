import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, Future
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import FrameType
from typing import Any, TypeVar
from unittest import mock

import responses
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import override_settings
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.armada_plugin_host import (
    ARMADA_PLUGIN_STARTUP_SECONDS,
    REDACTED,
    ArmadaPluginHost,
    _alive,
    _git_url,
    _pump,
)
from zerver.lib.armada_plugins import (
    armada_plugin_token,
    install_plugin,
    remove_plugin,
    update_plugin,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import PluginInstall, Repository, Stream, UserProfile

FIXTURE = Path(__file__).parent / "fixtures" / "armada_plugins" / "fake"
FAKE_KEY = "fake-key-0123456789abcdef"
T = TypeVar("T")


class ImmediateExecutor(Executor):
    """Runs what it is given at once, so that a test's pass holds the
    checkout's fetch and setup, and coverage sees them."""

    @override
    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> "Future[T]":
        future: Future[T] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as error:
            future.set_exception(error)
        return future


def environ_of(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    return dict(entry.split("=", 1) for entry in raw.decode().split("\0") if entry)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class PluginHostTest(ZulipTestCase):
    """The plugin host (zerver/lib/armada_plugin_host.py), with a fake
    plugin written in the standard library: it starts a plugin at its
    commit with an environment of its own, restarts it with backoff, marks
    it Failed after five failures in a row, stops it when it is removed or
    turned off, and restarts it when what it was started with changes."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.plugin_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.plugin_dir, ignore_errors=True)
        self.now = 1000.0
        self.host = ArmadaPluginHost(
            plugin_dir=self.plugin_dir, clock=lambda: self.now, executor=ImmediateExecutor()
        )
        self.addCleanup(self.host.stop_all)
        self.hamlet = self.example_user("hamlet")
        # The tests may not reach the internet; the fake plugin, on this
        # machine, they do reach.
        self.network = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def manifest(self, **changes: Any) -> dict[str, Any]:
        manifest = json.loads((FIXTURE / "armada-plugin.json").read_text())
        manifest.update(changes)
        return manifest

    def repository(self, *manifests: dict[str, Any], path: str = "") -> tuple[str, list[str]]:
        """A git repository holding the fake plugin in its directory `path`,
        one commit a manifest."""
        repository = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repository, ignore_errors=True)
        (repository / path).mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE / "armada_fake_plugin.py", repository / path)
        (repository / "README.md").write_text("The team's plugins.\n")
        git = ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "init", "-q"], check=True)
        shas = []
        for manifest in manifests:
            (repository / path / "armada-plugin.json").write_text(json.dumps(manifest))
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", manifest["id"]], check=True)
            shas.append(
                subprocess.run(
                    [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
                ).stdout.strip()
            )
        return str(repository), shas

    def install(
        self,
        manifest: dict[str, Any] | None = None,
        *,
        settings: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        sha: str | None = None,
        source: str | None = None,
        **where: Any,
    ) -> PluginInstall:
        manifest = manifest or self.manifest()
        if source is None:
            source, [found] = self.repository(manifest)
            sha = sha or found
        assert sha is not None
        return install_plugin(
            self.hamlet,
            manifest=manifest,
            **{"source_kind": "local", "source": source, "sha": sha, **where},
            plugin_settings=settings or {},
            secrets={"key": FAKE_KEY} if secrets is None else secrets,
        )

    def tick_until(self, done: Callable[[], bool], *, step: float = 0.25) -> None:
        """Pass after pass, the host's clock moving `step` seconds a pass,
        until done() is true; real processes take real time, so each pass
        also waits a little."""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.host.tick()
            if done():
                return
            self.now += step
            time.sleep(0.02)
        raise AssertionError("the host did not get there")

    def state(self, install: PluginInstall) -> int:
        install.refresh_from_db()
        return install.state

    def pid(self, install: PluginInstall) -> int:
        process = self.host.supervised[install.id].process
        assert process is not None
        return process.pid

    def running(self, install: PluginInstall) -> bool:
        """Running, by the host's own account too: a restart keeps the
        state it had."""
        supervised = self.host.supervised.get(install.id)
        return (
            self.state(install) == PluginInstall.RUNNING
            and supervised is not None
            and supervised.phase == "running"
        )

    def test_a_plugin_runs_with_an_environment_of_its_own(self) -> None:
        source, [one, two] = self.repository(
            self.manifest(),
            self.manifest(id="fake-two", secrets={"key": {"title": "Key", "env": "OTHER_KEY"}}),
        )
        fake = self.install(source=source, sha=one)
        other = self.install(
            self.manifest(id="fake-two", secrets={"key": {"title": "Key", "env": "OTHER_KEY"}}),
            source=source,
            sha=two,
            secrets={"key": "other-key-0987654321"},
        )
        self.tick_until(lambda: self.running(fake) and self.running(other))
        fake.refresh_from_db()
        assert fake.port is not None
        environment = environ_of(self.pid(fake))
        uv_dir = os.path.dirname("/usr/local/bin/uv")
        base = self.plugin_dir / f"fake-{fake.id}"
        self.assertEqual(
            environment,
            {
                "PATH": f"{uv_dir}:/usr/local/bin:/usr/bin:/bin",
                "HOME": str(base / "home"),
                "LANG": "C.UTF-8",
                "UV_CACHE_DIR": str(self.plugin_dir / ".uv-cache"),
                "UV_PYTHON_INSTALL_DIR": str(self.plugin_dir / ".uv-python"),
                "FAKE_KEY": FAKE_KEY,
                "ARMADA_SITE": fake.realm.url,
                "ARMADA_EMAIL": fake.bot.email,
                "ARMADA_API_KEY": fake.bot.api_key,
                "ARMADA_PLUGIN_TOKEN": armada_plugin_token(fake),
                "ARMADA_PORT": str(fake.port),
                "ARMADA_SETTINGS": "{}",
            },
        )
        # Another plugin's secret is in its environment alone.
        self.assertEqual(environ_of(self.pid(other))["OTHER_KEY"], "other-key-0987654321")
        self.assertNotIn("other-key-0987654321", environ_of(self.pid(fake)).values())
        self.assertNotIn(FAKE_KEY, environ_of(self.pid(other)).values())
        self.assertNotEqual(fake.port, PluginInstall.objects.get(id=other.id).port)
        # It runs as the host does, from its checkout at its commit, set up once.
        self.assertEqual(os.stat(f"/proc/{self.pid(fake)}").st_uid, os.getuid())
        checkout = base / one
        self.assertEqual(os.readlink(f"/proc/{self.pid(fake)}/cwd"), str(checkout))
        self.assertEqual((checkout / "setup-ran").read_text(), "1")
        self.assertEqual((base / "pid").read_text(), str(self.pid(fake)))
        self.assertEqual((fake.failures, fake.state_detail), (0, ""))
        self.assertIn(
            f"fake plugin {fake.bot.email} starting on {fake.port}",
            (base / "plugin.log").read_text(),
        )

    def test_a_crash_is_retried_ever_less_often_and_five_show_failed(self) -> None:
        install = self.install(settings={"exit_at_start": True})
        delays: list[float] = []
        states: list[int] = []
        while len(delays) < 10:
            failures = install.failures

            def failed_again(failures: int = failures) -> bool:
                return PluginInstall.objects.get(id=install.id).failures > failures

            self.tick_until(failed_again)
            install.refresh_from_db()
            delays.append(self.host.supervised[install.id].retry_at - self.now)
            states.append(install.state)
            self.now = self.host.supervised[install.id].retry_at
        self.assertEqual(delays, [1, 2, 4, 8, 16, 32, 64, 128, 256, 300])
        self.assertEqual(states, [PluginInstall.STARTING] * 4 + [PluginInstall.FAILED] * 6)
        self.assertEqual(
            install.state_detail.splitlines()[-2:],
            ["crashing on purpose", "It exited with status 3."],
        )
        # Retry tries at once, with its count back at zero.
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(f"/json/armada/plugins/{install.id}/retry"))
        install.refresh_from_db()
        self.assertEqual((install.state, install.failures), (PluginInstall.STARTING, 0))
        self.tick_until(lambda: PluginInstall.objects.get(id=install.id).failures == 1, step=0)
        self.assert_json_error(
            self.client_post(f"/json/armada/plugins/{install.id}/retry"), "Fake has not failed."
        )
        # Fixed, it runs, and the failures are forgotten.
        update_plugin(self.hamlet, install, plugin_settings={})
        self.tick_until(lambda: self.running(install))
        self.assertEqual((install.failures, install.state_detail), (0, ""))

    def test_a_process_that_stops_answering_is_restarted(self) -> None:
        install = self.install(settings={"unhealthy": True})
        self.tick_until(
            lambda: (
                install.id in self.host.supervised
                and self.host.supervised[install.id].phase == "starting"
            )
        )
        started = self.host.supervised[install.id].started
        self.tick_until(lambda: PluginInstall.objects.get(id=install.id).failures == 1)
        install.refresh_from_db()
        self.assertGreater(self.now - started, ARMADA_PLUGIN_STARTUP_SECONDS)
        self.assertEqual(install.state_detail.splitlines()[-1], "It did not answer GET /health.")
        # A running process that exits is restarted too.
        update_plugin(self.hamlet, install, plugin_settings={"crash_after": 0.5})
        self.tick_until(lambda: self.running(install))
        self.tick_until(lambda: PluginInstall.objects.get(id=install.id).failures == 1, step=0)
        install.refresh_from_db()
        self.assertEqual(install.state_detail.splitlines()[-1], "It exited with status 4.")
        self.tick_until(lambda: self.running(install), step=2)

    def test_remove_and_turning_off_stop_it(self) -> None:
        install = self.install()
        self.tick_until(lambda: self.running(install))
        pid = self.pid(install)
        update_plugin(self.hamlet, install, disabled=True)
        self.tick_until(lambda: gone(pid))
        install.refresh_from_db()
        self.assertEqual(install.state, PluginInstall.STARTING)
        self.assertNotIn(install.id, self.host.supervised)
        self.assertFalse((self.plugin_dir / f"fake-{install.id}" / "pid").exists())
        update_plugin(self.hamlet, install, disabled=False)
        self.tick_until(lambda: self.running(install))
        pid = self.pid(install)
        remove_plugin(self.hamlet, install)
        self.tick_until(lambda: gone(pid))
        self.assertEqual(self.state(install), PluginInstall.REMOVED)
        self.assertEqual(self.host.supervised, {})

    def test_what_it_was_started_with_changing_restarts_it(self) -> None:
        install = self.install()
        self.tick_until(lambda: self.running(install))
        first = self.pid(install)
        update_plugin(self.hamlet, install, plugin_settings={"crash_after": 3600})
        self.tick_until(lambda: gone(first) and self.running(install))
        second = self.pid(install)
        self.assertEqual(environ_of(second)["ARMADA_SETTINGS"], '{"crash_after": 3600}')
        update_plugin(self.hamlet, install, secrets={"key": "a-new-fake-key-000000"})
        self.tick_until(lambda: gone(second) and self.running(install))
        third = self.pid(install)
        self.assertEqual(environ_of(third)["FAKE_KEY"], "a-new-fake-key-000000")
        # So does a new key for its bot, and a rotated server key.
        UserProfile.objects.filter(id=install.bot_id).update(api_key="b" * 32)
        self.tick_until(lambda: gone(third) and self.running(install))
        self.assertEqual(environ_of(self.pid(install))["ARMADA_API_KEY"], "b" * 32)
        fourth = self.pid(install)
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=Fernet.generate_key().decode()):
            install.refresh_from_db()
            token = armada_plugin_token(install)
            # Its secret was kept under the old key: it waits to be set again.
            self.tick_until(lambda: gone(fourth) and self.state(install) == PluginInstall.FAILED)
            self.assertEqual(
                install.state_detail,
                "Its secrets were kept under a key this server no longer has; set them again.",
            )
            update_plugin(self.hamlet, install, secrets={"key": FAKE_KEY})
            self.tick_until(lambda: self.running(install))
            self.assertEqual(environ_of(self.pid(install))["ARMADA_PLUGIN_TOKEN"], token)
        # The port is kept from start to start.
        install.refresh_from_db()
        self.assertEqual(environ_of(self.pid(install))["ARMADA_PORT"], str(install.port))

    def test_an_install_waiting_or_failed_for_good_does_not_run(self) -> None:
        required = self.manifest(
            secrets={"key": {"title": "Key", "env": "FAKE_KEY", "required": True}}
        )
        waiting = self.install(required, secrets={})
        self.assertEqual(waiting.state, PluginInstall.WAITING)
        self.host.tick()
        self.assertNotIn(waiting.id, self.host.supervised)
        gpus = self.install(self.manifest(id="keyless"), secrets={})
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=None, DEVELOPMENT=False):
            self.host.tick()
            gpus.refresh_from_db()
            self.assertEqual(
                (gpus.state, gpus.state_detail),
                (
                    PluginInstall.FAILED,
                    "This server has no armada_plugin_secrets_key in zulip-secrets.conf,"
                    " so it cannot keep a plugin's secrets or give it a token.",
                ),
            )
        self.assertNotIn(gpus.id, self.host.supervised)

    def test_the_commit_must_hold_the_manifest_it_was_installed_with(self) -> None:
        source, [sha] = self.repository(self.manifest())
        changed = self.install(self.manifest(version="1.0.1"), source=source, sha=sha)
        self.tick_until(lambda: PluginInstall.objects.get(id=changed.id).failures == 1)
        changed.refresh_from_db()
        self.assertEqual(
            changed.state_detail, f"The manifest at {sha} is not the one Fake was installed with."
        )
        self.assertFalse((self.plugin_dir / f"fake-{changed.id}" / sha / "setup-ran").exists())
        # It may have been the network: it is fetched again a second later,
        # and the fetch's outcome is read on the pass after.
        self.now += 1
        self.host.tick()
        self.host.tick()
        self.assertEqual(PluginInstall.objects.get(id=changed.id).failures, 2)
        remove_plugin(self.hamlet, changed)

        missing = self.install(source=source, sha="0" * 40)
        self.tick_until(lambda: PluginInstall.objects.get(id=missing.id).failures == 1)
        missing.refresh_from_db()
        self.assertTrue(
            missing.state_detail.startswith(f"Could not fetch {source} at {'0' * 40}: "),
            missing.state_detail,
        )
        remove_plugin(self.hamlet, missing)

        # A repository that is gone from the server: its row was deleted.
        gone = self.install(source=source, sha=sha)
        PluginInstall.objects.filter(id=gone.id).update(source="")
        self.tick_until(lambda: PluginInstall.objects.get(id=gone.id).failures == 1)
        gone.refresh_from_db()
        self.assertEqual(gone.state_detail, "Its repository is no longer on this server.")
        remove_plugin(self.hamlet, gone)

        broken = self.manifest(
            run={
                "setup": ["python3", "-c", "raise SystemExit('no uv here')"],
                "command": ["python3", "armada_fake_plugin.py"],
            }
        )
        failing = self.install(broken)
        self.tick_until(lambda: PluginInstall.objects.get(id=failing.id).failures == 1)
        failing.refresh_from_db()
        self.assertEqual(failing.state_detail, "Its setup failed: no uv here")
        remove_plugin(self.hamlet, failing)

        git = ["git", "-C", source, "-c", "user.name=t", "-c", "user.email=t@example.com"]
        Path(source, "armada-plugin.json").write_text("{")
        subprocess.run([*git, "commit", "-q", "-am", "broken"], check=True)
        broken_sha = subprocess.run(
            [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        unreadable = self.install(source=source, sha=broken_sha)
        self.tick_until(lambda: PluginInstall.objects.get(id=unreadable.id).failures == 1)
        unreadable.refresh_from_db()
        self.assertTrue(
            unreadable.state_detail.startswith(
                f"The repository has no valid armada-plugin.json at {broken_sha}: "
            ),
            unreadable.state_detail,
        )
        remove_plugin(self.hamlet, unreadable)

        unknown = self.install(self.manifest(run={"command": ["no-such-program"]}))
        self.tick_until(lambda: PluginInstall.objects.get(id=unknown.id).failures == 1)
        unknown.refresh_from_db()
        self.assertEqual(unknown.state_detail, "It could not start: No such file or directory.")

    def test_what_a_plugin_writes_never_shows_a_secret(self) -> None:
        install = self.install(settings={"say_secrets": True, "exit_at_start": True})
        self.tick_until(lambda: PluginInstall.objects.get(id=install.id).failures == 1)
        install.refresh_from_db()
        log = (self.plugin_dir / f"fake-{install.id}" / "plugin.log").read_text()
        said = f"key {REDACTED} bot {REDACTED} token {REDACTED}"
        self.assertIn(said, install.state_detail)
        self.assertIn(said, log)
        for secret in (FAKE_KEY, install.bot.api_key, armada_plugin_token(install)):
            self.assertNotIn(secret, install.state_detail)
            self.assertNotIn(secret, log)

    def test_a_remote_plugin_is_asked_for_its_health(self) -> None:
        manifest = self.manifest(remote={"url": "https://plugins.example.com/fake/"})
        del manifest["run"], manifest["secrets"]
        install = self.install(manifest, source="", sha="", secrets={}, source_kind="remote")
        health = self.network.get("https://plugins.example.com/fake/health", body="ok")
        self.host.tick()
        self.assertEqual(self.state(install), PluginInstall.RUNNING)
        self.network.replace(responses.GET, "https://plugins.example.com/fake/health", status=500)
        for failures in range(1, 6):
            self.now += 10
            self.host.tick()
            install.refresh_from_db()
            self.assertEqual(install.failures, failures)
        self.assertEqual(
            (install.state, install.state_detail),
            (PluginInstall.FAILED, "https://plugins.example.com/fake/health did not answer."),
        )
        self.assertEqual(health.call_count, 1)
        # Nothing runs here for it.
        self.assertIsNone(self.host.supervised[install.id].process)
        self.assertFalse((self.plugin_dir / f"fake-{install.id}").exists())

    def test_a_port_is_free_and_its_own(self) -> None:
        first = self.install()
        self.tick_until(lambda: self.running(first))
        first.refresh_from_db()
        assert first.port is not None
        taken = first.port
        free = ArmadaPluginHost._free_port()
        # The first port offered is the other install's; the second is free.
        second = self.install(self.manifest(id="second"))
        with mock.patch.object(ArmadaPluginHost, "_free_port", side_effect=[taken, free]):
            self.tick_until(lambda: self.running(second))
        second.refresh_from_db()
        self.assertEqual(second.port, free)
        # A port someone else took meanwhile is given up for a free one.
        update_plugin(self.hamlet, first, disabled=True)
        self.tick_until(lambda: first.id not in self.host.supervised)
        with socket.socket() as squatter:
            squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            squatter.bind(("127.0.0.1", taken))
            squatter.listen()
            update_plugin(self.hamlet, first, disabled=False)
            self.tick_until(lambda: self.running(first))
        first.refresh_from_db()
        self.assertNotEqual(first.port, taken)
        # With no port to be had, it fails, and is tried again later.
        third = self.install(self.manifest(id="third"))
        with mock.patch.object(ArmadaPluginHost, "_free_port", return_value=free):
            self.tick_until(lambda: PluginInstall.objects.get(id=third.id).failures == 1)
        third.refresh_from_db()
        self.assertEqual(
            third.state_detail, "It could not have a port: no free port for the plugin."
        )

    def test_one_install_going_wrong_does_not_stop_the_others(self) -> None:
        broken = self.install()
        working = self.install(self.manifest(id="working"))
        reconcile = ArmadaPluginHost._reconcile

        def fails_for_one(host: ArmadaPluginHost, install: PluginInstall) -> None:
            if install.id == broken.id:
                raise RuntimeError("a bug")
            reconcile(host, install)

        with (
            mock.patch.object(ArmadaPluginHost, "_reconcile", fails_for_one),
            self.assertLogs("zerver.lib.armada_plugin_host", "ERROR") as logs,
        ):
            self.tick_until(lambda: self.running(working))
        self.assertEqual(
            logs.output[0].splitlines()[0],
            f"ERROR:zerver.lib.armada_plugin_host:The plugin host could not run install {broken.id}",
        )

    def test_orphans_of_an_earlier_host_are_stopped(self) -> None:
        base = self.plugin_dir / "fake-99"
        base.mkdir()
        orphan = subprocess.Popen(["sleep", "60"], cwd=base, start_new_session=True)
        stranger = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(stranger.kill)
        (base / "pid").write_text(str(orphan.pid))
        (self.plugin_dir / "other-98").mkdir()
        (self.plugin_dir / "other-98" / "pid").write_text(str(stranger.pid))
        (self.plugin_dir / "bad-97").mkdir()
        (self.plugin_dir / "bad-97" / "pid").write_text("not a pid")
        self.host.reap_orphans()
        self.assertEqual(orphan.wait(5), -signal.SIGTERM)
        # A process that is not a plugin's is left alone.
        self.assertIsNone(stranger.poll())
        self.assertEqual(list(self.plugin_dir.glob("*/pid")), [])

    def test_the_loop_outlives_a_failed_pass_and_stops_its_plugins(self) -> None:
        stop = threading.Event()
        passes = 0

        def failing_pass() -> None:
            nonlocal passes
            passes += 1
            if passes == 2:
                stop.set()
            raise RuntimeError("a bug")

        with (
            mock.patch("zerver.lib.armada_plugin_host.ARMADA_PLUGIN_TICK_SECONDS", 0),
            # Closing the connection would end the test's transaction.
            mock.patch("zerver.lib.armada_plugin_host.close_old_connections") as close,
            mock.patch.object(self.host, "reap_orphans") as reap_orphans,
            mock.patch.object(self.host, "tick", side_effect=failing_pass),
            mock.patch.object(self.host, "stop_all") as stop_all,
            self.assertLogs("zerver.lib.armada_plugin_host", "ERROR") as logs,
        ):
            self.host.run_forever(stop)
        reap_orphans.assert_called_once_with()
        self.assertEqual(passes, 2)
        self.assertEqual(close.call_count, 2)
        self.assertEqual(
            [line.splitlines()[0] for line in logs.output],
            ["ERROR:zerver.lib.armada_plugin_host:The plugin host's pass failed"] * 2,
        )
        stop_all.assert_called_once_with()

    def test_the_command_runs_the_host_until_it_is_signalled(self) -> None:
        plugin_dir = self.plugin_dir / "made-by-the-command"
        handlers: dict[int, Callable[[int, FrameType | None], None]] = {}

        def run_forever(host: ArmadaPluginHost, stop: threading.Event) -> None:
            self.assertEqual(host.plugin_dir, plugin_dir)
            self.assertTrue(plugin_dir.is_dir())
            self.assertFalse(stop.is_set())
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertTrue(stop.is_set())

        with (
            override_settings(ARMADA_PLUGIN_DIR=str(plugin_dir)),
            # The test process keeps its own handlers.
            mock.patch("signal.signal", side_effect=handlers.__setitem__),
            mock.patch.object(
                ArmadaPluginHost, "run_forever", autospec=True, side_effect=run_forever
            ) as run,
        ):
            call_command("armada_plugin_host")
        run.assert_called_once()
        self.assertEqual(set(handlers), {signal.SIGTERM, signal.SIGINT})

    def test_the_host_purges_removals_and_old_checkouts(self) -> None:
        install = self.install()
        self.tick_until(lambda: self.running(install))
        base = self.plugin_dir / f"fake-{install.id}"
        (base / ("f" * 40)).mkdir()
        (self.plugin_dir / "gone-12345").mkdir()
        (self.plugin_dir / "notes-here").mkdir()
        self.host.next_purge = 0
        self.host.tick()
        self.assertEqual(
            sorted(path.name for path in base.iterdir() if path.is_dir()),
            sorted([install.sha, "home"]),
        )
        self.assertFalse((self.plugin_dir / "gone-12345").exists())
        self.assertTrue((self.plugin_dir / "notes-here").exists())
        remove_plugin(self.hamlet, install)
        PluginInstall.objects.filter(id=install.id).update(
            date_removed=timezone_now() - timedelta(days=8)
        )
        self.now += 3600
        self.host.tick()
        self.assertFalse(PluginInstall.objects.filter(id=install.id).exists())
        self.host.next_purge = 0
        self.host.tick()
        self.assertFalse(base.exists())

    def test_the_output_pump_blanks_secrets_and_bounds_the_log(self) -> None:
        log = self.plugin_dir / "plugin.log"
        lines: deque[str] = deque(maxlen=2)
        output = BytesIO(b"one hunter2\ntwo\nthree hunter2 hunter2\n\xff\n")
        with mock.patch("zerver.lib.armada_plugin_host.ARMADA_PLUGIN_LOG_LIMIT_BYTES", 20):
            _pump(output, lines, log, ["hunter2"])
        self.assertEqual(list(lines), [f"three {REDACTED} {REDACTED}", "\ufffd"])
        # Past its limit, the log starts again.
        self.assertEqual(log.read_text(), "\ufffd\n")

    def test_where_a_repository_is_fetched_from(self) -> None:
        self.assertEqual(
            _git_url("github.com/plugyawn/armada-gpus"),
            "https://github.com/plugyawn/armada-gpus.git",
        )
        self.assertFalse(_alive(2**22 + 1))
        self.assertTrue(_alive(os.getpid()))

    def test_a_slow_fetch_does_not_hold_up_the_pass(self) -> None:
        pending: list[tuple[Future[Any], Callable[[], Any]]] = []

        class Later(Executor):
            @override
            def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> "Future[T]":
                future: Future[T] = Future()
                pending.append((future, lambda: fn(*args, **kwargs)))
                return future

        self.host = ArmadaPluginHost(
            plugin_dir=self.plugin_dir, clock=lambda: self.now, executor=Later()
        )
        install = self.install()
        self.host.tick()
        self.host.tick()
        self.assertEqual(self.host.supervised[install.id].phase, "preparing")
        [(future, fetch)] = pending
        future.set_result(fetch())
        self.tick_until(lambda: self.running(install))

    def test_a_plugin_of_the_teams_repository_runs_from_its_directory(self) -> None:
        manifest = self.manifest()
        source, [sha] = self.repository(manifest, path="plugins/fake")
        repository = Repository.objects.create(
            realm=self.hamlet.realm,
            stream=Stream.objects.get(realm=self.hamlet.realm, name="Verona"),
            repo_cache_path=source,
        )
        install = self.install(
            manifest, source="", sha=sha, repository_id=repository.id, path="plugins/fake"
        )
        self.tick_until(lambda: self.running(install))
        root = self.plugin_dir / f"fake-{install.id}" / sha / "plugins" / "fake"
        self.assertEqual(os.readlink(f"/proc/{self.pid(install)}/cwd"), str(root))
        self.assertEqual((root / "setup-ran").read_text(), "1")
        # Only the plugin's directory is taken from the repository.
        self.assertFalse((self.plugin_dir / f"fake-{install.id}" / sha / "README.md").exists())

    def test_a_plugin_on_github_is_fetched_at_its_commit(self) -> None:
        manifest = self.manifest()
        source, [sha] = self.repository(manifest)
        # The tests have no network: GitHub's address leads to the repository.
        with mock.patch("zerver.lib.armada_plugin_host._git_url", return_value=source) as url:
            install = self.install(
                manifest, source="github.com/plugyawn/armada-fake", sha=sha, source_kind="github"
            )
            self.tick_until(lambda: self.running(install))
        url.assert_called_with("github.com/plugyawn/armada-fake")
        checkout = self.plugin_dir / f"fake-{install.id}" / sha
        self.assertEqual(os.readlink(f"/proc/{self.pid(install)}/cwd"), str(checkout))
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            sha,
        )

    def test_a_draft_runs_only_in_a_trial(self) -> None:
        manifest = self.manifest()
        source, [sha] = self.repository(manifest)
        draft = install_plugin(
            self.hamlet,
            manifest=manifest,
            source_kind="local",
            source=source,
            sha=sha,
            draft=True,
        )
        self.host.tick()
        self.assertNotIn(draft.id, self.host.supervised)
