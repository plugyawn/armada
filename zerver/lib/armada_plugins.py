"""Plugins (design/r9/plugins): install, settings, secrets, links, remove,
Undo and the purge.

A plugin is a repository with armada-plugin.json, installed at a pinned
commit. Each install has its own bot, which is how the plugin speaks to
Armada: it posts, reads its events and pushes its widgets through Zulip's
REST and event API with the bot's key. Everything an install registers
hangs from its PluginInstall row (its secrets, links and widget states), so
removing it takes everything back. Its process is the plugin host's
(zerver/lib/armada_plugin_host.py).

Every member may install, link and remove a plugin (statement 43); guests
and bots may not. What each did is kept in RealmAuditLog, with the
install's bot as modified_user: that is the install's Recent list.

Widgets made on request (ROUND2.md) come in through the same functions: a
draft holds its id and is its maker's alone until it is installed; an
install records who asked the agent for it and where; and a new widget
shows in everyone's nav while the team has fewer than six that do, and is
offered in "+" after that.

Secrets are encrypted with Fernet under the server's plugin key
(armada_plugin_secrets_key in zulip-secrets.conf) and only the plugin host
decrypts them, into the plugin's own environment
(armada_plugin_secret_environment). People see whether one is set, who set
it and when, and the last four characters of a long one; no response,
audit record or log line carries one.
"""

import base64
import hashlib
import hmac
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.headerregistry import Address
from secrets import token_hex
from typing import Any

import requests
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.actions.create_user import do_create_user, do_reactivate_user
from zerver.actions.streams import bulk_add_subscriptions, bulk_remove_subscriptions
from zerver.actions.user_settings import do_change_full_name, do_regenerate_api_key
from zerver.actions.users import do_deactivate_user
from zerver.lib.armada_plugin_events import send_armada_plugin_event, send_armada_widget_event
from zerver.lib.armada_plugin_validation import (
    MARKUP,
    ArmadaPluginContractError,
    canonicalize_plugin_manifest,
    canonicalize_plugin_settings,
)
from zerver.lib.exceptions import JsonableError, ResourceNotFoundError
from zerver.lib.outgoing_http import OutgoingSession
from zerver.lib.stream_subscription import get_subscribed_stream_ids_for_user
from zerver.lib.streams import access_stream_by_id
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import (
    AgentRun,
    PluginInstall,
    PluginLink,
    PluginSecret,
    PluginWidgetState,
    Realm,
    RealmAuditLog,
    Repository,
    Stream,
    UserProfile,
)
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.realm_audit_logs import AuditLogEventType

# Remove can be undone for this long; then the purge deletes the install.
ARMADA_PLUGIN_UNDO_DAYS = 7
ARMADA_PLUGIN_SECRET_LIMIT = 4096
# A secret at least this long shows its last four characters.
ARMADA_PLUGIN_SECRET_HINT_LENGTH = 16
ARMADA_PLUGIN_DISABLED_TOOLS_LIMIT = 64
# The team's widgets that show in everyone's nav before new ones are
# offered in "+" instead (statement 45).
ARMADA_PLUGIN_SHOWN_WIDGET_LIMIT = 6
# Recent's lines, by the word the API gives each.
ARMADA_PLUGIN_RECENT_TYPES: dict[int, str] = {
    AuditLogEventType.PLUGIN_INSTALLED: "installed",
    AuditLogEventType.PLUGIN_SETTINGS_CHANGED: "settings_changed",
    AuditLogEventType.PLUGIN_LINKED: "linked",
    AuditLogEventType.PLUGIN_UNLINKED: "unlinked",
    AuditLogEventType.PLUGIN_REMOVED: "removed",
    AuditLogEventType.PLUGIN_RESTORED: "restored",
    AuditLogEventType.PLUGIN_MADE: "made",
    AuditLogEventType.PLUGIN_UPDATED: "updated",
    AuditLogEventType.PLUGIN_TOOL_CALLED: "tool_called",
    AuditLogEventType.PLUGIN_STARTED: "started",
}
ARMADA_PLUGIN_RECENT_LIMIT = 100
GITHUB_SOURCE = re.compile(r"^github\.com/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
SOURCE_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class ArmadaPluginKeyError(JsonableError):
    pass


@dataclass(frozen=True)
class PluginSource:
    """Where an install's code comes from (PluginInstall.source_kind)."""

    kind: str
    source: str = ""
    repository: Repository | None = None
    path: str = ""
    sha: str = ""


@dataclass(frozen=True)
class PluginOrigin:
    """Who asked the agent to make a plugin, in which run, and where."""

    made_by: UserProfile
    run: AgentRun | None = None
    stream: Stream | None = None
    topic: str | None = None
    message_id: int | None = None


def check_plugin_member(user: UserProfile) -> None:
    """Plugins are the team's: every member may see, install, link and
    remove them. A guest sees their widgets and nothing more, and a bot,
    a plugin's own included, cannot change them."""
    if user.is_bot:
        raise JsonableError(_("Plugins are installed, linked and removed by people, not bots."))
    if user.is_guest:
        raise JsonableError(_("Guests cannot see, install, link or remove plugins."))


def _sees(user: UserProfile, install: PluginInstall) -> bool:
    """A draft is its maker's alone; every other install is the team's."""
    return install.state != PluginInstall.DRAFT or user.id in (
        install.made_by_id,
        install.installed_by_id,
    )


def access_plugin_install(user: UserProfile, install_id: int) -> PluginInstall:
    check_plugin_member(user)
    install = (
        PluginInstall.objects.select_related("bot", "realm")
        .filter(id=install_id, realm=user.realm)
        .first()
    )
    if install is None or not _sees(user, install):
        raise ResourceNotFoundError(_("No such plugin"))
    return install


def access_plugin_by_plugin_id(user: UserProfile, plugin_id: str) -> PluginInstall:
    """The install of plugin_id in user's realm, as access_plugin_install
    finds it by its row's id: what the agent names a plugin by."""
    check_plugin_member(user)
    install = (
        PluginInstall.objects.select_related("bot", "realm")
        .filter(realm=user.realm, plugin_id=plugin_id)
        .exclude(state=PluginInstall.REMOVED)
        .first()
    )
    if install is None or not _sees(user, install):
        raise ResourceNotFoundError(_("No such plugin"))
    return install


def plugin_fields_without_values(install: PluginInstall, names: list[str]) -> list[str]:
    """Which of names, settings or secrets, have no value yet: a setting
    absent or empty, a secret never stored. Read afresh each time, for a
    run that waits on them."""
    install.refresh_from_db(fields=["settings"])
    secrets = install.manifest.get("secrets", {})
    stored = set(
        PluginSecret.objects.filter(install=install, name__in=names).values_list("name", flat=True)
    )
    return [
        name
        for name in names
        if (name not in stored if name in secrets else install.settings.get(name) in (None, "", []))
    ]


def check_not_removed(install: PluginInstall) -> None:
    if install.state == PluginInstall.REMOVED:
        raise JsonableError(
            _("{name} is removed; undo the removal first.").format(name=install.name)
        )


def _plugin_keys() -> list[bytes]:
    """The server's plugin keys, the current one first. A development
    server's secrets file may predate the key, so it derives one."""
    configured = settings.ARMADA_PLUGIN_SECRETS_KEY
    if configured:
        return [key.encode() for key in configured.split()]
    if settings.DEVELOPMENT:
        digest = hashlib.sha256(b"armada-plugin-secrets:" + settings.SECRET_KEY.encode()).digest()
        return [base64.urlsafe_b64encode(digest)]
    raise ArmadaPluginKeyError(
        _(
            "This server has no armada_plugin_secrets_key in zulip-secrets.conf,"
            " so it cannot keep a plugin's secrets or give it a token."
        )
    )


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(key) for key in _plugin_keys()])


