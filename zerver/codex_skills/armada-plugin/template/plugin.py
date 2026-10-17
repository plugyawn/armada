"""An Armada plugin: a widget in the nav, fed every cadence, and a tool for
the agent.

Armada starts it in its own directory, with this environment:

- ARMADA_PORT: where to serve, on 127.0.0.1.
- ARMADA_PLUGIN_TOKEN: what Armada and the agent present; refuse the rest.
- ARMADA_SITE, ARMADA_EMAIL, ARMADA_API_KEY: the plugin's own bot.
- ARMADA_SETTINGS: the settings people filled in, as JSON.
- Each secret, under the env name its manifest gives it.
"""

import hmac
import json
import os
import threading
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from armada_push import push

SETTINGS: dict[str, Any] = json.loads(os.environ.get("ARMADA_SETTINGS", "{}"))
# The widget's cadence, in seconds, as the manifest gives it.
CADENCE = 30
# How a job's state is drawn: one of the kit's marks.
STATES = {"running": "live", "waiting": "waiting", "failed": "failed", "done": "done"}


def read_jobs() -> list[dict[str, Any]]:
    """The queue's jobs. Replace these with the real source: an API, using
    the secret from its env name, a database, or a command."""
    return [
        {"id": "job-1", "name": "eval-small", "owner": "iago", "state": "running"},
        {"id": "job-2", "name": "eval-large", "owner": "hamlet", "state": "waiting"},
        {"id": "job-3", "name": "eval-long", "owner": "iago", "state": "waiting"},
    ]


def document(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """The widget, as data: a figure at rest, and the jobs as rows."""
    waiting = sum(1 for job in jobs if job["state"] == "waiting")
    running = sum(1 for job in jobs if job["state"] == "running")
    return {
        "version": 1,
        "title": "Queue",
        "data": {
            "waiting": f"{waiting} waiting",
            "running": f"{running} running",
            "waiting_count": waiting,
            "news": "failed" if any(job["state"] == "failed" for job in jobs) else None,
            "jobs": [
                {
                    "key": job["id"],
                    "label": job["name"],
                    "code": True,
                    "state": STATES.get(job["state"], "waiting"),
                    "sub": job["owner"],
                    "facets": {"owner": job["owner"]},
                }
                for job in jobs[:32]
            ],
        },
        "rest": {"figure": "data.waiting", "news": "data.news", "sizer": "88 waiting"},
        "blocks": [{"type": "list", "label": "Queue", "bind": "data.jobs"}],
        "actions": [{"label": "Ask Onix", "prompt": "What is waiting in the queue, and why?"}],
    }


server = MCPServer("example")


@server.tool()
def queue_jobs(owner: str | None = None) -> list[dict[str, Any]]:
    """The queue's jobs, with their state and owner; give owner for one
    person's jobs."""
    return [job for job in read_jobs() if owner is None or job["owner"] == owner]


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def app() -> Starlette:
    """MCP's Streamable HTTP transport at the manifest's tools path, and
    /health. Each request stands alone and is answered with JSON."""
    return server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True
    )


class RequireToken:
    """Refuse every request but /health that lacks Armada's token."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/health":
            token = os.environ.get("ARMADA_PLUGIN_TOKEN", "")
            presented = dict(scope["headers"]).get(b"authorization", b"")
            if not token or not hmac.compare_digest(presented, f"Bearer {token}".encode()):
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def feed(stop: threading.Event) -> None:
    """Push the widget now and every cadence; a failed push is said in the
    plugin's log, and the next one tries again."""
    while not stop.is_set():
        try:
            push("queue", document(read_jobs()))
        except Exception as error:
            print(f"Could not push the widget: {error}", flush=True)
        stop.wait(CADENCE)


def main() -> None:
    threading.Thread(target=feed, args=(threading.Event(),), daemon=True).start()
    uvicorn.run(
        RequireToken(app()),
        host="127.0.0.1",
        port=int(os.environ["ARMADA_PORT"]),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
