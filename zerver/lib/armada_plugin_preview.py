"""A plugin's widgets, drawn as people will see them, for whoever is
making the plugin to look at before anyone else does
(design/r9/plugins/ROUND2.md, decision 3).

The drawing is the beta's own page, drawn by the beta's own code. A
headless Chromium (the agent's, ARMADA_WORKER_BROWSER_DIR, or
ARMADA_PLUGIN_PREVIEW_BROWSER) opens it, driven over the DevTools
protocol through a pipe, as Playwright drives one, and every request
the page makes is answered here:

- the page itself, as the server renders it;
- its files, read from disk, or fetched from a development server;
- its calls to the API, from answers made in advance: one register
  snapshot that holds only the plugin on trial and its documents. The
  long poll for events is never answered, and nothing else is fetched.

The nav's widget rows are pictured at rest, and then each widget's row
is pressed and its sheet pictured, in light and in dark. The page is
also measured, because a figure wider than its cell or a label that the
nav cuts is something to fix before anyone sees it.

This module imports nothing of Django and touches no database, so it can
draw from answers prepared on another machine.
"""

import base64
import contextlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from zerver.lib.mime_types import guess_type

PREVIEW_WIDTH = 1440
PREVIEW_HEIGHT = 900
PREVIEW_SCALE = 2
PREVIEW_SECONDS = 90.0
# How long the page has to draw its rows, and a pressed row its sheet.
PREVIEW_DRAW_MILLISECONDS = 15_000
# The picture's margin around what it shows.
PREVIEW_MARGIN = 12
BROWSER_ARGUMENTS = (
    "--headless",
    "--remote-debugging-pipe",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--hide-scrollbars",
    "--mute-audio",
    "--force-color-profile=srgb",
    # As Playwright starts its Chromium on Linux; the agent's commands are
    # already sandboxed, and this browser opens nothing but the page.
    "--no-sandbox",
)
# What starts the browser: it puts the pipe's ends on descriptors 3 and 4,
# where the browser reads and writes the protocol, then becomes it.
TRAMPOLINE = """
import fcntl, os, sys
given = [int(end) for end in sys.argv[1:3]]
ends = [fcntl.fcntl(end, fcntl.F_DUPFD, 10) for end in given]
for end in given:
    os.close(end)
for end, descriptor in zip(ends, (3, 4)):
    os.dup2(end, descriptor)
    os.close(end)
os.execv(sys.argv[3], sys.argv[3:])
"""

# The page's rows and sheets, as the beta draws them
# (web/src/armada/beta/plugins.js and plugin_view.js): a widget's row has
# the id "<install>/<widget>" (the group's other rows, such as its "+",
# have none of that form), and its trailing cell .wf is as wide as the
# widget's sizer. The figure .wv in it is as wide as the cell and hides
# what does not fit, so a figure too wide scrolls inside its box.
# Declared inside each measuring function, since every evaluation runs in
# the page's one global scope.
MEASURE = """
  const box = (e) => { const b = e.getBoundingClientRect(); return {x: b.left, y: b.top, width: b.width, height: b.height}; };
  const cut = (e) => !!e && e.scrollWidth > e.clientWidth + 0.5;
  const frame = () => new Promise((done) => requestAnimationFrame(() => done()));
  const pause = (ms) => new Promise((done) => setTimeout(done, ms));
  async function until(find, ms) { const end = Date.now() + ms; for (;;) { const found = find(); if (found) return found; if (Date.now() > end) return null; await pause(50); } }
  async function still(element) { let last = ""; for (let i = 0; i < 100; i++) { const b = box(element), now = [b.x, b.y, b.width, b.height].join(); if (now === last) return; last = now; await frame(); await pause(30); } }
"""
MEASURE_ROWS = (
    "(async () => {"
    + MEASURE
    + """
  const rows = await until(() => { const found = [...document.querySelectorAll('nav .row.wg[data-w*="/"]')]; return found.length ? found : null; }, __MS__);
  if (!rows) return null;
  await document.fonts.ready;
  await frame(); await frame(); await pause(300);
  return {
    nav: box(document.querySelector("nav")),
    group: box(rows[0].parentElement),
    rows: rows.map((row) => {
      const cell = row.querySelector(".wf"), figure = cell && cell.querySelector(".wv"), label = row.querySelector(".lab");
      return {
        id: row.dataset.w, box: box(row),
        label: label ? label.textContent : "", label_cut: cut(label),
        figure: figure ? figure.textContent : "",
        figure_cut: cut(figure),
      };
    }),
  };
})()
"""
)
MEASURE_SHEET = (
    "(async () => {"
    + MEASURE
    + """
  const sheet = await until(() => { const found = document.querySelector(".wsheet"); return found && found.dataset.w === __ID__ ? found : null; }, __MS__);
  if (!sheet) return null;
  await still(sheet);
  await document.fonts.ready;
  await pause(200);
  return {
    sheet: box(sheet),
    rows: [...sheet.querySelectorAll(".wl > li")].map((row) => {
      const label = row.querySelector(":scope > .t"), figure = row.querySelector(":scope > .v");
      return {
        key: row.dataset.row,
        label: label ? label.firstChild ? label.firstChild.textContent : "" : "", label_cut: cut(label),
        figure: figure ? figure.textContent : "", figure_cut: cut(figure),
      };
    }),
  };
})()
"""
)


