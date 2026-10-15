"""Exactly-once sends (contract C4). A client names each send with a key
of its own; a repeat of the send with the same key from the same person
within CLIENT_KEY_LIFETIME answers with the first message's id, and sends
nothing: no message, no event, no agent run.

The key is claimed in the send's own transaction, before the message is
built: a concurrent repeat waits on the key's unique index until the
first send commits (and then answers with its id) or rolls back (and then
sends). A committed claim always names its message. Each claim first
clears the person's keys older than a day, which keeps the table small
without a job of its own.
"""

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol, TypeVar

from django.db import IntegrityError, transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.models import MessageClientKey, UserProfile

CLIENT_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
CLIENT_KEY_LIFETIME = timedelta(hours=24)


class Sent(Protocol):
    message_id: int


SentT = TypeVar("SentT", bound=Sent)


def check_client_key(client_key: str) -> None:
    if CLIENT_KEY_RE.fullmatch(client_key) is None:
        raise JsonableError(
            _("client_key must be 1 to 64 letters, digits, hyphens or underscores.")
        )


def first_message_id(user: UserProfile, client_key: str) -> int | None:
    return (
        MessageClientKey.objects.filter(
            user_profile=user,
            client_key=client_key,
            date_created__gte=timezone_now() - CLIENT_KEY_LIFETIME,
        )
        .values_list("message_id", flat=True)
        .first()
    )


def send_once(user: UserProfile, client_key: str, send: Callable[[], SentT]) -> SentT | int:
    """Run send unless this person already used this key: returns what
    send returned, or the first message's id when the send is a repeat."""
    check_client_key(client_key)
    sent_before = first_message_id(user, client_key)
    if sent_before is not None:
        return sent_before
    try:
        # The request's own transaction: a clash on the key rolls back
        # exactly this send, and the claim commits with its message.
        with transaction.atomic(durable=True):
            MessageClientKey.objects.filter(
                user_profile=user, date_created__lt=timezone_now() - CLIENT_KEY_LIFETIME
            ).delete()
            claim = MessageClientKey.objects.create(user_profile=user, client_key=client_key)
            sent = send()
            claim.message_id = sent.message_id
            claim.save(update_fields=["message_id"])
            return sent
    except IntegrityError:
        # A repeat that ran alongside the first send, which has committed
        # since; any other integrity error is not ours to answer.
        sent_before = first_message_id(user, client_key)
        if sent_before is None:
            raise
        return sent_before
