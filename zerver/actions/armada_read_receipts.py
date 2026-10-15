from collections.abc import Collection

from zerver.lib.armada_read_receipts import get_armada_read_receipt_recipients
from zerver.models import UserProfile
from zerver.tornado.django_api import send_event_on_commit


def send_armada_read_receipts_events(reader: UserProfile, message_ids: Collection[int]) -> None:
    """Tell each sender, once the transaction commits, which of their
    messages the reader has just marked as read, so that a sender's ticks
    turn without asking."""
    for sender_id, sender_message_ids in get_armada_read_receipt_recipients(
        reader, message_ids
    ).items():
        event = {
            "type": "read_receipts",
            "message_ids": sender_message_ids,
            "user_id": reader.id,
        }
        send_event_on_commit(reader.realm, event, [sender_id])
