import contextlib
import hashlib
import json
import logging
import os
import posixpath
import pwd
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.pear import (
    PEAR_DEFAULT_RESPONSE_POLICY,
    PEAR_REASONING_EFFORTS,
    PearActiveSessionSummary,
    PearAddressingIntentClassifier,
    PearEmbeddedBotHandler,
    PearPendingSteeringEvent,
    PearWorkerRunStatus,
    PearWorkerRuntime,
    build_pear_active_session_summary,
    build_pear_active_session_summary_from_messages,
    get_pear_session_payload,
    normalize_pear_response_policy,
    pear_message_id,
    pear_message_stream,
    pear_message_topic,
    trim_pear_text,
)
from zerver.lib.pear_agent import (
    PearCodexAppServerToolRunner,
    PearDisabledAgentRunner,
    PearGeminiInteractionsAddressingIntentClassifier,
    PearProcessIsolation,
)
from zerver.lib.pear_skills import reconcile_pear_codex_skills
from zerver.lib.queue import queue_json_publish_rollback_unsafe
from zerver.lib.streams import access_stream_by_name
from zerver.lib.topic import messages_for_topic
from zerver.models import Stream
from zerver.models.bots import PearProject, PearWorkerRun, PearWorkerRunEvent, PearWorkspaceNode
from zerver.models.users import UserProfile

PEAR_WORKER_QUEUE_NAME = "pear_worker"
PEAR_MONITOR_WORKER_QUEUE_NAME = "pear_monitor_worker"
PEAR_ADDRESSING_QUEUE_NAME = "pear_addressing"
PEAR_PRESENTATION_QUEUE_NAME = "pear_presentation"
PEAR_RUN_ATTENTION_TTL_SECONDS = 15
PEAR_PRESENTATION_CADENCE_SECONDS = 3
PEAR_WORKER_TOPIC_HISTORY_LIMIT = 200
PEAR_WORKER_RUN_ACTIVE_STATUSES = frozenset(
    (PearWorkerRun.QUEUED, PearWorkerRun.RUNNING, PearWorkerRun.DONE)
)
PEAR_WORKER_RUN_BUSY_STATUSES = frozenset((PearWorkerRun.QUEUED, PearWorkerRun.RUNNING))
PEAR_WORKER_RUN_TERMINAL_STATUSES = frozenset((PearWorkerRun.DONE, PearWorkerRun.CANCELLED))
PEAR_WORKER_MAX_CLAIM_ATTEMPTS = 2
PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS = 15
PEAR_RUN_LEGAL_TRANSITIONS: dict[PearWorkerRunStatus, frozenset[PearWorkerRunStatus]] = {
    PearWorkerRun.QUEUED: frozenset(
        (PearWorkerRun.RUNNING, PearWorkerRun.FAILED, PearWorkerRun.CANCELLED)
    ),
    PearWorkerRun.RUNNING: frozenset(
        (PearWorkerRun.DONE, PearWorkerRun.FAILED, PearWorkerRun.CANCELLED)
    ),
    PearWorkerRun.FAILED: frozenset((PearWorkerRun.QUEUED,)),
    PearWorkerRun.DONE: frozenset(),
    PearWorkerRun.CANCELLED: frozenset(),
}


def pear_run_attention_cache_key(run_id: int) -> str:
    return f"pear:run-attention:{run_id}"


def pear_run_presentation_gate_cache_key(run_id: int) -> str:
    return f"pear:run-presentation-gate:{run_id}"


def mark_pear_run_attended(run_id: int) -> None:
    cache.set(pear_run_attention_cache_key(run_id), True, PEAR_RUN_ATTENTION_TTL_SECONDS)


def pear_run_is_attended(run_id: int) -> bool:
    return cache.get(pear_run_attention_cache_key(run_id)) is True


def maybe_enqueue_pear_run_presentation(run_id: int, source_event_id: int) -> bool:
    if not pear_run_is_attended(run_id):
        return False
    if not cache.add(
        pear_run_presentation_gate_cache_key(run_id),
        True,
        PEAR_PRESENTATION_CADENCE_SECONDS,
    ):
        return False
    queue_json_publish_rollback_unsafe(
        PEAR_PRESENTATION_QUEUE_NAME,
        {"run_id": run_id, "source_event_id": source_event_id},
    )
    return True


PEAR_REPOSITORY_CLONE_TIMEOUT_SECONDS = 180
PEAR_GIT_STATUS_DELTA_TTL_SECONDS = 3
pear_git_status_delta_cache: dict[
    tuple[str, str, str],
    tuple[float, dict[str, object]],
] = {}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PearTopicRenameRefactorResult:
    stream_name: str
    topic_name: str
    message: str


def pear_worker_stale_after_seconds() -> int:
    return settings.PEAR_WORKER_TIMEOUT_SECONDS + 300


def pear_worker_run_is_stale(run: PearWorkerRun, *, current_time: datetime) -> bool:
    last_seen = run.heartbeat_at or run.date_updated
    return last_seen <= current_time - timedelta(seconds=pear_worker_stale_after_seconds())


def pear_workspace_is_paused(user_profile: UserProfile, *, stream: str, topic: str) -> bool:
    if not stream:
        return False
    return PearWorkspaceNode.objects.filter(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
        is_paused=True,
    ).exists()


def set_pear_workspace_paused(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    is_paused: bool,
) -> None:
    if not stream:
        return
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)
    if workspace_node.is_paused == is_paused:
        return
    workspace_node.is_paused = is_paused
    workspace_node.date_updated = timezone_now()
    workspace_node.save(update_fields=["is_paused", "date_updated"])


def pear_workspace_response_policy(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return PEAR_DEFAULT_RESPONSE_POLICY
    workspace_node = PearWorkspaceNode.objects.filter(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
    ).first()
    if workspace_node is None:
        return PEAR_DEFAULT_RESPONSE_POLICY
    return normalize_pear_response_policy(workspace_node.response_policy)


def set_pear_workspace_response_policy(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    response_policy: str,
) -> None:
    if not stream:
        return
    clean_response_policy = normalize_pear_response_policy(response_policy)
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)
    if workspace_node.response_policy == clean_response_policy:
        return
    workspace_node.response_policy = clean_response_policy
    workspace_node.date_updated = timezone_now()
    workspace_node.save(update_fields=["response_policy", "date_updated"])


def settle_orphaned_pear_run_message(run: PearWorkerRun) -> None:
    """Best-effort: settle an interrupted run's visible progress message."""
    message_id = run.result_message_id
    if message_id is None:
        return
    try:
        # Imported lazily: pear.py imports from this module.
        from zerver.lib.bot_lib import EmbeddedBotHandler
        from zerver.lib.pear import update_pear_worker_status_message

        bot_handler = EmbeddedBotHandler(run.bot_profile)
        update_pear_worker_status_message(
            bot_handler,
            progress_message_id=message_id,
            content="Run interrupted by a service restart.",
            rendering_only=True,
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to settle orphaned Pear run message %s.", message_id, exc_info=True
        )


def reap_stale_pear_worker_runs(
    *,
    realm: Any | None = None,
    bot_profile: UserProfile | None = None,
    stream: str | None = None,
    topic: str | None = None,
    assume_orphaned: bool = False,
    session_kinds: Sequence[str] | None = None,
) -> int:
    """Fail RUNNING runs that no longer have a live worker behind them.

    With assume_orphaned=True (worker startup), age is ignored: the pear
    queue is single-consumer, so at boot no run can legitimately be
    RUNNING — a restart mid-turn otherwise leaves a zombie record that
    blocks the deploy health gate for the full staleness window.
    """
    current_time = timezone_now()
    cutoff = current_time - timedelta(seconds=pear_worker_stale_after_seconds())
    stale_runs = PearWorkerRun.objects.filter(status=PearWorkerRun.RUNNING)
    if session_kinds is not None:
        stale_runs = stale_runs.filter(session_kind__in=list(session_kinds))
    if not assume_orphaned:
        stale_runs = stale_runs.filter(
            Q(heartbeat_at__isnull=False, heartbeat_at__lte=cutoff)
            | Q(heartbeat_at__isnull=True, date_updated__lte=cutoff)
        )
    if realm is not None:
        stale_runs = stale_runs.filter(realm=realm)
    if bot_profile is not None:
        stale_runs = stale_runs.filter(bot_profile=bot_profile)
    if stream is not None:
        stale_runs = stale_runs.filter(stream_name=stream)
    if topic is not None:
        stale_runs = stale_runs.filter(topic_name=topic)

    stale_run_ids = list(stale_runs.values_list("id", flat=True)[:100])
    reaped_count = 0
    settled_runs: list[PearWorkerRun] = []
    store = PearDatabaseWorkerRunStore()
    for run_id in stale_run_ids:
        with transaction.atomic(savepoint=False):
            run = (
                PearWorkerRun.objects.select_for_update()
                .filter(id=run_id, status=PearWorkerRun.RUNNING)
                .first()
            )
            if run is None:
                continue
            if not assume_orphaned and not pear_worker_run_is_stale(run, current_time=current_time):
                continue
            if not store._transition(
                run,
                PearWorkerRun.FAILED,
                stream=run.stream_name,
                topic=run.topic_name,
                result_message_id=run.result_message_id,
                error=(
                    "Pear worker run was orphaned by a worker restart."
                    if assume_orphaned
                    else "Pear worker run became stale and was recovered."
                ),
                allow_requeue=False,
            ):
                continue
            store._record_event(
                run,
                "orphan_reaped" if assume_orphaned else "stale_reaped",
                summary=(
                    "Recovered Pear worker run orphaned by restart."
                    if assume_orphaned
                    else "Recovered stale Pear worker run."
                ),
                payload={
                    "stale_after_seconds": pear_worker_stale_after_seconds(),
                    "heartbeat_at": run.heartbeat_at.isoformat()
                    if run.heartbeat_at is not None
                    else None,
                },
            )
            settled_runs.append(run)
            reaped_count += 1
    # Message edits happen outside the row locks.
    for run in settled_runs:
        settle_orphaned_pear_run_message(run)
    return reaped_count


def latest_pear_codex_run_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    before_message_id: int,
) -> PearWorkerRun | None:
    runs = PearWorkerRun.objects.filter(
        bot_profile=user_profile,
        stream_name=stream,
        topic_name=topic,
        session_kind=PearWorkerRun.SHARED,
    ).exclude(codex_thread_id="")
    if before_message_id > 0:
        runs = runs.filter(trigger_message_id__lt=before_message_id)
    return runs.order_by("-trigger_message_id", "-id").first()


