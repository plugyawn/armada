"""Plugins (design/r9/plugins), over zerver/lib/armada_plugins.py and
zerver/lib/armada_widgets.py. Every route a person uses reaches an install
through access_plugin_install, which looks only in the person's realm; a
plugin's bot pushes only to its own install."""

import orjson
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_post_parameters
from pydantic import Json

from zerver.lib.armada_plugin_mcp import (
    ARMADA_PLUGIN_MCP_SESSION_HEADER,
    ARMADA_PLUGIN_MCP_VERSION_HEADER,
    access_plugin_tools,
    end_mcp_session,
    forward_mcp_message,
    list_plugin_tools,
)
from zerver.lib.armada_plugin_runs import start_plugin_run
from zerver.lib.armada_plugins import (
    access_plugin_install,
    add_plugin_link,
    install_plugin,
    list_plugin_installs,
    plugin_recent,
    remove_plugin,
    remove_plugin_link,
    restore_plugin,
    retry_plugin,
    serialize_plugin_installs,
    update_plugin,
)
from zerver.lib.armada_widgets import push_widget, run_widget_action
from zerver.lib.exceptions import JsonableError
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
def post_armada_plugin_retry(
    request: HttpRequest, user_profile: UserProfile, *, install_id: PathOnly[int]
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    retry_plugin(user_profile, install)
    return json_success(request, data={"plugin": serialize_plugin_installs([install])[0]})


@typed_endpoint
def get_armada_plugin_recent(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    limit: Json[int] = 50,
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    return json_success(request, data={"recent": plugin_recent(user_profile, install, limit=limit)})


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


@typed_endpoint
def post_armada_widget(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    widget: str,
    link_id: Json[int] | None = None,
    item: str = "",
    document: Json[dict[str, object]] | None = None,
    data: Json[dict[str, object]] | None = None,
) -> HttpResponse:
    state = push_widget(
        user_profile, widget=widget, link_id=link_id, item=item, document=document, data=data
    )
    return json_success(request, data={"version": state.version})


@typed_endpoint
def post_armada_plugin_action(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    install_id: PathOnly[int],
    widget: str,
    link_id: Json[int] | None = None,
    item: str = "",
    row: str | None = None,
    index: Json[int] | None = None,
    confirmed: Json[bool] = False,
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    result = run_widget_action(
        user_profile,
        install,
        widget=widget,
        link_id=link_id,
        item=item,
        row=row,
        index=index,
        confirmed=confirmed,
    )
    return json_success(request, data=result)


@typed_endpoint
def get_armada_plugin_tools(
    request: HttpRequest, user_profile: UserProfile, *, install_id: PathOnly[int]
) -> HttpResponse:
    install = access_plugin_install(user_profile, install_id)
    return json_success(request, data={"tools": list_plugin_tools(install)})


@typed_endpoint
def post_armada_plugin_run(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_id: Json[int],
    topic: str,
    brief: str,
) -> HttpResponse:
    message_id = start_plugin_run(user_profile, stream_id=stream_id, topic=topic, brief=brief)
    return json_success(request, data={"message_id": message_id})


def _mcp_headers(request: HttpRequest) -> dict[str, str]:
    return {
        name: value
        for name in (ARMADA_PLUGIN_MCP_SESSION_HEADER, ARMADA_PLUGIN_MCP_VERSION_HEADER)
        if (value := request.headers.get(name))
    }


@typed_endpoint
def post_armada_plugin_mcp(
    request: HttpRequest, user_profile: UserProfile, *, install_id: PathOnly[int]
) -> HttpResponse:
    """One message of MCP's Streamable HTTP transport, for an agent off
    this server (zerver/lib/armada_plugin_mcp.py). It speaks JSON-RPC,
    not this API's JSON, so it is not in the API's documentation."""
    install = access_plugin_tools(user_profile, install_id)
    try:
        message = orjson.loads(request.body)
    except orjson.JSONDecodeError as error:
        raise JsonableError(_("An MCP message is one JSON object.")) from error
    reply = forward_mcp_message(user_profile, install, message, headers=_mcp_headers(request))
    response = HttpResponse(
        b"" if reply.message is None else orjson.dumps(reply.message),
        status=reply.status,
        content_type="application/json",
    )
    if reply.session_id:
        response[ARMADA_PLUGIN_MCP_SESSION_HEADER] = reply.session_id
    return response


@typed_endpoint
def delete_armada_plugin_mcp(
    request: HttpRequest, user_profile: UserProfile, *, install_id: PathOnly[int]
) -> HttpResponse:
    install = access_plugin_tools(user_profile, install_id)
    return HttpResponse(status=end_mcp_session(install, headers=_mcp_headers(request)))
