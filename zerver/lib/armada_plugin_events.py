"""What clients see of plugins (design/r9/plugins, section 4): the
armada_plugin, armada_widget and armada_widget_preference events, their
/register snapshot, and how an event lands in a snapshot.

An install is shown to everyone in the realm, a draft to its maker alone,
and a link only to whoever can read its channel. A widget's document in
the nav goes to everyone, and one in a linked place to that channel's
subscribers. A draft's widgets go to no one: its pushes are a trial's. A
person's preferences for a widget (ROUND2.md, section G) go to that person
alone, for the installs they see: they leave with a removed install and
come back with its Undo.
"""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from zerver.lib.stream_subscription import get_user_ids_for_streams
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import (
    PluginInstall,
    PluginLink,
    PluginWidgetPreference,
    PluginWidgetState,
    Stream,
    UserProfile,
)
from zerver.models.users import active_non_guest_user_ids, active_user_ids
from zerver.tornado.django_api import send_event_on_commit

# Installs neither listed nor drawn.
HIDDEN_STATES = (PluginInstall.REMOVED,)


def plugin_view(install: PluginInstall, links: Iterable[PluginLink]) -> dict[str, Any]:
    """An install as its widgets need it, for everyone who sees it: never
    a setting, a secret or what its process wrote."""
    return {
        "id": install.id,
        "plugin": install.plugin_id,
        "name": install.name,
        "glyph": install.manifest["glyph"],
        "version": install.version,
        "state": PluginInstall.STATE_NAMES[install.state],
        "disabled": install.disabled,
        "bot_user_id": install.bot_id,
        "widgets": install.manifest["provides"].get("widgets", []),
        "widget_defaults": install.widget_defaults,
        "links": [
            {
                "id": link.id,
                "kind": link.kind,
                "value": link.value,
                "stream_id": link.stream_id,
                "topic": link.topic,
            }
            for link in links
        ],
        "made_by": install.made_by_id,
        "made_in_stream_id": install.made_in_stream_id,
        "made_in_topic": install.made_in_topic,
        "made_in_message_id": install.made_in_message_id,
    }


def _plugin_viewers(install: PluginInstall) -> list[int]:
    if install.state == PluginInstall.DRAFT:
        return sorted(
            {user_id for user_id in (install.made_by_id, install.installed_by_id) if user_id}
        )
    return active_user_ids(install.realm_id)


def _link_readers(links: list[PluginLink]) -> dict[int, set[int]]:
    """Who can read each link's channel: its subscribers, and, for a public
    channel, every member who is not a guest."""
    subscribers = get_user_ids_for_streams({link.stream_id for link in links})
    members: set[int] | None = None
    readers: dict[int, set[int]] = {}
    for link in links:
        readers[link.id] = set(subscribers.get(link.stream_id, set()))
        if not link.stream.invite_only:
            if members is None:
                members = set(active_non_guest_user_ids(link.stream.realm_id))
            readers[link.id] |= members
    return readers


def send_armada_plugin_event(install: PluginInstall, op: str) -> None:
    """add, when an install becomes visible; update; remove. Each person
    gets the links they can read, so a link to a private channel is never
    shown outside it."""
    if op == "remove":
        send_event_on_commit(
            install.realm,
            {"type": "armada_plugin", "op": "remove", "install_id": install.id},
            _plugin_viewers(install),
        )
        return
    links = list(PluginLink.objects.filter(install=install).select_related("stream").order_by("id"))
    readers = _link_readers(links)
    groups: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for user_id in _plugin_viewers(install):
        groups[tuple(link.id for link in links if user_id in readers[link.id])].append(user_id)
    for link_ids, user_ids in groups.items():
        view = plugin_view(install, [link for link in links if link.id in link_ids])
        send_event_on_commit(
            install.realm, {"type": "armada_plugin", "op": op, "plugin": view}, user_ids
        )


def widget_payload(state: PluginWidgetState, link: PluginLink | None) -> dict[str, Any]:
    return {
        "install_id": state.install_id,
        "widget": state.widget,
        "link_id": state.link_id,
        "item": state.item,
        "stream_id": link.stream_id if link is not None else None,
        "topic": link.topic if link is not None else None,
        "document": state.document,
        "version": state.version,
        "fed_at": datetime_to_timestamp(state.date_fed),
        "stale": state.stale,
    }


def _scope_user_ids(install: PluginInstall, link: PluginLink | None) -> list[int]:
    """Everyone for the nav; the channel's subscribers for a linked place."""
    if link is None:
        return active_user_ids(install.realm_id)
    return sorted(get_user_ids_for_streams({link.stream_id}).get(link.stream_id, set()))


def send_armada_widget_event(
    install: PluginInstall, state: PluginWidgetState, link: PluginLink | None, op: str
) -> None:
    if install.state == PluginInstall.DRAFT:
        return
    if op == "remove":
        event: dict[str, Any] = {
            "type": "armada_widget",
            "op": "remove",
            "install_id": state.install_id,
            "widget": state.widget,
            "link_id": state.link_id,
            "item": state.item,
        }
    else:
        event = {"type": "armada_widget", "op": "update", **widget_payload(state, link)}
    send_event_on_commit(install.realm, event, _scope_user_ids(install, link))


def preference_view(preference: PluginWidgetPreference) -> dict[str, Any]:
    return {
        "install_id": preference.install_id,
        "widget": preference.widget,
        "shown": preference.shown,
        "position": preference.position,
        "label": preference.label,
        "rest_figure": preference.rest_figure,
        "filters": preference.filters,
        "alerts": preference.alerts,
        "in_column": preference.in_column,
        "seen_at": (
            datetime_to_timestamp(preference.seen_at) if preference.seen_at is not None else None
        ),
    }


