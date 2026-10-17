"""The agent's tools for making plugins (design/r9/plugins, ROUND2 section C),
which the armada-plugin skill (zerver/codex_skills/armada-plugin) teaches.
Each acts for the person whose message started the run.

- armada_plugin_check checks a plugin in the run's worktree: the contract,
  a trial of its program and a preview of its widgets
  (zerver/lib/armada_plugin_check.py). The first check keeps the plugin's
  id as the person's draft.
- armada_plugin_request_settings asks that person to fill in a plugin's
  settings, its secrets included, in the plugin's own settings sheet, and
  waits as a question does. It answers with the names that were set and the
  names still missing, never a value: a secret reaches only the plugin.
- armada_plugin_install releases a commit to the team's plugins repository
  and installs it for that person; armada_plugin_update moves an install to
  a new commit (zerver/lib/armada_plugin_repository.py). Both record who
  asked, in which run, and where it was asked: PLUGIN_MADE, PLUGIN_UPDATED.

Codex calls them as dynamic tools, as it calls the message tools
(zerver/lib/armada_message_tools.py).
"""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from zerver.lib.armada import ARMADA_AGENT_NAME, ARMADA_QUESTION_TIMEOUT_SECONDS
from zerver.lib.armada_message_tools import (
    ARMADA_STARTED_BY_EVENT,
    ArmadaToolCaller,
    ArmadaToolTurn,
    caller_for_turn,
)
from zerver.lib.armada_page_tools import (
    ArmadaToolAnswer,
    answer_tool_call,
    read_answer,
    string_argument,
    tool_arguments,
)
from zerver.lib.exceptions import JsonableError
from zerver.models import AgentRunEvent, PluginInstall, Stream

if TYPE_CHECKING:
    from zerver.lib.armada_plugins import PluginOrigin

ARMADA_PLUGIN_CHECK_TOOL: Final = "armada_plugin_check"
ARMADA_PLUGIN_REQUEST_SETTINGS_TOOL: Final = "armada_plugin_request_settings"
ARMADA_PLUGIN_INSTALL_TOOL: Final = "armada_plugin_install"
ARMADA_PLUGIN_UPDATE_TOOL: Final = "armada_plugin_update"
# A plugin declares at most 16 settings and 8 secrets.
ARMADA_PLUGIN_REQUEST_FIELD_LIMIT: Final = 24


COMMIT_PROPERTIES: Final[dict[str, Any]] = {
    "id": {"type": "string", "description": "The plugin's id, as its directory is named."},
    "commit": {
        "type": "string",
        "description": "The commit's full sha, from git rev-parse HEAD in your worktree.",
    },
}