class ArmadaPreviewError(Exception):
    """The browser could not draw the preview."""


@dataclass(frozen=True)
class PreviewSite:
    """What the browser is given in place of the server."""

    # Where the page is, on the server's own address, so that its files
    # and its calls to the API go where they would.
    page_url: str
    page_html: str
    # The API's answers, by path under /json/; "events" is never answered.
    answers: dict[str, Any]
    # URL prefixes whose files are read from a directory.
    files: tuple[tuple[str, str], ...] = ()
    # URL prefixes fetched from the network (a development server's files).
    network: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_url": self.page_url,
            "page_html": self.page_html,
            "answers": self.answers,
            "files": [list(pair) for pair in self.files],
            "network": list(self.network),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PreviewSite":
        return cls(
            page_url=value["page_url"],
            page_html=value["page_html"],
            answers=value["answers"],
            files=tuple((prefix, directory) for prefix, directory in value["files"]),
            network=tuple(value["network"]),
        )


@dataclass(frozen=True)
class Reply:
    status: int
    content_type: str
    body: bytes


# What answer_request says of a request the network should answer, and of
# one that is never answered.
FETCH = "fetch"
HOLD = "hold"


def answer_request(site: PreviewSite, url: str) -> Reply | str:
    """How the page's request for url is answered: a reply, FETCH or HOLD."""
    address = url.split("#", 1)[0]
    if address == site.page_url:
        return Reply(200, "text/html; charset=utf-8", site.page_html.encode())
    parts = urlsplit(address)
    origin = urlsplit(site.page_url)
    if (parts.scheme, parts.netloc) == (origin.scheme, origin.netloc) and parts.path.startswith(
        "/json/"
    ):
        route = parts.path.removeprefix("/json/")
        if route == "events":
            return HOLD
        answer = site.answers.get(route, {"result": "success", "msg": ""})
        return Reply(200, "application/json", json.dumps(answer).encode())
    for prefix, directory in site.files:
        if address.startswith(prefix):
            root = os.path.realpath(directory)
            path = os.path.realpath(
                os.path.join(root, unquote(address[len(prefix) :].split("?")[0]))
            )
            if path.startswith(root + os.sep) and os.path.isfile(path):
                content_type = guess_type(path)[0] or "application/octet-stream"
                return Reply(200, content_type, Path(path).read_bytes())
            return Reply(404, "text/plain", b"")
    if any(address.startswith(prefix) for prefix in site.network):
        return FETCH
    return Reply(404, "text/plain", b"")


