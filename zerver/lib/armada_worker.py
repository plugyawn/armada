import contextlib
import hashlib
import json
import logging
import os
import posixpath
import pwd
import re
import shutil
import subprocess
import tempfile
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
from django.db.models import Q
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.armada import (
    ARMADA_AGENT_NAME,
    ARMADA_DEFAULT_RESPONSE_POLICY,
    ARMADA_REASONING_EFFORTS,
    ArmadaActiveSessionSummary,
    ArmadaAddressingIntentClassifier,
    ArmadaAnswerStreamSender,
    ArmadaEmbeddedBotHandler,
    ArmadaPendingSteeringEvent,
    ArmadaReferencedMessage,
    ArmadaReferencedThread,
    ArmadaWorkerLeaseLostError,
    ArmadaWorkerRunStatus,
    ArmadaWorkerRuntime,
    ArmadaWorkspaceContext,
    armada_message_id,
    armada_message_stream,
    armada_message_topic,
    armada_topic_scope,
    build_armada_active_session_summary,
    build_armada_active_session_summary_from_messages,
    get_armada_session_payload,
    normalize_armada_response_policy,
    trim_armada_text,
)
from zerver.lib.armada_agent import (
    ArmadaCodexAppServerToolRunner,
    ArmadaDisabledAgentRunner,
    ArmadaGeminiInteractionsAddressingIntentClassifier,
    ArmadaProcessIsolation,
)
from zerver.lib.armada_run_card import record_agent_run_event
from zerver.lib.armada_skills import reconcile_armada_codex_skills
from zerver.lib.context_managers import lockfile
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import bulk_access_stream_messages_query
from zerver.lib.queue import queue_json_publish_rollback_unsafe
from zerver.lib.streams import access_stream_by_name
from zerver.lib.topic import messages_for_topic
from zerver.lib.users import get_inaccessible_user_ids
from zerver.models import Message, Realm, Stream
from zerver.models.bots import AgentMonitor, AgentRun, AgentRunEvent, Repository, Workspace
from zerver.models.users import UserProfile

ARMADA_WORKER_QUEUE_NAME = "armada_worker"
ARMADA_WORKER_QUEUE_NAMES = (ARMADA_WORKER_QUEUE_NAME, "armada_worker_1")
ARMADA_MONITOR_WORKER_QUEUE_NAME = "armada_monitor_worker"
ARMADA_ADDRESSING_QUEUE_NAME = "armada_addressing"
ARMADA_PRESENTATION_QUEUE_NAME = "armada_presentation"
ARMADA_RUN_ATTENTION_TTL_SECONDS = 15
ARMADA_PRESENTATION_CADENCE_SECONDS = 5
ARMADA_WORKER_TOPIC_HISTORY_LIMIT = 200
ARMADA_REFERENCED_CONVERSATION_LIMIT = 4
ARMADA_REFERENCED_MESSAGE_LIMIT = 30
ARMADA_REFERENCED_THREAD_TOKEN_LIMIT = 1500
ARMADA_REFERENCED_TOTAL_TOKEN_LIMIT = 3000
ARMADA_WORKER_RUN_ACTIVE_STATUSES = frozenset((AgentRun.QUEUED, AgentRun.RUNNING, AgentRun.DONE))
ARMADA_WORKER_RUN_BUSY_STATUSES = frozenset((AgentRun.QUEUED, AgentRun.RUNNING))
ARMADA_WORKER_RUN_TERMINAL_STATUSES = frozenset((AgentRun.DONE, AgentRun.CANCELLED))
ARMADA_WORKER_MAX_CLAIM_ATTEMPTS = 2
ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS = 15
ARMADA_WORKER_STALE_HEARTBEAT_MISSES = 4
ARMADA_RUN_LEGAL_TRANSITIONS: dict[str, frozenset[ArmadaWorkerRunStatus]] = {
    AgentRun.QUEUED: frozenset((AgentRun.RUNNING, AgentRun.FAILED, AgentRun.CANCELLED)),
    AgentRun.RUNNING: frozenset((AgentRun.DONE, AgentRun.FAILED, AgentRun.CANCELLED)),
    AgentRun.FAILED: frozenset((AgentRun.QUEUED,)),
    AgentRun.DONE: frozenset(),
    AgentRun.CANCELLED: frozenset(),
}


def armada_worker_home_slot(user_profile_id: int, *, stream: str, topic: str) -> int:
    """Return a stable home slot without creating a second ownership store."""
    stream, topic = armada_topic_scope(stream, topic)
    affinity_key = f"{user_profile_id}\0{stream}\0{topic}".encode()
    digest = hashlib.blake2s(affinity_key, digest_size=2).digest()
    return int.from_bytes(digest, "big") % len(ARMADA_WORKER_QUEUE_NAMES)


def armada_worker_queue_name(user_profile_id: int, *, stream: str, topic: str) -> str:
    return ARMADA_WORKER_QUEUE_NAMES[
        armada_worker_home_slot(user_profile_id, stream=stream, topic=topic)
    ]


def armada_referenced_conversations(content: str) -> list[tuple[str, str | None]]:
    if "#**" not in content:
        return []
    # Import lazily: the Markdown engine is intentionally not part of the
    # ordinary worker startup path. These are the canonical Zulip link
    # patterns, so the worker cannot drift into accepting a second reference syntax.
    from zerver.lib.markdown import (
        get_compiled_stream_link_regex,
        get_compiled_stream_topic_link_regex,
    )

    topic_matches = list(get_compiled_stream_topic_link_regex().finditer(content))
    topic_spans = [match.span() for match in topic_matches]
    references: list[tuple[int, str, str | None]] = [
        (match.start(), match.group("stream_name"), match.group("topic_name"))
        for match in topic_matches
    ]
    for match in get_compiled_stream_link_regex().finditer(content):
        if any(start <= match.start() < end for start, end in topic_spans):
            continue
        references.append((match.start(), match.group("stream_name"), None))

    ordered: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for _position, stream, topic in sorted(references):
        reference = (stream.strip(), topic.strip() if topic is not None else None)
        reference_key = (reference[0].casefold(), reference[1].casefold() if reference[1] else None)
        if not reference[0] or reference_key in seen:
            continue
        seen.add(reference_key)
        ordered.append(reference)
        if len(ordered) >= ARMADA_REFERENCED_CONVERSATION_LIMIT:
            break
    return ordered


def armada_reference_token_count(value: str) -> int:
    # A deterministic, dependency-free conservative approximation is enough
    # for a hard context budget. Punctuation counts separately, as it does for
    # the model tokenizer, rather than treating an entire code line as one word.
    return len(re.findall(r"\w+|[^\w\s]", value, re.UNICODE))


def load_armada_referenced_threads(
    requesting_user: UserProfile | None,
    *,
    content: str,
    current_stream: str,
    current_topic: str,
) -> list[ArmadaReferencedThread]:
    references = armada_referenced_conversations(content)
    if not references:
        return []

    remaining_total_tokens = ARMADA_REFERENCED_TOTAL_TOKEN_LIMIT
    referenced_threads: list[ArmadaReferencedThread] = []
    for stream_name, topic_name in references:
        if (
            topic_name is not None
            and stream_name.casefold() == current_stream.casefold()
            and topic_name.casefold() == current_topic.casefold()
        ):
            continue

        unavailable: ArmadaReferencedThread = {
            "stream": stream_name,
            "topic": topic_name,
            "accessible": False,
            "truncated": False,
            "messages": [],
        }
        if requesting_user is None:
            referenced_threads.append(unavailable)
            continue
        try:
            stream, _subscription = access_stream_by_name(requesting_user, stream_name)
        except JsonableError:
            referenced_threads.append(unavailable)
            continue

        assert stream.recipient_id is not None
        if topic_name is None:
            message_query = Message.objects.filter(
                realm_id=requesting_user.realm_id,
                recipient_id=stream.recipient_id,
            )
        else:
            message_query = messages_for_topic(
                requesting_user.realm_id,
                stream.recipient_id,
                topic_name,
            )
        visible_query = bulk_access_stream_messages_query(
            requesting_user,
            message_query,
            stream,
        )
        candidates = list(
            visible_query.select_related("sender").order_by("-id")[
                : ARMADA_REFERENCED_MESSAGE_LIMIT + 1
            ]
        )
        has_earlier_messages = len(candidates) > ARMADA_REFERENCED_MESSAGE_LIMIT
        candidates = candidates[:ARMADA_REFERENCED_MESSAGE_LIMIT]
        inaccessible_sender_ids = get_inaccessible_user_ids(
            [message.sender_id for message in candidates],
            requesting_user,
        )

        thread_budget = min(ARMADA_REFERENCED_THREAD_TOKEN_LIMIT, remaining_total_tokens)
        selected_messages: list[ArmadaReferencedMessage] = []
        used_tokens = 0
        for candidate in candidates:
            sender_name = (
                str(UserProfile.INACCESSIBLE_USER_NAME)
                if candidate.sender_id in inaccessible_sender_ids
                else candidate.sender.full_name
            )
            clean_content = trim_armada_text(candidate.content, limit=1200)
            message_tokens = armada_reference_token_count(
                sender_name
            ) + armada_reference_token_count(clean_content)
            if used_tokens + message_tokens > thread_budget:
                has_earlier_messages = True
                break
            selected_messages.append(
                {
                    "message_id": candidate.id,
                    "sender": trim_armada_text(sender_name, limit=120),
                    "sender_is_bot": candidate.sender.is_bot,
                    "content": clean_content,
                    "topic": candidate.topic_name(),
                }
            )
            used_tokens += message_tokens
        remaining_total_tokens = max(remaining_total_tokens - used_tokens, 0)
        selected_messages.reverse()
        referenced_threads.append(
            {
                "stream": stream.name,
                "topic": topic_name,
                "accessible": True,
                "truncated": has_earlier_messages,
                "messages": selected_messages,
            }
        )
    return referenced_threads


def armada_run_attention_cache_key(run_id: int) -> str:
    return f"armada:run-attention:{run_id}"


def armada_run_presentation_gate_cache_key(run_id: int) -> str:
    return f"armada:run-presentation-gate:{run_id}"


def mark_armada_run_attended(run_id: int) -> None:
    cache.set(armada_run_attention_cache_key(run_id), True, ARMADA_RUN_ATTENTION_TTL_SECONDS)


def armada_run_is_attended(run_id: int) -> bool:
    return cache.get(armada_run_attention_cache_key(run_id)) is True


def refresh_armada_workspace_after_terminal_run(workspace_node_id: int) -> None:
    """Observe direct runtime writes once a run can no longer mutate its worktree."""
    try:
        workspace_node = Workspace.objects.get(id=workspace_node_id)
        from zerver.lib.armada_workspace_resources import refresh_workspace_snapshot

        refresh_workspace_snapshot(workspace_node)
    except Exception:
        # Run settlement is authoritative even if the secondary Workspace
        # observer fails. A later explicit refresh still repairs the revision.
        logger.exception(
            "Could not refresh workspace %s after a terminal run",
            workspace_node_id,
        )


def maybe_enqueue_armada_run_presentation(run_id: int, source_event_id: int) -> bool:
    if not armada_run_is_attended(run_id):
        return False
    if not cache.add(
        armada_run_presentation_gate_cache_key(run_id),
        True,
        ARMADA_PRESENTATION_CADENCE_SECONDS,
    ):
        return False
    queue_json_publish_rollback_unsafe(
        ARMADA_PRESENTATION_QUEUE_NAME,
        {"run_id": run_id, "source_event_id": source_event_id},
    )
    return True


ARMADA_REPOSITORY_CLONE_TIMEOUT_SECONDS = 180
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArmadaTopicRenameRefactorResult:
    stream_name: str
    topic_name: str
    message: str


def armada_worker_stale_after_seconds() -> int:
    return ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS * ARMADA_WORKER_STALE_HEARTBEAT_MISSES


def armada_worker_run_is_stale(run: AgentRun, *, current_time: datetime) -> bool:
    last_seen = run.heartbeat_at or run.date_updated
    return last_seen <= current_time - timedelta(seconds=armada_worker_stale_after_seconds())


def wait_for_redelivered_armada_worker_run(
    *,
    bot_profile_id: int,
    trigger_message_id: int,
) -> None:
    """Hold a redelivery until its previous worker lease settles or expires.

    A RabbitMQ connection can disappear while Codex is still finishing a
    turn.  Acknowledging that redelivery immediately can lose the only retry;
    claiming it immediately can run the same turn twice.  The database
    heartbeat is already the authoritative lease, so wait on that one source
    of truth instead of adding a parallel ownership registry.
    """
    while True:
        run = AgentRun.objects.filter(
            bot_profile_id=bot_profile_id,
            trigger_message_id=trigger_message_id,
        ).first()
        current_time = timezone_now()
        if (
            run is None
            or run.status != AgentRun.RUNNING
            or armada_worker_run_is_stale(run, current_time=current_time)
        ):
            return
        last_seen = run.heartbeat_at or run.date_updated
        lease_remaining = (
            last_seen + timedelta(seconds=armada_worker_stale_after_seconds()) - current_time
        ).total_seconds()
        time.sleep(
            max(
                0.05,
                min(float(ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS), lease_remaining),
            )
        )


def armada_workspace_is_paused(user_profile: UserProfile, *, stream: str, topic: str) -> bool:
    if not stream:
        return False
    stream, topic = armada_topic_scope(stream, topic)
    return Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=stream,
        topic_name__iexact=topic,
        is_paused=True,
    ).exists()


