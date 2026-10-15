"""The agent's tools for personal pages (contract C6): read any page or
post the person who asked can read, and draft a post on that person's
own page. Both act as that person, through the pages library
(zerver/lib/armada_pages.py), so the agent can never read more than
they can, publish, or write on anyone else's page.

Codex calls them as dynamic tools (zerver/lib/armada_agent.py
registers the specs on a new thread and answers each call here). The
answer's text is what the agent reads; its title is what the run's turn
shows the people in the topic.
"""

import json
from collections.abc import Callable
from typing import Any, Final, TypedDict

from django.db import connection
from django.utils.translation import gettext as _

from zerver.lib.armada_pages import draft_post, list_pages, read_page, read_post
from zerver.lib.exceptions import JsonableError
from zerver.models import Post, UserProfile

ARMADA_PAGE_READ_TOOL: Final = "armada_page_read"
ARMADA_PAGE_DRAFT_TOOL: Final = "armada_page_draft"

ARMADA_PAGE_TOOL_SPECS: Final[list[dict[str, Any]]] = [
    {
        "type": "function",
        "name": ARMADA_PAGE_READ_TOOL,
        "description": (
            "Read the team's personal pages, as the person who asked you can. With no "
            "arguments: everyone's page, with how many posts each holds. With user_id: that "
            "person's bio and their posts' titles, newest first. With post_id: one post in "
            "full, as Markdown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Whose page to read."},
                "post_id": {"type": "integer", "description": "The post to read in full."},
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": ARMADA_PAGE_DRAFT_TOOL,
        "description": (
            "Draft a post on the page of the person who asked you, only when they ask for "
            "one. It stays a draft that only they see until they publish it; you cannot "
            "publish, or write on anyone else's page. Say in your reply that you drafted it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": Post.MAX_TITLE_LENGTH,
                },
                "content": {
                    "type": "string",
                    "description": "The post, in Markdown.",
                    "maxLength": Post.MAX_CONTENT_LENGTH,
                },
            },
            "required": ["title", "content"],
            "additionalProperties": False,
        },
    },
]
ARMADA_PAGE_TOOL_NAMES: Final = frozenset(spec["name"] for spec in ARMADA_PAGE_TOOL_SPECS)


class ArmadaPageToolAnswer(TypedDict):
    success: bool
    # What the agent reads.
    text: str
    # What the run's turn shows.
    title: str


def page_tool_arguments(arguments: object) -> dict[str, object]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            raise JsonableError(_("The tool's arguments are not JSON."))
    if not isinstance(arguments, dict):
        raise JsonableError(_("The tool's arguments must be an object."))
    return arguments


def optional_int_argument(arguments: dict[str, object], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise JsonableError(_("{name} must be an integer.").format(name=name))
    return value


def string_argument(arguments: dict[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise JsonableError(_("{name} must be a string.").format(name=name))
    return value


def read_answer(data: dict[str, Any], title: str) -> ArmadaPageToolAnswer:
    return {"success": True, "text": json.dumps(data, ensure_ascii=False), "title": title}


def read_pages_tool(asker: UserProfile, arguments: dict[str, object]) -> ArmadaPageToolAnswer:
    user_id = optional_int_argument(arguments, "user_id")
    post_id = optional_int_argument(arguments, "post_id")
    if user_id is not None and post_id is not None:
        raise JsonableError(_("Give user_id or post_id, not both."))
    if post_id is not None:
        post = read_post(asker, post_id)
        return read_answer(post, _("Read the post “{title}”.").format(title=post["title"]))
    if user_id is not None:
        person = UserProfile.objects.filter(id=user_id, realm_id=asker.realm_id).first()
        if person is None:
            raise JsonableError(_("No such user"))
        # read_page refuses a person the asker cannot see before their
        # name is used.
        page = read_page(asker, person)
        return read_answer(
            {**page, "full_name": person.full_name},
            _("Read {full_name}'s page.").format(full_name=person.full_name),
        )
    pages = list_pages(asker)
    names = dict(
        UserProfile.objects.filter(id__in=[page["user_id"] for page in pages]).values_list(
            "id", "full_name"
        )
    )
    return read_answer(
        {"pages": [{**page, "full_name": names[page["user_id"]]} for page in pages]},
        _("Read the list of pages."),
    )


def draft_post_tool(asker: UserProfile, arguments: dict[str, object]) -> ArmadaPageToolAnswer:
    post = draft_post(
        asker, string_argument(arguments, "title"), string_argument(arguments, "content")
    )
    return {
        "success": True,
        "text": (
            f"Drafted “{post.title}” (post {post.id}) on {asker.full_name}'s page. It is a "
            "draft only they see until they publish it. Say in your reply that you drafted "
            "it, with its title."
        ),
        "title": _("Drafted “{title}” on {full_name}'s page.").format(
            title=post.title, full_name=asker.full_name
        ),
    }


ARMADA_PAGE_TOOLS: Final[
    dict[str, Callable[[UserProfile, dict[str, object]], ArmadaPageToolAnswer]]
] = {
    ARMADA_PAGE_READ_TOOL: read_pages_tool,
    ARMADA_PAGE_DRAFT_TOOL: draft_post_tool,
}


def answer_armada_page_tool(
    asker_id: int | None, tool: str, arguments: object
) -> ArmadaPageToolAnswer:
    """Answer one of the page tools for the person who asked for the
    turn; a refusal is an answer the agent reads, never an exception."""
    try:
        asker = (
            UserProfile.objects.filter(id=asker_id, is_active=True, is_bot=False).first()
            if asker_id is not None
            else None
        )
        if asker is None:
            raise JsonableError(_("No person asked for this turn, so it has no page to use."))
        return ARMADA_PAGE_TOOLS[tool](asker, page_tool_arguments(arguments))
    except JsonableError as error:
        return {"success": False, "text": error.msg, "title": error.msg}
    finally:
        # Codex's tool calls are answered on threads of their own, and
        # each thread opens its own database connection: close it here
        # rather than leave it to the thread's end. Inside a transaction
        # (a test's), the connection is its owner's.
        if not connection.in_atomic_block:
            connection.close()
