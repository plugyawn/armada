"""The agent's tools for making plugins (design/r9/plugins, ROUND2 section C),
which the armada-plugin skill (zerver/codex_skills/armada-plugin) teaches.
Each acts for the person whose message started the run.

- armada_plugin_request_settings asks that person to fill in a plugin's
  settings, its secrets included, in the plugin's own settings sheet, and
  waits as a question does. It answers with the names that were set and the
  names still missing, never a value: a secret reaches only the plugin.

Codex calls them as dynamic tools, as it calls the message tools
(zerver/lib/armada_message_tools.py).
"""

from collections.abc import Callable
from typing import Any, Final

from django.utils.translation import gettext as _

from zerver.lib.armada import ARMADA_AGENT_NAME, ARMADA_QUESTION_TIMEOUT_SECONDS
from zerver.lib.armada_message_tools import ArmadaToolCaller, ArmadaToolTurn, caller_for_turn
from zerver.lib.armada_page_tools import (
    ArmadaToolAnswer,
    answer_tool_call,
    read_answer,
    string_argument,
    tool_arguments,
)
from zerver.lib.exceptions import JsonableError

ARMADA_PLUGIN_REQUEST_SETTINGS_TOOL: Final = "armada_plugin_request_settings"
# A plugin declares at most 16 settings and 8 secrets.
ARMADA_PLUGIN_REQUEST_FIELD_LIMIT: Final = 24


def armada_plugin_tool_specs() -> list[dict[str, Any]]:
    return [
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
    ]


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
    ARMADA_PLUGIN_REQUEST_SETTINGS_TOOL: request_settings_tool,
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