class _Browser:
    """A headless Chromium, and the DevTools protocol through its pipe:
    each message is JSON ending in a NUL byte, written to the browser's
    descriptor 3 and read from its descriptor 4."""

    def __init__(self, executable: str, *, deadline: float) -> None:
        self.deadline = deadline
        self.profile = tempfile.mkdtemp(prefix="armada-preview-")
        commands, self.commands = os.pipe()
        self.replies, replies = os.pipe()
        self.buffer = b""
        self.next_id = 0
        self.errors: list[str] = []
        # The events seen since the last navigation, by method.
        self.seen: set[str] = set()
        self.on_request: Callable[[dict[str, Any]], None] = lambda params: None
        self.stderr = Path(self.profile, "stderr")
        try:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    TRAMPOLINE,
                    str(commands),
                    str(replies),
                    executable,
                    *BROWSER_ARGUMENTS,
                    f"--user-data-dir={self.profile}",
                    "about:blank",
                ],
                pass_fds=(commands, replies),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self.stderr.open("wb"),
                start_new_session=True,
            )
        finally:
            os.close(commands)
            os.close(replies)

    def _read(self) -> dict[str, Any]:
        while b"\0" not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ArmadaPreviewError("The browser took too long.")
            ready, _write, _error = select.select([self.replies], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self.replies, 1 << 16)
            if not chunk:
                said = self.stderr.read_text(errors="replace").strip().splitlines()
                raise ArmadaPreviewError(
                    "The browser stopped." + (f" It said: {said[-1][:300]}" if said else "")
                )
            self.buffer += chunk
        message, _nul, self.buffer = self.buffer.partition(b"\0")
        return json.loads(message)

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session: str | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        self.next_id += 1
        message: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        encoded = json.dumps(message).encode() + b"\0"
        try:
            while encoded:
                encoded = encoded[os.write(self.commands, encoded) :]
        except OSError as error:
            raise ArmadaPreviewError("The browser stopped.") from error
        if not wait:
            return {}
        sent = self.next_id
        while True:
            reply = self._read()
            if reply.get("id") == sent:
                if "error" in reply:
                    raise ArmadaPreviewError(f"{method}: {reply['error'].get('message', '')}")
                return reply.get("result", {})
            self._event(reply)

    def _event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            # The reply to a command not waited on.
            return
        self.seen.add(method)
        if method == "Fetch.requestPaused":
            self.on_request(message["params"])
        elif method == "Runtime.exceptionThrown":
            details = message["params"]["exceptionDetails"]
            thrown = details.get("exception", {}).get("description") or details.get("text", "")
            self.errors.append(str(thrown).split("\n", 1)[0][:300])

    def wait_for(self, method: str) -> None:
        """Until the event has been seen, answering requests meanwhile."""
        while method not in self.seen:
            self._event(self._read())

    def close(self) -> None:
        with contextlib.suppress(ArmadaPreviewError):
            self.send("Browser.close", wait=False)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        os.close(self.commands)
        os.close(self.replies)
        shutil.rmtree(self.profile, ignore_errors=True)


@dataclass
class Picture:
    widget: str | None
    state: str
    theme: str
    path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "widget": self.widget,
            "state": self.state,
            "theme": self.theme,
            "path": str(self.path),
        }


@dataclass
class Drawn:
    pictures: list[Picture] = field(default_factory=list)
    # What the page measured at rest, and of each widget's sheet.
    rows: dict[str, Any] | None = None
    sheets: dict[str, Any] = field(default_factory=dict)
    # Why something was not drawn, and what the page threw.
    notes: list[str] = field(default_factory=list)


def _clip(*boxes: dict[str, float]) -> dict[str, float]:
    """The boxes' union, with a margin, kept inside the window."""
    left = max(0.0, min(b["x"] for b in boxes) - PREVIEW_MARGIN)
    top = max(0.0, min(b["y"] for b in boxes) - PREVIEW_MARGIN)
    right = min(float(PREVIEW_WIDTH), max(b["x"] + b["width"] for b in boxes) + PREVIEW_MARGIN)
    bottom = min(float(PREVIEW_HEIGHT), max(b["y"] + b["height"] for b in boxes) + PREVIEW_MARGIN)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top, "scale": 1}


