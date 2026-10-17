"""A person's own view of a plugin's widgets (design/r9/plugins/ROUND2.md,
sections D and G; package PL13).

The "Mine" tier: whether a widget is in my nav and where, my label for it,
which of its declared figures stands at rest, which rows its declared
facets keep, which of its declared alerts mark my nav, and whether its list
stands in my column; with it, whether I have opened the widget yet, on any
device, which decides whether my nav draws it as new (decision 9). None of
it needs code and none of it reaches anyone else: it is one
PluginWidgetPreference row a person and widget, sent to that person's
clients alone as armada_widget_preference events and in their /register
snapshot (zerver/lib/armada_plugin_events.py). The widget's document stays
one and shared; the client computes my view from it and from my row, and a
plugin never sees a preference.

Only what the widget's manifest declares can be chosen: a figure from its
options' figures, a facet from its facets, an alert from its alerts, within
the alert's bounds. Personal changes are never in an install's Recent.

The agent's tool, armada_widget_preference (zerver/lib/armada_plugin_tools.py,
ROUND2.md section D), passes the asker's own row through access_widget,
check_widget_preference and update_widget_preference, which are the whole
of what this route does.
"""

from typing import Any

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.armada_plugin_events import (
    send_widget_preference_event,
    send_widget_preference_removal,
)
from zerver.lib.armada_plugin_validation import MARKUP
from zerver.lib.armada_plugins import access_plugin_install
from zerver.lib.exceptions import JsonableError
from zerver.models import PluginInstall, PluginWidgetPreference, UserProfile

LABEL_MAX_LENGTH = 24
FACET_VALUE_MAX_LENGTH = 80
# The most places a widget group could need: a team's widgets, as many as
# anyone would keep in sight.
POSITION_MAX = 999


def access_widget(
    user: UserProfile, install_id: int, widget: str
) -> tuple[PluginInstall, dict[str, Any]]:
    """The install, as any route a person uses reaches it, and its declaration of
    the widget, which must be one that shows in a nav or a column."""
    install = access_plugin_install(user, install_id)
    if install.state == PluginInstall.REMOVED:
        raise JsonableError(_("{name} is removed.").format(name=install.name))
    for declaration in install.manifest["provides"].get("widgets", []):
        if declaration["key"] == widget and (
            "nav" in declaration["in"] or "column" in declaration["in"]
        ):
            return install, declaration
    raise JsonableError(
        _("{name} has no widget named {key} in a nav or a column.").format(
            name=install.name, key=widget
        )
    )


def _declared(declaration: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {option["key"]: option for option in declaration.get("options", {}).get(kind, [])}


def _check_label(label: str) -> str:
    label = label.strip()
    if len(label) > LABEL_MAX_LENGTH or MARKUP.search(label) is not None:
        raise JsonableError(
            _("A widget's label is plain words, at most {count} characters.").format(
                count=LABEL_MAX_LENGTH
            )
        )
    return label


def _check_filters(declaration: dict[str, Any], filters: dict[str, object]) -> dict[str, object]:
    facets = _declared(declaration, "facets")
    for key, value in filters.items():
        if key not in facets:
            raise JsonableError(
                _("{label} has no facet named {key}.").format(label=declaration["label"], key=key)
            )
        if not (
            isinstance(value, bool | int | float)
            or (isinstance(value, str) and len(value) <= FACET_VALUE_MAX_LENGTH)
        ):
            raise JsonableError(_("A facet's value is a word, a number or true or false."))
    return filters


def _check_alerts(declaration: dict[str, Any], alerts: dict[str, object]) -> dict[str, object]:
    declared = _declared(declaration, "alerts")
    for key, value in alerts.items():
        alert = declared.get(key)
        if alert is None:
            raise JsonableError(
                _("{label} has no alert named {key}.").format(label=declaration["label"], key=key)
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or value < alert.get("min", value)
            or value > alert.get("max", value)
        ):
            raise JsonableError(
                _("{title} takes a number from {min} to {max}.").format(
                    title=alert["title"], min=alert.get("min", "any"), max=alert.get("max", "any")
                )
            )
    return alerts


def check_widget_preference(declaration: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """The fields a person may set, each checked against what the widget's
    manifest declares; a field left out is not changed."""
    checked: dict[str, Any] = {}
    if "shown" in changes:
        if changes["shown"] is not None and "nav" not in declaration["in"]:
            raise JsonableError(_("{label} is not in the nav.").format(label=declaration["label"]))
        checked["shown"] = changes["shown"]
    if "position" in changes:
        position = changes["position"]
        if position is not None and not 0 <= position <= POSITION_MAX:
            raise JsonableError(
                _("A widget's place is a number from 0 to {max}.").format(max=POSITION_MAX)
            )
        checked["position"] = position
    if "label" in changes:
        checked["label"] = _check_label(changes["label"])
    if "rest_figure" in changes:
        figure = changes["rest_figure"]
        if figure and figure not in _declared(declaration, "figures"):
            raise JsonableError(
                _("{label} has no figure named {key}.").format(
                    label=declaration["label"], key=figure
                )
            )
        checked["rest_figure"] = figure
    if "filters" in changes:
        checked["filters"] = _check_filters(declaration, changes["filters"])
    if "alerts" in changes:
        checked["alerts"] = _check_alerts(declaration, changes["alerts"])
    if "in_column" in changes:
        if changes["in_column"] is not None and "column" not in declaration["in"]:
            raise JsonableError(
                _("{label} is not in the column.").format(label=declaration["label"])
            )
        checked["in_column"] = changes["in_column"]
    return checked


def update_widget_preference(
    user: UserProfile,
    install: PluginInstall,
    widget: str,
    changes: dict[str, Any],
    *,
    seen: bool | None = None,
) -> PluginWidgetPreference:
    """Set the fields given of this person's view of the widget; the others
    stay as they were (the team's, for a row made now). `seen` says they
    have opened it (the first time is kept) or makes it new to them again."""
    with transaction.atomic(durable=True):
        preference, _created = PluginWidgetPreference.objects.select_for_update(
            no_key=True
        ).get_or_create(user=user, install=install, widget=widget)
        for field, value in changes.items():
            setattr(preference, field, value)
        if seen is True and preference.seen_at is None:
            preference.seen_at = timezone_now()
        elif seen is False:
            preference.seen_at = None
        preference.date_updated = timezone_now()
        preference.save()
        send_widget_preference_event(install, preference)
    return preference


def _teams_view() -> dict[str, object]:
    """Each field of a person's view, at the value that means the team's."""
    return {
        "shown": None,
        "position": None,
        "label": "",
        "rest_figure": "",
        "filters": {},
        "alerts": {},
        "in_column": None,
    }


def remove_widget_preference(user: UserProfile, install: PluginInstall, widget: str) -> None:
    """Back to the team's view of the widget, every field. Whether they have
    opened it is not part of their view, and is kept."""
    with transaction.atomic(durable=True):
        preference = (
            PluginWidgetPreference.objects.select_for_update(no_key=True)
            .filter(user=user, install=install, widget=widget)
            .first()
        )
        if preference is None:
            return
        if preference.seen_at is None:
            preference.delete()
            send_widget_preference_removal(install, user.id, widget)
            return
        for field, value in _teams_view().items():
            setattr(preference, field, value)
        preference.date_updated = timezone_now()
        preference.save()
        send_widget_preference_event(install, preference)
