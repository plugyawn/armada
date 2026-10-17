"""A plugin for the plugin tests (zerver/tests/test_armada_plugin_host.py,
test_armada_widgets.py), written with the standard library alone. On
127.0.0.1:$ARMADA_PORT it serves GET /health and, at POST /mcp, the Model
Context Protocol's Streamable HTTP transport for its two tools, to
whoever presents $ARMADA_PLUGIN_TOKEN. It fails on purpose when its
settings ask it to:

    exit_at_start  print a line and exit with status 3
    crash_after    exit with status 4 that many seconds after starting
    unhealthy      answer /health with 500
    say_secrets    print its secret, its bot's key and its token
    sse            answer MCP requests as server-sent events
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

settings = json.loads(os.environ["ARMADA_SETTINGS"])
print(
    f"fake plugin {os.environ['ARMADA_EMAIL']} starting on {os.environ['ARMADA_PORT']}", flush=True
)
if settings.get("say_secrets"):
    print(
        "key",
        os.environ.get("FAKE_KEY", ""),
        "bot",
        os.environ["ARMADA_API_KEY"],
        "token",
        os.environ["ARMADA_PLUGIN_TOKEN"],
        flush=True,
    )
if settings.get("exit_at_start"):
    print("crashing on purpose", flush=True)
    sys.exit(3)

TOOLS = [
    {
        "name": "echo",
        "description": "Says back what it was called with, and for whom.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "pod_terminate",
        "description": "Ends a pod.",
        "inputSchema": {"type": "object", "properties": {"pod": {"type": "string"}}},
    },
]


def answer(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    params = message.get("params") or {}
    if "id" not in message:
        # A notification, such as notifications/initialized.
        return None
    result: dict[str, Any]
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call" and params.get("name") == "echo":
        said = {"arguments": params.get("arguments"), "_meta": params.get("_meta")}
        result = {"content": [{"type": "text", "text": json.dumps(said, sort_keys=True)}]}
    elif method == "tools/call" and params.get("name") == "pod_terminate":
        result = {"content": [{"type": "text", "text": "No such pod."}], "isError": True}
    else:
        error = {"code": -32602, "message": "unknown tool"}
        return {"jsonrpc": "2.0", "id": message["id"], "error": error}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Mcp-Session-Id", "fake-session")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/mcp":
            # No stream of its own for the client to open, which the
            # transport answers with 405; Codex asks for one.
            self.reply(405)
            return
        healthy = self.path == "/health" and not settings.get("unhealthy")
        self.reply(200 if healthy else 500, b"ok" if healthy else b"no")

    def do_POST(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {os.environ['ARMADA_PLUGIN_TOKEN']}":
            self.reply(401)
            return
        length = int(self.headers.get("Content-Length", "0"))
        reply = answer(json.loads(self.rfile.read(length)))
        if reply is None:
            self.reply(202)
        elif settings.get("sse"):
            event = f"event: message\ndata: {json.dumps(reply)}\n\n"
            self.reply(200, event.encode(), "text/event-stream")
        else:
            self.reply(200, json.dumps(reply).encode(), "application/json")

    def do_DELETE(self) -> None:
        self.reply(200)


# The server logs each request to stderr; only what the plugin says is kept.
sys.stderr = open(os.devnull, "w")  # noqa: SIM115
server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["ARMADA_PORT"])), Handler)
if settings.get("crash_after"):
    threading.Timer(float(settings["crash_after"]), lambda: os._exit(4)).start()
server.serve_forever()
