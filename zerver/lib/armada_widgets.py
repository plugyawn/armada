"""A plugin's widgets (design/r9/plugins, section 4).

Its bot pushes a document, or data for the document it pushed before, with
POST /api/v1/armada/widgets. Armada checks it against the plugin contract
and the plugin's manifest, keeps the latest for each widget and scope (the
nav, a link's place, or a row's item), and tells the clients that can see
that scope: at most one armada_widget event every two seconds for a widget
and scope, the newest document winning; the plugin host sends what waited.
A widget not fed for three times its cadence turns stale, so that a figure
that may be old never reads as current.

An action in a widget that calls one of the plugin's tools is run here,
on the plugin's MCP endpoint, for the person who pressed it, who is named
in the call's _meta. One that cannot be undone declares confirm, and is
run only once the person confirmed it. An action that asks the agent, or
opens a page, is the client's to do.
"""

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.armada_mcp_client import ArmadaMCPClient, ArmadaMCPError
from zerver.lib.armada_plugin_events import send_armada_widget_event
from zerver.lib.armada_plugin_validation import (
    ArmadaPluginContractError,
    canonicalize_widget_document,
)
from zerver.lib.armada_plugins import (
    armada_plugin_session,
    armada_plugin_token,
    armada_plugin_url,
    audit_plugin,
)
from zerver.lib.exceptions import JsonableError, ResourceNotFoundError
from zerver.lib.streams import access_stream_by_id
from zerver.models import PluginInstall, PluginLink, PluginWidgetState, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType

ARMADA_WIDGET_EVENT_SECONDS = 2
ARMADA_WIDGET_STALE_CADENCES = 3
ARMADA_WIDGET_DEFAULT_CADENCE = 60
ARMADA_PLUGIN_TOOL_TIMEOUT_SECONDS = 30
ARMADA_PLUGIN_TOOL_RESULT_LIMIT = 4000
ITEM_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _declared(install: PluginInstall, key: str) -> dict[str, Any]:
    for widget in install.manifest["provides"].get("widgets", []):
        if widget["key"] == key:
            return widget
    raise JsonableError(
        _("{name} declares no widget named {key}.").format(name=install.name, key=key)
    )


def _rows(document: dict[str, Any]) -> Iterator[dict[str, Any]]:
    data = document.get("data", {})
    for block in document["blocks"]:
        if block["type"] == "list":
            yield from block.get("items", data.get(block.get("bind", "").removeprefix("data."), []))


def _check_against_manifest(
    install: PluginInstall, declaration: dict[str, Any], document: dict[str, Any]
) -> None:
    """What only the manifest can say: a row opens one of the plugin's
    widgets that is opened from a row, and its facets are declared."""
    widgets = {widget["key"]: widget for widget in install.manifest["provides"].get("widgets", [])}
    facets = {facet["key"] for facet in declaration.get("options", {}).get("facets", [])}
    for row in _rows(document):
        target = row.get("open", {}).get("widget")
        if target is not None and "open" not in widgets.get(target, {}).get("in", []):
            raise JsonableError(
                _("A row opens {key}, which {name} does not open from a row.").format(
                    key=target, name=install.name
                )
            )
        for facet in row.get("facets", {}):
            if facet not in facets:
                raise JsonableError(
                    _("A row's facet {key} is not one {name} declares.").format(
                        key=facet, name=install.name
                    )
                )


def access_pushing_install(bot: UserProfile) -> PluginInstall:
    """The install whose bot this is: a plugin pushes only its own widgets."""
    install = (
        PluginInstall.objects.select_related("realm").filter(bot=bot).first()
        if bot.is_bot
        else None
    )
    if install is None:
        raise JsonableError(_("Only a plugin's own bot pushes its widgets."))
    if install.disabled or install.state == PluginInstall.REMOVED:
        raise JsonableError(_("{name} is turned off.").format(name=install.name))
    return install


def push_widget(
    bot: UserProfile,
    *,
    widget: str,
    link_id: int | None,
    item: str,
    document: dict[str, Any] | None,
    data: dict[str, Any] | None,
) -> PluginWidgetState:
    install = access_pushing_install(bot)
    declaration = _declared(install, widget)
    places = declaration["in"]
    link = None
    if link_id is not None:
        link = (
            PluginLink.objects.select_related("stream").filter(id=link_id, install=install).first()
        )
        if link is None:
            raise ResourceNotFoundError(_("No such link"))
    if item:
        if "open" not in places or not ITEM_KEY.match(item):
            raise JsonableError(
                _("{key} is not opened from a row, so it has no item {item}.").format(
                    key=widget, item=item
                )
            )
    elif link is None and "nav" not in places:
        raise JsonableError(
            _("{key} is not in the nav; it is pushed to a link.").format(key=widget)
        )
    elif link is not None and "column" not in places:
        raise JsonableError(
            _("{key} is not in the column, so it is not pushed to a link.").format(key=widget)
        )
    if (document is None) == (data is None):
        raise JsonableError(_("A push gives a widget's document, or data for the one it has."))
    state = PluginWidgetState.objects.filter(
        install=install, widget=widget, link=link, item=item
    ).first()
    if document is None:
        assert data is not None
        if state is None:
            raise JsonableError(_("{key}'s document comes before its data.").format(key=widget))
        document = {**state.document, "data": {**state.document.get("data", {}), **data}}
    try:
        canonical, _encoded = canonicalize_widget_document(document)
    except ArmadaPluginContractError as error:
        raise JsonableError(_("The widget is not valid: {error}").format(error=str(error)))
    _check_against_manifest(install, declaration, canonical)

    now = timezone_now()
    with transaction.atomic(durable=True):
        if state is None:
            state = PluginWidgetState(
                install=install, widget=widget, link=link, item=item, document=canonical
            )
        elif canonical == state.document and not state.stale:
            # Unchanged: fresher, and nothing to tell anyone.
            state.date_fed = now
            state.save(update_fields=["date_fed"])
            return state
        else:
            state.document = canonical
            state.version += 1
            state.stale = False
        state.date_fed = now
        due = state.date_sent is None or now - state.date_sent >= timedelta(
            seconds=ARMADA_WIDGET_EVENT_SECONDS
        )
        state.pending = not due
        if due:
            state.date_sent = now
            send_armada_widget_event(install, state, link, "update")
        state.save()
        if install.date_started is not None and (
            install.date_fed is None or install.date_fed < install.date_started
        ):
            # Recent's "Started": the first push of a fresh process.
            install.date_fed = now
            install.save(update_fields=["date_fed"])
            audit_plugin(
                install,
                bot,
                AuditLogEventType.PLUGIN_STARTED,
                {"version": install.version, "widget": widget},
            )
    return state


