# INTERIM BETA: the second interface at /beta/, served until the
# rebuild's conversation lands; then delete this file with
# web/src/armada/beta/ (see web/src/armada/beta/README.md for the
# whole list).
import os
from collections.abc import Callable
from typing import Annotated

import orjson
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe
from pydantic import Json

from zerver.decorator import (
    logged_in_and_active,
    user_passes_test,
    zulip_login_required,
    zulip_otp_required_if_logged_in,
)
from zerver.lib.armada import ARMADA_QUESTION_TIMEOUT_SECONDS
from zerver.lib.armada_identity import armada_bot_user_ids_for_realm
from zerver.lib.armada_worker import armada_user_is_trusted
from zerver.lib.partner import (
    armada_sprite_dir,
    choose_partner,
    evolve_partner,
    load_pack,
    partners_state,
    put_off_choice,
)
from zerver.lib.response import json_success
from zerver.lib.stream_subscription import get_subscribed_stream_ids_for_user
from zerver.lib.typed_endpoint import (
    ApiParamConfig,
    typed_endpoint,
    typed_endpoint_without_parameters,
)
from zerver.lib.user_groups import (
    get_group_setting_value_for_register_api,
    get_members_and_subgroups_of_groups,
)
from zerver.models import AgentRun, NamedUserGroup, Realm, Stream, UserProfile

# The live runs the page is told about. armada/runs takes 20 ids a
# request, so this is a handful of requests at most; a realm with more
# live runs than this has Home show the newest.
ARMADA_BETA_LIVE_RUN_LIMIT = 100

# Sprites and the pack's species never change under one name (a new pack
# is a new ?v= on species.json), so a browser keeps them for a year.
ARMADA_BETA_SPRITE_CACHE = "private, max-age=31536000, immutable"


def live_run_ids_for_user(user_profile: UserProfile) -> list[int]:
    """The shared runs that are queued or running in the channels this
    person is subscribed to, newest first. Home reads these (through
    armada/runs?run_ids=...) instead of asking every worktree for its
    runs. A run is placed in a channel the way
    check_can_access_armada_worker_run places it, so every id here is one
    armada/runs will let this person read."""
    subscribed = {
        name.lower()
        for name in Stream.objects.filter(
            id__in=get_subscribed_stream_ids_for_user(user_profile)
        ).values_list("name", flat=True)
    }
    runs = (
        AgentRun.objects.select_related("workspace_node")
        .filter(
            realm_id=user_profile.realm_id,
            session_kind=AgentRun.SHARED,
            status__in=[AgentRun.QUEUED, AgentRun.RUNNING],
        )
        .order_by("-date_created", "-id")
    )
    ids = []
    for run in runs:
        stream_name = (
            run.workspace_node.stream_name
            if run.workspace_node is not None and run.workspace_node.stream_name
            else run.stream_name
        )
        if stream_name.lower() in subscribed:
            ids.append(run.id)
            if len(ids) == ARMADA_BETA_LIVE_RUN_LIMIT:
                break
    return ids


def edit_and_delete_rules(realm: Realm) -> dict[str, object]:
    """The realm's rules for editing and deleting a message, written as
    register's realm bundle writes them: a group setting is a named
    group's id, or an anonymous group's members and subgroups. The page
    reads these five of the bundle's hundred keys, so it does not
    register for the bundle; the realm event keeps them current."""
    group_ids = [realm.can_delete_any_message_group_id, realm.can_delete_own_message_group_id]
    named = set(NamedUserGroup.objects.filter(id__in=group_ids).values_list("id", flat=True))
    anonymous = get_members_and_subgroups_of_groups([i for i in group_ids if i not in named])
    return {
        "allow_message_editing": realm.allow_message_editing,
        "message_content_edit_limit_seconds": realm.message_content_edit_limit_seconds,
        "message_content_delete_limit_seconds": realm.message_content_delete_limit_seconds,
        "can_delete_any_message_group": get_group_setting_value_for_register_api(
            realm.can_delete_any_message_group_id, anonymous
        ),
        "can_delete_own_message_group": get_group_setting_value_for_register_api(
            realm.can_delete_own_message_group_id, anonymous
        ),
    }


def link_preview_settings(realm: Realm) -> dict[str, bool]:
    """Which links and pictures the server previews, as register's realm
    bundle writes it: my echo keeps a place for a link's card by the
    renderer's rule before the server has rendered the message."""
    return {
        "realm_inline_url_embed_preview": realm.inline_url_embed_preview,
        "realm_inline_image_preview": realm.inline_image_preview,
        "server_inline_url_embed_preview": settings.INLINE_URL_EMBED_PREVIEW,
        "server_inline_image_preview": settings.INLINE_IMAGE_PREVIEW,
    }


