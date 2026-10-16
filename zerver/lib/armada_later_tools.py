"""The agent's tools for Later and Send when (zerver/lib/armada_later.py):
put a message on the Later list of the person who asked, to come back
to them when a run ends or after a while ("I'll remind you when the
tests finish"), and hold a message of the agent's own until a run ends
("post here when that run lands").

They follow the page tools (zerver/lib/armada_page_tools.py): Codex
calls them as dynamic tools, which zerver/lib/armada_agent.py registers
on a new thread and answers here, and they act for the person whose
message started the turn, with that person's rights. A refusal is the
error text that person would get.
"""

from datetime import timedelta
from typing import Final

from django.conf import settings
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.actions.message_send import check_message
from zerver.actions.reminders import schedule_reminder_for_message
from zerver.actions.scheduled_messages import check_schedule_message
from zerver.lib.addressee import Addressee
from zerver.lib.armada_later import access_run_to_wait_for
from zerver.lib.armada_page_tools import (
    ArmadaToolAnswer,
    answer_tool_call,
    optional_int_argument,
    string_argument,
    tool_arguments,
)
from zerver.lib.exceptions import JsonableError
from zerver.models import UserProfile
from zerver.models.bots import AgentRun
from zerver.models.clients import get_client
from zerver.models.streams import get_stream

ARMADA_LATER_TOOL: Final = "armada_later"
ARMADA_SEND_WHEN_TOOL: Final = "armada_send_when"
# A week: a promise to come back further off than that is a calendar's.
ARMADA_LATER_MAX_MINUTES: Final = 7 * 24 * 60