def armada_plugin_token(install: PluginInstall) -> str:
    """The bearer token the plugin is started with, and that Codex and
    Armada present to its MCP endpoint. It is derived from the install's
    salt and the server's current plugin key, so it is never stored.
    Rotating the key (a new key first in armada_plugin_secrets_key) gives
    every install a new token at once: the host restarts each plugin with
    its new one, and the old ones stop working. Secrets are unaffected,
    since any listed key decrypts them."""
    key = hashlib.sha256(b"armada-plugin-token:" + _plugin_keys()[0]).digest()
    message = f"{install.id}:{install.token_salt}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def armada_plugin_secret_environment(install: PluginInstall) -> dict[str, str]:
    """The install's secrets, decrypted, under the environment names its
    manifest gives them. Only the plugin host calls this, to start the
    plugin's own process."""
    declared = install.manifest.get("secrets", {})
    fernet = _fernet()
    return {
        declared[secret.name]["env"]: fernet.decrypt(secret.ciphertext.encode()).decode()
        for secret in PluginSecret.objects.filter(install=install)
        if secret.name in declared
    }


def check_plugin_source(
    user: UserProfile,
    manifest: dict[str, Any],
    *,
    kind: str,
    source: str = "",
    repository_id: int | None = None,
    path: str = "",
    sha: str = "",
) -> PluginSource:
    """Where the plugin's code is: a repository on GitHub, or on this
    server, at a commit and a directory in it; or nowhere, for a remote
    plugin, whose manifest names its service."""
    if kind not in PluginInstall.SOURCE_KINDS:
        raise JsonableError(_("A plugin comes from github, local or remote."))
    path = path.strip().strip("/")
    if path and (
        not SOURCE_PATH.match(path) or any(part in (".", "..") for part in path.split("/"))
    ):
        raise JsonableError(
            _("A plugin's path is a directory in its repository, as plugins/eval-queue.")
        )
    if kind == PluginInstall.REMOTE:
        if "remote" not in manifest:
            raise JsonableError(_("A remote plugin's manifest names its service, in remote."))
        if repository_id is not None or path or sha:
            raise JsonableError(_("A remote plugin has no repository, path or commit here."))
        return PluginSource(kind, source=manifest["remote"]["url"])
    if "run" not in manifest:
        raise JsonableError(_("A plugin whose code is here says how to run it, in run."))
    if not COMMIT_SHA.match(sha):
        raise JsonableError(_("A plugin is installed at a commit: 40 hexadecimal digits."))
    if kind == PluginInstall.GITHUB:
        source = source.strip().removeprefix("https://").removesuffix("/").removesuffix(".git")
        if repository_id is not None or not GITHUB_SOURCE.match(source):
            raise JsonableError(_("A plugin is installed from github.com/<owner>/<repository>."))
        return PluginSource(kind, source=source, path=path, sha=sha)
    if repository_id is not None:
        repository = Repository.objects.filter(id=repository_id, realm=user.realm).first()
        if repository is None or not repository.repo_cache_path:
            raise JsonableError(_("No such repository"))
        access_stream_by_id(user, repository.stream_id)
        return PluginSource(kind, repository=repository, path=path, sha=sha)
    if settings.ARMADA_PLUGIN_LOCAL_SOURCES and os.path.isabs(source):
        return PluginSource(kind, source=os.path.normpath(source), path=path, sha=sha)
    raise JsonableError(_("A local plugin is installed from one of this server's repositories."))


