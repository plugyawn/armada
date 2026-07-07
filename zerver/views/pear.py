import json
import subprocess
import time
from pathlib import Path
from shutil import which
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from pydantic import Json

from zerver.actions.message_send import internal_send_private_message, internal_send_stream_message
from zerver.actions.streams import bulk_add_subscriptions
from zerver.decorator import require_realm_admin
from zerver.lib.bot_config import get_bot_configs, set_bot_config
from zerver.lib.exceptions import JsonableError
from zerver.lib.pear import (
    PEAR_AGENT_NAME,
    PEAR_BOT_SHORT_NAME,
    PEAR_CANVAS_FILE_NAME,
    PEAR_EMBEDDED_BOT_SERVICE,
    PEAR_FIRST_RUN_STARTER_MESSAGE,
    PEAR_FIRST_RUN_STREAM,
    PEAR_FIRST_RUN_TOPIC,
    PEAR_REASONING_EFFORTS,
    PEAR_SELECTED_BOT_CONFIG_KEY,
    PEAR_SELECTED_BOT_CONFIG_VALUE,
    PEAR_SIDE_ENDPOINT,
    PearConfigPayload,
    PearEmbeddedBotHandler,
    PearTokenUsage,
    build_pear_active_session_summary,
    build_pear_active_session_summary_from_messages,
    build_pear_config_payload,
    get_pear_live_run_state,
    get_pear_session_payload,
    normalize_pear_response_policy,
    pear_normalize_token_usage,
    pear_token_usage_summary,
)
from zerver.lib.pear_gate import invalidate_pear_selected_bot_cache
from zerver.lib.pear_worker import (
    PearQueueWorkerDispatcher,
    ensure_pear_child_topic_context,
    ensure_pear_topic_context,
    pear_default_worker_reasoning_effort,
    pear_exclude_canvas_from_git_delta,
    pear_git_output,
    pear_git_status_delta,
    pear_logical_repository_label,
    pear_logical_worktree_label,
    pear_pinned_repository_value,
    pear_read_text_path,
    pear_repo_artifact_value_is_durable,
    pear_sha256_text,
    pear_topic_canvas_path,
    pear_topic_context_is_paused,
    pear_topic_context_response_policy,
    pear_topic_isolation_user_exists,
    pear_topic_isolation_username_for_realm,
    pear_topic_os_user_isolation_enabled,
    pear_worker_model_for_topic,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_sandbox_for_topic,
    pear_worker_workdir_for_topic,
    pear_workspace_node_default_branch,
    pear_write_text_path,
    set_pear_topic_context_paused,
    set_pear_topic_context_response_policy,
)
from zerver.lib.response import json_response, json_success
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
from zerver.models.bots import (
    PearTopicContext,
    PearWorkerRun,
    PearWorkerRunEvent,
    PearWorkspaceNode,
    Service,
)
from zerver.models.users import UserProfile
from zerver.views.users import add_bot_backend

PEAR_ACTIVE_SESSION_MESSAGE_LIMIT = 50
PEAR_RUN_LIST_LIMIT = 20
PEAR_RUN_IDS_FILTER_LIMIT = 20
PEAR_RUN_EVENT_LIMIT = 40
PEAR_GIT_TREE_COMMIT_LIMIT = 20
PEAR_MODEL_SLUG_LIMIT = 100
PEAR_MODEL_CATALOG_CACHE_SECONDS = 300
PEAR_CANVAS_MAX_BYTES = 64 * 1024

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    output = "\n".join((completed.stdout, completed.stderr))
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