def set_armada_workspace_paused(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    is_paused: bool,
) -> None:
    if not stream:
        return
    stream, topic = armada_topic_scope(stream, topic)
    with transaction.atomic(savepoint=False):
        workspace_node = armada_workspace_node_record(user_profile, stream=stream, topic=topic)
        workspace_node = Workspace.objects.select_for_update().get(id=workspace_node.id)
        if workspace_node.is_paused == is_paused:
            return
        workspace_node.is_paused = is_paused
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=["is_paused", "date_updated"])


def armada_workspace_response_policy(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return ARMADA_DEFAULT_RESPONSE_POLICY
    stream, topic = armada_topic_scope(stream, topic)
    workspace_node = Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=stream,
        topic_name__iexact=topic,
    ).first()
    if workspace_node is None:
        return ARMADA_DEFAULT_RESPONSE_POLICY
    return normalize_armada_response_policy(workspace_node.response_policy)


def set_armada_workspace_response_policy(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    response_policy: str,
) -> None:
    if not stream:
        return
    stream, topic = armada_topic_scope(stream, topic)
    clean_response_policy = normalize_armada_response_policy(response_policy)
    workspace_node = armada_workspace_node_record(user_profile, stream=stream, topic=topic)
    if workspace_node.response_policy == clean_response_policy:
        return
    workspace_node.response_policy = clean_response_policy
    workspace_node.date_updated = timezone_now()
    workspace_node.save(update_fields=["response_policy", "date_updated"])


def settle_armada_run_result_message(run: AgentRun, *, content: str) -> None:
    """Best-effort settlement through the run's canonical result Message."""
    message_id = run.result_message_id
    if message_id is None:
        return
    try:
        # Imported lazily: armada.py imports from this module.
        from zerver.lib.armada import update_armada_worker_status_message
        from zerver.lib.bot_lib import EmbeddedBotHandler

        bot_handler = EmbeddedBotHandler(run.bot_profile)
        update_armada_worker_status_message(
            bot_handler,
            progress_message_id=message_id,
            content=content,
            rendering_only=True,
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to settle run result message %s.", message_id, exc_info=True
        )


def settle_orphaned_armada_run_message(run: AgentRun) -> None:
    """Best-effort: settle an interrupted run's visible progress message."""
    settle_armada_run_result_message(
        run,
        content="Run interrupted by a service restart.",
    )


def reap_stale_armada_worker_runs(
    *,
    realm: Any | None = None,
    bot_profile: UserProfile | None = None,
    stream: str | None = None,
    topic: str | None = None,
    session_kinds: Sequence[str] | None = None,
) -> int:
    """Fail RUNNING runs whose shared heartbeat lease expired."""
    current_time = timezone_now()
    cutoff = current_time - timedelta(seconds=armada_worker_stale_after_seconds())
    stale_runs = AgentRun.objects.filter(status=AgentRun.RUNNING)
    if session_kinds is not None:
        stale_runs = stale_runs.filter(session_kind__in=list(session_kinds))
    stale_runs = stale_runs.filter(
        Q(heartbeat_at__isnull=False, heartbeat_at__lte=cutoff)
        | Q(heartbeat_at__isnull=True, date_updated__lte=cutoff)
    )
    if realm is not None:
        stale_runs = stale_runs.filter(realm=realm)
    if bot_profile is not None:
        stale_runs = stale_runs.filter(bot_profile=bot_profile)
    if stream is not None:
        stale_runs = stale_runs.filter(stream_name__iexact=stream)
    if topic is not None:
        stale_runs = stale_runs.filter(topic_name__iexact=topic)

    stale_run_ids = list(stale_runs.values_list("id", flat=True)[:100])
    reaped_count = 0
    settled_runs: list[AgentRun] = []
    store = ArmadaDatabaseWorkerRunStore()
    for run_id in stale_run_ids:
        with transaction.atomic(savepoint=False):
            run = (
                AgentRun.objects.select_for_update()
                .filter(id=run_id, status=AgentRun.RUNNING)
                .first()
            )
            if run is None:
                continue
            if not armada_worker_run_is_stale(run, current_time=current_time):
                continue
            if not store._transition(
                run,
                AgentRun.FAILED,
                stream=run.stream_name,
                topic=run.topic_name,
                result_message_id=run.result_message_id,
                error="The run became stale and was recovered.",
                allow_requeue=False,
            ):
                continue
            store._record_event(
                run,
                "stale_reaped",
                summary="Recovered a stale run.",
                payload={
                    "stale_after_seconds": armada_worker_stale_after_seconds(),
                    "heartbeat_at": run.heartbeat_at.isoformat()
                    if run.heartbeat_at is not None
                    else None,
                },
            )
            settled_runs.append(run)
            reaped_count += 1
    # Message edits happen outside the row locks.
    for run in settled_runs:
        settle_orphaned_armada_run_message(run)
    return reaped_count


def maintain_armada_worker_run_liveness(
    *,
    active_run_id: int | None,
    active_run_attempt: int | None,
    session_kinds: Sequence[str],
) -> int:
    """Renew the local run's lease, then settle abandoned leases.

    The worker's Codex RPC can be silent for minutes, so transcript events
    cannot be the only heartbeat source. Renewing the process-local active
    run before the stale pass protects a legitimate silent turn while rows
    whose worker disappeared settle after a bounded number of missed beats.
    """
    if active_run_id is not None:
        if active_run_attempt is None:
            raise ArmadaWorkerLeaseLostError(f"Run {active_run_id} has no active claim attempt.")
        current_time = timezone_now()
        renewed_count = AgentRun.objects.filter(
            id=active_run_id,
            attempts=active_run_attempt,
            status=AgentRun.RUNNING,
        ).update(heartbeat_at=current_time, date_updated=current_time)
        if renewed_count != 1:
            if AgentRun.objects.filter(
                id=active_run_id,
                attempts=active_run_attempt,
                status=AgentRun.DONE,
            ).exists():
                # The turn can settle between the liveness thread reading the
                # process-local id and issuing this update.  That exact,
                # completed attempt no longer needs a heartbeat and must not
                # have its now-warm Codex process aborted.
                return reap_stale_armada_worker_runs(session_kinds=session_kinds)
            raise ArmadaWorkerLeaseLostError(
                f"Run {active_run_id} attempt {active_run_attempt} lost its lease."
            )
    return reap_stale_armada_worker_runs(session_kinds=session_kinds)


def latest_armada_codex_run_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    before_message_id: int,
    source_message_id: int | None = None,
) -> AgentRun | None:
    stream, topic = armada_topic_scope(stream, topic)
    workspace = Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=stream,
        topic_name__iexact=topic,
    ).first()
    runs = AgentRun.objects.filter(
        bot_profile=user_profile,
        session_kind=AgentRun.SHARED,
    ).exclude(codex_thread_id="")
    if workspace is not None:
        runs = runs.filter(workspace_node=workspace)
    else:
        # Legacy runs created before workspace identity was attached remain
        # discoverable by their labels during the scratch-pad cutover.
        runs = runs.filter(stream_name__iexact=stream, topic_name__iexact=topic)
    if source_message_id is not None and source_message_id > 0:
        return (
            runs.filter(
                Q(trigger_message_id=source_message_id)
                | Q(result_message_id=source_message_id)
                | Q(source_message_id=source_message_id)
            )
            .order_by("-trigger_message_id", "-id")
            .first()
        )
    if before_message_id > 0:
        runs = runs.filter(trigger_message_id__lt=before_message_id)
    return runs.order_by("-trigger_message_id", "-id").first()


def armada_default_worker_model() -> str:
    return str(settings.ARMADA_WORKER_MODEL or "")


def armada_setting_text(value: object, default: str) -> str:
    """A setting's text, or the default when a deployment leaves it empty.

    The settings are typed as literals, but environment overrides can
    still deliver an empty string."""
    return str(value or default)


def armada_default_worker_reasoning_effort() -> str:
    effort = armada_setting_text(settings.ARMADA_WORKER_REASONING_EFFORT, "").strip()
    if effort in ARMADA_REASONING_EFFORTS:
        return effort
    return "medium"


def armada_default_worker_workdir() -> str:
    return str(settings.ARMADA_WORKER_WORKDIR or Path.cwd())


def armada_configured_worker_sandbox() -> str:
    return armada_setting_text(settings.ARMADA_WORKER_SANDBOX, "read-only")


def armada_runs_restricted() -> bool:
    return settings.ARMADA_WORKER_TRUSTED_USERS_ONLY or bool(
        settings.ARMADA_WORKER_TRUSTED_USER_IDS
    )


def armada_user_is_trusted(user_profile: UserProfile) -> bool:
    """Whether this person may drive the agent (start, steer or answer
    runs) and give a new channel a repository URL, which makes the
    server fetch it."""
    return (
        not armada_runs_restricted()
        or user_profile.is_realm_admin
        or user_profile.id in settings.ARMADA_WORKER_TRUSTED_USER_IDS
    )


def armada_topic_isolation_backend() -> str:
    return armada_setting_text(settings.ARMADA_TOPIC_ISOLATION_BACKEND, "codex-sandbox")


def armada_topic_os_user_isolation_enabled() -> bool:
    return armada_topic_isolation_backend() == "os-user"


def armada_topic_workdir_root() -> Path:
    base_workdir = armada_normalize_path(armada_default_worker_workdir())
    default_root = (
        Path("/srv/armada-workspaces")
        if armada_topic_os_user_isolation_enabled()
        else base_workdir.parent / "armada-workspaces"
    )
    configured_root = settings.ARMADA_TOPIC_WORKDIR_ROOT
    if isinstance(configured_root, str) and configured_root.strip():
        root = armada_normalize_path(configured_root.strip())
        if root == base_workdir or root.is_relative_to(base_workdir):
            return default_root
        if armada_topic_os_user_isolation_enabled():
            service_home = armada_normalize_path(Path.home())
            if root == service_home or root.is_relative_to(service_home):
                return default_root
        return Path(configured_root.strip())
    return default_root


def armada_topic_workdir_slug(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    stream, topic = armada_topic_scope(stream, topic)
    human_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", f"{stream}-{topic}").strip("-").lower()
    if not human_slug:
        human_slug = "topic"
    digest = hashlib.sha256(f"{user_profile.realm_id}\0{stream}\0{topic}".encode()).hexdigest()[:12]
    return f"{human_slug[:48]}-{digest}"


# Bytes git refuses anywhere in a ref name: ASCII control characters,
# space, DEL, and ~ ^ : ? * [ \ (refs.c, refname_disposition).
ARMADA_GIT_REF_FORBIDDEN_BYTES = frozenset([*range(0x20), 0x7F, *b" ~^:?*[\\"])


def armada_git_branch_name_is_valid(branch_name: str) -> bool:
    """What `git check-ref-format --branch` answers outside a repository,
    without running git: every topic header asks, and a subprocess per
    question cost a request two process starts."""
    if not branch_name or branch_name.startswith("-") or branch_name == "HEAD":
        return False
    try:
        name = branch_name.encode()
    except UnicodeEncodeError:
        return False
    if name.endswith(b".") or b".." in name or b"@{" in name:
        return False
    if any(byte in ARMADA_GIT_REF_FORBIDDEN_BYTES for byte in name):
        return False
    return all(
        component and not component.startswith(b".") and not component.endswith(b".lock")
        for component in name.split(b"/")
    )


def armada_topic_branch_name(topic: str) -> str:
    raw_topic = topic.strip()
    if armada_git_branch_name_is_valid(raw_topic):
        return raw_topic

    branch_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_topic).strip("-.")
    branch_name = re.sub(r"\.{2,}", ".", branch_name)
    if branch_name.endswith(".lock"):
        branch_name = branch_name.removesuffix(".lock")
    if not branch_name:
        branch_name = "topic"
    if armada_git_branch_name_is_valid(branch_name):
        return branch_name

    digest = hashlib.sha256(raw_topic.encode()).hexdigest()[:12]
    return f"topic-{digest}"


def armada_project_cache_slug(
    user_profile: UserProfile,
    *,
    stream: str,
) -> str:
    stream = stream.lower()
    human_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", stream).strip("-").lower()
    if not human_slug:
        human_slug = "project"
    digest = hashlib.sha256(f"{user_profile.realm_id}\0{stream}".encode()).hexdigest()[:12]
    return f"{human_slug[:48]}-{digest}"


def armada_expected_project_cache_path(
    user_profile: UserProfile,
    *,
    stream: str,
) -> Path:
    return (
        armada_topic_workdir_root()
        / "projects"
        / armada_project_cache_slug(
            user_profile,
            stream=stream,
        )
    ).with_suffix(".git")


def armada_project_for_stream(
    user_profile: UserProfile,
    *,
    stream: str,
    repository_url: str = "",
    repo_cache_path: str = "",
) -> Repository | None:
    if not stream:
        return None
    try:
        stream_obj, _sub = access_stream_by_name(user_profile, stream)
    except JsonableError:
        return None

    expected_cache_path = str(
        armada_expected_project_cache_path(
            user_profile,
            stream=stream_obj.name,
        )
    )
    defaults = {
        "repository_url": repository_url,
        "repo_cache_path": repo_cache_path or expected_cache_path,
        "clone_status": Repository.UNCONFIGURED,
    }
    project, _created = Repository.objects.get_or_create(
        realm=user_profile.realm,
        stream=stream_obj,
        defaults=defaults,
    )
    update_fields = []
    if repository_url and project.repository_url != repository_url:
        project.repository_url = repository_url
        update_fields.append("repository_url")
    canonical_cache_path = repo_cache_path or expected_cache_path
    if project.repo_cache_path != canonical_cache_path:
        project.repo_cache_path = canonical_cache_path
        update_fields.append("repo_cache_path")
    if update_fields:
        project.date_updated = timezone_now()
        project.save(update_fields=[*update_fields, "date_updated"])
    return project


