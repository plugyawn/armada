"""The agent's tools for talking in Armada (statement 38): post in a
topic or a thread, with files such as screenshots, read a topic or a
thread, start a run in another topic with a brief, and create a channel.
Each acts for the person whose message started the run, through Zulip's
own checks: what that person could not send, read, start or create, the
agent cannot either.

Codex calls them as dynamic tools, as it calls the page tools
(zerver/lib/armada_page_tools.py): zerver/lib/armada_agent.py offers the
specs to a new thread and answers each call here.
"""

import operator
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import reduce
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.html import escape
from django.utils.translation import gettext as _

from zerver.actions.message_send import check_message, do_send_messages
from zerver.lib.addressee import Addressee
from zerver.lib.armada import (
    ARMADA_AGENT_NAME,
    ArmadaEmbeddedBotHandler,
    ArmadaPersonWait,
    build_armada_ack_message,
    trim_armada_text,
)
from zerver.lib.armada_page_tools import (
    ArmadaToolAnswer,
    answer_tool_call,
    optional_int_argument,
    read_answer,
    string_argument,
    tool_arguments,
)
from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.exceptions import ChannelExistsError, JsonableError, StreamDoesNotExistError
from zerver.lib.message import access_message, messages_for_ids
from zerver.lib.message_cache import MessageDict
from zerver.lib.mime_types import guess_type
from zerver.lib.narrow import (
    LARGER_THAN_MAX_MESSAGE_ID,
    AnchorInfo,
    NarrowParameter,
    fetch_messages,
)
from zerver.lib.streams import access_stream_by_name, check_stream_name_available, list_to_streams
from zerver.lib.upload import (
    check_upload_within_quota,
    check_upload_within_size_limit,
    upload_message_attachment,
)
from zerver.lib.users import bulk_access_users_by_id
from zerver.models import Message, Stream, UserProfile
from zerver.models.bots import AgentRun, AgentRunEvent
from zerver.models.clients import get_client
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.streams import get_stream

if TYPE_CHECKING:
    from zerver.lib.armada_worker import ArmadaDatabaseWorkerRunStore

ARMADA_POST_TOOL: Final = "armada_post"
ARMADA_READ_TOOL: Final = "armada_read"
ARMADA_START_RUN_TOOL: Final = "armada_start_run"
ARMADA_CREATE_CHANNEL_TOOL: Final = "armada_create_channel"

# The run event log holds who started whom: the starting run records
# run_started, the run it started records started_by, with its depth.
ARMADA_RUN_STARTED_EVENT: Final = "run_started"
ARMADA_STARTED_BY_EVENT: Final = "started_by"

ARMADA_POST_FILE_LIMIT: Final = 10
ARMADA_READ_DEFAULT_LIMIT: Final = 20
ARMADA_READ_MAX_LIMIT: Final = 100
# Per message read: a long answer is cut, so twenty of them stay readable.
ARMADA_READ_MESSAGE_CHARS: Final = 3000

WHERE_PROPERTIES: Final[dict[str, Any]] = {
    "channel": {"type": "string", "description": "The channel's name, without #."},
    "topic": {
        "type": "string",
        "description": "The topic's name.",
        "maxLength": MAX_TOPIC_NAME_LENGTH,
    },
    "thread": {"type": "integer", "description": "A message id: its thread."},
}