def pear_default_worker_model() -> str:
    return str(settings.PEAR_WORKER_MODEL or "")


def pear_default_worker_reasoning_effort() -> str:
    effort = str(settings.PEAR_WORKER_REASONING_EFFORT or "").strip()
    if effort in PEAR_REASONING_EFFORTS:
        return effort
    return "medium"


def pear_default_worker_workdir() -> str:
    return str(settings.PEAR_WORKER_WORKDIR or Path.cwd())


def pear_configured_worker_sandbox() -> str:
    return str(settings.PEAR_WORKER_SANDBOX or "read-only")


def pear_topic_isolation_backend() -> str:
    return str(settings.PEAR_TOPIC_ISOLATION_BACKEND or "codex-sandbox")


def pear_topic_os_user_isolation_enabled() -> bool:
    return pear_topic_isolation_backend() == "os-user"


def pear_topic_workdir_root() -> Path:
    base_workdir = pear_normalize_path(pear_default_worker_workdir())
    default_root = (
        Path("/srv/pear-workspaces")
        if pear_topic_os_user_isolation_enabled()
        else base_workdir.parent / "pear-workspaces"
    )
    configured_root = settings.PEAR_TOPIC_WORKDIR_ROOT
    if isinstance(configured_root, str) and configured_root.strip():
        root = pear_normalize_path(configured_root.strip())
        if root == base_workdir or root.is_relative_to(base_workdir):
            return default_root
        if pear_topic_os_user_isolation_enabled():
            service_home = pear_normalize_path(Path.home())
            if root == service_home or root.is_relative_to(service_home):
                return default_root
        return Path(configured_root.strip())
    return default_root


