"""Dev-only engine behind `manage.py pear_demo_data`.

Armada's agent surfaces (live runs, plans, traces, sub-agents, artifacts,
wall monitors, threads) only have something to render after a Codex run,
and the Codex runtime is disabled in development.  This module produces
that state without a model: it posts ordinary messages, then drives the
*real* admission path (`PearEmbeddedBotHandler.handle_message`), the real
run store (`PearDatabaseWorkerRunStore`) and the real turn
(`PearWorkerTurn`) with a scripted tool runner standing in for the Codex
app-server.  Every row, event, cache entry and message edit is therefore
written by product code; the fixture only decides what the "agent" says.

The scenarios themselves live in `pear_demo_content.py`.
"""

import json
import shutil
import struct
import subprocess
import uuid
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils.timezone import now as timezone_now
from typing_extensions import override
from zulip_bots.lib import extract_query_without_mention

from zerver.actions.message_delete import do_delete_messages
from zerver.actions.message_send import do_send_messages, internal_prep_stream_message
from zerver.actions.streams import bulk_add_subscriptions, bulk_remove_subscriptions
from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.message import access_message
from zerver.lib.message_cache import MessageDict
from zerver.lib.pear import (
    PEAR_EXPLICIT_MENTION_MESSAGE_KEY,
    PEAR_TRIGGER_MESSAGE_KEY,
    PearEmbeddedBotHandler,
    PearWorkerRuntime,
    PearWorkerToolResult,
    PearWorkerTranscriptCallback,
    get_pear_live_run_state,
    pear_live_run_cache_key,
    pear_topic_scope,
)
from zerver.lib.pear_agent import build_pear_runtime_value, pear_thread_session_event
from zerver.lib.pear_artifact import artifact_tool_result
from zerver.lib.pear_monitor import pear_create_monitor_from_spec, pear_monitor_apply_tick
from zerver.lib.pear_worker import (
    PearDatabaseWorkerRunStore,
    ensure_pear_workspace_node,
    load_pear_active_session_from_zulip,
    pear_worker_model_for_topic,
    pear_worker_reasoning_effort_for_topic,
)
from zerver.lib.pear_workspace_resources import refresh_workspace_snapshot
from zerver.lib.topic import messages_for_topic
from zerver.models import Message, Realm, Stream, UserMessage, UserProfile
from zerver.models.bots import PearMonitor, PearWorkerRun, PearWorkspaceNode, Service
from zerver.models.streams import get_stream
from zerver.models.users import get_user_by_delivery_email
from zerver.views.pear import update_pear_run_control_event

DEMO_STREAM = "click"
DEMO_TOPIC_PREFIX = "demo-"
# The people who talk in the fixtures, by the short name scenarios use.
DEMO_CAST = {
    "desdemona": "desdemona@zulip.com",
    "iago": "iago@zulip.com",
    "hamlet": "hamlet@zulip.com",
    "othello": "othello@zulip.com",
    "cordelia": "cordelia@zulip.com",
}
# Subscribed to the channel before the fixtures were loaded; never removed.
DEMO_CHANNEL_OWNER = "desdemona"
# A lease that the stale-run reaper (heartbeat older than 60s => failed)
# can never consider expired.  See `DemoSeeder.request`.
DEMO_LEASE_UNTIL = datetime(2099, 1, 1, tzinfo=timezone.utc)
DEMO_LIVE_STATE_TTL_SECONDS = 28 * 24 * 3600

Plan = Sequence[tuple[str, str]]
Script = Callable[["ScriptedTurn"], str]


class FreezeRunError(BaseException):
    """Abandon a scripted turn mid-flight.

    Deliberately not an `Exception`: `PearWorkerTurn.run` settles a turn as
    failed on any `Exception`, and a frozen turn must stay exactly as a live
    one looks from outside: RUNNING, with its last events and partial text.
    """


class DemoRunStore(PearDatabaseWorkerRunStore):
    """The real run store, able to freeze a turn that waits on a human."""

    freeze_on_steering_poll = False

    @override
    def steering_events_after(
        self, bot_handler: Any, message_id: int, *, after_event_id: int
    ) -> Any:
        if self.freeze_on_steering_poll:
            raise FreezeRunError
        return super().steering_events_after(bot_handler, message_id, after_event_id=after_event_id)


