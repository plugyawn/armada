"""A plugin's trial (design/r9/plugins/ROUND2.md, section C, step 4): its
program run from the worktree it is being made in, to see what it would
show and post, with nothing reaching anyone.

The program runs as the plugin host runs a plugin: in a session of its
own, on a port of its own on 127.0.0.1, with the settings and secrets of
its install or its draft, and with nothing else of this server's
environment. It differs from a hosted plugin in three ways:

- Its ARMADA_SITE is a server that the trial runs in place of Armada's
  API. That server checks each widget push exactly as POST
  /api/v1/armada/widgets does (check_widget_push) and keeps the document.
  It also keeps what the plugin would post, and answers register and
  events with a queue on which nothing happens. No armada_widget event is
  sent, so no one sees what the trial shows.
- Its bot address is the install's, but its key and its token are the
  trial's own, so code on trial never holds the bot's real key.
- It stops two seconds after every widget in the nav has a document, or
  two seconds after a push was refused if no other push follows, and it
  is killed after a minute whatever it is doing.

What the program writes, the documents it pushes and what it would post
all have every secret value blanked, so nothing drawn or returned from a
trial carries one.
"""

import base64
import hmac
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django.utils.translation import gettext as _
from typing_extensions import override

from zerver.lib.armada_mcp_client import ArmadaMCPClient, ArmadaMCPError
from zerver.lib.armada_plugin_host import (
    ARMADA_PLUGIN_LAST_LINES,
    ARMADA_PLUGIN_STOP_SECONDS,
    REDACTED,
    free_local_port,
    kill_process_group,
    last_lines,
    plugin_base_environment,
    pump_plugin_output,
)
from zerver.lib.armada_plugins import armada_plugin_local_session
from zerver.lib.armada_widgets import check_widget_push
from zerver.lib.exceptions import JsonableError

# How long a trial may run, and how long its program has to answer
# /health once started.
ARMADA_PLUGIN_TRIAL_SECONDS = 60.0
ARMADA_PLUGIN_TRIAL_STARTUP_SECONDS = 30.0
ARMADA_PLUGIN_TRIAL_SETUP_SECONDS = 300
# Once every nav widget has a document, or a push was refused, a little
# longer, to see what follows.
ARMADA_PLUGIN_TRIAL_SETTLE_SECONDS = 2.0
ARMADA_PLUGIN_TRIAL_POLL_SECONDS = 0.1
ARMADA_PLUGIN_TRIAL_TOOL_SECONDS = 10
ARMADA_PLUGIN_TRIAL_POSTS = 20
ARMADA_PLUGIN_TRIAL_POST_LENGTH = 1000
ARMADA_PLUGIN_TRIAL_REQUEST_LIMIT = 1_000_000
# A trial's long poll for events answers before the plugin's own timeout.
ARMADA_PLUGIN_TRIAL_EVENTS_SECONDS = 50.0
# A contract error names the place first: "...: $.blocks[0].label: ...".
CONTRACT_PLACE = re.compile(r"^(?:[^$]*?: )?(\$[^ :]*): (.+)$", re.DOTALL)
# The contract's words for a value that does not fit, in plainer ones.
PLAIN_WORDS = [
    (re.compile(r"^string exceeds (\d+) characters$"), r"is longer than \1 characters"),
    (
        re.compile(r"^string must contain at least (\d+) characters?$"),
        r"is shorter than \1 characters",
    ),
    (re.compile(r"^missing required property '(.+)'$"), r"lacks \1, which it needs"),
    (re.compile(r"^unsupported property$"), "is not something the contract knows"),
    (re.compile(r"^expected type (.+)$"), r"should be a \1"),
    (re.compile(r"^array exceeds (\d+) items$"), r"has more than \1 items"),
    (re.compile(r"^value must be >= (.+)$"), r"is below \1"),
    (re.compile(r"^value must be <= (.+)$"), r"is above \1"),
    (re.compile(r"^expected one of (.+), got (.+)$", re.DOTALL), r"is \2, not one of \1"),
    (
        re.compile(r"^value does not match any allowed contract variant$"),
        "is not one of the shapes the contract allows",
    ),
]


@dataclass(frozen=True)
class Problem:
    """Something wrong with a plugin, where it is, in plain words."""

    where: str
    what: str

    def as_dict(self) -> dict[str, str]:
        return {"where": self.where, "what": self.what}


