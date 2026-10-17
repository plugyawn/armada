"""The team's plugins repository (design/r9/plugins/ROUND2.md, decision 1
and section G).

An organization keeps every plugin it makes in one repository channel,
`plugins`. Each plugin is a topic of it, `plugins › eval-queue`, which is
a worktree like any topic's, and its code lives in `plugins/eval-queue/`.
The repository is this server's, like any repository channel's. It can be
mirrored to a remote, such as a private GitHub repository: the remote is
a git remote named `mirror`, off until it is set, and the plugin host
pushes to it whenever the repository changes (statement 45).

To install a plugin from it, a commit is taken from the worktree it was
made in, which is called releasing it. Releasing:

- checks the manifest at `plugins/<id>/` in that commit;
- moves `main` to the commit, which must be a fast-forward of `main`;
- tags the commit `<id>/v<version>`.

The install is then pinned to that commit, and the plugin host fetches
`plugins/<id>/` at it with git archive
(zerver/lib/armada_plugin_host.py). A new version of a plugin that is
already installed is released the same way. An update back to an earlier
commit is how a code change is undone. That commit is already in `main`
and tagged, so it goes to update_plugin_version directly.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from zerver.actions.default_streams import do_add_default_stream
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib.armada_plugin_validation import ARMADA_PLUGIN_MANIFEST_FILE
from zerver.lib.armada_plugins import (
    COMMIT_SHA,
    PluginOrigin,
    check_installable,
    check_not_removed,
    check_plugin_manifest,
    check_plugin_member,
    install_plugin,
    update_plugin_version,
)
from zerver.lib.armada_worker import armada_project_for_stream, ensure_armada_project_origin
from zerver.lib.context_managers import lockfile
from zerver.lib.exceptions import JsonableError
from zerver.lib.streams import access_stream_by_id, create_stream_if_needed
from zerver.models import AgentRun, PluginInstall, Realm, Repository, UserProfile

logger = logging.getLogger(__name__)

ARMADA_PLUGIN_TEAM_CHANNEL = "plugins"
ARMADA_PLUGIN_TEAM_BRANCH = "main"
# Where each plugin's code is in the repository: plugins/<id>/.
ARMADA_PLUGIN_TEAM_DIRECTORY = "plugins"
ARMADA_PLUGIN_MIRROR_REMOTE = "mirror"
# What the repository's branches and tags were when the mirror last took
# them, kept in the repository's own directory, where git ignores it.
ARMADA_PLUGIN_MIRROR_STATE = "armada-mirrored"
ARMADA_PLUGIN_GIT_SECONDS = 60
ARMADA_PLUGIN_MIRROR_PUSH_SECONDS = 300
# The plugin host pushes without a terminal: ssh may not ask for a
# passphrase, and accepts a host's key the first time it sees it.
ARMADA_PLUGIN_MIRROR_SSH = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")
# git@github.com:runix/team-plugins.git, as ssh writes an address.
SCP_ADDRESS = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+$")
TEAM_README = """\
# The team's plugins

Each plugin is a directory here, `plugins/<id>/`, holding its
`armada-plugin.json` (Armada's plugin contract) and its program. It is
made and changed in the topic of the `plugins` channel with the same name.

Installing a plugin releases the commit it was made in: `main` moves to
that commit, which must be a fast-forward of `main`, and the commit is
tagged `<id>/v<version>`. Armada runs each plugin from its directory at
the commit it was installed at.
"""


class ArmadaPluginMirrorError(Exception):
    """A push to the mirror failed."""


@dataclass(frozen=True)
class TeamPluginRelease:
    """A commit of the team's repository that `main` holds and a tag names."""

    repository: Repository
    path: str
    sha: str
    tag: str
    manifest: dict[str, Any]


def _git(
    git_dir: str,
    *args: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = ARMADA_PLUGIN_GIT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "--git-dir", git_dir, *args],
            input=input_text if input_text is not None else "",
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(error))


def _git_checked(
    git_dir: str, *args: str, input_text: str | None = None, env: dict[str, str] | None = None
) -> str:
    completed = _git(git_dir, *args, input_text=input_text, env=env)
    if completed.returncode != 0:
        logger.warning("git %s in %s failed: %s", args[0], git_dir, completed.stderr.strip()[:500])
        raise JsonableError(_("The plugins repository could not be changed; the log says why."))
    return completed.stdout.strip()


