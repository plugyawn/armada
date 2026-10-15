from typing import Any

from django.conf import settings
from django.core.cache import cache

from zerver.models import Recipient, UserProfile

ARMADA_SELECTED_BOT_CACHE_SECONDS = 60


def armada_selected_bot_cache_key(realm_id: int) -> str:
    return f"armada:selected_bot:{realm_id}"


def invalidate_armada_selected_bot_cache(realm_id: int) -> None:
    cache.delete(armada_selected_bot_cache_key(realm_id))


def get_selected_armada_bot_user_id_for_realm(realm_id: int) -> int | None:
    cached = cache.get(armada_selected_bot_cache_key(realm_id))
    if isinstance(cached, dict) and "user_profile_id" in cached:
        user_profile_id = cached["user_profile_id"]
        if isinstance(user_profile_id, int):
            return user_profile_id
        return None

    from zerver.lib.armada import (
        ARMADA_EMBEDDED_BOT_SERVICE,
        ARMADA_SELECTED_BOT_CONFIG_KEY,
        ARMADA_SELECTED_BOT_CONFIG_VALUE,
    )
    from zerver.lib.bot_config import get_bot_configs
    from zerver.models.bots import Service

    armada_services = list(
        Service.objects.filter(
            name=ARMADA_EMBEDDED_BOT_SERVICE,
            user_profile__realm_id=realm_id,
            user_profile__is_active=True,
            user_profile__is_bot=True,
            user_profile__bot_type=UserProfile.EMBEDDED_BOT,
        ).order_by("user_profile_id")
    )
    if not armada_services:
        selected_bot_user_id = None
    else:
        bot_configs = get_bot_configs([service.user_profile_id for service in armada_services])
        selected_armada_service = next(
            (
                service
                for service in armada_services
                if bot_configs.get(service.user_profile_id, {}).get(ARMADA_SELECTED_BOT_CONFIG_KEY)
                == ARMADA_SELECTED_BOT_CONFIG_VALUE
            ),
            armada_services[0],
        )
        selected_bot_user_id = selected_armada_service.user_profile_id

    cache.set(
        armada_selected_bot_cache_key(realm_id),
        {"user_profile_id": selected_bot_user_id},
        timeout=ARMADA_SELECTED_BOT_CACHE_SECONDS,
    )
    return selected_bot_user_id


def armada_followup_topic_context_allows_candidate(
    *,
    realm_id: int,
    stream_name: str,
    topic_name: str,
) -> bool:
    from zerver.lib.armada import armada_topic_scope, normalize_armada_response_policy
    from zerver.models.bots import Workspace

    stream_name, topic_name = armada_topic_scope(stream_name, topic_name)
    workspace = (
        Workspace.objects.filter(
            realm_id=realm_id,
            stream_name__iexact=stream_name,
            topic_name__iexact=topic_name,
        )
        .values("is_paused", "response_policy")
        .first()
    )
    if workspace is None:
        # Ambient is the product default. A topic row is created only after
        # Pear has state worth persisting, so a missing row must not make a
        # brand-new topic silently mentions-only.
        return True
    if workspace["is_paused"]:
        return False
    return normalize_armada_response_policy(str(workspace["response_policy"])) == "ambient"


def get_armada_followup_service_bot_event(
    *,
    realm_id: int,
    recipient_id: int | None,
    recipient_type: int,
    stream_name: str | None,
    topic_name: str | None,
    content: str,
    mentioned_user_ids: set[int],
    agent_followup: bool = False,
) -> dict[str, Any] | None:
    if (
        not settings.EMBEDDED_BOTS_ENABLED
        or recipient_type != Recipient.STREAM
        or recipient_id is None
        or stream_name is None
        or topic_name is None
    ):
        return None

    from zerver.lib.armada import (
        ARMADA_FOLLOWUP_TRIGGER,
        is_armada_plain_mention_alias_message,
        is_armada_request_message,
    )

    selected_armada_bot_user_id = get_selected_armada_bot_user_id_for_realm(realm_id)
    if selected_armada_bot_user_id is None:
        return None
    if selected_armada_bot_user_id in mentioned_user_ids:
        return None

    if agent_followup:
        return {
            "trigger": ARMADA_FOLLOWUP_TRIGGER,
            "user_profile_id": selected_armada_bot_user_id,
        }

    has_plain_armada_alias = is_armada_plain_mention_alias_message(content)
    if is_armada_request_message(content) and not has_plain_armada_alias:
        return None

    if has_plain_armada_alias:
        return {
            "trigger": ARMADA_FOLLOWUP_TRIGGER,
            "user_profile_id": selected_armada_bot_user_id,
        }

    if settings.ARMADA_ADDRESSING_ROUTER_RUNTIME == "disabled":
        return None

    if not armada_followup_topic_context_allows_candidate(
        realm_id=realm_id,
        stream_name=stream_name,
        topic_name=topic_name,
    ):
        return None

    return {
        "trigger": ARMADA_FOLLOWUP_TRIGGER,
        "user_profile_id": selected_armada_bot_user_id,
    }