@dataclass
class TrialResult:
    # The latest document of each widget, by (widget, item).
    documents: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    # What it would have posted, and what it asked of the API that a
    # trial does not answer.
    posts: list[dict[str, str]] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    # Its tools for the agent; None when it offers none, or answered no list.
    tools: list[dict[str, str]] | None = None
    lines: list[str] = field(default_factory=list)
    pushes: int = 0
    seconds: float = 0.0
    # Still running when the minute was up.
    killed: bool = False


def plain_words(words: str) -> str:
    for pattern, replacement in PLAIN_WORDS:
        if pattern.match(words):
            return pattern.sub(replacement, words)
    return words


def contract_problem(where: str, message: str) -> Problem:
    """A contract error as a problem: where, with the place in the document
    it names, and its words, plainly."""
    match = CONTRACT_PLACE.match(message)
    if match is None:
        return Problem(where, message)
    return Problem(f"{where} at {match[1]}", plain_words(match[2]))


def blank(value: Any, hidden: list[str]) -> Any:
    """The value with every secret in its text replaced."""
    if isinstance(value, str):
        for secret in hidden:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, list):
        return [blank(item, hidden) for item in value]
    if isinstance(value, dict):
        return {key: blank(item, hidden) for key, item in value.items()}
    return value


class _Capture:
    """What the program on trial sends to Armada's API, kept instead of
    sent on."""

    def __init__(
        self, manifest: dict[str, Any], *, email: str, api_key: str, hidden: list[str]
    ) -> None:
        self.manifest = manifest
        login = f"{email}:{api_key}".encode()
        self.authorization = "Basic " + base64.b64encode(login).decode()
        self.email = email
        self.hidden = hidden
        self.lock = threading.Lock()
        self.result = TrialResult()
        self.over = threading.Event()
        # When it last pushed, and whether a push was refused.
        self.pushed_at = 0.0
        self.refused = False

    def push(self, fields: dict[str, str]) -> tuple[int, dict[str, Any]]:
        widget = fields.get("widget", "")
        item = fields.get("item", "")
        with self.lock:
            self.result.pushes += 1
            self.pushed_at = time.monotonic()
            where = f"push {self.result.pushes} of {widget or 'no widget'}"
            if item:
                where += f", item {item}"
            try:
                if fields.get("link_id") not in (None, "", "null"):
                    raise JsonableError(_("No such link"))
                values = {}
                for name in ("document", "data"):
                    try:
                        values[name] = json.loads(fields[name]) if name in fields else None
                    except ValueError:
                        raise JsonableError(
                            _('Argument "{name}" is not valid JSON.').format(name=name)
                        )
                document = check_widget_push(
                    self.manifest,
                    widget=widget,
                    linked=False,
                    item=item,
                    document=values["document"],
                    data=values["data"],
                    previous=self.result.documents.get((widget, item)),
                )
            except JsonableError as error:
                self.refused = True
                message = blank(error.msg, self.hidden)
                self.result.problems.append(contract_problem(where, message))
                return 400, {"result": "error", "msg": message, "code": "BAD_REQUEST"}
            self.result.documents[(widget, item)] = blank(document, self.hidden)
            return 200, {"result": "success", "msg": "", "version": self.result.pushes}

    def post(self, fields: dict[str, str]) -> tuple[int, dict[str, Any]]:
        with self.lock:
            if len(self.result.posts) < ARMADA_PLUGIN_TRIAL_POSTS:
                self.result.posts.append(
                    {
                        name: blank(fields.get(name, ""), self.hidden)[
                            :ARMADA_PLUGIN_TRIAL_POST_LENGTH
                        ]
                        for name in ("type", "to", "topic", "content")
                    }
                )
        return 200, {"result": "success", "msg": "", "id": 0}

    def unanswered(self, method: str, path: str) -> tuple[int, dict[str, Any]]:
        asked = f"{method} {path}"
        with self.lock:
            if asked not in self.result.asked:
                self.result.asked.append(asked)
        return 400, {
            "result": "error",
            "msg": f"A trial does not answer {asked}.",
            "code": "BAD_REQUEST",
        }

    def answer(self, method: str, path: str, fields: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if (method, path) == ("POST", "/api/v1/armada/widgets"):
            return self.push(fields)
        if (method, path) == ("POST", "/api/v1/messages"):
            return self.post(fields)
        if (method, path) == ("POST", "/api/v1/register"):
            return 200, {
                "result": "success",
                "msg": "",
                "queue_id": "trial",
                "last_event_id": -1,
                "armada_plugins": [],
                "armada_widgets": [],
            }
        if (method, path) == ("GET", "/api/v1/events"):
            # Nothing happens on a trial's queue: it waits, then says so.
            self.over.wait(ARMADA_PLUGIN_TRIAL_EVENTS_SECONDS)
            return 200, {
                "result": "success",
                "msg": "",
                "events": [{"type": "heartbeat", "id": 0}],
            }
        if (method, path) == ("DELETE", "/api/v1/events"):
            return 200, {"result": "success", "msg": ""}
        if (method, path) == ("GET", "/api/v1/users/me"):
            return 200, {"result": "success", "msg": "", "email": self.email, "is_bot": True}
        return self.unanswered(method, path)

    def handle(
        self, method: str, address: str, authorization: str, body: bytes
    ) -> tuple[int, dict[str, Any]]:
        """A request to the API, as Zulip's client sends one: the bot's key
        in basic auth, and form fields in the query or the body."""
        if not hmac.compare_digest(authorization, self.authorization):
            return 401, {"result": "error", "msg": "Invalid API key", "code": "UNAUTHORIZED"}
        if len(body) > ARMADA_PLUGIN_TRIAL_REQUEST_LIMIT:
            return 413, {
                "result": "error",
                "msg": "The request is too large.",
                "code": "BAD_REQUEST",
            }
        parts = urlsplit(address)
        given = {
            **parse_qs(parts.query, keep_blank_values=True),
            **parse_qs(body.decode(errors="replace"), keep_blank_values=True),
        }
        return self.answer(method, parts.path, {name: values[-1] for name, values in given.items()})


class _CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, capture: _Capture) -> None:
        super().__init__(("127.0.0.1", 0), _CaptureHandler)
        self.capture = capture


