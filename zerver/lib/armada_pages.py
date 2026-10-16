"""Personal pages (contract C5): each person's small, bearblog-like page,
a bio over a list of posts, which everyone in the organization and the
agent can read (statement 29).

Who sees what. Reading follows Zulip's user access: a guest sees the
pages of the people they can see, as the users endpoints do. A draft
is seen only by its author. Only the author writes their page and their
posts; bots have no pages. Every function here takes the person acting
or reading, so the agent's tools (C6) read with the asking person's
access and draft only on that person's page.

Posts are Markdown, rendered by Zulip's own renderer as a channel's
description is (no previews), so the HTML is sanitized as a message's
is. Each change to a post is pushed as an armada_post event: to the
author always, and to everyone else who can see the author while the
post is published (an add when it appears to them, a remove when it
leaves).

Uploads. A post claims the uploads its content links to, as a message
does (zerver/actions/uploads.py): whoever may read the post may read
them (zerver/lib/attachments.py asks may_read_upload_in_a_post), and
they are not unclaimed while it holds them.
"""

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from django.db import transaction
from django.db.models import Count, Max, Q, QuerySet
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError, MarkdownRenderingError, ResourceNotFoundError
from zerver.lib.markdown import MessageRenderingResult, markdown_convert
from zerver.lib.users import (
    access_user_by_id,
    check_can_access_user,
    check_user_can_access_all_users,
    get_accessible_user_ids,
    get_user_ids_who_can_access_user,
)
from zerver.models import Attachment, Page, Post, UserProfile
from zerver.tornado.django_api import send_event_on_commit


def timestamp(moment: datetime) -> int:
    return int(moment.timestamp())


def post_summary(post: Post) -> dict[str, Any]:
    return {
        "id": post.id,
        "title": post.title,
        "created": timestamp(post.date_created),
        "updated": timestamp(post.date_updated),
        "draft": post.draft,
    }


def post_in_full(post: Post) -> dict[str, Any]:
    return {
        "id": post.id,
        "user_id": post.author_id,
        "title": post.title,
        "content": post.content,
        "rendered": post.rendered_content,
        "created": timestamp(post.date_created),
        "updated": timestamp(post.date_updated),
        "draft": post.draft,
        "drafted_by_agent": post.drafted_by_agent,
    }


def visible_posts(viewer: UserProfile) -> Q:
    """The posts a viewer may see, among those of people they can see:
    every published post, and their own drafts. Post.is_visible_to
    asks the same of one post."""
    return Q(draft=False) | Q(author_id=viewer.id)


def access_page_person(viewer: UserProfile, user_id: int) -> UserProfile:
    return access_user_by_id(
        viewer, user_id, allow_deactivated=True, allow_bots=False, for_admin=False
    )


def check_can_write(author: UserProfile) -> None:
    if author.is_bot:
        raise JsonableError(_("Bots do not have pages."))


def list_pages(viewer: UserProfile) -> list[dict[str, Any]]:
    """Every active person the viewer can see, with how many of their
    posts the viewer can see and when their page last changed (None for a
    page with no bio and no posts), most recently changed first."""
    people = UserProfile.objects.filter(realm_id=viewer.realm_id, is_active=True, is_bot=False)
    if not check_user_can_access_all_users(viewer):
        people = people.filter(id__in=get_accessible_user_ids(viewer.realm, viewer))
    person_ids = list(people.values_list("id", flat=True))
    posts = {
        row["author_id"]: row
        for row in Post.objects.filter(visible_posts(viewer), author_id__in=person_ids)
        .values("author_id")
        .annotate(post_count=Count("id"), updated=Max("date_updated"))
    }
    bios = dict(
        Page.objects.filter(user_profile_id__in=person_ids)
        .exclude(bio="")
        .values_list("user_profile_id", "date_updated")
    )
    pages = []
    for person_id in person_ids:
        changes = [
            moment
            for moment in (posts.get(person_id, {}).get("updated"), bios.get(person_id))
            if moment is not None
        ]
        pages.append(
            {
                "user_id": person_id,
                "post_count": posts[person_id]["post_count"] if person_id in posts else 0,
                "updated": timestamp(max(changes)) if changes else None,
            }
        )
    pages.sort(key=lambda page: (page["updated"] is not None, page["updated"] or 0), reverse=True)
    return pages