def pear_git_command(
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


def pear_git_remote_label(remote_url: str, repo_root: str) -> str:
    clean_url = remote_url.strip()
    if clean_url:
        if clean_url.startswith("git@") and ":" in clean_url:
            path = clean_url.split(":", 1)[1]
            return path.removesuffix(".git").strip("/") or clean_url
        parsed = urlparse(clean_url)
        if parsed.scheme and parsed.netloc:
            path = parsed.path.removesuffix(".git").strip("/")
            return path or parsed.netloc
        return Path(clean_url.removesuffix(".git")).name or clean_url
    return Path(repo_root).name


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


def pear_context_isolation_username(
    context: PearTopicContext | None,
    *,
    stream_name: str,
    topic_name: str,
    workdir: str,
) -> str | None:
    if (
        context is not None
        and pear_topic_os_user_isolation_enabled()
        and pear_repo_artifact_value_is_durable(workdir)
    ):
        username = pear_topic_isolation_username_for_realm(
            context.realm_id,
            stream=stream_name,
            topic=topic_name,
        )
        if not pear_topic_isolation_user_exists(username):
            return None
        return username
    return None


def pear_topic_context_for_request(
    user_profile: UserProfile,
    *,
    stream_name: str,
    topic_name: str,
) -> PearTopicContext | None:
    return (
        PearTopicContext.objects.select_related("parent_context", "workspace_node__project")
        .filter(
            realm=user_profile.realm,
            stream_name=stream_name,
            topic_name=topic_name,
        )
        .first()
    )


def serialize_pear_topic_context(
    context: PearTopicContext | None,
    *,
    stream_name: str,
    topic_name: str,
    include_git: bool = True,
) -> dict[str, object]:
    runtime_config = build_pear_runtime_config_payload()
    default_model = str(runtime_config.get("model") or "")
    default_reasoning_effort = str(runtime_config.get("reasoning_effort") or "medium")
    selected_model = (
        context.model.strip() if context is not None and context.model.strip() else default_model
    )
    parent_context = (
        context.parent_context
        if context is not None and context.parent_context_id is not None
        else None
    )
    selected_reasoning_effort = (
        context.reasoning_effort.strip()
        if context is not None and context.reasoning_effort.strip()
        else (
            parent_context.reasoning_effort.strip()
            if parent_context is not None and parent_context.reasoning_effort.strip()
            else default_reasoning_effort
        )
    )
    selected_response_policy = normalize_pear_response_policy(
        context.response_policy if context is not None else None
    )
    workdir = context.workdir.strip() if context is not None and context.workdir.strip() else ""
    context_github_repo = context.github_repo.strip() if context is not None else ""
    parent_github_repo = parent_context.github_repo.strip() if parent_context is not None else ""
    github_repo = next(
        (
            repo_value
            for repo_value in (context_github_repo, parent_github_repo)
            if pear_repo_artifact_value_is_durable(repo_value)
        ),
        "",
    )
    pinned_repo = next(
        (
            repo_value
            for repo_value in (
                pear_pinned_repository_value(context_github_repo),
                pear_pinned_repository_value(parent_github_repo),
            )
            if repo_value
        ),
        "",
    )
    repo_label = pear_logical_repository_label(
        stream=stream_name,
        context_repo=context_github_repo,
        parent_repo=parent_github_repo,
    )
    worktree_label = pear_logical_worktree_label(
        stream=stream_name,
        topic=topic_name,
        fallback=Path(workdir).name,
    )
    workspace_node = (
        context.workspace_node
        if context is not None and context.workspace_node_id is not None
        else None
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
    isolation_username = pear_context_isolation_username(
        context,
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
        "head": workspace_node.head_commit if workspace_node is not None else "",
        "github_repo": github_repo,
        "pinned_repo": pinned_repo,
        "repo_label": repo_label,
        "repo_title": pinned_repo or workdir,
        "worktree_label": worktree_label,
        "workdir": workdir,
        "is_paused": bool(context is not None and context.is_paused),
        "parent_context_id": context.parent_context_id if context is not None else None,
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


def serialize_pear_worker_run_event(event: PearWorkerRunEvent) -> dict[str, object]:
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


def serialize_pear_worker_run_usage(run: PearWorkerRun) -> dict[str, object] | None:
    event = (
        run.events.filter(Q(event_type="usage") | Q(event_type="transcript"))
        .order_by("-date_created", "-id")
        .first()
    )
    usage = pear_usage_from_event_payload(event.payload) if event is not None else None
    if usage is None:
        return None
    duration_seconds = None
    if run.date_finished is not None:
        duration_seconds = round((run.date_finished - run.date_created).total_seconds(), 1)
    elif run.status in {PearWorkerRun.QUEUED, PearWorkerRun.RUNNING}:
        duration_seconds = round((timezone_now() - run.date_created).total_seconds(), 1)
    return {
        **usage,
        "summary": pear_token_usage_summary(usage),
        "duration_seconds": duration_seconds,
        "event_id": event.id if event is not None else None,
    }


def serialize_pear_worker_run(
    run: PearWorkerRun,
    *,
    include_events: bool = False,
) -> dict[str, object]:
    live_state = get_pear_live_run_state(run.trigger_message_id)
    if (
        run.status not in {PearWorkerRun.QUEUED, PearWorkerRun.RUNNING}
        and live_state is not None
        and live_state["status"] in {PearWorkerRun.QUEUED, PearWorkerRun.RUNNING}
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
        "usage": serialize_pear_worker_run_usage(run),
        "live": live_state,
    }
    if include_events:
        payload["events"] = [
            serialize_pear_worker_run_event(event)
            for event in run.events.order_by("-date_created", "-id")[:PEAR_RUN_EVENT_LIMIT]
        ]
    return payload


def check_can_access_pear_worker_run(
    user_profile: UserProfile,
    run: PearWorkerRun,
) -> None:
    if run.realm_id != user_profile.realm_id:
        raise JsonableError(_("Pear run not found."))
    if run.session_kind == PearWorkerRun.SIDE:
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
) -> PearWorkerRun:
    try:
        run = PearWorkerRun.objects.select_related(
            "bot_profile",
            "requesting_user",
            "workspace_node",
        ).get(id=run_id, realm=user_profile.realm)
    except PearWorkerRun.DoesNotExist as error:
        raise JsonableError(_("Pear run not found.")) from error
    check_can_access_pear_worker_run(user_profile, run)
    return run


def get_visible_pear_worker_runs_by_id(
    user_profile: UserProfile,
    run_ids: list[int],
) -> list[PearWorkerRun]:
    normalized_run_ids = list(dict.fromkeys(run_ids))
    if len(normalized_run_ids) > PEAR_RUN_IDS_FILTER_LIMIT:
        raise JsonableError(_("Too many Pear runs requested."))
    if any(run_id <= 0 for run_id in normalized_run_ids):
        raise JsonableError(_("Pear run not found."))

    runs = list(
        PearWorkerRun.objects.select_related(
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
    context = pear_topic_context_for_request(
        user_profile,
        stream_name=stream_name,
        topic_name=topic_name,
    )
    return json_success(
        request,
        data={
            "pear_topic_context": serialize_pear_topic_context(
                context,
                stream_name=stream_name,
                topic_name=topic_name,
                include_git=bool(include_git),
            ),
        },
    )


@typed_endpoint
def get_pear_git_tree_backend(
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
    context = pear_topic_context_for_request(
        user_profile,
        stream_name=stream_name,
        topic_name=topic_name,
    )
    workdir = context.workdir.strip() if context is not None and context.workdir.strip() else ""
    isolation_username = pear_context_isolation_username(
        context,
        stream_name=stream_name,
        topic_name=topic_name,
        workdir=workdir,
    )
    expected_branch = (
        context.workspace_node.branch_name.strip()
        if context is not None
        and context.workspace_node_id is not None
        and context.workspace_node.branch_name.strip()
        else pear_workspace_node_default_branch()
    )
    delta = build_pear_git_context(
        workdir,
        topic=expected_branch,
        isolation_username=isolation_username,
    )
    graph = ""
    status_short = ""
    if delta.get("available") is True:
        graph = pear_git_output(
            workdir,
            ["log", "--graph", "--oneline", "--decorate", f"-{PEAR_GIT_TREE_COMMIT_LIMIT}"],
            isolation_username=isolation_username,
        )
        status_short = pear_git_output(
            workdir,
            ["status", "--short"],
            isolation_username=isolation_username,
        )

    return json_success(
        request,
        data={
            "git_tree": {
                "available": delta.get("available") is True,
                "workdir": workdir,
                "graph": graph,
                "status_short": status_short,
                "delta": delta,
            },
        },
    )


def pear_canvas_exists(canvas_path: Path, content: str) -> bool:
    if content:
        return True
    try:
        return canvas_path.exists()
    except OSError:
        return False


def pear_canvas_payload(
    *,
    workdir: str,
    canvas_path: Path | None,
    content: str,
) -> dict[str, object]:
    relative_path = ""
    if canvas_path is not None and workdir:
        relative_path = str(canvas_path.relative_to(workdir))
    elif workdir:
        relative_path = PEAR_CANVAS_FILE_NAME
    return {
        "content": content,
        "version": pear_sha256_text(content),
        "workdir_path": workdir,
        "path": str(canvas_path) if canvas_path is not None else "",
        "relative_path": relative_path,
        "exists": pear_canvas_exists(canvas_path, content) if canvas_path is not None else False,
        "max_bytes": PEAR_CANVAS_MAX_BYTES,
    }


@typed_endpoint
def get_pear_canvas_backend(
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
    context = pear_topic_context_for_request(
        user_profile,
        stream_name=stream_name,
        topic_name=topic_name,
    )
    if context is None or not context.workdir.strip():
        return json_success(
            request,
            data={
                "pear_canvas": pear_canvas_payload(
                    workdir="",
                    canvas_path=None,
                    content="",
                ),
            },
        )
    workdir = context.workdir.strip()
    isolation_username = pear_context_isolation_username(
        context,
        stream_name=stream_name,
        topic_name=topic_name,
        workdir=workdir,
    )
    canvas_path = pear_topic_canvas_path(workdir)
    content = pear_read_text_path(canvas_path, isolation_username=isolation_username)
    return json_success(
        request,
        data={
            "pear_canvas": pear_canvas_payload(
                workdir=workdir,
                canvas_path=canvas_path,
                content=content,
            ),
        },
    )


@typed_endpoint
def put_pear_canvas_backend(
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
    content: Annotated[
        str,
        ApiParamConfig("content", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    base_version: Annotated[
        str,
        ApiParamConfig("base_version", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
) -> HttpResponse:
    access_stream_by_name(user_profile, stream_name)
    if len(content.encode("utf-8")) > PEAR_CANVAS_MAX_BYTES:
        raise JsonableError(_("Pear canvas is too large."))
    context = ensure_pear_topic_context(
        user_profile,
        stream=stream_name,
        topic=topic_name,
        ensure_canvas=False,
    )
    workdir = context.workdir.strip()
    isolation_username = pear_context_isolation_username(
        context,
        stream_name=stream_name,
        topic_name=topic_name,
        workdir=workdir,
    )
    pear_exclude_canvas_from_git_delta(workdir)
    canvas_path = pear_topic_canvas_path(workdir)
    current_content = pear_read_text_path(canvas_path, isolation_username=isolation_username)
    current_version = pear_sha256_text(current_content)
    if base_version != current_version:
        return json_response(
            "error",
            msg="Pear canvas changed; reload before saving.",
            data={
                "pear_canvas": pear_canvas_payload(
                    workdir=workdir,
                    canvas_path=canvas_path,
                    content=current_content,
                ),
            },
            status=409,
        )
    if not pear_write_text_path(
        canvas_path,
        content,
        isolation_username=isolation_username,
    ):
        raise JsonableError(_("Could not write Pear canvas."))
    return json_success(
        request,
        data={
            "pear_canvas": pear_canvas_payload(
                workdir=workdir,
                canvas_path=canvas_path,
                content=content,
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
    stream, _sub = access_stream_by_name(user_profile, stream_name)
    clean_model = clean_pear_model_slug(model) if model is not None else None
    clean_reasoning_effort = (
        clean_pear_reasoning_effort(reasoning_effort) if reasoning_effort is not None else None
    )
    clean_response_policy = (
        normalize_pear_response_policy(response_policy) if response_policy is not None else None
    )
    context = ensure_pear_topic_context(user_profile, stream=stream_name, topic=topic_name)
    previous_model = (
        context.model.strip() if context.model.strip() else str(settings.PEAR_WORKER_MODEL or "")
    )
    previous_reasoning_effort = pear_worker_reasoning_effort_for_topic(
        user_profile,
        stream=stream_name,
        topic=topic_name,
    )
    previous_response_policy = pear_topic_context_response_policy(
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
    if model_changed and clean_model is not None:
        context.model = clean_model
        context.date_updated = timezone_now()
        context.save(update_fields=["model", "date_updated"])
        if context.workspace_node_id is not None:
            context.workspace_node.model = clean_model
            context.workspace_node.date_updated = context.date_updated
            context.workspace_node.save(update_fields=["model", "date_updated"])
    elif clean_model is not None and not context.model:
        context.model = clean_model
        context.date_updated = timezone_now()
        context.save(update_fields=["model", "date_updated"])
        if context.workspace_node_id is not None:
            context.workspace_node.model = clean_model
            context.workspace_node.date_updated = context.date_updated
            context.workspace_node.save(update_fields=["model", "date_updated"])

    if clean_reasoning_effort is not None:
        if reasoning_effort_changed or not context.reasoning_effort:
            context.reasoning_effort = clean_reasoning_effort
            context.date_updated = timezone_now()
            context.save(update_fields=["reasoning_effort", "date_updated"])
            if context.workspace_node_id is not None:
                context.workspace_node.reasoning_effort = clean_reasoning_effort
                context.workspace_node.date_updated = context.date_updated
                context.workspace_node.save(update_fields=["reasoning_effort", "date_updated"])

    if clean_response_policy is not None:
        set_pear_topic_context_response_policy(
            user_profile,
            stream=stream_name,
            topic=topic_name,
            response_policy=clean_response_policy,
        )
        context.refresh_from_db(fields=["response_policy", "date_updated"])

    changed = model_changed or reasoning_effort_changed or response_policy_changed
    if model_changed and clean_model is not None:
        pear_bot = get_selected_pear_bot(user_profile)
        if pear_bot is None:
            raise JsonableError(_("Pear bot is not configured."))
        internal_send_stream_message(
            pear_bot.user_profile,
            stream,
            topic_name,
            f"{user_profile.full_name} changed current model to `{clean_model}`.",
            mark_as_read_for_acting_user=True,
            acting_user=user_profile,
        )

    return json_success(
        request,
        data={
            "changed": changed,
            "pear_topic_context": serialize_pear_topic_context(
                context,
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
            checklist=[],
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
        active_session = build_pear_active_session_summary_from_messages(
            pear_session=pear_session,
            source="server",
            stream_exists=True,
            messages=message_rows,
            topic_message_count=message_count,
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
    topic_runs: list[PearWorkerRun]
    workspace_node: PearWorkspaceNode | None = None
    topic_context_record: PearTopicContext | None = None
    if workspace_id is not None:
        workspace_node = PearWorkspaceNode.objects.filter(
            realm=user_profile.realm,
            id=workspace_id,
        ).first()
        if workspace_node is None:
            raise JsonableError(_("Pear workspace not found."))
        if workspace_node.stream_name:
            access_stream_by_name(user_profile, workspace_node.stream_name)
        topic_runs = list(
            PearWorkerRun.objects.select_related("bot_profile", "requesting_user", "workspace_node")
            .filter(realm=user_profile.realm, workspace_node=workspace_node)
            .order_by("-date_created", "-id")[:PEAR_RUN_LIST_LIMIT]
        )
    elif stream_name is not None and topic_name is not None:
        access_stream_by_name(user_profile, stream_name)
        topic_context_record = pear_topic_context_for_request(
            user_profile,
            stream_name=stream_name,
            topic_name=topic_name,
        )
        workspace_node = (
            topic_context_record.workspace_node
            if topic_context_record is not None
            and topic_context_record.workspace_node_id is not None
            else None
        )
        shared_run_filter = Q(
            session_kind=PearWorkerRun.SHARED,
            stream_name=stream_name,
            topic_name=topic_name,
        )
        if workspace_node is not None:
            shared_run_filter |= Q(
                session_kind=PearWorkerRun.SHARED,
                workspace_node=workspace_node,
            )
        topic_runs = list(
            PearWorkerRun.objects.select_related("bot_profile", "requesting_user", "workspace_node")
            .filter(realm=user_profile.realm)
            .filter(
                shared_run_filter
                | Q(
                    session_kind=PearWorkerRun.SIDE,
                    requesting_user=user_profile,
                    source_stream_name=stream_name,
                    source_topic_name=topic_name,
                )
            )
            .order_by("-date_created", "-id")[:PEAR_RUN_LIST_LIMIT]
        )
    elif requested_runs:
        topic_runs = []
    else:
        topic_runs = list(
            PearWorkerRun.objects.select_related("bot_profile", "requesting_user", "workspace_node")
            .filter(
                realm=user_profile.realm,
                session_kind=PearWorkerRun.SIDE,
                requesting_user=user_profile,
            )
            .order_by("-date_created", "-id")[:PEAR_RUN_LIST_LIMIT]
        )

    runs_by_id = {run.id: run for run in [*topic_runs, *requested_runs]}
    runs = sorted(runs_by_id.values(), key=lambda run: (run.date_created, run.id), reverse=True)
    topic_context = None
    if bool(include_context) and stream_name is not None and topic_name is not None:
        topic_context = serialize_pear_topic_context(
            topic_context_record
            if topic_context_record is not None
            else pear_topic_context_for_request(
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
                serialize_pear_worker_run(run, include_events=bool(include_events)) for run in runs
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


def update_pear_run_control_event(
    user_profile: UserProfile,
    run_id: int,
    event_type: str,
    summary: str,
) -> PearWorkerRun:
    run = get_visible_pear_worker_run(user_profile, run_id)
    if event_type == "cancel" and run.status in {PearWorkerRun.QUEUED, PearWorkerRun.RUNNING}:
        timestamp = timezone_now()
        run.cancel_requested_at = timestamp
        run.status = PearWorkerRun.CANCELLED
        run.current_activity = "Cancelled from the Pear panel."
        run.summary = "Cancelled."
        run.date_updated = timestamp
        run.date_finished = timestamp
        run.save(
            update_fields=[
                "cancel_requested_at",
                "status",
                "current_activity",
                "summary",
                "date_updated",
                "date_finished",
            ]
        )
    elif event_type in {"pause", "resume"}:
        control_stream = run.source_stream_name if run.session_kind == PearWorkerRun.SIDE else ""
        control_topic = run.source_topic_name if run.session_kind == PearWorkerRun.SIDE else ""
        set_pear_topic_context_paused(
            run.bot_profile,
            stream=control_stream or run.stream_name,
            topic=control_topic or run.topic_name,
            is_paused=event_type == "pause",
        )
        run.current_activity = summary
        run.date_updated = timezone_now()
        run.save(update_fields=["current_activity", "date_updated"])
    PearWorkerRunEvent.objects.create(
        run=run,
        event_type=event_type,
        summary=summary,
        payload={"user_id": user_profile.id},
    )
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


@typed_endpoint
def post_pear_side_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    side_request: Annotated[
        str,
        ApiParamConfig("request", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ],
    stream_name: Annotated[
        str | None,
        ApiParamConfig("stream", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    topic_name: Annotated[
        str | None,
        ApiParamConfig("topic", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
    source_message_id: Annotated[
        Json[int] | None,
        ApiParamConfig("message_id", documentation_status=INTENTIONALLY_UNDOCUMENTED),
    ] = None,
) -> HttpResponse:
    side_request = side_request.strip()
    if not side_request:
        raise JsonableError(_("Pear side request cannot be empty."))
    if stream_name is not None:
        access_stream_by_name(user_profile, stream_name)

    pear_bot = get_selected_pear_bot(user_profile)
    if pear_bot is None:
        raise JsonableError(_("Pear bot is not configured."))

    source_label = (
        f"{stream_name} > {topic_name}"
        if stream_name is not None and topic_name is not None
        else "current Zulip context"
    )
    dm_intro = "\n".join(
        [
            "Pear side session started.",
            "",
            f"Source: {source_label}",
            f"Task: {side_request}",
        ]
    )
    dm_message_id = internal_send_private_message(
        pear_bot.user_profile,
        user_profile,
        dm_intro,
        disable_external_notifications=True,
    )
    if dm_message_id is None:
        raise JsonableError(_("Pear could not open a side DM."))

    side_context_stream = "direct messages"
    side_context_topic = f"side/{dm_message_id}"
    side_context = ensure_pear_child_topic_context(
        pear_bot.user_profile,
        stream=side_context_stream,
        topic=side_context_topic,
        parent_stream=stream_name or "",
        parent_topic=topic_name or "",
    )

    run = PearWorkerRun.objects.create(
        realm=user_profile.realm,
        bot_profile=pear_bot.user_profile,
        requesting_user=user_profile,
        trigger_message_id=dm_message_id,
        stream_name=side_context_stream,
        topic_name=side_context_topic,
        workspace_node=side_context.workspace_node,
        session_kind=PearWorkerRun.SIDE,
        source_stream_name=stream_name or "",
        source_topic_name=topic_name or "",
        source_message_id=source_message_id,
        status=PearWorkerRun.QUEUED,
        summary="Queued side session.",
        current_activity=side_request,
        runtime_label=str(settings.PEAR_WORKER_AGENT_RUNTIME or ""),
        model=pear_worker_model_for_topic(
            pear_bot.user_profile,
            stream=side_context_stream,
            topic=side_context_topic,
        ),
        sandbox=pear_worker_sandbox_for_topic(
            pear_bot.user_profile,
            stream=side_context_stream,
            topic=side_context_topic,
        ),
        approval_policy=str(settings.PEAR_WORKER_APPROVAL_POLICY or ""),
        workdir=pear_worker_workdir_for_topic(
            pear_bot.user_profile,
            stream=side_context_stream,
            topic=side_context_topic,
        ),
    )
    PearWorkerRunEvent.objects.create(
        run=run,
        event_type="queued",
        summary="Queued private Pear side session.",
        payload={
            "source_stream": stream_name,
            "source_topic": topic_name,
            "source_message_id": source_message_id,
        },
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
        "content": side_request,
    }
    PearQueueWorkerDispatcher().dispatch(
        PearEmbeddedBotHandler(),
        SimpleNamespace(user_profile=pear_bot.user_profile),
        worker_message,
        content=side_request,
        active_session=None,
        message_id=dm_message_id,
        stream=side_context_stream,
        topic=side_context_topic,
        requesting_user_id=user_profile.id,
        session_kind=PearWorkerRun.SIDE,
        source_stream=stream_name or "",
        source_topic=topic_name or "",
        source_message_id=source_message_id,
        progress_message_id=dm_message_id,
    )

    return json_success(
        request,
        data={
            "pear_run": serialize_pear_worker_run(run, include_events=True),
            "dm_message_id": dm_message_id,
            "dm_narrow_url": f"/#narrow/dm/{pear_bot.user_profile_id}",
            "side_endpoint": PEAR_SIDE_ENDPOINT,
        },
    )
