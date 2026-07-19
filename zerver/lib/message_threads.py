from typing import Any

from django.db.models import (
    BooleanField,
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce, RowNumber
from django.db.models.query import QuerySet

from zerver.lib.avatar import get_avatar_field, get_avatar_for_inaccessible_user
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.lib.user_groups import get_recursive_membership_groups
from zerver.lib.users import get_inaccessible_user_ids
from zerver.models import Message, Recipient, Stream, Subscription, UserMessage, UserProfile

MESSAGE_THREAD_PARTICIPANT_LIMIT = 4
THREAD_ROOT_ACCESS_ALIAS = "viewer_can_access_thread_root"


def _stream_access_filters(user_profile: UserProfile) -> Q:
    stream_access_filters = Q(pk__in=[])
    if user_profile.can_access_public_streams():
        stream_access_filters |= Q(invite_only=False)
    stream_access_filters |= Q(
        recipient__in=Subscription.objects.filter(
            user_profile=user_profile,
            active=True,
        ).values("recipient")
    )
    if not user_profile.is_guest:
        recursive_group_ids = get_recursive_membership_groups(user_profile).values("id")
        stream_access_filters |= Q(can_subscribe_group_id__in=recursive_group_ids) | Q(
            can_add_subscribers_group_id__in=recursive_group_ids
        )
    return stream_access_filters


def _annotate_viewer_message_access(
    query: QuerySet[Message],
    user_profile: UserProfile,
    *,
    message_id_field: str,
    alias: str,
) -> QuerySet[Message]:
    """Annotate access to a message identified by a field on each query row.

    Thread roots and replies use the same destination, enforced at send time.
    Thus the row recipient supplies the stream policy, while the requested
    message field supplies the UserMessage boundary for protected history.
    """
    has_usermessage_alias = f"{alias}_has_usermessage"
    has_public_stream_alias = f"{alias}_has_public_stream"
    has_shared_history_alias = f"{alias}_has_shared_history"
    has_current_stream_alias = f"{alias}_has_current_stream"

    accessible_stream = Stream.objects.filter(recipient=OuterRef("recipient")).filter(
        _stream_access_filters(user_profile)
    )
    query = query.alias(
        **{
            has_usermessage_alias: Exists(
                UserMessage.objects.filter(
                    user_profile_id=user_profile.id,
                    message_id=OuterRef(message_id_field),
                )
            ),
            has_public_stream_alias: Exists(
                Stream.objects.filter(
                    recipient=OuterRef("recipient"),
                    invite_only=False,
                )
            ),
            has_shared_history_alias: Exists(
                accessible_stream.filter(history_public_to_subscribers=True)
            ),
            has_current_stream_alias: Exists(accessible_stream),
        }
    )
    has_usermessage = Q(**{has_usermessage_alias: True})
    stream_access = Q(**{has_shared_history_alias: True}) | (
        Q(**{has_current_stream_alias: True}) & has_usermessage
    )
    if user_profile.can_access_public_streams():
        stream_access |= Q(**{has_public_stream_alias: True})
    access = (Q(recipient__type=Recipient.STREAM) & stream_access) | (
        ~Q(recipient__type=Recipient.STREAM) & has_usermessage
    )
    return query.alias(
        **{
            alias: Case(
                When(access, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        }
    )


def annotate_thread_root_access(
    query: QuerySet[Message], user_profile: UserProfile
) -> QuerySet[Message]:
    """Annotate whether the viewer may see each reply's canonical root."""
    return _annotate_viewer_message_access(
        query,
        user_profile,
        message_id_field="thread_root_id",
        alias=THREAD_ROOT_ACCESS_ALIAS,
    )


def message_ids_with_accessible_thread_roots(
    message_ids: list[int], user_profile: UserProfile
) -> set[int]:
    if not message_ids:
        return set()
    query = annotate_thread_root_access(
        Message.objects.filter(id__in=message_ids, thread_root__isnull=False),
        user_profile,
    )
    return set(query.filter(**{THREAD_ROOT_ACCESS_ALIAS: True}).values_list("id", flat=True))


def user_ids_without_thread_root_access_for_delivery(
    *,
    thread_root_message_id: int | None,
    stream: Stream | None,
    user_ids: set[int],
) -> set[int]:
    """Return live recipients for whom a reply root is protected history.

    Direct-message recipients are fixed and therefore have root access. Live
    channel recipients have current channel access. Public channels and
    private channels with shared history expose the root normally; only
    protected private channels require the root's UserMessage row.
    """
    if thread_root_message_id is None or not user_ids or stream is None:
        return set()
    if stream.is_public() or stream.history_public_to_subscribers:
        return set()
    accessible_user_ids = set(
        UserMessage.objects.filter(
            message_id=thread_root_message_id,
            user_profile_id__in=user_ids,
        ).values_list("user_profile_id", flat=True)
    )
    return user_ids - accessible_user_ids


def _serialize_thread_user(
    row: dict[str, Any], inaccessible_user_ids: set[int]
) -> dict[str, object]:
    if row["sender_id"] in inaccessible_user_ids:
        return {
            "user_id": row["sender_id"],
            "full_name": str(UserProfile.INACCESSIBLE_USER_NAME),
            "avatar_url": get_avatar_for_inaccessible_user(),
        }

    return {
        "user_id": row["sender_id"],
        "full_name": row["sender__full_name"],
        "avatar_url": get_avatar_field(
            user_id=row["sender_id"],
            realm_id=row["sender__realm_id"],
            email=row["sender__delivery_email"],
            avatar_source=row["sender__avatar_source"],
            avatar_version=row["sender__avatar_version"],
            medium=False,
            client_gravatar=False,
        ),
    }


def _visible_thread_replies(
    message_ids: list[int], user_profile: UserProfile | None
) -> QuerySet[Message]:
    replies = Message.objects.filter(thread_root_id__in=message_ids)
    if user_profile is None:
        return replies
    access_alias = "viewer_can_access_thread_reply"
    return _annotate_viewer_message_access(
        replies,
        user_profile,
        message_id_field="id",
        alias=access_alias,
    ).filter(**{access_alias: True})


def bulk_message_thread_summaries(
    message_ids: list[int],
    *,
    user_profile: UserProfile | None = None,
) -> dict[int, dict[str, object]]:
    """Derive viewer-safe summaries directly from ordinary reply Messages."""
    if not message_ids:
        return {}

    sender_fields = [
        "sender_id",
        "sender__full_name",
        "sender__delivery_email",
        "sender__avatar_source",
        "sender__avatar_version",
        "sender__realm_id",
    ]
    replies = _visible_thread_replies(message_ids, user_profile)
    latest_query = replies.annotate(
        reply_count=Window(Count("id"), partition_by=F("thread_root_id")),
        reply_rank=Window(
            RowNumber(),
            partition_by=F("thread_root_id"),
            order_by=F("id").desc(),
        ),
    )
    latest_fields = ["thread_root_id", "date_sent", "reply_count", *sender_fields]
    if user_profile is not None:
        unread_replies = (
            UserMessage.objects.filter(
                user_profile_id=user_profile.id,
                message__thread_root_id=OuterRef("thread_root_id"),
                flags__andz=UserMessage.flags.read.mask,
            )
            .values("message__thread_root_id")
            .annotate(count=Count("id"))
            .values("count")
        )
        latest_query = latest_query.annotate(
            unread_reply_count=Coalesce(
                Subquery(unread_replies, output_field=IntegerField()),
                Value(0),
            )
        )
        latest_fields.append("unread_reply_count")
    latest_replies = list(latest_query.filter(reply_rank=1).values(*latest_fields))

    # Rank the most-recent contribution from each sender, then keep only the
    # four most-recent unique participants per root.  This preserves the
    # bounded Slack-like facepile without a denormalized participant list.
    participant_rows = list(
        replies.values("thread_root_id", *sender_fields)
        .annotate(latest_reply_id=Max("id"))
        .annotate(
            participant_rank=Window(
                RowNumber(),
                partition_by=F("thread_root_id"),
                order_by=F("latest_reply_id").desc(),
            )
        )
        .filter(participant_rank__lte=MESSAGE_THREAD_PARTICIPANT_LIMIT)
        .order_by("thread_root_id", "participant_rank")
    )
    inaccessible_user_ids = get_inaccessible_user_ids(
        list({row["sender_id"] for row in [*latest_replies, *participant_rows]}),
        user_profile,
    )
    participants_by_root: dict[int, list[dict[str, object]]] = {}
    for row in participant_rows:
        participants_by_root.setdefault(row["thread_root_id"], []).append(
            _serialize_thread_user(row, inaccessible_user_ids)
        )

    summaries: dict[int, dict[str, object]] = {}
    for row in latest_replies:
        root_id = row["thread_root_id"]
        summary: dict[str, object] = {
            "reply_count": row["reply_count"],
            "last_reply_at": datetime_to_timestamp(row["date_sent"]),
            "last_sender": _serialize_thread_user(row, inaccessible_user_ids),
            "participants": participants_by_root.get(root_id, []),
        }
        if user_profile is not None:
            summary["unread_reply_count"] = row["unread_reply_count"]
        summaries[root_id] = summary
    return summaries
