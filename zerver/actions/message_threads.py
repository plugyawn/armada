from django.db import transaction
from django.utils.timezone import now as timezone_now

from zerver.actions.message_send import render_incoming_message
from zerver.lib.mention import MentionBackend, MentionData
from zerver.lib.message import event_recipient_ids_for_action_on_messages, normalize_body
from zerver.lib.message_threads import bulk_message_thread_summaries, serialize_message_thread_reply
from zerver.models import Message, MessageThread, MessageThreadReply, UserProfile
from zerver.tornado.django_api import send_event_on_commit

MESSAGE_THREAD_PARTICIPANT_LIMIT = 4


def _render_message_thread_reply(
    message: Message,
    sender: UserProfile,
    content: str,
) -> str:
    rendering_message = Message(
        sender=sender,
        recipient_id=message.recipient_id,
        realm_id=message.realm_id,
        subject=message.subject,
        content=content,
        date_sent=timezone_now(),
        sending_client_id=message.sending_client_id,
        is_channel_message=message.is_channel_message,
    )
    mention_data = MentionData(
        mention_backend=MentionBackend(message.realm_id),
        content=content,
        message_sender=sender,
    )
    rendering_result = render_incoming_message(
        rendering_message,
        content,
        message.realm,
        mention_data=mention_data,
        acting_user=sender,
        no_previews=True,
    )
    return rendering_result.rendered_content


def do_add_message_thread_reply(
    message: Message,
    sender: UserProfile,
    content: str,
) -> tuple[MessageThreadReply, dict[str, object]]:
    """Add a reply while the caller holds a lock on the parent message."""
    assert transaction.get_connection().in_atomic_block
    normalized_content = normalize_body(content)
    rendered_content = _render_message_thread_reply(message, sender, normalized_content)

    try:
        thread = MessageThread.objects.select_for_update().get(message_id=message.id)
    except MessageThread.DoesNotExist:
        # The parent Message lock serializes first-reply creation.
        thread = MessageThread.objects.create(message=message)

    reply_time = timezone_now()
    reply = MessageThreadReply.objects.create(
        thread=thread,
        sender=sender,
        content=normalized_content,
        rendered_content=rendered_content,
        date_sent=reply_time,
    )
    participant_user_ids = [
        sender.id,
        *(
            user_id
            for user_id in thread.participant_user_ids
            if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id != sender.id
        ),
    ][:MESSAGE_THREAD_PARTICIPANT_LIMIT]
    thread.reply_count += 1
    thread.last_reply_at = reply_time
    thread.last_reply_sender = sender
    thread.participant_user_ids = participant_user_ids
    thread.save(
        update_fields=[
            "reply_count",
            "last_reply_at",
            "last_reply_sender",
            "participant_user_ids",
        ]
    )

    reply_data = serialize_message_thread_reply(reply)
    summary = bulk_message_thread_summaries([message.id])[message.id]
    event = {
        "type": "message_thread",
        "message_id": message.id,
        "reply_summary": summary,
        "reply": reply_data,
    }
    target_user_ids = event_recipient_ids_for_action_on_messages(
        [message.id], message.is_channel_message
    )
    send_event_on_commit(message.realm, event, target_user_ids)
    return reply, summary
