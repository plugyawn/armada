import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.translation import gettext as _
from django.utils.timezone import now as timezone_now

from zerver.lib.exceptions import JsonableError
from zerver.lib.pear import (
    PearActiveSessionSummary,
    PearAddressingIntentClassifier,
    PearEmbeddedBotHandler,
    PearPendingSteeringEvent,
    PearWorkerToolResult,
    PearWorkerRunStatus,
    PearWorkerRuntime,
    PEAR_CANVAS_FILE_NAME,
    PEAR_CANVAS_TEMPLATE,
    PEAR_REASONING_EFFORTS,
    build_pear_active_session_summary,
    build_pear_active_session_summary_from_messages,
    get_pear_session_payload,
    normalize_pear_response_policy,
    pear_message_id,
    pear_message_stream,
    pear_message_topic,
    parse_pear_artifacts,
)
from zerver.lib.pear_agent import (
    PearAgentRuntime,
    PearCodexAgentsMcpToolRunner,
    PearCodexAppServerToolRunner,
    PearCodexExecAddressingIntentClassifier,
    PearCodexExecToolRunner,
    PearCodexSdkToolRunner,
    PearDisabledAgentRunner,
    PearGeminiInteractionsAddressingIntentClassifier,
    PearProcessIsolation,
)
from zerver.lib.queue import queue_json_publish_rollback_unsafe
from zerver.lib.streams import access_stream_by_name
from zerver.lib.topic import messages_for_topic
from zerver.models import Message, Stream
from zerver.models.bots import PearTopicContext, PearWorkerRun, PearWorkerRunEvent
from zerver.models.users import UserProfile

PEAR_WORKER_QUEUE_NAME = "pear_worker"
PEAR_WORKER_TOPIC_HISTORY_LIMIT = 200
PEAR_WORKER_RUN_ACTIVE_STATUSES = frozenset(
    (PearWorkerRun.QUEUED, PearWorkerRun.RUNNING, PearWorkerRun.DONE)
)
PEAR_WORKER_RUN_BUSY_STATUSES = frozenset((PearWorkerRun.QUEUED, PearWorkerRun.RUNNING))
PEAR_WORKER_RUN_TERMINAL_STATUSES = frozenset(
    (PearWorkerRun.DONE, PearWorkerRun.CANCELLED)
)
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
PEAR_REPOSITORY_CLONE_TIMEOUT_SECONDS = 180
PEAR_GIT_STATUS_DELTA_TTL_SECONDS = 3
PEAR_TOPIC_WORKDIR_OVERLAY_PATHS = frozenset(
    (
        "tools/pear_deploy/README.md",
        "tools/pear_deploy/bootstrap_remote.sh",
        "tools/pear_deploy/pear_do.py",
        "tools/pear_deploy/reset_remote_dev_data.sh",
        "tools/pear_deploy/verify_remote.sh",
        "web/src/pear_panel.ts",
        "web/styles/pear.css",
        "web/templates/pear_panel.hbs",
        "web/templates/settings/pear_config_admin.hbs",
        "zerver/lib/pear.py",
        "zerver/lib/pear_agent.py",
        "zerver/lib/pear_worker.py",
        "zerver/migrations/0805_pearworkerrun.py",
        "zerver/migrations/0806_pear_worker_run_event_state.py",
        "zerver/migrations/0807_peartopiccontext.py",
        "zerver/migrations/0808_peartopiccontext_reasoning_effort.py",
        "zerver/tests/test_pear.py",
        "zerver/tests/test_pear_endpoint.py",
        "zerver/views/pear.py",
        "zerver/worker/pear_worker.py",
    )
)
pear_git_status_delta_cache: dict[
    tuple[str, str, str],
    tuple[float, dict[str, object]],
] = {}

PEAR_CANVAS_FALLBACK_PATH = ".pear/CANVAS.md"


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


def pear_topic_context_is_paused(user_profile: UserProfile, *, stream: str, topic: str) -> bool:
    if not stream:
        return False
    return PearTopicContext.objects.filter(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
        is_paused=True,
    ).exists()


def set_pear_topic_context_paused(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    is_paused: bool,
) -> None:
    if not stream:
        return
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    if context.is_paused == is_paused:
        return
    context.is_paused = is_paused
    context.date_updated = timezone_now()
    context.save(update_fields=["is_paused", "date_updated"])


