import json
import subprocess
import time
from pathlib import Path
from shutil import which
from types import SimpleNamespace
from typing import Annotated, Any

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max, Q, Window
from django.db.models.functions import RowNumber
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from pydantic import Json

from zerver.actions.armada import send_pear_run_event
from zerver.actions.message_send import internal_send_private_message, internal_send_stream_message
from zerver.actions.streams import bulk_add_subscriptions
from zerver.decorator import require_realm_admin
from zerver.lib.armada import (
    PEAR_AGENT_NAME,
    PEAR_BOT_SHORT_NAME,
    PEAR_EMBEDDED_BOT_SERVICE,
    PEAR_FIRST_RUN_STARTER_MESSAGE,
    PEAR_FIRST_RUN_STREAM,
    PEAR_FIRST_RUN_TOPIC,
    PEAR_REASONING_EFFORTS,
    PEAR_SELECTED_BOT_CONFIG_KEY,
    PEAR_SELECTED_BOT_CONFIG_VALUE,
    PearConfigPayload,
    PearEmbeddedBotHandler,
    PearTokenUsage,
    build_pear_active_session_summary,
    build_pear_active_session_summary_from_messages,
    build_pear_config_payload,
    get_pear_live_run_state,
    get_pear_session_payload,
    is_pear_task_request_message,
    normalize_pear_response_policy,
    normalize_pear_visible_markdown,
    pear_normalize_token_usage,
    pear_token_usage_summary,
    pear_topic_scope,
    trim_pear_text,
)
from zerver.lib.armada_agent_threads import (
    load_pear_agent_thread_events,
    serialize_pear_agent_threads,
)
from zerver.lib.armada_gate import invalidate_pear_selected_bot_cache
from zerver.lib.armada_monitor import (
    PEAR_MONITOR_MAX_CADENCE_SECONDS,
    PEAR_MONITOR_MAX_PER_REALM,
    PEAR_MONITOR_MAX_PER_TOPIC,
    PEAR_MONITOR_MIN_CADENCE_SECONDS,
    PearMonitorSpecError,
    build_pear_monitor_contract,
    pear_create_monitor_from_spec,
    serialize_pear_monitor,
)
from zerver.lib.armada_presentation import pear_presentation_current_step
from zerver.lib.armada_worker import (
    PearDatabaseWorkerRunStore,
    PearQueueWorkerDispatcher,
    ensure_pear_workspace_node,
    mark_pear_run_attended,
    maybe_enqueue_pear_run_presentation,
    pear_default_worker_reasoning_effort,
    pear_git_status_delta,
    pear_logical_repository_label,
    pear_logical_worktree_label,
    pear_pinned_repository_value,
    pear_repo_artifact_value_is_durable,
    pear_topic_isolation_user_exists,
    pear_topic_isolation_username_for_realm,
    pear_topic_os_user_isolation_enabled,
    pear_user_is_trusted,
    pear_worker_model_for_topic,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_sandbox_for_topic,
    pear_worker_workdir_for_topic,
    pear_workspace_is_paused,
    pear_workspace_node_default_branch,
    pear_workspace_response_policy,
    set_pear_workspace_paused,
)
from zerver.lib.armada_workspace_map import pear_workspace_topics_for_stream
from zerver.lib.bot_config import get_bot_configs, set_bot_config
from zerver.lib.exceptions import JsonableError
from zerver.lib.markdown import render_message_markdown
from zerver.lib.response import json_success
from zerver.lib.streams import access_stream_by_name, create_stream_if_needed
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.lib.topic import messages_for_topic
from zerver.lib.typed_endpoint import (
    INTENTIONALLY_UNDOCUMENTED,
    ApiParamConfig,
    typed_endpoint,
    typed_endpoint_without_parameters,
)
from zerver.lib.url_encoding import topic_narrow_url
from zerver.models import Message
from zerver.models.bots import AgentMonitor, AgentRun, AgentRunEvent, Service, Workspace
from zerver.models.users import UserProfile
from zerver.views.users import add_bot_backend

PEAR_ACTIVE_SESSION_MESSAGE_LIMIT = 50
PEAR_RUN_LIST_LIMIT = 20
PEAR_RUN_IDS_FILTER_LIMIT = 20
PEAR_RUN_EVENT_LIMIT = 40
PEAR_RUN_TRACE_LIMIT = 50
PEAR_RUN_TRACE_MAX_LIMIT = 100
PEAR_MODEL_SLUG_LIMIT = 100
PEAR_MODEL_CATALOG_CACHE_SECONDS = 300

pear_model_catalog_cache: tuple[float, list[dict[str, object]]] | None = None


def pear_worker_runtime_dependency_status(worker_agent_runtime: str) -> tuple[bool, str]:
    codex_bin = settings.PEAR_WORKER_CODEX_BIN
    codex_path = which(codex_bin)
    if worker_agent_runtime == "codex-app-server":
        if codex_path is None:
            return False, f"Missing {codex_bin} executable."
        return True, f"codex app-server via {codex_path}."
    return (
        False,
        "Set PEAR_WORKER_AGENT_RUNTIME to codex-app-server.",
    )


def pear_codex_login_command() -> str:
    return f"{settings.PEAR_WORKER_CODEX_BIN} login"


