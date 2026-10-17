"""The plugin host (design/r9/plugins, section 3): one supervised program,
manage.py armada_plugin_host, that makes the plugin processes running on
this server match the installs in the database. Plugin code never runs in a
Django or Tornado process.

For each install that should run, the host:

1. fetches its code at the pinned commit into
   ARMADA_PLUGIN_DIR/<plugin>-<install>/<sha>: from GitHub, or, for a
   plugin of one of this server's repositories (the team's plugins
   repository, ROUND2.md), the plugin's directory of that repository with
   git archive; and refuses it if the manifest there is not the one it was
   installed with;
2. runs the manifest's setup there, once a commit;
3. starts the manifest's command as its own user (zulip), in a fresh
   environment holding the plugin's bot, token, port, settings and own
   secrets, and nothing of the host's;
4. waits for GET /health on 127.0.0.1:<port>, and asks again every 10 s;
5. restarts it when it exits or stops answering, after 1 s, then 2, 4, up
   to 5 minutes; after five failures in a row the install shows Failed,
   with its last lines, and the host keeps trying at that pace until it
   answers again or someone presses Retry, which tries at once;
6. stops it when the install is removed, turned off or waiting, and
   restarts it when anything it was started with changes.

A remote plugin runs elsewhere; the host asks its /health the same way.
What a process writes (its last lines, kept on the install, and its log
beside its checkout) has its secrets, its bot's key and its token blanked.
The host also purges removed installs after a week, and their checkouts,
and on each pass sends the widget documents that waited out their two
seconds and marks the widgets that went quiet as stale. It keeps each
organization's plugins repository mirrored, when it has a mirror
(zerver/lib/armada_plugin_repository.py).
"""

import contextlib
import errno
import hashlib
import io
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.db import close_old_connections
from django.utils.timezone import now as timezone_now

from zerver.lib.armada_plugin_repository import (
    ArmadaPluginMirrorError,
    push_team_plugin_mirror,
    team_plugin_mirror_is_behind,
    team_plugin_repositories,
)
from zerver.lib.armada_plugin_validation import (
    ARMADA_PLUGIN_MANIFEST_FILE,
    ArmadaPluginContractError,
    canonicalize_plugin_manifest,
)
from zerver.lib.armada_plugins import (
    ArmadaPluginKeyError,
    armada_plugin_secret_environment,
    armada_plugin_session,
    armada_plugin_token,
    armada_plugin_url,
    purge_removed_plugins,
    set_plugin_state,
)
from zerver.lib.armada_widgets import flush_widget_events, mark_stale_widgets
from zerver.models import PluginInstall

logger = logging.getLogger(__name__)

ARMADA_PLUGIN_FAILURE_LIMIT = 5
ARMADA_PLUGIN_BACKOFF_FIRST_SECONDS = 1.0
ARMADA_PLUGIN_BACKOFF_LIMIT_SECONDS = 300.0
# How long a new process has to answer /health, and how often it is asked.
ARMADA_PLUGIN_STARTUP_SECONDS = 30.0
ARMADA_PLUGIN_HEALTH_SECONDS = 10.0
ARMADA_PLUGIN_HEALTH_TIMEOUT_SECONDS = 2.0
ARMADA_PLUGIN_GIT_TIMEOUT_SECONDS = 300
ARMADA_PLUGIN_SETUP_TIMEOUT_SECONDS = 600
ARMADA_PLUGIN_STOP_SECONDS = 5.0
ARMADA_PLUGIN_LAST_LINES = 20
ARMADA_PLUGIN_LOG_LIMIT_BYTES = 1_000_000
ARMADA_PLUGIN_PURGE_SECONDS = 3600.0
ARMADA_PLUGIN_TICK_SECONDS = 0.5
# How often a plugins repository is compared with what its mirror took,
# and the longest a failed push waits before it is tried again.
ARMADA_PLUGIN_MIRROR_CHECK_SECONDS = 30.0
ARMADA_PLUGIN_MIRROR_BACKOFF_LIMIT_SECONDS = 3600.0
READY_MARK = ".armada-ready"
REDACTED = "[secret]"


