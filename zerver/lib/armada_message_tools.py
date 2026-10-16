"""The agent's tools for talking in Armada (statement 38): post in a
topic or a thread, with files such as screenshots, and read a topic or
a thread. Each acts for the person whose message started the run, through
Zulip's own checks: what that person could not send or read, the agent
cannot send or read for them.

Codex calls them as dynamic tools, as it calls the page tools
(zerver/lib/armada_page_tools.py): zerver/lib/armada_agent.py offers the
specs to a new thread and answers each call here.
"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from django.conf import settings
from django.utils.html import escape
from django.utils.translation import gettext as _

from zerver.actions.message_send import check_message, do_send_messages
from zerver.lib.addressee import Addressee
from zerver.lib.armada import ARMADA_AGENT_NAME, trim_armada_text
from zerver.lib.armada_page_tools import (
    ArmadaToolAnswer,
    answer_tool_call,
    optional_int_argument,
    read_answer,
    string_argument,
    tool_arguments,
)
from zerver.lib.exceptions import JsonableError, StreamDoesNotExistError
from zerver.lib.message import access_message, messages_for_ids
from zerver.lib.message_cache import MessageDict
from zerver.lib.mime_types import guess_type
from zerver.lib.narrow import (
    LARGER_THAN_MAX_MESSAGE_ID,
    AnchorInfo,
    NarrowParameter,
    fetch_messages,
)
from zerver.lib.streams import access_stream_by_name
from zerver.lib.upload import (
    check_upload_within_quota,
    check_upload_within_size_limit,
    upload_message_attachment,
)
from zerver.models import Message, Stream, UserProfile
from zerver.models.bots import AgentRun
from zerver.models.clients import get_client
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.streams import get_stream

ARMADA_POST_TOOL: Final = "armada_post"
ARMADA_READ_TOOL: Final = "armada_read"

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


def post_as_agent(
    caller: ArmadaToolCaller, where: ArmadaWhere, content: str, files: list[object]
) -> int:
    """Post as the agent what the asker could post: Zulip's own send check
    runs with the asker as the sender (channel access, posting and topic
    rights, mention rights, the thread's destination), and then again for
    the agent, as for every message it sends."""
    if len(files) > ARMADA_POST_FILE_LIMIT:
        raise JsonableError(_("Attach at most {count} files.").format(count=ARMADA_POST_FILE_LIMIT))
    read_files = [read_run_file(caller, path) for path in files]
    if not content.strip() and not read_files:
        raise JsonableError(_("Give content, or files to attach."))
    client = get_client("Internal")
    addressee = Addressee.for_stream(where.stream, where.topic)
    realm = caller.asker.realm
    # The links are not uploaded yet: the person's check sees the text,
    # or the files' names when there is none.
    check_message(
        caller.asker,
        client,
        addressee,
        content.strip() or "\n".join(name for name, _data in read_files),
        realm=realm,
        thread_root_message=where.root,
    )
    links = [upload_link(caller, name, data) for name, data in read_files]
    message_request = check_message(
        caller.run.bot_profile,
        client,
        addressee,
        "\n".join(part for part in [content.strip(), *links] if part),
        realm=realm,
        thread_root_message=where.root,
    )
    [sent] = do_send_messages([message_request])
    return sent.message_id


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


ARMADA_MESSAGE_TOOLS: Final[
    dict[str, Callable[[ArmadaToolCaller, dict[str, object]], ArmadaToolAnswer]]
] = {
    ARMADA_POST_TOOL: post_tool,
    ARMADA_READ_TOOL: read_tool,
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