def read_page(viewer: UserProfile, user: UserProfile) -> dict[str, Any]:
    """A person's bio and the posts the viewer can see, newest first."""
    if user.is_bot or user.realm_id != viewer.realm_id or not check_can_access_user(user, viewer):
        raise JsonableError(_("No such user"))
    bio = Page.objects.filter(user_profile=user).values_list("bio", flat=True).first()
    posts = Post.objects.filter(visible_posts(viewer), author=user).order_by("-date_created", "-id")
    return {
        "user_id": user.id,
        "bio": bio or "",
        "posts": [post_summary(post) for post in posts],
    }


def first_readable_post(viewer: UserProfile, posts: QuerySet[Post]) -> Post | None:
    """The first of the posts the viewer may read: a published post by
    someone they can see, or a draft of their own. Reading a post and
    reading the uploads it shows both ask this."""
    for post in posts.filter(visible_posts(viewer), realm_id=viewer.realm_id).select_related(
        "author"
    ):
        if check_can_access_user(post.author, viewer, realm=viewer.realm):
            return post
    return None


def access_post(viewer: UserProfile, post_id: int) -> Post:
    """The post, if the viewer may read it; one error for a post that is
    not there and one they may not see, so a draft's existence does not
    leak."""
    post = first_readable_post(viewer, Post.objects.filter(id=post_id))
    if post is None:
        raise ResourceNotFoundError(_("No such post."))
    return post


def may_read_upload_in_a_post(viewer: UserProfile, attachment: Attachment) -> bool:
    """Whether the viewer may read a post that claims the upload, and so
    the upload, as a message's readers may read its uploads."""
    claiming_posts = attachment.armada_posts.defer("content", "rendered_content")
    return first_readable_post(viewer, claiming_posts) is not None


def read_post(viewer: UserProfile, post_id: int) -> dict[str, Any]:
    return post_in_full(access_post(viewer, post_id))


def render_post(author: UserProfile, content: str) -> MessageRenderingResult:
    try:
        return markdown_convert(
            content, message_realm=author.realm, no_previews=True, acting_user=author
        )
    except MarkdownRenderingError:
        raise JsonableError(_("Unable to render the post."))


def claim_post_uploads(post: Post, path_ids: Iterable[str]) -> None:
    """Makes the post's claims the uploads its content links to, as
    check_attachment_reference_change does for an edited message: a
    link the content dropped releases its upload, and a new one claims
    its upload only if the author may read it already, so that naming
    someone else's file in a post never opens it to the post's
    readers."""
    linked = set(path_ids)
    held = set(post.attachments.values_list("path_id", flat=True))
    released = held - linked
    if released:
        post.attachments.remove(
            *Attachment.objects.filter(path_id__in=released).select_for_update(no_key=True)
        )
    # Imported here: zerver.lib.attachments imports this module, for
    # may_read_upload_in_a_post.
    from zerver.lib.attachments import validate_attachment_request

    claimed = []
    for path_id in sorted(linked - held):
        may_read, attachment = validate_attachment_request(post.author, path_id)
        if not may_read:
            # A link that only looks like an upload, or an upload the
            # author may not read; as for a message, either suggests a
            # client bug, so it is logged.
            logging.warning(
                "User %s tried to claim upload %s in post %s, but lacks permission",
                post.author_id,
                path_id,
                post.id,
            )
            continue
        assert attachment is not None
        claimed.append(attachment)
    post.attachments.add(*claimed)


def check_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise JsonableError(_("A post needs a title."))
    if len(title) > Post.MAX_TITLE_LENGTH:
        raise JsonableError(
            _("A title is at most {max_length} characters.").format(
                max_length=Post.MAX_TITLE_LENGTH
            )
        )
    return title


def check_content(content: str) -> str:
    if len(content) > Post.MAX_CONTENT_LENGTH:
        raise JsonableError(
            _("A post is at most {max_length} characters.").format(
                max_length=Post.MAX_CONTENT_LENGTH
            )
        )
    return content