class CapturingDispatcher:
    """Stands in for the RabbitMQ dispatcher: keeps the turn in-process."""

    def __init__(self) -> None:
        self.dispatched: dict[str, Any] | None = None

    def warm(self, bot_handler: Any, message: dict[str, Any], *, stream: str, topic: str) -> None:
        return

    def dispatch(
        self,
        pear_handler: Any,
        bot_handler: Any,
        message: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.dispatched = {"message": {**message, "content": kwargs["content"]}, **kwargs}


@dataclass
class DemoAgent:
    thread_id: str
    nickname: str
    role: str
    turn_id: str


class ScriptedTurn:
    """What a scenario script holds: the vocabulary of one Codex turn.

    Each method emits the transcript events the Codex app-server translator
    (`zerver.lib.pear_agent`) would emit for the same thing, through the
    turn's real event callback, and advances the fixture clock.
    """

    def __init__(
        self,
        seeder: "DemoSeeder",
        runner: "ScriptedRunner",
        emit: PearWorkerTranscriptCallback,
    ) -> None:
        self.seeder = seeder
        self.runner = runner
        self.emit = emit
        self.root = runner.root_thread_id
        self.turn_id = seeder.new_id(self.root, f"turn:{runner.ordinal}")
        self.agents: dict[str, DemoAgent] = {}
        self.artifacts: list[PearWorkerToolResult] = []
        self.items = 0
        self.usage_totals = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}

    # --- plumbing -----------------------------------------------------

    def wait(self, seconds: float) -> None:
        self.seeder.shift(seconds)

    def _item_id(self) -> str:
        self.items += 1
        return f"item_{self.items}"

    def _metadata(self, method: str, agent: DemoAgent | None, **extra: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            **extra,
            "codex_method": method,
            "codex_thread_id": self.root if agent is None else agent.thread_id,
            "codex_turn_id": self.turn_id if agent is None else agent.turn_id,
        }
        if agent is not None:
            metadata["parent_thread_id"] = self.root
            metadata["agent_nickname"] = agent.nickname
            metadata["agent_role"] = agent.role
        return metadata

    def _item(
        self,
        kind: str,
        item_type: str,
        title: str,
        *,
        seconds: float,
        agent: DemoAgent | None,
        failed: bool = False,
        finish: bool = True,
        **item_metadata: Any,
    ) -> None:
        item_id = self._item_id()
        base = {"id": item_id, "type": item_type, **item_metadata}
        self.emit(
            {
                "kind": kind,
                "status": "running",
                "title": title,
                "body": "",
                "item_id": item_id,
                "metadata": self._metadata("item/started", agent, **base, status="inProgress"),
            }
        )
        if not finish:
            return
        self.wait(seconds)
        self.emit(
            {
                "kind": kind,
                "status": "failed" if failed else "done",
                "title": title,
                "body": "",
                "item_id": item_id,
                "metadata": self._metadata(
                    "item/completed", agent, **base, status="failed" if failed else "completed"
                ),
            }
        )

    # --- the root turn ------------------------------------------------

    def begin(self) -> None:
        self.emit(
            pear_thread_session_event(
                {
                    "id": self.root,
                    "parent_thread_id": None,
                    "agent_nickname": None,
                    "agent_role": None,
                    "name": None,
                    "created_at": timezone_now().timestamp(),
                    "status": {"type": "active"},
                }
            )
        )
        self.emit(
            {
                "kind": "status",
                "status": "running",
                "title": "Codex turn started.",
                "metadata": self._metadata("turn/started", None),
            }
        )

    def end(self) -> None:
        self.emit(
            {
                "kind": "status",
                "status": "done",
                "title": "Codex turn completed.",
                "metadata": self._metadata("turn/completed", None, turn_status="completed"),
            }
        )

    def plan(self, steps: Plan, *, agent: DemoAgent | None = None) -> None:
        self.emit(
            {
                "kind": "plan",
                "status": "done",
                "title": "Plan",
                "metadata": self._metadata(
                    "turn/plan/updated",
                    agent,
                    steps=[{"step": step, "status": status} for step, status in steps],
                ),
            }
        )
        self.wait(1)

    def command(
        self,
        command: str,
        *,
        seconds: float = 2.0,
        failed: bool = False,
        finish: bool = True,
        agent: DemoAgent | None = None,
    ) -> None:
        self._item(
            "command",
            "commandExecution",
            command,
            seconds=seconds,
            agent=agent,
            failed=failed,
            finish=finish,
            command=command,
        )

    def file_change(
        self, path: str, *, seconds: float = 1.0, agent: DemoAgent | None = None
    ) -> None:
        self._item("file_change", "fileChange", path, seconds=seconds, agent=agent, path=path)

    def tool(
        self,
        name: str,
        *,
        seconds: float = 1.5,
        failed: bool = False,
        agent: DemoAgent | None = None,
    ) -> None:
        self._item(
            "tool", "mcpToolCall", name, seconds=seconds, agent=agent, failed=failed, name=name
        )

    def say(self, text: str, *, agent: DemoAgent | None = None) -> None:
        """A completed assistant message (a commentary paragraph or the answer)."""
        self.emit(
            {
                "kind": "assistant",
                "status": "done",
                "title": "Codex",
                "body": text,
                "item_id": self._item_id(),
                "metadata": self._metadata("item/completed", agent, type="agentMessage"),
            }
        )
        self.wait(2)

    def stream(self, text: str, *, chunk_size: int = 48) -> None:
        """Assistant text arriving as deltas; leaves it mid-message."""
        item_id = self._item_id()
        for start in range(0, len(text), chunk_size):
            self.emit(
                {
                    "kind": "assistant",
                    "status": "running",
                    "title": "Codex",
                    "body": text[start : start + chunk_size],
                    "item_id": item_id,
                    "metadata": self._metadata(
                        "item/agentMessage/delta", None, streaming=True, delta=True
                    ),
                }
            )
        # The turn rate-limits its own flushes to one per 200ms of wall
        # time; the script runs faster than that, so ask for the flush the
        # throttle would have produced.
        self.runner.turn.update_live_transcript("running", force=True)
        self.wait(3)

    def usage(self, *, input: int, cached: int, output: int, reasoning: int) -> None:
        self.emit(
            {
                "kind": "usage",
                "status": "done",
                "title": "Token usage updated.",
                "metadata": self._metadata(
                    "thread/tokenUsage/updated",
                    None,
                    usage={
                        "input_tokens": input,
                        "cached_input_tokens": cached,
                        "output_tokens": output,
                        "reasoning_output_tokens": reasoning,
                        "total_tokens": input + output,
                        "cache_percent": round(cached * 100 / input, 1),
                    },
                ),
            }
        )

    def overview(self, text: str, *, step: str) -> None:
        """The presentation worker's one-line drill-down under the current step."""
        self.runner.store.record_event(
            self.runner.bot_handler,
            self.runner.trigger_message_id,
            "presentation",
            summary=text,
            payload={"overview": text, "source_event_id": 0, "current_step": step},
        )

    def artifact(self, document: dict[str, Any]) -> None:
        """Hand an artifact to the turn as the `pear_artifact` tool would."""
        self.tool("pear_artifact")
        self.artifacts.append(self.seeder.artifact_result(document))

    # --- native sub-agents ---------------------------------------------

    def spawn(self, nickname: str, role: str, task: str) -> DemoAgent:
        thread_id = self.seeder.new_id(self.root, f"agent:{self.runner.ordinal}:{nickname}")
        agent = DemoAgent(thread_id, nickname, role, self.seeder.new_id(thread_id, "turn"))
        self.agents[nickname] = agent
        self._item(
            "agent",
            "collabAgentToolCall",
            "Started an agent",
            seconds=1,
            agent=None,
            prompt=task,
            receiver_thread_ids=[thread_id],
        )
        self.emit(
            pear_thread_session_event(
                {
                    "id": thread_id,
                    "parent_thread_id": self.root,
                    "agent_nickname": nickname,
                    "agent_role": role,
                    "name": task,
                    "created_at": timezone_now().timestamp(),
                    "status": {"type": "active"},
                    "thread_source": "subagent",
                }
            )
        )
        self.emit(
            {
                "kind": "status",
                "status": "running",
                "title": "Codex turn started.",
                "metadata": self._metadata("turn/started", agent),
            }
        )
        return agent

    def finish_agent(self, agent: DemoAgent, *, failed: bool = False, error: str = "") -> None:
        if failed:
            self.emit(
                {
                    "kind": "error",
                    "status": "failed",
                    "title": "Codex runtime error.",
                    "body": error,
                    "metadata": self._metadata("error", agent),
                }
            )
        self.emit(
            {
                "kind": "status",
                "status": "done",
                "title": "Codex turn completed.",
                "metadata": self._metadata(
                    "turn/completed", agent, turn_status="failed" if failed else "completed"
                ),
            }
        )
        if not failed:
            self.emit(
                {
                    "kind": "agent",
                    "status": "done",
                    "title": "Agent idle",
                    "metadata": self._metadata(
                        "thread/status/changed", agent, thread_status="idle"
                    ),
                }
            )

    def wait_for_agents(self) -> None:
        self._item(
            "agent",
            "collabAgentToolCall",
            "Waited for agents",
            seconds=1,
            agent=None,
            receiver_thread_ids=[agent.thread_id for agent in self.agents.values()],
        )

    # --- people and controls --------------------------------------------

    def steer(self, sender: str, text: str) -> None:
        """Someone addresses Pear mid-run; admission records it as steering."""
        self.seeder.request(sender, self.runner.topic, text)
        self.wait(2)

    def cancel(self, by: str) -> None:
        """The panel's cancel control, pressed while the turn is working."""
        run = self.runner.run()
        update_pear_run_control_event(self.seeder.user(by), run.id, "cancel", "Cancelled by user.")

    def ask(self, questions: list[dict[str, Any]]) -> None:
        """The engine's request_user_input; the run then waits on a human."""
        self.runner.store.freeze_on_steering_poll = True
        self.runner.turn.request_user_answers(questions)

    def freeze(self) -> None:
        raise FreezeRunError

    def fail(self, error: str) -> None:
        raise RuntimeError(error)