def check_plugin_manifest(manifest: object) -> dict[str, Any]:
    try:
        return canonicalize_plugin_manifest(manifest)
    except ArmadaPluginContractError as error:
        raise JsonableError(
            _("The plugin's manifest is not valid: {error}").format(error=str(error))
        )


def _check_settings(manifest: dict[str, Any], values: object) -> tuple[dict[str, Any], list[str]]:
    try:
        return canonicalize_plugin_settings(manifest, values)
    except ArmadaPluginContractError as error:
        raise JsonableError(_("The settings are not valid: {error}").format(error=str(error)))


def _check_secrets(manifest: dict[str, Any], values: dict[str, str]) -> None:
    declared = manifest.get("secrets", {})
    for name, value in values.items():
        if name not in declared:
            raise JsonableError(_("The plugin asks for no secret named {name}.").format(name=name))
        if not 0 < len(value) <= ARMADA_PLUGIN_SECRET_LIMIT:
            raise JsonableError(
                _("A secret is between 1 and {limit} characters.").format(
                    limit=ARMADA_PLUGIN_SECRET_LIMIT
                )
            )


def _store_secrets(install: PluginInstall, values: dict[str, str], user: UserProfile) -> None:
    if not values:
        return
    fernet = _fernet()
    for name, value in values.items():
        PluginSecret.objects.update_or_create(
            install=install,
            name=name,
            defaults={
                "ciphertext": fernet.encrypt(value.encode()).decode(),
                "hint": value[-4:] if len(value) >= ARMADA_PLUGIN_SECRET_HINT_LENGTH else "",
                "set_by": user,
                "date_set": timezone_now(),
            },
        )


def _waiting_for(install: PluginInstall, missing_settings: list[str]) -> list[str]:
    stored = set(PluginSecret.objects.filter(install=install).values_list("name", flat=True))
    missing_secrets = [
        name
        for name, secret in install.manifest.get("secrets", {}).items()
        if secret.get("required") and name not in stored
    ]
    return [*missing_settings, *missing_secrets]


def _settle_state(install: PluginInstall, missing_settings: list[str]) -> None:
    """Waiting while something required is missing; otherwise the host
    starts it, and reports Running or Failed. A draft stays a draft, and
    only says what it lacks."""
    waiting_for = _waiting_for(install, missing_settings)
    if install.state == PluginInstall.DRAFT:
        install.state_detail = ", ".join(waiting_for)
    elif waiting_for:
        install.state = PluginInstall.WAITING
        install.state_detail = ", ".join(waiting_for)
    elif install.state in (PluginInstall.WAITING, PluginInstall.FAILED):
        install.state = PluginInstall.STARTING
        install.state_detail = ""
        install.failures = 0


def audit_plugin(
    install: PluginInstall,
    acting_user: UserProfile,
    event_type: AuditLogEventType,
    extra_data: dict[str, Any],
    *,
    stream_id: int | None = None,
) -> None:
    """A line in the install's Recent list: who did what to it."""
    if install.state == PluginInstall.DRAFT:
        # Recent is the team's; a draft is its maker's until it is installed.
        return
    RealmAuditLog.objects.create(
        realm_id=install.realm_id,
        acting_user=acting_user,
        modified_user_id=install.bot_id,
        modified_stream_id=stream_id,
        event_type=event_type,
        event_time=timezone_now(),
        extra_data={"install_id": install.id, "plugin": install.plugin_id, **extra_data},
    )


def plugin_recent(user: UserProfile, install: PluginInstall, *, limit: int) -> list[dict[str, Any]]:
    """Recent, newest first: who did what to the install. What a line
    records holds a secret by its name alone, and a line about a channel
    the person cannot read is left out, as that channel's links are."""
    if not 0 < limit <= ARMADA_PLUGIN_RECENT_LIMIT:
        raise JsonableError(
            _("limit is between 1 and {limit}.").format(limit=ARMADA_PLUGIN_RECENT_LIMIT)
        )
    lines = (
        RealmAuditLog.objects.filter(
            realm_id=install.realm_id,
            event_type__in=list(ARMADA_PLUGIN_RECENT_TYPES),
            modified_user_id=install.bot_id,
            extra_data__install_id=install.id,
        )
        .filter(
            Q(modified_stream__isnull=True)
            | Q(modified_stream__invite_only=False)
            | Q(modified_stream_id__in=get_subscribed_stream_ids_for_user(user))
        )
        .order_by("-event_time", "-id")[:limit]
    )
    return [
        {
            "id": line.id,
            "type": ARMADA_PLUGIN_RECENT_TYPES[line.event_type],
            "acting_user_id": line.acting_user_id,
            "time": datetime_to_timestamp(line.event_time),
            "stream_id": line.modified_stream_id,
            "detail": {
                key: value
                for key, value in line.extra_data.items()
                if key not in ("install_id", "plugin")
            },
        }
        for line in lines
    ]


