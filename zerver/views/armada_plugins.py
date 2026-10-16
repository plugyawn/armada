"""Plugins (design/r9/plugins), over zerver/lib/armada_plugins.py. Every
route reaches an install through access_plugin_install, which looks only in
the person's realm."""

from django.http import HttpRequest, HttpResponse
from django.views.decorators.debug import sensitive_post_parameters
from pydantic import Json

from zerver.lib.armada_plugins import (
    access_plugin_install,
    add_plugin_link,
    install_plugin,
    list_plugin_installs,
    remove_plugin,
    remove_plugin_link,
    restore_plugin,
    serialize_plugin_installs,
    update_plugin,
)
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint, typed_endpoint_without_parameters
from zerver.models import UserProfile


@typed_endpoint_without_parameters
def get_armada_plugins(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(request, data={"plugins": list_plugin_installs(user_profile)})


@sensitive_post_parameters("secrets")
@typed_endpoint
def post_armada_plugin(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    manifest: Json[dict[str, object]],
    source_kind: str = "github",
    source: str = "",
    repository_id: Json[int] | None = None,
    path: str = "",
    sha: str = "",
    settings: Json[dict[str, object]] | None = None,
    secrets: Json[dict[str, str]] | None = None,
    widget_defaults: Json[dict[str, object]] | None = None,
) -> HttpResponse:
    install = install_plugin(
        user_profile,
        manifest=manifest,
        source_kind=source_kind,
        source=source,
        repository_id=repository_id,
        path=path,
        sha=sha,
        plugin_settings=settings,
        secrets=secrets,
        widget_defaults=widget_defaults,
    )
    return json_success(request, data={"plugin": serialize_plugin_installs([install])[0]})


@sensitive_post_parameters("secrets")
@typed_endpoint
def patch_armada_plugin(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    settings: Json[dict[str, object]] | None = None,
    secrets: Json[dict[str, str]] | None = None,
    disabled: Json[bool] | None = None,
    widget_defaults: Json[dict[str, object]] | None = None,
    disabled_tools: Json[list[str]] | None = None,
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    update_plugin(
        user_profile,
        install,
        plugin_settings=settings,
        secrets=secrets,
        disabled=disabled,
        widget_defaults=widget_defaults,
        disabled_tools=disabled_tools,
    )
    return json_success(request, data={"plugin": serialize_plugin_installs([install])[0]})


@typed_endpoint
def delete_armada_plugin(
    request: HttpRequest, user_profile: UserProfile, *, install_id: PathOnly[int]
) -> HttpResponse:
    remove_plugin(user_profile, access_plugin_install(user_profile, install_id))
    return json_success(request)


@typed_endpoint
def post_armada_plugin_restore(
    request: HttpRequest, user_profile: UserProfile, *, install_id: PathOnly[int]
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    restore_plugin(user_profile, install)
    return json_success(request, data={"plugin": serialize_plugin_installs([install])[0]})


@typed_endpoint
def post_armada_plugin_link(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    kind: str,
    value: str,
    stream_id: Json[int],
    topic: str | None = None,
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    link = add_plugin_link(
        user_profile, install, kind=kind, value=value, stream_id=stream_id, topic=topic
    )
    return json_success(request, data={"id": link.id})


@typed_endpoint
def delete_armada_plugin_link(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    link_id: PathOnly[int],
) -> HttpResponse:
    remove_plugin_link(user_profile, access_plugin_install(user_profile, install_id), link_id)
    return json_success(request)