def armada_plugin_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": ARMADA_PLUGIN_CHECK_TOOL,
            "description": (
                "Check a plugin in your worktree, as Armada will run it: its manifest and "
                "widget documents against the contract, its program in a trial whose pushes "
                "reach no one, and its widgets drawn with Armada's own kit, light and dark. "
                "The first check keeps the plugin's id as a draft for the person you act "
                "for. Returns ok, problems [{where, what}], waiting_for (the settings and "
                "secrets to ask for), its widgets, tools and posts, and previews: look at "
                "each with view_image, and fix what looks wrong as well as each problem."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The plugin's directory in your worktree: plugins/<id>.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": ARMADA_PLUGIN_REQUEST_SETTINGS_TOOL,
            "description": (
                "Ask the person you act for to fill in a plugin's settings, its secrets "
                "included, in the plugin's own settings sheet, and wait for them. Never ask "
                "for a key, token or password in the conversation; this is how. fields names "
                "the settings and secrets to fill in; prefill gives settings, never secrets, "
                "the values the sheet opens with. Returns the names that were set and the "
                "names still missing, never a value. If something is still missing, say "
                "plainly what is needed and stop: don't install a plugin half set up."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The plugin's id."},
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": ARMADA_PLUGIN_REQUEST_FIELD_LIMIT,
                        "description": "The names of the settings and secrets to fill in.",
                    },
                    "prefill": {
                        "type": "object",
                        "description": "Settings' values the sheet opens with.",
                    },
                },
                "required": ["id", "fields"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": ARMADA_PLUGIN_INSTALL_TOOL,
            "description": (
                "Install a plugin you made and checked, for the team and for the person you "
                "act for: its commit is released to the team's plugins repository (main moves "
                "fast-forward, and the version is tagged), and its draft becomes the install "
                "the server runs. Its settings must be set first. Returns its version, its "
                "state and where its widgets show."
            ),
            "inputSchema": {
                "type": "object",
                "properties": COMMIT_PROPERTIES,
                "required": ["id", "commit"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": ARMADA_PLUGIN_UPDATE_TOOL,
            "description": (
                "Move an installed plugin to a new commit of yours, with a higher version, "
                "for everyone. Returns what the new version asks for that the old one did "
                "not (hosts, secrets, events) and the widgets it drops; say them in your "
                "reply. If it now waits for a setting, ask for it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": COMMIT_PROPERTIES,
                "required": ["id", "commit"],
                "additionalProperties": False,
            },
        },
    ]


def run_origin(caller: ArmadaToolCaller) -> "PluginOrigin":
    """Who asked for the plugin, in which run, and where: the place a run
    started by another run (armada_start_run) was asked from, else the
    run's own."""
    from zerver.lib.armada_plugins import PluginOrigin

    run = caller.run
    started_by = (
        AgentRunEvent.objects.filter(run=run, event_type=ARMADA_STARTED_BY_EVENT)
        .values_list("payload", flat=True)
        .first()
    )
    if isinstance(started_by, dict):
        stream_name = started_by.get("stream")
        topic = started_by.get("topic")
        message_id = started_by.get("message_id")
    else:
        stream_name, topic, message_id = run.stream_name, run.topic_name, run.trigger_message_id
    stream = Stream.objects.filter(realm_id=run.realm_id, name__iexact=stream_name).first()
    return PluginOrigin(
        made_by=caller.asker,
        run=run,
        stream=stream,
        topic=topic if stream is not None and isinstance(topic, str) else None,
        message_id=message_id if isinstance(message_id, int) else None,
    )


def run_workdir(caller: ArmadaToolCaller) -> Path:
    if caller.turn.workdir is None:
        raise JsonableError(_("This run has no worktree, where a plugin is made."))
    return Path(caller.turn.workdir)


def check_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    from zerver.lib.armada_plugin_check import check_plugin

    path = string_argument(arguments, "path")
    result = check_plugin(
        caller.asker, workdir=run_workdir(caller), path=path, origin=run_origin(caller)
    )
    plugin = result["plugin"] or path.strip().strip("/")
    if result["problems"]:
        title = _("Checked {plugin} · {problem}").format(
            plugin=plugin, problem=result["problems"][0]["what"]
        )
    else:
        widgets = len(result["widgets"])
        tools = len(result["tools"] or [])
        title = _("Checked {plugin} · {widgets}, {tools}, no problems").format(
            plugin=plugin,
            widgets=ngettext("{count} widget", "{count} widgets", widgets).format(count=widgets),
            tools=ngettext("{count} tool", "{count} tools", tools).format(count=tools),
        )
    return read_answer(result, title)


def install_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    from zerver.lib.armada_plugin_repository import install_team_plugin
    from zerver.lib.armada_plugins import plugin_waiting_for

    plugin_id = string_argument(arguments, "id")
    person = caller.asker
    draft = PluginInstall.objects.filter(
        realm=person.realm, plugin_id=plugin_id, state=PluginInstall.DRAFT
    ).first()
    if draft is None or person.id not in (draft.made_by_id, draft.installed_by_id):
        raise JsonableError(
            _(
                "Check {plugin} first with armada_plugin_check: that makes its draft, where "
                "its settings are kept until it is installed."
            ).format(plugin=plugin_id)
        )
    waiting_for = plugin_waiting_for(draft)
    if waiting_for:
        raise JsonableError(
            _(
                "{plugin} still needs {fields}: ask for them with "
                "armada_plugin_request_settings before installing it."
            ).format(plugin=draft.name, fields=", ".join(waiting_for))
        )
    install = install_team_plugin(
        person,
        plugin_id=plugin_id,
        sha=string_argument(arguments, "commit"),
        workdir=run_workdir(caller),
        origin=run_origin(caller),
    )
    return read_answer(
        {
            "id": install.plugin_id,
            "version": install.version,
            "sha": install.sha,
            "state": PluginInstall.STATE_NAMES[install.state],
            "widgets": install.widget_defaults,
        },
        _("Installed {plugin} {version} for the team, for {person}.").format(
            plugin=install.plugin_id, version=install.version, person=person.full_name
        ),
    )


def update_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    from zerver.lib.armada_plugin_repository import update_team_plugin
    from zerver.lib.armada_plugins import access_plugin_by_plugin_id, plugin_waiting_for

    person = caller.asker
    install = access_plugin_by_plugin_id(person, string_argument(arguments, "id"))
    if install.state == PluginInstall.DRAFT:
        raise JsonableError(
            _("{plugin} is not installed yet: install it with armada_plugin_install.").format(
                plugin=install.plugin_id
            )
        )
    diff = update_team_plugin(
        person,
        install,
        sha=string_argument(arguments, "commit"),
        workdir=run_workdir(caller),
        run=caller.run,
        for_user=person,
    )
    install.refresh_from_db()
    return read_answer(
        {
            "id": install.plugin_id,
            "version": install.version,
            "sha": install.sha,
            "state": PluginInstall.STATE_NAMES[install.state],
            "diff": diff,
            "waiting_for": plugin_waiting_for(install),
        },
        _("Updated {plugin} to {version} for the team, for {person}.").format(
            plugin=install.plugin_id, version=install.version, person=person.full_name
        ),
    )


def request_settings_tool(
    caller: ArmadaToolCaller, arguments: dict[str, object]
) -> ArmadaToolAnswer:
    # Lazy import: armada_plugins imports the actions, which import the
    # worker, which imports the agent runtime and so this module.
    from zerver.lib.armada_plugins import access_plugin_by_plugin_id, plugin_fields_without_values

    install = access_plugin_by_plugin_id(caller.asker, string_argument(arguments, "id"))
    fields = arguments.get("fields")
    if (
        not isinstance(fields, list)
        or not 0 < len(fields) <= ARMADA_PLUGIN_REQUEST_FIELD_LIMIT
        or not all(isinstance(name, str) for name in fields)
        or len(set(fields)) != len(fields)
    ):
        raise JsonableError(_("fields names each setting or secret to fill in once."))
    names: list[str] = [name for name in fields if isinstance(name, str)]
    settings = install.manifest.get("settings", {}).get("properties", {})
    secrets = install.manifest.get("secrets", {})
    for name in names:
        if name not in settings and name not in secrets:
            raise JsonableError(
                _("{plugin} has no setting or secret named {name}.").format(
                    plugin=install.name, name=name
                )
            )
    prefill = arguments.get("prefill", {})
    if not isinstance(prefill, dict) or any(key not in settings for key in prefill):
        raise JsonableError(_("prefill gives values to settings only; a secret is never given."))

    person = caller.asker
    titles = [(settings.get(name) or secrets[name])["title"] for name in names]
    if not plugin_fields_without_values(install, names):
        return read_answer(
            {"set": names, "missing": []},
            _("Already set for {plugin}: {fields}.").format(
                plugin=install.name, fields=", ".join(names)
            ),
        )
    wait = caller.turn.person_wait
    if wait is None:
        raise JsonableError(
            _("Nobody can be asked from here; say in your reply what {plugin} needs.").format(
                plugin=install.name
            )
        )
    ask = "\n\n".join(
        [
            _("{agent} asks {person} to set up **{plugin}**, in its settings: {titles}.").format(
                agent=ARMADA_AGENT_NAME,
                person=f"@**{person.full_name}|{person.id}**",
                plugin=install.name,
                titles=", ".join(titles),
            ),
            "*"
            + _(
                "Open the settings from the button, or from {plugin} in Plugins. The run "
                "waits up to {minutes} minutes."
            ).format(plugin=install.name, minutes=ARMADA_QUESTION_TIMEOUT_SECONDS // 60)
            + "*",
        ]
    )
    wait(
        ask,
        {
            "kind": "settings",
            "label": "waiting on you",
            "install_id": install.id,
            "fields": names,
            "prefill": prefill,
        },
        lambda: not plugin_fields_without_values(install, names),
    )
    missing = plugin_fields_without_values(install, names)
    title = _("Asked {person} for {fields}, in the settings.").format(
        person=person.full_name, fields=", ".join(names)
    )
    if missing:
        title += " " + _("Still missing: {fields}.").format(fields=", ".join(missing))
    return read_answer(
        {"set": [name for name in names if name not in missing], "missing": missing}, title
    )


ARMADA_PLUGIN_TOOLS: Final[
    dict[str, Callable[[ArmadaToolCaller, dict[str, object]], ArmadaToolAnswer]]
] = {
    ARMADA_PLUGIN_CHECK_TOOL: check_tool,
    ARMADA_PLUGIN_REQUEST_SETTINGS_TOOL: request_settings_tool,
    ARMADA_PLUGIN_INSTALL_TOOL: install_tool,
    ARMADA_PLUGIN_UPDATE_TOOL: update_tool,
}
ARMADA_PLUGIN_TOOL_NAMES: Final = frozenset(ARMADA_PLUGIN_TOOLS)


def answer_armada_plugin_tool(
    turn: ArmadaToolTurn, tool: str, arguments: object
) -> ArmadaToolAnswer:
    """Answer one of the plugin tools, for the person who asked for the
    turn."""
    return answer_tool_call(
        lambda: ARMADA_PLUGIN_TOOLS[tool](caller_for_turn(turn), tool_arguments(arguments))
    )
