"""A person's own view of a plugin's widget (design/r9/plugins/ROUND2.md,
section D), over zerver/lib/armada_widget_preferences.py."""

from typing import Annotated, Literal

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json, StringConstraints

from zerver.lib.armada_widget_preferences import (
    access_widget,
    check_widget_preference,
    remove_widget_preference,
    update_widget_preference,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import UserProfile

WidgetKey = Annotated[str, StringConstraints(max_length=32)]


@typed_endpoint
def patch_armada_widget_preference(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    widget: PathOnly[WidgetKey],
    shown: Json[bool] | None = None,
    position: Json[int] | None = None,
    label: str | None = None,
    rest_figure: str | None = None,
    filters: Json[dict[str, object]] | None = None,
    alerts: Json[dict[str, object]] | None = None,
    in_column: Json[bool] | None = None,
    reset: Json[list[Literal["shown", "position", "in_column"]]] | None = None,
    seen: Json[bool] | None = None,
) -> HttpResponse:
    """Change the fields given; a field left out stays as it is. `reset`
    puts the named fields back to the team's; "" and {} do the same for
    the label, the figure, the filters and the alerts. `seen` is true once
    the user opens the widget, on any device, and false to make it new to
    them again."""
    install, declaration = access_widget(user_profile, install_id, widget)
    given = {
        "shown": shown,
        "position": position,
        "label": label,
        "rest_figure": rest_figure,
        "filters": filters,
        "alerts": alerts,
        "in_column": in_column,
    }
    changes: dict[str, object] = {
        field: value for field, value in given.items() if value is not None
    }
    for field in reset or []:
        if field in changes:
            raise JsonableError(_("{field} is both given and reset.").format(field=field))
        changes[field] = None
    if not changes and seen is None:
        raise JsonableError(_("Nothing to change."))
    update_widget_preference(
        user_profile, install, widget, check_widget_preference(declaration, changes), seen=seen
    )
    return json_success(request)


@typed_endpoint
def delete_armada_widget_preference(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    widget: PathOnly[WidgetKey],
) -> HttpResponse:
    """Back to the team's view of the widget, every field."""
    install, _declaration = access_widget(user_profile, install_id, widget)
    remove_widget_preference(user_profile, install, widget)
    return json_success(request)