def _lock(repository: Repository) -> str:
    """The lock provisioning takes for a channel's repository
    (ensure_armada_project_origin), so that no worktree is made from it
    while main moves."""
    origin = Path(repository.repo_cache_path)
    return str(origin.parent / f".{origin.name}.lock")


def team_plugin_repositories() -> QuerySet[Repository]:
    """Every organization's plugins repository: a ready repository of a
    channel named `plugins`."""
    return Repository.objects.select_related("stream").filter(
        stream__name__iexact=ARMADA_PLUGIN_TEAM_CHANNEL,
        stream__deactivated=False,
        clone_status=Repository.READY,
    )


def team_plugin_repository(realm: Realm) -> Repository | None:
    return team_plugin_repositories().filter(realm=realm).first()


def access_team_plugin_repository(user: UserProfile) -> Repository:
    """The organization's plugins repository, for a member who can read
    its channel."""
    check_plugin_member(user)
    repository = team_plugin_repository(user.realm)
    if repository is None:
        raise JsonableError(
            _(
                "This organization has no plugins repository yet; an administrator makes it"
                " with ./manage.py armada_plugins init-team-repo."
            )
        )
    access_stream_by_id(user, repository.stream_id)
    return repository


def _identity(user: UserProfile | None) -> dict[str, str]:
    name = user.full_name if user is not None else "Armada"
    email = user.email if user is not None else f"armada@{settings.EXTERNAL_HOST.split(':')[0]}"
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def _seed_readme(origin: str) -> None:
    """Put the README on a repository that has only its first, empty
    commit."""
    main = f"refs/heads/{ARMADA_PLUGIN_TEAM_BRANCH}"
    head = _git_checked(origin, "rev-parse", "--verify", f"{main}^{{commit}}")
    if _git_checked(origin, "ls-tree", "--name-only", head):
        return
    blob = _git_checked(origin, "hash-object", "-w", "--stdin", input_text=TEAM_README)
    tree = _git_checked(origin, "mktree", input_text=f"100644 blob {blob}\tREADME.md\n")
    commit = _git_checked(
        origin,
        "commit-tree",
        tree,
        "-p",
        head,
        "-m",
        "Say what the team's plugins repository holds.",
        env=_identity(None),
    )
    _git_checked(origin, "update-ref", main, commit, head)


def init_team_plugin_repository(realm: Realm, *, acting_user: UserProfile) -> Repository:
    """The `plugins` channel, with every member in it and every new member
    joining it, and its repository on this server, with a README. Run
    again, it repairs what is missing and changes nothing else."""
    stream, _created = create_stream_if_needed(
        realm,
        ARMADA_PLUGIN_TEAM_CHANNEL,
        stream_description=_("The team's plugins: one topic for each."),
        acting_user=acting_user,
    )
    existing = Repository.objects.filter(realm=realm, stream=stream).first()
    if existing is not None and existing.repository_url:
        raise JsonableError(
            _("#{channel} is already the repository {url}; rename that channel first.").format(
                channel=stream.name, url=existing.repository_url
            )
        )
    members = list(
        UserProfile.objects.filter(realm=realm, is_active=True, is_bot=False).exclude(
            role=UserProfile.ROLE_GUEST
        )
    )
    with transaction.atomic(durable=True):
        bulk_add_subscriptions(realm, [stream], members, acting_user=acting_user)
        do_add_default_stream(stream)
    repository = armada_project_for_stream(acting_user, stream=stream.name)
    assert repository is not None
    origin = str(ensure_armada_project_origin(acting_user, repository))
    repository.refresh_from_db()
    with lockfile(_lock(repository)):
        _seed_readme(origin)
        # Only releasing a plugin moves main: nothing pushed rewrites it.
        _git_checked(origin, "config", "receive.denyNonFastForwards", "true")
    return repository


def check_mirror_address(address: str) -> str:
    """A git remote's address the server can push to without a person:
    ssh with the server's key, or https with git's own credentials. An
    address that holds a password or a token is refused, since it would
    sit in the repository's config and in every log line about a push."""
    address = address.strip()
    if address and not address.startswith("-") and not any(c.isspace() for c in address):
        if SCP_ADDRESS.match(address):
            return address
        parts = urlsplit(address)
        if parts.scheme in ("https", "ssh") and parts.hostname:
            if parts.password is not None or (parts.scheme == "https" and parts.username):
                raise JsonableError(
                    _(
                        "A mirror's address holds no password or token: the server signs in"
                        " with its own SSH key, or with git's credentials."
                    )
                )
            return address
        if settings.ARMADA_PLUGIN_LOCAL_SOURCES and os.path.isabs(address):
            return os.path.normpath(address)
    raise JsonableError(
        _("A mirror is an ssh or https address, as git@github.com:runix/team-plugins.git.")
    )


