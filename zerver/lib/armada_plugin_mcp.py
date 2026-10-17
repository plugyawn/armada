"""The team's plugins' tools for agents off this server (design/r9/plugins,
section 2c): /api/v1/armada/plugins/<id>/mcp, which a mini, or a person's
own Codex or Claude Code, reaches with an Armada API key.

Armada forwards each message of MCP's Streamable HTTP transport to the
plugin with the plugin's own token, which the caller never sees. A tool
call names its caller in _meta, as a widget's action does, and goes into
the plugin's Recent; the tools the team turned off are left out of
tools/list and refused on tools/call. The answer is always one JSON
message, never the plugin's own body: a plugin that answers with
server-sent events has its reply among them, and nothing it sends is
served from Armada's origin as anything but JSON.
"""

from dataclasses import dataclass
from typing import Any

import requests
from django.utils.translation import gettext as _

from zerver.lib.armada_mcp_client import ArmadaMCPClient, ArmadaMCPError, armada_mcp_messages
from zerver.lib.armada_plugins import (
    armada_plugin_session,
    armada_plugin_token,
    armada_plugin_url,
    audit_plugin,
)
from zerver.lib.exceptions import JsonableError, ResourceNotFoundError
from zerver.models import PluginInstall, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType

ARMADA_PLUGIN_MCP_TIMEOUT_SECONDS = 30
# The transport's headers the caller and the plugin exchange.
ARMADA_PLUGIN_MCP_SESSION_HEADER = "Mcp-Session-Id"
ARMADA_PLUGIN_MCP_VERSION_HEADER = "MCP-Protocol-Version"
JSON_RPC_INVALID_PARAMS = -32602


class ArmadaPluginMCPError(JsonableError):
    """A plugin's failure, with the status its caller gets for it."""

    def __init__(self, msg: str, *, http_status_code: int) -> None:
        super().__init__(msg)
        self.http_status_code = http_status_code


@dataclass(frozen=True)
class ArmadaPluginMCPReply:
    status: int
    message: dict[str, Any] | None
    session_id: str | None = None


def access_plugin_tools(user: UserProfile, install_id: int) -> PluginInstall:
    """The running install whose tools this agent may use: any member's,
    or any bot's but a plugin's own, which acts only as the design lets
    it. A guest sees the plugins' widgets and nothing more."""
    if user.is_guest:
        raise JsonableError(_("Guests cannot use the team's plugins' tools."))
    if PluginInstall.objects.filter(bot=user).exists():
        raise JsonableError(_("A plugin's bot cannot use the plugins' tools."))
    install = (
        PluginInstall.objects.select_related("bot", "realm")
        .filter(id=install_id, realm=user.realm)
        .exclude(state__in=[PluginInstall.REMOVED, PluginInstall.DRAFT])
        .first()
    )
    if install is None:
        raise ResourceNotFoundError(_("No such plugin"))
    check_plugin_tools_running(install)
    return install


def check_plugin_tools_running(install: PluginInstall) -> None:
    if "tools" not in install.manifest["provides"]:
        raise JsonableError(_("{name} has no tools.").format(name=install.name))
    if install.disabled or install.state != PluginInstall.RUNNING:
        raise JsonableError(_("{name} is not running.").format(name=install.name))


def list_plugin_tools(install: PluginInstall) -> list[dict[str, Any]]:
    """The tools a running plugin offers, as its settings page lists them
    for "Tools for Onix": each tool's name, title and description, in
    the plugin's own words and drawn as text, and whether the team turned
    it off."""
    check_plugin_tools_running(install)
    tools = install.manifest["provides"]["tools"]
    session = armada_plugin_session(install, timeout=ARMADA_PLUGIN_MCP_TIMEOUT_SECONDS)
    try:
        with ArmadaMCPClient(
            f"{armada_plugin_url(install)}{tools['path']}",
            token=armada_plugin_token(install),
            session=session,
        ) as client:
            listed = client.list_tools()
    except ArmadaMCPError as error:
        raise JsonableError(
            _("{name} {error}.").format(name=install.name, error=str(error))
        ) from error
    return [
        {
            "name": tool["name"],
            "title": _tool_title(tool),
            "description": tool["description"] if isinstance(tool.get("description"), str) else "",
            "disabled": tool["name"] in install.disabled_tools,
        }
        for tool in listed
        if isinstance(tool.get("name"), str)
    ]


def _tool_title(tool: dict[str, Any]) -> str | None:
    """A tool's title: its own, or, in earlier revisions of MCP, its
    annotations'."""
    annotations = tool.get("annotations")
    for title in (
        tool.get("title"),
        annotations.get("title") if isinstance(annotations, dict) else None,
    ):
        if isinstance(title, str) and title:
            return title
    return None


