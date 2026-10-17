"""A stand-in for Chromium in the preview's tests
(zerver/tests/test_armada_plugin_preview.py and test_armada_plugin_check.py),
written with the standard library alone; the tests start it through a
small script of their own, since a browser is an executable. Like
Chromium started with --remote-debugging-pipe, it reads the DevTools
protocol on descriptor 3 and writes on descriptor 4, and it answers the
commands the preview sends. It draws nothing:

- navigated, it asks for the page and for each URL in
  $FAKE_CHROMIUM_REQUESTS, as a page loading would, then says the page
  has loaded;
- it measures what a page would draw from the register answer it was
  given: a row for each widget shown in the nav, whose figure is wider
  than its cell when it has more characters than its rest.sizer, and a
  sheet whose rows' labels and figures are cut past 24 and 8 characters;
- each picture is a 1 × 1 PNG.

It writes what it was asked and how each request was answered to
$FAKE_CHROMIUM_LOG, one JSON line each. $FAKE_CHROMIUM picks a way to go
wrong: crash, blank, nosheet, error, exception, throws or slow.
"""

import base64
import json
import os
import sys
import time
from typing import Any

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
behaviour = os.environ.get("FAKE_CHROMIUM", "")
log = open(os.environ["FAKE_CHROMIUM_LOG"], "a")  # noqa: SIM115
if behaviour == "crash":
    sys.exit(1)
commands = os.fdopen(3, "rb", buffering=0)
replies = os.fdopen(4, "wb", buffering=0)
buffer = b""
requests = 0
drawn: dict[str, Any] = {"plugins": [], "widgets": []}


def note(**entry: Any) -> None:
    log.write(json.dumps(entry) + "\n")
    log.flush()


def read() -> dict[str, Any]:
    global buffer
    while b"\0" not in buffer:
        chunk = commands.read(65536)
        if not chunk:
            sys.exit(0)
        buffer += chunk
    message, _nul, buffer = buffer.partition(b"\0")
    return json.loads(message)


def send(message: dict[str, Any]) -> None:
    replies.write(json.dumps(message).encode() + b"\0")


def box(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def rows() -> dict[str, Any] | None:
    shown = [
        (plugin["id"], widget)
        for plugin in drawn["plugins"]
        for widget in plugin["widgets"]
        if plugin["widget_defaults"][widget["key"]]["nav"] == "shown"
    ]
    if behaviour == "blank" or not shown:
        return None
    measured = []
    for index, (install_id, widget) in enumerate(shown):
        document: dict[str, Any] = next(
            (w["document"] for w in drawn["widgets"] if w["widget"] == widget["key"]), {}
        )
        rest = document.get("rest", {})
        figure = str(document.get("data", {}).get(rest.get("figure", "").removeprefix("data."), ""))
        measured.append(
            {
                "id": f"{install_id}/{widget['key']}",
                "box": box(8, 713 + 28 * index, 216, 28),
                "label": widget["label"],
                "label_cut": len(widget["label"]) > 20,
                "figure": figure,
                "figure_cut": len(figure) > len(rest.get("sizer", "")),
            }
        )
    return {
        "nav": box(0, 0, 232, 900),
        "group": box(8, 713, 216, 28 * len(shown)),
        "rows": measured,
    }


def sheet(row_id: str) -> dict[str, Any] | None:
    if behaviour == "nosheet":
        return None
    key = row_id.split("/", 1)[1]
    document = next(w["document"] for w in drawn["widgets"] if w["widget"] == key)
    items = [
        item
        for block in document["blocks"]
        if block["type"] == "list"
        for item in block.get("items", [])
    ]
    return {
        "sheet": box(240, 497, 344, 244),
        "rows": [
            {
                "key": item["key"],
                "label": item["label"],
                "label_cut": len(item["label"]) > 24,
                "figure": item.get("figure", ""),
                "figure_cut": len(str(item.get("figure", ""))) > 8,
            }
            for item in items
        ],
    }


while True:
    message = read()
    method = message.get("method")
    params = message.get("params", {})
    session = message.get("sessionId")
    result: dict[str, Any] = {}
    if method in ("Fetch.fulfillRequest", "Fetch.continueRequest"):
        body = base64.b64decode(params.get("body", "")).decode(errors="replace")
        note(
            answer=method,
            request=params["requestId"],
            status=params.get("responseCode"),
            body=body[:2000],
        )
        if body.startswith("{") and '"queue_id": "preview"' in body:
            register = json.loads(body)
            drawn = {"plugins": register["armada_plugins"], "widgets": register["armada_widgets"]}
    elif method == "Target.createTarget":
        result = {"targetId": "target"}
    elif method == "Target.attachToTarget":
        result = {"sessionId": "page"}
    elif method == "Emulation.setEmulatedMedia":
        note(media=params["features"])
    elif method == "Page.navigate":
        note(navigate=params["url"])
        for url in [params["url"], *json.loads(os.environ.get("FAKE_CHROMIUM_REQUESTS", "[]"))]:
            requests += 1
            note(request=f"r{requests}", url=url)
            send(
                {
                    "method": "Fetch.requestPaused",
                    "sessionId": session,
                    "params": {
                        "requestId": f"r{requests}",
                        "request": {"url": url, "method": "GET"},
                    },
                }
            )
        send({"id": message["id"], "sessionId": session, "result": {"frameId": "frame"}})
        if behaviour == "throws":
            details = {
                "text": "Uncaught",
                "exception": {"description": "TypeError: x is null\n    at y"},
            }
            send(
                {
                    "method": "Runtime.exceptionThrown",
                    "sessionId": session,
                    "params": {"exceptionDetails": details},
                }
            )
        send({"method": "Page.loadEventFired", "sessionId": session, "params": {}})
        continue
    elif method == "Runtime.evaluate":
        expression = params["expression"]
        if behaviour == "slow":
            time.sleep(30)
        if behaviour == "error":
            send({"id": message["id"], "sessionId": session, "error": {"message": "Target closed"}})
            continue
        if behaviour == "exception":
            result = {"result": {"type": "undefined"}, "exceptionDetails": {"text": "Uncaught"}}
        elif ".wsheet" in expression:
            row_id = json.loads(expression.split("found.dataset.w === ", 1)[1].split(" ?", 1)[0])
            result = {"result": {"type": "object", "value": sheet(row_id)}}
        else:
            result = {"result": {"type": "object", "value": rows()}}
    elif method == "Input.dispatchMouseEvent":
        note(press=[params["type"], params["x"], params["y"]])
    elif method == "Page.captureScreenshot":
        note(picture=params["clip"])
        result = {"data": PNG}
    elif method == "Browser.close":
        note(closed=True)
        sys.exit(0)
    reply: dict[str, Any] = {"id": message["id"], "result": result}
    if session is not None:
        reply["sessionId"] = session
    send(reply)