class _Page:
    def __init__(self, browser: _Browser, site: PreviewSite) -> None:
        self.browser = browser
        self.site = site
        target = browser.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = browser.send("Target.attachToTarget", {"targetId": target, "flatten": True})[
            "sessionId"
        ]
        browser.on_request = self.answer
        self.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": PREVIEW_WIDTH,
                "height": PREVIEW_HEIGHT,
                "deviceScaleFactor": PREVIEW_SCALE,
                "mobile": False,
            },
        )

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.browser.send(method, params, session=self.session)

    def answer(self, params: dict[str, Any]) -> None:
        """A paused request, answered while the page waits for it; never
        waited on here, since the page may be waiting for this one."""
        request_id = params["requestId"]
        answer = answer_request(self.site, params["request"]["url"])
        if answer == HOLD:
            return
        if answer == FETCH:
            self.browser.send(
                "Fetch.continueRequest", {"requestId": request_id}, session=self.session, wait=False
            )
            return
        assert isinstance(answer, Reply)
        self.browser.send(
            "Fetch.fulfillRequest",
            {
                "requestId": request_id,
                "responseCode": answer.status,
                "responseHeaders": [{"name": "Content-Type", "value": answer.content_type}],
                "body": base64.b64encode(answer.body).decode(),
            },
            session=self.session,
            wait=False,
        )

    def evaluate(self, expression: str) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            thrown = details.get("exception", {}).get("description") or details.get("text", "")
            raise ArmadaPreviewError(
                f"The page could not be measured: {str(thrown).splitlines()[0][:300]}"
            )
        return result["result"].get("value")

    def open(self, theme: str) -> dict[str, Any] | None:
        self.send(
            "Emulation.setEmulatedMedia",
            {
                "features": [
                    {"name": "prefers-color-scheme", "value": theme},
                    {"name": "prefers-reduced-motion", "value": "reduce"},
                ]
            },
        )
        self.browser.seen.clear()
        self.send("Page.navigate", {"url": self.site.page_url})
        self.browser.wait_for("Page.loadEventFired")
        return self.evaluate(MEASURE_ROWS.replace("__MS__", str(PREVIEW_DRAW_MILLISECONDS)))

    def press(self, box: dict[str, float]) -> None:
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for kind in ("mousePressed", "mouseReleased"):
            self.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    def picture(self, clip: dict[str, float], path: Path) -> None:
        shot = self.send("Page.captureScreenshot", {"format": "png", "clip": clip})
        path.write_bytes(base64.b64decode(shot["data"]))


def draw_widget_previews(
    site: PreviewSite,
    *,
    browser: str,
    widgets: list[str],
    out_dir: Path,
    name: str,
) -> Drawn:
    """Picture the nav's widget rows at rest and each widget's sheet
    pressed open, in light and dark, into out_dir as <name>-rest-<theme>.png
    and <name>-<widget>-pressed-<theme>.png. widgets are the row ids,
    "<install>/<widget>", to press."""
    out_dir.mkdir(parents=True, exist_ok=True)
    drawn = Drawn()
    chromium = _Browser(browser, deadline=time.monotonic() + PREVIEW_SECONDS)
    try:
        page = _Page(chromium, site)
        for theme in ("light", "dark"):
            rows = page.open(theme)
            if rows is None:
                drawn.notes.append("The page drew no widget rows in the nav.")
                break
            drawn.rows = drawn.rows or rows
            # The widget rows and what is below them, the width of the nav.
            foot = [rows["group"], {**rows["nav"], "y": rows["group"]["y"]}]
            path = out_dir / f"{name}-rest-{theme}.png"
            page.picture(_clip(*foot), path)
            drawn.pictures.append(Picture(None, "rest", theme, path))
            for widget in widgets:
                rows = page.open(theme)
                row = next(
                    (row for row in (rows or {"rows": []})["rows"] if row["id"] == widget), None
                )
                if row is None:
                    drawn.notes.append(f"The nav has no row for {widget}.")
                    continue
                page.press(row["box"])
                sheet = page.evaluate(
                    MEASURE_SHEET.replace("__ID__", json.dumps(widget)).replace(
                        "__MS__", str(PREVIEW_DRAW_MILLISECONDS)
                    )
                )
                if sheet is None:
                    drawn.notes.append(f"Pressing {widget}'s row opened no sheet.")
                    continue
                drawn.sheets.setdefault(widget, sheet)
                key = widget.split("/", 1)[1]
                path = out_dir / f"{name}-{key}-pressed-{theme}.png"
                page.picture(_clip(*foot, sheet["sheet"]), path)
                drawn.pictures.append(Picture(key, "pressed", theme, path))
    finally:
        drawn.notes.extend(f"The page threw: {error}" for error in dict.fromkeys(chromium.errors))
        chromium.close()
    return drawn