def team_plugin_mirror(repository: Repository) -> str | None:
    """The mirror's address, or None while there is none."""
    configured = _git(
        repository.repo_cache_path, "config", "--get", f"remote.{ARMADA_PLUGIN_MIRROR_REMOTE}.url"
    )
    return configured.stdout.strip() or None


def set_team_plugin_mirror(repository: Repository, address: str | None) -> None:
    """Set the mirror (None turns it off). The plugin host pushes every
    branch and tag to a new mirror on its next pass."""
    origin = repository.repo_cache_path
    if address is not None:
        address = check_mirror_address(address)
    with lockfile(_lock(repository)):
        current = team_plugin_mirror(repository)
        if address is None:
            if current is not None:
                _git_checked(origin, "remote", "remove", ARMADA_PLUGIN_MIRROR_REMOTE)
        elif current is None:
            _git_checked(origin, "remote", "add", ARMADA_PLUGIN_MIRROR_REMOTE, address)
        elif current != address:
            _git_checked(origin, "remote", "set-url", ARMADA_PLUGIN_MIRROR_REMOTE, address)
        Path(origin, ARMADA_PLUGIN_MIRROR_STATE).unlink(missing_ok=True)


def _refs(origin: str) -> str:
    """A fingerprint of the repository's branches and tags."""
    listed = _git(
        origin, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags"
    )
    if listed.returncode != 0:
        raise ArmadaPluginMirrorError(f"Could not list the branches and tags of {origin}.")
    return hashlib.sha256(listed.stdout.encode()).hexdigest()


def team_plugin_mirror_is_behind(repository: Repository) -> bool:
    """Whether the repository has a mirror that lacks some of it."""
    if team_plugin_mirror(repository) is None:
        return False
    try:
        mirrored = Path(repository.repo_cache_path, ARMADA_PLUGIN_MIRROR_STATE).read_text()
    except OSError:
        mirrored = ""
    return mirrored != _refs(repository.repo_cache_path)


def push_team_plugin_mirror(origin: str) -> None:
    """Push every branch and tag to the mirror: the branches as they are
    here, the tags never moved. The plugin host runs this beside its
    pass, since it waits on the network."""
    refs = _refs(origin)
    ssh = os.environ.get("GIT_SSH_COMMAND", ARMADA_PLUGIN_MIRROR_SSH)
    pushed = _git(
        origin,
        "push",
        "--porcelain",
        ARMADA_PLUGIN_MIRROR_REMOTE,
        "+refs/heads/*:refs/heads/*",
        "refs/tags/*:refs/tags/*",
        env={"GIT_SSH_COMMAND": ssh},
        timeout=ARMADA_PLUGIN_MIRROR_PUSH_SECONDS,
    )
    if pushed.returncode != 0:
        lines = (pushed.stderr or pushed.stdout).strip().splitlines()
        raise ArmadaPluginMirrorError(" ".join(lines[-3:]))
    Path(origin, ARMADA_PLUGIN_MIRROR_STATE).write_text(refs)


