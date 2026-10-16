"""Removing the worktrees of finished topics.

A topic's worktree is a clone of its channel's repository (for Armada's
own repository about 0.75 GB with its build outputs), and nothing ever
removed one, so they piled up for every topic ever used. Once an hour the
monitor worker removes those that are done and idle, by this policy:

- the topic is resolved, or deleted (no messages left, or its channel
  gone), or everything in its worktree is already in the channel's base
  branch (its branch merged, or never changed);
- nothing has used it for ARMADA_WORKTREE_RETENTION_DAYS: no run, no
  change to the worktree;
- no run is queued or running in it, and no monitor on its wall is live
  or being set up;
- removing it loses nothing: it has no uncommitted or untracked file
  (ignored ones, such as build outputs, go), and every commit in it is in
  the channel's repository, its topic branch being pushed there first
  (fast-forward only).

The Workspace row stays, marked archived. The topic's next run provisions
the worktree again from the channel's repository, on its branch, and the
Codex thread is resumed as before. Worktrees owned by a topic's OS user
(ARMADA_TOPIC_ISOLATION_BACKEND = "os-user") are left alone: the worker
cannot read or delete that user's files.
"""

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils.timezone import now as timezone_now

from zerver.lib.context_managers import lockfile
from zerver.lib.topic import RESOLVED_TOPIC_PREFIX, messages_for_topic
from zerver.models import Stream
from zerver.models.bots import AgentMonitor, AgentRun, Repository, Workspace

logger = logging.getLogger(__name__)

ARMADA_WORKTREE_RETENTION_PASS_SECONDS = 60 * 60
# A pass removes at most this many, so one pass never holds the tick
# thread for long; the rest go in the next.
ARMADA_WORKTREE_RETENTION_BATCH_LIMIT = 20
ARMADA_WORKTREE_RETENTION_GIT_TIMEOUT_SECONDS = 60

armada_worktree_retention_last_pass: float | None = None