def _shown_widget_count(realm: Realm, exclude_id: int | None) -> int:
    """How many of the team's widgets show in everyone's nav."""
    installs = PluginInstall.objects.filter(realm=realm, disabled=False).exclude(
        state__in=[PluginInstall.DRAFT, PluginInstall.REMOVED]
    )
    if exclude_id is not None:
        installs = installs.exclude(id=exclude_id)
    count = 0
    for widget_defaults in installs.values_list("widget_defaults", flat=True):
        count += sum(choice.get("nav") == "shown" for choice in widget_defaults.values())
    return count


def _widget_defaults(
    realm: Realm,
    manifest: dict[str, Any],
    given: dict[str, Any] | None,
    *,
    current: dict[str, Any],
    exclude_id: int | None,
) -> dict[str, Any]:
    """The team's default for each widget: what was given, else what it
    had, else the manifest's team default, shown while the team has fewer
    than six widgets in everyone's nav and offered in "+" after that."""
    widgets = {widget["key"]: widget for widget in manifest["provides"].get("widgets", [])}
    given = given or {}
    for key, choice in given.items():
        widget = widgets.get(key)
        if widget is None:
            raise JsonableError(
                _("{name} has no widget named {key}.").format(name=manifest["name"], key=key)
            )
        nav = choice.get("nav") if isinstance(choice, dict) else None
        column = choice.get("column") if isinstance(choice, dict) else None
        if (
            not isinstance(choice, dict)
            or set(choice) - {"nav", "column"}
            or nav not in (None, "shown", "offered", "off")
            or (nav in ("shown", "offered") and "nav" not in widget["in"])
            or column not in (None, True, False)
            or (column and "column" not in widget["in"])
        ):
            raise JsonableError(
                _("A widget's default is its nav (shown, offered or off) and its column.")
            )
    shown = _shown_widget_count(realm, exclude_id)
    defaults: dict[str, Any] = {}
    for key, widget in widgets.items():
        choice = {**current.get(key, {}), **given.get(key, {})}
        if "nav" not in choice:
            team = widget.get("team_default", "shown")
            if "nav" not in widget["in"]:
                choice["nav"] = "off"
            elif team == "shown" and shown >= ARMADA_PLUGIN_SHOWN_WIDGET_LIMIT:
                choice["nav"] = "offered"
            else:
                choice["nav"] = team
        choice.setdefault("column", "column" in widget["in"])
        shown += choice["nav"] == "shown"
        defaults[key] = choice
    return defaults


def _plugin_bot_email(realm: Realm, plugin_id: str) -> str:
    return Address(username=f"{plugin_id}-plugin-bot", domain=realm.get_bot_domain()).addr_spec


def _free_plugin_bot(realm: Realm, email: str) -> UserProfile | None:
    """The bot an earlier install of this plugin had, when it is free to
    be the new install's: deactivated, and no longer any install's."""
    bot = UserProfile.objects.filter(realm=realm, delivery_email__iexact=email).first()
    if bot is None:
        return None
    if not bot.is_bot or bot.is_active or PluginInstall.objects.filter(bot=bot).exists():
        raise JsonableError(
            _("{email} is taken, so the plugin cannot have its own bot.").format(email=email)
        )
    return bot


def _origin_fields(origin: PluginOrigin | None) -> dict[str, Any]:
    if origin is None:
        return {}
    return {
        "made_by": origin.made_by,
        "made_by_run": origin.run,
        "made_in_stream": origin.stream,
        "made_in_topic": origin.topic,
        "made_in_message_id": origin.message_id,
    }


def _source_fields(source: PluginSource) -> dict[str, Any]:
    return {
        "source_kind": source.kind,
        "source": source.source,
        "source_repository": source.repository,
        "source_path": source.path,
        "sha": source.sha,
    }


def check_installable(user: UserProfile, plugin_id: str) -> PluginInstall | None:
    """The install that holds the plugin's id, when installing it would
    take its place: the person's own draft, or a removal not yet purged.
    Refuses someone else's draft and an install."""
    existing = PluginInstall.objects.filter(realm=user.realm, plugin_id=plugin_id).first()
    if existing is None or existing.state == PluginInstall.REMOVED:
        return existing
    if existing.state != PluginInstall.DRAFT:
        raise JsonableError(_("{name} is already installed.").format(name=existing.name))
    if not _sees(user, existing):
        raise JsonableError(_("{name} is being made, and holds its id.").format(name=existing.name))
    return existing


