from typing import Any

from django.conf import settings
from django.core.cache import cache

from zerver.models import Recipient, UserProfile

PEAR_SELECTED_BOT_CACHE_SECONDS = 60


def pear_selected_bot_cache_key(realm_id: int) -> str:
    return f"pear:selected_bot:{realm_id}"


def invalidate_pear_selected_bot_cache(realm_id: int) -> None:
    cache.delete(pear_selected_bot_cache_key(realm_id))


def get_selected_pear_bot_user_id_for_realm(realm_id: int) -> int | None:
    cached = cache.get(pear_selected_bot_cache_key(realm_id))
    if isinstance(cached, dict) and "user_profile_id" in cached:
        user_profile_id = cached["user_profile_id"]
        if isinstance(user_profile_id, int):
            return user_profile_id
        return None

    from zerver.lib.bot_config import get_bot_configs
    from zerver.lib.pear import (
        PEAR_EMBEDDED_BOT_SERVICE,
        PEAR_SELECTED_BOT_CONFIG_KEY,
        PEAR_SELECTED_BOT_CONFIG_VALUE,
    )
    from zerver.models.bots import Service

    pear_services = list(
        Service.objects.filter(
            name=PEAR_EMBEDDED_BOT_SERVICE,
            user_profile__realm_id=realm_id,
            user_profile__is_active=True,
            user_profile__is_bot=True,
            user_profile__bot_type=UserProfile.EMBEDDED_BOT,
        ).order_by("user_profile_id")
    )
    if not pear_services:
        selected_bot_user_id = None
    else:
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
        selected_bot_user_id = selected_pear_service.user_profile_id

    cache.set(
        pear_selected_bot_cache_key(realm_id),
        {"user_profile_id": selected_bot_user_id},
        timeout=PEAR_SELECTED_BOT_CACHE_SECONDS,
    )
    return selected_bot_user_id


def pear_followup_topic_context_allows_candidate(
    *,
    realm_id: int,
    stream_name: str,
    topic_name: str,
) -> bool:
    from zerver.lib.pear import normalize_pear_response_policy
    from zerver.models.bots import PearTopicContext

    context = (
        PearTopicContext.objects.filter(
            realm_id=realm_id,
            stream_name=stream_name,
            topic_name=topic_name,
        )
        .values("is_paused", "response_policy")
        .first()
    )
    if context is None:
        return False
    if context["is_paused"]:
        return False
    return normalize_pear_response_policy(str(context["response_policy"])) == "ambient"


def get_pear_followup_service_bot_event(
    *,
    realm_id: int,
    recipient_id: int | None,
    recipient_type: int,
    stream_name: str | None,
    topic_name: str | None,
    content: str,
    mentioned_user_ids: set[int],
    pear_followup: bool = False,
) -> dict[str, Any] | None:
    if (
        not settings.EMBEDDED_BOTS_ENABLED
        or recipient_type != Recipient.STREAM
        or recipient_id is None
        or stream_name is None
        or topic_name is None
    ):
        return None

    from zerver.lib.pear import (
        PEAR_FOLLOWUP_TRIGGER,
        is_pear_control_template_message,
        is_pear_plain_mention_alias_message,
        is_pear_request_message,
        parse_pear_artifacts,
        parse_pear_checklist,
    )

    selected_pear_bot_user_id = get_selected_pear_bot_user_id_for_realm(realm_id)
    if selected_pear_bot_user_id is None:
        return None
    if selected_pear_bot_user_id in mentioned_user_ids:
        return None

    if pear_followup:
        return {
            "trigger": PEAR_FOLLOWUP_TRIGGER,
            "user_profile_id": selected_pear_bot_user_id,
        }

    has_plain_pear_alias = is_pear_plain_mention_alias_message(content)
    if (
        (is_pear_request_message(content) and not has_plain_pear_alias)
        or is_pear_control_template_message(content)
        or parse_pear_checklist(content)
        or parse_pear_artifacts(content, message_id=0)
    ):
        return None

    if has_plain_pear_alias:
        return {
            "trigger": PEAR_FOLLOWUP_TRIGGER,
            "user_profile_id": selected_pear_bot_user_id,
        }

    if settings.PEAR_ADDRESSING_ROUTER_RUNTIME == "disabled":
        return None

    if not pear_followup_topic_context_allows_candidate(
        realm_id=realm_id,
        stream_name=stream_name,
        topic_name=topic_name,
    ):
        return None

    return {
        "trigger": PEAR_FOLLOWUP_TRIGGER,
        "user_profile_id": selected_pear_bot_user_id,
    }