class ScriptedRunner:
    """A `PearWorkerToolRunner` that replays a script instead of calling Codex."""

    def __init__(
        self,
        seeder: "DemoSeeder",
        script: Script,
        *,
        store: DemoRunStore,
        bot_handler: EmbeddedBotHandler,
        trigger_message_id: int,
        topic: str,
        root_thread_id: str,
        ordinal: int,
    ) -> None:
        self.seeder = seeder
        self.ordinal = ordinal
        self.script = script
        self.store = store
        self.bot_handler = bot_handler
        self.trigger_message_id = trigger_message_id
        self.topic = topic
        self.root_thread_id = root_thread_id
        self.turn: Any = None

    def run(self) -> PearWorkerRun:
        return PearWorkerRun.objects.get(
            bot_profile=self.seeder.bot, trigger_message_id=self.trigger_message_id
        )

    def __call__(
        self,
        active_session: Any,
        task: str,
        *,
        event_callback: PearWorkerTranscriptCallback | None = None,
        user_input_provider: Any = None,
    ) -> Sequence[PearWorkerToolResult]:
        assert event_callback is not None
        # The callback is `PearWorkerTurn.handle_transcript_event`; the turn
        # is what a script needs for the two things only it can do (flush
        # the live message, ask the humans a question).
        self.turn = event_callback.__self__  # type: ignore[attr-defined] # bound method
        scripted = ScriptedTurn(self.seeder, self, event_callback)
        scripted.begin()
        result = self.script(scripted)
        scripted.end()
        return [{"label": "Result", "value": result}, *scripted.artifacts]

    def run_side(
        self,
        active_session: Any,
        task: str,
        *,
        conversation_key: str | None = None,
        event_callback: PearWorkerTranscriptCallback | None = None,
        user_input_provider: Any = None,
    ) -> Sequence[PearWorkerToolResult]:
        return self(active_session, task, event_callback=event_callback)

    def steer(self, instruction: str) -> bool:
        return True

    def interrupt(self) -> bool:
        return True

    def codex_thread_id(self) -> str:
        return self.root_thread_id

    def runtime_value(self) -> str:
        run = self.run()
        return build_pear_runtime_value(
            "codex-app-server",
            workdir=run.workdir,
            sandbox="workspace-write",
            approval_policy=run.approval_policy,
            model=pear_worker_model_for_topic(
                self.seeder.bot, stream=DEMO_STREAM, topic=self.topic
            ),
            service_tier="fast",
            reasoning_effort=pear_worker_reasoning_effort_for_topic(
                self.seeder.bot, stream=DEMO_STREAM, topic=self.topic
            ),
        )