def armada_retention_git(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=ARMADA_WORKTREE_RETENTION_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(["git", *arguments], 1, "", str(error))


def armada_topic_done_reason(workspace_node: Workspace) -> str | None:
    """Why the topic is done, from the database alone, or None."""
    stream = Stream.objects.filter(
        realm_id=workspace_node.realm_id, name__iexact=workspace_node.stream_name
    ).first()
    if stream is None or stream.deactivated or stream.recipient_id is None:
        return "deleted"
    if workspace_node.topic_name.startswith(RESOLVED_TOPIC_PREFIX):
        return "resolved"
    if not messages_for_topic(
        workspace_node.realm_id, stream.recipient_id, workspace_node.topic_name
    ).exists():
        return "deleted"
    return None


def armada_worktree_is_saved(workspace_node: Workspace) -> bool:
    """Whether removing the worktree loses nothing: it is clean, and every
    commit in it (any branch, the stash, a detached HEAD) is in the
    channel's repository. Pushes the topic branch there first."""
    workdir = workspace_node.workdir
    status = armada_retention_git(
        ["-C", workdir, "status", "--porcelain", "--untracked-files=normal"]
    )
    if status.returncode != 0 or status.stdout.strip():
        return False
    current_branch = armada_retention_git(["-C", workdir, "branch", "--show-current"])
    if current_branch.returncode == 0 and current_branch.stdout.strip() == (
        workspace_node.branch_name
    ):
        # Fast-forward only: git refuses to move the repository's copy of
        # the branch anywhere else, and then the check below keeps it.
        armada_retention_git(
            [
                "-C",
                workdir,
                "push",
                "--quiet",
                "origin",
                f"HEAD:refs/heads/{workspace_node.branch_name}",
            ]
        )
    unsaved = armada_retention_git(
        ["-C", workdir, "rev-list", "--max-count=1", "--all", "--not", "--remotes=origin"]
    )
    return unsaved.returncode == 0 and not unsaved.stdout.strip()


def armada_refresh_channel_upstream(project: Repository) -> None:
    """Fetch the channel's upstream into its repository, so that a branch
    merged there counts as merged. Under the lock provisioning takes."""
    origin = Path(project.repo_cache_path)
    if not project.repository_url or not origin.is_dir():
        return
    with lockfile(str(origin.parent / f".{origin.name}.lock")):
        fetched = armada_retention_git(
            [
                "--git-dir",
                str(origin),
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                "upstream",
                "+refs/heads/*:refs/remotes/upstream/*",
            ]
        )
    if fetched.returncode != 0:
        logger.info(
            "Could not fetch the upstream of %s; merged worktrees wait: %s",
            project.repo_cache_path,
            fetched.stderr.strip()[:300],
        )


def armada_worktree_is_merged(workspace_node: Workspace) -> bool:
    """Whether the worktree's HEAD is in the channel's base branch, as the
    channel's repository or its copy of the upstream has it."""
    project = workspace_node.project
    if project is None:
        return False
    head = armada_retention_git(["-C", workspace_node.workdir, "rev-parse", "HEAD"])
    base_ref = workspace_node.base_ref or project.default_base_ref
    if head.returncode != 0 or not base_ref:
        return False
    return any(
        armada_retention_git(
            [
                "--git-dir",
                project.repo_cache_path,
                "merge-base",
                "--is-ancestor",
                head.stdout.strip(),
                base,
            ]
        ).returncode
        == 0
        for base in (f"refs/heads/{base_ref}", f"refs/remotes/upstream/{base_ref}")
    )


def armada_worktree_in_use(workspace_node: Workspace, *, since: datetime) -> bool:
    if (
        AgentRun.objects.filter(
            Q(workspace_node=workspace_node)
            | Q(
                realm_id=workspace_node.realm_id,
                stream_name__iexact=workspace_node.stream_name,
                topic_name__iexact=workspace_node.topic_name,
            )
        )
        .filter(Q(status__in=(AgentRun.QUEUED, AgentRun.RUNNING)) | Q(date_updated__gte=since))
        .exists()
    ):
        return True
    return AgentMonitor.objects.filter(
        realm_id=workspace_node.realm_id,
        stream_name__iexact=workspace_node.stream_name,
        topic_name__iexact=workspace_node.topic_name,
        status__in=(AgentMonitor.LIVE, AgentMonitor.PROVISIONING),
    ).exists()


def armada_remove_worktree(workspace_node: Workspace, *, since: datetime, reason: str) -> bool:
    with transaction.atomic(savepoint=False):
        # A run's claim takes this lock too, so no run can start in the
        # worktree while it goes; one that starts after provisions it anew.
        locked = Workspace.objects.select_for_update(no_key=True).get(id=workspace_node.id)
        if (
            locked.lifecycle_status != Workspace.ACTIVE
            or locked.workdir != workspace_node.workdir
            or armada_worktree_in_use(locked, since=since)
        ):
            return False
        shutil.rmtree(locked.workdir)
        # A new revision, so no process serves its cached snapshot.
        Workspace.objects.filter(id=locked.id).update(
            lifecycle_status=Workspace.ARCHIVED,
            content_revision=F("content_revision") + 1,
            date_updated=timezone_now(),
        )
    logger.info(
        "Removed the worktree of %s > %s (%s): %s",
        workspace_node.stream_name,
        workspace_node.topic_name,
        reason,
        workspace_node.workdir,
    )
    return True


def reap_retired_armada_worktrees(*, current_time: datetime | None = None) -> int:
    """Remove the worktrees of finished topics, by the policy above;
    return how many went."""
    retention_days = settings.ARMADA_WORKTREE_RETENTION_DAYS
    if retention_days <= 0:
        return 0
    now = timezone_now() if current_time is None else current_time
    since = now - timedelta(days=retention_days)
    candidates = (
        Workspace.objects.select_related("project")
        .filter(lifecycle_status=Workspace.ACTIVE, isolation_username="", date_updated__lt=since)
        .exclude(topic_name="")
        .order_by("date_updated")
    )
    refreshed_projects: set[int] = set()
    removed = 0
    for workspace_node in candidates.iterator():
        if removed >= ARMADA_WORKTREE_RETENTION_BATCH_LIMIT:
            break
        if not os.path.isdir(workspace_node.workdir) or armada_worktree_in_use(
            workspace_node, since=since
        ):
            continue
        reason = armada_topic_done_reason(workspace_node)
        if reason is None:
            project = workspace_node.project
            if project is not None and project.id not in refreshed_projects:
                refreshed_projects.add(project.id)
                armada_refresh_channel_upstream(project)
            if not armada_worktree_is_merged(workspace_node):
                # A topic still in progress: its branch is left as it is,
                # not pushed.
                continue
            reason = "merged"
        if not armada_worktree_is_saved(workspace_node):
            continue
        try:
            if armada_remove_worktree(workspace_node, since=since, reason=reason):
                removed += 1
        except OSError:
            logger.exception("Could not remove the worktree %s", workspace_node.workdir)
    return removed


def maybe_reap_retired_armada_worktrees(*, current_time: float | None = None) -> None:
    """At most one pass an hour, on the monitor worker's tick thread."""
    global armada_worktree_retention_last_pass
    now = time.monotonic() if current_time is None else current_time
    if (
        armada_worktree_retention_last_pass is not None
        and now - armada_worktree_retention_last_pass < ARMADA_WORKTREE_RETENTION_PASS_SECONDS
    ):
        return
    armada_worktree_retention_last_pass = now
    reap_retired_armada_worktrees()