def armada_workspace_node_default_branch(
    *,
    project: Repository | None = None,
    topic: str = "",
) -> str:
    # armada_topic_branch_name never returns an empty string (it falls back
    # to the literal "topic"), so blank candidates must be skipped here or
    # an unset project base ref would name every workspace branch "topic".
    # The topic outranks the parent branch: worktrees sharing one base
    # repository need per-topic branch names, or every sibling topic
    # collides and falls into the digest-suffix retry.
    candidates = (
        topic,
        project.default_base_ref if project is not None else "",
        "main",
    )
    for candidate in candidates:
        if not candidate.strip():
            continue
        return armada_topic_branch_name(candidate)
    return "main"


def armada_expected_topic_workdir(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> Path:
    return armada_topic_workdir_root() / armada_topic_workdir_slug(
        user_profile,
        stream=stream,
        topic=topic,
    )


def armada_topic_isolation_username_for_realm(
    realm_id: int,
    *,
    stream: str,
    topic: str,
) -> str:
    stream, topic = armada_topic_scope(stream, topic)
    digest = hashlib.sha256(f"{realm_id}\0{stream}\0{topic}".encode()).hexdigest()[:24]
    return f"armada-{digest}"


def armada_topic_isolation_username(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    return armada_topic_isolation_username_for_realm(
        user_profile.realm_id,
        stream=stream,
        topic=topic,
    )


def armada_topic_isolation_user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
    except KeyError:
        return False
    return True


def armada_topic_process_isolation(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> ArmadaProcessIsolation | None:
    if not armada_topic_os_user_isolation_enabled():
        return None
    username = armada_topic_isolation_username(user_profile, stream=stream, topic=topic)
    home = f"/home/{username}"
    return ArmadaProcessIsolation(
        username=username,
        home=home,
        codex_home=f"{home}/.codex",
    )


def armada_shared_codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME") or Path.home() / ".codex").expanduser()


def armada_codex_skills_source_dir() -> Path | None:
    configured_path = settings.ARMADA_CODEX_SKILLS_DIR
    if isinstance(configured_path, str) and configured_path.strip():
        return armada_normalize_path(configured_path)
    deploy_root = getattr(settings, "DEPLOY_ROOT", None)
    if isinstance(deploy_root, str) and deploy_root.strip():
        return armada_normalize_path(Path(deploy_root).parent / "armada" / "skills")
    return None


def armada_provision_codex_skills(codex_home: str | Path) -> list[str]:
    source_dir = armada_codex_skills_source_dir()
    if source_dir is None or not source_dir.is_dir():
        return []

    return reconcile_armada_codex_skills(source_dir, codex_home)


def armada_normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def armada_workdir_is_under_topic_root(workdir: str | Path) -> bool:
    workdir_path = armada_normalize_path(workdir)
    topic_root = armada_normalize_path(armada_topic_workdir_root())
    return workdir_path == topic_root or workdir_path.is_relative_to(topic_root)


def armada_workdir_uses_shared_git_admin(workdir: str | Path) -> bool:
    try:
        return (Path(workdir) / ".git").is_file()
    except PermissionError:
        return False


def armada_worker_runtime_isolation_error(workdir: str | None) -> str | None:
    if workdir is None or not workdir.strip():
        return "Refused to start Codex because no isolated topic worktree was selected."
    if armada_workdir_is_under_topic_root(workdir):
        return None
    return (
        "Refused to start Codex outside the topic workspace root. "
        "Pick or create a topic-scoped worktree before starting the agent."
    )


def armada_worker_sandbox_for_workdir(workdir: str | None) -> str:
    configured_sandbox = armada_configured_worker_sandbox()
    if armada_worker_runtime_isolation_error(workdir) is not None:
        return "read-only"
    if armada_topic_os_user_isolation_enabled():
        return "danger-full-access"
    if configured_sandbox == "danger-full-access":
        return "workspace-write"
    return configured_sandbox


def armada_worker_process_isolation_error(
    process_isolation: ArmadaProcessIsolation | None,
    *,
    workdir: str,
) -> str | None:
    if process_isolation is None:
        return None
    helper = str(settings.ARMADA_TOPIC_ISOLATION_HELPER or "")
    if not helper:
        return "Per-topic OS-user isolation is enabled, but no isolation helper is configured."
    if armada_worker_runtime_isolation_error(workdir) is not None:
        return (
            "Per-topic OS-user isolation refused to prepare a workdir outside "
            "the topic workspace root."
        )
    if armada_workdir_uses_shared_git_admin(workdir):
        return (
            "Per-topic OS-user isolation refused to launch from a shared Git "
            "worktree. Recreate this topic workspace as an independent clone first."
        )

    codex_home_source = str(armada_shared_codex_home())
    try:
        armada_provision_codex_skills(codex_home_source)
    except OSError as error:
        return f"Codex skills provisioning failed: {error}"
    return armada_prepare_isolated_workdir(
        isolation_username=process_isolation.username,
        workdir=workdir,
        codex_home_source=codex_home_source,
    )


def armada_prepare_isolated_workdir(
    *,
    isolation_username: str,
    workdir: str,
    codex_home_source: str | None = None,
) -> str | None:
    """Give a topic's isolation user ownership of its workdir through the
    privileged helper, so isolation-user git operations and the runtime can
    use it. Required anywhere a workdir is consumed as the isolation user,
    not only at run start: zulipdev-owned clones fail git's dubious
    ownership guard for those consumers."""
    helper = str(settings.ARMADA_TOPIC_ISOLATION_HELPER or "")
    if not helper:
        return "Per-topic OS-user isolation is enabled, but no isolation helper is configured."
    if codex_home_source is None:
        codex_home_source = str(armada_shared_codex_home())
    command = [
        "sudo",
        "-n",
        helper,
        "--user",
        isolation_username,
        "--workdir",
        workdir,
        "--topic-root",
        str(armada_topic_workdir_root()),
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
            "Per-topic OS-user isolation is enabled, but sudo or the isolation "
            "helper is not installed."
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"Per-topic OS-user isolation setup failed: {error}"
    if completed.returncode != 0:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        return (
            "Per-topic OS-user isolation setup failed: "
            f"{output or f'exit code {completed.returncode}'}"
        )
    return None


def armada_workspace_workdir_needs_repair(workspace_node: Workspace) -> bool:
    workdir = workspace_node.workdir.strip()
    if not workdir:
        return True
    workdir_path = armada_normalize_path(workdir)
    topic_root = armada_normalize_path(armada_topic_workdir_root())
    if workdir_path != topic_root and not workdir_path.is_relative_to(topic_root):
        return True
    if not workspace_node.topic_name:
        return False
    base_workdir = armada_normalize_path(armada_default_worker_workdir())
    return workdir_path == base_workdir or workdir_path.is_relative_to(base_workdir)


def armada_git_command_prefix(isolation_username: str | None = None) -> list[str]:
    if isolation_username is None:
        return ["git"]
    return [
        "sudo",
        "-n",
        "-u",
        isolation_username,
        "/usr/bin/env",
        "git",
    ]


def armada_git_branch_exists(
    base_path: Path,
    branch_name: str,
    *,
    isolation_username: str | None = None,
) -> bool:
    completed = subprocess.run(
        [
            *armada_git_command_prefix(isolation_username),
            "-C",
            str(base_path),
            "rev-parse",
            "--verify",
            f"refs/heads/{branch_name}",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    return completed.returncode == 0


def armada_path_is_git_worktree(
    path: Path,
    *,
    isolation_username: str | None = None,
) -> bool:
    try:
        completed = subprocess.run(
            [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "rev-parse",
                "--is-inside-work-tree",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def armada_git_current_branch_name(
    path: Path,
    *,
    isolation_username: str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "branch",
                "--show-current",
            ],
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
    return armada_topic_branch_name(completed.stdout.strip())


def armada_switch_workdir_to_branch(
    path: Path,
    branch_name: str,
    *,
    isolation_username: str | None = None,
) -> bool:
    try:
        if armada_git_branch_exists(
            path,
            branch_name,
            isolation_username=isolation_username,
        ):
            args = [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "switch",
                branch_name,
            ]
        else:
            args = [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "switch",
                "-c",
                branch_name,
            ]
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


def armada_workspace_relative_path(workdir: str, relative_path: str) -> Path | None:
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


def armada_resolve_file_path(
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


def armada_read_text_path(path: Path, *, isolation_username: str | None = None) -> str:
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


def armada_read_bytes_path(
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


def armada_write_text_path(
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


def armada_workspace_node_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> Workspace | None:
    """The topic's Workspace row, if it has one; one query, no git."""
    stream, topic = armada_topic_scope(stream, topic)
    return Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=stream,
        topic_name__iexact=topic,
    ).first()


def armada_workspace_node_record(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
    project: Repository | None = None,
) -> Workspace:
    """The topic's Workspace row, made or repaired from the database
    alone: no git and no file system. ensure_armada_workspace_node adds
    the worktree on disk; only a turn about to run in it needs that."""
    stream, topic = armada_topic_scope(stream, topic)
    if project is None:
        project = armada_project_for_stream(user_profile, stream=stream)
    if project is None:
        raise JsonableError(_("Could not create a repository for this channel."))
    branch_name = armada_workspace_node_default_branch(
        project=project,
        topic=topic,
    )
    expected_workdir = str(armada_expected_topic_workdir(user_profile, stream=stream, topic=topic))
    workspace_node, _created = Workspace.objects.get_or_create(
        realm=user_profile.realm,
        stream_name=stream,
        topic_name=topic,
        defaults={
            "project": project,
            "workdir": expected_workdir,
            "branch_name": branch_name,
            "base_ref": project.default_base_ref,
            "base_commit": project.default_base_commit,
            "isolation_username": (
                armada_topic_isolation_username(user_profile, stream=stream, topic=topic)
                if armada_topic_os_user_isolation_enabled()
                else ""
            ),
        },
    )
    update_fields = []
    if workspace_node.project_id != project.id:
        workspace_node.project = project
        update_fields.append("project")
    if armada_workspace_workdir_needs_repair(workspace_node):
        workspace_node.workdir = expected_workdir
        update_fields.append("workdir")
    if workspace_node.branch_name != branch_name:
        workspace_node.branch_name = branch_name
        update_fields.append("branch_name")
    if workspace_node.base_ref != project.default_base_ref:
        workspace_node.base_ref = project.default_base_ref
        update_fields.append("base_ref")
    if workspace_node.base_commit != project.default_base_commit:
        workspace_node.base_commit = project.default_base_commit
        update_fields.append("base_commit")
    expected_isolation_username = (
        armada_topic_isolation_username(user_profile, stream=stream, topic=topic)
        if armada_topic_os_user_isolation_enabled()
        else ""
    )
    if workspace_node.isolation_username != expected_isolation_username:
        workspace_node.isolation_username = expected_isolation_username
        update_fields.append("isolation_username")
    if update_fields:
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=[*update_fields, "date_updated"])
    return workspace_node


def ensure_armada_workspace_node(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> Workspace:
    """The topic's Workspace row and its worktree on disk: the channel's
    repository, the topic branch checked out, a git identity. About ten
    git processes, so a turn does this once, when it starts."""
    stream, topic = armada_topic_scope(stream, topic)
    project = armada_project_for_stream(user_profile, stream=stream)
    if project is None:
        raise JsonableError(_("Could not create a repository for this channel."))
    origin = ensure_armada_project_origin(user_profile, project)
    workspace_node = armada_workspace_node_record(
        user_profile, stream=stream, topic=topic, project=project
    )
    existing_isolation_username = (
        workspace_node.isolation_username
        if workspace_node.isolation_username
        and armada_topic_isolation_user_exists(workspace_node.isolation_username)
        else None
    )
    effective_branch = ensure_armada_topic_workdir(
        workspace_node.workdir,
        origin=origin,
        branch_name=workspace_node.branch_name,
        base_ref=project.default_base_ref,
        isolation_username=existing_isolation_username,
    )
    effective_branch_name = armada_topic_branch_name(effective_branch)
    provisioned_fields = []
    if effective_branch_name and workspace_node.branch_name != effective_branch_name:
        workspace_node.branch_name = effective_branch_name
        provisioned_fields.append("branch_name")
    if workspace_node.lifecycle_status == Workspace.ARCHIVED:
        # Its worktree was removed once the topic was done
        # (armada_worktree_retention); it is back.
        workspace_node.lifecycle_status = Workspace.ACTIVE
        provisioned_fields.append("lifecycle_status")
    if provisioned_fields:
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=[*provisioned_fields, "date_updated"])
    return workspace_node


def armada_ensure_workdir_git_identity(
    path: Path,
    *,
    isolation_username: str | None = None,
) -> None:
    # Fresh scratch repos and isolated clones have no author identity, so
    # the agent's very first commit fails and every session burns a
    # failed-tool round on `git config`.  Provision a local identity up
    # front, but never override one the repository already has.
    try:
        existing = subprocess.run(
            [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "config",
                "user.email",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return
        subprocess.run(
            [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "config",
                "user.name",
                "Armada",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        subprocess.run(
            [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "config",
                "user.email",
                f"armada-worker@{settings.EXTERNAL_HOST.split(':')[0]}",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass


def ensure_armada_topic_workdir(
    workdir: str,
    *,
    origin: str | Path,
    branch_name: str,
    base_ref: str,
    isolation_username: str | None = None,
) -> str:
    branch = armada_provision_topic_workdir(
        workdir,
        origin=origin,
        branch_name=branch_name,
        base_ref=base_ref,
        isolation_username=isolation_username,
    )
    path = Path(workdir)
    if armada_path_is_git_worktree(path, isolation_username=isolation_username):
        armada_ensure_workdir_git_identity(
            path,
            isolation_username=isolation_username,
        )
    return branch


def armada_ensure_origin_branch(
    origin: Path,
    *,
    branch_name: str,
    base_ref: str,
) -> None:
    if armada_git_branch_exists(origin, branch_name):
        return
    base_commit = armada_run_git_checked(
        [
            "git",
            "--git-dir",
            str(origin),
            "rev-parse",
            "--verify",
            f"refs/heads/{base_ref}^{{commit}}",
        ],
        error_message="Could not find the channel repository base branch.",
        timeout=10,
    ).stdout.strip()
    armada_run_git_checked(
        [
            "git",
            "--git-dir",
            str(origin),
            "update-ref",
            f"refs/heads/{branch_name}",
            base_commit,
        ],
        error_message="Could not create the topic branch.",
        timeout=10,
    )


def armada_provision_topic_workdir(
    workdir: str,
    *,
    origin: str | Path,
    branch_name: str,
    base_ref: str,
    isolation_username: str | None = None,
) -> str:
    path = Path(workdir)
    origin_path = Path(origin)
    branch_name = armada_topic_branch_name(branch_name)
    base_ref = armada_topic_branch_name(base_ref)
    armada_ensure_origin_branch(
        origin_path,
        branch_name=branch_name,
        base_ref=base_ref,
    )

    if path.exists():
        if not armada_path_is_git_worktree(
            path,
            isolation_username=isolation_username,
        ):
            # An isolation user's git refuses a zulipdev-owned clone on
            # dubious-ownership grounds. Repair the ownership through the
            # privileged helper rather than forcing a reset, but only when
            # the path is genuinely a Git workdir; anything else is the
            # wrong-file case the strict check exists for.
            if (
                isolation_username is not None
                and settings.ARMADA_TOPIC_ISOLATION_HELPER
                and armada_path_is_git_worktree(
                    path,
                    isolation_username=None,
                )
            ):
                prep_error = armada_prepare_isolated_workdir(
                    isolation_username=isolation_username,
                    workdir=str(path),
                )
                if prep_error is not None:
                    raise JsonableError(_(prep_error))
            else:
                raise JsonableError(
                    _("This topic has a non-Git workspace; reset it before starting Armada.")
                )
        origin_url = armada_run_git_checked(
            [
                *armada_git_command_prefix(isolation_username),
                "-C",
                str(path),
                "remote",
                "get-url",
                "origin",
            ],
            error_message="Could not inspect the topic repository.",
            timeout=10,
        ).stdout.strip()
        if armada_normalize_path(origin_url) != armada_normalize_path(origin_path):
            raise JsonableError(
                _("This topic workspace is not attached to its channel repository.")
            )
        current_branch = armada_git_current_branch_name(
            path,
            isolation_username=isolation_username,
        )
        if current_branch != branch_name and not armada_switch_workdir_to_branch(
            path,
            branch_name,
            isolation_username=isolation_username,
        ):
            raise JsonableError(_("Could not switch to the topic branch."))
        return branch_name

    path.parent.mkdir(parents=True, exist_ok=True)
    armada_run_git_checked(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            "--no-tags",
            "--branch",
            branch_name,
            str(origin_path),
            str(path),
        ],
        error_message="Could not create the topic workspace.",
    )
    exclude_path = path / ".git" / "info" / "exclude"
    with exclude_path.open("a", encoding="utf-8") as exclude_file:
        exclude_file.write("\n# Armada runtime outputs\n.armada/\n")
    if isolation_username is not None and settings.ARMADA_TOPIC_ISOLATION_HELPER:
        # Hand the fresh clone to the isolation user immediately: every
        # later consumer (attach checks, runtime, workspace IO) runs git as
        # that user, and a zulipdev-owned clone fails the dubious-ownership
        # guard from its first breath.
        prep_error = armada_prepare_isolated_workdir(
            isolation_username=isolation_username,
            workdir=str(path),
        )
        if prep_error is not None:
            raise JsonableError(_(prep_error))
    return branch_name


def clean_armada_repository_url(repository_url: str) -> str:
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


def armada_run_git_checked(
    command: Sequence[str],
    *,
    error_message: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = ARMADA_REPOSITORY_CLONE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            input=input_text,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        raise JsonableError(_(error_message)) from error
    if completed.returncode != 0:
        raise JsonableError(_(error_message))
    return completed


def armada_seed_empty_bare_origin(origin: Path, *, branch_name: str) -> str:
    tree = armada_run_git_checked(
        ["git", "--git-dir", str(origin), "mktree"],
        error_message="Could not initialize the channel repository.",
        input_text="",
        timeout=10,
    ).stdout.strip()
    identity_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Armada",
        "GIT_AUTHOR_EMAIL": f"armada-worker@{settings.EXTERNAL_HOST.split(':')[0]}",
        "GIT_COMMITTER_NAME": "Armada",
        "GIT_COMMITTER_EMAIL": f"armada-worker@{settings.EXTERNAL_HOST.split(':')[0]}",
    }
    commit = armada_run_git_checked(
        ["git", "--git-dir", str(origin), "commit-tree", tree, "-m", "Initialize workspace"],
        error_message="Could not initialize the channel repository.",
        env=identity_env,
        timeout=10,
    ).stdout.strip()
    armada_run_git_checked(
        ["git", "--git-dir", str(origin), "update-ref", f"refs/heads/{branch_name}", commit],
        error_message="Could not initialize the channel repository.",
        timeout=10,
    )
    return commit


def armada_initialize_bare_origin(
    origin: Path,
    *,
    repository_url: str,
    fetch_timeout_seconds: int = ARMADA_REPOSITORY_CLONE_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    armada_run_git_checked(
        ["git", "init", "--bare", "--quiet", "--initial-branch=main", str(origin)],
        error_message="Could not initialize the channel repository.",
        timeout=30,
    )
    if not repository_url:
        base_ref = "main"
        base_commit = armada_seed_empty_bare_origin(origin, branch_name=base_ref)
    else:
        armada_run_git_checked(
            ["git", "--git-dir", str(origin), "remote", "add", "upstream", repository_url],
            error_message="Could not configure the channel repository.",
            timeout=10,
        )
        armada_run_git_checked(
            [
                "git",
                "--git-dir",
                str(origin),
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                "upstream",
                "+refs/heads/*:refs/remotes/upstream/*",
            ],
            error_message="Could not clone the Git repository.",
            timeout=fetch_timeout_seconds,
        )
        remote_refs = armada_run_git_checked(
            [
                "git",
                "--git-dir",
                str(origin),
                "for-each-ref",
                "--format=%(refname:strip=3)",
                "refs/remotes/upstream",
            ],
            error_message="Could not inspect the Git repository.",
            timeout=10,
        ).stdout.splitlines()
        candidates = sorted(ref.strip() for ref in remote_refs if ref.strip() and ref != "HEAD")
        if not candidates:
            raise JsonableError(_("The Git repository has no branches."))
        base_ref = next(
            (candidate for candidate in ("main", "master") if candidate in candidates),
            candidates[0],
        )
        base_commit = armada_run_git_checked(
            [
                "git",
                "--git-dir",
                str(origin),
                "rev-parse",
                "--verify",
                f"refs/remotes/upstream/{base_ref}^{{commit}}",
            ],
            error_message="Could not inspect the Git repository.",
            timeout=10,
        ).stdout.strip()
        armada_run_git_checked(
            [
                "git",
                "--git-dir",
                str(origin),
                "update-ref",
                f"refs/heads/{base_ref}",
                base_commit,
            ],
            error_message="Could not initialize the channel repository.",
            timeout=10,
        )
    armada_run_git_checked(
        [
            "git",
            "--git-dir",
            str(origin),
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{base_ref}",
        ],
        error_message="Could not initialize the channel repository.",
        timeout=10,
    )
    return base_ref, base_commit


def ensure_armada_project_origin(
    user_profile: UserProfile,
    project: Repository,
    *,
    refresh_upstream: bool = False,
    fetch_timeout_seconds: int = ARMADA_REPOSITORY_CLONE_TIMEOUT_SECONDS,
) -> Path:
    origin = armada_expected_project_cache_path(
        user_profile,
        stream=project.stream.name,
    )
    origin.parent.mkdir(parents=True, exist_ok=True)
    lock_path = origin.parent / f".{origin.name}.lock"
    try:
        with lockfile(str(lock_path)):
            if origin.exists():
                is_bare = armada_run_git_checked(
                    ["git", "--git-dir", str(origin), "rev-parse", "--is-bare-repository"],
                    error_message="The channel repository cache is invalid.",
                    timeout=10,
                ).stdout.strip()
                if is_bare != "true":
                    raise JsonableError(_("The channel repository cache is invalid."))
                configured_url = (
                    armada_run_git_checked(
                        [
                            "git",
                            "--git-dir",
                            str(origin),
                            "config",
                            "--get",
                            "remote.upstream.url",
                        ],
                        error_message="Could not inspect the channel repository.",
                        timeout=10,
                    ).stdout.strip()
                    if project.repository_url
                    else ""
                )
                if project.repository_url and configured_url != project.repository_url:
                    raise JsonableError(_("This channel already has a different Git repository."))
                if project.repository_url and refresh_upstream:
                    armada_run_git_checked(
                        [
                            "git",
                            "--git-dir",
                            str(origin),
                            "fetch",
                            "--quiet",
                            "--prune",
                            "--no-tags",
                            "upstream",
                            "+refs/heads/*:refs/remotes/upstream/*",
                        ],
                        error_message="Could not refresh the Git repository.",
                        timeout=fetch_timeout_seconds,
                    )
                base_ref = project.default_base_ref.strip() or armada_run_git_checked(
                    ["git", "--git-dir", str(origin), "symbolic-ref", "--short", "HEAD"],
                    error_message="Could not inspect the channel repository.",
                    timeout=10,
                ).stdout.strip().removeprefix("refs/heads/")
                base_commit = armada_run_git_checked(
                    [
                        "git",
                        "--git-dir",
                        str(origin),
                        "rev-parse",
                        "--verify",
                        f"refs/heads/{base_ref}^{{commit}}",
                    ],
                    error_message="Could not inspect the channel repository.",
                    timeout=10,
                ).stdout.strip()
            else:
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{origin.name}.",
                        dir=origin.parent,
                    )
                )
                try:
                    base_ref, base_commit = armada_initialize_bare_origin(
                        temporary,
                        repository_url=project.repository_url.strip(),
                        fetch_timeout_seconds=fetch_timeout_seconds,
                    )
                    os.replace(temporary, origin)
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
    except JsonableError as error:
        project.clone_status = Repository.FAILED
        project.clone_error = str(error)
        project.date_updated = timezone_now()
        project.save(update_fields=["clone_status", "clone_error", "date_updated"])
        raise

    update_fields = []
    origin_value = str(origin)
    if project.repo_cache_path != origin_value:
        project.repo_cache_path = origin_value
        update_fields.append("repo_cache_path")
    if project.default_base_ref != base_ref:
        project.default_base_ref = base_ref
        update_fields.append("default_base_ref")
    if project.default_base_commit != base_commit:
        project.default_base_commit = base_commit
        update_fields.append("default_base_commit")
    if project.clone_status != Repository.READY:
        project.clone_status = Repository.READY
        update_fields.append("clone_status")
    if project.clone_error:
        project.clone_error = ""
        update_fields.append("clone_error")
    if update_fields:
        project.date_updated = timezone_now()
        project.save(update_fields=[*update_fields, "date_updated"])
    return origin


def configure_armada_channel_repository_workspace(
    user_profile: UserProfile,
    *,
    stream: str,
    repository_url: str,
    fetch_timeout_seconds: int = ARMADA_REPOSITORY_CLONE_TIMEOUT_SECONDS,
) -> Workspace:
    """Give a channel its repository and fetch it.  Channel creation calls
    this inside its request; an operator attaching a repository too large
    to fetch within the web server's request limit calls it from
    `manage.py shell` with a longer fetch_timeout_seconds
    (docs/armada/deploy.md, 6.5)."""
    stream, _topic = armada_topic_scope(stream, "")
    clean_url = clean_armada_repository_url(repository_url)
    project = armada_project_for_stream(user_profile, stream=stream)
    if project is None:
        raise JsonableError(_("Could not configure this channel workspace."))
    if project.repository_url and project.repository_url != clean_url:
        raise JsonableError(_("This channel already has a different Git repository."))
    origin = armada_expected_project_cache_path(user_profile, stream=stream)
    if not project.repository_url and origin.exists():
        raise JsonableError(
            _(
                "This channel already has local work. Reset its workspace before "
                "attaching a Git repository."
            )
        )
    if project.repository_url != clean_url:
        project.repository_url = clean_url
        project.clone_status = Repository.UNCONFIGURED
        project.clone_error = ""
        project.date_updated = timezone_now()
        project.save(
            update_fields=[
                "repository_url",
                "clone_status",
                "clone_error",
                "date_updated",
            ]
        )
    ensure_armada_project_origin(
        user_profile,
        project,
        refresh_upstream=True,
        fetch_timeout_seconds=fetch_timeout_seconds,
    )
    return ensure_armada_workspace_node(
        user_profile,
        stream=stream,
        topic="",
    )


def maybe_refactor_armada_workspace_on_move(
    user_profile: UserProfile,
    *,
    old_stream: Stream,
    old_topic: str,
    new_stream: Stream,
    new_topic: str,
    moved_all_visible_messages: bool,
) -> ArmadaTopicRenameRefactorResult | None:
    old_stream_name, old_topic_name = armada_topic_scope(old_stream.name, old_topic)
    new_stream_name, new_topic_name = armada_topic_scope(new_stream.name, new_topic)
    if old_stream_name == new_stream_name and old_topic_name == new_topic_name:
        return None
    workspace_node = Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=old_stream_name,
        topic_name__iexact=old_topic_name,
    ).first()
    monitors = AgentMonitor.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=old_stream_name,
        topic_name__iexact=old_topic_name,
    )
    if workspace_node is None and not monitors.exists():
        return None

    target = (
        Workspace.objects.filter(
            realm=user_profile.realm,
            stream_name__iexact=new_stream_name,
            topic_name__iexact=new_topic_name,
        ).first()
        if workspace_node is not None
        else None
    )
    if target is not None and workspace_node is not None and target.id != workspace_node.id:
        monitor_count = monitors.update(
            stream_name=new_stream_name,
            topic_name=new_topic_name,
            date_updated=timezone_now(),
        )
        return ArmadaTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Workspace warning: I did not move the workspace context "
                "because the destination topic already has its own context. "
                + (
                    f"I moved {monitor_count} wall monitor(s) with the topic. "
                    if monitor_count
                    else ""
                )
                + "Review the sidebar before starting more Codex work."
            ),
        )

    if not moved_all_visible_messages:
        return ArmadaTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Workspace warning: I did not move the workspace context "
                "because only part of the topic was moved. The old topic keeps the existing "
                f"Codex thread/worktree; this topic will get a fresh context when {ARMADA_AGENT_NAME} is used."
            ),
        )

    busy_run_exists = AgentRun.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=old_stream_name,
        topic_name__iexact=old_topic_name,
        session_kind=AgentRun.SHARED,
        status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
    ).exists()
    if busy_run_exists:
        reap_stale_armada_worker_runs(
            realm=user_profile.realm,
            stream=old_stream_name,
            topic=old_topic_name,
        )
        busy_run_exists = AgentRun.objects.filter(
            realm=user_profile.realm,
            stream_name__iexact=old_stream_name,
            topic_name__iexact=old_topic_name,
            session_kind=AgentRun.SHARED,
            status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
        ).exists()
    if busy_run_exists:
        return ArmadaTopicRenameRefactorResult(
            stream_name=new_stream.name,
            topic_name=new_topic,
            message=(
                "Workspace warning: I did not rename the workspace context "
                "because the old topic still has an active run. Wait for it to "
                f"finish, then ask {ARMADA_AGENT_NAME} to pin or migrate the workspace explicitly."
            ),
        )

    timestamp = timezone_now()
    if workspace_node is not None:
        workspace_node.stream_name = new_stream_name
        workspace_node.topic_name = new_topic_name
        workspace_node.date_updated = timestamp
        workspace_node.save(update_fields=["stream_name", "topic_name", "date_updated"])
    monitor_count = monitors.update(
        stream_name=new_stream_name,
        topic_name=new_topic_name,
        date_updated=timestamp,
    )

    return ArmadaTopicRenameRefactorResult(
        stream_name=new_stream.name,
        topic_name=new_topic,
        message=(
            "The workspace label moved with this topic."
            if workspace_node is not None
            else f"The wall moved {monitor_count} monitor(s) with this topic."
        ),
    )


def maybe_refactor_armada_stream_workspaces_on_rename(
    user_profile: UserProfile,
    *,
    old_stream_name: str,
    new_stream_name: str,
) -> str | None:
    old_scope_stream, _topic = armada_topic_scope(old_stream_name, "")
    new_scope_stream, _topic = armada_topic_scope(new_stream_name, "")
    if old_scope_stream == new_scope_stream:
        return None

    workspace_nodes = Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=old_scope_stream,
    )
    monitors = AgentMonitor.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=old_scope_stream,
    )
    if not workspace_nodes.exists() and not monitors.exists():
        return None

    if Workspace.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=new_scope_stream,
    ).exists():
        monitor_count = monitors.update(
            stream_name=new_scope_stream,
            date_updated=timezone_now(),
        )
        return (
            "Workspace warning: I did not rename the channel context "
            "because the destination channel name already has its own context. "
            + (
                f"I moved {monitor_count} wall monitor(s) with the channel. "
                if monitor_count
                else ""
            )
            + "Review the sidebar before starting more Codex work."
        )

    timestamp = timezone_now()
    workspace_count = workspace_nodes.update(stream_name=new_scope_stream, date_updated=timestamp)
    monitor_count = monitors.update(stream_name=new_scope_stream, date_updated=timestamp)

    return (
        f"Channel context renamed from `{old_stream_name}` to `{new_stream_name}` "
        f"for {workspace_count} workspace layer(s) and {monitor_count} wall monitor(s). "
        "Existing worktree paths were kept unchanged; "
        "check the sidebar before starting Codex work."
    )


def armada_git_output(
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
        # Captured output is always bytes here, even with text=True.
        stderr_text = stderr.decode(errors="replace").strip() if isinstance(stderr, bytes) else ""
        logger.warning(
            "Git command timed out in %s with args %s: %s",
            workdir,
            args,
            stderr_text,
            exc_info=True,
        )
        return ""
    except (FileNotFoundError, OSError):
        logger.warning(
            "Git command failed to start in %s with args %s.",
            workdir,
            args,
            exc_info=True,
        )
        return ""
    if completed.returncode != 0:
        logger.warning(
            "Git command exited with status %s in %s with args %s: %s",
            completed.returncode,
            workdir,
            args,
            (completed.stderr or "").strip(),
        )
        return ""
    return completed.stdout.strip()


ARMADA_GIT_CHANGED_FILES_LIMIT = 50


def armada_git_changed_file_from_status_line(line: str) -> dict[str, str] | None:
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


def parse_armada_git_status_delta(
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
            changed_file = armada_git_changed_file_from_status_line(line)
            if changed_file is not None and len(changed_files) <= ARMADA_GIT_CHANGED_FILES_LIMIT:
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
        "changed_files": changed_files[:ARMADA_GIT_CHANGED_FILES_LIMIT],
        "changed_files_truncated": len(changed_files) > ARMADA_GIT_CHANGED_FILES_LIMIT,
        "detached": detached,
        "branch_matches_topic": bool(topic and not detached and branch == topic),
    }


def armada_git_remote_label(remote_url: str, repo_root: str) -> str:
    clean_url = remote_url.strip()
    if clean_url:
        if clean_url.startswith("git@") and ":" in clean_url:
            return clean_url.split(":", 1)[1].removesuffix(".git").strip("/") or clean_url
        parsed_url = urlparse(clean_url)
        if parsed_url.scheme and parsed_url.netloc:
            return parsed_url.path.removesuffix(".git").strip("/") or parsed_url.netloc
        return Path(clean_url.removesuffix(".git")).name or clean_url
    return Path(repo_root).name


def build_armada_workspace_context(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> ArmadaWorkspaceContext:
    # The turn provisioned the worktree before it loads its session; the
    # addressing worker only needs the labels.
    workspace_node = armada_workspace_node_record(user_profile, stream=stream, topic=topic)
    workdir = armada_worker_workdir_for_workspace(workspace_node)
    project = workspace_node.project
    workspace_repo = project.repository_url.strip() if project is not None else ""
    repository = armada_logical_repository_label(
        stream=stream,
        context_repo=workspace_repo,
        parent_repo="",
    )
    worktree = armada_logical_worktree_label(
        stream=stream,
        topic=topic,
        fallback=Path(workdir).name,
    )
    return {
        "repository": repository,
        "remote_url": armada_pinned_repository_value(workspace_repo) or "",
        "worktree": worktree,
        "workdir": workdir,
        "branch": workspace_node.branch_name.strip() or armada_workspace_node_default_branch(),
        "head": "",
    }


def armada_repo_artifact_value_is_durable(repo_value: str) -> bool:
    value = repo_value.strip()
    if not value:
        return False
    if armada_repo_artifact_value_is_local_topic_path(value):
        return True
    if value.startswith("git@") and ":" in value:
        return True
    parsed_url = urlparse(value)
    if parsed_url.scheme in {"file", "git", "http", "https", "ssh"} and (
        parsed_url.netloc or parsed_url.path
    ):
        return True
    return re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\s+<[^>\n]+>)?", value) is not None


def armada_repo_artifact_value_is_local_topic_path(repo_value: str) -> bool:
    value = repo_value.strip()
    if not value or not Path(value).is_absolute():
        return False
    try:
        local_path = armada_normalize_path(value)
    except (OSError, RuntimeError):
        return False
    try:
        topic_root = armada_normalize_path(armada_topic_workdir_root())
        return armada_workdir_is_under_topic_root(local_path) and local_path != topic_root
    except OSError:
        return False


def armada_pinned_repository_value(repo_value: str) -> str:
    value = repo_value.strip()
    if not value:
        return ""
    if not armada_repo_artifact_value_is_durable(value):
        return ""
    if armada_repo_artifact_value_is_local_topic_path(value):
        return ""
    return value


def armada_logical_repository_label(
    *,
    stream: str,
    context_repo: str,
    parent_repo: str,
) -> str:
    for repo_value in (context_repo, parent_repo):
        pinned_repo = armada_pinned_repository_value(repo_value)
        if pinned_repo:
            return pinned_repo
    return stream


def armada_logical_worktree_label(*, stream: str, topic: str, fallback: str) -> str:
    if topic:
        return topic
    if stream:
        return stream
    return fallback


def armada_worker_model_for_workspace(workspace_node: Workspace | None) -> str:
    if workspace_node is not None and workspace_node.model.strip():
        return workspace_node.model.strip()
    return armada_default_worker_model()


def armada_worker_reasoning_effort_for_workspace(workspace_node: Workspace | None) -> str:
    if workspace_node is not None and workspace_node.reasoning_effort.strip():
        return workspace_node.reasoning_effort.strip()
    return armada_default_worker_reasoning_effort()


def armada_worker_workdir_for_workspace(workspace_node: Workspace) -> str:
    return workspace_node.workdir.strip() or armada_default_worker_workdir()


def armada_worker_model_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return armada_default_worker_model()
    return armada_worker_model_for_workspace(
        armada_workspace_node_for_topic(user_profile, stream=stream, topic=topic)
    )


def armada_worker_reasoning_effort_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    if not stream:
        return armada_default_worker_reasoning_effort()
    return armada_worker_reasoning_effort_for_workspace(
        armada_workspace_node_for_topic(user_profile, stream=stream, topic=topic)
    )


def armada_worker_workdir_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    """The topic's worktree for what only reads or runs a command in it (a
    monitor's tick, a run's label): provisioned when it is missing, for
    one stat when it is there. A turn provisions its own."""
    if not stream:
        return armada_default_worker_workdir()
    workdir = armada_worker_workdir_for_workspace(
        armada_workspace_node_record(user_profile, stream=stream, topic=topic)
    )
    if os.path.isdir(workdir):
        return workdir
    return armada_worker_workdir_for_workspace(
        ensure_armada_workspace_node(user_profile, stream=stream, topic=topic)
    )


def armada_worker_sandbox_for_topic(
    user_profile: UserProfile,
    *,
    stream: str,
    topic: str,
) -> str:
    return armada_worker_sandbox_for_workdir(
        armada_worker_workdir_for_topic(user_profile, stream=stream, topic=topic)
    )


def build_armada_worker_runtime_from_settings(
    *,
    model_override: str | None = None,
    workdir_override: str | None = None,
    reasoning_effort_override: str | None = None,
    sandbox_override: str | None = None,
    process_isolation_override: ArmadaProcessIsolation | None = None,
    process_isolation_error: str | None = None,
) -> ArmadaWorkerRuntime:
    # A string, not the setting's literal type: an environment override
    # can name a runtime this build does not know.
    runtime: str = settings.ARMADA_WORKER_AGENT_RUNTIME
    model = model_override if model_override is not None else settings.ARMADA_WORKER_MODEL
    workdir = workdir_override if workdir_override is not None else settings.ARMADA_WORKER_WORKDIR
    sandbox = (
        sandbox_override
        if sandbox_override is not None
        else armada_worker_sandbox_for_workdir(workdir)
    )
    reasoning_effort = (
        reasoning_effort_override
        if reasoning_effort_override is not None
        else armada_default_worker_reasoning_effort()
    )
    isolation_error = armada_worker_runtime_isolation_error(workdir)
    if runtime != "disabled" and isolation_error is not None:
        return ArmadaWorkerRuntime(ArmadaDisabledAgentRunner(isolation_error))
    if runtime != "disabled" and process_isolation_error is not None:
        return ArmadaWorkerRuntime(ArmadaDisabledAgentRunner(process_isolation_error))
    if runtime != "disabled" and process_isolation_override is None:
        try:
            armada_provision_codex_skills(armada_shared_codex_home())
        except OSError as error:
            return ArmadaWorkerRuntime(
                ArmadaDisabledAgentRunner(f"Codex skills provisioning failed: {error}")
            )
    if runtime == "codex-app-server":
        return ArmadaWorkerRuntime(
            ArmadaCodexAppServerToolRunner(
                codex_bin=settings.ARMADA_WORKER_CODEX_BIN,
                workdir=workdir,
                model=model,
                sandbox=sandbox,
                approval_policy=settings.ARMADA_WORKER_APPROVAL_POLICY,
                timeout_seconds=settings.ARMADA_WORKER_TIMEOUT_SECONDS,
                output_limit=settings.ARMADA_WORKER_OUTPUT_LIMIT,
                service_tier=settings.ARMADA_WORKER_SERVICE_TIER,
                fast_mode_enabled=settings.ARMADA_WORKER_FAST_MODE,
                memories_enabled=settings.ARMADA_WORKER_CODEX_MEMORIES,
                reasoning_effort=reasoning_effort,
                ephemeral=settings.ARMADA_WORKER_CODEX_EPHEMERAL,
                process_isolation=process_isolation_override,
            )
        )
    if runtime != "disabled":
        return ArmadaWorkerRuntime(
            ArmadaDisabledAgentRunner(
                f"The agent runtime is unsupported: {runtime!r}. "
                "Use codex-app-server for worker turns; configure ambient "
                "message routing with ARMADA_ADDRESSING_ROUTER_RUNTIME."
            )
        )
    return ArmadaWorkerRuntime(
        ArmadaDisabledAgentRunner(
            "The agent runtime is disabled. Set ARMADA_WORKER_AGENT_RUNTIME "
            "to 'codex-app-server' for direct Codex app-server control."
        )
    )


def build_armada_addressing_intent_classifier_from_settings() -> (
    ArmadaAddressingIntentClassifier | None
):
    runtime = settings.ARMADA_ADDRESSING_ROUTER_RUNTIME
    if runtime == "gemini-interactions":
        return ArmadaGeminiInteractionsAddressingIntentClassifier(
            api_key=settings.ARMADA_GEMINI_INTERACTIONS_API_KEY,
            model=settings.ARMADA_ADDRESSING_ROUTER_GEMINI_MODEL,
            timeout_seconds=4,
            output_limit=1000,
            url=settings.ARMADA_GEMINI_INTERACTIONS_URL,
            thinking_level=settings.ARMADA_ADDRESSING_ROUTER_GEMINI_THINKING_LEVEL,
        )
    return None


class ArmadaDatabaseWorkerRunStore:
    def __init__(self) -> None:
        # A store instance belongs to one queue delivery. Once it claims a run,
        # its attempt number becomes the fencing token for every worker-owned
        # read and write made through that instance. Web/pre-claim stores have
        # no token and retain their existing control-plane behavior.
        self._claimed_attempts: dict[tuple[int, int], int] = {}

    def _run_key(self, bot_profile_id: int, message_id: int) -> tuple[int, int]:
        return (bot_profile_id, message_id)

    def _remember_claimed_attempt(self, run: AgentRun) -> None:
        self._claimed_attempts[self._run_key(run.bot_profile_id, run.trigger_message_id)] = (
            run.attempts
        )

    def _claimed_attempt(self, bot_profile_id: int, message_id: int) -> int | None:
        return self._claimed_attempts.get(self._run_key(bot_profile_id, message_id))

    def _require_worker_attempt(
        self,
        *,
        bot_profile_id: int,
        message_id: int,
        run: AgentRun | None,
        require_running: bool = True,
    ) -> None:
        claimed_attempt = self._claimed_attempt(bot_profile_id, message_id)
        if claimed_attempt is None:
            return
        if (
            run is None
            or run.attempts != claimed_attempt
            or (require_running and run.status != AgentRun.RUNNING)
        ):
            raise ArmadaWorkerLeaseLostError(
                f"Run for message {message_id} lost attempt {claimed_attempt}."
            )

    @contextlib.contextmanager
    def worker_completion(self, bot_handler: Any, message_id: int) -> Any:
        """Serialize final visible writes against cancel/reap transitions."""
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        with transaction.atomic(savepoint=False):
            run = (
                AgentRun.objects.select_for_update()
                .filter(bot_profile=user_profile, trigger_message_id=message_id)
                .first()
            )
            self._require_worker_attempt(
                bot_profile_id=user_profile.id,
                message_id=message_id,
                run=run,
            )
            yield

    def _lock_workspace_node(
        self,
        workspace_node: Workspace | None,
    ) -> Workspace | None:
        if workspace_node is None:
            return None
        return Workspace.objects.select_for_update().get(id=workspace_node.id)

    def _lock_realm(self, user_profile: UserProfile) -> None:
        Realm.objects.select_for_update().only("id").get(id=user_profile.realm_id)

    def _realm_run_limit_reached(
        self,
        user_profile: UserProfile,
        *,
        exclude_run_id: int | None = None,
    ) -> bool:
        realm_limit = settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM
        if realm_limit <= 0:
            return False
        busy_runs = AgentRun.objects.filter(
            realm=user_profile.realm,
            status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
        )
        if exclude_run_id is not None:
            busy_runs = busy_runs.exclude(id=exclude_run_id)
        return busy_runs.count() >= realm_limit

    def _shared_topic_run_limit_reached(
        self,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
        exclude_run_id: int | None = None,
    ) -> bool:
        topic_limit = settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC
        if topic_limit <= 0:
            return False
        busy_runs = AgentRun.objects.filter(
            realm=user_profile.realm,
            bot_profile=user_profile,
            stream_name__iexact=stream,
            topic_name__iexact=topic,
            session_kind=AgentRun.SHARED,
            status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
        )
        if exclude_run_id is not None:
            busy_runs = busy_runs.exclude(id=exclude_run_id)
        return busy_runs.count() >= topic_limit

    def _shared_run_is_topic_blocked(
        self,
        run: AgentRun,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
    ) -> bool:
        if run.session_kind != AgentRun.SHARED:
            return False
        return self._shared_topic_run_limit_reached(
            user_profile,
            stream=stream,
            topic=topic,
            exclude_run_id=run.id,
        )

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
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        reap_stale_armada_worker_runs(
            realm=user_profile.realm,
            bot_profile=user_profile,
            stream=stream,
            topic=topic,
        )

        run = (
            AgentRun.objects.filter(
                realm=user_profile.realm,
                bot_profile=user_profile,
                stream_name__iexact=stream,
                topic_name__iexact=topic,
                session_kind=AgentRun.SHARED,
                status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
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
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)

        with transaction.atomic(savepoint=False):
            run = (
                AgentRun.objects.select_for_update()
                .filter(
                    bot_profile=user_profile,
                    trigger_message_id=message_id,
                    stream_name__iexact=stream,
                    topic_name__iexact=topic,
                    status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
                )
                .first()
            )
            self._require_worker_attempt(
                bot_profile_id=user_profile.id,
                message_id=message_id,
                run=run,
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

    def ensure_progress_message(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        message: dict[str, Any],
        stream: str,
        topic: str,
        content: str,
    ) -> int | None:
        """Create and bind the run's ordinary result Message atomically.

        A redelivered trigger reuses the already-bound Message. If a side
        pump claimed the row first, that worker owns creation and this path
        returns its binding (which may still be pending) rather than racing it.
        """
        if message_id <= 0:
            return None
        stream, topic = armada_topic_scope(stream, topic)
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)

        with transaction.atomic(savepoint=False):
            run = (
                AgentRun.objects.select_for_update()
                .filter(
                    bot_profile=user_profile,
                    trigger_message_id=message_id,
                    stream_name__iexact=stream,
                    topic_name__iexact=topic,
                    status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
                )
                .first()
            )
            if run is None or run.result_message_id is not None:
                return run.result_message_id if run is not None else None
            if run.status != AgentRun.QUEUED:
                return None

            result = bot_handler.send_reply(message, content)
            progress_message_id = result.get("id") if isinstance(result, dict) else None
            if not isinstance(progress_message_id, int):
                raise RuntimeError("Progress message send returned no message id.")
            run.result_message_id = progress_message_id
            run.date_updated = timezone_now()
            run.save(update_fields=["result_message_id", "date_updated"])
            self._record_event(
                run,
                "progress_registered",
                summary="Registered started-progress message.",
                payload={"result_message_id": progress_message_id},
            )
            return progress_message_id

    def start_blocked_reason(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        requesting_user_id: int | None,
    ) -> str | None:
        if not stream:
            return "Codex runs currently require a channel topic."
        if message_id <= 0 or requesting_user_id is None:
            return None
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        reap_stale_armada_worker_runs(realm=user_profile.realm)

        try:
            requesting_user = UserProfile.objects.get(
                id=requesting_user_id,
                realm=user_profile.realm,
            )
        except UserProfile.DoesNotExist:
            return "Could not identify the requesting user."

        if not armada_user_is_trusted(requesting_user):
            return f"This user is not allowed to start {ARMADA_AGENT_NAME}'s runs."
        if armada_workspace_is_paused(user_profile, stream=stream, topic=topic):
            return "The session is paused until a resume control is posted."

        queued_run = AgentRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
            session_kind=AgentRun.SHARED,
            stream_name__iexact=stream,
            topic_name__iexact=topic,
            status=AgentRun.QUEUED,
        ).first()
        if queued_run is not None:
            return None

        topic_limit = settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC
        realm_limit = settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM
        busy_runs = AgentRun.objects.filter(
            realm=user_profile.realm,
            status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
        )
        if (
            topic_limit > 0
            and busy_runs.filter(
                bot_profile=user_profile,
                stream_name__iexact=stream,
                topic_name__iexact=topic,
                session_kind=AgentRun.SHARED,
            ).count()
            >= topic_limit
        ):
            return "This topic already has an active run."
        if realm_limit > 0 and busy_runs.count() >= realm_limit:
            return (
                f"{ARMADA_AGENT_NAME} already has the maximum number of active runs in this realm."
            )
        return None

    def side_start_blocked_reason(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        requesting_user_id: int | None,
    ) -> str | None:
        """Apply run admission without treating a native /btw as a main turn."""
        if not stream:
            return "Codex runs currently require a channel topic."
        if message_id <= 0 or requesting_user_id is None:
            return None
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        reap_stale_armada_worker_runs(realm=user_profile.realm)
        try:
            requesting_user = UserProfile.objects.get(
                id=requesting_user_id,
                realm=user_profile.realm,
            )
        except UserProfile.DoesNotExist:
            return "Could not identify the requesting user."
        if not armada_user_is_trusted(requesting_user):
            return f"This user is not allowed to start {ARMADA_AGENT_NAME}'s runs."
        if armada_workspace_is_paused(user_profile, stream=stream, topic=topic):
            return "The session is paused until a resume control is posted."
        if AgentRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
            session_kind=AgentRun.SIDE,
            stream_name__iexact=stream,
            topic_name__iexact=topic,
            status=AgentRun.QUEUED,
        ).exists():
            return None
        realm_limit = settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM
        busy_runs = AgentRun.objects.filter(
            realm=user_profile.realm,
            status__in=ARMADA_WORKER_RUN_BUSY_STATUSES,
        )
        if realm_limit > 0 and busy_runs.count() >= realm_limit:
            return (
                f"{ARMADA_AGENT_NAME} already has the maximum number of active runs in this realm."
            )
        return None

    def start(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        source_message_id: int | None = None,
    ) -> bool:
        if not stream:
            return False
        if message_id <= 0:
            return True
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)

        try:
            with transaction.atomic(savepoint=False):
                self._lock_realm(user_profile)
                workspace_node = self._workspace_node_for_topic(
                    user_profile,
                    stream=stream,
                    topic=topic,
                )
                workspace_node = self._lock_workspace_node(workspace_node)
                run = (
                    AgentRun.objects.select_for_update()
                    .filter(bot_profile=user_profile, trigger_message_id=message_id)
                    .first()
                )
                if run is not None and run.status == AgentRun.QUEUED:
                    return (
                        run.session_kind == AgentRun.SHARED
                        and run.stream_name.casefold() == stream.casefold()
                        and run.topic_name.casefold() == topic.casefold()
                        and run.source_message_id == source_message_id
                    )
                if run is not None and run.status in ARMADA_WORKER_RUN_ACTIVE_STATUSES:
                    return False
                if self._shared_topic_run_limit_reached(
                    user_profile,
                    stream=stream,
                    topic=topic,
                    exclude_run_id=run.id if run is not None else None,
                ):
                    return False
                if self._realm_run_limit_reached(
                    user_profile,
                    exclude_run_id=run.id if run is not None else None,
                ):
                    return False
                if run is None:
                    run = AgentRun.objects.create(
                        realm=user_profile.realm,
                        bot_profile=user_profile,
                        trigger_message_id=message_id,
                        stream_name=stream,
                        topic_name=topic,
                        workspace_node=workspace_node,
                        source_stream_name=stream if source_message_id is not None else "",
                        source_topic_name=topic if source_message_id is not None else "",
                        source_message_id=source_message_id,
                        status=AgentRun.QUEUED,
                        **self._runtime_labels(workspace_node),
                    )
                    self._record_event(
                        run,
                        "queued",
                        summary="Queued the run.",
                        payload={"stream": stream, "topic": topic},
                    )
                    return True
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
                run.session_kind = AgentRun.SHARED
                run.source_stream_name = stream if source_message_id is not None else ""
                run.source_topic_name = topic if source_message_id is not None else ""
                run.source_message_id = source_message_id
                run.save(
                    update_fields=[
                        "session_kind",
                        "source_stream_name",
                        "source_topic_name",
                        "source_message_id",
                    ]
                )
                self._record_event(
                    run,
                    "queued",
                    summary="Re-queued the run.",
                    payload={"stream": stream, "topic": topic},
                )
                return True
        except IntegrityError:
            return False

    def start_side(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        stream: str,
        topic: str,
        requesting_user_id: int | None,
        source_message_id: int,
    ) -> bool:
        """Create telemetry for an ordinary threaded Message's native /btw turn."""
        if not stream or message_id <= 0 or source_message_id <= 0:
            return False
        stream, topic = armada_topic_scope(stream, topic)
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        requesting_user = None
        if requesting_user_id is not None:
            requesting_user = UserProfile.objects.filter(
                id=requesting_user_id,
                realm=user_profile.realm,
            ).first()
        try:
            with transaction.atomic(savepoint=False):
                self._lock_realm(user_profile)
                workspace_node = self._workspace_node_for_topic(
                    user_profile,
                    stream=stream,
                    topic=topic,
                )
                workspace_node = self._lock_workspace_node(workspace_node)
                run = (
                    AgentRun.objects.select_for_update()
                    .filter(bot_profile=user_profile, trigger_message_id=message_id)
                    .first()
                )
                if run is not None and run.status == AgentRun.QUEUED:
                    return (
                        run.session_kind == AgentRun.SIDE
                        and run.stream_name.casefold() == stream.casefold()
                        and run.topic_name.casefold() == topic.casefold()
                        and run.source_message_id == source_message_id
                    )
                if run is not None and run.status in ARMADA_WORKER_RUN_ACTIVE_STATUSES:
                    return False
                if self._realm_run_limit_reached(
                    user_profile,
                    exclude_run_id=run.id if run is not None else None,
                ):
                    return False
                if run is None:
                    run = AgentRun.objects.create(
                        realm=user_profile.realm,
                        bot_profile=user_profile,
                        requesting_user=requesting_user,
                        trigger_message_id=message_id,
                        stream_name=stream,
                        topic_name=topic,
                        workspace_node=workspace_node,
                        session_kind=AgentRun.SIDE,
                        source_stream_name=stream,
                        source_topic_name=topic,
                        source_message_id=source_message_id,
                        status=AgentRun.QUEUED,
                        summary="Queued threaded side conversation.",
                        **self._runtime_labels(workspace_node),
                    )
                else:
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
                    run.requesting_user = requesting_user
                    run.workspace_node = workspace_node
                    run.session_kind = AgentRun.SIDE
                    run.source_stream_name = stream
                    run.source_topic_name = topic
                    run.source_message_id = source_message_id
                    run.save(
                        update_fields=[
                            "requesting_user",
                            "workspace_node",
                            "session_kind",
                            "source_stream_name",
                            "source_topic_name",
                            "source_message_id",
                        ]
                    )
                self._record_event(
                    run,
                    "queued",
                    summary="Queued native Codex side conversation.",
                    payload={
                        "stream": stream,
                        "topic": topic,
                        "source_message_id": source_message_id,
                    },
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
        redelivered: bool = False,
    ) -> bool:
        if not stream:
            return False
        if message_id <= 0:
            return True
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)

        current_time = timezone_now()
        try:
            with transaction.atomic(savepoint=False):
                self._lock_realm(user_profile)
                workspace_node = self._workspace_node_for_topic(
                    user_profile,
                    stream=stream,
                    topic=topic,
                )
                workspace_node = self._lock_workspace_node(workspace_node)
                topic_is_paused = (
                    workspace_node.is_paused
                    if workspace_node is not None
                    else armada_workspace_is_paused(
                        user_profile,
                        stream=stream,
                        topic=topic,
                    )
                )
                run = (
                    AgentRun.objects.select_for_update()
                    .filter(bot_profile=user_profile, trigger_message_id=message_id)
                    .first()
                )
                if self._realm_run_limit_reached(
                    user_profile,
                    exclude_run_id=run.id if run is not None else None,
                ):
                    return False
                if run is None:
                    if topic_is_paused:
                        return False
                    if self._shared_topic_run_limit_reached(
                        user_profile,
                        stream=stream,
                        topic=topic,
                    ):
                        return False
                    run = AgentRun.objects.create(
                        realm=user_profile.realm,
                        bot_profile=user_profile,
                        trigger_message_id=message_id,
                        stream_name=stream,
                        topic_name=topic,
                        workspace_node=workspace_node,
                        status=AgentRun.RUNNING,
                        attempts=1,
                        heartbeat_at=current_time,
                        **self._runtime_labels(workspace_node),
                    )
                    self._remember_claimed_attempt(run)
                    self._record_event(
                        run,
                        "claimed",
                        summary="Claimed the run.",
                        payload={
                            "stream": stream,
                            "topic": topic,
                            "attempts": run.attempts,
                            "created": True,
                        },
                    )
                    return True

                if topic_is_paused:
                    if run.status in ARMADA_WORKER_RUN_BUSY_STATUSES:
                        self._transition(
                            run,
                            AgentRun.FAILED,
                            stream=stream,
                            topic=topic,
                            result_message_id=run.result_message_id,
                            error="The topic is paused.",
                            allow_requeue=False,
                        )
                    self._record_claim_dropped(
                        run,
                        stream=stream,
                        topic=topic,
                        reason="topic_paused",
                    )
                    return False

                if run.status == AgentRun.QUEUED:
                    if self._shared_run_is_topic_blocked(
                        run,
                        user_profile,
                        stream=stream,
                        topic=topic,
                    ):
                        self._record_claim_dropped(
                            run,
                            stream=stream,
                            topic=topic,
                            reason="topic_busy",
                        )
                        return False
                    return self._claim_locked_run(
                        run,
                        stream=stream,
                        topic=topic,
                        workspace_node=workspace_node,
                        current_time=current_time,
                        stale_reclaim=False,
                    )

                if run.status == AgentRun.RUNNING:
                    if not self._run_is_stale(run, current_time=current_time):
                        self._record_claim_dropped(
                            run,
                            stream=stream,
                            topic=topic,
                            reason="already_running",
                        )
                        return False
                    if run.attempts >= ARMADA_WORKER_MAX_CLAIM_ATTEMPTS:
                        self._transition(
                            run,
                            AgentRun.FAILED,
                            stream=stream,
                            topic=topic,
                            result_message_id=run.result_message_id,
                            error="The run exceeded its stale-claim retry limit.",
                            allow_requeue=False,
                        )
                        self._record_claim_dropped(
                            run,
                            stream=stream,
                            topic=topic,
                            reason="stale_retry_limit",
                        )
                        return False
                    if self._shared_run_is_topic_blocked(
                        run,
                        user_profile,
                        stream=stream,
                        topic=topic,
                    ):
                        self._record_claim_dropped(
                            run,
                            stream=stream,
                            topic=topic,
                            reason="topic_busy",
                        )
                        return False
                    return self._claim_locked_run(
                        run,
                        stream=stream,
                        topic=topic,
                        workspace_node=workspace_node,
                        current_time=current_time,
                        stale_reclaim=True,
                    )

                if run.status == AgentRun.FAILED and redelivered:
                    latest_event_type = (
                        run.events.order_by("-id").values_list("event_type", flat=True).first()
                    )
                    if latest_event_type == "stale_reaped":
                        if run.attempts >= ARMADA_WORKER_MAX_CLAIM_ATTEMPTS:
                            self._record_claim_dropped(
                                run,
                                stream=stream,
                                topic=topic,
                                reason="stale_retry_limit",
                            )
                            return False
                        if self._shared_run_is_topic_blocked(
                            run,
                            user_profile,
                            stream=stream,
                            topic=topic,
                        ):
                            self._record_claim_dropped(
                                run,
                                stream=stream,
                                topic=topic,
                                reason="topic_busy",
                            )
                            return False
                        if not self._transition(
                            run,
                            AgentRun.QUEUED,
                            stream=stream,
                            topic=topic,
                            result_message_id=run.result_message_id,
                            error=None,
                            allow_requeue=True,
                        ):
                            return False
                        return self._claim_locked_run(
                            run,
                            stream=stream,
                            topic=topic,
                            workspace_node=workspace_node,
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

    def fail_unreconstructable_side_run(self, run_id: int, *, error: str) -> None:
        """Settle a queued /btw whose ordinary trigger Message was deleted."""
        settled_run: AgentRun | None = None
        with transaction.atomic(savepoint=False):
            run = (
                AgentRun.objects.select_for_update()
                .filter(
                    id=run_id,
                    session_kind=AgentRun.SIDE,
                    status=AgentRun.QUEUED,
                )
                .first()
            )
            if run is None:
                return
            if not self._transition(
                run,
                AgentRun.FAILED,
                stream=run.stream_name,
                topic=run.topic_name,
                result_message_id=run.result_message_id,
                error=error,
                allow_requeue=False,
            ):
                return
            self._record_event(
                run,
                "failed",
                summary=error,
                payload={"reason": "missing_trigger_message"},
                require_running_lease=False,
            )
            settled_run = run
        # Message edits use their normal action/event lifecycle and must not
        # run while the durable run row is locked. The result is an ordinary
        # threaded Message, so settle that exact entity rather than inventing
        # pump-specific presentation state.
        assert settled_run is not None
        settle_armada_run_result_message(
            settled_run,
            content="This side conversation could not start because its request message was deleted.",
        )

    def record(
        self,
        bot_handler: Any,
        message_id: int,
        status: ArmadaWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if message_id <= 0:
            return
        stream, topic = armada_topic_scope(stream, topic)

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        workspace_node = self._workspace_node_for_topic(
            user_profile,
            stream=stream,
            topic=topic,
        )

        with transaction.atomic(savepoint=False):
            run, created = AgentRun.objects.select_for_update().get_or_create(
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
                    **self._runtime_labels(workspace_node),
                },
            )
            self._require_worker_attempt(
                bot_profile_id=user_profile.id,
                message_id=message_id,
                run=run,
            )
            if created:
                self._record_event(
                    run,
                    status,
                    summary=f"Recorded run status: {status}.",
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
                summary=f"Recorded run status: {status}.",
                payload={
                    "stream": stream,
                    "topic": topic,
                    "result_message_id": result_message_id,
                    "error": error,
                },
                require_running_lease=False,
            )

    def _claim_locked_run(
        self,
        run: AgentRun,
        *,
        stream: str,
        topic: str,
        workspace_node: Workspace | None,
        current_time: datetime,
        stale_reclaim: bool,
    ) -> bool:
        if not self._transition(
            run,
            AgentRun.RUNNING,
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
        # The topic's model or effort may have changed while the run waited
        # in the queue; it runs with today's.
        labels = self._runtime_labels(workspace_node)
        for field_name, value in labels.items():
            setattr(run, field_name, value)
        run.save(update_fields=["attempts", "heartbeat_at", "date_updated", *labels])
        self._remember_claimed_attempt(run)
        self._record_event(
            run,
            "claimed",
            summary="Claimed the run.",
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
        run: AgentRun,
        *,
        stream: str,
        topic: str,
        reason: str,
    ) -> None:
        self._record_event(
            run,
            "claim_dropped",
            summary="Dropped a duplicate delivery.",
            payload={
                "stream": stream,
                "topic": topic,
                "status": run.status,
                "attempts": run.attempts,
                "reason": reason,
            },
            # A claim can be dropped because this same fenced attempt just
            # settled the row (for example, after exhausting stale retries).
            # Preserve attempt fencing, but do not require a terminal row to
            # still be running merely to record why the delivery was dropped.
            require_running_lease=False,
        )

    def _run_is_stale(self, run: AgentRun, *, current_time: datetime) -> bool:
        return armada_worker_run_is_stale(run, current_time=current_time)

    def _heartbeat_due(self, run: AgentRun, *, current_time: datetime) -> bool:
        if run.status != AgentRun.RUNNING:
            return False
        last_heartbeat = run.heartbeat_at or run.date_updated
        return last_heartbeat <= current_time - timedelta(
            seconds=ARMADA_WORKER_HEARTBEAT_INTERVAL_SECONDS
        )

    def _apply_heartbeat_if_due(
        self,
        run: AgentRun,
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
                AgentRun.objects.select_for_update()
                .filter(bot_profile=user_profile, trigger_message_id=message_id)
                .first()
            )
            self._require_worker_attempt(
                bot_profile_id=user_profile.id,
                message_id=message_id,
                run=run,
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
        run = AgentRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
        ).first()
        self._require_worker_attempt(
            bot_profile_id=user_profile.id,
            message_id=message_id,
            run=run,
        )
        if run is None:
            return
        self._record_event(run, event_type, summary=summary, payload=payload or {})

    def cancel_requested(self, bot_handler: Any, message_id: int) -> bool:
        if message_id <= 0:
            return False

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        run = AgentRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
        ).first()
        if run is None:
            self._require_worker_attempt(
                bot_profile_id=user_profile.id,
                message_id=message_id,
                run=run,
            )
            return False
        claimed_attempt = self._claimed_attempt(user_profile.id, message_id)
        if claimed_attempt is not None and run.attempts != claimed_attempt:
            raise ArmadaWorkerLeaseLostError(
                f"Run for message {message_id} lost attempt {claimed_attempt}."
            )
        if run.status == AgentRun.CANCELLED:
            return True
        self._require_worker_attempt(
            bot_profile_id=user_profile.id,
            message_id=message_id,
            run=run,
        )
        return run.cancel_requested_at is not None

    def topic_response_policy(
        self,
        bot_handler: Any,
        *,
        stream: str,
        topic: str,
    ) -> str:
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        stream, topic = armada_topic_scope(stream, topic)
        return armada_workspace_response_policy(
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
        stream, topic = armada_topic_scope(stream, topic)
        set_armada_workspace_response_policy(
            user_profile,
            stream=stream,
            topic=topic,
            response_policy=response_policy,
        )

    def answer_stream_sender(
        self, bot_handler: Any, message_id: int
    ) -> ArmadaAnswerStreamSender | None:
        """What sends this run's streamed words to the people who see the
        run; None for a run with no row. One query, once per run."""
        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        run = (
            AgentRun.objects.select_related("realm")
            .filter(bot_profile=user_profile, trigger_message_id=message_id)
            .first()
        )
        if run is None:
            return None
        from zerver.actions.armada import send_armada_stream_event

        def send(*, message_id: int, item: int, offset: int, text: str) -> None:
            send_armada_stream_event(
                run, message_id=message_id, item=item, offset=offset, text=text
            )

        return send

    def steering_events_after(
        self,
        bot_handler: Any,
        message_id: int,
        *,
        after_event_id: int,
    ) -> list[ArmadaPendingSteeringEvent]:
        if message_id <= 0:
            return []

        user_profile = bot_handler.user_profile
        assert isinstance(user_profile, UserProfile)
        run = AgentRun.objects.filter(
            bot_profile=user_profile,
            trigger_message_id=message_id,
        ).first()
        self._require_worker_attempt(
            bot_profile_id=user_profile.id,
            message_id=message_id,
            run=run,
        )
        if run is None:
            return []

        events = list(
            AgentRunEvent.objects.filter(
                run=run,
                event_type="steering",
                id__gt=after_event_id,
            ).order_by("id")
        )
        if not events:
            return []
        steering_message_ids = {
            event.payload["message_id"]
            for event in events
            if isinstance(event.payload, dict) and isinstance(event.payload.get("message_id"), int)
        }
        steering_messages = {
            steering_message.id: steering_message
            for steering_message in Message.objects.select_related("sender").filter(
                id__in=steering_message_ids,
                realm_id=user_profile.realm_id,
            )
        }
        pending_events: list[ArmadaPendingSteeringEvent] = []
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
            pending_event: ArmadaPendingSteeringEvent = {
                "event_id": event.id,
                "message_id": message_id_value,
                "instruction": instruction,
            }
            steering_message = steering_messages.get(message_id_value)
            if steering_message is not None:
                pending_event["sender_full_name"] = steering_message.sender.full_name
            if steering_message is not None and "#**" in instruction:
                referenced_threads = load_armada_referenced_threads(
                    steering_message.sender if steering_message.sender.is_active else None,
                    content=steering_message.content,
                    current_stream=run.stream_name,
                    current_topic=run.topic_name,
                )
                if referenced_threads:
                    pending_event["referenced_threads"] = referenced_threads
            pending_events.append(pending_event)
        return pending_events

    def _update_run(
        self,
        run: AgentRun,
        status: ArmadaWorkerRunStatus,
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

    def cancel_locked_run(self, run: AgentRun, *, summary: str) -> bool:
        """Cancel a row already locked by the caller through the shared state machine."""
        if not self._transition(
            run,
            AgentRun.CANCELLED,
            stream=run.stream_name,
            topic=run.topic_name,
            result_message_id=run.result_message_id,
            error=None,
            allow_requeue=False,
        ):
            return False
        run.cancel_requested_at = run.date_updated
        run.current_activity = "Cancelled from the run panel."
        run.summary = summary
        run.save(update_fields=["cancel_requested_at", "current_activity", "summary"])
        return True

    def _transition(
        self,
        run: AgentRun,
        status: ArmadaWorkerRunStatus,
        *,
        stream: str,
        topic: str,
        result_message_id: int | None,
        error: str | None,
        allow_requeue: bool,
    ) -> bool:
        old_status = run.status
        same_status = old_status == status
        legal_statuses = ARMADA_RUN_LEGAL_TRANSITIONS.get(old_status, frozenset())
        is_explicit_requeue = (
            old_status == AgentRun.FAILED and status == AgentRun.QUEUED and allow_requeue
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
        if old_status == AgentRun.FAILED and status == AgentRun.QUEUED and not is_explicit_requeue:
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
        if status in (AgentRun.DONE, AgentRun.FAILED, AgentRun.CANCELLED):
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
        if (
            not same_status
            and status in (AgentRun.DONE, AgentRun.FAILED, AgentRun.CANCELLED)
            and run.workspace_node_id is not None
        ):
            workspace_node_id = run.workspace_node_id
            transaction.on_commit(
                lambda: refresh_armada_workspace_after_terminal_run(workspace_node_id)
            )
        if (
            not same_status
            and status in (AgentRun.DONE, AgentRun.FAILED)
            and run.requesting_user_id is not None
        ):
            # A finished run counts toward its requester's partner, as a
            # send counts toward the sender's.
            from zerver.lib.partner import fold_partner_after_run

            requesting_user_id = run.requesting_user_id
            transaction.on_commit(lambda: fold_partner_after_run(requesting_user_id))
        return True

    def _record_transition_rejected(
        self,
        run: AgentRun,
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
            summary=f"Rejected run status transition: {old_status} -> {new_status}.",
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
        run: AgentRun,
        event_type: str,
        *,
        summary: str,
        payload: dict[str, Any],
        require_running_lease: bool = True,
    ) -> None:
        # Keep the durable event and every run field represented by its push
        # payload in one commit. The runs endpoint can then expose the event id
        # as an authoritative snapshot watermark.
        with transaction.atomic(savepoint=False):
            claimed_attempt = self._claimed_attempt(
                run.bot_profile_id,
                run.trigger_message_id,
            )
            if claimed_attempt is not None:
                fenced_run = (
                    AgentRun.objects.select_for_update()
                    .filter(id=run.id, attempts=claimed_attempt)
                    .first()
                )
                self._require_worker_attempt(
                    bot_profile_id=run.bot_profile_id,
                    message_id=run.trigger_message_id,
                    run=fenced_run,
                    require_running=require_running_lease,
                )
                assert fenced_run is not None
                run = fenced_run
            run_event = record_agent_run_event(run, event_type, summary=summary, payload=payload)
            if event_type == "tool_results" and run.workspace_node_id is not None:
                # The tools may have changed the worktree.
                from zerver.lib.armada_workspace_git import request_workspace_git_refresh

                request_workspace_git_refresh(run.workspace_node_id)
            current_time = timezone_now()
            if self._heartbeat_due(run, current_time=current_time):
                run.heartbeat_at = current_time
                run.date_updated = current_time
                run.save(update_fields=["heartbeat_at", "date_updated"])
            from zerver.actions.armada import send_armada_run_event

            # This helper builds the payload from the current row now, then
            # uses send_event_on_commit for delivery after durability.
            send_armada_run_event(run, run_event)
            if event_type in {"steering_applied", "tool_results", "transcript"}:
                transaction.on_commit(
                    lambda: maybe_enqueue_armada_run_presentation(run.id, run_event.id)
                )

    def _runtime_label(self) -> str:
        return armada_setting_text(settings.ARMADA_WORKER_AGENT_RUNTIME, "")

    def _runtime_labels(self, workspace_node: Workspace | None) -> dict[str, str]:
        """What a run records about where and how it runs, read from its
        topic's row: no git, so it is cheap under any lock."""
        workdir = (
            armada_worker_workdir_for_workspace(workspace_node)
            if workspace_node is not None
            else armada_default_worker_workdir()
        )
        return {
            "runtime_label": self._runtime_label(),
            "model": armada_worker_model_for_workspace(workspace_node),
            "sandbox": armada_worker_sandbox_for_workdir(workdir),
            "approval_policy": armada_setting_text(settings.ARMADA_WORKER_APPROVAL_POLICY, ""),
            "workdir": workdir,
        }

    def _workspace_node_for_topic(
        self,
        user_profile: UserProfile,
        *,
        stream: str,
        topic: str,
    ) -> Workspace | None:
        if not stream:
            return None
        return armada_workspace_node_record(user_profile, stream=stream, topic=topic)


class ArmadaQueueWorkerDispatcher:
    def dispatch(
        self,
        _armada_handler: ArmadaEmbeddedBotHandler,
        bot_handler: Any,
        message: dict[str, Any],
        *,
        content: str,
        active_session: ArmadaActiveSessionSummary | None,
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
            raise AssertionError("Queue dispatch requires a bot user id")
        if session_kind == AgentRun.SHARED and not stream:
            raise AssertionError("Shared queue dispatch requires a channel topic")
        worker_message = dict(message)
        worker_message["content"] = content
        # Monitor turns ride their own queue/consumer: a provisioning
        # wall block must never queue ahead of a chat run.
        context_stream = stream or source_stream
        context_topic = topic or source_topic
        queue_name = (
            ARMADA_MONITOR_WORKER_QUEUE_NAME
            if session_kind == AgentRun.MONITOR
            else armada_worker_queue_name(
                user_profile_id,
                stream=context_stream,
                topic=context_topic,
            )
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


def load_armada_active_session_from_zulip(
    message: dict[str, Any],
    bot_handler: Any,
) -> ArmadaActiveSessionSummary:
    user_profile = bot_handler.user_profile
    assert isinstance(user_profile, UserProfile)
    armada_session = get_armada_session_payload(
        stream=armada_message_stream(message),
        topic=armada_message_topic(message),
    )
    try:
        stream, _sub = access_stream_by_name(user_profile, armada_session["stream"])
    except JsonableError:
        return build_armada_active_session_summary(
            armada_session=armada_session,
            source="server",
            stream_exists=False,
            topic_message_count=0,
            latest_message_id=None,
        )

    assert stream.recipient_id is not None
    message_query = messages_for_topic(
        user_profile.realm_id,
        stream.recipient_id,
        armada_session["topic"],
    )
    trigger_message_id = armada_message_id(message)
    if trigger_message_id > 0:
        message_query = message_query.filter(id__lte=trigger_message_id)
    message_count = message_query.count()
    message_rows = list(
        message_query.order_by("-id").values_list(
            "id",
            "content",
            "sender__is_bot",
            "sender__full_name",
        )[:ARMADA_WORKER_TOPIC_HISTORY_LIMIT]
    )
    if trigger_message_id not in {row[0] for row in message_rows}:
        trigger_sender_name = str(
            message.get("sender_full_name") or message.get("sender_email") or "User"
        )
        message_rows.append(
            (trigger_message_id, str(message.get("content", "")), False, trigger_sender_name)
        )
        message_count = max(message_count, len(message_rows))
    latest_codex_run = latest_armada_codex_run_for_topic(
        user_profile,
        stream=armada_session["stream"],
        topic=armada_session["topic"],
        before_message_id=trigger_message_id,
        source_message_id=(
            message["thread_root_message_id"]
            if isinstance(message.get("thread_root_message_id"), int)
            and not isinstance(message.get("thread_root_message_id"), bool)
            else None
        ),
    )
    latest_codex_thread_id = (
        latest_codex_run.codex_thread_id.strip() if latest_codex_run is not None else None
    )
    if not latest_codex_thread_id:
        latest_codex_run = None
        latest_codex_thread_id = None
    # An exact-parent thread reply becomes a native /btw fork; an unrelated
    # child thread starts its own shared Codex thread. Replaying either child
    # conversation through a later topic-level turn would cross those context
    # boundaries. Keep children in routing/topic counts, but out of the parent
    # prompt delta.
    prompt_message_query = message_query.filter(thread_root__isnull=True)
    if trigger_message_id > 0:
        # The current request is already the Codex turn input. Repeating it in
        # the transcript wastes context and teaches the engine a duplicated
        # conversation shape.
        prompt_message_query = prompt_message_query.filter(id__lt=trigger_message_id)
    if latest_codex_run is not None:
        # Codex owns the durable thread, including every prior request and its
        # own replies. Armada transports only Zulip messages that arrived since
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
    requesting_user_id = message.get("sender_id")
    if not isinstance(requesting_user_id, int) or isinstance(requesting_user_id, bool):
        requesting_user_id = (
            Message.objects.filter(id=trigger_message_id, realm_id=user_profile.realm_id)
            .values_list("sender_id", flat=True)
            .first()
        )
    requesting_user = (
        UserProfile.objects.filter(
            id=requesting_user_id,
            realm_id=user_profile.realm_id,
            is_active=True,
        ).first()
        if isinstance(requesting_user_id, int)
        else None
    )
    referenced_threads = load_armada_referenced_threads(
        requesting_user,
        content=str(message.get("content", "")),
        current_stream=armada_session["stream"],
        current_topic=armada_session["topic"],
    )
    workspace_context = build_armada_workspace_context(
        user_profile,
        stream=armada_session["stream"],
        topic=armada_session["topic"],
    )
    active_session = build_armada_active_session_summary_from_messages(
        armada_session=armada_session,
        source="server",
        stream_exists=True,
        messages=message_rows,
        prompt_messages=prompt_message_rows,
        referenced_threads=referenced_threads,
        topic_message_count=message_count,
        latest_codex_thread_id=latest_codex_thread_id,
        latest_codex_trigger_message_id=(
            latest_codex_run.trigger_message_id if latest_codex_run is not None else None
        ),
        current_trigger_message_id=trigger_message_id if trigger_message_id > 0 else None,
        workspace_context=workspace_context,
        is_paused=armada_workspace_is_paused(
            user_profile,
            stream=armada_session["stream"],
            topic=armada_session["topic"],
        ),
        response_policy=armada_workspace_response_policy(
            user_profile,
            stream=armada_session["stream"],
            topic=armada_session["topic"],
        ),
    )
    if requesting_user is not None:
        active_session["requesting_user_id"] = requesting_user.id
    return active_session