class DemoSeeder:
    def __init__(self, realm: Realm, traveller: Any) -> None:
        self.realm = realm
        self.traveller = traveller
        self.real_now = timezone_now()
        self.stream: Stream = get_stream(DEMO_STREAM, realm)
        service = (
            Service.objects.select_related("user_profile")
            .filter(name="pear", user_profile__realm=realm, user_profile__is_active=True)
            .order_by("user_profile_id")
            .first()
        )
        if service is None:
            raise RuntimeError("The Pear bot does not exist in this realm.")
        self.bot: UserProfile = service.user_profile
        self.users = {
            name: get_user_by_delivery_email(email, realm) for name, email in DEMO_CAST.items()
        }
        self.message_ids: list[int] = []
        self.runs_by_topic: dict[str, int] = {}

    # --- identity, time ---------------------------------------------------

    def user(self, name: str) -> UserProfile:
        return self.users[name]

    def new_id(self, *parts: str) -> str:
        """A stable Codex-style id, so agent sigils survive reloading."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "/".join(["pear-demo", *parts])))

    def _next_ordinal(self, topic: str) -> int:
        self.runs_by_topic[topic] = self.runs_by_topic.get(topic, 0) + 1
        return self.runs_by_topic[topic]

    def minutes_ago(self, minutes: float) -> None:
        self.traveller.move_to(self.real_now - timedelta(minutes=minutes))

    def shift(self, seconds: float) -> None:
        self.traveller.shift(timedelta(seconds=seconds))

    # --- channel ------------------------------------------------------------

    def subscribe_cast(self) -> None:
        bulk_add_subscriptions(
            self.realm, [self.stream], list(self.users.values()), acting_user=None
        )

    def workspace(self, topic: str) -> PearWorkspaceNode:
        """The topic's worktree, through the same call a first run makes."""
        return ensure_pear_workspace_node(self.bot, stream=DEMO_STREAM, topic=topic)

    # --- messages -------------------------------------------------------------

    def post(
        self,
        sender: str,
        topic: str,
        content: str,
        *,
        thread_root: Message | None = None,
        wait: float = 40,
    ) -> Message:
        """An ordinary message, sent through the ordinary send path.

        The one difference from a browser send: the service-bot fan-out is
        dropped, so the dev server's own Pear workers never see fixture
        requests (they would answer that the runtime is disabled).
        """
        request = internal_prep_stream_message(
            self.user(sender), self.stream, topic, content, thread_root_message=thread_root
        )
        assert request is not None
        request.service_bot_tuples = []
        do_send_messages([request])
        self.message_ids.append(request.message.id)
        self.shift(wait)
        return request.message

    def result_message(self, run: PearWorkerRun) -> Message:
        """The ordinary message that carries a run's answer."""
        assert run.result_message_id is not None
        return access_message(self.bot, run.result_message_id, is_modifying_message=False)

    def request(
        self,
        sender: str,
        topic: str,
        content: str,
        script: Script | None = None,
        *,
        thread_root: Message | None = None,
    ) -> PearWorkerRun | None:
        """`@Pear <content>` from a person, then whatever Pear does with it.

        Admission runs for real, so the outcome is the product's: a queued
        run and its "Working" message, steering for a run already live in
        the topic, or a refusal (paused topic).  With a `script` the queued
        run is then claimed and its turn executed in-process.
        """
        message = self.post(sender, topic, f"@**Pear** {content}", thread_root=thread_root, wait=1)
        bot_handler = EmbeddedBotHandler(self.bot)
        service_message = MessageDict.wide_dict(message, self.realm.id)
        service_message[PEAR_TRIGGER_MESSAGE_KEY] = "mention"
        service_message["content"] = extract_query_without_mention(service_message, bot_handler)
        service_message[PEAR_EXPLICIT_MENTION_MESSAGE_KEY] = True
        dispatcher = CapturingDispatcher()
        PearEmbeddedBotHandler(
            active_session_loader=load_pear_active_session_from_zulip,
            worker_run_store=PearDatabaseWorkerRunStore(),
            worker_dispatcher=dispatcher,
        ).handle_message(service_message, bot_handler)
        self._remember_bot_messages(topic)
        delivery = dispatcher.dispatched
        if delivery is None:
            return None
        run = PearWorkerRun.objects.get(bot_profile=self.bot, trigger_message_id=message.id)
        if script is None:
            return run

        self.shift(1)
        store = DemoRunStore()
        # One commit for the claim and the fixture lease: the dev server's
        # liveness thread must never see this run RUNNING with a heartbeat
        # on the fixture clock, which is in the past and so always "stale".
        with transaction.atomic(durable=True):
            if not store.claim_for_run(bot_handler, message.id, stream=DEMO_STREAM, topic=topic):
                raise RuntimeError(f"Could not claim the fixture run in {topic}.")
            PearWorkerRun.objects.filter(id=run.id).update(heartbeat_at=DEMO_LEASE_UNTIL)
        runner = ScriptedRunner(
            self,
            script,
            store=store,
            bot_handler=bot_handler,
            trigger_message_id=message.id,
            topic=topic,
            root_thread_id=self.new_id(topic, "thread"),
            ordinal=self._next_ordinal(topic),
        )
        handler = PearEmbeddedBotHandler(
            worker_runtime=PearWorkerRuntime(runner),
            active_session_loader=load_pear_active_session_from_zulip,
            worker_run_store=store,
        )
        worker_message = delivery.pop("message")
        try:
            handler.run_worker_turn(
                worker_message,
                bot_handler,
                active_session=handler.load_active_session(
                    worker_message, bot_handler, delivery["content"]
                ),
                post_started_progress=True,
                **{key: value for key, value in delivery.items() if key != "active_session"},
            )
        except FreezeRunError:
            frozen = True
        else:
            frozen = False
        self._remember_bot_messages(topic)
        self.shift(30)
        run.refresh_from_db()
        if frozen != (run.status == PearWorkerRun.RUNNING):
            raise RuntimeError(f"The fixture run in {topic} ended as {run.status}: {run.error}")
        if frozen:
            self.keep_live_state(run)
        else:
            PearWorkerRun.objects.filter(id=run.id).update(heartbeat_at=F("date_updated"))
        return run

    def _remember_bot_messages(self, topic: str) -> None:
        assert self.stream.recipient_id is not None
        self.message_ids.extend(
            messages_for_topic(self.realm.id, self.stream.recipient_id, topic)
            .filter(sender=self.bot)
            .values_list("id", flat=True)
        )

    def keep_live_state(self, run: PearWorkerRun) -> None:
        """Keep a frozen run's volatile state for as long as its lease.

        A frozen run stays RUNNING because of the lease taken in `request`:
        the pear_worker liveness thread (`reap_stale_pear_worker_runs`)
        fails RUNNING runs whose `heartbeat_at` is older than 60 seconds,
        and real writers only move a heartbeat forward once it is 15
        seconds old, so one dated 2099 is never touched and never stale.

        The other half of "live" is the volatile state (current operation,
        "has visible text"): a cache entry with a one-hour TTL.  Rewrite it
        with a long one.
        """
        state = get_pear_live_run_state(run.trigger_message_id)
        if state is not None:
            cache.set(
                pear_live_run_cache_key(run.trigger_message_id),
                state,
                timeout=DEMO_LIVE_STATE_TTL_SECONDS,
            )

    def mark_everything_read(self) -> None:
        UserMessage.objects.filter(message_id__in=self.message_ids).update(
            flags=F("flags").bitor(UserMessage.flags.read)
        )

    # --- artifacts ----------------------------------------------------------------

    def artifact_result(self, document: dict[str, Any]) -> PearWorkerToolResult:
        return {"label": "Artifact", "value": artifact_tool_result(json.dumps(document))["value"]}

    def write_media(self, topic: str, relative_path: str, data: bytes) -> None:
        """Generated media, where an agent would have written it."""
        path = Path(self.workspace(topic).workdir) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    # --- worktrees ------------------------------------------------------------------

    def git(self, node: PearWorkspaceNode, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", node.workdir, *args], check=True, capture_output=True, text=True
        ).stdout

    def refresh_workspace(self, node: PearWorkspaceNode) -> None:
        refresh_workspace_snapshot(node)

    # --- wall monitors -----------------------------------------------------------------

    def monitor(
        self,
        topic: str,
        ask: str,
        spec: dict[str, Any],
        *,
        status: str = PearMonitor.LIVE,
        last_error: str = "",
    ) -> PearMonitor:
        """A wall monitor, created the way pinning an artifact creates one."""
        stream_name, topic_name = pear_topic_scope(DEMO_STREAM, topic)
        monitor = pear_create_monitor_from_spec(
            realm=self.realm,
            bot_profile=self.bot,
            creator=self.user(DEMO_CHANNEL_OWNER),
            stream_name=stream_name,
            topic_name=topic_name,
            ask=ask,
            raw_spec=spec,
            position=PearMonitor.objects.filter(
                realm=self.realm, stream_name=stream_name, topic_name=topic_name
            ).count(),
        )
        # One sample, as the scheduler's first tick would take it.  The
        # recipe is static, so later real ticks re-publish the same data
        # and keep `last_ok_at` fresh without running any command.
        pear_monitor_apply_tick(
            monitor, workdir=self.workspace(topic).workdir, process_isolation=None
        )
        if status == PearMonitor.PAUSED:
            monitor.status = PearMonitor.PAUSED
            monitor.next_due_at = None
        elif status == PearMonitor.ERROR:
            monitor.status = PearMonitor.ERROR
            monitor.next_due_at = None
            monitor.consecutive_failures = 5
            monitor.last_error = last_error
        monitor.save()
        return monitor

    def provisioning_monitor(self, topic: str, ask: str) -> PearMonitor:
        """A monitor whose setup agent has not written its spec yet."""
        stream_name, topic_name = pear_topic_scope(DEMO_STREAM, topic)
        return PearMonitor.objects.create(
            realm=self.realm,
            bot_profile=self.bot,
            creator=self.user(DEMO_CHANNEL_OWNER),
            stream_name=stream_name,
            topic_name=topic_name,
            title=ask[:24],
            ask=ask,
            position=PearMonitor.objects.filter(
                realm=self.realm, stream_name=stream_name, topic_name=topic_name
            ).count(),
            # The monitor scheduler turns a setup older than 30 minutes into
            # an error; a setup "started" far in the future never is.
            date_created=DEMO_LEASE_UNTIL,
        )