class ArmadaPluginPrepareError(Exception):
    """The install cannot be run as it is: retrying will not help."""


def _base_dir(plugin_dir: Path, install: PluginInstall) -> Path:
    return plugin_dir / f"{install.plugin_id}-{install.id}"


def _git_url(source: str) -> str:
    return f"https://{source}.git"


def _origin(install: PluginInstall) -> str:
    """Where the host fetches from: GitHub, or a repository on this disk."""
    if install.source_kind == PluginInstall.GITHUB:
        return _git_url(install.source)
    if install.source_repository is not None:
        return install.source_repository.repo_cache_path
    return install.source


def last_lines(output: str, count: int = ARMADA_PLUGIN_LAST_LINES) -> str:
    return "\n".join(output.strip().splitlines()[-count:])


def plugin_base_environment(plugin_dir: Path, home: Path) -> dict[str, str]:
    """What every plugin process starts with: a PATH that finds Zulip's
    uv first, its own home, and uv's cache and Pythons shared between
    installs."""
    uv_dir = os.path.dirname(settings.ARMADA_PLUGIN_UV)
    return {
        "PATH": f"{uv_dir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "UV_CACHE_DIR": str(plugin_dir / ".uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(plugin_dir / ".uv-python"),
    }


def _run(argv: list[str], *, timeout: int, **kwargs: Any) -> str:
    completed = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
        **kwargs,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, argv, completed.stdout)
    return completed.stdout