class _CaptureHandler(BaseHTTPRequestHandler):  # nocoverage: in the server's threads.
    """Only hands each request to _Capture.handle, which the tests call
    themselves: coverage does not follow the server's threads."""

    server: _CaptureServer

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(length, ARMADA_PLUGIN_TRIAL_REQUEST_LIMIT + 1))
        status, answer = self.server.capture.handle(
            method, self.path, self.headers.get("Authorization", ""), body
        )
        encoded = json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


def _healthy(port: int) -> bool:
    session = armada_plugin_local_session(timeout=2)
    try:
        return session.get(f"http://127.0.0.1:{port}/health").status_code == 200
    except OSError:
        return False


def _watch(
    process: "subprocess.Popen[bytes]",
    port: int,
    capture: _Capture,
    *,
    started: float,
) -> tuple[bool, bool]:
    """Wait until the program has shown each of its nav widgets, or has
    gone wrong, or its time is up; returns whether it answered /health,
    and whether its time ran out."""
    widgets = capture.manifest["provides"].get("widgets", [])
    nav = [widget["key"] for widget in widgets if "nav" in widget["in"]]
    problems = capture.result.problems
    seconds = ARMADA_PLUGIN_TRIAL_SECONDS
    healthy = False
    shown_at: float | None = None
    while True:
        now = time.monotonic()
        if process.poll() is not None:
            problems.append(Problem("run.command", f"It exited with status {process.returncode}."))
            return healthy, False
        if now - started >= seconds:
            with capture.lock:
                missing = [key for key in nav if (key, "") not in capture.result.documents]
            for key in missing:
                problems.append(
                    Problem(
                        f"widget {key}",
                        f"It pushed no document for {key} in {seconds:g} seconds.",
                    )
                )
            return healthy, True
        if not healthy:
            healthy = _healthy(port)
            if not healthy and now - started >= ARMADA_PLUGIN_TRIAL_STARTUP_SECONDS:
                problems.append(
                    Problem(
                        "run.command",
                        f"It did not answer GET /health on its port within"
                        f" {ARMADA_PLUGIN_TRIAL_STARTUP_SECONDS:g} seconds.",
                    )
                )
                return False, False
        if healthy:
            with capture.lock:
                shown = all((key, "") in capture.result.documents for key in nav)
                quiet = (
                    capture.refused
                    and now - capture.pushed_at >= ARMADA_PLUGIN_TRIAL_SETTLE_SECONDS
                )
            if shown:
                shown_at = now if shown_at is None else shown_at
            if quiet or (
                shown_at is not None and now - shown_at >= ARMADA_PLUGIN_TRIAL_SETTLE_SECONDS
            ):
                return True, False
        time.sleep(ARMADA_PLUGIN_TRIAL_POLL_SECONDS)