def pear_topic_context_response_policy(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return "mentions_only"
    context = PearTopicContext.objects.filter(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
    ).first()
    if context is None:
        return "mentions_only"
    return normalize_pear_response_policy(context.response_policy)


def set_pear_topic_context_response_policy(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    response_policy: str,
) -> None:
    if not stream:
        return
    clean_response_policy = normalize_pear_response_policy(response_policy)
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    if context.response_policy == clean_response_policy:
        return
    context.response_policy = clean_response_policy
    context.date_updated = timezone_now()
    context.save(update_fields=["response_policy", "date_updated"])


def reap_stale_pear_worker_runs(
    *,
    realm: Any | None = None,
    bot_profile: UserProfile | None = None,
    stream: str | None = None,
    topic: str | None = None,
) -> int:
    current_time = timezone_now()
    cutoff = current_time - timedelta(seconds=pear_worker_stale_after_seconds())
    stale_runs = PearWorkerRun.objects.filter(status=PearWorkerRun.RUNNING).filter(
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
    store = PearDatabaseWorkerRunStore()
    for run_id in stale_run_ids:
        with transaction.atomic():
            run = (
                PearWorkerRun.objects.select_for_update()
                .filter(id=run_id, status=PearWorkerRun.RUNNING)
                .first()
            )
            if run is None or not pear_worker_run_is_stale(run, current_time=current_time):
                continue
            if not store._transition(
                run,
                PearWorkerRun.FAILED,
                stream=run.stream_name,
                topic=run.topic_name,
                result_message_id=run.result_message_id,
                error="Pear worker run became stale and was recovered.",
                allow_requeue=False,
            ):
                continue
            store._record_event(
                run,
                "stale_reaped",
                summary="Recovered stale Pear worker run.",
                payload={
                    "stale_after_seconds": pear_worker_stale_after_seconds(),
                    "heartbeat_at": run.heartbeat_at.isoformat()
                    if run.heartbeat_at is not None
                    else None,
                },
            )
            reaped_count += 1
    return reaped_count


def latest_pear_codex_thread_id_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    before_message_id: int,
) -> str | None:
    runs = PearWorkerRun.objects.filter(
        bot_profile=user_profile,
        stream_name=stream,
        topic_name=topic,
        session_kind=PearWorkerRun.SHARED,
    ).exclude(codex_thread_id="")
    if before_message_id > 0:
        runs = runs.filter(trigger_message_id__lt=before_message_id)
    run = runs.order_by("-date_updated", "-id").first()
    if run is None:
        return None
    thread_id = run.codex_thread_id.strip()
    if not thread_id:
        return None
    return thread_id


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
    digest = hashlib.sha256(
        f"{user_profile.realm_id}\0{stream}\0{topic}".encode()
    ).hexdigest()[:12]
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

    skills_home = Path(codex_home).expanduser() / "skills"
    skills_home.mkdir(parents=True, exist_ok=True)
    provisioned_skills: list[str] = []
    for source_skill_dir in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if not source_skill_dir.is_dir() or not (source_skill_dir / "SKILL.md").is_file():
            continue
        destination = skills_home / source_skill_dir.name
        shutil.copytree(
            source_skill_dir,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        provisioned_skills.append(source_skill_dir.name)
    return provisioned_skills


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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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


def pear_context_workdir_needs_repair(
    context: PearTopicContext,
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> bool:
    workdir = context.workdir.strip()
    if not workdir:
        return True
    workdir_path = pear_normalize_path(workdir)
    topic_root = pear_normalize_path(pear_topic_workdir_root())
    if workdir_path != topic_root and not workdir_path.is_relative_to(topic_root):
        return True
    if not topic:
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


def pear_try_add_worktree(
    base_path: Path,
    path: Path,
    *,
    branch_name: str,
) -> bool:
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
    return completed.returncode == 0


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


def pear_git_workdir_has_changes(path: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain=v1"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return True
    return completed.returncode != 0 or bool(completed.stdout.strip())


def pear_reconcile_topic_branch(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> bool:
    if not stream:
        return False
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    workdir = context.workdir.strip()
    if not workdir:
        return False
    path = Path(workdir)
    if not pear_path_is_git_worktree(path):
        return False

    target_branch = pear_topic_branch_name(topic)
    delta = pear_git_status_delta(workdir, topic=target_branch)
    if delta.get("branch_matches_topic") is True:
        return True
    if pear_git_workdir_has_changes(path):
        return False
    switched = pear_switch_workdir_to_branch(path, target_branch)
    if switched:
        invalidate_pear_git_status_delta(workdir)
    return switched


def pear_rename_topic_branch(path: Path, old_branch_name: str, new_branch_name: str) -> bool:
    if old_branch_name == new_branch_name or not pear_path_is_git_worktree(path):
        return True
    if pear_git_workdir_has_changes(path):
        return False
    try:
        if pear_git_branch_exists(path, new_branch_name):
            completed = subprocess.run(
                ["git", "-C", str(path), "switch", new_branch_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        elif pear_git_branch_exists(path, old_branch_name):
            completed = subprocess.run(
                ["git", "-C", str(path), "branch", "-m", old_branch_name, new_branch_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        else:
            completed = subprocess.run(
                ["git", "-C", str(path), "switch", "-C", new_branch_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode == 0:
        invalidate_pear_git_status_delta(str(path))
    return completed.returncode == 0


def pear_canvas_template(stream: str, topic: str, *, backfill_content: str = "") -> str:
    content = PEAR_CANVAS_TEMPLATE.format(
        stream=stream or "Pear",
        topic=topic or "general",
    ).rstrip()
    if backfill_content.strip():
        content = f"{content}\n\n{backfill_content.strip()}"
    return f"{content}\n"


def pear_git_workdir_tracks_path(workdir: Path, relative_path: str) -> bool:
    if not pear_path_is_git_worktree(workdir):
        return False
    try:
        completed = subprocess.run(
            ["git", "-C", str(workdir), "ls-files", "--error-unmatch", relative_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def pear_topic_canvas_relative_path(workdir: str | Path) -> str:
    path = Path(workdir)
    if pear_git_workdir_tracks_path(path, PEAR_CANVAS_FILE_NAME):
        return PEAR_CANVAS_FALLBACK_PATH
    return PEAR_CANVAS_FILE_NAME


def pear_topic_canvas_path(workdir: str | Path) -> Path:
    path = Path(workdir)
    return path / pear_topic_canvas_relative_path(path)


def pear_git_exclude_path(workdir: Path) -> Path | None:
    if not pear_path_is_git_worktree(workdir):
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--git-path", "info/exclude"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    exclude_path = Path(completed.stdout.strip())
    if not exclude_path.is_absolute():
        exclude_path = workdir / exclude_path
    return exclude_path


def pear_exclude_canvas_from_git_delta(workdir: str | Path) -> None:
    path = Path(workdir)
    exclude_path = pear_git_exclude_path(path)
    if exclude_path is None:
        return
    relative_path = pear_topic_canvas_relative_path(path)
    exclude_line = f"/{relative_path}\n"
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        if exclude_line not in existing.splitlines(keepends=True):
            with exclude_path.open("a", encoding="utf-8") as exclude_file:
                if existing and not existing.endswith("\n"):
                    exclude_file.write("\n")
                exclude_file.write(exclude_line)
    except OSError:
        return


PEAR_LEGACY_CANVAS_BACKFILL_PATTERN = re.compile(
    r"^\s*(?:[-*]\s*)?"
    r"(Routine disabled|Disabled routine|Routine off|Routine|Routines|"
    r"Routine cadence|Cadence|Schedule|Watch cadence|Memory forgotten|"
    r"Forgotten memory|Memory off|Memory|Memories|Done|Definition of done|"
    r"Success criteria):[ \t]*([^\r\n]*?)[ \t]*$",
    re.IGNORECASE,
)


def pear_legacy_canvas_backfill_items(content: str) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for match in PEAR_LEGACY_CANVAS_BACKFILL_PATTERN.finditer(content):
        label = match.group(1).strip()
        value = match.group(2).strip()
        if not value:
            continue
        normalized_label = " ".join(label.lower().split())
        if normalized_label in {"done", "definition of done", "success criteria"}:
            section = "done"
        elif normalized_label in {
            "routine",
            "routines",
            "routine cadence",
            "cadence",
            "schedule",
            "watch cadence",
        }:
            section = "routine"
        elif normalized_label in {
            "memory",
            "memories",
            "memory forgotten",
            "forgotten memory",
            "memory off",
            "routine disabled",
            "disabled routine",
            "routine off",
        }:
            section = "pinned"
        else:
            section = "notes"
        items.append((section, label, value))
    return items


def pear_backfill_canvas_from_topic_history(
    messages: list[tuple[int, str, bool | None, str | None]]
) -> str:
    done_items: list[str] = []
    pinned_items: list[str] = []
    routine_items: list[str] = []
    notes: list[str] = []
    for message_id, content, _sender_is_bot, _sender_name in messages:
        for artifact in parse_pear_artifacts(content, message_id=message_id):
            value = artifact["value"].strip()
            if not value:
                continue
            kind = artifact["kind"]
            if kind in {
                "document",
                "ticket",
                "pull_request",
                "branch",
                "repository",
                "reviewers",
            }:
                pinned_items.append(f"{artifact['label']}: {value}")
            elif kind in {"verification", "diff"}:
                notes.append(f"{artifact['label']}: {value}")
        for section, label, value in pear_legacy_canvas_backfill_items(content):
            if section == "done":
                done_items.append(value)
            elif section == "routine":
                routine_items.append(f"{label}: {value}")
            elif section == "pinned":
                pinned_items.append(f"{label}: {value}")
            else:
                notes.append(f"{label}: {value}")

    sections: list[str] = []
    if done_items:
        sections.append(
            "### Backfilled definition of done\n"
            + "\n".join(f"- {item}" for item in done_items)
        )
    if pinned_items:
        sections.append(
            "### Backfilled pinned context\n"
            + "\n".join(f"- {item}" for item in pinned_items)
        )
    if routine_items:
        sections.append(
            "### Backfilled routines\n" + "\n".join(f"- {item}" for item in routine_items)
        )
    if notes:
        sections.append(
            "### Backfilled notes\n" + "\n".join(f"- {item}" for item in notes)
        )
    return "\n\n".join(sections)


def pear_sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def pear_read_text_path(path: Path, *, isolation_username: str | None = None) -> str:
    if isolation_username is None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
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
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout


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


def pear_ensure_topic_canvas(
    workdir: str | Path,
    *,
    stream: str = "",
    topic: str = "",
    backfill_content: str = "",
    isolation_username: str | None = None,
) -> Path:
    path = Path(workdir)
    pear_exclude_canvas_from_git_delta(path)
    canvas_path = pear_topic_canvas_path(path)
    template_content = pear_canvas_template(stream, topic)
    content_with_backfill = pear_canvas_template(
        stream,
        topic,
        backfill_content=backfill_content,
    )
    current_content = pear_read_text_path(
        canvas_path,
        isolation_username=isolation_username,
    )
    if not current_content:
        pear_write_text_path(
            canvas_path,
            content_with_backfill,
            isolation_username=isolation_username,
        )
    elif backfill_content.strip() and current_content == template_content:
        pear_write_text_path(
            canvas_path,
            content_with_backfill,
            isolation_username=isolation_username,
        )
    return canvas_path


def ensure_pear_topic_context(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    parent_context: PearTopicContext | None = None,
) -> PearTopicContext:
    context, _created = PearTopicContext.objects.get_or_create(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
    )
    if parent_context is None and topic:
        parent_context = PearTopicContext.objects.filter(
            realm=user_profile.realm,
            stream_name=stream,
            topic_name="",
        ).first()
    update_fields = []
    if (
        parent_context is not None
        and context.id != parent_context.id
        and context.parent_context_id != parent_context.id
    ):
        context.parent_context = parent_context
        update_fields.append("parent_context")
    if pear_context_workdir_needs_repair(
        context,
        user_profile,
        stream=stream,
        topic=topic,
    ):
        context.workdir = str(pear_expected_topic_workdir(user_profile, stream=stream, topic=topic))
        update_fields.append("workdir")
    if update_fields:
        context.date_updated = timezone_now()
        context.save(update_fields=[*update_fields, "date_updated"])
    parent_workdir = (
        parent_context.workdir.strip()
        if parent_context is not None
        and parent_context.github_repo.strip()
        and parent_context.workdir.strip()
        else None
    )
    parent_repository_url = None
    if parent_context is not None and parent_context.github_repo.strip():
        parent_repository_value = parent_context.github_repo.strip()
        if not pear_repo_artifact_value_is_local_topic_path(parent_repository_value):
            parent_repository_url = parent_repository_value
    ensure_pear_topic_workdir(
        context.workdir,
        base_workdir=parent_workdir,
        repository_url=parent_repository_url,
        branch_name=pear_topic_branch_name(topic),
    )
    isolation_username = (
        pear_topic_isolation_username(user_profile, stream=stream, topic=topic)
        if pear_topic_os_user_isolation_enabled()
        else None
    )
    pear_ensure_topic_canvas(
        context.workdir,
        stream=stream,
        topic=topic,
        isolation_username=isolation_username,
    )
    return context


def ensure_pear_child_topic_context(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    parent_stream: str,
    parent_topic: str,
) -> PearTopicContext:
    parent_context = None
    if parent_stream and parent_topic is not None:
        parent_context = ensure_pear_topic_context(
            user_profile,
            stream=parent_stream,
            topic=parent_topic,
        )
    return ensure_pear_topic_context(
        user_profile,
        stream=stream,
        topic=topic,
        parent_context=parent_context,
    )


def ensure_pear_topic_workdir(
    workdir: str,
    *,
    base_workdir: str | Path | None = None,
    repository_url: str | None = None,
    branch_name: str | None = None,
) -> None:
    path = Path(workdir)
    branch_name = pear_topic_branch_name(branch_name or Path(workdir).name)
    if pear_topic_os_user_isolation_enabled():
        if path.exists():
            if not pear_path_is_git_worktree(path):
                try:
                    subprocess.run(
                        ["git", "-C", str(path), "init", "--quiet"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                    pass
                pear_switch_workdir_to_branch(path, branch_name)
            return
        if repository_url:
            clone_or_update_pear_repository_workdir(repository_url, path)
            pear_switch_workdir_to_branch(path, branch_name)
            return
        path.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["git", "-C", str(path), "init", "--quiet"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        pear_switch_workdir_to_branch(path, branch_name)
        return
    base_path = Path(base_workdir) if base_workdir is not None else Path(pear_default_worker_workdir())
    base_is_git_worktree = pear_path_is_git_worktree(base_path)
    path_was_empty = not ((path / ".git").exists() or path.exists() and any(path.iterdir()))
    worktree_added = False
    if path_was_empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            worktree_added = pear_try_add_worktree(base_path, path, branch_name=branch_name)
            if not worktree_added:
                digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
                worktree_added = pear_try_add_worktree(
                    base_path,
                    path,
                    branch_name=f"{branch_name}-{digest}",
                )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
    if base_is_git_worktree and not ((path / ".git").exists() or worktree_added):
        return
    path.mkdir(parents=True, exist_ok=True)
    if path_was_empty and not worktree_added and pear_path_is_git_worktree(path):
        pear_switch_workdir_to_branch(path, branch_name)


def clean_pear_repository_url(repository_url: str) -> str:
    clean_url = repository_url.strip()
    if not clean_url:
        return ""
    if len(clean_url) > 500 or any(character.isspace() for character in clean_url):
        raise JsonableError(_("Enter a valid Git repository URL."))
    if clean_url.startswith("git@") and ":" in clean_url:
        return clean_url

    parsed_url = urlparse(clean_url)
    allowed_schemes = {"file", "git", "http", "https", "ssh"}
    if parsed_url.scheme not in allowed_schemes:
        raise JsonableError(_("Enter a valid Git repository URL."))
    if parsed_url.scheme == "file":
        if not parsed_url.path:
            raise JsonableError(_("Enter a valid Git repository URL."))
        return clean_url
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


def configure_pear_channel_repository_context(
    user_profile: UserProfile,
    *,
    stream: str,
    repository_url: str,
) -> PearTopicContext:
    clean_url = clean_pear_repository_url(repository_url)
    context, _created = PearTopicContext.objects.get_or_create(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name="",
    )
    update_fields = []
    if not context.workdir.strip():
        context.workdir = str(
            pear_topic_workdir_root() / pear_topic_workdir_slug(user_profile, stream=stream, topic="")
        )
        update_fields.append("workdir")
    clone_or_update_pear_repository_workdir(clean_url, Path(context.workdir))
    if context.github_repo != clean_url:
        context.github_repo = clean_url
        update_fields.append("github_repo")
    if update_fields:
        context.date_updated = timezone_now()
        context.save(update_fields=[*update_fields, "date_updated"])
    return context


def pear_move_workdir_for_topic_rename(
    user_profile: UserProfile,
    context: PearTopicContext,
    *,
    stream: str,
    topic: str,
    old_topic: str = "",
) -> tuple[bool, str]:
    old_workdir = context.workdir.strip()
    new_workdir = pear_expected_topic_workdir(user_profile, stream=stream, topic=topic)
    old_branch_name = pear_topic_branch_name(old_topic or context.topic_name)
    new_branch_name = pear_topic_branch_name(topic)
    if not old_workdir:
        return True, str(new_workdir)

    old_path = Path(old_workdir)
    if pear_normalize_path(old_path) == pear_normalize_path(new_workdir):
        pear_rename_topic_branch(old_path, old_branch_name, new_branch_name)
        return True, str(new_workdir)
    if pear_normalize_path(old_path) == pear_normalize_path(pear_default_worker_workdir()):
        ensure_pear_topic_workdir(str(new_workdir), branch_name=new_branch_name)
        return True, str(new_workdir)
    if not old_path.exists():
        ensure_pear_topic_workdir(str(new_workdir), branch_name=new_branch_name)
        return True, str(new_workdir)

    root = pear_normalize_path(pear_topic_workdir_root())
    if not pear_normalize_path(old_path).is_relative_to(root):
        return False, old_workdir
    if new_workdir.exists():
        return False, old_workdir

    new_workdir.parent.mkdir(parents=True, exist_ok=True)
    moved = False
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(old_path),
                "worktree",
                "move",
                str(old_path),
                str(new_workdir),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        moved = completed.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        moved = False
    if not moved:
        try:
            shutil.move(str(old_path), str(new_workdir))
            moved = True
        except (OSError, shutil.Error):
            return False, old_workdir
    pear_rename_topic_branch(new_workdir, old_branch_name, new_branch_name)
    return True, str(new_workdir)


def maybe_refactor_pear_topic_context_on_move(
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
    context = PearTopicContext.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream.name,
        topic_name=old_topic,
    ).first()
    if context is None:
        return None

    target = PearTopicContext.objects.filter(
        realm=user_profile.realm,
        stream_name=new_stream.name,
        topic_name=new_topic,
    ).first()
    if target is not None and target.id != context.id:
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

    workdir_moved, new_workdir = pear_move_workdir_for_topic_rename(
        user_profile,
        context,
        stream=new_stream.name,
        topic=new_topic,
        old_topic=old_topic,
    )
    if not workdir_moved:
        return PearTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Pear workspace warning: I did not rename the Pear workspace context "
                "because its worktree could not be moved safely. The old context is unchanged."
            ),
        )

    context.stream_name = new_stream.name
    context.topic_name = new_topic
    context.workdir = new_workdir
    context.date_updated = timezone_now()
    context.save(update_fields=["stream_name", "topic_name", "workdir", "date_updated"])
    PearWorkerRun.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream.name,
        topic_name=old_topic,
        session_kind=PearWorkerRun.SHARED,
    ).update(stream_name=new_stream.name, topic_name=new_topic, date_updated=timezone_now())
    PearWorkerRun.objects.filter(
        realm=user_profile.realm,
        source_stream_name=old_stream.name,
        source_topic_name=old_topic,
    ).update(source_stream_name=new_stream.name, source_topic_name=new_topic)

    return PearTopicRenameRefactorResult(
        stream_name=new_stream.name,
        topic_name=new_topic,
        message=(
            "Pear workspace context moved with this topic. "
            f"Worktree: `{new_workdir}`"
        ),
    )


def maybe_refactor_pear_stream_contexts_on_rename(
    user_profile: UserProfile,
    *,
    old_stream_name: str,
    new_stream_name: str,
) -> str | None:
    if old_stream_name == new_stream_name:
        return None

    contexts = PearTopicContext.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream_name,
    )
    if not contexts.exists():
        return None

    if PearTopicContext.objects.filter(
        realm=user_profile.realm,
        stream_name=new_stream_name,
    ).exists():
        return (
            "Pear workspace warning: I did not rename Pear channel context "
            "because the destination channel name already has Pear context. "
            "Review the sidebar before starting more Codex work."
        )

    timestamp = timezone_now()
    context_count = contexts.update(stream_name=new_stream_name, date_updated=timestamp)
    PearWorkerRun.objects.filter(
        realm=user_profile.realm,
        stream_name=old_stream_name,
    ).update(stream_name=new_stream_name, date_updated=timestamp)
    PearWorkerRun.objects.filter(
        realm=user_profile.realm,
        source_stream_name=old_stream_name,
    ).update(source_stream_name=new_stream_name)

    return (
        f"Pear channel context renamed from `{old_stream_name}` to `{new_stream_name}` "
        f"for {context_count} context layer(s). Existing worktree paths were kept unchanged; "
        "check the sidebar before starting Codex work."
    )


def pear_worktree_overlay_path_allowed(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        return False
    return relative_path in PEAR_TOPIC_WORKDIR_OVERLAY_PATHS


def pear_base_dirty_paths(base_workdir: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(base_workdir), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []

    paths: list[str] = []
    records = completed.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record or len(record) < 4:
            continue
        status = record[:2]
        relative_path = record[3:]
        if "R" in status or "C" in status:
            index += 1
            continue
        paths.append(relative_path)
    return paths


def sync_pear_topic_workdir_overlay(base_workdir: Path, topic_workdir: Path) -> None:
    for relative_path in pear_base_dirty_paths(base_workdir):
        if not pear_worktree_overlay_path_allowed(relative_path):
            continue
        source = base_workdir / relative_path
        destination = topic_workdir / relative_path
        if not source.is_file() or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def pear_git_output(
    workdir: str,
    args: list[str],
    *,
    isolation_username: str | None = None,
) -> str:
    command = ["git", "-C", workdir, *args]
    if isolation_username is not None:
        command = ["sudo", "-n", "-u", isolation_username, "/usr/bin/env", *command]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


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

    for line in status_output.splitlines():
        if not line.strip():
            continue
        if not line.startswith("# "):
            changed_count += 1
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
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    parent_context = (
        context.parent_context
        if context.parent_context_id is not None
        else None
    )
    workdir = context.workdir.strip() or pear_default_worker_workdir()
    context_repo = context.github_repo.strip()
    parent_repo = parent_context.github_repo.strip() if parent_context is not None else ""
    repository = pear_logical_repository_label(
        stream=stream,
        context_repo=context_repo,
        parent_repo=parent_repo,
    )
    worktree = pear_logical_worktree_label(
        stream=stream,
        topic=topic,
        fallback=Path(workdir).name,
    )
    return {
        "repository": repository,
        "remote_url": pear_pinned_repository_value(context_repo)
        or pear_pinned_repository_value(parent_repo),
        "worktree": worktree,
        "workdir": workdir,
        "branch": pear_topic_branch_name(topic),
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
    return (
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\s+<[^>\n]+>)?", value)
        is not None
    )


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
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    if context.model.strip():
        return context.model.strip()
    parent_context = context.parent_context if context.parent_context_id is not None else None
    if parent_context is not None and parent_context.model.strip():
        return parent_context.model.strip()
    return pear_default_worker_model()


def pear_worker_reasoning_effort_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return pear_default_worker_reasoning_effort()
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    if context.reasoning_effort.strip():
        return context.reasoning_effort.strip()
    parent_context = context.parent_context if context.parent_context_id is not None else None
    if parent_context is not None and parent_context.reasoning_effort.strip():
        return parent_context.reasoning_effort.strip()
    return pear_default_worker_reasoning_effort()


def pear_worker_workdir_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return pear_default_worker_workdir()
    context = ensure_pear_topic_context(user_profile, stream=stream, topic=topic)
    return context.workdir.strip() or pear_default_worker_workdir()


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
    if runtime == "codex-sdk" and process_isolation_override is not None:
        return PearWorkerRuntime(
            PearDisabledAgentRunner(
                "Pear per-topic OS-user isolation cannot use the in-process Codex SDK "
                "runtime. Use codex-app-server, codex-exec, or codex-agents-mcp."
            )
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
    if runtime == "codex-agents-mcp":
        return PearWorkerRuntime(
            PearCodexAgentsMcpToolRunner(
                codex_bin=settings.PEAR_WORKER_CODEX_BIN,
                workdir=workdir,
                model=model,
                sandbox=sandbox,
                approval_policy=settings.PEAR_WORKER_APPROVAL_POLICY,
                timeout_seconds=settings.PEAR_WORKER_TIMEOUT_SECONDS,
                output_limit=settings.PEAR_WORKER_OUTPUT_LIMIT,
                reasoning_effort=reasoning_effort,
                process_isolation=process_isolation_override,
            )
        )
    if runtime == "codex-exec":
        return PearWorkerRuntime(
            PearCodexExecToolRunner(
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
                extra_args=settings.PEAR_WORKER_CODEX_EXTRA_ARGS,
                process_isolation=process_isolation_override,
            )
        )
    if runtime == "codex-sdk":
        return PearWorkerRuntime(
            PearCodexSdkToolRunner(
                codex_bin=settings.PEAR_WORKER_CODEX_BIN,
                workdir=workdir,
                model=model,
                sandbox=sandbox,
                approval_policy=settings.PEAR_WORKER_APPROVAL_POLICY,
                service_tier=settings.PEAR_WORKER_SERVICE_TIER,
                fast_mode_enabled=settings.PEAR_WORKER_FAST_MODE,
                reasoning_effort=reasoning_effort,
                output_limit=settings.PEAR_WORKER_OUTPUT_LIMIT,
                process_isolation=process_isolation_override,
            )
        )
    if runtime != "disabled":
        invalid_runtime: PearAgentRuntime | str = runtime
        return PearWorkerRuntime(
            PearDisabledAgentRunner(
                f"Pear worker agent runtime is unsupported: {invalid_runtime!r}."
            )
        )
    return PearWorkerRuntime(
        PearDisabledAgentRunner(
            "Pear worker agent runtime is disabled. Set PEAR_WORKER_AGENT_RUNTIME "
            "to 'codex-app-server' for direct Codex app-server control, "
            "'codex-agents-mcp' for the OpenAI Agents SDK + Codex MCP tool runtime, "
            "'codex-sdk' for direct official Codex SDK control, or 'codex-exec' "
            "for the non-interactive CLI fallback."
        )
    )


def build_pear_addressing_intent_classifier_from_settings() -> (
    PearAddressingIntentClassifier | None
):
    runtime = settings.PEAR_ADDRESSING_ROUTER_RUNTIME
    if runtime == "codex-exec":
        return PearCodexExecAddressingIntentClassifier(
            codex_bin=settings.PEAR_WORKER_CODEX_BIN,
            workdir=settings.PEAR_WORKER_WORKDIR,
            model=settings.PEAR_ADDRESSING_ROUTER_MODEL,
            reasoning_effort=settings.PEAR_ADDRESSING_ROUTER_REASONING_EFFORT,
            sandbox="read-only",
            approval_policy="never",
            timeout_seconds=settings.PEAR_ADDRESSING_ROUTER_TIMEOUT_SECONDS,
            output_limit=settings.PEAR_ADDRESSING_ROUTER_OUTPUT_LIMIT,
            extra_args=settings.PEAR_ADDRESSING_ROUTER_CODEX_EXTRA_ARGS,
        )
    if runtime == "gemini-interactions":
        return PearGeminiInteractionsAddressingIntentClassifier(
            api_key=settings.PEAR_ADDRESSING_ROUTER_GEMINI_API_KEY,
            model=settings.PEAR_ADDRESSING_ROUTER_GEMINI_MODEL,
            timeout_seconds=settings.PEAR_ADDRESSING_ROUTER_TIMEOUT_SECONDS,
            output_limit=settings.PEAR_ADDRESSING_ROUTER_OUTPUT_LIMIT,
            url=settings.PEAR_ADDRESSING_ROUTER_GEMINI_URL,
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

    def active_worker_run_status_message(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
    ) -> dict[str, object] | None:
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
            .exclude(result_message_id=None)
            .order_by("-date_updated", "-id")
            .first()
        )
        if run is None or run.result_message_id is None:
            return None
        message = Message.objects.filter(id=run.result_message_id).first()
        return {
            "message_id": run.result_message_id,
            "content": message.content if message is not None else "",
        }

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

        with transaction.atomic():
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
        if trusted_user_ids and not requesting_user.is_realm_admin and requesting_user.id not in trusted_user_ids:
            return "This user is not allowed to start Pear worker runs."
        if pear_topic_context_is_paused(user_profile, stream=stream, topic=topic):
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

        try:
            with transaction.atomic():
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

        current_time = timezone_now()
        try:
            with transaction.atomic():
                topic_is_paused = pear_topic_context_is_paused(
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

        with transaction.atomic():
            run, created = PearWorkerRun.objects.select_for_update().get_or_create(
                bot_profile=user_profile,
                trigger_message_id=message_id,
                defaults={
                    "realm": user_profile.realm,
                    "stream_name": stream,
                    "topic_name": topic,
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
        with transaction.atomic():
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
        return PearWorkerRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
        ).filter(Q(cancel_requested_at__isnull=False) | Q(status=PearWorkerRun.CANCELLED)).exists()

    def set_topic_paused(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
        is_paused: bool,
    ) -> None:
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        set_pear_topic_context_paused(
            user_profile,
            stream=stream,
            topic=topic,
            is_paused=is_paused,
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
        return pear_topic_context_response_policy(
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
        set_pear_topic_context_response_policy(
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
            old_status == PearWorkerRun.FAILED
            and status == PearWorkerRun.QUEUED
            and allow_requeue
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
        PearWorkerRunEvent.objects.create(
            run=run,
            event_type=event_type,
            summary=summary,
            payload=payload,
        )
        if run.workdir:
            invalidate_pear_git_status_delta(run.workdir)
        current_time = timezone_now()
        if self._heartbeat_due(run, current_time=current_time):
            run.heartbeat_at = current_time
            run.date_updated = current_time
            run.save(update_fields=["heartbeat_at", "date_updated"])

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


class PearQueueWorkerDispatcher:
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
        queue_json_publish_rollback_unsafe(
            PEAR_WORKER_QUEUE_NAME,
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
            checklist=[],
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
            message.get("sender_full_name")
            or message.get("sender_email")
            or "User"
        )
        message_rows.append(
            (trigger_message_id, str(message.get("content", "")), False, trigger_sender_name)
        )
        message_count = max(message_count, len(message_rows))
    latest_codex_thread_id = latest_pear_codex_thread_id_for_topic(
        user_profile,
        stream=pear_session["stream"],
        topic=pear_session["topic"],
        before_message_id=trigger_message_id,
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
        topic_message_count=message_count,
        latest_codex_thread_id=latest_codex_thread_id,
        workspace_context=workspace_context,
        is_paused=pear_topic_context_is_paused(
            user_profile,
            stream=pear_session["stream"],
            topic=pear_session["topic"],
        ),
        response_policy=pear_topic_context_response_policy(
            user_profile,
            stream=pear_session["stream"],
            topic=pear_session["topic"],
        ),
    )
