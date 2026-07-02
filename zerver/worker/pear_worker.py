# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
from typing import Any

from typing_extensions import override

from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.pear import (
    PearEmbeddedBotHandler,
    PearWorkerRuntime,
    pear_message_id,
    pear_message_stream,
    pear_message_topic,
)
from zerver.lib.pear_agent import PearProcessIsolation
from zerver.lib.pear_worker import (
    PearDatabaseWorkerRunStore,
    build_pear_worker_runtime_from_settings,
    load_pear_active_session_from_zulip,
    pear_topic_process_isolation,
    pear_worker_process_isolation_error,
    pear_worker_model_for_topic,
    pear_worker_reasoning_effort_for_topic,
    pear_worker_sandbox_for_workdir,
    pear_worker_workdir_for_topic,
)
from zerver.models.users import get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue

pear_worker_runtimes: dict[tuple[str, str, str, str, str], PearWorkerRuntime] = {}


def get_pear_worker_runtime(
    model: str,
    workdir: str,
    reasoning_effort: str,
    process_isolation: PearProcessIsolation | None,
    isolation_error: str | None,
) -> PearWorkerRuntime:
    sandbox = pear_worker_sandbox_for_workdir(workdir)
    isolation_label = process_isolation.label() if process_isolation is not None else ""
    runtime_key = (model, workdir, reasoning_effort, sandbox, isolation_label)
    runtime = pear_worker_runtimes.get(runtime_key)
    if runtime is None or isolation_error is not None:
        runtime = build_pear_worker_runtime_from_settings(
            model_override=model,
            workdir_override=workdir,
            reasoning_effort_override=reasoning_effort,
            sandbox_override=sandbox,
            process_isolation_override=process_isolation,
            process_isolation_error=isolation_error,
        )
        if isolation_error is None:
            pear_worker_runtimes[runtime_key] = runtime
    return runtime


@assign_queue("pear_worker")
class PearWorker(QueueProcessingWorker):
    # Codex turns can legitimately take minutes; the embedded_bots queue should
    # only dispatch to this worker, not own the runtime budget.
    MAX_CONSUME_SECONDS = None

    @override
    def consume(self, event: dict[str, Any]) -> None:
        user_profile = get_user_profile_by_id(event["user_profile_id"])
        bot_handler = EmbeddedBotHandler(user_profile)
        message = event["message"]
        content = str(event.get("content", "")).strip()
        message_id = event.get("message_id")
        if not isinstance(message_id, int):
            message_id = pear_message_id(message)
        stream = str(event.get("stream") or pear_message_stream(message))
        topic = str(event.get("topic") or pear_message_topic(message))
        requesting_user_id = event.get("requesting_user_id")
        if not isinstance(requesting_user_id, int):
            requesting_user_id = None
        session_kind = str(event.get("session_kind") or "shared")
        source_stream = str(event.get("source_stream") or "")
        source_topic = str(event.get("source_topic") or "")
        source_message_id = event.get("source_message_id")
        if not isinstance(source_message_id, int):
            source_message_id = None
        progress_message_id = event.get("progress_message_id")
        if not isinstance(progress_message_id, int):
            progress_message_id = None
        context_stream = stream or source_stream
        context_topic = topic or source_topic
        model = pear_worker_model_for_topic(user_profile, stream=context_stream, topic=context_topic)
        reasoning_effort = pear_worker_reasoning_effort_for_topic(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        workdir = pear_worker_workdir_for_topic(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        process_isolation = pear_topic_process_isolation(
            user_profile,
            stream=context_stream,
            topic=context_topic,
        )
        isolation_error = pear_worker_process_isolation_error(
            process_isolation,
            workdir=workdir,
        )
        pear_handler = PearEmbeddedBotHandler(
            worker_runtime=get_pear_worker_runtime(
                model,
                workdir,
                reasoning_effort,
                process_isolation,
                isolation_error,
            ),
            active_session_loader=load_pear_active_session_from_zulip,
            worker_run_store=PearDatabaseWorkerRunStore(),
        )
        active_session = pear_handler.load_active_session(message, bot_handler, content)
        pear_handler.run_worker_turn(
            message,
            bot_handler,
            content=content,
            active_session=active_session,
            message_id=message_id,
            stream=stream,
            topic=topic,
            post_started_progress=True,
            requesting_user_id=requesting_user_id,
            session_kind=session_kind,
            source_stream=source_stream,
            source_topic=source_topic,
            source_message_id=source_message_id,
            progress_message_id=progress_message_id,
        )