def install_plugin(
    user: UserProfile,
    *,
    manifest: object,
    source_kind: str,
    source: str = "",
    repository_id: int | None = None,
    path: str = "",
    sha: str = "",
    plugin_settings: object | None = None,
    secrets: dict[str, str] | None = None,
    widget_defaults: dict[str, Any] | None = None,
    draft: bool = False,
    origin: PluginOrigin | None = None,
) -> PluginInstall:
    """Pin the commit, make the install's bot, keep its settings and
    secrets; the plugin host starts it. A plugin removed and not yet
    purged is replaced: its bot, which signed its messages, is reused. A
    draft, which holds the plugin's id while its maker builds it, becomes
    the install in place when its maker installs it, keeping its bot,
    settings and secrets."""
    check_plugin_member(user)
    manifest = check_plugin_manifest(manifest)
    pinned = check_plugin_source(
        user,
        manifest,
        kind=source_kind,
        source=source,
        repository_id=repository_id,
        path=path,
        sha=sha,
    )
    secrets = secrets or {}
    _check_secrets(manifest, secrets)
    if secrets:
        # Refused before anything is made, on a server that cannot keep them.
        _plugin_keys()
    realm = user.realm
    existing = check_installable(user, manifest["id"])
    if existing is not None and existing.state == PluginInstall.DRAFT:
        return _install_draft(
            user,
            existing,
            manifest,
            pinned,
            plugin_settings,
            secrets,
            widget_defaults,
            draft,
            origin,
        )
    values, missing_settings = _check_settings(manifest, plugin_settings or {})
    defaults = _widget_defaults(realm, manifest, widget_defaults, current={}, exclude_id=None)
    email = _plugin_bot_email(realm, manifest["id"])
    bot = existing.bot if existing is not None else _free_plugin_bot(realm, email)
    if bot is not None:
        # The earlier install's process, and anything that saw its
        # environment, held the old key.
        do_regenerate_api_key(bot, user)

    with transaction.atomic(durable=True):
        if existing is not None:
            existing.delete()
        if bot is None:
            bot = do_create_user(
                email,
                None,
                realm,
                manifest["name"],
                bot_type=UserProfile.DEFAULT_BOT,
                acting_user=user,
                add_initial_stream_subscriptions=False,
            )
        else:
            do_reactivate_user(bot, acting_user=user)
            if bot.full_name != manifest["name"]:
                do_change_full_name(bot, manifest["name"], user, notify=True)
        install = PluginInstall.objects.create(
            realm=realm,
            plugin_id=manifest["id"],
            name=manifest["name"],
            version=manifest["version"],
            manifest=manifest,
            settings=values,
            state=PluginInstall.DRAFT if draft else PluginInstall.STARTING,
            token_salt=token_hex(16),
            bot=bot,
            installed_by=user,
            widget_defaults=defaults,
            **_source_fields(pinned),
            **_origin_fields(origin),
        )
        _store_secrets(install, secrets, user)
        _settle_state(install, missing_settings)
        install.save(update_fields=["state", "state_detail"])
        if not draft:
            _audit_install(install, user, origin, secrets)
        send_armada_plugin_event(install, "add")
    return install


def _install_draft(
    user: UserProfile,
    install: PluginInstall,
    manifest: dict[str, Any],
    pinned: PluginSource,
    plugin_settings: object | None,
    secrets: dict[str, str],
    widget_defaults: dict[str, Any] | None,
    draft: bool,
    origin: PluginOrigin | None,
) -> PluginInstall:
    """A draft checked again, or installed: it keeps its bot, and its
    settings and secrets unless new ones are given."""
    kept = {
        name: value
        for name, value in install.settings.items()
        if name in manifest.get("settings", {}).get("properties", {})
    }
    values, missing_settings = _check_settings(
        manifest, kept if plugin_settings is None else plugin_settings
    )
    defaults = _widget_defaults(
        install.realm,
        manifest,
        widget_defaults,
        # Checked again, a draft keeps its defaults; installed, it takes
        # its place among the team's widgets now.
        current=install.widget_defaults if draft else {},
        exclude_id=install.id,
    )
    with transaction.atomic(durable=True):
        PluginSecret.objects.filter(install=install).exclude(
            name__in=list(manifest.get("secrets", {}))
        ).delete()
        _store_secrets(install, secrets, user)
        for name, value in {
            **_source_fields(pinned),
            **_origin_fields(origin),
            "name": manifest["name"],
            "version": manifest["version"],
            "manifest": manifest,
            "settings": values,
            "widget_defaults": defaults,
            "installed_by": user,
            "generation": install.generation + 1,
            "date_updated": timezone_now(),
        }.items():
            setattr(install, name, value)
        if not draft:
            install.state = PluginInstall.STARTING
            install.date_installed = timezone_now()
        _settle_state(install, missing_settings)
        install.save()
        if install.bot.full_name != manifest["name"]:
            do_change_full_name(install.bot, manifest["name"], user, notify=True)
        if not draft:
            _audit_install(install, user, origin, secrets)
        send_armada_plugin_event(install, "update" if draft else "add")
    return install


def _audit_install(
    install: PluginInstall,
    user: UserProfile,
    origin: PluginOrigin | None,
    secrets: dict[str, str],
) -> None:
    """Recent's first line: installed from the store, or made by the agent
    for someone."""
    if origin is None:
        audit_plugin(
            install,
            user,
            AuditLogEventType.PLUGIN_INSTALLED,
            {
                "version": install.version,
                "source_kind": install.source_kind,
                "source": install.source,
                "source_path": install.source_path,
                "sha": install.sha,
                "settings": install.settings,
                "secrets": sorted(secrets),
            },
        )
        return
    audit_plugin(
        install,
        user,
        AuditLogEventType.PLUGIN_MADE,
        {
            "from_sha": None,
            "to_sha": install.sha,
            "from_version": None,
            "to_version": install.version,
            "run_id": origin.run.id if origin.run is not None else None,
            "for_user_id": origin.made_by.id,
        },
    )


