"""What the agent gets of the team's plugins (design/r9/plugins, section
2c): each running plugin's MCP endpoint as one of Codex's MCP servers, a
line of the developer instructions from its manifest, and its name on the
trace's tool calls.

Codex reaches a plugin with its bearer token, which it reads from the
variable that bearer_token_env_var names: the token is in the app-server's
process environment and nowhere else, never in an argument, which `ps`
shows, and never in Codex's config. A plugin's secrets never reach the
agent; they are in the plugin's own process alone.
"""

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import astuple, dataclass, replace
from functools import cache

from django.conf import settings

from zerver.models import PluginInstall, Realm

logger = logging.getLogger(__name__)

# A dead plugin never blocks a turn (required=false), and one that does
# not answer delays its first turn by this much at most.
ARMADA_PLUGIN_AGENT_STARTUP_SECONDS = 10
ARMADA_PLUGIN_AGENT_TOOL_SECONDS = 30
ARMADA_PLUGIN_TOKEN_VARIABLE_PREFIX = "ARMADA_PLUGIN_TOKEN_"


@dataclass(frozen=True)
class ArmadaPluginServer:
    """One plugin that offers the agent tools. Its MCP server in Codex is
    named by the plugin's id, so its tools reach the model as
    <id>__<tool>. Only a running one is given to Codex; one that is
    starting or failed keeps its line of the instructions, so a plugin
    that fails and comes back starts no new thread."""

    server: str
    install_id: int
    name: str
    glyph: str
    instructions: str
    running: bool
    url: str = ""
    token: str = ""
    generation: int = 0
    disabled_tools: tuple[str, ...] = ()

    @property
    def token_variable(self) -> str:
        return ARMADA_PLUGIN_TOKEN_VARIABLE_PREFIX + self.server.upper().replace("-", "_")


@cache
def _warn_no_plugins_under_os_users() -> None:
    logger.warning(
        "The agent gets no plugin tools: ARMADA_TOPIC_ISOLATION_BACKEND is os-user, and"
        " sudo would drop the tokens from the app-server's environment."
    )


def armada_plugin_servers(realm: Realm) -> list[ArmadaPluginServer]:
    """The realm's plugins that offer the agent tools, by id: every
    install that is on and neither removed nor a draft."""
    # Here, not at the top: zerver/actions/streams.py imports the worker,
    # which imports this module, and armada_plugins imports the actions.
    from zerver.lib.armada_plugins import (
        ArmadaPluginKeyError,
        armada_plugin_token,
        armada_plugin_url,
    )

    # Under per-topic OS users (armada_topic_os_user_isolation_enabled),
    # the app-server starts through sudo, which drops its environment.
    if settings.ARMADA_TOPIC_ISOLATION_BACKEND == "os-user":
        _warn_no_plugins_under_os_users()
        return []
    installs = (
        PluginInstall.objects.filter(realm=realm, disabled=False)
        .exclude(state__in=[PluginInstall.REMOVED, PluginInstall.DRAFT])
        .order_by("plugin_id")
    )
    servers: list[ArmadaPluginServer] = []
    for install in installs:
        tools = install.manifest["provides"].get("tools")
        if tools is None:
            continue
        server = ArmadaPluginServer(
            server=install.plugin_id,
            install_id=install.id,
            name=install.name,
            glyph=install.manifest["glyph"],
            instructions=tools.get("instructions", ""),
            running=False,
        )
        if install.state == PluginInstall.RUNNING:
            try:
                token = armada_plugin_token(install)
            except ArmadaPluginKeyError:
                # Without the server's key no plugin can run; this one's
                # state has yet to say so.
                pass
            else:
                server = replace(
                    server,
                    running=True,
                    url=f"{armada_plugin_url(install)}{tools['path']}",
                    token=token,
                    generation=install.generation,
                    disabled_tools=tuple(install.disabled_tools),
                )
        servers.append(server)
    return servers


def armada_plugin_config_args(servers: Sequence[ArmadaPluginServer]) -> list[str]:
    """Codex's flags for the running plugins' MCP servers. Their tools are
    approved here, since Armada answers no approval request for them.
    Values are TOML, which JSON's strings and arrays of strings are."""
    args: list[str] = []
    for server in servers:
        if not server.running:
            continue
        prefix = f"mcp_servers.{server.server}"
        values = [
            f"url={json.dumps(server.url)}",
            f"bearer_token_env_var={json.dumps(server.token_variable)}",
            "required=false",
            f"startup_timeout_sec={ARMADA_PLUGIN_AGENT_STARTUP_SECONDS}",
            f"tool_timeout_sec={ARMADA_PLUGIN_AGENT_TOOL_SECONDS}",
            'default_tools_approval_mode="approve"',
        ]
        if server.disabled_tools:
            values.append(f"disabled_tools={json.dumps(list(server.disabled_tools))}")
        for value in values:
            args.extend(["--config", f"{prefix}.{value}"])
    return args


def armada_plugin_environment(servers: Sequence[ArmadaPluginServer]) -> dict[str, str]:
    """The app-server's environment beyond its own: each running plugin's
    token, under the variable its bearer_token_env_var names."""
    return {server.token_variable: server.token for server in servers if server.running}


def armada_plugin_instructions(servers: Sequence[ArmadaPluginServer]) -> str:
    """The developer instructions' lines for the team's plugins: one a
    plugin, in its manifest's words."""
    if not servers:
        return ""
    lines = [
        "Your team's plugins give you tools, from MCP servers named by each plugin's id."
        " A plugin that is not running has none at the moment:"
    ]
    for server in servers:
        line = f"- {server.name} ({server.server})"
        if server.instructions:
            line += f": {server.instructions}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def armada_plugin_instruction_lines(servers: Sequence[ArmadaPluginServer]) -> list[list[str]]:
    """What a thread's instructions hold of the plugins, for the thread's
    version (armada_codex_thread_version)."""
    return [[server.server, server.name, server.instructions] for server in servers]


def armada_plugin_fingerprint(servers: Sequence[ArmadaPluginServer]) -> str:
    """Everything an app-server is started with of the plugins: which run,
    where, with which token, generation and tools turned off, and the
    instructions its new threads get. A warm app-server whose fingerprint
    is not the realm's is restarted before the next turn."""
    encoded = json.dumps([astuple(server) for server in servers]).encode()
    return hashlib.sha256(encoded).hexdigest()
