"""A worktree's git facts for its topic's header: branch, distance from
its upstream, changed files, last commit and commit count.

Reading them runs git, which takes from a few milliseconds to half a
second on a repository with a long history, so no request does: they are
read in a background thread when the worktree changes (a run's tool
results and its end, a write through Files) or when they are missing or
old, kept on the Workspace row, and served from there. A refresh that
finds them changed tells the channel's subscribers, whose pages read the
context again.
"""

import time
from pathlib import Path

from django.core.cache import cache
from django.db.models import Q

from zerver.lib.armada import trim_armada_text
from zerver.lib.armada_background_task import ArmadaBackgroundTask
from zerver.lib.armada_worker import (
    armada_git_output,
    armada_topic_isolation_user_exists,
    parse_armada_git_status_delta,
)
from zerver.models.bots import Workspace

# Facts older than this are read again when someone looks at them, in
# case the worktree changed behind the server's back (a person at a shell).
ARMADA_WORKSPACE_GIT_MAX_AGE_SECONDS = 300
# How long a refresh asked for by a reader keeps other processes from
# asking for one too.
ARMADA_WORKSPACE_GIT_READER_REFRESH_SECONDS = 30

# The facts kept from `git status --porcelain=v2 --branch`; the rest of
# what parse_armada_git_status_delta returns depends on the worktree's
# path or the topic, and is added when the facts are served.
ARMADA_WORKSPACE_GIT_STATUS_FIELDS = (
    "branch",
    "head",
    "head_short",
    "upstream",
    "ahead",
    "behind",
    "dirty",
    "changed_count",
    "changed_files",
    "changed_files_truncated",
    "detached",
)


def workspace_git_isolation_username(node: Workspace) -> str | None:
    username = node.isolation_username.strip()
    if username and armada_topic_isolation_user_exists(username):
        return username
    return None


def read_workspace_git_state(
    workdir: str,
    *,
    isolation_username: str | None,
    previous: dict[str, object],
) -> dict[str, object]:
    """Run git in the worktree. One command when HEAD has not moved since
    `previous`; three when it has, the count of commits taken from the
    previous count and the commits between the two heads rather than by
    walking the whole history again."""
    status_output = armada_git_output(
        workdir,
        ["status", "--porcelain=v2", "--branch"],
        isolation_username=isolation_username,
    )
    if not status_output:
        return {"available": False}
    delta = parse_armada_git_status_delta(status_output, workdir=workdir)
    state: dict[str, object] = {"available": True}
    state.update((field, delta[field]) for field in ARMADA_WORKSPACE_GIT_STATUS_FIELDS)
    head_oid = next(
        (
            line.removeprefix("# branch.oid ").strip()
            for line in status_output.splitlines()
            if line.startswith("# branch.oid ")
        ),
        "",
    )
    # An unborn HEAD ("(initial)") has no commits to describe.
    if not delta["head_short"]:
        return state
    state["head_oid"] = head_oid

    previous_oid = previous.get("head_oid")
    if previous_oid == head_oid and "last_commit" in previous:
        state["last_commit"] = previous["last_commit"]
    else:
        log_parts = armada_git_output(
            workdir,
            ["log", "-1", "--format=%h%x1f%s%x1f%an%x1f%ct", head_oid],
            isolation_username=isolation_username,
        ).split("\x1f")
        if len(log_parts) == 4 and log_parts[0]:
            state["last_commit"] = {
                "sha": log_parts[0],
                "subject": trim_armada_text(log_parts[1], limit=200),
                "author": trim_armada_text(log_parts[2], limit=80),
                "timestamp": int(log_parts[3]) if log_parts[3].isdigit() else None,
            }

    previous_count = previous.get("commit_count")
    if previous_oid == head_oid and isinstance(previous_count, int):
        state["commit_count"] = previous_count
        return state
    if isinstance(previous_oid, str) and previous_oid and isinstance(previous_count, int):
        # The commits only the old head has, and those only the new one
        # has: a fast-forward, a reset and a rebase all cost the commits
        # between them.
        sides = armada_git_output(
            workdir,
            ["rev-list", "--left-right", "--count", f"{previous_oid}...{head_oid}"],
            isolation_username=isolation_username,
        ).split()
        if len(sides) == 2 and all(side.isdigit() for side in sides):
            state["commit_count"] = previous_count - int(sides[0]) + int(sides[1])
            return state
    count = armada_git_output(
        workdir,
        ["rev-list", "--count", head_oid],
        isolation_username=isolation_username,
    )
    if count.isdigit():
        state["commit_count"] = int(count)
    return state


def refresh_workspace_git_state(workspace_id: int) -> bool:
    """Read the worktree's git facts and keep them on its row. Returns
    whether they changed; when they did, the channel's subscribers are
    told."""
    node = Workspace.objects.filter(id=workspace_id).first()
    if node is None or not node.workdir.strip():
        return False
    previous = node.git_state if isinstance(node.git_state, dict) else {}
    observed_at = time.time()
    state = {
        **read_workspace_git_state(
            node.workdir.strip(),
            isolation_username=workspace_git_isolation_username(node),
            previous=previous,
        ),
        "observed_at": observed_at,
    }
    # Another process may have read the worktree after this one did;
    # its facts are the newer and stay.
    stored = (
        Workspace.objects.filter(id=workspace_id)
        .filter(Q(git_state__observed_at__lt=observed_at) | ~Q(git_state__has_key="observed_at"))
        .update(git_state=state)
    )
    changed = bool(stored) and {**previous, "observed_at": None} != {
        **state,
        "observed_at": None,
    }
    if changed:
        from zerver.actions.armada import send_armada_workspace_changed_event

        send_armada_workspace_changed_event(node, int(node.content_revision))
    return changed


workspace_git_refresh = ArmadaBackgroundTask[int](
    "armada-workspace-git", refresh_workspace_git_state
)


def request_workspace_git_refresh(workspace_id: int) -> None:
    """Read the worktree's git facts again, in the background, once the
    current transaction commits: the worktree has changed."""
    workspace_git_refresh.request(workspace_id)


def workspace_git_context(
    node: Workspace | None, *, workdir: str, branch: str
) -> dict[str, object]:
    """The topic header's git facts as the topic context serves them,
    from the row. Facts that are missing or old are asked for in the
    background, at most once in ARMADA_WORKSPACE_GIT_READER_REFRESH_SECONDS
    across processes, and this answer does not wait for them."""
    if not workdir:
        return {"available": False}
    state = node.git_state if node is not None and isinstance(node.git_state, dict) else {}
    observed_at = state.get("observed_at")
    stale = (
        not isinstance(observed_at, int | float)
        or time.time() - observed_at >= ARMADA_WORKSPACE_GIT_MAX_AGE_SECONDS
    )
    if (
        node is not None
        and stale
        and cache.add(
            f"armada:workspace-git-reader-refresh:{node.id}",
            True,
            ARMADA_WORKSPACE_GIT_READER_REFRESH_SECONDS,
        )
    ):
        request_workspace_git_refresh(node.id)
    if not state.get("available"):
        return {"available": False, "workdir": workdir}
    name = Path(workdir).name
    context: dict[str, object] = {
        "available": True,
        "workdir": workdir,
        "repo_name": name,
        "worktree_name": name,
    }
    context.update((field, state[field]) for field in ARMADA_WORKSPACE_GIT_STATUS_FIELDS)
    topic_branch = branch.lower()
    context["branch_matches_topic"] = bool(
        topic_branch and not state["detached"] and state["branch"] == topic_branch
    )
    for field in ("last_commit", "commit_count"):
        if field in state:
            context[field] = state[field]
    return context