def update_plugin(
    user: UserProfile,
    install: PluginInstall,
    *,
    plugin_settings: object | None = None,
    secrets: dict[str, str] | None = None,
    disabled: bool | None = None,
    widget_defaults: dict[str, Any] | None = None,
    disabled_tools: list[str] | None = None,
) -> None:
    """Settings are saved whole. A change the process must see restarts
    it, and lifts a failure, since the person may have fixed its cause."""
    check_not_removed(install)
    changes: dict[str, Any] = {}
    restart = False
    missing_settings = _check_settings(install.manifest, install.settings)[1]
    if plugin_settings is not None:
        values, missing_settings = _check_settings(install.manifest, plugin_settings)
        if values != install.settings:
            changes["settings"] = {"old": install.settings, "new": values}
            install.settings = values
            restart = True
    if secrets:
        _check_secrets(install.manifest, secrets)
        changes["secrets"] = sorted(secrets)
        restart = True
    if disabled is not None and disabled != install.disabled:
        changes["disabled"] = {"old": install.disabled, "new": disabled}
        install.disabled = disabled
        restart = True
    if widget_defaults is not None:
        defaults = _widget_defaults(
            install.realm,
            install.manifest,
            widget_defaults,
            current=install.widget_defaults,
            exclude_id=install.id,
        )
        if defaults != install.widget_defaults:
            changes["widget_defaults"] = {"old": install.widget_defaults, "new": defaults}
            install.widget_defaults = defaults
    if disabled_tools is not None:
        tools = sorted(set(disabled_tools))
        if len(tools) > ARMADA_PLUGIN_DISABLED_TOOLS_LIMIT or not all(
            TOOL_NAME.match(tool) for tool in tools
        ):
            raise JsonableError(_("disabled_tools lists at most 64 tool names."))
        if tools != install.disabled_tools:
            changes["disabled_tools"] = {"old": install.disabled_tools, "new": tools}
            install.disabled_tools = tools
    if not changes:
        return

    with transaction.atomic(durable=True):
        if secrets:
            _store_secrets(install, secrets, user)
        if restart:
            install.generation += 1
            _settle_state(install, missing_settings)
        install.date_updated = timezone_now()
        install.save()
        audit_plugin(install, user, AuditLogEventType.PLUGIN_SETTINGS_CHANGED, changes)
        send_armada_plugin_event(install, "update")


def remove_plugin(user: UserProfile, install: PluginInstall) -> None:
    """Take back what the install shows: its widgets go from every nav and
    column, by event, its bot is deactivated (its messages stay), and the
    host stops its process. Its settings, secrets and links are kept a week,
    for Undo. A draft, which no one else saw, is simply discarded."""
    check_not_removed(install)
    if install.state == PluginInstall.DRAFT:
        with transaction.atomic(durable=True):
            send_armada_plugin_event(install, "remove")
            install.delete()
            do_deactivate_user(install.bot, acting_user=user)
        return
    with transaction.atomic(durable=True):
        _remove_widget_states(install, PluginWidgetState.objects.filter(install=install))
        install.state = PluginInstall.REMOVED
        install.state_detail = ""
        install.generation += 1
        install.date_removed = install.date_updated = timezone_now()
        install.save()
        do_deactivate_user(install.bot, acting_user=user)
        audit_plugin(install, user, AuditLogEventType.PLUGIN_REMOVED, {"version": install.version})
        send_armada_plugin_event(install, "remove")


def restore_plugin(user: UserProfile, install: PluginInstall) -> None:
    """Undo a removal, within a week: the bot comes back with its links,
    settings and secrets, and the host starts the plugin again."""
    if install.state != PluginInstall.REMOVED:
        raise JsonableError(_("{name} is not removed.").format(name=install.name))
    assert install.date_removed is not None
    if install.date_removed < timezone_now() - timedelta(days=ARMADA_PLUGIN_UNDO_DAYS):
        raise JsonableError(
            _("{name} was removed more than a week ago; install it again.").format(
                name=install.name
            )
        )
    with transaction.atomic(durable=True):
        do_reactivate_user(install.bot, acting_user=user)
        install.state = PluginInstall.STARTING
        install.failures = 0
        install.date_removed = None
        install.generation += 1
        install.date_updated = timezone_now()
        _settle_state(install, _check_settings(install.manifest, install.settings)[1])
        install.save()
        audit_plugin(install, user, AuditLogEventType.PLUGIN_RESTORED, {"version": install.version})
        send_armada_plugin_event(install, "add")


def retry_plugin(user: UserProfile, install: PluginInstall) -> None:
    """Retry a plugin that failed five times in a row: the host starts it
    again, with five more tries."""
    if install.state != PluginInstall.FAILED:
        raise JsonableError(_("{name} has not failed.").format(name=install.name))
    install.state = PluginInstall.STARTING
    install.state_detail = ""
    install.failures = 0
    install.generation += 1
    install.date_updated = timezone_now()
    with transaction.atomic(durable=True):
        install.save(
            update_fields=["state", "state_detail", "failures", "generation", "date_updated"]
        )
        send_armada_plugin_event(install, "update")


def set_plugin_state(
    install: PluginInstall, state: int, *, detail: str, failures: int | None = None
) -> None:
    """The plugin host reports what an install's process is doing. It
    acts on what it read at the start of its pass, so the report is
    dropped if the install changed since (a change raises generation).
    Becoming Running starts the wait for its first push, which Recent
    shows as its start."""
    fields: dict[str, object] = {"state": state, "state_detail": detail[-4000:]}
    if failures is not None:
        fields["failures"] = failures
    if all(getattr(install, name) == value for name, value in fields.items()):
        return
    if state == PluginInstall.RUNNING and install.state != PluginInstall.RUNNING:
        fields["date_started"] = timezone_now()
    if PluginInstall.objects.filter(id=install.id, generation=install.generation).update(**fields):
        for name, value in fields.items():
            setattr(install, name, value)
        send_armada_plugin_event(install, "update")


def armada_plugin_url(install: PluginInstall) -> str:
    """Where the plugin answers: its port on this server, or its service."""
    if "remote" in install.manifest:
        return install.manifest["remote"]["url"].rstrip("/")
    return f"http://127.0.0.1:{install.port}"