def flush_widget_events(now: datetime) -> int:
    """Send the documents that waited for their two seconds; the plugin
    host calls this on every pass. Returns how many went."""
    due = PluginWidgetState.objects.select_related("install__realm", "link__stream").filter(
        pending=True, date_sent__lte=now - timedelta(seconds=ARMADA_WIDGET_EVENT_SECONDS)
    )
    sent = 0
    for state in due:
        state.pending = False
        state.date_sent = now
        state.save(update_fields=["pending", "date_sent"])
        send_armada_widget_event(state.install, state, state.link, "update")
        sent += 1
    return sent


def mark_stale_widgets(now: datetime) -> int:
    """Mark the widgets not fed for three times their cadence, and tell
    their clients, which show the paused mark in place of the figure."""
    marked = 0
    for state in PluginWidgetState.objects.select_related("install__realm", "link__stream").filter(
        stale=False
    ):
        widgets = state.install.manifest["provides"].get("widgets", [])
        cadence = next(
            (
                widget.get("cadence", ARMADA_WIDGET_DEFAULT_CADENCE)
                for widget in widgets
                if widget["key"] == state.widget
            ),
            ARMADA_WIDGET_DEFAULT_CADENCE,
        )
        if now - state.date_fed <= timedelta(seconds=ARMADA_WIDGET_STALE_CADENCES * cadence):
            continue
        state.stale = True
        state.pending = False
        state.date_sent = now
        state.save(update_fields=["stale", "pending", "date_sent"])
        send_armada_widget_event(state.install, state, state.link, "update")
        marked += 1
    return marked


def _find_action(
    document: dict[str, Any], *, row: str | None, index: int | None
) -> dict[str, Any] | None:
    if row is not None:
        return next((found.get("action") for found in _rows(document) if found["key"] == row), None)
    actions = document.get("actions", [])
    if index is not None and 0 <= index < len(actions):
        return actions[index]
    return None


def run_widget_action(
    user: UserProfile,
    install: PluginInstall,
    *,
    widget: str,
    link_id: int | None,
    item: str,
    row: str | None,
    index: int | None,
    confirmed: bool,
) -> dict[str, Any]:
    """Call the tool a widget's action names, for the person who pressed
    it; returns what the tool said."""
    if install.disabled or install.state in (PluginInstall.REMOVED, PluginInstall.DRAFT):
        raise JsonableError(_("{name} is turned off.").format(name=install.name))
    state = (
        PluginWidgetState.objects.select_related("link")
        .filter(install=install, widget=widget, link_id=link_id, item=item)
        .first()
    )
    if state is None:
        raise ResourceNotFoundError(_("No such widget"))
    if state.link is not None:
        access_stream_by_id(user, state.link.stream_id)
    action = _find_action(state.document, row=row, index=index)
    if action is None:
        raise JsonableError(_("No such action"))
    if "tool" not in action:
        raise JsonableError(_("That action asks the agent or opens a page, which the app does."))
    if "confirm" in action and not confirmed:
        raise JsonableError(
            _("{label} needs confirming: {confirm}").format(
                label=action["label"], confirm=action["confirm"]
            )
        )
    tools = install.manifest["provides"].get("tools")
    if tools is None:
        raise JsonableError(_("{name} has no tools.").format(name=install.name))
    url = f"{armada_plugin_url(install)}{tools['path']}"
    session = armada_plugin_session(install, timeout=ARMADA_PLUGIN_TOOL_TIMEOUT_SECONDS)
    try:
        with ArmadaMCPClient(url, token=armada_plugin_token(install), session=session) as client:
            result = client.call_tool(
                action["tool"],
                action.get("arguments", {}),
                meta={
                    "armada/user": {
                        "id": user.id,
                        "email": user.email,
                        "full_name": user.full_name,
                    }
                },
            )
    except ArmadaMCPError as error:
        raise JsonableError(
            _("{name} {error}.").format(name=install.name, error=str(error))
        ) from error
    text = "\n".join(
        part["text"]
        for part in result.get("content", [])
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )
    is_error = bool(result.get("isError"))
    audit_plugin(
        install,
        user,
        AuditLogEventType.PLUGIN_TOOL_CALLED,
        {
            "tool": action["tool"],
            "widget": widget,
            "link_id": link_id,
            "item": item,
            "row": row,
            "is_error": is_error,
        },
    )
    return {"text": text[:ARMADA_PLUGIN_TOOL_RESULT_LIMIT], "is_error": is_error}
