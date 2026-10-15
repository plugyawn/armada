from collections import defaultdict
from collections.abc import Collection

from zerver.lib.users import check_can_access_user, get_inaccessible_user_ids
from zerver.models import Message, UserMessage, UserProfile

# A client asks about the messages it is showing; this bounds the work of
# one request, as the per-message endpoint bounds it to one.
ARMADA_READ_RECEIPTS_BATCH_LIMIT = 100


def get_armada_read_receipts(
    requester: UserProfile, message_ids: Collection[int]
) -> dict[str, list[int]]:
    """Who has read each of the requester's own messages among
    message_ids, keyed by message id; ids of other people's messages are
    left out.

    The rules are GET /messages/{message_id}/read_receipts's (see the
    comment in zerver/views/read_receipts.py): only active readers who
    send read receipts, never the sender, and neither someone who muted
    the requester nor someone the requester muted. One more: a person
    the requester cannot access (a guest under limited user access) is
    left out, so that a receipt never reveals them.
    """
    own_message_ids = list(
        Message.objects.filter(
            id__in=message_ids, realm_id=requester.realm_id, sender_id=requester.id
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    readers = list(
        UserMessage.objects.filter(
            message_id__in=own_message_ids,
            user_profile__is_active=True,
            user_profile__send_read_receipts=True,
        )
        .exclude(user_profile_id=requester.id)
        .exclude(user_profile__muter__muted_user_id=requester.id)
        .exclude(user_profile__muted__user_profile_id=requester.id)
        # The read flag is a bit in a bitfield, which the ORM cannot
        # test; where_read() is a constant SQL fragment.
        .extra(where=[UserMessage.where_read()])  # noqa: S610
        .order_by("message_id", "user_profile_id")
        .values_list("message_id", "user_profile_id")
    )
    inaccessible_user_ids = get_inaccessible_user_ids(
        list({user_id for _message_id, user_id in readers}), requester
    )
    read_receipts: dict[str, list[int]] = {str(message_id): [] for message_id in own_message_ids}
    for message_id, user_id in readers:
        if user_id not in inaccessible_user_ids:
            read_receipts[str(message_id)].append(user_id)
    return read_receipts


def get_armada_read_receipt_recipients(
    reader: UserProfile, message_ids: Collection[int]
) -> dict[int, list[int]]:
    """The senders who may learn that the reader has just read
    message_ids, each with the ids of their own messages among them.

    This is get_armada_read_receipts seen from the reader's side: each
    sender learns exactly what that endpoint would answer them. Bots are
    left out, since nothing shows a bot its receipts; a bot can still ask
    the endpoint.
    """
    realm = reader.realm
    if not message_ids or not realm.enable_read_receipts or not reader.send_read_receipts:
        return {}

    rows = (
        Message.objects.filter(
            id__in=message_ids,
            realm_id=realm.id,
            sender__is_active=True,
            sender__is_bot=False,
        )
        .exclude(sender_id=reader.id)
        # The sender muted the reader, or the reader muted the sender.
        .exclude(sender__muter__muted_user_id=reader.id)
        .exclude(sender__muted__user_profile_id=reader.id)
        .order_by("id")
        .values_list("sender_id", "sender__role", "id")
    )
    message_ids_by_sender: dict[int, list[int]] = defaultdict(list)
    guest_sender_ids = set()
    for sender_id, sender_role, message_id in rows:
        message_ids_by_sender[sender_id].append(message_id)
        if sender_role == UserProfile.ROLE_GUEST:
            guest_sender_ids.add(sender_id)

    # Only a guest can be denied access to other people.
    if guest_sender_ids:
        for guest in UserProfile.objects.filter(id__in=guest_sender_ids).select_related("realm"):
            if not check_can_access_user(reader, guest, realm):
                del message_ids_by_sender[guest.id]
    return message_ids_by_sender