def armada_plugin_session(install: PluginInstall, *, timeout: float) -> requests.Session:
    """A session that reaches the plugin. A process on this server is
    reached directly: Zulip's outgoing proxy, which a production server's
    environment names, refuses 127.0.0.1. A remote plugin is reached
    through the proxy, like any other service."""
    if "remote" in install.manifest:
        return OutgoingSession(role="armada_plugin", timeout=timeout)
    session = OutgoingSession(role="armada_plugin", timeout=timeout, proxies={})
    session.trust_env = False
    return session


def _manifest_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What a new manifest asks for that the old one did not, which the
    person updating accepts, and the widget keys it drops: people's
    preferences for those go with them."""

    def widget_keys(manifest: dict[str, Any]) -> set[str]:
        return {widget["key"] for widget in manifest["provides"].get("widgets", [])}

    return {
        "hosts": sorted(set(new.get("reaches", [])) - set(old.get("reaches", []))),
        "secrets": sorted(set(new.get("secrets", {})) - set(old.get("secrets", {}))),
        "events": sorted(
            set(new["provides"].get("events", [])) - set(old["provides"].get("events", []))
        ),
        "posts": bool(new["provides"].get("posts")) and not old["provides"].get("posts"),
        "dropped_widgets": sorted(widget_keys(old) - widget_keys(new)),
    }


def update_plugin_version(
    user: UserProfile,
    install: PluginInstall,
    *,
    sha: str,
    manifest: object,
    run: AgentRun | None = None,
    for_user: UserProfile | None = None,
) -> dict[str, Any]:
    """Move an install to another commit of its repository: a new version,
    or back to an earlier one, which is how a code change is undone (so no
    list of versions is kept). Settings, secrets and widgets the new
    manifest no longer declares go; returns the manifest's diff."""
    check_not_removed(install)
    if install.source_kind == PluginInstall.REMOTE:
        raise JsonableError(_("A remote plugin is updated where it runs."))
    new = check_plugin_manifest(manifest)
    if new["id"] != install.plugin_id:
        raise JsonableError(
            _("The manifest at that commit is another plugin's: {id}.").format(id=new["id"])
        )
    pinned = check_plugin_source(
        user,
        new,
        kind=install.source_kind,
        source=install.source,
        repository_id=install.source_repository_id,
        path=install.source_path,
        sha=sha,
    )
    diff = _manifest_diff(install.manifest, new)
    form = new.get("settings", {}).get("properties", {})
    values, missing_settings = _check_settings(
        new, {name: value for name, value in install.settings.items() if name in form}
    )
    dropped = diff["dropped_widgets"]
    defaults = _widget_defaults(
        install.realm,
        new,
        None,
        current={
            key: value for key, value in install.widget_defaults.items() if key not in dropped
        },
        exclude_id=install.id,
    )
    extra_data = {
        "from_sha": install.sha,
        "to_sha": pinned.sha,
        "from_version": install.version,
        "to_version": new["version"],
        "run_id": run.id if run is not None else None,
        "for_user_id": (for_user or user).id,
        "diff": diff,
    }
    with transaction.atomic(durable=True):
        PluginSecret.objects.filter(install=install).exclude(
            name__in=list(new.get("secrets", {}))
        ).delete()
        _remove_widget_states(
            install, PluginWidgetState.objects.filter(install=install, widget__in=dropped)
        )
        install.sha = pinned.sha
        install.manifest = new
        install.name = new["name"]
        install.version = new["version"]
        install.settings = values
        install.widget_defaults = defaults
        install.generation += 1
        install.date_updated = timezone_now()
        _settle_state(install, missing_settings)
        install.save()
        if install.bot.full_name != new["name"]:
            do_change_full_name(install.bot, new["name"], user, notify=True)
        audit_plugin(install, user, AuditLogEventType.PLUGIN_UPDATED, extra_data)
        send_armada_plugin_event(install, "update")
    return diff


def _remove_widget_states(install: PluginInstall, states: QuerySet[PluginWidgetState]) -> None:
    """Delete widget documents, and take them from the clients that had them."""
    for state in states.select_related("link"):
        send_armada_widget_event(install, state, state.link, "remove")
    states.delete()


def purge_removed_plugins(now: datetime) -> int:
    """Delete the installs removed more than a week ago, with everything
    hanging from them; their bots stay deactivated, signing their
    messages. Returns how many installs went."""
    cutoff = now - timedelta(days=ARMADA_PLUGIN_UNDO_DAYS)
    _count, deleted = PluginInstall.objects.filter(
        state=PluginInstall.REMOVED, date_removed__lt=cutoff
    ).delete()
    return deleted.get(PluginInstall._meta.label, 0)


