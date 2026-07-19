from django.db import transaction
from django.http import HttpRequest, HttpResponse
from pydantic import Json, NonNegativeInt, PositiveInt

from zerver.actions.message_threads import do_add_message_thread_reply
from zerver.lib.message import access_message
from zerver.lib.message_threads import bulk_message_thread_summaries, serialize_message_thread_reply
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import MessageThreadReply, UserProfile

MESSAGE_THREAD_DEFAULT_LIMIT = 50
MESSAGE_THREAD_MAX_LIMIT = 100


@typed_endpoint
def get_message_thread_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    message_id: PathOnly[NonNegativeInt],
    before: Json[PositiveInt] | None = None,
    limit: Json[PositiveInt] = MESSAGE_THREAD_DEFAULT_LIMIT,
) -> HttpResponse:
    message = access_message(user_profile, message_id, is_modifying_message=False)
    requested_limit = min(limit, MESSAGE_THREAD_MAX_LIMIT)
    replies_query = MessageThreadReply.objects.filter(thread_id=message.id).select_related("sender")
    if before is not None:
        replies_query = replies_query.filter(id__lt=before)
    newest_replies = list(replies_query.order_by("-id")[: requested_limit + 1])
    has_more = len(newest_replies) > requested_limit
    replies = newest_replies[:requested_limit]
    replies.reverse()

    summary = bulk_message_thread_summaries([message.id]).get(message.id)
    return json_success(
        request,
        {
            "reply_summary": summary,
            "replies": [serialize_message_thread_reply(reply) for reply in replies],
            "has_more": has_more,
        },
    )


@transaction.atomic(durable=True)
@typed_endpoint
def post_message_thread_reply_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    message_id: PathOnly[NonNegativeInt],
    content: str,
) -> HttpResponse:
    message = access_message(user_profile, message_id, lock_message=True, is_modifying_message=True)
    reply, summary = do_add_message_thread_reply(message, user_profile, content)
    return json_success(
        request,
        {
            "reply_summary": summary,
            "reply": serialize_message_thread_reply(reply),
        },
    )
