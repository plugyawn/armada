"""A plugin for the plugin host's tests (zerver/tests/test_armada_plugin_host.py),
written with the standard library alone. It serves GET /health on
127.0.0.1:$ARMADA_PORT, and fails on purpose when its settings ask it to:

    exit_at_start  print a line and exit with status 3
    crash_after    exit with status 4 that many seconds after starting
    unhealthy      answer /health with 500
    say_secrets    print its secret, its bot's key and its token
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        healthy = self.path == "/health" and not settings.get("unhealthy")
        self.send_response(200 if healthy else 500)
        self.end_headers()
        self.wfile.write(b"ok" if healthy else b"no")


# The server logs each request to stderr; only what the plugin says is kept.
sys.stderr = open(os.devnull, "w")  # noqa: SIM115
server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["ARMADA_PORT"])), Handler)
if settings.get("crash_after"):
    threading.Timer(float(settings["crash_after"]), lambda: os._exit(4)).start()
server.serve_forever()