def pear_codex_login_available(codex_bin: str) -> bool:
    try:
        completed = subprocess.run(
            [codex_bin, "login", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    output = f"{completed.stdout}\n{completed.stderr}"
    return completed.returncode == 0 and "Logged in" in output


def pear_worker_runtime_auth_status(
    worker_agent_runtime: str,
) -> tuple[bool, str, str | None, str | None, str | None, str | None]:
    codex_bin = settings.PEAR_WORKER_CODEX_BIN
    login_command = pear_codex_login_command()
    codex_logged_in = pear_codex_login_available(codex_bin)
    codex_login_instructions = "Run Codex CLI login inside the worker environment used by Pear."

    if worker_agent_runtime == "codex-app-server":
        if codex_logged_in:
            return (
                True,
                "Codex CLI is logged in.",
                "codex-cli",
                login_command,
                "worker-environment",
                codex_login_instructions,
            )
        return (
            False,
            f"Run `{login_command}` inside the worker environment.",
            "codex-cli",
            login_command,
            "worker-environment",
            codex_login_instructions,
        )

    return (
        False,
        "Select a supported worker runtime before logging in.",
        None,
        None,
        None,
        None,
    )


def get_pear_bot_query(user_profile: UserProfile) -> Any:
    return (
        Service.objects.select_related("user_profile")
        .filter(
            name=PEAR_EMBEDDED_BOT_SERVICE,
            user_profile__realm=user_profile.realm,
            user_profile__is_active=True,
            user_profile__is_bot=True,
            user_profile__bot_type=UserProfile.EMBEDDED_BOT,
        )
        .order_by("user_profile_id")
    )


def get_selected_pear_bot(user_profile: UserProfile) -> Service | None:
    pear_bots = list(get_pear_bot_query(user_profile))
    if not pear_bots:
        return None
    bot_configs = get_bot_configs([bot.user_profile_id for bot in pear_bots])
    return next(
        (
            bot
            for bot in pear_bots
            if bot_configs.get(bot.user_profile_id, {}).get(PEAR_SELECTED_BOT_CONFIG_KEY)
            == PEAR_SELECTED_BOT_CONFIG_VALUE
        ),
        pear_bots[0],
    )


def clean_pear_model_slug(model: str) -> str:
    model = model.strip()
    if not model:
        raise JsonableError(_("Pear model cannot be empty."))
    if len(model) > PEAR_MODEL_SLUG_LIMIT or any(character.isspace() for character in model):
        raise JsonableError(_("Pear model must be a short model slug without whitespace."))
    return model


def clean_pear_reasoning_effort(reasoning_effort: str) -> str:
    reasoning_effort = reasoning_effort.strip()
    if reasoning_effort not in PEAR_REASONING_EFFORTS:
        raise JsonableError(_("Pear reasoning effort must be low, medium, high, or xhigh."))
    return reasoning_effort


def pear_reasoning_effort_label(reasoning_effort: str) -> str:
    labels = {
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "xhigh": "XHigh",
        "max": "Max",
        "ultra": "Ultra",
    }
    return labels.get(reasoning_effort, reasoning_effort)


def build_pear_reasoning_effort_payload(
    levels: object | None = None,
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    if isinstance(levels, list):
        for level in levels:
            if isinstance(level, dict):
                effort = str(level.get("effort") or "").strip()
                description = str(level.get("description") or "").strip()
            elif isinstance(level, str):
                effort = level.strip()
                description = ""
            else:
                continue
            if effort not in PEAR_REASONING_EFFORTS:
                continue
            payload.append(
                {
                    "effort": effort,
                    "label": pear_reasoning_effort_label(effort),
                    "description": description,
                }
            )
    if payload:
        return payload
    return [
        {
            "effort": effort,
            "label": pear_reasoning_effort_label(effort),
            "description": "",
        }
        for effort in PEAR_REASONING_EFFORTS
    ]


def configured_pear_model_choices() -> list[str]:
    return list(
        dict.fromkeys(
            model.strip()
            for model in [
                settings.PEAR_WORKER_MODEL,
                *settings.PEAR_WORKER_MODEL_CHOICES,
            ]
            if isinstance(model, str) and model.strip()
        )
    )


def pear_model_catalog_entry(model: dict[str, object]) -> dict[str, object] | None:
    if model.get("visibility") != "list":
        return None
    slug = str(model.get("slug") or "").strip()
    if not slug:
        return None
    display_name = str(model.get("display_name") or slug).strip()
    return {
        "slug": slug,
        "display_name": display_name or slug,
        "description": str(model.get("description") or "").strip(),
        "priority": model.get("priority") if isinstance(model.get("priority"), int) else 1000,
        "default_reasoning_level": str(
            model.get("default_reasoning_level") or pear_default_worker_reasoning_effort()
        ).strip(),
        "supported_reasoning_levels": build_pear_reasoning_effort_payload(
            model.get("supported_reasoning_levels")
        ),
        "supported_in_api": bool(model.get("supported_in_api", False)),
        "service_tiers": model.get("service_tiers")
        if isinstance(model.get("service_tiers"), list)
        else [],
        "additional_speed_tiers": model.get("additional_speed_tiers")
        if isinstance(model.get("additional_speed_tiers"), list)
        else [],
    }


def extract_pear_codex_model_catalog(data: object) -> list[dict[str, object]]:
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    entries = [
        entry
        for model in data["models"]
        if isinstance(model, dict)
        for entry in [pear_model_catalog_entry(model)]
        if entry is not None
    ]

    def priority_key(entry: dict[str, object]) -> tuple[int, str]:
        priority = entry.get("priority")
        return priority if isinstance(priority, int) else 1000, str(entry["slug"])

    return sorted(entries, key=priority_key)


def fallback_pear_model_catalog(model_choices: list[str]) -> list[dict[str, object]]:
    return [
        {
            "slug": model,
            "display_name": model,
            "description": "",
            "priority": index,
            "default_reasoning_level": pear_default_worker_reasoning_effort(),
            "supported_reasoning_levels": build_pear_reasoning_effort_payload(),
            "supported_in_api": False,
            "service_tiers": [],
            "additional_speed_tiers": [],
        }
        for index, model in enumerate(model_choices)
    ]


def build_pear_codex_model_catalog(model_choices: list[str]) -> list[dict[str, object]]:
    global pear_model_catalog_cache
    now = time.monotonic()
    if (
        pear_model_catalog_cache is not None
        and now - pear_model_catalog_cache[0] < PEAR_MODEL_CATALOG_CACHE_SECONDS
    ):
        catalog = [dict(entry) for entry in pear_model_catalog_cache[1]]
    else:
        try:
            completed = subprocess.run(
                [settings.PEAR_WORKER_CODEX_BIN, "debug", "models"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            catalog = []
        else:
            catalog = []
            if completed.returncode == 0:
                try:
                    catalog = extract_pear_codex_model_catalog(json.loads(completed.stdout))
                except json.JSONDecodeError:
                    catalog = []
        if not catalog:
            catalog = fallback_pear_model_catalog([])
        pear_model_catalog_cache = (now, [dict(entry) for entry in catalog])

    slugs = {str(entry["slug"]) for entry in catalog}
    for entry in fallback_pear_model_catalog(model_choices):
        if str(entry["slug"]) not in slugs:
            catalog.append(entry)
    return catalog


def build_pear_git_context(
    workdir: str,
    *,
    topic: str = "",
    isolation_username: str | None = None,
) -> dict[str, object]:
    return pear_git_status_delta(
        workdir,
        topic=topic,
        isolation_username=isolation_username,
    )


def pear_workspace_isolation_username(
    workspace_node: Workspace | None,
    *,
    stream_name: str,
    topic_name: str,
    workdir: str,
) -> str | None:
    if (
        workspace_node is not None
        and pear_topic_os_user_isolation_enabled()
        and pear_repo_artifact_value_is_durable(workdir)
    ):
        username = pear_topic_isolation_username_for_realm(
            workspace_node.realm_id,
            stream=stream_name,
            topic=topic_name,
        )
        if not pear_topic_isolation_user_exists(username):
            return None
        return username
    return None


def pear_workspace_for_request(
    user_profile: UserProfile,
    *,
    stream_name: str,
    topic_name: str,
) -> Workspace | None:
    stream_name, topic_name = pear_topic_scope(stream_name, topic_name)
    return (
        Workspace.objects.select_related("project")
        .filter(
            realm=user_profile.realm,
            stream_name__iexact=stream_name,
            topic_name__iexact=topic_name,
        )
        .first()
    )


def serialize_pear_workspace_context(
    workspace_node: Workspace | None,
    *,
    stream_name: str,
    topic_name: str,
    include_git: bool = True,
) -> dict[str, object]:
    runtime_config = build_pear_runtime_config_payload()
    default_model = str(runtime_config.get("model") or "")
    default_reasoning_effort = str(runtime_config.get("reasoning_effort") or "medium")
    selected_model = (
        workspace_node.model.strip()
        if workspace_node is not None and workspace_node.model.strip()
        else default_model
    )
    selected_reasoning_effort = (
        workspace_node.reasoning_effort.strip()
        if workspace_node is not None and workspace_node.reasoning_effort.strip()
        else default_reasoning_effort
    )
    selected_response_policy = normalize_pear_response_policy(
        workspace_node.response_policy if workspace_node is not None else None
    )
    workdir = (
        workspace_node.workdir.strip()
        if workspace_node is not None and workspace_node.workdir.strip()
        else ""
    )
    workspace_repo = (
        workspace_node.project.repository_url.strip()
        if workspace_node is not None and workspace_node.project_id is not None
        else ""
    )
    github_repo = next(
        (
            repo_value
            for repo_value in (workspace_repo,)
            if pear_repo_artifact_value_is_durable(repo_value)
        ),
        "",
    )
    pinned_repo = next(
        (
            repo_value
            for repo_value in (
                pear_pinned_repository_value(workspace_repo),
            )
            if repo_value
        ),
        "",
    )
    repo_label = pear_logical_repository_label(
        stream=stream_name,
        context_repo=workspace_repo,
        parent_repo="",
    )
    worktree_label = pear_logical_worktree_label(
        stream=stream_name,
        topic=topic_name,
        fallback=Path(workdir).name,
    )
    project = (
        workspace_node.project
        if workspace_node is not None and workspace_node.project_id is not None
        else None
    )
    expected_branch = (
        workspace_node.branch_name.strip()
        if workspace_node is not None and workspace_node.branch_name.strip()
        else pear_workspace_node_default_branch()
    )
    isolation_username = pear_workspace_isolation_username(
        workspace_node,
        stream_name=stream_name,
        topic_name=topic_name,
        workdir=workdir,
    )
    payload: dict[str, object] = {
        "stream": stream_name,
        "topic": topic_name,
        "model": selected_model,
        "default_model": default_model,
        "model_choices": runtime_config["model_choices"],
        "model_catalog": runtime_config["model_catalog"],
        "reasoning_effort": selected_reasoning_effort,
        "default_reasoning_effort": default_reasoning_effort,
        "reasoning_efforts": runtime_config["reasoning_efforts"],
        "response_policy": selected_response_policy,
        "workspace_id": workspace_node.id if workspace_node is not None else None,
        "workspace_uuid": str(workspace_node.uuid) if workspace_node is not None else "",
        "project_id": project.id if project is not None else None,
        "repository_url": project.repository_url if project is not None else pinned_repo,
        "base_ref": workspace_node.base_ref if workspace_node is not None else "",
        "base_commit": workspace_node.base_commit if workspace_node is not None else "",
        "branch": expected_branch,
        # The workspace branch is derived at creation; when it still equals
        # what creation would derive today, clients can hide the branch
        # chip entirely — only a surprising branch (legacy default, custom,
        # detached) is worth header space next to the topic name. Compare
        # against the same derivation used at creation so the empty topic
        # ("general chat"), whose default is the trunk branch, is also
        # recognized as default.
        "branch_is_topic_default": expected_branch
        == pear_workspace_node_default_branch(
            project=project,
            topic=topic_name,
        ),
        "head": workspace_node.head_commit if workspace_node is not None else "",
        "github_repo": github_repo,
        "pinned_repo": pinned_repo,
        "repo_label": repo_label,
        "repo_title": pinned_repo or workdir,
        "worktree_label": worktree_label,
        "workdir": workdir,
        "is_paused": bool(workspace_node is not None and workspace_node.is_paused),
        # Kept as an additive-wire compatibility field. Topic workspaces no
        # longer have hidden parent/config rows; every topic is an ordinary
        # branch of the stream's canonical repository.
        "parent_workspace_id": None,
    }
    if include_git:
        payload["git"] = build_pear_git_context(
            workdir,
            topic=expected_branch,
            isolation_username=isolation_username,
        )
    return payload


def build_pear_runtime_config_payload() -> dict[str, object]:
    configured_model_choices = configured_pear_model_choices()
    model_catalog = build_pear_codex_model_catalog(configured_model_choices)
    model_choices = [str(entry["slug"]) for entry in model_catalog]
    return {
        "agent_runtime": settings.PEAR_WORKER_AGENT_RUNTIME,
        "model": settings.PEAR_WORKER_MODEL,
        "model_choices": model_choices,
        "model_catalog": model_catalog,
        "reasoning_effort": pear_default_worker_reasoning_effort(),
        "reasoning_efforts": build_pear_reasoning_effort_payload(),
        "service_tier": settings.PEAR_WORKER_SERVICE_TIER,
        "fast_mode": settings.PEAR_WORKER_FAST_MODE,
        "sandbox": settings.PEAR_WORKER_SANDBOX,
        "approval_policy": settings.PEAR_WORKER_APPROVAL_POLICY,
        "workdir": settings.PEAR_WORKER_WORKDIR,
        "trusted_user_ids_configured": bool(settings.PEAR_WORKER_TRUSTED_USER_IDS),
        "max_active_runs_per_topic": settings.PEAR_WORKER_MAX_ACTIVE_RUNS_PER_TOPIC,
        "max_active_runs_per_realm": settings.PEAR_WORKER_MAX_ACTIVE_RUNS_PER_REALM,
    }


def build_pear_config_for_user(user_profile: UserProfile) -> PearConfigPayload:
    worker_agent_runtime = settings.PEAR_WORKER_AGENT_RUNTIME
    worker_runtime_available, worker_runtime_detail = pear_worker_runtime_dependency_status(
        worker_agent_runtime
    )
    (
        worker_runtime_auth_available,
        worker_runtime_auth_detail,
        worker_runtime_login_method,
        worker_runtime_login_command,
        worker_runtime_login_target,
        worker_runtime_login_instructions,
    ) = pear_worker_runtime_auth_status(worker_agent_runtime)
    pear_bot_query = get_pear_bot_query(user_profile)
    pear_bots = list(pear_bot_query)
    bot_count = len(pear_bots)
    bot_profile_ids = [bot.user_profile_id for bot in pear_bots]
    bot_configs = get_bot_configs(bot_profile_ids)
    selected_pear_bot = next(
        (
            bot
            for bot in pear_bots
            if bot_configs.get(bot.user_profile_id, {}).get(PEAR_SELECTED_BOT_CONFIG_KEY)
            == PEAR_SELECTED_BOT_CONFIG_VALUE
        ),
        pear_bots[0] if pear_bots else None,
    )
    return build_pear_config_payload(
        embedded_bots_enabled=settings.EMBEDDED_BOTS_ENABLED,
        bot_count=bot_count,
        bot_user_id=selected_pear_bot.user_profile_id if selected_pear_bot is not None else None,
        bot_full_name=selected_pear_bot.user_profile.full_name
        if selected_pear_bot is not None
        else None,
        bot_email=selected_pear_bot.user_profile.email if selected_pear_bot is not None else None,
        selected_bot_user_id=selected_pear_bot.user_profile_id
        if selected_pear_bot is not None
        else None,
        selected_bot_full_name=selected_pear_bot.user_profile.full_name
        if selected_pear_bot is not None
        else None,
        selected_bot_email=selected_pear_bot.user_profile.email
        if selected_pear_bot is not None
        else None,
        bot_identities=[
            {
                "bot_user_id": bot.user_profile_id,
                "bot_full_name": bot.user_profile.full_name,
                "bot_email": bot.user_profile.email,
                "selected": selected_pear_bot is not None
                and bot.user_profile_id == selected_pear_bot.user_profile_id,
            }
            for bot in pear_bots
        ],
        worker_agent_runtime=worker_agent_runtime,
        worker_runtime_available=worker_runtime_available,
        worker_runtime_detail=worker_runtime_detail,
        worker_runtime_auth_available=worker_runtime_auth_available,
        worker_runtime_auth_detail=worker_runtime_auth_detail,
        worker_runtime_login_method=worker_runtime_login_method,
        worker_runtime_login_command=worker_runtime_login_command,
        worker_runtime_login_target=worker_runtime_login_target,
        worker_runtime_login_instructions=worker_runtime_login_instructions,
        worker_service_tier=settings.PEAR_WORKER_SERVICE_TIER,
        worker_fast_mode=settings.PEAR_WORKER_FAST_MODE,
    )


def serialize_pear_worker_run_event(event: AgentRunEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "summary": event.summary,
        "payload": event.payload,
        "date_created": datetime_to_timestamp(event.date_created),
    }


def pear_usage_from_event_payload(payload: object) -> PearTokenUsage | None:
    if not isinstance(payload, dict):
        return None
    usage = pear_normalize_token_usage(payload.get("usage"))
    if usage is not None:
        return usage
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        usage = pear_normalize_token_usage(metadata.get("usage"))
        if usage is not None:
            return usage
    return pear_normalize_token_usage(payload)


def pear_worker_run_summary_events(
    runs: list[AgentRun],
) -> dict[int, list[AgentRunEvent]]:
    """Load bounded run-card state without issuing per-run queries.

    Run-list hydration must not issue per-run event queries. Detail
    serialization can still use the model managers when no batch was supplied.
    """
    events_by_run_id = {run.id: [] for run in runs}
    if not events_by_run_id:
        return events_by_run_id
    newest_usage_candidates = (
        AgentRunEvent.objects.filter(
            run_id__in=events_by_run_id,
            event_type__in=("usage", "transcript"),
        )
        .annotate(
            summary_rank=Window(
                expression=RowNumber(),
                partition_by=(F("run_id"),),
                order_by=(F("date_created").desc(), F("id").desc()),
            )
        )
        .filter(summary_rank__lte=20)
        .only("id", "run_id", "event_type", "payload", "date_created")
    )
    newest_display_candidates = (
        AgentRunEvent.objects.filter(run_id__in=events_by_run_id)
        .filter(Q(event_type="presentation") | Q(event_type="transcript", payload__kind="plan"))
        .annotate(
            summary_rank=Window(
                expression=RowNumber(),
                partition_by=(F("run_id"), F("event_type")),
                order_by=(F("date_created").desc(), F("id").desc()),
            )
        )
        .filter(summary_rank=1)
        .only("id", "run_id", "event_type", "payload", "date_created")
    )
    agent_candidates = load_pear_agent_thread_events(events_by_run_id)
    unique_events: dict[int, AgentRunEvent] = {}
    for event in [
        *newest_usage_candidates,
        *newest_display_candidates,
        *agent_candidates,
    ]:
        unique_events[event.id] = event
    for event in sorted(
        unique_events.values(),
        key=lambda candidate: (candidate.run_id, candidate.date_created, candidate.id),
        reverse=True,
    ):
        events_by_run_id[event.run_id].append(event)
    return events_by_run_id


def serialize_pear_worker_run_usage(
    run: AgentRun,
    summary_events: list[AgentRunEvent] | None = None,
) -> dict[str, object] | None:
    # The newest usage-bearing event wins, but the run's final events
    # are usually usage-less transcript markers ("Codex turn
    # completed."), so scan back a bounded window instead of trusting
    # the single latest event.
    usage: PearTokenUsage | None = None
    event = None
    candidates = (
        [event for event in summary_events if event.event_type in {"usage", "transcript"}][:20]
        if summary_events is not None
        else run.events.filter(Q(event_type="usage") | Q(event_type="transcript")).order_by(
            "-date_created", "-id"
        )[:20]
    )
    for candidate in candidates:
        usage = pear_usage_from_event_payload(candidate.payload)
        if usage is not None:
            event = candidate
            break
    if usage is None:
        return None
    duration_seconds = None
    if run.date_finished is not None:
        duration_seconds = round((run.date_finished - run.date_created).total_seconds(), 1)
    elif run.status in {AgentRun.QUEUED, AgentRun.RUNNING}:
        duration_seconds = round((timezone_now() - run.date_created).total_seconds(), 1)
    return {
        **usage,
        "summary": pear_token_usage_summary(usage),
        "duration_seconds": duration_seconds,
        "event_id": event.id if event is not None else None,
    }


def serialize_pear_worker_run_plan(
    run: AgentRun,
    summary_events: list[AgentRunEvent] | None = None,
) -> list[dict[str, str]] | None:
    """The run's latest plan_update, for the rail's pinned plan card.

    Plan events carry the whole checklist (full-state replacement, the
    codex update_plan contract), so the newest one wins; bounded scan
    like the usage lookup.
    """
    events = (
        [event for event in summary_events if event.event_type == "transcript"]
        if summary_events is not None
        else run.events.filter(event_type="transcript", payload__kind="plan").order_by(
            "-date_created", "-id"
        )[:1]
    )
    for event in events:
        payload = event.payload
        if not isinstance(payload, dict) or payload.get("kind") != "plan":
            continue
        metadata = payload.get("metadata")
        raw_steps = metadata.get("steps") if isinstance(metadata, dict) else None
        if not isinstance(raw_steps, list):
            continue
        steps = [
            {"step": str(step["step"]), "status": str(step.get("status", "pending"))}
            for step in raw_steps
            if isinstance(step, dict) and step.get("step")
        ]
        if steps:
            return steps
    return None


def serialize_pear_worker_run_tool_count(
    run: AgentRun,
    summary_events: list[AgentRunEvent] | None = None,
) -> int:
    # The worker records one tool_results event per turn segment, each
    # carrying that segment's tool_count; the run's total is their sum.
    if summary_events is not None:
        return run.tool_count
    tool_count = 0
    for payload in run.events.filter(event_type="tool_results").values_list("payload", flat=True):
        if isinstance(payload, dict) and isinstance(payload.get("tool_count"), int):
            tool_count += payload["tool_count"]
    return tool_count


def serialize_pear_worker_run_overview(
    run: AgentRun,
    summary_events: list[AgentRunEvent] | None = None,
    *,
    plan: list[dict[str, str]] | None = None,
) -> str | None:
    if plan is None:
        plan = serialize_pear_worker_run_plan(run, summary_events)
    if plan is None:
        return None
    current_step = pear_presentation_current_step(plan)
    events = (
        [event for event in summary_events if event.event_type == "presentation"]
        if summary_events is not None
        else run.events.filter(event_type="presentation").order_by("-date_created", "-id")[:1]
    )
    for event in events:
        payload = event.payload
        overview = payload.get("overview") if isinstance(payload, dict) else None
        overview_step = payload.get("current_step") if isinstance(payload, dict) else None
        if isinstance(overview, str) and overview.strip() and overview_step == current_step:
            return trim_pear_text(" ".join(overview.split()), limit=72)
    return None


def serialize_pear_worker_run(
    run: AgentRun,
    *,
    include_events: bool = False,
    summary_events: list[AgentRunEvent] | None = None,
) -> dict[str, object]:
    plan = serialize_pear_worker_run_plan(run, summary_events)
    live_state = get_pear_live_run_state(run.trigger_message_id)
    if (
        run.status not in {AgentRun.QUEUED, AgentRun.RUNNING}
        and live_state is not None
        and live_state["status"] in {AgentRun.QUEUED, AgentRun.RUNNING}
    ):
        live_state = None
    payload: dict[str, object] = {
        "id": run.id,
        "trigger_message_id": run.trigger_message_id,
        "result_message_id": run.result_message_id,
        "stream": run.stream_name,
        "topic": run.topic_name,
        "workspace_id": run.workspace_node_id,
        "session_kind": run.session_kind,
        "source_stream": run.source_stream_name,
        "source_topic": run.source_topic_name,
        "source_message_id": run.source_message_id,
        "status": run.status,
        "summary": run.summary,
        "current_activity": run.current_activity,
        "runtime_label": run.runtime_label,
        "model": run.model,
        "sandbox": run.sandbox,
        "approval_policy": run.approval_policy,
        "workdir": run.workdir,
        "codex_thread_id": run.codex_thread_id,
        "ambient_triggered": run.ambient_triggered,
        "error": run.error,
        "date_created": datetime_to_timestamp(run.date_created),
        "date_updated": datetime_to_timestamp(run.date_updated),
        "date_finished": datetime_to_timestamp(run.date_finished)
        if run.date_finished is not None
        else None,
        "bot_user_id": run.bot_profile_id,
        "requesting_user_id": run.requesting_user_id,
        "requesting_user_full_name": run.requesting_user.full_name
        if run.requesting_user is not None
        else None,
        "usage": serialize_pear_worker_run_usage(run, summary_events),
        "tool_count": serialize_pear_worker_run_tool_count(run, summary_events),
        "plan": plan,
        "overview": serialize_pear_worker_run_overview(run, summary_events, plan=plan),
        "live": live_state,
        "agents": serialize_pear_agent_threads(run, summary_events),
    }
    snapshot_sequence = getattr(run, "snapshot_sequence", None)
    if isinstance(snapshot_sequence, int):
        payload["snapshot_sequence"] = snapshot_sequence
    if include_events:
        payload["events"] = [
            serialize_pear_worker_run_event(event)
            for event in run.events.order_by("-date_created", "-id")[:PEAR_RUN_EVENT_LIMIT]
        ]
    return payload


def check_can_access_pear_worker_run(
    user_profile: UserProfile,
    run: AgentRun,
) -> None:
    if run.realm_id != user_profile.realm_id:
        raise JsonableError(_("Pear run not found."))
    if run.session_kind == AgentRun.SIDE:
        if run.requesting_user_id != user_profile.id:
            raise JsonableError(_("Pear run not found."))
        return
    stream_name = (
        run.workspace_node.stream_name
        if run.workspace_node_id is not None and run.workspace_node.stream_name
        else run.stream_name
    )
    access_stream_by_name(user_profile, stream_name)


def get_visible_pear_worker_run(
    user_profile: UserProfile,
    run_id: int,
) -> AgentRun:
    try:
        run = AgentRun.objects.select_related(
            "bot_profile",
            "requesting_user",
            "workspace_node",
        ).get(id=run_id, realm=user_profile.realm)
    except AgentRun.DoesNotExist as error:
        raise JsonableError(_("Pear run not found.")) from error
    check_can_access_pear_worker_run(user_profile, run)
    return run


def get_visible_pear_worker_runs_by_id(
    user_profile: UserProfile,
    run_ids: list[int],
) -> list[AgentRun]:
    normalized_run_ids = list(dict.fromkeys(run_ids))
    if len(normalized_run_ids) > PEAR_RUN_IDS_FILTER_LIMIT:
        raise JsonableError(_("Too many Pear runs requested."))
    if any(run_id <= 0 for run_id in normalized_run_ids):
        raise JsonableError(_("Pear run not found."))

    runs = list(
        AgentRun.objects.select_related(
            "bot_profile",
            "requesting_user",
            "workspace_node",
        ).filter(id__in=normalized_run_ids, realm=user_profile.realm)
    )
    runs_by_id = {run.id: run for run in runs}
    if len(runs_by_id) != len(normalized_run_ids):
        raise JsonableError(_("Pear run not found."))
    for run in runs_by_id.values():
        check_can_access_pear_worker_run(user_profile, run)
    return list(runs_by_id.values())


def set_selected_pear_bot(user_profile: UserProfile, selected_bot_user_id: int | None) -> bool:
    pear_bots = list(get_pear_bot_query(user_profile))
    if not pear_bots:
        return False

    pear_bot_by_user_id = {bot.user_profile_id: bot for bot in pear_bots}
    if selected_bot_user_id is None:
        bot_configs = get_bot_configs(list(pear_bot_by_user_id))
        selected_bot = next(
            (
                bot
                for bot in pear_bots
                if bot_configs.get(bot.user_profile_id, {}).get(PEAR_SELECTED_BOT_CONFIG_KEY)
                == PEAR_SELECTED_BOT_CONFIG_VALUE
            ),
            pear_bots[0],
        )
        selected_bot_user_id = selected_bot.user_profile_id
    elif selected_bot_user_id not in pear_bot_by_user_id:
        raise JsonableError(_("Invalid Pear bot selection."))

    changed = False
    bot_configs = get_bot_configs(list(pear_bot_by_user_id))
    for bot in pear_bots:
        selected_value = (
            PEAR_SELECTED_BOT_CONFIG_VALUE
            if bot.user_profile_id == selected_bot_user_id
            else "false"
        )
        previous_value = bot_configs.get(bot.user_profile_id, {}).get(PEAR_SELECTED_BOT_CONFIG_KEY)
        if previous_value != selected_value:
            changed = True
        set_bot_config(bot.user_profile, PEAR_SELECTED_BOT_CONFIG_KEY, selected_value)
    if changed:
        invalidate_pear_selected_bot_cache(user_profile.realm_id)
    return changed


def ensure_pear_configured_bot(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    selected_bot_user_id: int | None = None,
) -> tuple[Service, PearConfigPayload, bool, bool]:
    pear_config = build_pear_config_for_user(user_profile)
    created = False
    if not pear_config["configured"] and selected_bot_user_id is not None:
        raise JsonableError(_("Invalid Pear bot selection."))
    if not pear_config["configured"]:
        add_bot_backend(
            request,
            user_profile,
            bot_type=UserProfile.EMBEDDED_BOT,
            config_data={PEAR_SELECTED_BOT_CONFIG_KEY: PEAR_SELECTED_BOT_CONFIG_VALUE},
            full_name_raw=PEAR_AGENT_NAME,
            interface_type=Service.GENERIC,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
            short_name_raw=PEAR_BOT_SHORT_NAME,
        )
        created = True
        invalidate_pear_selected_bot_cache(user_profile.realm_id)

    selection_changed = set_selected_pear_bot(user_profile, selected_bot_user_id)
    pear_config = build_pear_config_for_user(user_profile)
    pear_bot = get_selected_pear_bot(user_profile)
    if pear_bot is None:
        raise JsonableError(_("Pear bot is not configured."))
    return pear_bot, pear_config, created, selection_changed


@typed_endpoint_without_parameters
def get_pear_session_backend(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(
        request,
        data={
            "pear_session": get_pear_session_payload(),
        },
    )


@typed_endpoint_without_parameters
def get_pear_config_backend(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(
        request,
        data={
            "pear_config": build_pear_config_for_user(user_profile),
        },
    )


@require_realm_admin
@typed_endpoint
def post_pear_config_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    selected_bot_user_id: Json[int] | None = None,
) -> HttpResponse:
    _pear_bot, pear_config, created, selection_changed = ensure_pear_configured_bot(
        request,
        user_profile,
        selected_bot_user_id=selected_bot_user_id,
    )

    return json_success(
        request,
        data={
            "created": created,
            "selection_changed": selection_changed,
            "pear_config": pear_config,
        },
    )


@require_realm_admin
@typed_endpoint_without_parameters
def post_pear_first_run_backend(
    request: HttpRequest,
    user_profile: UserProfile,
) -> HttpResponse:
    pear_bot, pear_config, bot_created, selection_changed = ensure_pear_configured_bot(
        request,
        user_profile,
    )
    stream, stream_created = create_stream_if_needed(
        user_profile.realm,
        PEAR_FIRST_RUN_STREAM,
        stream_description="Pear sandbox for first-run Codex sessions.",
        acting_user=user_profile,
    )
    bulk_add_subscriptions(
        user_profile.realm,
        [stream],
        [user_profile, pear_bot.user_profile],
        acting_user=user_profile,
    )

    assert stream.recipient_id is not None
    existing_message = (
        messages_for_topic(
            user_profile.realm_id,
            stream.recipient_id,
            PEAR_FIRST_RUN_TOPIC,
        )
        .filter(content=PEAR_FIRST_RUN_STARTER_MESSAGE)
        .order_by("id")
        .first()
    )
    if existing_message is None:
        starter_message_id = internal_send_stream_message(
            user_profile,
            stream,
            PEAR_FIRST_RUN_TOPIC,
            PEAR_FIRST_RUN_STARTER_MESSAGE,
            mark_as_read_for_acting_user=True,
            acting_user=user_profile,
        )
        if starter_message_id is None:
            raise JsonableError(_("Pear could not post the first-run message."))
        message_created = True
    else:
        starter_message_id = existing_message.id
        message_created = False

    narrow_url = topic_narrow_url(
        realm=user_profile.realm,
        stream=stream,
        topic_name=PEAR_FIRST_RUN_TOPIC,
    )
    return json_success(
        request,
        data={
            "narrow_url": narrow_url,
            "pear_first_run": {
                "stream": stream.name,
                "stream_id": stream.id,
                "topic": PEAR_FIRST_RUN_TOPIC,
                "message_id": starter_message_id,
                "narrow_url": narrow_url,
                "bot_created": bot_created,
                "stream_created": stream_created,
                "message_created": message_created,
                "selection_changed": selection_changed,
                "pear_config": pear_config,
            },
        },
    )


@typed_endpoint
def get_pear_workspace_topics_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_name: Annotated[
        str,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    access_stream_by_name(user_profile, stream_name)
    return json_success(
        request,
        data={
            "pear_workspace_topics": pear_workspace_topics_for_stream(
                user_profile.realm, stream_name
            ),
        },
    )


@typed_endpoint
def get_pear_topic_context_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_name: Annotated[
        str,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    topic_name: Annotated[
        str,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    include_git: Annotated[
        Json[bool] | None,
        ApiParamConfig("include_git", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = True,
) -> HttpResponse:
    access_stream_by_name(user_profile, stream_name)
    workspace_node = pear_workspace_for_request(
        user_profile,
        stream_name=stream_name,
        topic_name=topic_name,
    )
    return json_success(
        request,
        data={
            "pear_topic_context": serialize_pear_workspace_context(
                workspace_node,
                stream_name=stream_name,
                topic_name=topic_name,
                include_git=bool(include_git),
            ),
        },
    )


@typed_endpoint
def post_pear_topic_context_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_name: Annotated[
        str,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    topic_name: Annotated[
        str,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    model: Annotated[
        str | None,
        ApiParamConfig("model", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        ApiParamConfig("reasoning_effort", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    response_policy: Annotated[
        str | None,
        ApiParamConfig("response_policy", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    include_git: Annotated[
        Json[bool] | None,
        ApiParamConfig("include_git", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = True,
) -> HttpResponse:
    access_stream_by_name(user_profile, stream_name)
    clean_model = clean_pear_model_slug(model) if model is not None else None
    clean_reasoning_effort = (
        clean_pear_reasoning_effort(reasoning_effort) if reasoning_effort is not None else None
    )
    if response_policy is not None and response_policy not in {"ambient", "mentions_only"}:
        raise JsonableError(_("Invalid Pear response policy."))
    clean_response_policy = response_policy
    workspace_node = ensure_pear_workspace_node(user_profile, stream=stream_name, topic=topic_name)
    previous_model = pear_worker_model_for_topic(
        user_profile,
        stream=stream_name,
        topic=topic_name,
    )
    previous_reasoning_effort = pear_worker_reasoning_effort_for_topic(
        user_profile,
        stream=stream_name,
        topic=topic_name,
    )
    previous_response_policy = pear_workspace_response_policy(
        user_profile,
        stream=stream_name,
        topic=topic_name,
    )
    model_changed = clean_model is not None and previous_model != clean_model
    reasoning_effort_changed = (
        clean_reasoning_effort is not None and previous_reasoning_effort != clean_reasoning_effort
    )
    response_policy_changed = (
        clean_response_policy is not None and previous_response_policy != clean_response_policy
    )
    update_fields: list[str] = []
    if clean_model is not None and workspace_node.model != clean_model:
        workspace_node.model = clean_model
        update_fields.append("model")
    if (
        clean_reasoning_effort is not None
        and workspace_node.reasoning_effort != clean_reasoning_effort
    ):
        workspace_node.reasoning_effort = clean_reasoning_effort
        update_fields.append("reasoning_effort")
    if (
        clean_response_policy is not None
        and workspace_node.response_policy != clean_response_policy
    ):
        workspace_node.response_policy = clean_response_policy
        update_fields.append("response_policy")
    if update_fields:
        workspace_node.date_updated = timezone_now()
        workspace_node.save(update_fields=[*update_fields, "date_updated"])

    changed = model_changed or reasoning_effort_changed or response_policy_changed
    # Runtime changes deliberately post no message: the topic header shows
    # the current model/effort to every viewer, and effort changes were
    # already silent — announcing only model flips just filled topics with
    # settings chatter and unread counts.

    return json_success(
        request,
        data={
            "changed": changed,
            "pear_topic_context": serialize_pear_workspace_context(
                workspace_node,
                stream_name=stream_name,
                topic_name=topic_name,
                include_git=bool(include_git),
            ),
        },
    )


@typed_endpoint
def get_pear_active_session_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_name: Annotated[
        str | None,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    topic_name: Annotated[
        str | None,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
) -> HttpResponse:
    pear_session = get_pear_session_payload(stream=stream_name, topic=topic_name)
    try:
        stream, _sub = access_stream_by_name(user_profile, pear_session["stream"])
    except JsonableError:
        active_session = build_pear_active_session_summary(
            pear_session=pear_session,
            source="server",
            stream_exists=False,
            topic_message_count=0,
            latest_message_id=None,
        )
    else:
        assert stream.recipient_id is not None
        message_query = messages_for_topic(
            user_profile.realm_id,
            stream.recipient_id,
            pear_session["topic"],
        )
        message_count = message_query.count()
        message_rows = list(
            message_query.order_by("-id").values_list(
                "id",
                "content",
                "sender__is_bot",
                "sender__full_name",
            )[:PEAR_ACTIVE_SESSION_MESSAGE_LIMIT]
        )
        message_summary = build_pear_active_session_summary_from_messages(
            pear_session=pear_session,
            source="server",
            stream_exists=True,
            messages=message_rows,
            topic_message_count=message_count,
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
        scope_stream, scope_topic = pear_topic_scope(pear_session["stream"], pear_session["topic"])
        topic_runs = AgentRun.objects.filter(
            realm=user_profile.realm,
            session_kind=AgentRun.SHARED,
            stream_name__iexact=scope_stream,
            topic_name__iexact=scope_topic,
        )
        latest_run = topic_runs.order_by("-trigger_message_id", "-id").first()
        latest_thread_run = (
            topic_runs.exclude(codex_thread_id="").order_by("-trigger_message_id", "-id").first()
        )

        # Once routing has accepted a request, AgentRun is the durable
        # source of truth. The message check only covers the short interval
        # before an explicit @Pear request has been claimed by the queue.
        latest_persisted_trigger_id = latest_run.trigger_message_id if latest_run else 0
        pending_explicit_request_ids = [
            message_id
            for message_id, content, _sender_is_bot, _sender_name in message_rows
            if message_id > latest_persisted_trigger_id and is_pear_task_request_message(content)
        ]
        latest_request_message_id = max(
            [
                *pending_explicit_request_ids,
                *([latest_persisted_trigger_id] if latest_run else []),
            ],
            default=None,
        )
        latest_result_message_id = latest_run.result_message_id if latest_run else None
        if (
            latest_result_message_id is not None
            and latest_request_message_id is not None
            and latest_result_message_id < latest_request_message_id
        ):
            latest_result_message_id = None

        active_session = build_pear_active_session_summary(
            pear_session=pear_session,
            source="server",
            stream_exists=True,
            topic_message_count=message_count,
            request_count=topic_runs.count() + len(pending_explicit_request_ids),
            latest_request_message_id=latest_request_message_id,
            latest_message_id=latest_result_message_id,
            latest_codex_thread_id=(
                latest_thread_run.codex_thread_id if latest_thread_run is not None else None
            ),
            recent_messages=message_summary["recent_messages"],
            routing_messages=message_summary["routing_messages"],
            workspace_context=message_summary["workspace_context"],
            is_paused=message_summary["is_paused"],
            response_policy=message_summary["response_policy"],
        )

    return json_success(
        request,
        data={
            "pear_session": pear_session,
            "active_session": active_session,
        },
    )


@typed_endpoint
def get_pear_runs_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_name: Annotated[
        str | None,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    topic_name: Annotated[
        str | None,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    include_events: Annotated[
        Json[bool] | None,
        ApiParamConfig("include_events", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
    include_context: Annotated[
        Json[bool] | None,
        ApiParamConfig("include_context", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = False,
    include_git: Annotated[
        Json[bool] | None,
        ApiParamConfig("include_git", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = True,
    run_ids: Annotated[
        Json[list[int]] | None,
        ApiParamConfig("run_ids", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    workspace_id: Annotated[
        int | None,
        ApiParamConfig("workspace_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
) -> HttpResponse:
    requested_runs = (
        get_visible_pear_worker_runs_by_id(user_profile, run_ids) if run_ids is not None else []
    )
    topic_runs: list[AgentRun]
    workspace_node: Workspace | None = None
    topic_workspace: Workspace | None = None
    if workspace_id is not None:
        workspace_node = Workspace.objects.filter(
            realm=user_profile.realm,
            id=workspace_id,
        ).first()
        if workspace_node is None:
            raise JsonableError(_("Pear workspace not found."))
        if workspace_node.stream_name:
            access_stream_by_name(user_profile, workspace_node.stream_name)
        topic_runs = list(
            AgentRun.objects.select_related("bot_profile", "requesting_user", "workspace_node")
            .filter(realm=user_profile.realm, workspace_node=workspace_node)
            .order_by("-date_created", "-id")[:PEAR_RUN_LIST_LIMIT]
        )
    elif stream_name is not None and topic_name is not None:
        access_stream_by_name(user_profile, stream_name)
        scope_stream, scope_topic = pear_topic_scope(stream_name, topic_name)
        topic_workspace = pear_workspace_for_request(
            user_profile,
            stream_name=stream_name,
            topic_name=topic_name,
        )
        workspace_node = topic_workspace
        shared_run_filter = Q(
            session_kind=AgentRun.SHARED,
            stream_name__iexact=scope_stream,
            topic_name__iexact=scope_topic,
        )
        if workspace_node is not None:
            shared_run_filter |= Q(
                session_kind=AgentRun.SHARED,
                workspace_node=workspace_node,
            )
        topic_runs = list(
            AgentRun.objects.select_related("bot_profile", "requesting_user", "workspace_node")
            .filter(realm=user_profile.realm)
            .filter(
                shared_run_filter
                | Q(
                    session_kind=AgentRun.SIDE,
                    requesting_user=user_profile,
                    source_stream_name__iexact=scope_stream,
                    source_topic_name__iexact=scope_topic,
                )
            )
            .order_by("-date_created", "-id")[:PEAR_RUN_LIST_LIMIT]
        )
    elif requested_runs:
        topic_runs = []
    else:
        topic_runs = list(
            AgentRun.objects.select_related("bot_profile", "requesting_user", "workspace_node")
            .filter(
                realm=user_profile.realm,
                session_kind=AgentRun.SIDE,
                requesting_user=user_profile,
            )
            .order_by("-date_created", "-id")[:PEAR_RUN_LIST_LIMIT]
        )

    runs_by_id = {run.id: run for run in [*topic_runs, *requested_runs]}
    # Refetch the selected rows with their latest event id in one SQL
    # snapshot. This is the authoritative watermark used by the browser to
    # reject an older HTTP response after a newer push event has landed.
    snapshot_runs_by_id = {
        run.id: run
        for run in AgentRun.objects.select_related(
            "bot_profile", "requesting_user", "workspace_node"
        )
        .filter(id__in=runs_by_id)
        .annotate(snapshot_sequence=Max("events__id"))
    }
    runs = sorted(
        snapshot_runs_by_id.values(),
        key=lambda run: (run.date_created, run.id),
        reverse=True,
    )
    summary_events_by_run_id = pear_worker_run_summary_events(runs)
    topic_context = None
    if bool(include_context) and stream_name is not None and topic_name is not None:
        topic_context = serialize_pear_workspace_context(
            topic_workspace
            if topic_workspace is not None
            else pear_workspace_for_request(
                user_profile,
                stream_name=stream_name,
                topic_name=topic_name,
            ),
            stream_name=stream_name,
            topic_name=topic_name,
            include_git=bool(include_git),
        )
    return json_success(
        request,
        data={
            "pear_runs": [
                serialize_pear_worker_run(
                    run,
                    include_events=bool(include_events),
                    summary_events=summary_events_by_run_id[run.id],
                )
                for run in runs
            ],
            "runtime_config": build_pear_runtime_config_payload(),
            "pear_topic_context": topic_context,
        },
    )


@typed_endpoint_without_parameters
def get_pear_run_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
) -> HttpResponse:
    run = get_visible_pear_worker_run(user_profile, run_id)
    return json_success(
        request,
        data={
            "pear_run": serialize_pear_worker_run(run, include_events=True),
            "runtime_config": build_pear_runtime_config_payload(),
        },
    )


def serialize_pear_run_trace_event(event: AgentRunEvent) -> dict[str, object] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.event_type in {"transcript", "agent"}:
        kind = payload.get("kind")
        if kind == "assistant":
            body = payload.get("body")
            if not isinstance(body, str) or not body.strip():
                return None
            return {
                "id": event.id,
                "kind": "assistant",
                "title": "Codex",
                "body": body,
                "status": str(payload.get("status") or "done"),
                "date_created": datetime_to_timestamp(event.date_created),
            }
        if kind == "plan":
            plan = _pear_trace_plan(payload)
            if plan is None:
                return None
            return {
                "id": event.id,
                "kind": "plan",
                "title": "Plan updated",
                "plan": plan,
                "date_created": datetime_to_timestamp(event.date_created),
            }
        if kind in {"agent", "command", "error", "file_change", "status", "tool"}:
            title = payload.get("title")
            serialized: dict[str, object] = {
                "id": event.id,
                "kind": str(kind),
                "title": trim_pear_text(str(title or event.summary), limit=500),
                "status": "failed" if kind == "error" else str(payload.get("status") or "done"),
                "date_created": datetime_to_timestamp(event.date_created),
            }
            item_id = payload.get("item_id")
            if isinstance(item_id, str) and item_id:
                serialized["item_id"] = item_id
            return serialized
        return None
    if event.event_type == "tool_results":
        count = payload.get("tool_count")
        title = f"Ran {count} tools" if isinstance(count, int) else "Tools completed"
    elif event.event_type == "steering_applied":
        title = event.summary or "Steering applied"
    elif event.event_type in {"cancelled", "done", "failed", "running"}:
        title = event.summary or event.event_type.capitalize()
    else:
        return None
    return {
        "id": event.id,
        "kind": event.event_type,
        "title": trim_pear_text(title, limit=500),
        "status": "failed" if event.event_type == "failed" else "done",
        "date_created": datetime_to_timestamp(event.date_created),
    }


def pear_run_trace_markdown(trace: list[dict[str, object]]) -> str:
    """Rebuild the former visible work log without exposing hidden reasoning or raw output."""
    blocks: list[str] = []
    tool_events: list[dict[str, object]] = []

    def flush_tools() -> None:
        if not tool_events:
            return
        visible_events: list[dict[str, object]] = []
        visible_by_item_id: dict[str, dict[str, object]] = {}
        for event in tool_events:
            item_id = event.get("item_id")
            existing = visible_by_item_id.get(item_id) if isinstance(item_id, str) else None
            if existing is None:
                visible_event = dict(event)
                visible_events.append(visible_event)
                if isinstance(item_id, str):
                    visible_by_item_id[item_id] = visible_event
                continue
            # Codex emits lifecycle updates for one item. Keep its stable
            # first title and latest typed terminal status as one visible row.
            existing["status"] = event.get("status") or existing.get("status")
        failures = sum(event.get("status") in {"failed", "error"} for event in visible_events)
        count = len(visible_events)
        title = f"Ran {count} {'tool' if count == 1 else 'tools'}"
        if failures:
            title += f" · {failures} {'retry' if failures == 1 else 'retries'}"
        rows: list[str] = []
        for event in visible_events:
            status = str(event.get("status") or "done")
            glyph = "✷" if status in {"failed", "error"} else "·"
            event_title = " ".join(str(event.get("title") or "tool").split())
            event_title = trim_pear_text(event_title.replace("`", "'"), limit=220)
            rows.append(f"- {glyph} `{event_title}`")
        tool_body = "\n".join(rows)
        blocks.append(f"```spoiler {title}\n{tool_body}\n```")
        tool_events.clear()

    for event in trace:
        kind = str(event.get("kind") or "")
        if kind in {"command", "file_change", "tool"}:
            tool_events.append(event)
            continue
        if kind == "error":
            flush_tools()
            error_title = trim_pear_text(str(event.get("title") or "Agent failed."), limit=500)
            blocks.append(f"**Agent error:** `{error_title.replace('`', "'")}`")
            continue
        if kind == "assistant":
            flush_tools()
            body = event.get("body")
            if isinstance(body, str) and body.strip():
                blocks.append(normalize_pear_visible_markdown(body.strip()))
    flush_tools()
    return "\n\n".join(blocks)


def _pear_trace_plan(payload: dict[str, object]) -> list[dict[str, str]] | None:
    metadata = payload.get("metadata")
    steps = metadata.get("steps") if isinstance(metadata, dict) else None
    if not isinstance(steps, list):
        return None
    plan = [
        {"step": str(step["step"]), "status": str(step.get("status", "pending"))}
        for step in steps
        if isinstance(step, dict) and step.get("step")
    ]
    return plan or None


@typed_endpoint
def get_pear_run_trace_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
    *,
    before_event_id: Annotated[
        Json[int] | None,
        ApiParamConfig("before_event_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    limit: Annotated[
        Json[int],
        ApiParamConfig("limit", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = PEAR_RUN_TRACE_LIMIT,
    thread_id: Annotated[
        str | None,
        ApiParamConfig("thread_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
) -> HttpResponse:
    run = get_visible_pear_worker_run(user_profile, run_id)
    requested_limit = max(1, min(limit, PEAR_RUN_TRACE_MAX_LIMIT))
    if thread_id is not None:
        visible_thread_ids = {
            str(agent["thread_id"]) for agent in serialize_pear_agent_threads(run)
        }
        if thread_id not in visible_thread_ids:
            raise JsonableError(_("Pear agent thread not found."))
        events = run.events.filter(
            event_type="agent",
            payload__metadata__codex_thread_id=thread_id,
            payload__kind__in=[
                "agent",
                "assistant",
                "plan",
                "command",
                "file_change",
                "error",
                "status",
                "tool",
            ],
        ).order_by("-id")
    else:
        events = run.events.filter(
            Q(
                event_type="transcript",
                payload__kind__in=[
                    "assistant",
                    "plan",
                    "command",
                    "file_change",
                    "status",
                    "tool",
                ],
            )
            | Q(
                event_type__in=[
                    "tool_results",
                    "steering_applied",
                    "cancelled",
                    "done",
                    "failed",
                    "running",
                ]
            )
        ).order_by("-id")
    if before_event_id is not None:
        events = events.filter(id__lt=before_event_id)
    candidates = list(events[: requested_limit + 1])
    trace = [
        serialized
        for event in reversed(candidates[:requested_limit])
        if (serialized := serialize_pear_run_trace_event(event)) is not None
    ]
    trace_markdown = pear_run_trace_markdown(trace)
    trace_message = Message()
    trace_message.sender = run.bot_profile
    trace_message.realm = user_profile.realm
    trace_message.content = trace_markdown
    rendered_trace = render_message_markdown(
        trace_message,
        trace_markdown,
        realm=user_profile.realm,
        acting_user=user_profile,
        no_previews=True,
    ).rendered_content
    return json_success(
        request,
        data={
            "run_id": run.id,
            "thread_id": thread_id,
            "trace": trace,
            "rendered": rendered_trace,
            "has_more": len(candidates) > requested_limit,
            "next_before_event_id": candidates[requested_limit - 1].id
            if len(candidates) > requested_limit
            else None,
        },
    )


@typed_endpoint_without_parameters
def post_pear_run_attention_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
) -> HttpResponse:
    run = get_visible_pear_worker_run(user_profile, run_id)
    if run.status not in {AgentRun.QUEUED, AgentRun.RUNNING}:
        return json_success(request, data={"attended": False})
    mark_pear_run_attended(run.id)
    latest_event_id = run.events.order_by("-id").values_list("id", flat=True).first()
    if latest_event_id is not None:
        maybe_enqueue_pear_run_presentation(run.id, latest_event_id)
    return json_success(request, data={"attended": True})


def update_pear_run_control_event(
    user_profile: UserProfile,
    run_id: int,
    event_type: str,
    summary: str,
) -> AgentRun:
    visible_run = get_visible_pear_worker_run(user_profile, run_id)
    with transaction.atomic(savepoint=False):
        if event_type in {"pause", "resume"}:
            # Worker claims take locks in realm -> workspace -> run order.  Keep
            # controls on the same workspace-before-run order so a pause cannot
            # deadlock with a claim holding the workspace row while waiting for
            # this run row.
            control_stream = (
                visible_run.source_stream_name if visible_run.session_kind == AgentRun.SIDE else ""
            )
            control_topic = (
                visible_run.source_topic_name if visible_run.session_kind == AgentRun.SIDE else ""
            )
            set_pear_workspace_paused(
                visible_run.bot_profile,
                stream=control_stream or visible_run.stream_name,
                topic=control_topic or visible_run.topic_name,
                is_paused=event_type == "pause",
            )
        run = AgentRun.objects.select_for_update().get(id=visible_run.id)
        record_control_event = True
        if event_type == "cancel" and run.status in {
            AgentRun.QUEUED,
            AgentRun.RUNNING,
        }:
            record_control_event = PearDatabaseWorkerRunStore().cancel_locked_run(
                run, summary="Cancelled."
            )
        elif event_type == "cancel":
            record_control_event = False
        elif event_type in {"pause", "resume"}:
            run.current_activity = summary
            run.date_updated = timezone_now()
            run.save(update_fields=["current_activity", "date_updated"])
        if record_control_event:
            run_event = AgentRunEvent.objects.create(
                run=run,
                event_type=event_type,
                summary=summary,
                payload={"user_id": user_profile.id},
            )
            # Capture this exact transition's payload now; the action helper
            # defers delivery with send_event_on_commit.
            send_pear_run_event(run, run_event)
    return run


@typed_endpoint_without_parameters
def post_pear_run_cancel_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
) -> HttpResponse:
    run = update_pear_run_control_event(user_profile, run_id, "cancel", "Cancelled by user.")
    return json_success(
        request,
        data={"pear_run": serialize_pear_worker_run(run, include_events=True)},
    )


@typed_endpoint_without_parameters
def post_pear_run_not_for_pear_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
) -> HttpResponse:
    visible_run = get_visible_pear_worker_run(user_profile, run_id)
    if not visible_run.ambient_triggered:
        raise JsonableError(_("This Pear response was explicitly requested."))

    if visible_run.status in {AgentRun.QUEUED, AgentRun.RUNNING}:
        visible_run = update_pear_run_control_event(
            user_profile,
            run_id,
            "cancel",
            "Cancelled after Not for Pear feedback.",
        )

    with transaction.atomic(savepoint=False):
        run = AgentRun.objects.select_for_update().get(id=visible_run.id)
        feedback_exists = run.events.filter(event_type="routing_feedback").exists()
        if not feedback_exists:
            AgentRunEvent.objects.create(
                run=run,
                event_type="routing_feedback",
                summary="Not for Pear.",
                payload={
                    "label": "false_positive",
                    "trigger_message_id": run.trigger_message_id,
                    "result_message_id": run.result_message_id,
                    "user_id": user_profile.id,
                },
            )

    return json_success(
        request,
        data={
            "changed": not feedback_exists,
            "pear_run": serialize_pear_worker_run(run, include_events=True),
        },
    )


@typed_endpoint_without_parameters
def post_pear_run_pause_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
) -> HttpResponse:
    run = update_pear_run_control_event(user_profile, run_id, "pause", "Paused by user.")
    return json_success(
        request,
        data={"pear_run": serialize_pear_worker_run(run, include_events=True)},
    )


@typed_endpoint_without_parameters
def post_pear_run_resume_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    run_id: int,
) -> HttpResponse:
    run = update_pear_run_control_event(user_profile, run_id, "resume", "Resumed by user.")
    return json_success(
        request,
        data={"pear_run": serialize_pear_worker_run(run, include_events=True)},
    )


def check_pear_user_may_start_runs(user_profile: UserProfile) -> None:
    if not pear_user_is_trusted(user_profile):
        raise JsonableError(_("You are not allowed to start agent runs."))


def _dispatch_pear_monitor_run(
    user_profile: UserProfile,
    pear_bot: Service,
    monitor: AgentMonitor,
    *,
    repair_error: str = "",
) -> None:
    """Mint a DM anchor and queue the monitor's setup/repair agent run.
    The run is a normal MONITOR-kind AgentRun; ticks never are."""
    stream_name = monitor.stream_name
    topic_name = monitor.topic_name
    task = build_pear_monitor_contract(
        monitor_id=monitor.id,
        ask=monitor.ask,
        stream=stream_name,
        topic=topic_name,
        repair_error=repair_error,
    )
    dm_intro = "\n".join(
        [
            "Pear monitor setup started.",
            "",
            f"Topic: {stream_name} > {topic_name}",
            f"Monitor: {monitor.ask}",
        ]
    )
    dm_message_id = internal_send_private_message(
        pear_bot.user_profile,
        user_profile,
        dm_intro,
        disable_external_notifications=True,
    )
    if dm_message_id is None:
        raise JsonableError(_("Pear could not open the monitor transcript."))
    monitor.anchor_message_id = dm_message_id
    monitor.status = AgentMonitor.PROVISIONING
    monitor.date_updated = timezone_now()
    monitor.save(update_fields=["anchor_message_id", "status", "date_updated"])

    run = AgentRun.objects.create(
        realm=user_profile.realm,
        bot_profile=pear_bot.user_profile,
        requesting_user=user_profile,
        trigger_message_id=dm_message_id,
        stream_name=stream_name,
        topic_name=topic_name,
        session_kind=AgentRun.MONITOR,
        source_stream_name=stream_name,
        source_topic_name=topic_name,
        status=AgentRun.QUEUED,
        summary="Queued monitor setup.",
        current_activity=trim_pear_text(monitor.ask, limit=200),
        runtime_label=str(settings.PEAR_WORKER_AGENT_RUNTIME or ""),
        model=pear_worker_model_for_topic(
            pear_bot.user_profile, stream=stream_name, topic=topic_name
        ),
        sandbox=pear_worker_sandbox_for_topic(
            pear_bot.user_profile, stream=stream_name, topic=topic_name
        ),
        approval_policy=str(settings.PEAR_WORKER_APPROVAL_POLICY or ""),
        workdir=pear_worker_workdir_for_topic(
            pear_bot.user_profile, stream=stream_name, topic=topic_name
        ),
    )
    AgentRunEvent.objects.create(
        run=run,
        event_type="queued",
        summary="Queued monitor setup.",
        payload={"monitor_id": monitor.id, "ask": trim_pear_text(monitor.ask, limit=500)},
    )
    worker_message = {
        "id": dm_message_id,
        "type": "private",
        "display_recipient": [
            {
                "id": user_profile.id,
                "email": user_profile.email,
                "full_name": user_profile.full_name,
            }
        ],
        "sender_id": user_profile.id,
        "sender_email": user_profile.email,
        "content": task,
    }
    PearQueueWorkerDispatcher().dispatch(
        PearEmbeddedBotHandler(worker_run_store=PearDatabaseWorkerRunStore()),
        SimpleNamespace(user_profile=pear_bot.user_profile),
        worker_message,
        content=task,
        active_session=None,
        message_id=dm_message_id,
        stream=stream_name,
        topic=topic_name,
        requesting_user_id=user_profile.id,
        session_kind=AgentRun.MONITOR,
        source_stream=stream_name,
        source_topic=topic_name,
        progress_message_id=dm_message_id,
    )


@typed_endpoint
def get_pear_monitors_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_name: Annotated[
        str,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    topic_name: Annotated[
        str,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    access_stream_by_name(user_profile, stream_name)
    stream_name, topic_name = pear_topic_scope(stream_name, topic_name)
    monitors = AgentMonitor.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=stream_name,
        topic_name__iexact=topic_name,
    ).order_by("position", "id")
    return json_success(
        request,
        data={"pear_monitors": [serialize_pear_monitor(monitor) for monitor in monitors]},
    )


@typed_endpoint
def post_pear_monitors_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    ask_request: Annotated[
        str,
        ApiParamConfig("request", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    stream_name: Annotated[
        str,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    topic_name: Annotated[
        str,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    spec: Annotated[
        Json[dict[str, object]] | None,
        ApiParamConfig("spec", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
) -> HttpResponse:
    ask_request = ask_request.strip()
    if not ask_request:
        raise JsonableError(_("Pear monitor request cannot be empty."))
    access_stream_by_name(user_profile, stream_name)
    stream_name, topic_name = pear_topic_scope(stream_name, topic_name)
    pear_bot = get_selected_pear_bot(user_profile)
    if pear_bot is None:
        raise JsonableError(_("Pear bot is not configured."))
    topic_count = AgentMonitor.objects.filter(
        realm=user_profile.realm,
        stream_name__iexact=stream_name,
        topic_name__iexact=topic_name,
    ).count()
    if topic_count >= PEAR_MONITOR_MAX_PER_TOPIC:
        raise JsonableError(_("This topic already has the maximum number of monitors."))
    realm_count = AgentMonitor.objects.filter(realm=user_profile.realm).count()
    if realm_count >= PEAR_MONITOR_MAX_PER_REALM:
        raise JsonableError(_("This organization already has the maximum number of monitors."))

    if spec is not None:
        # Pin-to-wall: the artifact spec is already composed; the monitor
        # goes live immediately with no agent run.
        try:
            monitor = pear_create_monitor_from_spec(
                realm=user_profile.realm,
                bot_profile=pear_bot.user_profile,
                creator=user_profile,
                stream_name=stream_name,
                topic_name=topic_name,
                ask=ask_request,
                raw_spec=spec,
                position=topic_count,
            )
        except PearMonitorSpecError as error:
            raise JsonableError(_("Invalid artifact spec: {error}").format(error=error))
        return json_success(request, data={"pear_monitor": serialize_pear_monitor(monitor)})

    check_pear_user_may_start_runs(user_profile)
    monitor = AgentMonitor.objects.create(
        realm=user_profile.realm,
        bot_profile=pear_bot.user_profile,
        creator=user_profile,
        stream_name=stream_name,
        topic_name=topic_name,
        title=trim_pear_text(ask_request, limit=24),
        ask=trim_pear_text(ask_request, limit=500),
        position=topic_count,
    )
    _dispatch_pear_monitor_run(user_profile, pear_bot, monitor)
    return json_success(request, data={"pear_monitor": serialize_pear_monitor(monitor)})


def _get_accessible_pear_monitor(user_profile: UserProfile, monitor_id: int) -> AgentMonitor:
    try:
        monitor = AgentMonitor.objects.get(id=monitor_id, realm=user_profile.realm)
    except AgentMonitor.DoesNotExist as error:
        raise JsonableError(_("Pear monitor not found.")) from error
    access_stream_by_name(user_profile, monitor.stream_name)
    return monitor


@typed_endpoint
def update_pear_monitor_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    monitor_id: int,
    *,
    action: Annotated[
        str,
        ApiParamConfig("action", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    cadence_seconds: Annotated[
        Json[int] | None,
        ApiParamConfig("cadence_seconds", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    title: Annotated[
        str | None,
        ApiParamConfig("title", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
) -> HttpResponse:
    monitor = _get_accessible_pear_monitor(user_profile, monitor_id)
    now = timezone_now()
    if action == "pause":
        monitor.status = AgentMonitor.PAUSED
        monitor.next_due_at = None
    elif action == "resume":
        if not monitor.recipe:
            raise JsonableError(_("This monitor has no recipe to resume."))
        monitor.status = AgentMonitor.LIVE
        monitor.consecutive_failures = 0
        monitor.next_due_at = now
    elif action == "refresh_now":
        if monitor.status != AgentMonitor.LIVE:
            raise JsonableError(_("Only live monitors can refresh."))
        monitor.next_due_at = now
    elif action == "cadence":
        if cadence_seconds is None:
            raise JsonableError(_("cadence_seconds is required."))
        monitor.cadence_seconds = min(
            max(cadence_seconds, PEAR_MONITOR_MIN_CADENCE_SECONDS),
            PEAR_MONITOR_MAX_CADENCE_SECONDS,
        )
        if monitor.status == AgentMonitor.LIVE:
            monitor.next_due_at = now
    elif action == "rename":
        if title is None or not title.strip():
            raise JsonableError(_("title is required."))
        monitor.title = title.strip()[:24]
    elif action == "rebuild":
        check_pear_user_may_start_runs(user_profile)
        pear_bot = get_selected_pear_bot(user_profile)
        if pear_bot is None:
            raise JsonableError(_("Pear bot is not configured."))
        monitor.date_updated = now
        monitor.save(update_fields=["date_updated"])
        _dispatch_pear_monitor_run(user_profile, pear_bot, monitor, repair_error=monitor.last_error)
        monitor.refresh_from_db()
        return json_success(request, data={"pear_monitor": serialize_pear_monitor(monitor)})
    else:
        raise JsonableError(_("Unknown monitor action."))
    monitor.date_updated = now
    monitor.save()
    return json_success(request, data={"pear_monitor": serialize_pear_monitor(monitor)})


@typed_endpoint_without_parameters
def delete_pear_monitor_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    monitor_id: int,
) -> HttpResponse:
    monitor = _get_accessible_pear_monitor(user_profile, monitor_id)
    monitor.delete()
    return json_success(request)