def push_post_change(
    post: Post, op: Literal["add", "update", "remove"], *, was_published: bool
) -> None:
    """The author hears of every change as it is; everyone else who can
    see the author hears of a post only while it is published."""
    author = post.author
    event_post = {
        "id": post.id,
        "user_id": author.id,
        "title": post.title,
        "updated": timestamp(post.date_updated),
        "draft": post.draft,
    }
    send_event_on_commit(
        author.realm, {"type": "armada_post", "op": op, "post": event_post}, [author.id]
    )
    is_published = op != "remove" and not post.draft
    if was_published and is_published:
        others_op = "update"
    elif is_published:
        others_op = "add"
    elif was_published:
        others_op = "remove"
    else:
        return
    others = [
        user_id for user_id in get_user_ids_who_can_access_user(author) if user_id != author.id
    ]
    send_event_on_commit(
        author.realm, {"type": "armada_post", "op": others_op, "post": event_post}, others
    )


# The writes below check everything before their transaction and raise
# only after it: the transaction may be a caller's (the agent's), which a
# refusal raised inside would spoil.
def create_post(
    author: UserProfile, title: str, content: str, *, draft: bool, drafted_by_agent: bool = False
) -> Post:
    check_can_write(author)
    title = check_title(title)
    content = check_content(content)
    rendering = render_post(author, content)
    with transaction.atomic(savepoint=False):
        post = Post.objects.create(
            realm_id=author.realm_id,
            author=author,
            title=title,
            content=content,
            rendered_content=rendering.rendered_content,
            draft=draft,
            drafted_by_agent=drafted_by_agent,
        )
        claim_post_uploads(post, rendering.potential_attachment_path_ids)
        push_post_change(post, "add", was_published=False)
    return post


def lock_own_post(author: UserProfile, post_id: int) -> Post | None:
    """A post its author is changing, held for the transaction."""
    return (
        Post.objects.select_for_update(no_key=True)
        .filter(id=post_id, author=author)
        .select_related("author")
        .first()
    )


def update_post(
    author: UserProfile,
    post_id: int,
    *,
    title: str | None = None,
    content: str | None = None,
    draft: bool | None = None,
) -> None:
    if title is not None:
        title = check_title(title)
    rendering = None
    if content is not None:
        content = check_content(content)
        rendering = render_post(author, content)
    with transaction.atomic(savepoint=False):
        post = lock_own_post(author, post_id)
        if post is not None:
            was_published = not post.draft
            if title is not None:
                post.title = title
            if content is not None and rendering is not None:
                post.content = content
                post.rendered_content = rendering.rendered_content
                claim_post_uploads(post, rendering.potential_attachment_path_ids)
            if draft is not None:
                post.draft = draft
            post.date_updated = timezone_now()
            post.save()
            push_post_change(post, "update", was_published=was_published)
    if post is None:
        raise ResourceNotFoundError(_("No such post."))


def delete_post(author: UserProfile, post_id: int) -> None:
    with transaction.atomic(savepoint=False):
        post = lock_own_post(author, post_id)
        if post is not None:
            was_published = not post.draft
            post.delete()
            # The event names the post that was.
            post.id = post_id
            push_post_change(post, "remove", was_published=was_published)
    if post is None:
        raise ResourceNotFoundError(_("No such post."))


def update_bio(author: UserProfile, bio: str) -> None:
    check_can_write(author)
    if len(bio) > Page.MAX_BIO_LENGTH:
        raise JsonableError(
            _("A bio is at most {max_length} characters.").format(max_length=Page.MAX_BIO_LENGTH)
        )
    Page.objects.update_or_create(
        user_profile=author,
        defaults={"realm_id": author.realm_id, "bio": bio, "date_updated": timezone_now()},
    )


def draft_post(author: UserProfile, title: str, content: str) -> Post:
    """The agent's draft, on the page of the person who asked it: a draft
    only that person sees, until they publish it."""
    return create_post(author, title, content, draft=True, drafted_by_agent=True)