def _plugin_request(
    install: PluginInstall, method: str, headers: dict[str, str], **kwargs: Any
) -> requests.Response:
    tools = install.manifest["provides"]["tools"]
    session = armada_plugin_session(install, timeout=ARMADA_PLUGIN_MCP_TIMEOUT_SECONDS)
    headers = {
        **{
            name: value
            for name, value in headers.items()
            if name in (ARMADA_PLUGIN_MCP_SESSION_HEADER, ARMADA_PLUGIN_MCP_VERSION_HEADER)
        },
        "Authorization": f"Bearer {armada_plugin_token(install)}",
    }
    try:
        return session.request(
            method, f"{armada_plugin_url(install)}{tools['path']}", headers=headers, **kwargs
        )
    except requests.RequestException as error:
        raise JsonableError(
            _("{name} could not be reached: {error}.").format(
                name=install.name, error=error.__class__.__name__
            )
        ) from error


def _plugin_failed(
    install: PluginInstall, response: requests.Response
) -> ArmadaPluginMCPError | None:
    """What a plugin's failure is to the caller: a lost session (404), or
    a method the plugin does not take (405), is the caller's to act on;
    anything else is the plugin's, which the caller cannot mend."""
    if response.status_code < 400:
        return None
    return ArmadaPluginMCPError(
        _("{name} answered with HTTP {status}.").format(
            name=install.name, status=response.status_code
        ),
        http_status_code=response.status_code if response.status_code in (404, 405) else 400,
    )


def forward_mcp_message(
    user: UserProfile, install: PluginInstall, message: object, *, headers: dict[str, str]
) -> ArmadaPluginMCPReply:
    """Forward one message of the caller's to the plugin, and return the
    plugin's reply to it, if it is a request."""
    if not isinstance(message, dict):
        raise JsonableError(_("An MCP message is one JSON object."))
    method = message.get("method")
    if method == "tools/call":
        params = message.get("params")
        tool = params.get("name") if isinstance(params, dict) else None
        if not isinstance(params, dict) or not isinstance(tool, str):
            raise JsonableError(_("A tool call names its tool."))
        if tool in install.disabled_tools:
            error = {
                "code": JSON_RPC_INVALID_PARAMS,
                "message": f"{tool} is turned off for this team.",
            }
            return ArmadaPluginMCPReply(
                200, {"jsonrpc": "2.0", "id": message.get("id"), "error": error}
            )
        given_meta = params.get("_meta")
        meta = given_meta if isinstance(given_meta, dict) else {}
        caller = {"id": user.id, "email": user.email, "full_name": user.full_name}
        message = {**message, "params": {**params, "_meta": {**meta, "armada/user": caller}}}

    response = _plugin_request(
        install,
        "POST",
        {**headers, "Accept": "application/json, text/event-stream"},
        json=message,
    )
    failure = _plugin_failed(install, response)
    if failure is not None:
        raise failure
    session_id = response.headers.get(ARMADA_PLUGIN_MCP_SESSION_HEADER)
    if "id" not in message or "method" not in message:
        # A notification, or the caller's answer to the plugin's request:
        # the plugin acknowledges it, with nothing to say.
        return ArmadaPluginMCPReply(202, None, session_id)
    try:
        replies = armada_mcp_messages(response)
    except ValueError as error:
        raise JsonableError(
            _("{name} answered with something other than JSON.").format(name=install.name)
        ) from error
    reply = next(
        (
            reply
            for reply in replies
            if isinstance(reply, dict)
            and reply.get("id") == message["id"]
            and ("result" in reply or "error" in reply)
        ),
        None,
    )
    if reply is None:
        raise JsonableError(_("{name} did not answer.").format(name=install.name))
    result = reply.get("result")
    if method == "tools/list" and isinstance(result, dict):
        reply = {
            **reply,
            "result": {
                **result,
                "tools": [
                    tool
                    for tool in result.get("tools", [])
                    if not isinstance(tool, dict) or tool.get("name") not in install.disabled_tools
                ],
            },
        }
    if method == "tools/call":
        audit_plugin(
            install,
            user,
            AuditLogEventType.PLUGIN_TOOL_CALLED,
            {
                "tool": message["params"]["name"],
                "via": "mcp",
                "is_error": "error" in reply
                or (isinstance(result, dict) and bool(result.get("isError"))),
            },
        )
    return ArmadaPluginMCPReply(200, reply, session_id)


def end_mcp_session(install: PluginInstall, *, headers: dict[str, str]) -> int:
    """Forward the caller's end of its session; the plugin may not keep
    sessions, or allow ending them."""
    response = _plugin_request(install, "DELETE", headers)
    failure = _plugin_failed(install, response)
    if failure is not None:
        raise failure
    return response.status_code
