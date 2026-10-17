"""A small client for a plugin's MCP endpoint (Model Context Protocol,
Streamable HTTP transport), written for the two calls Armada makes itself:
tools/list and tools/call. Codex and the minis reach the same endpoint
with their own MCP clients (design/r9/plugins, section 2c).

Each request is one JSON-RPC message POSTed to the endpoint, with the
install's bearer token. The answer comes back as JSON, or as a stream of
server-sent events that holds it. A session starts with initialize and
notifications/initialized, and carries the server's Mcp-Session-Id, if it
gives one, until it is closed.
"""

import contextlib
import json
from typing import Any

import requests
from typing_extensions import Self

# The revision this client speaks, which is the one it offers; a server may
# answer with an earlier one it speaks instead.
ARMADA_MCP_PROTOCOL_VERSION = "2025-06-18"
ARMADA_MCP_PROTOCOL_VERSIONS = frozenset({"2025-06-18", "2025-03-26", "2024-11-05"})
# tools/list pages followed, at most.
ARMADA_MCP_TOOL_PAGES = 10


class ArmadaMCPError(Exception):
    pass


def armada_mcp_messages(response: requests.Response) -> list[Any]:
    """The JSON-RPC messages in an answer: a body of JSON, or each event's
    data in a stream of server-sent events."""
    if response.headers.get("Content-Type", "").startswith("text/event-stream"):
        messages = []
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[len("data:") :].removeprefix(" ")
                for line in event.split("\n")
                if line.startswith("data:")
            )
            if data:
                messages.append(json.loads(data))
        return messages
    return [response.json()]


class ArmadaMCPClient:
    def __init__(self, url: str, *, token: str, session: requests.Session) -> None:
        self.url = url
        self.token = token
        self.session = session
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.next_id = 1

    def __enter__(self) -> Self:
        self.initialize()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.protocol_version is not None:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, method: str, params: dict[str, Any], *, notify: bool = False) -> Any:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            message["id"] = request_id = self.next_id
            self.next_id += 1
        try:
            response = self.session.post(self.url, json=message, headers=self._headers())
        except requests.RequestException as error:
            raise ArmadaMCPError(f"could not be reached: {error.__class__.__name__}") from error
        if response.status_code >= 400:
            raise ArmadaMCPError(f"answered {method} with HTTP {response.status_code}")
        if notify:
            return None
        self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
        try:
            replies = armada_mcp_messages(response)
        except ValueError as error:
            raise ArmadaMCPError(f"answered {method} with something other than JSON") from error
        for reply in replies:
            if isinstance(reply, dict) and reply.get("id") == request_id:
                if "error" in reply:
                    failure = reply["error"]
                    detail = failure.get("message", "") if isinstance(failure, dict) else ""
                    raise ArmadaMCPError(f"refused {method}: {detail}".rstrip(": "))
                return reply.get("result", {})
        raise ArmadaMCPError(f"did not answer {method}")

    def initialize(self) -> None:
        result = self._post(
            "initialize",
            {
                "protocolVersion": ARMADA_MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Armada", "version": "1"},
            },
        )
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if version not in ARMADA_MCP_PROTOCOL_VERSIONS:
            raise ArmadaMCPError(f"speaks an MCP revision Armada does not: {version}")
        self.protocol_version = version
        self._post("notifications/initialized", {}, notify=True)

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _page in range(ARMADA_MCP_TOOL_PAGES):
            result = self._post("tools/list", {} if cursor is None else {"cursor": cursor})
            tools.extend(tool for tool in result.get("tools", []) if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any]
    ) -> dict[str, Any]:
        result = self._post("tools/call", {"name": name, "arguments": arguments, "_meta": meta})
        if not isinstance(result, dict):
            raise ArmadaMCPError("answered tools/call with something other than a result")
        return result

    def close(self) -> None:
        """End the session, if the server keeps one; it may not allow it."""
        if self.session_id is None:
            return
        with contextlib.suppress(requests.RequestException):
            self.session.delete(self.url, headers=self._headers())
        self.session_id = None
