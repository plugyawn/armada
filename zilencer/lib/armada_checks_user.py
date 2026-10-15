"""Dev-only engine behind `manage.py armada_checks_user`.

The checks in tools/armada-checks drive the development server as one user.
They used to log in as desdemona@zulip.com, whom people and agents also use
there, so someone else's session spent the checks' rate limit and someone
else's posts reached the checks' pages mid-measurement.  The checks now log
in as a user of their own, which this module creates and keeps in shape: it
sees what the reference user (desdemona) sees, with nobody else signed in as
it and a rate limit no run of the checks reaches.

"Sees what the reference user sees" is what the checks' fixtures were written
for: the same channels, the same read and unread messages in them (the
sidebar's counts, where a topic opens), the same direct-message conversations
in the same order, and the same time zone.  The one difference left is the
sender: the fixtures' messages from desdemona are someone else's here.
"""

from collections import defaultdict
from collections.abc import Iterable

from django.conf import settings
from django.db.models import F, Max

from zerver.actions.create_user import do_create_user
from zerver.actions.message_delete import do_delete_messages
from zerver.actions.message_send import (
    internal_send_group_direct_message,
    internal_send_private_message,
)
from zerver.actions.streams import bulk_add_subscriptions
from zerver.actions.users import do_change_user_role
from zerver.lib.onboarding_steps import ALL_ONBOARDING_STEPS
from zerver.models import (
    Message,
    OnboardingStep,
    Realm,
    Recipient,
    Stream,
    Subscription,
    UserMessage,
    UserProfile,
)
from zerver.models.users import get_system_bot, get_user_by_delivery_email

ARMADA_CHECKS_EMAIL = "armada-checks@zulip.com"
ARMADA_CHECKS_FULL_NAME = "Armada Checks"
ARMADA_CHECKS_REFERENCE_EMAIL = "desdemona@zulip.com"
# The person the checks' direct-message view (`#narrow/dm/<id>-King-Hamlet`
# in lib/selectors.mjs) talks to.
ARMADA_CHECKS_DM_PARTNER_EMAIL = "hamlet@zulip.com"
# "seconds:requests" pairs for RateLimitedUser: a run sends a few hundred
# requests over ten minutes, far below these.
ARMADA_CHECKS_RATE_LIMITS = "1:1000,60:30000"
COPIED_SUBSCRIPTION_PROPERTIES = ["color", "is_muted", "pin_to_top"]
MIRROR_CONTENT = "The checks keep this conversation where the reference user has one."


def ensure_armada_checks_user(
    realm: Realm, reference_email: str = ARMADA_CHECKS_REFERENCE_EMAIL
) -> UserProfile:
    reference = get_user_by_delivery_email(reference_email, realm)
    try:
        user = get_user_by_delivery_email(ARMADA_CHECKS_EMAIL, realm)
    except UserProfile.DoesNotExist:
        user = do_create_user(
            ARMADA_CHECKS_EMAIL,
            None,
            realm,
            ARMADA_CHECKS_FULL_NAME,
            role=reference.role,
            tos_version=settings.TERMS_OF_SERVICE_VERSION,
            add_initial_stream_subscriptions=False,
            acting_user=None,
        )
        # The welcome bot's direct messages would add a conversation the
        # reference user's sidebar does not have.
        welcome_bot = get_system_bot(settings.WELCOME_BOT, realm.id)
        do_delete_messages(
            realm,
            Message.objects.filter(
                id__in=UserMessage.objects.filter(
                    user_profile=user, message__sender=welcome_bot
                ).values("message_id")
            ),
            acting_user=None,
        )

    if user.role != reference.role:
        do_change_user_role(user, reference.role, acting_user=None, notify=False)
    if user.rate_limits != ARMADA_CHECKS_RATE_LIMITS:
        user.rate_limits = ARMADA_CHECKS_RATE_LIMITS
        user.save(update_fields=["rate_limits"])
    # Times on the page, and the day a message's date line falls on.
    if user.timezone != reference.timezone:
        user.timezone = reference.timezone
        user.save(update_fields=["timezone"])

    # No tour, banner or intro modal: each would move what the checks measure.
    OnboardingStep.objects.bulk_create(
        [OnboardingStep(user=user, onboarding_step=step.name) for step in ALL_ONBOARDING_STEPS],
        ignore_conflicts=True,
    )

    reference_subscriptions = {
        subscription.recipient.type_id: subscription
        for subscription in Subscription.objects.filter(
            user_profile=reference, active=True, recipient__type=Recipient.STREAM
        ).select_related("recipient")
    }
    streams = Stream.objects.filter(id__in=reference_subscriptions, deactivated=False)
    bulk_add_subscriptions(realm, streams, [user], acting_user=None)
    for subscription in Subscription.objects.filter(
        user_profile=user,
        recipient__type=Recipient.STREAM,
        recipient__type_id__in=reference_subscriptions,
    ).select_related("recipient"):
        source = reference_subscriptions[subscription.recipient.type_id]
        for name in COPIED_SUBSCRIPTION_PROPERTIES:
            setattr(subscription, name, getattr(source, name))
        subscription.save(update_fields=COPIED_SUBSCRIPTION_PROPERTIES)

    partner = get_user_by_delivery_email(ARMADA_CHECKS_DM_PARTNER_EMAIL, realm)
    if not UserMessage.objects.filter(
        user_profile=user,
        message__sender=partner,
        message__recipient__type=Recipient.DIRECT_MESSAGE_GROUP,
    ).exists():
        message_id = internal_send_private_message(
            partner, user, "The checks read and unread this conversation."
        )
        assert message_id is not None
        # Read, like everything else the checks start from.
        UserMessage.objects.filter(user_profile=user, message_id=message_id).update(
            flags=F("flags").bitor(UserMessage.flags.read)
        )

    mirror_channel_message_flags(user, reference, [stream.id for stream in streams])
    mirror_direct_message_conversations(user, reference)
    return user