ARMADA_LATER_TOOL_SPECS: Final = [
    {
        "type": "function",
        "name": ARMADA_LATER_TOOL,
        "description": (
            "Put a message on the Later list of the person who asked you, so that it comes "
            "back to them as a reminder: when a run ends (your current run, or run_id), or "
            "after in_minutes. Use it when you promise to come back to something (\"I'll "
            'remind you when the tests finish"), and say in your reply that you did. '
            "message_id is the message that comes back; without it, the message that asked "
            "you."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "The message that comes back.",
                },
                "run_id": {
                    "type": "integer",
                    "description": "It comes back when this run ends.",
                },
                "in_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": ARMADA_LATER_MAX_MINUTES,
                    "description": "It comes back after this many minutes instead.",
                },
                "note": {
                    "type": "string",
                    "maxLength": settings.MAX_REMINDER_NOTE_LENGTH,
                    "description": "A line shown with it when it comes back.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": ARMADA_SEND_WHEN_TOOL,
        "description": (
            "Post a message as yourself when a run ends (done, failed or cancelled): your "
            "current run, or run_id, such as a run you started elsewhere. It goes to channel "
            "and topic, to another topic of the channel you were asked in, or to the topic you "
            "were asked in, with the rights of the person who asked you. Use it to report "
            "where you were asked once the work lands. Write Markdown; it is posted as "
            "written when the run ends."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": settings.MAX_MESSAGE_LENGTH,
                    "description": "The message, in Markdown.",
                },
                "run_id": {
                    "type": "integer",
                    "description": "It is posted when this run ends.",
                },
                "channel": {
                    "type": "string",
                    "description": "The channel's name, without #.",
                },
                "topic": {"type": "string", "description": "The topic's name."},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
]
ARMADA_LATER_TOOL_NAMES: Final = frozenset(spec["name"] for spec in ARMADA_LATER_TOOL_SPECS)


def optional_string_argument(arguments: dict[str, object], name: str) -> str | None:
    return None if arguments.get(name) is None else string_argument(arguments, name)


def run_of_turn(asker: UserProfile, trigger_message_id: int | None) -> AgentRun:
    run = (
        AgentRun.objects.filter(realm_id=asker.realm_id, trigger_message_id=trigger_message_id)
        .order_by("-id")
        .first()
        if trigger_message_id is not None
        else None
    )
    if run is None:
        raise JsonableError(_("This turn is not part of a run."))
    return run


def run_to_wait_for(
    asker: UserProfile, arguments: dict[str, object], current_run: AgentRun
) -> AgentRun:
    run_id = optional_int_argument(arguments, "run_id")
    return access_run_to_wait_for(asker, current_run.id if run_id is None else run_id)


def later_tool(
    asker: UserProfile, arguments: dict[str, object], current_run: AgentRun
) -> ArmadaToolAnswer:
    message_id = optional_int_argument(arguments, "message_id")
    in_minutes = optional_int_argument(arguments, "in_minutes")
    note = optional_string_argument(arguments, "note") or ""
    if message_id is None:
        message_id = current_run.trigger_message_id
    if in_minutes is not None:
        if arguments.get("run_id") is not None:
            raise JsonableError(_("Give run_id or in_minutes, not both."))
        if not 1 <= in_minutes <= ARMADA_LATER_MAX_MINUTES:
            raise JsonableError(
                _("in_minutes must be from 1 to {maximum}.").format(
                    maximum=ARMADA_LATER_MAX_MINUTES
                )
            )
        deliver_at = timezone_now() + timedelta(minutes=in_minutes)
        schedule_reminder_for_message(asker, get_client("Internal"), message_id, deliver_at, note)
        when = f"at {deliver_at:%Y-%m-%d %H:%M} UTC"
    else:
        run = run_to_wait_for(asker, arguments, current_run)
        schedule_reminder_for_message(
            asker, get_client("Internal"), message_id, None, note, agent_run=run
        )
        when = f"when run {run.id} ends"
    return {
        "success": True,
        "text": (
            f"Message {message_id} is on {asker.full_name}'s Later list. It comes back to them "
            f"as a reminder {when}. Say in your reply that you put it there."
        ),
        "title": _("Put a message on {full_name}'s Later list.").format(full_name=asker.full_name),
    }


def send_when_tool(
    asker: UserProfile, arguments: dict[str, object], current_run: AgentRun
) -> ArmadaToolAnswer:
    content = string_argument(arguments, "content")
    channel = optional_string_argument(arguments, "channel")
    topic = optional_string_argument(arguments, "topic")
    if channel is None:
        # The channel the agent was asked in, and its topic unless another is given.
        channel = current_run.stream_name
        if topic is None:
            topic = current_run.topic_name
    elif topic is None:
        raise JsonableError(_("Give the topic with the channel."))
    run = run_to_wait_for(asker, arguments, current_run)
    client = get_client("Internal")
    # The person who asked must be able to post there themself; the
    # agent's own check follows when it is scheduled, and again when it
    # is sent.
    check_message(asker, client, Addressee.for_stream_name(channel, topic), content)
    stream = get_stream(channel, asker.realm)
    scheduled_message_id = check_schedule_message(
        current_run.bot_profile,
        client,
        "stream",
        [stream.id],
        topic,
        content,
        None,
        asker.realm,
        agent_run=run,
    )
    return {
        "success": True,
        "text": (
            f"Scheduled message {scheduled_message_id} is posted in #{stream.name} > {topic} "
            f"when run {run.id} ends."
        ),
        "title": _("Will post in #{channel} > {topic} when the run ends.").format(
            channel=stream.name, topic=topic
        ),
    }


ARMADA_LATER_TOOLS: Final = {
    ARMADA_LATER_TOOL: later_tool,
    ARMADA_SEND_WHEN_TOOL: send_when_tool,
}


def answer_armada_later_tool(
    asker_id: int | None, trigger_message_id: int | None, tool: str, arguments: object
) -> ArmadaToolAnswer:
    """Answer one of the Later tools for the person who asked for the turn,
    whose run is the one that trigger_message_id started."""

    def answer() -> ArmadaToolAnswer:
        asker = (
            UserProfile.objects.filter(id=asker_id, is_active=True, is_bot=False).first()
            if asker_id is not None
            else None
        )
        if asker is None:
            raise JsonableError(_("No person asked for this turn, so it has no Later list."))
        return ARMADA_LATER_TOOLS[tool](
            asker, tool_arguments(arguments), run_of_turn(asker, trigger_message_id)
        )

    return answer_tool_call(answer)
