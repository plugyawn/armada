from typing import Any

from django.conf import settings

from zerver.models import Recipient, UserProfile

PEAR_FOLLOWUP_TOPIC_HISTORY_LIMIT = 50


def get_pear_followup_service_bot_event(
    *,
    realm_id: int,
    recipient_id: int | None,
    recipient_type: int,
    stream_name: str | None,
    topic_name: str | None,
    content: str,
    mentioned_user_ids: set[int],
) -> dict[str, Any] | None:
    if (
        not settings.EMBEDDED_BOTS_ENABLED
        or recipient_type != Recipient.STREAM
        or recipient_id is None
        or stream_name is None
        or topic_name is None
    ):
        return None

    from zerver.lib.bot_config import get_bot_configs
    from zerver.lib.pear import (
        PEAR_EMBEDDED_BOT_SERVICE,
        PEAR_FOLLOWUP_TRIGGER,
        PEAR_SELECTED_BOT_CONFIG_KEY,
        PEAR_SELECTED_BOT_CONFIG_VALUE,
        build_pear_active_session_summary_from_messages,
        get_pear_session_payload,
        is_pear_control_template_message,
        is_pear_request_message,
        parse_pear_artifacts,
        parse_pear_checklist,
        pear_worker_should_start,
    )
    from zerver.lib.topic import messages_for_topic
    from zerver.models.bots import Service

    if (
        is_pear_request_message(content)
        or is_pear_control_template_message(content)
        or parse_pear_checklist(content)
        or parse_pear_artifacts(content, message_id=0)
    ):
        return None

    pear_services = list(
        Service.objects.select_related("user_profile")
        .filter(
            name=PEAR_EMBEDDED_BOT_SERVICE,
            user_profile__realm_id=realm_id,
            user_profile__is_active=True,
            user_profile__is_bot=True,
            user_profile__bot_type=UserProfile.EMBEDDED_BOT,
        )
        .order_by("user_profile_id")
    )
    if not pear_services:
        return None

    bot_configs = get_bot_configs([service.user_profile_id for service in pear_services])
    selected_pear_service = next(
        (
            service
            for service in pear_services
            if bot_configs.get(service.user_profile_id, {}).get(
                PEAR_SELECTED_BOT_CONFIG_KEY
            )
            == PEAR_SELECTED_BOT_CONFIG_VALUE
        ),
        pear_services[0],
    )
    if selected_pear_service.user_profile_id in mentioned_user_ids:
        return None

    if settings.PEAR_ADDRESSING_ROUTER_RUNTIME != "disabled":
        return {
            "trigger": PEAR_FOLLOWUP_TRIGGER,
            "user_profile_id": selected_pear_service.user_profile_id,
        }

    message_rows = list(
        messages_for_topic(realm_id, recipient_id, topic_name)
        .order_by("-id")
        .values_list("id", "content", "sender__is_bot")[
            :PEAR_FOLLOWUP_TOPIC_HISTORY_LIMIT
        ]
    )
    active_session = build_pear_active_session_summary_from_messages(
        pear_session=get_pear_session_payload(stream=stream_name, topic=topic_name),
        source="server",
        stream_exists=True,
        messages=message_rows,
    )
    # pear_worker_should_start enforces active_session["response_policy"] so
    # mention-only topics do not queue ambient follow-up workers.
    if not pear_worker_should_start(content, active_session):
        return None

    return {
        "trigger": PEAR_FOLLOWUP_TRIGGER,
        "user_profile_id": selected_pear_service.user_profile_id,
    }
