"""A plugin for the trial's and the check's tests
(zerver/tests/test_armada_plugin_check.py), written with the standard
library alone. On 127.0.0.1:$ARMADA_PORT it serves GET /health, and its
tools at POST /mcp to whoever presents $ARMADA_PLUGIN_TOKEN. It talks to
$ARMADA_SITE with its bot's address and key, as a plugin does, and its
setting `behaviour` says what it does there:

    good      push its nav widget's document, then data for it
    bad       push a document whose second row's figure is too long
    silent    push nothing
    exit      print a line and exit with status 3
    unhealthy never answer /health
    secret    print its secret and push a document that shows it
    chatty    register a queue, wait on it, post a line, then push
    patches   push data before any document, then for a widget it lacks
    wide      push a figure wider than its sizer, and rows too long to fit

It writes its process id to the file `pid` where it runs.
"""

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

settings = json.loads(os.environ["ARMADA_SETTINGS"])
behaviour = settings.get("behaviour", "good")
site = os.environ["ARMADA_SITE"]
login = f"{os.environ['ARMADA_EMAIL']}:{os.environ['ARMADA_API_KEY']}"
authorization = "Basic " + base64.b64encode(login.encode()).decode()
print(f"queue plugin starting in a trial: {os.environ.get('ARMADA_TRIAL')}", flush=True)


def call(method: str, path: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Armada's API, as Zulip's client calls it: form fields, each value
    that is not a string encoded as JSON."""
    encoded = urllib.parse.urlencode(
        {
            name: value if isinstance(value, str) else json.dumps(value)
            for name, value in (fields or {}).items()
        }
    )
    url = f"{site}/api/v1/{path}"
    body = None
    if method == "GET":
        url += f"?{encoded}"
    else:
        body = encoded.encode()
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", authorization)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=70) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read())


def document(
    *, figure_two: str = "2h 14m", title: str = "Eval queue", sizer: str = "88 waiting"
) -> dict[str, Any]:
    return {
        "version": 1,
        "title": title,
        "data": {"waiting": "12 waiting", "news": None},
        "rest": {"figure": "data.waiting", "news": "data.news", "sizer": sizer},
        "blocks": [
            {
                "type": "list",
                "label": "Waiting",
                "items": [
                    {
                        "key": "mmlu",
                        "label": "mmlu-5shot",
                        "code": True,
                        "state": "live",
                        "sub": "lr-3e-4-b, step 42,100",
                        "figure": "4m",
                    },
                    {
                        "key": "gsm8k",
                        "label": "gsm8k",
                        "code": True,
                        "state": "waiting",
                        "sub": "behind mmlu-5shot",
                        "figure": figure_two,
                    },
                ],
            }
        ],
    }


def push(**fields: Any) -> None:
    print("push:", call("POST", "armada/widgets", fields)["result"], flush=True)


def work() -> None:
    if behaviour == "good":
        push(widget="queue", document=document())
        push(widget="queue", data={"waiting": "13 waiting"})
    elif behaviour == "bad":
        push(widget="queue", document=document(figure_two="2 hours and 14 minutes, about"))
    elif behaviour == "secret":
        secret = os.environ.get("QUEUE_REDIS_URL", "")
        print("connecting to", secret, flush=True)
        push(widget="queue", document=document(title=f"Queue at {secret}"))
    elif behaviour == "chatty":
        queue = call("POST", "register", {"event_types": ["message"]})
        threading.Thread(
            target=call,
            args=("GET", "events", {"queue_id": queue["queue_id"], "last_event_id": -1}),
            daemon=True,
        ).start()
        print(
            "posted:",
            call(
                "POST",
                "messages",
                {"type": "stream", "to": "plugins", "topic": "queue", "content": "Queue is up."},
            )["result"],
            flush=True,
        )
        print("asked:", call("GET", "streams")["msg"], flush=True)
        push(widget="queue", document=document())
    elif behaviour == "wide":
        wide = document(sizer="88", figure_two="2h 14m 30s")
        wide["blocks"][0]["items"][0]["label"] = "mmlu-5shot-with-chain-of-thought"
        push(widget="queue", document=wide)
    elif behaviour == "patches":
        push(widget="queue", data={"waiting": "1 waiting"})
        push(widget="nope", document=document())
        push(widget="queue", document=document())


TOOLS = [{"name": "queue_status", "description": "What is waiting, and for what."}]


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.reply(200 if self.path == "/health" else 404, b"ok")

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.reply(404)
            return
        if self.headers.get("Authorization") != f"Bearer {os.environ['ARMADA_PLUGIN_TOKEN']}":
            self.reply(401)
            return
        message = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if "id" not in message:
            self.reply(202)
            return
        if message["method"] == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "queue", "version": "0.1.0"},
            }
        else:
            result = {"tools": TOOLS}
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        self.reply(200, json.dumps(reply).encode(), "application/json")


with open("pid", "w") as pid:
    pid.write(str(os.getpid()))
if behaviour == "exit":
    print("crashing on purpose", flush=True)
    sys.exit(3)
if behaviour == "unhealthy":
    time.sleep(3600)
# The server logs each request to stderr; only what the plugin says is kept.
sys.stderr = open(os.devnull, "w")  # noqa: SIM115
server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["ARMADA_PORT"])), Handler)
threading.Thread(target=work, daemon=True).start()
server.serve_forever()
