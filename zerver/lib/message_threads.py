from zerver.lib.avatar import avatar_url
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import MessageThread, MessageThreadReply, UserProfile


def _serialize_thread_user(user_profile: UserProfile) -> dict[str, object]:
    return {
        "user_id": user_profile.id,
        "full_name": user_profile.full_name,
        "avatar_url": avatar_url(user_profile),
    }


def bulk_message_thread_summaries(
    message_ids: list[int],
) -> dict[int, dict[str, object]]:
    """Return API-ready summaries without loading any reply bodies."""
    if not message_ids:
        return {}

    threads = list(
        MessageThread.objects.filter(message_id__in=message_ids, reply_count__gt=0).only(
            "message",
            "reply_count",
            "last_reply_at",
            "last_reply_sender",
            "participant_user_ids",
        )
    )
    user_ids: set[int] = set()
    for thread in threads:
        if thread.last_reply_sender_id is not None:
            user_ids.add(thread.last_reply_sender_id)
        user_ids.update(
            user_id
            for user_id in thread.participant_user_ids
            if isinstance(user_id, int) and not isinstance(user_id, bool)
        )

    users_by_id = {
        user.id: user
        for user in UserProfile.objects.filter(id__in=user_ids).only(
            "id",
            "full_name",
            "delivery_email",
            "avatar_source",
            "avatar_version",
            "realm_id",
        )
    }
    summaries: dict[int, dict[str, object]] = {}
    for thread in threads:
        assert thread.last_reply_at is not None
        last_sender = users_by_id.get(thread.last_reply_sender_id)
        participants = [
            _serialize_thread_user(users_by_id[user_id])
            for user_id in thread.participant_user_ids
            if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id in users_by_id
        ]
        summaries[thread.message_id] = {
            "reply_count": thread.reply_count,
            "last_reply_at": datetime_to_timestamp(thread.last_reply_at),
            "last_sender": (
                _serialize_thread_user(last_sender) if last_sender is not None else None
            ),
            "participants": participants,
        }
    return summaries


def serialize_message_thread_reply(reply: MessageThreadReply) -> dict[str, object]:
    return {
        "id": reply.id,
        "message_id": reply.thread_id,
        "sender_id": reply.sender_id,
        "sender_full_name": reply.sender.full_name,
        "avatar_url": avatar_url(reply.sender),
        "content": reply.rendered_content,
        "content_type": "text/html",
        "timestamp": datetime_to_timestamp(reply.date_sent),
    }