def pear_topic_workdir_slug(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    human_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", f"{stream}-{topic}").strip("-").lower()
    if not human_slug:
        human_slug = "topic"
    digest = hashlib.sha256(f"{user_profile.realm_id}\0{stream}\0{topic}".encode()).hexdigest()[:12]
    return f"{human_slug[:48]}-{digest}"


def pear_git_branch_name_is_valid(branch_name: str) -> bool:
    if not branch_name or branch_name.startswith("-"):
        return False
    try:
        completed = subprocess.run(
            ["git", "check-ref-format", "--branch", branch_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return re.fullmatch(r"[A-Za-z0-9._/-]+", branch_name) is not None
    return completed.returncode == 0


def pear_topic_branch_name(topic: str) -> str:
    raw_topic = topic.strip()
    if pear_git_branch_name_is_valid(raw_topic):
        return raw_topic

    branch_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_topic).strip("-.")
    branch_name = re.sub(r"\.{2,}", ".", branch_name)
    if branch_name.endswith(".lock"):
        branch_name = branch_name.removesuffix(".lock")
    if not branch_name:
        branch_name = "topic"
    if pear_git_branch_name_is_valid(branch_name):
        return branch_name

    digest = hashlib.sha256(raw_topic.encode()).hexdigest()[:12]
    return f"topic-{digest}"


def pear_project_cache_slug(
    user_profile: UserProfile,
    *,
    stream: str,
) -> str:
    human_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", stream).strip("-").lower()
    if not human_slug:
        human_slug = "project"
    digest = hashlib.sha256(f"{user_profile.realm_id}\0{stream}".encode()).hexdigest()[:12]
    return f"{human_slug[:48]}-{digest}"


def pear_expected_project_cache_path(
    user_profile: UserProfile,
    *,
    stream: str,
) -> Path:
    return (
        pear_topic_workdir_root()
        / "projects"
        / pear_project_cache_slug(
            user_profile,
            stream=stream,
        )
    )


def pear_project_for_stream(
    user_profile: UserProfile,
    *,
    stream: str,
    repository_url: str = "",
    repo_cache_path: str = "",
) -> PearProject | None:
    if not stream:
        return None
    try:
        stream_obj, _sub = access_stream_by_name(user_profile, stream)
    except JsonableError:
        return None

    defaults = {
        "repository_url": repository_url,
        "repo_cache_path": repo_cache_path,
        "clone_status": PearProject.READY if repository_url else PearProject.UNCONFIGURED,
    }
    project, _created = PearProject.objects.get_or_create(
        realm=user_profile.realm,
        stream=stream_obj,
        defaults=defaults,
    )
    update_fields = []
    if repository_url and project.repository_url != repository_url:
        project.repository_url = repository_url
        update_fields.append("repository_url")
    if repo_cache_path and project.repo_cache_path != repo_cache_path:
        project.repo_cache_path = repo_cache_path
        update_fields.append("repo_cache_path")
    if repository_url and project.clone_status == PearProject.UNCONFIGURED:
        project.clone_status = PearProject.READY
        update_fields.append("clone_status")
    if update_fields:
        project.date_updated = timezone_now()
        project.save(update_fields=[*update_fields, "date_updated"])
    return project


def pear_workspace_node_default_branch(
    *,
    project: PearProject | None = None,
    parent_node: PearWorkspaceNode | None = None,
    topic: str = "",
) -> str:
    # pear_topic_branch_name never returns an empty string (it falls back
    # to the literal "topic"), so blank candidates must be skipped here or
    # an unset project base ref would name every workspace branch "topic".
    # The topic outranks the parent branch: worktrees sharing one base
    # repository need per-topic branch names, or every sibling topic
    # collides and falls into the digest-suffix retry.
    candidates = (
        project.default_base_ref if project is not None else "",
        topic,
        parent_node.branch_name if parent_node is not None else "",
        "main",
    )
    for candidate in candidates:
        if not candidate.strip():
            continue
        return pear_topic_branch_name(candidate)
    return "main"


def pear_expected_topic_workdir(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> Path:
    return pear_topic_workdir_root() / pear_topic_workdir_slug(
        user_profile,
        stream=stream,
        topic=topic,
    )


def pear_topic_isolation_username_for_realm(
    realm_id: int,
    *,
    stream: str,
    topic: str,
) -> str:
    digest = hashlib.sha256(f"{realm_id}\0{stream}\0{topic}".encode()).hexdigest()[:24]
    return f"pear-{digest}"


def pear_topic_isolation_username(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    return pear_topic_isolation_username_for_realm(
        user_profile.realm_id,
        stream=stream,
        topic=topic,
    )


def pear_topic_isolation_user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
    except KeyError:
        return False
    return True


def pear_topic_process_isolation(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> PearProcessIsolation | None:
    if not pear_topic_os_user_isolation_enabled():
        return None
    username = pear_topic_isolation_username(user_profile, stream=stream, topic=topic)
    home = f"/home/{username}"
    return PearProcessIsolation(
        username=username,
        home=home,
        codex_home=f"{home}/.codex",
    )


def pear_shared_codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME") or Path.home() / ".codex").expanduser()


def pear_codex_skills_source_dir() -> Path | None:
    configured_path = settings.PEAR_CODEX_SKILLS_DIR
    if isinstance(configured_path, str) and configured_path.strip():
        return pear_normalize_path(configured_path)
    deploy_root = getattr(settings, "DEPLOY_ROOT", None)
    if isinstance(deploy_root, str) and deploy_root.strip():
        return pear_normalize_path(Path(deploy_root).parent / "pear" / "skills")
    return None


def pear_provision_codex_skills(codex_home: str | Path) -> list[str]:
    source_dir = pear_codex_skills_source_dir()
    if source_dir is None or not source_dir.is_dir():
        return []

    return reconcile_pear_codex_skills(source_dir, codex_home)


def pear_normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def pear_workdir_is_under_topic_root(workdir: str | Path) -> bool:
    workdir_path = pear_normalize_path(workdir)
    topic_root = pear_normalize_path(pear_topic_workdir_root())
    return workdir_path == topic_root or workdir_path.is_relative_to(topic_root)


def pear_workdir_uses_shared_git_admin(workdir: str | Path) -> bool:
    try:
        return (Path(workdir) / ".git").is_file()
    except PermissionError:
        return False


def pear_worker_runtime_isolation_error(workdir: str | None) -> str | None:
    if workdir is None or not workdir.strip():
        return "Pear refused to start Codex because no isolated topic worktree was selected."
    if pear_workdir_is_under_topic_root(workdir):
        return None
    return (
        "Pear refused to start Codex outside the Pear topic workspace root. "
        "Pick or create a topic-scoped worktree before starting the agent."
    )


def pear_worker_sandbox_for_workdir(workdir: str | None) -> str:
    configured_sandbox = pear_configured_worker_sandbox()
    if pear_worker_runtime_isolation_error(workdir) is not None:
        return "read-only"
    if pear_topic_os_user_isolation_enabled():
        return "danger-full-access"
    if configured_sandbox == "danger-full-access":
        return "workspace-write"
    return configured_sandbox


def pear_worker_process_isolation_error(
    process_isolation: PearProcessIsolation | None,
    *,
    workdir: str,
) -> str | None:
    if process_isolation is None:
        return None
    helper = str(settings.PEAR_TOPIC_ISOLATION_HELPER or "")
    if not helper:
        return "Pear per-topic OS-user isolation is enabled, but no isolation helper is configured."
    if pear_worker_runtime_isolation_error(workdir) is not None:
        return (
            "Pear per-topic OS-user isolation refused to prepare a workdir outside "
            "the Pear topic workspace root."
        )
    if pear_workdir_uses_shared_git_admin(workdir):
        return (
            "Pear per-topic OS-user isolation refused to launch from a shared Git "
            "worktree. Recreate this topic workspace as an independent clone first."
        )

    codex_home_source = str(pear_shared_codex_home())
    try:
        pear_provision_codex_skills(codex_home_source)
    except OSError as error:
        return f"Pear Codex skills provisioning failed: {error}"
    command = [
        "sudo",
        "-n",
        helper,
        "--user",
        process_isolation.username,
        "--workdir",
        workdir,
        "--topic-root",
        str(pear_topic_workdir_root()),
        "--codex-home-source",
        codex_home_source,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return (
            "Pear per-topic OS-user isolation is enabled, but sudo or the isolation "
            "helper is not installed."
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"Pear per-topic OS-user isolation setup failed: {error}"
    if completed.returncode != 0:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        return (
            "Pear per-topic OS-user isolation setup failed: "
            f"{output or f'exit code {completed.returncode}'}"
        )
    return None


def pear_workspace_workdir_needs_repair(workspace_node: PearWorkspaceNode) -> bool:
    workdir = workspace_node.workdir.strip()
    if not workdir:
        return True
    workdir_path = pear_normalize_path(workdir)
    topic_root = pear_normalize_path(pear_topic_workdir_root())
    if workdir_path != topic_root and not workdir_path.is_relative_to(topic_root):
        return True
    if not workspace_node.topic_name:
        return False
    base_workdir = pear_normalize_path(pear_default_worker_workdir())
    return workdir_path == base_workdir or workdir_path.is_relative_to(base_workdir)


def pear_git_branch_exists(base_path: Path, branch_name: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(base_path), "rev-parse", "--verify", f"refs/heads/{branch_name}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    return completed.returncode == 0


def pear_path_is_git_worktree(path: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def pear_git_current_branch_name(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return pear_topic_branch_name(completed.stdout.strip())


def pear_try_add_worktree(
    base_path: Path,
    path: Path,
    *,
    branch_name: str,
) -> str | None:
    if pear_git_branch_exists(base_path, branch_name):
        args = ["git", "-C", str(base_path), "worktree", "add", str(path), branch_name]
    else:
        args = [
            "git",
            "-C",
            str(base_path),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(path),
            "HEAD",
        ]
    completed = subprocess.run(
        args,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    if completed.returncode != 0:
        return None
    return branch_name


def pear_switch_workdir_to_branch(path: Path, branch_name: str) -> bool:
    try:
        if pear_git_branch_exists(path, branch_name):
            args = ["git", "-C", str(path), "switch", branch_name]
        else:
            args = ["git", "-C", str(path), "switch", "-c", branch_name]
        completed = subprocess.run(
            args,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def pear_workspace_relative_path(workdir: str, relative_path: str) -> Path | None:
    """Resolve a client-supplied path, refusing anything that could
    escape the workdir (or poke at .git internals)."""
    normalized = posixpath.normpath(relative_path.strip())
    if (
        normalized in ("", ".", "..")
        or normalized.startswith(("../", ".git/"))
        or posixpath.isabs(normalized)
        or "\\" in normalized
        or normalized == ".git"
    ):
        return None
    return Path(workdir) / normalized


def pear_resolve_file_path(
    path: Path,
    *,
    isolation_username: str | None = None,
) -> Path | None:
    """Resolve an existing regular file without crossing the OS-user boundary.

    Topic workdirs are intentionally mode 0700.  The service user can name the
    workdir but cannot traverse it, so resolving artifact media as the service
    user produces a misleading ``PermissionError``.  Resolve as the topic user
    and return the canonical path to the service, which can then enforce that
    it remains below the trusted workspace root before reading it through the
    same isolation boundary.
    """
    if isolation_username is None:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        return resolved if resolved.is_file() else None
    command = [
        "sudo",
        "-n",
        "-u",
        isolation_username,
        "/usr/bin/env",
        "python3",
        "-c",
        (
            "import json, sys; from pathlib import Path; "
            "path = Path(sys.argv[1]).resolve(strict=True); "
            "sys.stdout.write(json.dumps(str(path)) if path.is_file() else '')"
        ),
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"Could not resolve {path}: {error}") from error
    if completed.returncode != 0 or not completed.stdout:
        return None
    try:
        resolved_value = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise OSError(f"Could not decode resolved path for {path}") from error
    if not isinstance(resolved_value, str) or not Path(resolved_value).is_absolute():
        raise OSError(f"Could not resolve {path}: invalid path returned")
    return Path(resolved_value)


def pear_read_text_path(path: Path, *, isolation_username: str | None = None) -> str:
    if isolation_username is None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as error:
            raise OSError(f"Could not read {path}: {error}") from error
    command = [
        "sudo",
        "-n",
        "-u",
        isolation_username,
        "/usr/bin/env",
        "python3",
        "-c",
        (
            "from pathlib import Path; import sys; "
            "path = Path(sys.argv[1]); "
            "sys.stdout.write(path.read_text(encoding='utf-8') if path.exists() else '')"
        ),
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"Could not read {path}: {error}") from error
    if completed.returncode != 0:
        error_text = completed.stderr.strip()
        if error_text:
            raise OSError(f"Could not read {path}: {error_text}")
        raise OSError(f"Could not read {path}: sudo exited {completed.returncode}")
    return completed.stdout


def pear_read_bytes_path(
    path: Path,
    *,
    isolation_username: str | None = None,
    max_bytes: int = 4_000_000,
) -> bytes:
    """Read a worktree file as bytes (isolation-aware), for artifact
    uploads. Returns b"" for missing files; raises OSError otherwise.
    Reads at most max_bytes + 1 so callers can detect oversize."""
    if isolation_username is None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError as error:
            raise OSError(f"Could not read {path}: {error}") from error
        return data[: max_bytes + 1]
    command = [
        "sudo",
        "-n",
        "-u",
        isolation_username,
        "/usr/bin/env",
        "python3",
        "-c",
        (
            "import base64, sys; from pathlib import Path; "
            "path = Path(sys.argv[1]); limit = int(sys.argv[2]); "
            "sys.stdout.write(base64.b64encode(path.read_bytes()[:limit]).decode() "
            "if path.exists() else '')"
        ),
        str(path),
        str(max_bytes + 1),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"Could not read {path}: {error}") from error
    if completed.returncode != 0:
        raise OSError(f"Could not read {path}: {completed.stderr.strip()}")
    import base64

    try:
        return base64.b64decode(completed.stdout.strip() or "")
    except ValueError as error:
        raise OSError(f"Could not decode {path}: {error}") from error


def pear_write_text_path(
    path: Path,
    content: str,
    *,
    isolation_username: str | None = None,
) -> bool:
    if isolation_username is None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError:
            return False
        return True
    command = [
        "sudo",
        "-n",
        "-u",
        isolation_username,
        "/usr/bin/env",
        "python3",
        "-c",
        (
            "from pathlib import Path; import sys; "
            "path = Path(sys.argv[1]); "
            "path.parent.mkdir(parents=True, exist_ok=True); "
            "path.write_text(sys.stdin.read(), encoding='utf-8')"
        ),
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            input=content,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def ensure_pear_workspace_node(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    parent_node: PearWorkspaceNode | None = None,
) -> PearWorkspaceNode:
    if parent_node is None and topic:
        parent_node = PearWorkspaceNode.objects.filter(
            realm=user_profile.realm,
            stream_name=stream,
            topic_name="",
        ).first()
    project = (
        parent_node.project
        if parent_node is not None and parent_node.project_id is not None
        else pear_project_for_stream(user_profile, stream=stream)
    )
    expected_workdir = str(pear_expected_topic_workdir(user_profile, stream=stream, topic=topic))
    workspace_node, _created = PearWorkspaceNode.objects.get_or_create(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
        defaults={
            "project": project,
            "parent_node": parent_node,
            "workdir": expected_workdir,
            "branch_name": pear_workspace_node_default_branch(
                project=project,
                parent_node=parent_node,
                topic=topic,
            ),
            "isolation_username": (
                pear_topic_isolation_username(user_profile, stream=stream, topic=topic)
                if pear_topic_os_user_isolation_enabled()
                else ""
            ),
        },
    )
    update_fields = []
    if project is not None and workspace_node.project_id != project.id:
        workspace_node.project = project
        update_fields.append("project")
    if parent_node is not None and parent_node.id not in (
        workspace_node.id,
        workspace_node.parent_node_id,
    ):
        workspace_node.parent_node = parent_node
        update_fields.append("parent_node")
    if pear_workspace_workdir_needs_repair(workspace_node):
        workspace_node.workdir = expected_workdir
        update_fields.append("workdir")
    if not workspace_node.branch_name:
        workspace_node.branch_name = pear_workspace_node_default_branch(
            project=project,
            parent_node=parent_node,
            topic=topic,
        )
        update_fields.append("branch_name")
    expected_isolation_username = (
        pear_topic_isolation_username(user_profile, stream=stream, topic=topic)
        if pear_topic_os_user_isolation_enabled()
        else ""
    )
    if workspace_node.isolation_username != expected_isolation_username:
        workspace_node.isolation_username = expected_isolation_username
        update_fields.append("isolation_username")
    if update_fields:
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=[*update_fields, "date_updated"])
    repository_url = (
        project.repository_url.strip()
        if project is not None and project.repository_url.strip()
        else None
    )
    base_workdir = (
        project.repo_cache_path.strip()
        if project is not None and project.repo_cache_path.strip() and topic
        else None
    )
    effective_branch = ensure_pear_topic_workdir(
        workspace_node.workdir,
        base_workdir=base_workdir,
        repository_url=repository_url,
        branch_name=workspace_node.branch_name,
    )
    effective_branch_name = (
        pear_topic_branch_name(effective_branch) if isinstance(effective_branch, str) else ""
    )
    if effective_branch_name and workspace_node.branch_name != effective_branch_name:
        workspace_node.branch_name = effective_branch_name
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=["branch_name", "date_updated"])
    return workspace_node


def ensure_pear_child_workspace_node(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    parent_stream: str,
    parent_topic: str,
) -> PearWorkspaceNode:
    parent_node = None
    if parent_stream and parent_topic is not None:
        parent_node = ensure_pear_workspace_node(
            user_profile,
            stream=parent_stream,
            topic=parent_topic,
        )
    return ensure_pear_workspace_node(
        user_profile,
        stream=stream,
        topic=topic,
        parent_node=parent_node,
    )


def pear_ensure_workdir_git_identity(path: Path) -> None:
    # Fresh scratch repos and isolated clones have no author identity, so
    # the agent's very first commit fails and every session burns a
    # failed-tool round on `git config`.  Provision a local identity up
    # front, but never override one the repository already has.
    try:
        existing = subprocess.run(
            ["git", "-C", str(path), "config", "user.email"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "Pear"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "config",
                "user.email",
                f"pear-worker@{settings.EXTERNAL_HOST.split(':')[0]}",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass


def ensure_pear_topic_workdir(
    workdir: str,
    *,
    base_workdir: str | Path | None = None,
    repository_url: str | None = None,
    branch_name: str | None = None,
) -> str:
    branch = pear_provision_topic_workdir(
        workdir,
        base_workdir=base_workdir,
        repository_url=repository_url,
        branch_name=branch_name,
    )
    path = Path(workdir)
    if pear_path_is_git_worktree(path):
        pear_ensure_workdir_git_identity(path)
    return branch


def pear_provision_topic_workdir(
    workdir: str,
    *,
    base_workdir: str | Path | None = None,
    repository_url: str | None = None,
    branch_name: str | None = None,
) -> str:
    path = Path(workdir)
    branch_name = pear_topic_branch_name(branch_name or "main")
    if pear_topic_os_user_isolation_enabled():
        if path.exists():
            if pear_path_is_git_worktree(path):
                return pear_git_current_branch_name(path) or branch_name
            with contextlib.suppress(FileNotFoundError, OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["git", "-C", str(path), "init", "--quiet"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            pear_switch_workdir_to_branch(path, branch_name)
            return pear_git_current_branch_name(path) or branch_name
        if repository_url:
            clone_or_update_pear_repository_workdir(repository_url, path)
            pear_switch_workdir_to_branch(path, branch_name)
            return pear_git_current_branch_name(path) or branch_name
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError, OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["git", "-C", str(path), "init", "--quiet"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        pear_switch_workdir_to_branch(path, branch_name)
        return pear_git_current_branch_name(path) or branch_name
    base_path = (
        Path(base_workdir) if base_workdir is not None else Path(pear_default_worker_workdir())
    )
    base_is_git_worktree = pear_path_is_git_worktree(base_path)
    path_was_empty = not ((path / ".git").exists() or (path.exists() and any(path.iterdir())))
    worktree_added_branch: str | None = None
    if path_was_empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            worktree_added_branch = pear_try_add_worktree(
                base_path,
                path,
                branch_name=branch_name,
            )
            if worktree_added_branch is None:
                digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
                worktree_added_branch = pear_try_add_worktree(
                    base_path,
                    path,
                    branch_name=f"{branch_name}-{digest}",
                )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
    if not path_was_empty and pear_path_is_git_worktree(path):
        return pear_git_current_branch_name(path) or branch_name
    if base_is_git_worktree and not ((path / ".git").exists() or worktree_added_branch):
        return worktree_added_branch or branch_name
    path.mkdir(parents=True, exist_ok=True)
    if path_was_empty and worktree_added_branch is None and pear_path_is_git_worktree(path):
        pear_switch_workdir_to_branch(path, branch_name)
    return pear_git_current_branch_name(path) or worktree_added_branch or branch_name


def clean_pear_repository_url(repository_url: str) -> str:
    clean_url = repository_url.strip()
    if not clean_url:
        return ""
    if len(clean_url) > 500 or any(character.isspace() for character in clean_url):
        raise JsonableError(_("Enter a valid Git repository URL."))
    if clean_url.startswith("git@") and ":" in clean_url:
        return clean_url

    parsed_url = urlparse(clean_url)
    # file:// is intentionally excluded: with per-topic os-user isolation it
    # would let a topic clone arbitrary local paths (other topics' workdirs or
    # host files).  Only remote transports are accepted.
    allowed_schemes = {"git", "http", "https", "ssh"}
    if parsed_url.scheme not in allowed_schemes:
        raise JsonableError(_("Enter a valid Git repository URL."))
    if not parsed_url.netloc or not parsed_url.path.strip("/"):
        raise JsonableError(_("Enter a valid Git repository URL."))
    return clean_url


def clone_or_update_pear_repository_workdir(repository_url: str, workdir: Path) -> None:
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if (workdir / ".git").exists():
        try:
            completed = subprocess.run(
                ["git", "-C", str(workdir), "config", "--get", "remote.origin.url"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            completed = None
        current_url = completed.stdout.strip() if completed is not None else ""
        if current_url and current_url != repository_url:
            raise JsonableError(_("This channel already has a different repository checkout."))
        subprocess.run(
            ["git", "-C", str(workdir), "fetch", "--quiet", "--prune", "origin"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PEAR_REPOSITORY_CLONE_TIMEOUT_SECONDS,
        )
        return

    if workdir.exists() and any(workdir.iterdir()):
        raise JsonableError(_("This channel already has a non-empty workspace."))

    try:
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-tags",
                repository_url,
                str(workdir),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=PEAR_REPOSITORY_CLONE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise JsonableError(_("Could not clone the Git repository.")) from error
    if completed.returncode != 0:
        if workdir.exists() and not any(workdir.iterdir()):
            workdir.rmdir()
        raise JsonableError(_("Could not clone the Git repository."))


def configure_pear_channel_repository_workspace(
    user_profile: UserProfile,
    *,
    stream: str,
    repository_url: str,
) -> PearWorkspaceNode:
    clean_url = clean_pear_repository_url(repository_url)
    workspace_node = PearWorkspaceNode.objects.filter(
        realm=user_profile.realm, stream_name=stream, topic_name=""
    ).first()
    workdir = (
        workspace_node.workdir.strip()
        if workspace_node is not None and workspace_node.workdir.strip()
        else str(pear_expected_topic_workdir(user_profile, stream=stream, topic=""))
    )
    clone_or_update_pear_repository_workdir(clean_url, Path(workdir))
    project = pear_project_for_stream(
        user_profile,
        stream=stream,
        repository_url=clean_url,
        repo_cache_path=workdir,
    )
    if project is None:
        raise JsonableError(_("Could not configure this channel workspace."))
    if workspace_node is None:
        workspace_node = PearWorkspaceNode.objects.create(
            realm=user_profile.realm,
            project=project,
            stream_name=stream,
            topic_name="",
            workdir=workdir,
            branch_name=pear_workspace_node_default_branch(project=project),
            isolation_username=(
                pear_topic_isolation_username(user_profile, stream=stream, topic="")
                if pear_topic_os_user_isolation_enabled()
                else ""
            ),
        )
        return workspace_node
    update_fields = []
    if workspace_node.project_id != project.id:
        workspace_node.project = project
        update_fields.append("project")
    if workspace_node.workdir != workdir:
        workspace_node.workdir = workdir
        update_fields.append("workdir")
    if not workspace_node.branch_name:
        workspace_node.branch_name = pear_workspace_node_default_branch(project=project)
        update_fields.append("branch_name")
    if update_fields:
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=[*update_fields, "date_updated"])
    return workspace_node


def maybe_refactor_pear_workspace_on_move(
    user_profile: UserProfile,
    *,
    old_stream: Stream,
    old_topic: str,
    new_stream: Stream,
    new_topic: str,
    moved_all_visible_messages: bool,
) -> PearTopicRenameRefactorResult | None:
    if old_stream.name == new_stream.name and old_topic == new_topic:
        return None
    workspace_node = PearWorkspaceNode.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream.name,
        topic_name=old_topic,
    ).first()
    if workspace_node is None:
        return None

    target = PearWorkspaceNode.objects.filter(
        realm=user_profile.realm,
        stream_name=new_stream.name,
        topic_name=new_topic,
    ).first()
    if target is not None and target.id != workspace_node.id:
        return PearTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Pear workspace warning: I did not move the Pear workspace context "
                "because the destination topic already has its own Pear context. "
                "Review the sidebar before starting more Codex work."
            ),
        )

    if not moved_all_visible_messages:
        return PearTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Pear workspace warning: I did not move the Pear workspace context "
                "because only part of the topic was moved. The old topic keeps the existing "
                "Codex thread/worktree; this topic will get a fresh context when Pear is used."
            ),
        )

    busy_run_exists = PearWorkerRun.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream.name,
        topic_name=old_topic,
        session_kind=PearWorkerRun.SHARED,
        status__in=PEAR_WORKER_RUN_BUSY_STATUSES,
    ).exists()
    if busy_run_exists:
        reap_stale_pear_worker_runs(
            realm=user_profile.realm,
            stream=old_stream.name,
            topic=old_topic,
        )
        busy_run_exists = PearWorkerRun.objects.filter(
            realm=user_profile.realm,
            stream_name=old_stream.name,
            topic_name=old_topic,
            session_kind=PearWorkerRun.SHARED,
            status__in=PEAR_WORKER_RUN_BUSY_STATUSES,
        ).exists()
    if busy_run_exists:
        return PearTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Pear workspace warning: I did not rename the Pear workspace context "
                "because the old topic still has an active Pear worker run. Wait for it to "
                "finish, then ask Pear to pin or migrate the workspace explicitly."
            ),
        )

    workspace_node.stream_name = new_stream.name
    workspace_node.topic_name = new_topic
    workspace_node.date_updated = timezone_now()
    workspace_node.save(update_fields=["stream_name", "topic_name", "date_updated"])

    return PearTopicRenameRefactorResult(
        stream_name=new_stream.name,
        topic_name=new_topic,
        message="Pear workspace label moved with this topic.",
    )


def maybe_refactor_pear_stream_workspaces_on_rename(
    user_profile: UserProfile,
    *,
    old_stream_name: str,
    new_stream_name: str,
) -> str | None:
    if old_stream_name == new_stream_name:
        return None

    workspace_nodes = PearWorkspaceNode.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream_name,
    )
    if not workspace_nodes.exists():
        return None

    if PearWorkspaceNode.objects.filter(
        realm=user_profile.realm,
        stream_name=new_stream_name,
    ).exists():
        return (
            "Pear workspace warning: I did not rename Pear channel context "
            "because the destination channel name already has Pear context. "
            "Review the sidebar before starting more Codex work."
        )

    timestamp = timezone_now()
    workspace_count = workspace_nodes.update(stream_name=new_stream_name, date_updated=timestamp)

    return (
        f"Pear channel context renamed from `{old_stream_name}` to `{new_stream_name}` "
        f"for {workspace_count} workspace layer(s). Existing worktree paths were kept unchanged; "
        "check the sidebar before starting Codex work."
    )


def pear_git_output(
    workdir: str,
    args: list[str],
    *,
    isolation_username: str | None = None,
) -> str:
    # Workspace nodes can legitimately outlive disposable topic worktrees
    # (for example, while serializing a cached node after a test or cleanup
    # removes its temporary repository).  Treat that lifecycle state as
    # unavailable rather than spawning Git against a path we already know is
    # gone and emitting a misleading infrastructure warning.
    workdir_path = Path(workdir)
    if not workdir_path.is_dir():
        return ""
    # Non-isolated workspaces are local to the service account, so we can
    # distinguish an existing-but-not-yet-initialized directory without a
    # subprocess. Isolated worktrees may be mode 700 and must be inspected by
    # Git under their owning user instead.
    if isolation_username is None and not (workdir_path / ".git").exists():
        return ""
    command = ["git", "-C", workdir, *args]
    if isolation_username is not None:
        command = ["sudo", "-n", "-u", isolation_username, "/usr/bin/env", *command]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except subprocess.TimeoutExpired as error:
        stderr = error.stderr
        stderr_text = (
            stderr.decode(errors="replace").strip()
            if isinstance(stderr, bytes)
            else (stderr or "").strip()
        )
        logger.warning(
            "Pear git command timed out in %s with args %s: %s",
            workdir,
            args,
            stderr_text,
            exc_info=True,
        )
        return ""
    except (FileNotFoundError, OSError):
        logger.warning(
            "Pear git command failed to start in %s with args %s.",
            workdir,
            args,
            exc_info=True,
        )
        return ""
    if completed.returncode != 0:
        logger.warning(
            "Pear git command exited with status %s in %s with args %s: %s",
            completed.returncode,
            workdir,
            args,
            (completed.stderr or "").strip(),
        )
        return ""
    return completed.stdout.strip()


PEAR_GIT_CHANGED_FILES_LIMIT = 50


def pear_git_changed_file_from_status_line(line: str) -> dict[str, str] | None:
    record_type, _, rest = line.partition(" ")
    if record_type == "?":
        return {"status": "??", "kind": "untracked", "path": rest}
    if record_type == "!":
        return None
    if record_type == "u":
        fields = rest.split(" ", 9)
        if len(fields) < 10:
            return None
        return {"status": "U", "kind": "changed", "path": fields[9]}
    if record_type == "1":
        fields = rest.split(" ", 7)
        if len(fields) < 8:
            return None
        xy, path = fields[0], fields[7]
    elif record_type == "2":
        fields = rest.split(" ", 8)
        if len(fields) < 9:
            return None
        xy, path = fields[0], fields[8].split("\t", 1)[0]
    else:
        return None
    status = xy.replace(".", "")
    if "R" in xy or "C" in xy:
        kind = "renamed"
    elif "A" in xy:
        kind = "added"
    elif "D" in xy:
        kind = "deleted"
    else:
        kind = "modified"
    return {"status": status, "kind": kind, "path": path}


def parse_pear_git_status_delta(
    status_output: str,
    *,
    workdir: str,
    topic: str = "",
) -> dict[str, object]:
    branch = ""
    head_short = ""
    upstream = ""
    ahead = 0
    behind = 0
    detached = False
    changed_count = 0
    changed_files: list[dict[str, str]] = []

    for line in status_output.splitlines():
        if not line.strip():
            continue
        if not line.startswith("# "):
            changed_count += 1
            changed_file = pear_git_changed_file_from_status_line(line)
            if changed_file is not None and len(changed_files) <= PEAR_GIT_CHANGED_FILES_LIMIT:
                changed_files.append(changed_file)
            continue
        if line.startswith("# branch.oid "):
            oid = line.removeprefix("# branch.oid ").strip()
            if oid and oid not in {"(initial)", "(unknown)"}:
                head_short = oid[:7]
            continue
        if line.startswith("# branch.head "):
            branch_head = line.removeprefix("# branch.head ").strip()
            if branch_head == "(detached)":
                detached = True
                branch = ""
            else:
                branch = branch_head
            continue
        if line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ").strip()
            continue
        if line.startswith("# branch.ab "):
            for part in line.removeprefix("# branch.ab ").split():
                if part.startswith("+"):
                    ahead = int(part[1:] or "0")
                elif part.startswith("-"):
                    behind = int(part[1:] or "0")

    return {
        "available": True,
        "workdir": workdir,
        "repo_name": Path(workdir).name,
        "worktree_name": Path(workdir).name,
        "branch": branch,
        "head": head_short,
        "head_short": head_short,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "dirty": changed_count > 0,
        "changed_count": changed_count,
        "changed_files": changed_files[:PEAR_GIT_CHANGED_FILES_LIMIT],
        "changed_files_truncated": len(changed_files) > PEAR_GIT_CHANGED_FILES_LIMIT,
        "detached": detached,
        "branch_matches_topic": bool(topic and not detached and branch == topic),
    }


def invalidate_pear_git_status_delta(workdir: str | None = None) -> None:
    if workdir is None:
        pear_git_status_delta_cache.clear()
        return
    for cache_key in list(pear_git_status_delta_cache):
        if cache_key[0] == workdir:
            del pear_git_status_delta_cache[cache_key]


def pear_git_status_delta(
    workdir: str,
    *,
    topic: str = "",
    isolation_username: str | None = None,
) -> dict[str, object]:
    if not workdir:
        return {"available": False}

    cache_key = (workdir, isolation_username or "", topic)
    current_time = time.monotonic()
    cached = pear_git_status_delta_cache.get(cache_key)
    if cached is not None:
        cached_at, delta = cached
        if current_time - cached_at < PEAR_GIT_STATUS_DELTA_TTL_SECONDS:
            return dict(delta)

    status_output = pear_git_output(
        workdir,
        ["status", "--porcelain=v2", "--branch"],
        isolation_username=isolation_username,
    )
    if not status_output:
        return {"available": False, "workdir": workdir}

    delta = parse_pear_git_status_delta(status_output, workdir=workdir, topic=topic)
    # The topic header's commit sentence (design §8.2): latest commit
    # identity plus the total count, fetched with the same isolation and
    # cached under the same TTL as the rest of the delta. An unborn HEAD
    # (branch.oid "(initial)" clears head_short) has no commits to
    # describe, so skip the reads instead of letting git fail them
    # noisily on every refresh of a freshly initialized workspace.
    if delta["head_short"]:
        log_output = pear_git_output(
            workdir,
            ["log", "-1", "--format=%h%x1f%s%x1f%an%x1f%ct"],
            isolation_username=isolation_username,
        )
        log_parts = log_output.strip().split("\x1f")
        if len(log_parts) == 4 and log_parts[0]:
            delta["last_commit"] = {
                "sha": log_parts[0],
                "subject": trim_pear_text(log_parts[1], limit=200),
                "author": trim_pear_text(log_parts[2], limit=80),
                "timestamp": int(log_parts[3]) if log_parts[3].isdigit() else None,
            }
        count_output = pear_git_output(
            workdir,
            ["rev-list", "--count", "HEAD"],
            isolation_username=isolation_username,
        ).strip()
        if count_output.isdigit():
            delta["commit_count"] = int(count_output)
    pear_git_status_delta_cache[cache_key] = (current_time, dict(delta))
    return delta


def pear_git_remote_label(remote_url: str, repo_root: str) -> str:
    clean_url = remote_url.strip()
    if clean_url:
        if clean_url.startswith("git@") and ":" in clean_url:
            return clean_url.split(":", 1)[1].removesuffix(".git").strip("/") or clean_url
        parsed_url = urlparse(clean_url)
        if parsed_url.scheme and parsed_url.netloc:
            return parsed_url.path.removesuffix(".git").strip("/") or parsed_url.netloc
        return Path(clean_url.removesuffix(".git")).name or clean_url
    return Path(repo_root).name


def build_pear_workspace_context(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> dict[str, str]:
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)
    parent_node = workspace_node.parent_node if workspace_node.parent_node_id is not None else None
    workdir = workspace_node.workdir.strip() or pear_default_worker_workdir()
    workspace_repo = (
        workspace_node.project.repository_url.strip()
        if workspace_node.project_id is not None
        else ""
    )
    parent_repo = (
        parent_node.project.repository_url.strip()
        if parent_node is not None and parent_node.project_id is not None
        else ""
    )
    repository = pear_logical_repository_label(
        stream=stream,
        context_repo=workspace_repo,
        parent_repo=parent_repo,
    )
    worktree = pear_logical_worktree_label(
        stream=stream,
        topic=topic,
        fallback=Path(workdir).name,
    )
    return {
        "repository": repository,
        "remote_url": pear_pinned_repository_value(workspace_repo)
        or pear_pinned_repository_value(parent_repo),
        "worktree": worktree,
        "workdir": workdir,
        "branch": (
            workspace_node.branch_name.strip()
            if workspace_node.branch_name.strip()
            else pear_workspace_node_default_branch()
        ),
        "head": "",
    }


def pear_repo_artifact_value_is_durable(repo_value: str) -> bool:
    value = repo_value.strip()
    if not value:
        return False
    if pear_repo_artifact_value_is_local_topic_path(value):
        return True
    if value.startswith("git@") and ":" in value:
        return True
    parsed_url = urlparse(value)
    if parsed_url.scheme in {"file", "git", "http", "https", "ssh"} and (
        parsed_url.netloc or parsed_url.path
    ):
        return True
    return re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\s+<[^>\n]+>)?", value) is not None


def pear_repo_artifact_value_is_local_topic_path(repo_value: str) -> bool:
    value = repo_value.strip()
    if not value or not Path(value).is_absolute():
        return False
    try:
        local_path = pear_normalize_path(value)
    except (OSError, RuntimeError):
        return False
    try:
        topic_root = pear_normalize_path(pear_topic_workdir_root())
        return pear_workdir_is_under_topic_root(local_path) and local_path != topic_root
    except OSError:
        return False


def pear_pinned_repository_value(repo_value: str) -> str:
    value = repo_value.strip()
    if not value:
        return ""
    if not pear_repo_artifact_value_is_durable(value):
        return ""
    if pear_repo_artifact_value_is_local_topic_path(value):
        return ""
    return value


def pear_logical_repository_label(
    *,
    stream: str,
    context_repo: str,
    parent_repo: str,
) -> str:
    for repo_value in (context_repo, parent_repo):
        pinned_repo = pear_pinned_repository_value(repo_value)
        if pinned_repo:
            return pinned_repo
    return stream


def pear_logical_worktree_label(*, stream: str, topic: str, fallback: str) -> str:
    if topic:
        return topic
    if stream:
        return stream
    return fallback


def pear_worker_model_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return pear_default_worker_model()
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)
    if workspace_node.model.strip():
        return workspace_node.model.strip()
    parent_node = workspace_node.parent_node if workspace_node.parent_node_id is not None else None
    if parent_node is not None and parent_node.model.strip():
        return parent_node.model.strip()
    return pear_default_worker_model()


def pear_worker_reasoning_effort_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return pear_default_worker_reasoning_effort()
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)
    if workspace_node.reasoning_effort.strip():
        return workspace_node.reasoning_effort.strip()
    parent_node = workspace_node.parent_node if workspace_node.parent_node_id is not None else None
    if parent_node is not None and parent_node.reasoning_effort.strip():
        return parent_node.reasoning_effort.strip()
    return pear_default_worker_reasoning_effort()


def pear_worker_workdir_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return pear_default_worker_workdir()
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)
    return workspace_node.workdir.strip() or pear_default_worker_workdir()


def pear_worker_sandbox_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    return pear_worker_sandbox_for_workdir(
        pear_worker_workdir_for_topic(user_profile, stream=stream, topic=topic)
    )


def build_pear_worker_runtime_from_settings(
    *,
    model_override: str | None = None,
    workdir_override: str | None = None,
    reasoning_effort_override: str | None = None,
    sandbox_override: str | None = None,
    process_isolation_override: PearProcessIsolation | None = None,
    process_isolation_error: str | None = None,
) -> PearWorkerRuntime:
    runtime = settings.PEAR_WORKER_AGENT_RUNTIME
    model = model_override if model_override is not None else settings.PEAR_WORKER_MODEL
    workdir = workdir_override if workdir_override is not None else settings.PEAR_WORKER_WORKDIR
    sandbox = (
        sandbox_override
        if sandbox_override is not None
        else pear_worker_sandbox_for_workdir(workdir)
    )
    reasoning_effort = (
        reasoning_effort_override
        if reasoning_effort_override is not None
        else pear_default_worker_reasoning_effort()
    )
    isolation_error = pear_worker_runtime_isolation_error(workdir)
    if runtime != "disabled" and isolation_error is not None:
        return PearWorkerRuntime(PearDisabledAgentRunner(isolation_error))
    if runtime != "disabled" and process_isolation_error is not None:
        return PearWorkerRuntime(PearDisabledAgentRunner(process_isolation_error))
    if runtime != "disabled" and process_isolation_override is None:
        try:
            pear_provision_codex_skills(pear_shared_codex_home())
        except OSError as error:
            return PearWorkerRuntime(
                PearDisabledAgentRunner(f"Pear Codex skills provisioning failed: {error}")
            )
    if runtime == "codex-app-server":
        return PearWorkerRuntime(
            PearCodexAppServerToolRunner(
                codex_bin=settings.PEAR_WORKER_CODEX_BIN,
                workdir=workdir,
                model=model,
                sandbox=sandbox,
                approval_policy=settings.PEAR_WORKER_APPROVAL_POLICY,
                timeout_seconds=settings.PEAR_WORKER_TIMEOUT_SECONDS,
                output_limit=settings.PEAR_WORKER_OUTPUT_LIMIT,
                service_tier=settings.PEAR_WORKER_SERVICE_TIER,
                fast_mode_enabled=settings.PEAR_WORKER_FAST_MODE,
                reasoning_effort=reasoning_effort,
                ephemeral=settings.PEAR_WORKER_CODEX_EPHEMERAL,
                process_isolation=process_isolation_override,
            )
        )
    if runtime != "disabled":
        return PearWorkerRuntime(
            PearDisabledAgentRunner(
                f"Pear worker agent runtime is unsupported: {runtime!r}. "
                "Use codex-app-server for worker turns; configure ambient "
                "message routing with PEAR_ADDRESSING_ROUTER_RUNTIME."
            )
        )
    return PearWorkerRuntime(
        PearDisabledAgentRunner(
            "Pear worker agent runtime is disabled. Set PEAR_WORKER_AGENT_RUNTIME "
            "to 'codex-app-server' for direct Codex app-server control."
        )
    )


def build_pear_addressing_intent_classifier_from_settings() -> (
    PearAddressingIntentClassifier | None
):
    runtime = settings.PEAR_ADDRESSING_ROUTER_RUNTIME
    if runtime == "gemini-interactions":
        return PearGeminiInteractionsAddressingIntentClassifier(
            api_key=settings.PEAR_GEMINI_INTERACTIONS_API_KEY,
            model=settings.PEAR_ADDRESSING_ROUTER_GEMINI_MODEL,
            timeout_seconds=4,
            output_limit=1000,
            url=settings.PEAR_GEMINI_INTERACTIONS_URL,
            thinking_level=settings.PEAR_ADDRESSING_ROUTER_GEMINI_THINKING_LEVEL,
        )
    return None


class PearDatabaseWorkerRunStore:
    def active_worker_run_message_id(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
        requesting_user_id: int | None = None,
    ) -> int | None:
        if not stream:
            return None

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        reap_stale_pear_worker_runs(
            realm=user_profile.realm,
            bot_profile=user_profile,
            stream=stream,
            topic=topic,
        )

        run = (
            PearWorkerRun.objects.filter(
                realm=user_profile.realm,
                bot_profile=user_profile,
                stream_name=stream,
                topic_name=topic,
                status__in=PEAR_WORKER_RUN_BUSY_STATUSES,
            )
            .order_by("-date_updated", "-id")
            .first()
        )
        if run is None:
            return None
        return run.trigger_message_id

    def register_progress_message(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        progress_message_id: int | None,
    ) -> None:
        if message_id <= 0 or progress_message_id is None:
            return

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)

        with transaction.atomic(savepoint=False):
            run = (
                PearWorkerRun.objects.select_for_update()
                .filter(
                    bot_profile=user_profile,
                    trigger_message_id=message_id,
                    stream_name=stream,
                    topic_name=topic,
                    status__in=PEAR_WORKER_RUN_BUSY_STATUSES,
                )
                .first()
            )
            if run is None or run.result_message_id == progress_message_id:
                return
            run.result_message_id = progress_message_id
            run.date_updated = timezone_now()
            run.save(update_fields=["result_message_id", "date_updated"])
            self._record_event(
                run,
                "progress_registered",
                summary="Registered started-progress message.",
                payload={"result_message_id": progress_message_id},
            )

    def start_blocked_reason(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        requesting_user_id: int | None,
    ) -> str | None:
        if message_id <= 0 or requesting_user_id is None:
            return None

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        reap_stale_pear_worker_runs(realm=user_profile.realm)

        try:
            requesting_user = UserProfile.objects.get(
                id=requesting_user_id,
                realm=user_profile.realm,
            )
        except UserProfile.DoesNotExist:
            return "Pear could not identify the requesting user."

        trusted_user_ids = set(settings.PEAR_WORKER_TRUSTED_USER_IDS)
        if (
            trusted_user_ids
            and not requesting_user.is_realm_admin
            and requesting_user.id not in trusted_user_ids
        ):
            return "This user is not allowed to start Pear worker runs."
        if pear_workspace_is_paused(user_profile, stream=stream, topic=topic):
            return "Pear session is paused until a resume control is posted."

        topic_limit = settings.PEAR_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC
        realm_limit = settings.PEAR_WORKER_MAX_ACTIVE_RUNS_PER_REALM
        busy_runs = PearWorkerRun.objects.filter(
            realm=user_profile.realm,
            status__in=PEAR_WORKER_RUN_BUSY_STATUSES,
        )
        if (
            topic_limit > 0
            and busy_runs.filter(
                bot_profile=user_profile,
                stream_name=stream,
                topic_name=topic,
                session_kind=PearWorkerRun.SHARED,
            ).count()
            >= topic_limit
        ):
            return "This topic already has an active Pear worker run."
        if realm_limit > 0 and busy_runs.count() >= realm_limit:
            return "Pear already has the maximum number of active worker runs in this realm."
        return None

    def start(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
    ) -> bool:
        if message_id <= 0:
            return True

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        workspace_node = self._workspace_node_for_topic(
            user_profile,
            stream=stream,
            topic=topic,
        )

        try:
            with transaction.atomic(savepoint=False):
                run = (
                    PearWorkerRun.objects.select_for_update()
                    .filter(bot_profile=user_profile, trigger_message_id=message_id)
                    .first()
                )
                if run is None:
                    run = PearWorkerRun.objects.create(
                        realm=user_profile.realm,
                        bot_profile=user_profile,
                        trigger_message_id=message_id,
                        stream_name=stream,
                        topic_name=topic,
                        workspace_node=workspace_node,
                        status=PearWorkerRun.QUEUED,
                        runtime_label=self._runtime_label(),
                        model=self._model_label(user_profile, stream=stream, topic=topic),
                        sandbox=self._sandbox_label(
                            user_profile,
                            stream=stream,
                            topic=topic,
                        ),
                        approval_policy=self._approval_policy_label(),
                        workdir=self._workdir_label(user_profile, stream=stream, topic=topic),
                    )
                    self._record_event(
                        run,
                        "queued",
                        summary="Queued Pear worker run.",
                        payload={"stream": stream, "topic": topic},
                    )
                    return True
                if run.status in PEAR_WORKER_RUN_ACTIVE_STATUSES:
                    return False
                if not self._update_run(
                    run,
                    "queued",
                    stream=stream,
                    topic=topic,
                    result_message_id=None,
                    error=None,
                    allow_requeue=True,
                ):
                    return False
                self._record_event(
                    run,
                    "queued",
                    summary="Re-queued Pear worker run.",
                    payload={"stream": stream, "topic": topic},
                )
                return True
        except IntegrityError:
            return False

    def claim_for_run(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
    ) -> bool:
        if message_id <= 0:
            return True

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        workspace_node = self._workspace_node_for_topic(
            user_profile,
            stream=stream,
            topic=topic,
        )

        current_time = timezone_now()
        try:
            with transaction.atomic(savepoint=False):
                topic_is_paused = pear_workspace_is_paused(
                    user_profile,
                    stream=stream,
                    topic=topic,
                )
                run = (
                    PearWorkerRun.objects.select_for_update()
                    .filter(bot_profile=user_profile, trigger_message_id=message_id)
                    .first()
                )
                if run is None:
                    if topic_is_paused:
                        return False
                    run = PearWorkerRun.objects.create(
                        realm=user_profile.realm,
                        bot_profile=user_profile,
                        trigger_message_id=message_id,
                        stream_name=stream,
                        topic_name=topic,
                        workspace_node=workspace_node,
                        status=PearWorkerRun.RUNNING,
                        attempts=1,
                        heartbeat_at=current_time,
                        runtime_label=self._runtime_label(),
                        model=self._model_label(user_profile, stream=stream, topic=topic),
                        sandbox=self._sandbox_label(
                            user_profile,
                            stream=stream,
                            topic=topic,
                        ),
                        approval_policy=self._approval_policy_label(),
                        workdir=self._workdir_label(user_profile, stream=stream, topic=topic),
                    )
                    self._record_event(
                        run,
                        "claimed",
                        summary="Claimed Pear worker run.",
                        payload={
                            "stream": stream,
                            "topic": topic,
                            "attempts": run.attempts,
                            "created": True,
                        },
                    )
                    return True

                if topic_is_paused:
                    if run.status in PEAR_WORKER_RUN_BUSY_STATUSES:
                        self._transition(
                            run,
                            PearWorkerRun.FAILED,
                            stream=stream,
                            topic=topic,
                            result_message_id=run.result_message_id,
                            error="Pear topic is paused.",
                            allow_requeue=False,
                        )
                    self._record_claim_dropped(
                        run,
                        stream=stream,
                        topic=topic,
                        reason="topic_paused",
                    )
                    return False

                if run.status == PearWorkerRun.QUEUED:
                    return self._claim_locked_run(
                        run,
                        stream=stream,
                        topic=topic,
                        current_time=current_time,
                        stale_reclaim=False,
                    )

                if run.status == PearWorkerRun.RUNNING:
                    if not self._run_is_stale(run, current_time=current_time):
                        self._record_claim_dropped(
                            run,
                            stream=stream,
                            topic=topic,
                            reason="already_running",
                        )
                        return False
                    if run.attempts >= PEAR_WORKER_MAX_CLAIM_ATTEMPTS:
                        self._transition(
                            run,
                            PearWorkerRun.FAILED,
                            stream=stream,
                            topic=topic,
                            result_message_id=run.result_message_id,
                            error="Pear worker run exceeded its stale-claim retry limit.",
                            allow_requeue=False,
                        )
                        self._record_claim_dropped(
                            run,
                            stream=stream,
                            topic=topic,
                            reason="stale_retry_limit",
                        )
                        return False
                    return self._claim_locked_run(
                        run,
                        stream=stream,
                        topic=topic,
                        current_time=current_time,
                        stale_reclaim=True,
                    )

                self._record_claim_dropped(
                    run,
                    stream=stream,
                    topic=topic,
                    reason=f"terminal_or_finished_{run.status}",
                )
                return False
        except IntegrityError:
            return False

    def record(
        self,
        bot_handler: Any,
        message_id: int,
        status: PearWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if message_id <= 0:
            return

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        workspace_node = self._workspace_node_for_topic(
            user_profile,
            stream=stream,
            topic=topic,
        )

        with transaction.atomic(savepoint=False):
            run, created = PearWorkerRun.objects.select_for_update().get_or_create(
                bot_profile=user_profile,
                trigger_message_id=message_id,
                defaults={
                    "realm": user_profile.realm,
                    "stream_name": stream,
                    "topic_name": topic,
                    "workspace_node": workspace_node,
                    "status": status,
                    "result_message_id": result_message_id,
                    "error": error,
                    "runtime_label": self._runtime_label(),
                    "model": self._model_label(user_profile, stream=stream, topic=topic),
                    "sandbox": self._sandbox_label(
                        user_profile,
                        stream=stream,
                        topic=topic,
                    ),
                    "approval_policy": self._approval_policy_label(),
                    "workdir": self._workdir_label(user_profile, stream=stream, topic=topic),
                },
            )
            if created:
                self._record_event(
                    run,
                    status,
                    summary=f"Recorded Pear worker status: {status}.",
                    payload={"stream": stream, "topic": topic},
                )
                return
            if not self._update_run(
                run,
                status,
                stream=stream,
                topic=topic,
                result_message_id=result_message_id,
                error=error,
            ):
                return
            self._record_event(
                run,
                status,
                summary=f"Recorded Pear worker status: {status}.",
                payload={
                    "stream": stream,
                    "topic": topic,
                    "result_message_id": result_message_id,
                    "error": error,
                },
            )

    def _claim_locked_run(
        self,
        run: PearWorkerRun,
        *,
        stream: str,
        topic: str,
        current_time: datetime,
        stale_reclaim: bool,
    ) -> bool:
        if not self._transition(
            run,
            PearWorkerRun.RUNNING,
            stream=stream,
            topic=topic,
            result_message_id=run.result_message_id,
            error=None,
            allow_requeue=False,
        ):
            return False
        run.attempts += 1
        run.heartbeat_at = current_time
        run.date_updated = current_time
        run.save(update_fields=["attempts", "heartbeat_at", "date_updated"])
        self._record_event(
            run,
            "claimed",
            summary="Claimed Pear worker run.",
            payload={
                "stream": stream,
                "topic": topic,
                "attempts": run.attempts,
                "stale_reclaim": stale_reclaim,
            },
        )
        return True

    def _record_claim_dropped(
        self,
        run: PearWorkerRun,
        *,
        stream: str,
        topic: str,
        reason: str,
    ) -> None:
        self._record_event(
            run,
            "claim_dropped",
            summary="Dropped duplicate Pear worker delivery.",
            payload={
                "stream": stream,
                "topic": topic,
                "status": run.status,
                "attempts": run.attempts,
                "reason": reason,
            },
        )

    def _run_is_stale(self, run: PearWorkerRun, *, current_time: datetime) -> bool:
        return pear_worker_run_is_stale(run, current_time=current_time)

    def _heartbeat_due(self, run: PearWorkerRun, *, current_time: datetime) -> bool:
        if run.status != PearWorkerRun.RUNNING:
            return False
        last_heartbeat = run.heartbeat_at or run.date_updated
        return last_heartbeat <= current_time - timedelta(
            seconds=PEAR_WORKER_HEARTBEAT_INTERVAL_SECONDS
        )

    def _apply_heartbeat_if_due(
        self,
        run: PearWorkerRun,
        *,
        update_fields: list[str],
        current_time: datetime,
    ) -> None:
        if not self._heartbeat_due(run, current_time=current_time):
            return
        run.heartbeat_at = current_time
        update_fields.append("heartbeat_at")

    def update_metadata(
        self,
        bot_handler: Any,
        message_id: int,
        **metadata: Any,
    ) -> None:
        if message_id <= 0:
            return

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        with transaction.atomic(savepoint=False):
            run = (
                PearWorkerRun.objects.select_for_update()
                .filter(bot_profile=user_profile, trigger_message_id=message_id)
                .first()
            )
            if run is None:
                return
            update_fields = ["date_updated"]
            if isinstance(metadata.get("requesting_user_id"), int):
                try:
                    run.requesting_user = UserProfile.objects.get(
                        id=metadata["requesting_user_id"],
                        realm=user_profile.realm,
                    )
                    update_fields.append("requesting_user")
                except UserProfile.DoesNotExist:
                    pass
            for field in (
                "session_kind",
                "summary",
                "current_activity",
                "runtime_label",
                "codex_thread_id",
            ):
                value = metadata.get(field)
                if isinstance(value, str):
                    setattr(run, field, value)
                    update_fields.append(field)
            ambient_triggered = metadata.get("ambient_triggered")
            if isinstance(ambient_triggered, bool):
                run.ambient_triggered = ambient_triggered
                update_fields.append("ambient_triggered")
            context_stream = run.stream_name
            context_topic = run.topic_name
            source_stream = metadata.get("source_stream")
            if isinstance(source_stream, str):
                run.source_stream_name = source_stream
                update_fields.append("source_stream_name")
            source_topic = metadata.get("source_topic")
            if isinstance(source_topic, str):
                run.source_topic_name = source_topic
                update_fields.append("source_topic_name")
            source_message_id = metadata.get("source_message_id")
            if isinstance(source_message_id, int):
                run.source_message_id = source_message_id
                update_fields.append("source_message_id")
            run.model = self._model_label(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            run.sandbox = self._sandbox_label(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            run.approval_policy = self._approval_policy_label()
            run.workdir = self._workdir_label(
                user_profile,
                stream=context_stream,
                topic=context_topic,
            )
            update_fields.extend(["model", "sandbox", "approval_policy", "workdir"])
            current_time = timezone_now()
            run.date_updated = current_time
            self._apply_heartbeat_if_due(
                run,
                update_fields=update_fields,
                current_time=current_time,
            )
            run.save(update_fields=list(dict.fromkeys(update_fields)))

    def record_event(
        self,
        bot_handler: Any,
        message_id: int,
        event_type: str,
        *,
        summary: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        if message_id <= 0:
            return

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        run = PearWorkerRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
        ).first()
        if run is None:
            return
        self._record_event(run, event_type, summary=summary, payload=payload or {})

    def cancel_requested(self, bot_handler: Any, message_id: int) -> bool:
        if message_id <= 0:
            return False

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        return (
            PearWorkerRun.objects.filter(
                bot_profile=user_profile,
                trigger_message_id=message_id,
            )
            .filter(Q(cancel_requested_at__isnull=False) | Q(status=PearWorkerRun.CANCELLED))
            .exists()
        )

    def topic_response_policy(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
    ) -> str:
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        return pear_workspace_response_policy(
            user_profile,
            stream=stream,
            topic=topic,
        )

    def set_topic_response_policy(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
        response_policy: str,
    ) -> None:
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        set_pear_workspace_response_policy(
            user_profile,
            stream=stream,
            topic=topic,
            response_policy=response_policy,
        )

    def steering_events_after(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        after_event_id: int,
    ) -> list[PearPendingSteeringEvent]:
        if message_id <= 0:
            return []

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        run = PearWorkerRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
        ).first()
        if run is None:
            return []

        events = PearWorkerRunEvent.objects.filter(
            run=run,
            event_type="steering",
            id__gt=after_event_id,
        ).order_by("id")
        pending_events: list[PearPendingSteeringEvent] = []
        for event in events:
            payload = event.payload
            instruction = ""
            message_id_value = 0
            if isinstance(payload, dict):
                raw_instruction = payload.get("instruction")
                raw_message_id = payload.get("message_id")
                if isinstance(raw_instruction, str):
                    instruction = raw_instruction.strip()
                if isinstance(raw_message_id, int):
                    message_id_value = raw_message_id
            if not instruction:
                instruction = event.summary.strip()
            if not instruction:
                continue
            pending_events.append(
                {
                    "event_id": event.id,
                    "message_id": message_id_value,
                    "instruction": instruction,
                }
            )
        return pending_events

    def _update_run(
        self,
        run: PearWorkerRun,
        status: PearWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None,
        error: str | None,
        allow_requeue: bool = False,
    ) -> bool:
        return self._transition(
            run,
            status,
            stream=stream,
            topic=topic,
            result_message_id=result_message_id,
            error=error,
            allow_requeue=allow_requeue,
        )

    def _transition(
        self,
        run: PearWorkerRun,
        status: PearWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None,
        error: str | None,
        allow_requeue: bool,
    ) -> bool:
        old_status = run.status
        same_status = old_status == status
        legal_statuses = PEAR_RUN_LEGAL_TRANSITIONS.get(old_status, frozenset())
        is_explicit_requeue = (
            old_status == PearWorkerRun.FAILED and status == PearWorkerRun.QUEUED and allow_requeue
        )
        if not same_status and status not in legal_statuses:
            self._record_transition_rejected(
                run,
                old_status=old_status,
                new_status=status,
                stream=stream,
                topic=topic,
                reason="illegal_transition",
            )
            return False
        if (
            old_status == PearWorkerRun.FAILED
            and status == PearWorkerRun.QUEUED
            and not is_explicit_requeue
        ):
            self._record_transition_rejected(
                run,
                old_status=old_status,
                new_status=status,
                stream=stream,
                topic=topic,
                reason="implicit_requeue",
            )
            return False

        run.stream_name = stream
        run.topic_name = topic
        run.status = status
        run.result_message_id = result_message_id
        run.error = error
        run.date_updated = timezone_now()
        if status in (PearWorkerRun.DONE, PearWorkerRun.FAILED, PearWorkerRun.CANCELLED):
            run.date_finished = run.date_updated
        else:
            run.date_finished = None
        run.save(
            update_fields=[
                "stream_name",
                "topic_name",
                "status",
                "result_message_id",
                "error",
                "date_updated",
                "date_finished",
            ]
        )
        if run.workdir:
            invalidate_pear_git_status_delta(run.workdir)
        return True

    def _record_transition_rejected(
        self,
        run: PearWorkerRun,
        *,
        old_status: str,
        new_status: str,
        stream: str,
        topic: str,
        reason: str,
    ) -> None:
        self._record_event(
            run,
            "transition_rejected",
            summary=f"Rejected Pear worker status transition: {old_status} -> {new_status}.",
            payload={
                "old_status": old_status,
                "new_status": new_status,
                "stream": stream,
                "topic": topic,
                "reason": reason,
            },
        )

    def _record_event(
        self,
        run: PearWorkerRun,
        event_type: str,
        *,
        summary: str,
        payload: dict[str, Any],
    ) -> None:
        run_event = PearWorkerRunEvent.objects.create(
            run=run,
            event_type=event_type,
            summary=summary,
            payload=payload,
        )
        event_tool_count = payload.get("tool_count")
        if event_type == "tool_results" and isinstance(event_tool_count, int):
            PearWorkerRun.objects.filter(id=run.id).update(
                tool_count=F("tool_count") + event_tool_count
            )
            run.tool_count += event_tool_count
        if run.workdir:
            invalidate_pear_git_status_delta(run.workdir)
        current_time = timezone_now()
        if self._heartbeat_due(run, current_time=current_time):
            run.heartbeat_at = current_time
            run.date_updated = current_time
            run.save(update_fields=["heartbeat_at", "date_updated"])
        from zerver.actions.pear import send_pear_run_event

        send_pear_run_event(run, run_event)
        if event_type in {"steering_applied", "tool_results", "transcript"}:
            maybe_enqueue_pear_run_presentation(run.id, run_event.id)

    def _runtime_label(self) -> str:
        return str(settings.PEAR_WORKER_AGENT_RUNTIME or "")

    def _model_label(
        self,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
    ) -> str:
        return pear_worker_model_for_topic(user_profile, stream=stream, topic=topic)

    def _sandbox_label(
        self,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
    ) -> str:
        return pear_worker_sandbox_for_topic(user_profile, stream=stream, topic=topic)

    def _approval_policy_label(self) -> str:
        return str(settings.PEAR_WORKER_APPROVAL_POLICY or "")

    def _workdir_label(
        self,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
    ) -> str:
        return pear_worker_workdir_for_topic(user_profile, stream=stream, topic=topic)

    def _workspace_node_for_topic(
        self,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
    ) -> PearWorkspaceNode | None:
        if not stream:
            return None
        return ensure_pear_workspace_node(user_profile, stream=stream, topic=topic)


class PearQueueWorkerDispatcher:
    def warm(
        self,
        bot_handler: Any,
        message: dict[str, Any],
        *,
        stream: str,
        topic: str,
    ) -> None:
        user_profile_id = getattr(bot_handler.user_profile, "id", None)
        if not isinstance(user_profile_id, int):
            raise AssertionError("Pear queue warm requires a bot user id")
        queue_json_publish_rollback_unsafe(
            PEAR_WORKER_QUEUE_NAME,
            {
                "warm_only": True,
                "user_profile_id": user_profile_id,
                "message_id": pear_message_id(message),
                "stream": stream,
                "topic": topic,
                "content": str(message.get("content", "")),
                "message": dict(message),
            },
        )

    def dispatch(
        self,
        _pear_handler: PearEmbeddedBotHandler,
        bot_handler: Any,
        message: dict[str, Any],
        *,
        content: str,
        active_session: PearActiveSessionSummary | None,
        message_id: int,
        stream: str,
        topic: str,
        requesting_user_id: int | None = None,
        session_kind: str = "shared",
        source_stream: str = "",
        source_topic: str = "",
        source_message_id: int | None = None,
        progress_message_id: int | None = None,
    ) -> None:
        user_profile_id = getattr(bot_handler.user_profile, "id", None)
        if not isinstance(user_profile_id, int):
            raise AssertionError("Pear queue dispatch requires a bot user id")
        worker_message = dict(message)
        worker_message["content"] = content
        # Monitor turns ride their own queue/consumer: a provisioning
        # wall block must never queue ahead of a chat run.
        queue_name = (
            PEAR_MONITOR_WORKER_QUEUE_NAME
            if session_kind == PearWorkerRun.MONITOR
            else PEAR_WORKER_QUEUE_NAME
        )
        queue_json_publish_rollback_unsafe(
            queue_name,
            {
                "user_profile_id": user_profile_id,
                "message_id": message_id,
                "stream": stream,
                "topic": topic,
                "content": content,
                "message": worker_message,
                "requesting_user_id": requesting_user_id,
                "session_kind": session_kind,
                "source_stream": source_stream,
                "source_topic": source_topic,
                "source_message_id": source_message_id,
                "progress_message_id": progress_message_id,
            },
        )


def load_pear_active_session_from_zulip(
    message: dict[str, Any],
    bot_handler: Any,
) -> PearActiveSessionSummary:
    user_profile = bot_handler.user_profile
    assert isinstance(user_profile, UserProfile)
    pear_session = get_pear_session_payload(
        stream=pear_message_stream(message),
        topic=pear_message_topic(message),
    )
    try:
        stream, _sub = access_stream_by_name(user_profile, pear_session["stream"])
    except JsonableError:
        return build_pear_active_session_summary(
            pear_session=pear_session,
            source="server",
            stream_exists=False,
            topic_message_count=0,
            latest_message_id=None,
        )

    assert stream.recipient_id is not None
    message_query = messages_for_topic(
        user_profile.realm_id,
        stream.recipient_id,
        pear_session["topic"],
    )
    trigger_message_id = pear_message_id(message)
    if trigger_message_id > 0:
        message_query = message_query.filter(id__lte=trigger_message_id)
    message_count = message_query.count()
    message_rows = list(
        message_query.order_by("-id").values_list(
            "id",
            "content",
            "sender__is_bot",
            "sender__full_name",
        )[:PEAR_WORKER_TOPIC_HISTORY_LIMIT]
    )
    if trigger_message_id not in {row[0] for row in message_rows}:
        trigger_sender_name = str(
            message.get("sender_full_name") or message.get("sender_email") or "User"
        )
        message_rows.append(
            (trigger_message_id, str(message.get("content", "")), False, trigger_sender_name)
        )
        message_count = max(message_count, len(message_rows))
    latest_codex_run = latest_pear_codex_run_for_topic(
        user_profile,
        stream=pear_session["stream"],
        topic=pear_session["topic"],
        before_message_id=trigger_message_id,
    )
    latest_codex_thread_id = (
        latest_codex_run.codex_thread_id.strip() if latest_codex_run is not None else None
    )
    if not latest_codex_thread_id:
        latest_codex_run = None
        latest_codex_thread_id = None
    prompt_message_query = message_query
    if trigger_message_id > 0:
        # The current request is already the Codex turn input. Repeating it in
        # the transcript wastes context and teaches the engine a duplicated
        # conversation shape.
        prompt_message_query = prompt_message_query.filter(id__lt=trigger_message_id)
    if latest_codex_run is not None:
        # Codex owns the durable thread, including every prior request and its
        # own replies. Pear transports only Zulip messages that arrived since
        # the run which last carried this thread; this is a message boundary,
        # not a second context cache.
        prompt_message_query = prompt_message_query.filter(
            id__gt=latest_codex_run.trigger_message_id
        )
    prompt_message_rows = list(
        prompt_message_query.exclude(sender=user_profile)
        .order_by("-id")
        .values_list(
            "id",
            "content",
            "sender__is_bot",
            "sender__full_name",
        )[:30]
    )
    workspace_context = build_pear_workspace_context(
        user_profile,
        stream=pear_session["stream"],
        topic=pear_session["topic"],
    )
    return build_pear_active_session_summary_from_messages(
        pear_session=pear_session,
        source="server",
        stream_exists=True,
        messages=message_rows,
        prompt_messages=prompt_message_rows,
        topic_message_count=message_count,
        latest_codex_thread_id=latest_codex_thread_id,
        workspace_context=workspace_context,
        is_paused=pear_workspace_is_paused(
            user_profile,
            stream=pear_session["stream"],
            topic=pear_session["topic"],
        ),
        response_policy=pear_workspace_response_policy(
            user_profile,
            stream=pear_session["stream"],
            topic=pear_session["topic"],
        ),
    )