def armada_message_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": ARMADA_POST_TOOL,
            "description": (
                f"Post a message as yourself ({ARMADA_AGENT_NAME}): in a topic, or as a reply "
                "in a thread. Give thread (a message id: the reply goes in that message's "
                "thread, starting one under it if it has none), or channel and topic (a topic "
                "that does not exist yet is started); with neither, it posts where you were "
                "asked. Use it to give updates where someone asked for them while you keep "
                "working; your final answer still goes where you were asked. Write Markdown; "
                "mention people as @**Full Name**, which notifies them. files attaches files "
                "by path, relative to your working directory or absolute; images, such as "
                "screenshots, show inline. You post with the rights of the person who asked "
                "you. Returns the new message's id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The message, in Markdown.",
                        "maxLength": settings.MAX_MESSAGE_LENGTH,
                    },
                    **WHERE_PROPERTIES,
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths of files to attach.",
                        "maxItems": ARMADA_POST_FILE_LIMIT,
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": ARMADA_READ_TOOL,
            "description": (
                "Read messages, as the person who asked you can: a topic's latest messages, "
                "each thread shown by its first message with its reply count, or one thread "
                "in full. Give thread (a message id) or channel and topic; with neither, where "
                "you were asked. after returns only messages newer than that id: pass the last "
                "id you read to follow a topic or a run you started. Returns the messages "
                "oldest first (id, sender, time, Markdown, replies) and the topic's latest run "
                "with its status."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    **WHERE_PROPERTIES,
                    "after": {"type": "integer", "description": "Only messages after this id."},
                    "limit": {
                        "type": "integer",
                        "description": "How many messages, newest first.",
                        "minimum": 1,
                        "maximum": ARMADA_READ_MAX_LIMIT,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": ARMADA_START_RUN_TOOL,
            "description": (
                f"Start a new {ARMADA_AGENT_NAME} run in a topic, for the person who asked "
                "you: brief is posted there as the topic's request, and a run starts on it in "
                "that topic's own worktree (a topic that does not exist yet gets a new "
                "branch). The run sees that topic and the brief, not this conversation: put "
                "everything it needs in the brief, including where to report (for example: "
                "post progress with armada_post in thread 1234). It does not report back by "
                "itself. Fails if the topic already has a live run. Limits: a run can start "
                f"at most {settings.ARMADA_WORKER_MAX_RUNS_STARTED_PER_RUN} runs, and runs "
                "started this way nest at most "
                f"{settings.ARMADA_WORKER_MAX_RUN_DEPTH} deep (a run that deep starts none). "
                "Returns the brief's message id and a link (#**channel>topic@id**) to give "
                "people; follow the run with armada_read."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": WHERE_PROPERTIES["channel"],
                    "topic": WHERE_PROPERTIES["topic"],
                    "brief": {
                        "type": "string",
                        "description": (
                            "The task and all the context the new run needs, in Markdown."
                        ),
                        "maxLength": settings.MAX_MESSAGE_LENGTH - 500,
                    },
                },
                "required": ["channel", "topic", "brief"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": ARMADA_CREATE_CHANNEL_TOOL,
            "description": (
                "Create a channel for the person who asked you, with their rights: the "
                "organization decides who may create public and private channels. The person "
                "is subscribed, as when they create a channel themselves; subscribers adds "
                "others, by full name as the transcript shows them. A private channel also "
                f"gets you ({ARMADA_AGENT_NAME}), so you can post and work in it. Fails if a "
                "channel of that name exists. Returns the channel's name, id and a link "
                "(#**name**) to give people."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The channel's name, without #.",
                        "maxLength": Stream.MAX_NAME_LENGTH,
                    },
                    "description": {
                        "type": "string",
                        "description": "What the channel is for, in one line.",
                        "maxLength": Stream.MAX_DESCRIPTION_LENGTH,
                    },
                    "private": {
                        "type": "boolean",
                        "description": "Whether only its subscribers can see it; false if not given.",
                    },
                    "subscribers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Full names of the people, or bots, to subscribe besides the person "
                            "who asked."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    ]


@dataclass(frozen=True)
class ArmadaToolTurn:
    """What the runtime knows about the turn a tool call belongs to."""

    # The person whose message started the run.
    requesting_user_id: int | None
    # The message the run answers, which names the run.
    trigger_message_id: int | None
    # Where the run's files are, and the OS user that may read them.
    workdir: str | None
    isolation_username: str | None
    # How a tool waits on the person, as a question does; a side
    # conversation asks nobody, so it has none.
    person_wait: ArmadaPersonWait | None = None


@dataclass(frozen=True)
class ArmadaToolCaller:
    asker: UserProfile
    run: AgentRun
    turn: ArmadaToolTurn


@dataclass(frozen=True)
class ArmadaWhere:
    stream: Stream
    topic: str
    # The thread's root, for a thread.
    root: Message | None


def optional_string_argument(arguments: dict[str, object], name: str) -> str | None:
    if arguments.get(name) is None:
        return None
    return string_argument(arguments, name)


def caller_for_turn(turn: ArmadaToolTurn) -> ArmadaToolCaller:
    asker = (
        UserProfile.objects.select_related("realm")
        .filter(id=turn.requesting_user_id, is_active=True, is_bot=False)
        .first()
        if turn.requesting_user_id is not None
        else None
    )
    if asker is None:
        raise JsonableError(_("No person asked for this turn, so there is nobody to act for."))
    run = (
        AgentRun.objects.select_related("bot_profile")
        .filter(realm_id=asker.realm_id, trigger_message_id=turn.trigger_message_id)
        .order_by("-id")
        .first()
        if turn.trigger_message_id is not None
        else None
    )
    if run is None:
        raise JsonableError(_("This turn is not part of a run."))
    return ArmadaToolCaller(asker=asker, run=run, turn=turn)


def thread_root(asker: UserProfile, message_id: int) -> Message:
    """The root of the thread message_id is in (or would start), as the
    asker can access it."""
    message = access_message(asker, message_id, is_modifying_message=False)
    if message.thread_root_id is not None:
        message = access_message(asker, message.thread_root_id, is_modifying_message=False)
    if not message.is_channel_message:
        raise JsonableError(
            _("{agent} posts and reads only in channels.").format(agent=ARMADA_AGENT_NAME)
        )
    return message


def where_for(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaWhere:
    """Where a call points: a thread, a channel's topic, or where the run
    was asked. Access is checked by the action, as the person."""
    thread = optional_int_argument(arguments, "thread")
    channel = optional_string_argument(arguments, "channel")
    topic = optional_string_argument(arguments, "topic")
    if thread is not None:
        if channel is not None or topic is not None:
            raise JsonableError(_("Give thread, or channel and topic, not both."))
        root = thread_root(caller.asker, thread)
        return ArmadaWhere(
            stream=Stream.objects.get(recipient_id=root.recipient_id),
            topic=root.topic_name(),
            root=root,
        )
    if channel is None and topic is None:
        run = caller.run
        root_id = (
            Message.objects.filter(id=run.trigger_message_id, realm_id=run.realm_id)
            .values_list("thread_root_id", flat=True)
            .first()
        )
        if root_id is not None:
            root = thread_root(caller.asker, root_id)
            return ArmadaWhere(
                stream=Stream.objects.get(recipient_id=root.recipient_id),
                topic=root.topic_name(),
                root=root,
            )
        channel, topic = run.stream_name, run.topic_name
    if channel is None or topic is None:
        raise JsonableError(_("Give both channel and topic."))
    try:
        stream = get_stream(channel.strip().removeprefix("#"), caller.asker.realm)
    except Stream.DoesNotExist:
        raise StreamDoesNotExistError(escape(channel))
    return ArmadaWhere(stream=stream, topic=topic.strip(), root=None)


def where_title(where: ArmadaWhere) -> str:
    return f"#{where.stream.name} > {where.topic}"


def read_run_file(caller: ArmadaToolCaller, raw_path: object) -> tuple[str, bytes]:
    """A file the run names, read as the run would read it: as its
    topic's OS user when the topic has one. Within the realm's upload
    limits."""
    # Lazy import: armada_worker imports the agent runtime, which imports
    # this module.
    from zerver.lib.armada_worker import armada_read_bytes_path

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise JsonableError(_("files must be a list of paths."))
    turn = caller.turn
    if os.path.isabs(raw_path):
        path = Path(raw_path)
    elif turn.workdir:
        path = Path(turn.workdir) / raw_path
    else:
        raise JsonableError(
            _("This run has no working directory; give {path} as an absolute path.").format(
                path=raw_path
            )
        )
    realm = caller.asker.realm
    try:
        data = armada_read_bytes_path(
            path,
            isolation_username=turn.isolation_username,
            max_bytes=realm.get_max_file_upload_size_mebibytes() * 1024 * 1024,
        )
    except OSError:
        raise JsonableError(_("Could not read {path}.").format(path=raw_path))
    if not data:
        raise JsonableError(_("No file, or an empty one, at {path}.").format(path=raw_path))
    check_upload_within_size_limit(realm, len(data))
    check_upload_within_quota(realm, len(data))
    return path.name, data


def upload_link(caller: ArmadaToolCaller, file_name: str, data: bytes) -> str:
    """Upload a file as the agent, the way a person's upload goes, and
    link it: the message that links it claims it, and an image shows
    inline."""
    content_type = guess_type(file_name)[0] or "application/octet-stream"
    url, uploaded_name = upload_message_attachment(
        file_name, content_type, data, caller.run.bot_profile
    )
    label = re.sub(r"([\[\]\\])", r"\\\1", uploaded_name)
    return f"[{label}]({url})"


def check_as_asker(asker: UserProfile, where: ArmadaWhere, content: str) -> None:
    """Zulip's own send check, with the asker as the sender: channel
    access, posting and topic rights, mention rights, the thread's
    destination. What the agent posts for someone, they could post."""
    check_message(
        asker,
        get_client("Internal"),
        Addressee.for_stream(where.stream, where.topic),
        content,
        realm=asker.realm,
        thread_root_message=where.root,
    )


def send_as_agent(agent: UserProfile, where: ArmadaWhere, content: str) -> int:
    """Send as the agent, through the check every message it sends gets."""
    message_request = check_message(
        agent,
        get_client("Internal"),
        Addressee.for_stream(where.stream, where.topic),
        content,
        realm=agent.realm,
        thread_root_message=where.root,
    )
    [sent] = do_send_messages([message_request])
    return sent.message_id


def post_as_agent(
    caller: ArmadaToolCaller, where: ArmadaWhere, content: str, files: list[object]
) -> int:
    """Post as the agent what the asker could post (check_as_asker)."""
    if len(files) > ARMADA_POST_FILE_LIMIT:
        raise JsonableError(_("Attach at most {count} files.").format(count=ARMADA_POST_FILE_LIMIT))
    read_files = [read_run_file(caller, path) for path in files]
    if not content.strip() and not read_files:
        raise JsonableError(_("Give content, or files to attach."))
    # The links are not uploaded yet: the person's check sees the text,
    # or the files' names when there is none.
    check_as_asker(
        caller.asker, where, content.strip() or "\n".join(name for name, _data in read_files)
    )
    links = [upload_link(caller, name, data) for name, data in read_files]
    return send_as_agent(
        caller.run.bot_profile,
        where,
        "\n".join(part for part in [content.strip(), *links] if part),
    )


def post_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    content = string_argument(arguments, "content")
    files = arguments.get("files", [])
    if not isinstance(files, list):
        raise JsonableError(_("files must be a list of paths."))
    where = where_for(caller, arguments)
    message_id = post_as_agent(caller, where, content, files)
    answer: dict[str, object] = {
        "message_id": message_id,
        "channel": where.stream.name,
        "topic": where.topic,
    }
    if where.root is not None:
        answer["thread"] = where.root.id
        title = _("Replied in a thread in {where}.").format(where=where_title(where))
    else:
        title = _("Posted in {where}.").format(where=where_title(where))
    return read_answer(answer, title)


def message_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    """Read as the asker reads in Armada: Zulip's own message fetch
    (GET /messages) with them as the user."""
    after = optional_int_argument(arguments, "after")
    limit = optional_int_argument(arguments, "limit")
    if limit is None:
        limit = ARMADA_READ_DEFAULT_LIMIT
    if not 1 <= limit <= ARMADA_READ_MAX_LIMIT:
        raise JsonableError(
            _("limit must be between 1 and {maximum}.").format(maximum=ARMADA_READ_MAX_LIMIT)
        )
    asker = caller.asker
    where = where_for(caller, arguments)
    if where.root is None:
        # A clear refusal, rather than the empty answer a fetch gives for
        # a channel the asker cannot read.
        access_stream_by_name(asker, where.stream.name)
        narrow = [
            NarrowParameter(operator="channel", operand=where.stream.name),
            NarrowParameter(operator="topic", operand=where.topic),
        ]
    else:
        narrow = [NarrowParameter(operator="thread", operand=where.root.id)]
    if after is None:
        anchor = AnchorInfo(type="message_id", value=LARGER_THAN_MAX_MESSAGE_ID)
        num_before, num_after = limit, 0
    else:
        anchor = AnchorInfo(type="message_id", value=after)
        num_before, num_after = 0, limit
    fetched = fetch_messages(
        narrow=narrow,
        user_profile=asker,
        realm=asker.realm,
        is_web_public_query=False,
        anchor_info=anchor,
        include_anchor=after is None,
        num_before=num_before,
        num_after=num_after,
        exclude_thread_replies=where.root is None,
    )
    message_ids = [row[0] for row in fetched.rows]
    more = not (fetched.found_newest if after is not None else fetched.found_oldest)
    # A thread's first message is its root, which the thread narrow leaves out.
    if (
        where.root is not None
        and (after is None or where.root.id > after)
        and (after is not None or fetched.found_oldest)
    ):
        message_ids.insert(0, where.root.id)
    messages = messages_for_ids(
        message_ids=message_ids,
        user_message_flags={message_id: [] for message_id in message_ids},
        search_fields={},
        apply_markdown=False,
        client_gravatar=True,
        allow_empty_topic_name=True,
        message_edit_history_visibility_policy=asker.realm.message_edit_history_visibility_policy,
        user_profile=asker,
        realm=asker.realm,
    )
    if where.root is None:
        MessageDict.bulk_hydrate_message_thread_summaries(messages, user_profile=asker)
    answer: dict[str, object] = {
        "channel": where.stream.name,
        "topic": where.topic,
        "messages": [
            {
                "id": message["id"],
                "sender": message["sender_full_name"],
                "time": message_time(message["timestamp"]),
                "content": trim_armada_text(message["content"], limit=ARMADA_READ_MESSAGE_CHARS),
                **(
                    {"replies": message["reply_summary"]["reply_count"]}
                    if "reply_summary" in message
                    else {}
                ),
            }
            for message in messages
        ],
        "more": more,
    }
    if where.root is not None:
        answer["thread"] = where.root.id
        title = _("Read a thread in {where}.").format(where=where_title(where))
    else:
        run = (
            AgentRun.objects.filter(
                realm_id=asker.realm_id,
                bot_profile_id=caller.run.bot_profile_id,
                session_kind=AgentRun.SHARED,
                stream_name__iexact=where.stream.name,
                topic_name__iexact=where.topic,
            )
            .order_by("-trigger_message_id", "-id")
            .first()
        )
        answer["run"] = (
            {
                "status": run.status,
                "request": run.trigger_message_id,
                "answer": run.result_message_id,
            }
            if run is not None
            else None
        )
        title = _("Read {where}.").format(where=where_title(where))
    return read_answer(answer, title)


def run_depth(run: AgentRun) -> int:
    """How deep a run is in a chain of runs starting runs: a person's run
    is at 0."""
    payload = (
        AgentRunEvent.objects.filter(run=run, event_type=ARMADA_STARTED_BY_EVENT)
        .values_list("payload", flat=True)
        .first()
    )
    depth = payload.get("depth") if isinstance(payload, dict) else None
    return depth if isinstance(depth, int) else 0


def start_armada_run(
    asker: UserProfile,
    agent: UserProfile,
    where: ArmadaWhere,
    content: str,
    *,
    parent: AgentRun | None = None,
) -> int:
    """Start a run as a person's request to the agent starts one, acting
    for asker: admitted by the same rules (start_blocked_reason), its brief
    posted with asker's rights, and queued for the worker with its progress
    message. A run that a run starts (parent) counts against that run's
    depth and number of runs started. Returns the brief's message id."""
    # Lazy import: armada_worker imports the agent runtime, which imports
    # this module.
    from zerver.lib.armada_worker import ArmadaDatabaseWorkerRunStore, ArmadaQueueWorkerDispatcher

    stream_name, topic_name = where.stream.name, where.topic
    bot_handler = EmbeddedBotHandler(agent)
    store = ArmadaDatabaseWorkerRunStore()
    reason = store.start_blocked_reason(
        bot_handler, None, stream=stream_name, topic=topic_name, requesting_user_id=asker.id
    )
    if reason is not None:
        raise JsonableError(reason)
    # One transaction: a run that does not start leaves no brief behind.
    with transaction.atomic(durable=True):
        depth = 0
        if parent is not None:
            # Locked, so that parallel calls count each other's runs.
            parent = AgentRun.objects.select_for_update(no_key=True).get(id=parent.id)
            depth = run_depth(parent)
            if depth >= settings.ARMADA_WORKER_MAX_RUN_DEPTH:
                raise JsonableError(
                    _(
                        "This run is at depth {depth}, where runs started by runs stop: do the "
                        "work here, or ask the person to start a run."
                    ).format(depth=depth)
                )
            if (
                AgentRunEvent.objects.filter(
                    run=parent, event_type=ARMADA_RUN_STARTED_EVENT
                ).count()
                >= settings.ARMADA_WORKER_MAX_RUNS_STARTED_PER_RUN
            ):
                raise JsonableError(
                    _("This run has started {count} runs, the most one run may start.").format(
                        count=settings.ARMADA_WORKER_MAX_RUNS_STARTED_PER_RUN
                    )
                )
        check_as_asker(asker, where, content.strip())
        message_id = send_as_agent(agent, where, content.strip())
        # The topic's run limit and the realm's, again, against a race.
        if not store.start(bot_handler, message_id, stream=stream_name, topic=topic_name):
            raise JsonableError(_("The run could not start; try again."))
        child = AgentRun.objects.get(
            bot_profile=bot_handler.user_profile, trigger_message_id=message_id
        )
        child.requesting_user = asker
        child.save(update_fields=["requesting_user"])
        if parent is not None:
            _record_started_by(store, bot_handler, parent, child, depth, where, message_id)
    # After the commit, as for a person's request: the run's progress
    # message, then the worker.
    message = MessageDict.wide_dict(access_message(agent, message_id, is_modifying_message=False))
    progress_message_id = store.ensure_progress_message(
        bot_handler,
        message_id,
        message=message,
        stream=stream_name,
        topic=topic_name,
        content=build_armada_ack_message(content),
    )
    ArmadaQueueWorkerDispatcher().dispatch(
        ArmadaEmbeddedBotHandler(worker_run_store=store),
        bot_handler,
        message,
        content=content,
        active_session=None,
        message_id=message_id,
        stream=stream_name,
        topic=topic_name,
        requesting_user_id=asker.id,
        progress_message_id=progress_message_id,
    )
    return message_id


def _record_started_by(
    store: "ArmadaDatabaseWorkerRunStore",
    bot_handler: EmbeddedBotHandler,
    parent: AgentRun,
    child: AgentRun,
    depth: int,
    where: ArmadaWhere,
    message_id: int,
) -> None:
    """The run event log's record of who started whom."""
    stream_name, topic_name = where.stream.name, where.topic
    store.record_event(
        bot_handler,
        message_id,
        ARMADA_STARTED_BY_EVENT,
        summary=_("Started by a run in #{channel} > {topic}.").format(
            channel=parent.stream_name, topic=parent.topic_name
        ),
        payload={
            "run_id": parent.id,
            "depth": depth + 1,
            "stream": parent.stream_name,
            "topic": parent.topic_name,
            "message_id": parent.trigger_message_id,
        },
    )
    store.record_event(
        bot_handler,
        parent.trigger_message_id,
        ARMADA_RUN_STARTED_EVENT,
        summary=_("Started a run in #{channel} > {topic}.").format(
            channel=stream_name, topic=topic_name
        ),
        payload={
            "run_id": child.id,
            "stream": stream_name,
            "topic": topic_name,
            "message_id": message_id,
        },
    )


def start_run_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    """Start a run in another place, for the person who asked for this
    one (start_armada_run)."""
    brief = string_argument(arguments, "brief").strip()
    if not brief:
        raise JsonableError(_("Give the run a brief."))
    where = where_for(
        caller,
        {"channel": string_argument(arguments, "channel"), "topic": arguments.get("topic")},
    )
    # The topic as the message will carry it.
    where = ArmadaWhere(
        stream=where.stream,
        topic=Addressee.for_stream(where.stream, where.topic).topic_name(),
        root=None,
    )
    asker, parent = caller.asker, caller.run
    origin = _("Started for {person} from {link}.").format(
        person=f"@_**{asker.full_name}|{asker.id}**",
        link=f"#**{parent.stream_name}>{parent.topic_name}@{parent.trigger_message_id}**",
    )
    message_id = start_armada_run(
        asker, parent.bot_profile, where, f"{brief}\n\n{origin}", parent=parent
    )
    stream_name, topic_name = where.stream.name, where.topic
    return read_answer(
        {
            "message_id": message_id,
            "channel": stream_name,
            "topic": topic_name,
            "link": f"#**{stream_name}>{topic_name}@{message_id}**",
        },
        _("Started a run in #{channel} > {topic}.").format(channel=stream_name, topic=topic_name),
    )


def named_subscribers(asker: UserProfile, names: object) -> set[UserProfile]:
    """The people (or bots) named, by full name as the transcript shows
    them, whom the asker may subscribe: Zulip's own access rule decides."""
    if not isinstance(names, list):
        raise JsonableError(_("subscribers must be a list of full names."))
    wanted: set[str] = set()
    for name in names:
        # A mention's markup, @**Full Name**, is tolerated around a name.
        clean_name = (
            name.strip().removeprefix("@").strip("*").strip() if isinstance(name, str) else ""
        )
        if not clean_name:
            raise JsonableError(_("subscribers must be a list of full names."))
        wanted.add(clean_name)
    if not wanted:
        return set()
    ids_by_name: dict[str, list[int]] = {}
    for user_id, full_name in UserProfile.objects.filter(
        reduce(operator.or_, (Q(full_name__iexact=name) for name in wanted)),
        realm_id=asker.realm_id,
        is_active=True,
    ).values_list("id", "full_name"):
        ids_by_name.setdefault(full_name.lower(), []).append(user_id)
    user_ids: list[int] = []
    for name in sorted(wanted):
        matches = ids_by_name.get(name.lower(), [])
        if not matches:
            raise JsonableError(_("Nobody here is named {name}.").format(name=name))
        if len(matches) > 1:
            raise JsonableError(
                _("More than one person here is named {name}; ask which one is meant.").format(
                    name=name
                )
            )
        user_ids.extend(matches)
    return bulk_access_users_by_id(
        user_ids, acting_user=asker, allow_deactivated=False, allow_bots=True, for_admin=False
    )


def create_channel_tool(caller: ArmadaToolCaller, arguments: dict[str, object]) -> ArmadaToolAnswer:
    """Create a channel as a person creates one in Armada, by the path
    POST /json/users/me/subscriptions takes (add_subscriptions_backend):
    Zulip's name and creation checks, the organization's default
    permissions with the asker as creator, the asker subscribed, and the
    "channel events" notice. Unlike that path, an existing channel is
    refused rather than joined."""
    # Lazy imports: Zulip's stream actions import armada_worker, which
    # imports the agent runtime, which imports this module.
    from zerver.actions.streams import bulk_add_subscriptions
    from zerver.views.streams import send_user_subscribed_and_new_channel_notifications

    asker = caller.asker
    realm = asker.realm
    name = string_argument(arguments, "name").strip().removeprefix("#").strip()
    description = (optional_string_argument(arguments, "description") or "").strip()
    if len(description) > Stream.MAX_DESCRIPTION_LENGTH:
        raise JsonableError(
            _("description is longer than {limit} characters.").format(
                limit=Stream.MAX_DESCRIPTION_LENGTH
            )
        )
    private = arguments.get("private", False)
    if not isinstance(private, bool):
        raise JsonableError(_("private must be true or false."))
    may_create = (
        asker.can_create_private_streams() if private else asker.can_create_public_streams()
    )
    if asker.is_guest or not may_create:
        raise JsonableError(
            (
                _(
                    "{person} may not create private channels in this organization, so neither "
                    "can you: an administrator can create it, or let them."
                )
                if private
                else _(
                    "{person} may not create channels in this organization, so neither can "
                    "you: an administrator can create it, or let them."
                )
            ).format(person=asker.full_name)
        )
    check_stream_name_available(realm, name)
    subscribers = {asker, *named_subscribers(asker, arguments.get("subscribers", []))}
    if private:
        # The agent reads and runs in a channel as itself: a private one
        # it is not subscribed to is closed to it.
        subscribers.add(caller.run.bot_profile)
    with transaction.atomic(durable=True):
        existing, created = list_to_streams(
            [
                {
                    "name": name,
                    # As a person's description: one line.
                    "description": description.replace("\n", " "),
                    "invite_only": private,
                    "is_web_public": False,
                }
            ],
            asker,
            autocreate=True,
            # Every permission as the organization's defaults set it.
            request_settings_dict=dict.fromkeys(Stream.stream_permission_group_settings),
        )
        if existing:
            # Someone else made it since the check above.
            raise ChannelExistsError(name)
        [stream] = created
        bulk_add_subscriptions(realm, [stream], subscribers, acting_user=asker)
        send_user_subscribed_and_new_channel_notifications(
            user_profile=asker,
            subscribers=set(),
            new_subscriptions={},
            id_to_user_profile={},
            created_streams=[stream],
            announce=False,
        )
    return read_answer(
        {
            "channel_id": stream.id,
            "channel": stream.name,
            "private": stream.invite_only,
            "subscribers": sorted(subscriber.full_name for subscriber in subscribers),
            "link": f"#**{stream.name}**",
        },
        _("Created #{channel}.").format(channel=stream.name),
    )


ARMADA_MESSAGE_TOOLS: Final[
    dict[str, Callable[[ArmadaToolCaller, dict[str, object]], ArmadaToolAnswer]]
] = {
    ARMADA_POST_TOOL: post_tool,
    ARMADA_READ_TOOL: read_tool,
    ARMADA_START_RUN_TOOL: start_run_tool,
    ARMADA_CREATE_CHANNEL_TOOL: create_channel_tool,
}
ARMADA_MESSAGE_TOOL_NAMES: Final = frozenset(ARMADA_MESSAGE_TOOLS)


def answer_armada_message_tool(
    turn: ArmadaToolTurn, tool: str, arguments: object
) -> ArmadaToolAnswer:
    """Answer one of the message tools, for the person who asked for the
    turn."""
    return answer_tool_call(
        lambda: ARMADA_MESSAGE_TOOLS[tool](caller_for_turn(turn), tool_arguments(arguments))
    )