def _list_tools(manifest: dict[str, Any], port: int, token: str) -> list[dict[str, str]]:
    url = f"http://127.0.0.1:{port}{manifest['provides']['tools']['path']}"
    session = armada_plugin_local_session(timeout=ARMADA_PLUGIN_TRIAL_TOOL_SECONDS)
    with ArmadaMCPClient(url, token=token, session=session) as client:
        listed = client.list_tools()
    return [
        {"name": str(tool.get("name", "")), "description": str(tool.get("description", ""))}
        for tool in listed
    ]


def run_plugin_trial(
    root: Path,
    manifest: dict[str, Any],
    *,
    plugin_settings: dict[str, Any],
    secret_environment: dict[str, str],
    email: str,
    plugin_dir: Path,
) -> TrialResult:
    """Run the program in root, the plugin's directory in its worktree, as
    the manifest says, and return what it showed, posted and offered, and
    what went wrong."""
    hidden = sorted({value for value in secret_environment.values() if value}, key=len)[::-1]
    api_key = secrets.token_urlsafe(24)
    token = secrets.token_hex(32)
    capture = _Capture(manifest, email=email, api_key=api_key, hidden=hidden)
    result = capture.result
    server = _CaptureServer(capture)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    home = Path(tempfile.mkdtemp(prefix="armada-trial-"))
    lines: deque[str] = deque(maxlen=ARMADA_PLUGIN_LAST_LINES)
    process: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    started = time.monotonic()
    try:
        setup = manifest["run"].get("setup")
        if setup is not None:
            try:
                subprocess.run(
                    setup,
                    cwd=root,
                    env=plugin_base_environment(plugin_dir, home),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=ARMADA_PLUGIN_TRIAL_SETUP_SECONDS,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as error:
                output = getattr(error, "output", None)
                said = (
                    output.decode(errors="replace")
                    if isinstance(output, bytes) and output.strip()
                    else str(error)
                )
                result.problems.append(
                    Problem("run.setup", f"Its setup failed: {last_lines(blank(said, hidden))}")
                )
                return result
        port = free_local_port()
        environment = {
            **plugin_base_environment(plugin_dir, home),
            **secret_environment,
            "ARMADA_SITE": f"http://127.0.0.1:{server.server_address[1]}",
            "ARMADA_EMAIL": email,
            "ARMADA_API_KEY": api_key,
            "ARMADA_PLUGIN_TOKEN": token,
            "ARMADA_PORT": str(port),
            "ARMADA_SETTINGS": json.dumps(plugin_settings),
            "ARMADA_TRIAL": "1",
        }
        try:
            process = subprocess.Popen(
                manifest["run"]["command"],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            result.problems.append(Problem("run.command", f"It could not start: {error.strerror}."))
            return result
        assert process.stdout is not None
        reader = threading.Thread(
            target=pump_plugin_output,
            args=(process.stdout, lines, home / "plugin.log", hidden),
            daemon=True,
        )
        reader.start()
        healthy, result.killed = _watch(process, port, capture, started=started)
        if healthy and "tools" in manifest["provides"]:
            try:
                result.tools = _list_tools(manifest, port, token)
            except ArmadaMCPError as error:
                result.problems.append(
                    Problem("provides.tools", f"Its tools could not be listed: it {error}.")
                )
    finally:
        capture.over.set()
        if process is not None:
            kill_process_group(process.pid, process)
        if reader is not None:
            reader.join(ARMADA_PLUGIN_STOP_SECONDS)
        server.shutdown()
        server.server_close()
        serving.join()
        shutil.rmtree(home, ignore_errors=True)
        result.lines = list(lines)
        result.seconds = round(time.monotonic() - started, 1)
    return result