# --- generated media ---------------------------------------------------------------


def png(width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]]) -> bytes:
    """A tiny dependency-free PNG encoder for offline artifact media."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    rows = b"".join(
        b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


# --- removal ---------------------------------------------------------------------------


def demo_topic_names(realm: Realm) -> list[str]:
    stream = get_stream(DEMO_STREAM, realm)
    names = set(
        Message.objects.filter(
            realm=realm,
            recipient=stream.recipient,
            is_channel_message=True,
            subject__istartswith=DEMO_TOPIC_PREFIX,
        ).values_list("subject", flat=True)
    )
    names.update(
        PearWorkspaceNode.objects.filter(
            realm=realm,
            stream_name__iexact=DEMO_STREAM,
            topic_name__istartswith=DEMO_TOPIC_PREFIX,
        ).values_list("topic_name", flat=True)
    )
    return sorted(names)


def remove_demo_data(realm: Realm, *, remove_workspaces: bool) -> dict[str, int]:
    """Delete everything a previous load created.

    Fixture state is found by name (channel `click`, topics starting with
    `demo-`), never by remembered ids, so this also cleans up after a load
    that crashed halfway.
    """
    stream = get_stream(DEMO_STREAM, realm)
    runs = PearWorkerRun.objects.filter(
        realm=realm,
        stream_name__iexact=DEMO_STREAM,
        topic_name__istartswith=DEMO_TOPIC_PREFIX,
    )
    for trigger_message_id in runs.values_list("trigger_message_id", flat=True):
        cache.delete(pear_live_run_cache_key(trigger_message_id))
    counts = {"runs": runs.count()}
    runs.delete()
    monitors = PearMonitor.objects.filter(
        realm=realm,
        stream_name__iexact=DEMO_STREAM,
        topic_name__istartswith=DEMO_TOPIC_PREFIX,
    )
    counts["monitors"] = monitors.count()
    monitors.delete()

    messages = list(
        Message.objects.filter(
            realm=realm,
            recipient=stream.recipient,
            is_channel_message=True,
            subject__istartswith=DEMO_TOPIC_PREFIX,
        )
    )
    counts["messages"] = len(messages)
    if messages:
        do_delete_messages(realm, messages, acting_user=None)

    nodes = PearWorkspaceNode.objects.filter(
        realm=realm,
        stream_name__iexact=DEMO_STREAM,
        topic_name__istartswith=DEMO_TOPIC_PREFIX,
    ).select_related("project")
    counts["workspaces"] = nodes.count()
    for node in nodes:
        workdir = Path(node.workdir)
        if remove_workspaces:
            if workdir.is_dir():
                shutil.rmtree(workdir)
            if node.project is not None and node.project.repo_cache_path and node.branch_name:
                subprocess.run(
                    [
                        "git",
                        "--git-dir",
                        node.project.repo_cache_path,
                        "update-ref",
                        "-d",
                        f"refs/heads/{node.branch_name}",
                    ],
                    check=False,
                    capture_output=True,
                )
            node.delete()
        elif workdir.is_dir():
            # Keep the clone (re-cloning is the slow part of a load) but
            # return it to the pristine topic branch.
            base = node.base_commit or "HEAD"
            subprocess.run(
                ["git", "-C", str(workdir), "reset", "--quiet", "--hard", base],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(workdir), "clean", "--quiet", "-fdx"],
                check=True,
                capture_output=True,
            )
            PearWorkspaceNode.objects.filter(id=node.id).update(is_paused=False)
            refresh_workspace_snapshot(node)

    if remove_workspaces:
        cast = [
            get_user_by_delivery_email(email, realm)
            for name, email in DEMO_CAST.items()
            if name != DEMO_CHANNEL_OWNER
        ]
        bulk_remove_subscriptions(realm, cast, [stream], acting_user=None)
    return counts


# --- motion ------------------------------------------------------------------------------

ANIMATION_COMMANDS = [
    "pytest tests/test_formatting.py -q",
    "rg -n 'col_max' src/click/formatting.py",
    "pytest tests/test_options.py -k help -q",
    "ruff check src/click/formatting.py",
    "git diff --stat",
]


def animate_live_run(realm: Realm, topic: str, *, interval_seconds: float = 4.0) -> None:
    """Emit real run events on the frozen live run until interrupted.

    A loaded live run is correct but still; this makes it move (tool rows
    appear and finish, the activity line changes, the typing indicator
    stays up) for work on transitions.  Events go through the run store,
    so they are stored and pushed exactly like a worker's.
    """
    import time

    from zerver.lib.pear import send_pear_worker_typing_notification

    run = (
        PearWorkerRun.objects.filter(
            realm=realm,
            stream_name__iexact=DEMO_STREAM,
            topic_name__iexact=topic,
            status=PearWorkerRun.RUNNING,
        )
        .order_by("-id")
        .first()
    )
    if run is None:
        raise RuntimeError(f"No live fixture run in {topic}; load the fixtures first.")
    store = PearDatabaseWorkerRunStore()
    bot_handler = EmbeddedBotHandler(run.bot_profile)
    step = 0
    while True:
        command = ANIMATION_COMMANDS[step % len(ANIMATION_COMMANDS)]
        item_id = f"anim_{int(time.time())}_{step}"
        for status in ("running", "done"):
            store.record_event(
                bot_handler,
                run.trigger_message_id,
                "transcript",
                summary=command,
                payload={
                    "kind": "command",
                    "status": status,
                    "title": command,
                    "body": "",
                    "item_id": item_id,
                    "metadata": {
                        "id": item_id,
                        "type": "commandExecution",
                        "command": command,
                        "codex_thread_id": run.codex_thread_id,
                    },
                },
            )
            store.update_metadata(bot_handler, run.trigger_message_id, current_activity=command)
            send_pear_worker_typing_notification(
                bot_handler, stream=run.stream_name, topic=run.topic_name, operator="start"
            )
            time.sleep(interval_seconds / 2)
        step += 1