def release_team_plugin_commit(
    user: UserProfile, plugin_id: str, sha: str, *, workdir: Path | None = None
) -> TeamPluginRelease:
    """Move main to the commit and tag it with the plugin's version. The
    commit comes from the worktree it was made in when one is given; it
    must hold a manifest for the plugin in its directory, be a
    fast-forward of main, and carry a version that no other commit is
    tagged with. Each refusal says what to do next."""
    if not PLUGIN_ID.match(plugin_id):
        raise JsonableError(
            _("A plugin's id is lowercase letters, digits and dashes, as eval-queue.")
        )
    if not COMMIT_SHA.match(sha):
        raise JsonableError(_("A plugin is installed at a commit: 40 hexadecimal digits."))
    repository = access_team_plugin_repository(user)
    origin = repository.repo_cache_path
    path = f"{ARMADA_PLUGIN_TEAM_DIRECTORY}/{plugin_id}"
    manifest_path = f"{path}/{ARMADA_PLUGIN_MANIFEST_FILE}"
    main_ref = f"refs/heads/{ARMADA_PLUGIN_TEAM_BRANCH}"
    with lockfile(_lock(repository)):
        if workdir is not None and _git(origin, "cat-file", "-e", f"{sha}^{{commit}}").returncode:
            _git(
                origin, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(workdir), sha
            )
        if _git(origin, "cat-file", "-e", f"{sha}^{{commit}}").returncode:
            raise JsonableError(
                _(
                    "There is no commit {sha} in the worktree or the plugins repository:"
                    " commit the plugin, then install that commit."
                ).format(sha=sha[:12])
            )
        shown = _git(origin, "show", f"{sha}:{manifest_path}")
        if shown.returncode:
            raise JsonableError(
                _("{sha} has no {file}: a plugin's code is in {path}/.").format(
                    sha=sha[:12], file=manifest_path, path=path
                )
            )
        try:
            value = json.loads(shown.stdout)
        except ValueError as error:
            raise JsonableError(
                _("{file} is not JSON: {error}").format(file=manifest_path, error=str(error))
            )
        manifest = check_plugin_manifest(value)
        if manifest["id"] != plugin_id:
            raise JsonableError(
                _("{file} names the plugin {id}; a plugin's directory is named after it.").format(
                    file=manifest_path, id=manifest["id"]
                )
            )
        main = _git_checked(origin, "rev-parse", "--verify", f"{main_ref}^{{commit}}")
        if _git(origin, "merge-base", "--is-ancestor", main, sha).returncode:
            missing = int(_git_checked(origin, "rev-list", "--count", f"{sha}..{main}"))
            raise JsonableError(
                ngettext(
                    "{sha} is not a fast-forward of main, which has {count} commit it lacks."
                    " Rebase onto main (git fetch origin, then git rebase origin/main),"
                    " check the plugin again, and install the new commit.",
                    "{sha} is not a fast-forward of main, which has {count} commits it lacks."
                    " Rebase onto main (git fetch origin, then git rebase origin/main),"
                    " check the plugin again, and install the new commit.",
                    missing,
                ).format(sha=sha[:12], count=missing)
            )
        tag = f"{plugin_id}/v{manifest['version']}"
        tagged = _git(origin, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}")
        tagged_sha = tagged.stdout.strip()
        if tagged_sha and tagged_sha != sha:
            raise JsonableError(
                _(
                    "{name} {version} is already tagged at {tagged}: raise the version in {file}."
                ).format(
                    name=manifest["name"],
                    version=manifest["version"],
                    tagged=tagged_sha[:12],
                    file=manifest_path,
                )
            )
        if main != sha:
            _git_checked(origin, "update-ref", main_ref, sha, main)
        if not tagged_sha:
            _git_checked(
                origin,
                "tag",
                "--annotate",
                "--message",
                f"{manifest['name']} {manifest['version']}, released by {user.full_name}.",
                tag,
                sha,
                env=_identity(user),
            )
    return TeamPluginRelease(repository, path, sha, tag, manifest)


def install_team_plugin(
    user: UserProfile,
    *,
    plugin_id: str,
    sha: str,
    workdir: Path | None = None,
    plugin_settings: object | None = None,
    secrets: dict[str, str] | None = None,
    widget_defaults: dict[str, Any] | None = None,
    origin: PluginOrigin | None = None,
) -> PluginInstall:
    """Release the commit, then install the plugin pinned to it; a draft
    of the person's own becomes the install. Whatever would refuse the
    install refuses before main moves."""
    check_plugin_member(user)
    check_installable(user, plugin_id)
    release = release_team_plugin_commit(user, plugin_id, sha, workdir=workdir)
    return install_plugin(
        user,
        manifest=release.manifest,
        source_kind=PluginInstall.LOCAL,
        repository_id=release.repository.id,
        path=release.path,
        sha=release.sha,
        plugin_settings=plugin_settings,
        secrets=secrets,
        widget_defaults=widget_defaults,
        origin=origin,
    )


def update_team_plugin(
    user: UserProfile,
    install: PluginInstall,
    *,
    sha: str,
    workdir: Path | None = None,
    run: AgentRun | None = None,
    for_user: UserProfile | None = None,
) -> dict[str, Any]:
    """Release a new commit of an installed plugin and move the install to
    it; returns the manifest's diff (update_plugin_version)."""
    check_not_removed(install)
    repository = access_team_plugin_repository(user)
    if install.source_kind != PluginInstall.LOCAL or install.source_repository_id != repository.id:
        raise JsonableError(
            _("{name} does not come from the team's plugins repository.").format(name=install.name)
        )
    release = release_team_plugin_commit(user, install.plugin_id, sha, workdir=workdir)
    return update_plugin_version(
        user, install, sha=release.sha, manifest=release.manifest, run=run, for_user=for_user
    )