def armada_beta_page(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    """The new design on this server's real data, for the signed-in
    person: what / serves them (zerver/views/home.py) and /beta/ too. It
    registers an event queue over the session (/json/register) like the
    old interface, and every write goes through /json/ with the session's
    CSRF token. The page is given two facts it cannot ask for: how long a
    run waits for an answer to its question, which runs are live now, and
    whether a mention of the agent by this person can start a run (the
    embedded bots are on and the person may drive runs: the rules
    handle_message and start_blocked_reason apply, so the field offers
    the agent only where a mention of it does something); the version of
    the partner pack, so that its species.json can be kept for good under
    each version; and the little it would otherwise register for whole
    bundles to learn: the agent's user ids (from realm_bot), the rules
    for editing and deleting, which links the server previews, and how
    often to report presence and when a person counts as gone (from
    realm)."""
    pack = load_pack()
    return render(
        request,
        "zerver/armada_beta.html",
        context={
            "doc_root_title": "Armada",
            "question_timeout_seconds": ARMADA_QUESTION_TIMEOUT_SECONDS,
            "live_run_ids": orjson.dumps(live_run_ids_for_user(user_profile)).decode(),
            "may_start_runs": settings.EMBEDDED_BOTS_ENABLED
            and armada_user_is_trusted(user_profile),
            "read_receipts": user_profile.realm.enable_read_receipts,
            "sprite_version": pack.version if pack is not None else "",
            "agent_user_ids": orjson.dumps(
                armada_bot_user_ids_for_realm(user_profile.realm)
            ).decode(),
            "edit_and_delete": orjson.dumps(edit_and_delete_rules(user_profile.realm)).decode(),
            "link_previews": orjson.dumps(link_preview_settings(user_profile.realm)).decode(),
            "presence_ping_interval_seconds": settings.PRESENCE_PING_INTERVAL_SECS,
            "presence_offline_threshold_seconds": settings.OFFLINE_THRESHOLD_SECS,
        },
    )


@zulip_login_required
def armada_beta(request: HttpRequest) -> HttpResponse:
    """The address the new interface had before / served it, kept so
    that links to it keep working."""
    user_profile = request.user
    assert isinstance(user_profile, UserProfile)
    return armada_beta_page(request, user_profile)


def sprite_login_required(
    view_func: Callable[[HttpRequest, str], HttpResponse],
) -> Callable[[HttpRequest, str], HttpResponse]:
    """zulip_login_required's sign-in and two-factor checks, without its
    rate limit. Sprites are static files, and the picker's first screen
    asks for a hundred at once: counted against the person's API budget
    (200 requests a minute, which zulip_login_required charges every
    browser view), they would starve the page's own event polling."""
    return user_passes_test(logged_in_and_active, login_url=settings.HOME_NOT_LOGGED_IN)(
        zulip_otp_required_if_logged_in(login_url=settings.HOME_NOT_LOGGED_IN)(view_func)
    )


@require_safe
@sprite_login_required
def armada_beta_sprite(request: HttpRequest, path: str) -> HttpResponse:
    """A file of the git-ignored sprite directory, to a signed-in person
    of this realm only (a signed-out request is sent to sign in, like
    /beta/ itself): a sprite (any .png) or the pack's species.json.
    Nothing outside the directory is served: the path is resolved, links
    included, and must stay inside it. Anything else, and any file that
    is not on this server, is a 404, and so is any path the file system
    refuses (a NUL byte, a name too long, a file used as a directory)."""
    if "\0" in path:
        raise Http404
    root = os.path.realpath(armada_sprite_dir())
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(root + os.sep):
        raise Http404
    relative = os.path.relpath(full, root)
    if relative.endswith(".png"):
        content_type = "image/png"
    elif relative == "species.json":
        content_type = "application/json"
    else:
        raise Http404
    try:
        with open(full, "rb") as f:
            content = f.read()
    except OSError:
        raise Http404
    response = HttpResponse(content, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = ARMADA_BETA_SPRITE_CACHE
    return response


# Partners (zerver/lib/partner.py), as the beta reads and changes them.
# The routes are the r4 partner study's (/json/armada/partner...); they
# are intentionally undocumented, like the beta's other armada/ routes.


@typed_endpoint_without_parameters
def get_armada_partners(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    """Every person's partner, the agent's, and mine in full, as stored;
    my experience is folded in the background when it is due."""
    return json_success(request, data=partners_state(user_profile))


@typed_endpoint
def post_armada_partner(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    species: str | None = None,
    picks: Annotated[Json[list[str]] | None, ApiParamConfig("into")] = None,
    later: Json[bool] = False,
) -> HttpResponse:
    """Choose or change my partner (species, with picks for the branches
    my level passes on the way), or put the choice off (later)."""
    if later:
        put_off_choice(user_profile)
    elif species is not None:
        choose_partner(user_profile, species, picks or [])
    return json_success(request, data=partners_state(user_profile))


@typed_endpoint
def post_armada_partner_evolve(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    into: str | None = None,
    hold: Json[bool] = False,
) -> HttpResponse:
    """Take the change my level allows, or hold my partner as it is."""
    evolve_partner(user_profile, into, hold)
    return json_success(request, data=partners_state(user_profile))