def mirror_channel_message_flags(
    user: UserProfile, reference: UserProfile, stream_ids: Iterable[int]
) -> None:
    """Give the user the reference user's flags on every message of their
    channels: read and unread as the reference has them, so that the sidebar
    counts the same and a topic opens at the same message.  The user's own
    messages stay read, and so does anything the reference has no row for."""
    reference_flags = {
        message_id: int(flags)
        for message_id, flags in UserMessage.objects.filter(
            user_profile=reference,
            message__recipient__type=Recipient.STREAM,
            message__recipient__type_id__in=list(stream_ids),
        )
        .exclude(message__sender=user)
        .values_list("message_id", "flags")
    }
    own_flags = {
        message_id: int(flags)
        for message_id, flags in UserMessage.objects.filter(
            user_profile=user, message__recipient__type=Recipient.STREAM
        ).values_list("message_id", "flags")
    }
    UserMessage.objects.bulk_create(
        [
            UserMessage(user_profile=user, message_id=message_id, flags=flags)
            for message_id, flags in reference_flags.items()
            if message_id not in own_flags
        ],
        ignore_conflicts=True,
    )
    by_flags: dict[int, list[int]] = defaultdict(list)
    for message_id, flags in own_flags.items():
        if message_id in reference_flags and flags != reference_flags[message_id]:
            by_flags[reference_flags[message_id]].append(message_id)
    for flags, message_ids in by_flags.items():
        UserMessage.objects.filter(user_profile=user, message_id__in=message_ids).update(
            flags=flags
        )
    UserMessage.objects.filter(
        user_profile=user, message__recipient__type=Recipient.STREAM
    ).exclude(message_id__in=reference_flags).update(flags=F("flags").bitor(UserMessage.flags.read))


def direct_message_conversations(user: UserProfile) -> dict[frozenset[int], int]:
    """The user's direct-message conversations, each named by the other
    people in it, with the id of its latest message."""
    latest = dict(
        UserMessage.objects.filter(user_profile=user)
        .exclude(message__recipient__type=Recipient.STREAM)
        .values("message__recipient_id")
        .annotate(latest=Max("message_id"))
        .values_list("message__recipient_id", "latest")
    )
    members: dict[int, set[int]] = defaultdict(set)
    for recipient_id, user_profile_id in Subscription.objects.filter(
        recipient_id__in=latest
    ).values_list("recipient_id", "user_profile_id"):
        members[recipient_id].add(user_profile_id)
    return {
        frozenset(members[recipient_id] - {user.id}): message_id
        for recipient_id, message_id in latest.items()
    }


def mirror_direct_message_conversations(user: UserProfile, reference: UserProfile) -> None:
    """Give the user a conversation with the same people as each of the
    reference user's, newest in the same order, all read.  Each missing or
    out-of-order one gets a message, oldest first; it is marked read for
    everyone in the conversation, so nobody is left a notice to answer."""
    wanted = sorted(
        direct_message_conversations(reference).items(), key=lambda conversation: conversation[1]
    )
    have = direct_message_conversations(user)
    in_order = [have.get(people) for people, _ in wanted]
    if all(message_id is not None for message_id in in_order) and in_order == sorted(
        message_id for message_id in in_order if message_id is not None
    ):
        return
    people_by_id = {
        person.id: person
        for person in UserProfile.objects.filter(
            id__in={person_id for people, _ in wanted for person_id in people}
        )
    }
    for people, _ in wanted:
        others = [people_by_id[person_id] for person_id in sorted(people)]
        if len(others) <= 1:
            sender = others[0] if others else user
            message_id = internal_send_private_message(sender, user, MIRROR_CONTENT)
        else:
            message_id = internal_send_group_direct_message(
                user.realm, others[0], MIRROR_CONTENT, recipient_users=[*others[1:], user]
            )
        assert message_id is not None
        UserMessage.objects.filter(message_id=message_id).update(
            flags=F("flags").bitor(UserMessage.flags.read)
        )