def _fetch(kind: str, origin: str, sha: str, path: str, checkout: Path) -> None:
    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    timeout = ARMADA_PLUGIN_GIT_TIMEOUT_SECONDS
    if kind == PluginInstall.GITHUB:
        _run(["git", "init", "-q", str(checkout)], timeout=timeout, env=git_env)
        _run(
            ["git", "-C", str(checkout), "fetch", "-q", "--depth", "1", origin, sha],
            timeout=timeout,
            env=git_env,
        )
        _run(
            [
                *("git", "-C", str(checkout), "-c", "advice.detachedHead=false"),
                *("checkout", "-q", "FETCH_HEAD"),
            ],
            timeout=timeout,
            env=git_env,
        )
        return
    # A repository on this server holds all of the team's plugins: only the
    # plugin's directory, at its commit, is taken from it.
    archive = subprocess.run(
        [
            "git",
            "-C",
            origin,
            "archive",
            "--format=tar",
            f"{sha}^{{commit}}",
            *(["--", path] if path else []),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        env=git_env,
        check=False,
    )
    if archive.returncode != 0:
        raise subprocess.CalledProcessError(
            archive.returncode, "git archive", archive.stderr.decode(errors="replace")
        )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(checkout, filter="data")


def prepare_checkout(
    plugin_dir: Path,
    base: Path,
    *,
    name: str,
    kind: str,
    origin: str,
    sha: str,
    path: str,
    manifest: dict[str, Any],
) -> Path:
    """The install's code at its commit, set up; returns the plugin's own
    directory in it. Runs in a worker thread, so it touches no database:
    what it needs is passed in."""
    checkout = base / sha
    root = checkout / path
    if (checkout / READY_MARK).exists():
        return root
    if checkout.exists():
        shutil.rmtree(checkout)
    checkout.mkdir(parents=True)
    if not origin:
        raise ArmadaPluginPrepareError("Its repository is no longer on this server.")
    try:
        _fetch(kind, origin, sha, path, checkout)
    except subprocess.CalledProcessError as error:
        raise ArmadaPluginPrepareError(
            f"Could not fetch {origin} at {sha}: {last_lines(error.output, 3)}"
        ) from error
    try:
        found = canonicalize_plugin_manifest(
            json.loads((root / ARMADA_PLUGIN_MANIFEST_FILE).read_text())
        )
    except (OSError, ValueError, ArmadaPluginContractError) as error:
        raise ArmadaPluginPrepareError(
            f"The repository has no valid {ARMADA_PLUGIN_MANIFEST_FILE} at {sha}: {error}"
        ) from error
    if found != manifest:
        raise ArmadaPluginPrepareError(
            f"The manifest at {sha} is not the one {name} was installed with."
        )
    setup = manifest["run"].get("setup")
    if setup is not None:
        home = base / "home"
        home.mkdir(exist_ok=True)
        try:
            _run(
                setup,
                timeout=ARMADA_PLUGIN_SETUP_TIMEOUT_SECONDS,
                cwd=root,
                env=plugin_base_environment(plugin_dir, home),
            )
        except (OSError, subprocess.SubprocessError) as error:
            output = getattr(error, "output", None) or str(error)
            raise ArmadaPluginPrepareError(f"Its setup failed: {last_lines(output)}") from error
    (checkout / READY_MARK).touch()
    return root


def _alive(pid: int) -> bool:
    """Whether the process runs: a zombie, waiting for its parent to reap
    it, has stopped."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def free_local_port() -> int:
    """A port on 127.0.0.1 that nothing holds now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def kill_process_group(pid: int, process: "subprocess.Popen[bytes] | None" = None) -> None:
    """SIGTERM to the process's group (a plugin's command may fork, as
    uv run does), then SIGKILL to what has not gone in five seconds."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + ARMADA_PLUGIN_STOP_SECONDS
    while time.monotonic() < deadline:
        if process is not None:
            if process.poll() is not None:
                break
        elif not _alive(pid):
            break
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)
    if process is not None:
        process.wait()


@dataclass
class Supervised:
    """One install as the host runs it."""

    install_id: int
    # What the process was started with; when it changes, it is restarted.
    fingerprint: str
    phase: str
    preparing: "Future[Path] | None" = None
    checkout: Path | None = None
    process: "subprocess.Popen[bytes] | None" = None
    reader: threading.Thread | None = None
    started: float = 0.0
    next_health: float = 0.0
    retry_at: float = 0.0
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=ARMADA_PLUGIN_LAST_LINES))


@dataclass
class Mirroring:
    """An organization's plugins repository as the host mirrors it."""

    pushing: "Future[None] | None" = None
    next_check: float = 0.0
    failures: int = 0


def pump_plugin_output(
    stream: IO[bytes], lines: deque[str], log_path: Path, hidden: list[str]
) -> None:
    """Keep what a process writes, with what it must not show blanked."""
    with open(log_path, "a", encoding="utf-8") as log:
        for raw in stream:
            line = raw.decode(errors="replace").rstrip("\n")
            for value in hidden:
                line = line.replace(value, REDACTED)
            lines.append(line)
            if log.tell() > ARMADA_PLUGIN_LOG_LIMIT_BYTES:
                log.truncate(0)
                log.seek(0)
            log.write(line + "\n")
            log.flush()


class ArmadaPluginHost:
    def __init__(
        self,
        *,
        plugin_dir: Path,
        clock: Callable[[], float] = time.monotonic,
        executor: Executor | None = None,
    ) -> None:
        self.plugin_dir = plugin_dir
        self.clock = clock
        # Fetching and setting up take a while, so they run beside the pass.
        self.executor = executor or ThreadPoolExecutor(max_workers=2)
        self.supervised: dict[int, Supervised] = {}
        self.mirrors: dict[int, Mirroring] = {}
        self.next_purge = 0.0

    # ------------------------------------------------------------ the loop

    def run_forever(self, stop: threading.Event) -> None:
        self.reap_orphans()
        while not stop.is_set():
            close_old_connections()
            try:
                self.tick()
            except Exception:
                logger.exception("The plugin host's pass failed")
            stop.wait(ARMADA_PLUGIN_TICK_SECONDS)
        self.stop_all()

    def tick(self) -> None:
        """One pass: every install's process brought to what it should be."""
        installs = {
            install.id: install
            for install in PluginInstall.objects.select_related(
                "bot", "realm", "source_repository"
            ).order_by("id")
        }
        for install_id in list(self.supervised):
            install = installs.get(install_id)
            if install is None or not self._wanted(install):
                self._stop(install_id, install)
        for install in installs.values():
            if self._wanted(install):
                try:
                    self._reconcile(install)
                except Exception:
                    logger.exception("The plugin host could not run install %s", install.id)
        # The widget documents that waited out their two seconds, and the
        # widgets that went quiet, reach their clients from here.
        flush_widget_events(timezone_now())
        mark_stale_widgets(timezone_now())
        now = self.clock()
        if now >= self.next_purge:
            self.next_purge = now + ARMADA_PLUGIN_PURGE_SECONDS
            purge_removed_plugins(timezone_now())
            self._prune({install.id: install for install in installs.values()})
        self._mirror()

    def stop_all(self) -> None:
        for install_id in list(self.supervised):
            self._stop(install_id, None)
        self.executor.shutdown(wait=True)

    def reap_orphans(self) -> None:
        """Stop the processes an earlier host left running (it was killed
        before it could stop them), found by the pid files it wrote."""
        for pid_file in self.plugin_dir.glob("*/pid"):
            try:
                pid = int(pid_file.read_text())
                cwd = os.readlink(f"/proc/{pid}/cwd")
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)
                continue
            if Path(cwd).is_relative_to(self.plugin_dir):
                kill_process_group(pid)
            pid_file.unlink(missing_ok=True)

    def _mirror(self) -> None:
        """Push each plugins repository to its mirror when it has changed,
        beside the pass. A push that failed is tried again after a minute,
        then ever less often, and at least once an hour."""
        now = self.clock()
        for repository in team_plugin_repositories():
            mirroring = self.mirrors.setdefault(repository.id, Mirroring())
            if mirroring.pushing is not None:
                if not mirroring.pushing.done():
                    continue
                try:
                    mirroring.pushing.result()
                    mirroring.failures = 0
                except ArmadaPluginMirrorError as error:
                    mirroring.failures += 1
                    delay = ARMADA_PLUGIN_MIRROR_CHECK_SECONDS * 2 ** min(mirroring.failures, 7)
                    mirroring.next_check = now + min(
                        delay, ARMADA_PLUGIN_MIRROR_BACKOFF_LIMIT_SECONDS
                    )
                    logger.warning(
                        "Could not push the plugins repository %s to its mirror: %s",
                        repository.repo_cache_path,
                        error,
                    )
                mirroring.pushing = None
            if now < mirroring.next_check:
                continue
            mirroring.next_check = now + ARMADA_PLUGIN_MIRROR_CHECK_SECONDS
            try:
                behind = team_plugin_mirror_is_behind(repository)
            except ArmadaPluginMirrorError as error:
                logger.warning("%s", error)
                continue
            if behind:
                mirroring.pushing = self.executor.submit(
                    push_team_plugin_mirror, repository.repo_cache_path
                )

    # ------------------------------------------------------------ one install

    @staticmethod
    def _wanted(install: PluginInstall) -> bool:
        return (
            install.state in (PluginInstall.STARTING, PluginInstall.RUNNING, PluginInstall.FAILED)
            and not install.disabled
        )

    def _fingerprint(self, install: PluginInstall) -> str:
        parts = [
            install.sha,
            str(install.generation),
            armada_plugin_token(install),
            install.bot.api_key,
        ]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def _reconcile(self, install: PluginInstall) -> None:
        try:
            fingerprint = self._fingerprint(install)
        except ArmadaPluginKeyError as error:
            set_plugin_state(install, PluginInstall.FAILED, detail=error.msg)
            return
        supervised = self.supervised.get(install.id)
        if supervised is not None and supervised.fingerprint != fingerprint:
            self._stop(install.id, None)
            supervised = None
        now = self.clock()
        if "remote" in install.manifest:
            if supervised is None:
                supervised = self.supervised[install.id] = Supervised(
                    install.id, fingerprint, "remote"
                )
            if now >= supervised.next_health:
                supervised.next_health = now + ARMADA_PLUGIN_HEALTH_SECONDS
                if self._healthy(install):
                    self._running(install, supervised)
                else:
                    self._fail(
                        install, supervised, f"{armada_plugin_url(install)}/health did not answer."
                    )
            return
        if supervised is None:
            supervised = self.supervised[install.id] = Supervised(install.id, fingerprint, "")
            self._prepare(install, supervised)
            return
        if supervised.phase == "preparing":
            assert supervised.preparing is not None
            if not supervised.preparing.done():
                return
            try:
                supervised.checkout = supervised.preparing.result()
            except ArmadaPluginPrepareError as error:
                # It may be the network; it is tried again at the pace of
                # any other failure.
                self._fail(install, supervised, str(error))
                return
            self._launch(install, supervised)
        elif supervised.phase == "backoff":
            if now < supervised.retry_at:
                return
            if supervised.checkout is None:
                self._prepare(install, supervised)
            else:
                self._launch(install, supervised)
        elif supervised.phase in ("starting", "running"):
            process = supervised.process
            assert process is not None
            if process.poll() is not None:
                self._fail(install, supervised, f"It exited with status {process.returncode}.")
            elif now >= supervised.next_health:
                supervised.next_health = now + ARMADA_PLUGIN_HEALTH_SECONDS
                if self._healthy(install):
                    self._running(install, supervised)
                elif (
                    supervised.phase == "running"
                    or now - supervised.started > ARMADA_PLUGIN_STARTUP_SECONDS
                ):
                    self._fail(install, supervised, "It did not answer GET /health.")
                else:
                    # Still starting: ask again soon.
                    supervised.next_health = now + ARMADA_PLUGIN_BACKOFF_FIRST_SECONDS / 4

    def _prepare(self, install: PluginInstall, supervised: Supervised) -> None:
        supervised.phase = "preparing"
        supervised.preparing = self.executor.submit(
            prepare_checkout,
            self.plugin_dir,
            _base_dir(self.plugin_dir, install),
            name=install.name,
            kind=install.source_kind,
            origin=_origin(install),
            sha=install.sha,
            path=install.source_path,
            manifest=install.manifest,
        )

    def _environment(self, install: PluginInstall, home: Path) -> tuple[dict[str, str], list[str]]:
        """The process's environment, and the values it must never show."""
        token = armada_plugin_token(install)
        secrets = armada_plugin_secret_environment(install)
        assert install.port is not None
        environment = {
            **plugin_base_environment(self.plugin_dir, home),
            **secrets,
            "ARMADA_SITE": install.realm.url,
            "ARMADA_EMAIL": install.bot.email,
            "ARMADA_API_KEY": install.bot.api_key,
            "ARMADA_PLUGIN_TOKEN": token,
            "ARMADA_PORT": str(install.port),
            "ARMADA_SETTINGS": json.dumps(install.settings),
        }
        hidden = [value for value in (*secrets.values(), token, install.bot.api_key) if value]
        return environment, hidden

    def _assign_port(self, install: PluginInstall) -> None:
        """A port on 127.0.0.1, kept from start to start while it is free."""
        if install.port is not None and self._port_free(install.port):
            return
        for _attempt in range(10):
            port = free_local_port()
            # Free on this machine, but another install's, which may be
            # stopped: ports are kept, and unique.
            if PluginInstall.objects.filter(port=port).exclude(id=install.id).exists():
                continue
            PluginInstall.objects.filter(id=install.id).update(port=port)
            install.port = port
            return
        raise OSError(errno.EADDRINUSE, "no free port for the plugin")

    @staticmethod
    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # As a server binds it: the connections of the process that
            # held it a moment ago, waiting out TIME_WAIT, do not keep it.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def _launch(self, install: PluginInstall, supervised: Supervised) -> None:
        assert supervised.checkout is not None
        base = _base_dir(self.plugin_dir, install)
        home = base / "home"
        home.mkdir(parents=True, exist_ok=True)
        try:
            self._assign_port(install)
            environment, hidden = self._environment(install, home)
        except InvalidToken:
            # Not tried again: the secrets must be set again first.
            supervised.phase = "failed"
            set_plugin_state(
                install,
                PluginInstall.FAILED,
                detail="Its secrets were kept under a key this server no longer has; set them again.",
            )
            return
        except OSError as error:
            self._fail(install, supervised, f"It could not have a port: {error.strerror}.")
            return
        supervised.lines.clear()
        try:
            process = subprocess.Popen(
                install.manifest["run"]["command"],
                cwd=supervised.checkout,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            self._fail(install, supervised, f"It could not start: {error.strerror}.")
            return
        assert process.stdout is not None
        (base / "pid").write_text(str(process.pid))
        supervised.process = process
        supervised.reader = threading.Thread(
            target=pump_plugin_output,
            args=(process.stdout, supervised.lines, base / "plugin.log", hidden),
            daemon=True,
        )
        supervised.reader.start()
        supervised.phase = "starting"
        supervised.started = supervised.next_health = self.clock()

    def _healthy(self, install: PluginInstall) -> bool:
        session = armada_plugin_session(install, timeout=ARMADA_PLUGIN_HEALTH_TIMEOUT_SECONDS)
        try:
            return session.get(f"{armada_plugin_url(install)}/health").status_code == 200
        except OSError:
            return False

    def _running(self, install: PluginInstall, supervised: Supervised) -> None:
        if supervised.phase != "remote":
            supervised.phase = "running"
        set_plugin_state(install, PluginInstall.RUNNING, detail="", failures=0)

    def _fail(self, install: PluginInstall, supervised: Supervised, reason: str) -> None:
        """Count a failure, and try again later: 1 s after the first, twice
        as long after each one more, never more than 5 minutes."""
        self._terminate(supervised)
        failures = install.failures + 1
        if supervised.phase != "remote":
            supervised.phase = "backoff"
            delay = ARMADA_PLUGIN_BACKOFF_FIRST_SECONDS * 2 ** min(failures - 1, 16)
            supervised.retry_at = self.clock() + min(delay, ARMADA_PLUGIN_BACKOFF_LIMIT_SECONDS)
        state = (
            PluginInstall.FAILED
            if failures >= ARMADA_PLUGIN_FAILURE_LIMIT
            else PluginInstall.STARTING
        )
        detail = "\n".join([*supervised.lines, reason])
        set_plugin_state(install, state, detail=detail, failures=failures)

    def _stop(self, install_id: int, install: PluginInstall | None) -> None:
        supervised = self.supervised.pop(install_id)
        self._terminate(supervised)
        if install is not None and install.state == PluginInstall.RUNNING:
            # Turned off: it starts again when it is turned on.
            set_plugin_state(install, PluginInstall.STARTING, detail="")

    def _terminate(self, supervised: Supervised) -> None:
        process = supervised.process
        supervised.process = None
        if process is None:
            return
        kill_process_group(process.pid, process)
        if supervised.reader is not None:
            supervised.reader.join(ARMADA_PLUGIN_STOP_SECONDS)
        for pid_file in self.plugin_dir.glob(f"*-{supervised.install_id}/pid"):
            pid_file.unlink(missing_ok=True)

    def _prune(self, installs: dict[int, PluginInstall]) -> None:
        """Delete the checkouts of installs that are gone, and of commits an
        install has moved off."""
        for base in self.plugin_dir.glob("*-*"):
            install_id = base.name.rsplit("-", 1)[1]
            install = installs.get(int(install_id)) if install_id.isdigit() else None
            if install is None:
                if install_id.isdigit():
                    shutil.rmtree(base, ignore_errors=True)
                continue
            for checkout in base.iterdir():
                if checkout.is_dir() and checkout.name not in (install.sha, "home"):
                    shutil.rmtree(checkout, ignore_errors=True)