def send_widget_preference_event(
    install: PluginInstall, preference: PluginWidgetPreference
) -> None:
    send_event_on_commit(
        install.realm,
        {
            "type": "armada_widget_preference",
            "op": "update",
            "preference": preference_view(preference),
        },
        [preference.user_id],
    )


def send_widget_preference_removal(install: PluginInstall, user_id: int, widget: str) -> None:
    send_event_on_commit(
        install.realm,
        {
            "type": "armada_widget_preference",
            "op": "remove",
            "install_id": install.id,
            "widget": widget,
        },
        [user_id],
    )


def send_widget_preferences(install: PluginInstall, *, removed: bool) -> None:
    """An install removed, or back from Remove: each person's preferences for
    it leave their clients with it, and come back with it. The rows stay
    for the week Undo lasts, and are purged with the install."""
    for preference in PluginWidgetPreference.objects.filter(install=install):
        if removed:
            send_widget_preference_removal(install, preference.user_id, preference.widget)
        else:
            send_widget_preference_event(install, preference)


def forget_widget_preferences(install: PluginInstall, widgets: Iterable[str]) -> None:
    """Widgets an update dropped take everyone's preferences for them along
    (ROUND2.md A9: a widget's key is stable for an install's life)."""
    preferences = list(
        PluginWidgetPreference.objects.filter(install=install, widget__in=list(widgets))
    )
    for preference in preferences:
        send_widget_preference_removal(install, preference.user_id, preference.widget)
    PluginWidgetPreference.objects.filter(id__in=[p.id for p in preferences]).delete()


def fetch_armada_plugin_state(
    user: UserProfile, *, want_plugins: bool, want_widgets: bool, want_preferences: bool = False
) -> dict[str, Any]:
    """The /register snapshot: the installs this person sees, the widget
    documents of the places they can see, and their own preferences for
    those installs' widgets."""
    state: dict[str, Any] = {}
    installs = [
        install
        for install in PluginInstall.objects.filter(realm_id=user.realm_id)
        .exclude(state__in=HIDDEN_STATES)
        .order_by("id")
        if install.state != PluginInstall.DRAFT
        or user.id in (install.made_by_id, install.installed_by_id)
    ]
    links = list(
        PluginLink.objects.filter(install__in=installs).select_related("stream").order_by("id")
    )
    subscribed = set(
        Stream.objects.filter(
            recipient__subscription__user_profile=user,
            recipient__subscription__active=True,
            id__in={link.stream_id for link in links},
        ).values_list("id", flat=True)
    )
    if want_plugins:
        readable = [
            link
            for link in links
            if link.stream_id in subscribed or (not link.stream.invite_only and not user.is_guest)
        ]
        by_install: dict[int, list[PluginLink]] = defaultdict(list)
        for link in readable:
            by_install[link.install_id].append(link)
        state["armada_plugins"] = [
            plugin_view(install, by_install[install.id]) for install in installs
        ]
    if want_widgets:
        shown = {install.id for install in installs if install.state != PluginInstall.DRAFT}
        links_by_id = {link.id: link for link in links}
        state["armada_widgets"] = sorted(
            (
                widget_payload(widget, links_by_id.get(widget.link_id) if widget.link_id else None)
                for widget in PluginWidgetState.objects.filter(install_id__in=shown)
                if widget.link_id is None or links_by_id[widget.link_id].stream_id in subscribed
            ),
            key=_scope_order,
        )
    if want_preferences:
        state["armada_widget_preferences"] = (
            [
                preference_view(preference)
                for preference in PluginWidgetPreference.objects.filter(
                    user=user, install__in=installs
                ).order_by("install_id", "widget")
            ]
            if installs
            else []
        )
    return state


def _scope_order(widget: dict[str, Any]) -> tuple[int, str, int, str]:
    return (widget["install_id"], widget["widget"], widget["link_id"] or 0, widget["item"])


def _same_scope(entry: dict[str, Any], event: dict[str, Any]) -> bool:
    return all(entry[key] == event[key] for key in ("install_id", "widget", "link_id", "item"))


def _apply_widget_preference_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    same = (
        (event["preference"]["install_id"], event["preference"]["widget"])
        if event["op"] == "update"
        else (event["install_id"], event["widget"])
    )
    preferences = [
        preference
        for preference in state["armada_widget_preferences"]
        if (preference["install_id"], preference["widget"]) != same
    ]
    if event["op"] == "update":
        preferences.append(event["preference"])
    state["armada_widget_preferences"] = sorted(
        preferences, key=lambda preference: (preference["install_id"], preference["widget"])
    )


def apply_armada_plugin_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    """An armada_plugin, armada_widget or armada_widget_preference event,
    applied to a /register snapshot as the client applies it. A queue gets
    only the event types it registered for, and its snapshot holds each of
    them."""
    if event["type"] == "armada_widget_preference":
        _apply_widget_preference_event(state, event)
        return
    if event["type"] == "armada_plugin":
        install_id = event["install_id"] if event["op"] == "remove" else event["plugin"]["id"]
        plugins = [plugin for plugin in state["armada_plugins"] if plugin["id"] != install_id]
        if event["op"] != "remove":
            plugins.append(event["plugin"])
        state["armada_plugins"] = sorted(plugins, key=lambda plugin: plugin["id"])
        return
    widgets = [widget for widget in state["armada_widgets"] if not _same_scope(widget, event)]
    if event["op"] == "update":
        widgets.append(
            {key: value for key, value in event.items() if key not in ("type", "op", "id")}
        )
    state["armada_widgets"] = sorted(widgets, key=_scope_order)