def add_plugin_link(
    user: UserProfile,
    install: PluginInstall,
    *,
    kind: str,
    value: str,
    stream_id: int,
    topic: str | None,
) -> PluginLink:
    """Join a value the plugin understands to a channel, or to one of its
    topics. The install's bot joins the channel, so that its events show it
    what a member there sees."""
    check_not_removed(install)
    declared = install.manifest["provides"].get("links", {}).get(kind)
    if declared is None:
        raise JsonableError(
            _("{name} offers no link named {kind}.").format(name=install.name, kind=kind)
        )
    if topic is None and "channel" not in declared["to"]:
        raise JsonableError(
            _("{title} is linked to a topic, not to a whole channel.").format(
                title=declared["title"]
            )
        )
    if topic is not None and "topic" not in declared["to"]:
        raise JsonableError(
            _("{title} is linked to a whole channel, not to a topic.").format(
                title=declared["title"]
            )
        )
    value = value.strip()
    pattern = declared.get("pattern")
    if (
        not 0 < len(value) <= 200
        or MARKUP.search(value) is not None
        or (pattern is not None and re.search(pattern, value) is None)
    ):
        raise JsonableError(
            _("{value} is not a {title} that {name} understands.").format(
                value=value[:200], title=declared["title"], name=install.name
            )
        )
    if topic is not None:
        topic = topic.strip()
        if not 0 < len(topic) <= MAX_TOPIC_NAME_LENGTH:
            raise JsonableError(_("Invalid topic name"))
    stream, _subscription = access_stream_by_id(user, stream_id)
    with transaction.atomic(durable=True):
        places = PluginLink.objects.filter(install=install, kind=kind, value=value, stream=stream)
        taken = (
            places.filter(topic__isnull=True)
            if topic is None
            else places.filter(topic__iexact=topic)
        ).exists()
        if taken:
            raise JsonableError(_("That is linked there already."))
        link = PluginLink.objects.create(
            install=install, kind=kind, value=value, stream=stream, topic=topic, created_by=user
        )
        bulk_add_subscriptions(user.realm, [stream], [install.bot], acting_user=user)
        audit_plugin(
            install,
            user,
            AuditLogEventType.PLUGIN_LINKED,
            {"link_id": link.id, "kind": kind, "value": value, "topic": topic},
            stream_id=stream.id,
        )
        send_armada_plugin_event(install, "update")
    return link


def remove_plugin_link(user: UserProfile, install: PluginInstall, link_id: int) -> None:
    """Unlink; the install's bot leaves the channel when nothing of the
    install is linked there any more."""
    check_not_removed(install)
    link = PluginLink.objects.select_related("stream").filter(id=link_id, install=install).first()
    if link is None:
        raise ResourceNotFoundError(_("No such link"))
    stream = link.stream
    with transaction.atomic(durable=True):
        extra_data = {
            "link_id": link.id,
            "kind": link.kind,
            "value": link.value,
            "topic": link.topic,
        }
        _remove_widget_states(install, PluginWidgetState.objects.filter(link=link))
        link.delete()
        if not PluginLink.objects.filter(install=install, stream=stream).exists():
            bulk_remove_subscriptions(user.realm, [install.bot], [stream], acting_user=user)
        audit_plugin(
            install, user, AuditLogEventType.PLUGIN_UNLINKED, extra_data, stream_id=stream.id
        )
        send_armada_plugin_event(install, "update")


def _serialize_secret(
    name: str, declared: dict[str, Any], secret: PluginSecret | None
) -> dict[str, Any]:
    return {
        "name": name,
        "title": declared["title"],
        "required": declared.get("required", False),
        "set": secret is not None,
        "hint": secret.hint if secret is not None else "",
        "set_by": secret.set_by_id if secret is not None else None,
        "date_set": datetime_to_timestamp(secret.date_set) if secret is not None else None,
    }


def _serialize_install(
    install: PluginInstall, secrets: list[PluginSecret], links: list[PluginLink]
) -> dict[str, Any]:
    stored = {secret.name: secret for secret in secrets}
    return {
        "id": install.id,
        "plugin": install.plugin_id,
        "name": install.name,
        "version": install.version,
        "source_kind": install.source_kind,
        "source": install.source,
        "source_repository_id": install.source_repository_id,
        "source_path": install.source_path,
        "sha": install.sha,
        "manifest": install.manifest,
        "state": PluginInstall.STATE_NAMES[install.state],
        "detail": install.state_detail,
        "bot_user_id": install.bot_id,
        "installed_by": install.installed_by_id,
        "made_by": install.made_by_id,
        "made_by_run_id": install.made_by_run_id,
        "made_in_stream_id": install.made_in_stream_id,
        "made_in_topic": install.made_in_topic,
        "made_in_message_id": install.made_in_message_id,
        "settings": install.settings,
        "secrets": [
            _serialize_secret(name, declared, stored.get(name))
            for name, declared in install.manifest.get("secrets", {}).items()
        ],
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
        "disabled": install.disabled,
        "widget_defaults": install.widget_defaults,
        "disabled_tools": install.disabled_tools,
        "date_installed": datetime_to_timestamp(install.date_installed),
        "date_removed": (
            datetime_to_timestamp(install.date_removed) if install.date_removed else None
        ),
    }


def serialize_plugin_installs(installs: list[PluginInstall]) -> list[dict[str, Any]]:
    """Each install as a person sees it: never a secret, only whether it is set."""
    secrets: dict[int, list[PluginSecret]] = defaultdict(list)
    for secret in PluginSecret.objects.filter(install__in=installs).order_by("name"):
        secrets[secret.install_id].append(secret)
    links: dict[int, list[PluginLink]] = defaultdict(list)
    for link in PluginLink.objects.filter(install__in=installs).order_by("id"):
        links[link.install_id].append(link)
    return [
        _serialize_install(install, secrets[install.id], links[install.id]) for install in installs
    ]


def list_plugin_installs(user: UserProfile) -> list[dict[str, Any]]:
    """The team's plugins, with the drafts this person is making."""
    check_plugin_member(user)
    installs = PluginInstall.objects.filter(realm=user.realm).order_by("id")
    return serialize_plugin_installs([install for install in installs if _sees(user, install)])
