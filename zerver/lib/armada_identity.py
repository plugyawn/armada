from collections.abc import Iterable

from zerver.lib.armada import PEAR_EMBEDDED_BOT_SERVICE
from zerver.models import Realm, UserProfile
from zerver.models.bots import Service


def pear_bot_user_ids_for_realm(realm: Realm) -> list[int]:
    return list(
        Service.objects.filter(
            name=PEAR_EMBEDDED_BOT_SERVICE,
            user_profile__realm=realm,
            user_profile__is_active=True,
            user_profile__bot_type=UserProfile.EMBEDDED_BOT,
        )
        .order_by("user_profile_id")
        .values_list("user_profile_id", flat=True)
    )


def pear_bot_user_ids_from_user_ids(user_ids: Iterable[int]) -> set[int]:
    user_id_set = set(user_ids)
    if not user_id_set:
        return set()

    return set(
        Service.objects.filter(
            name=PEAR_EMBEDDED_BOT_SERVICE,
            user_profile_id__in=user_id_set,
            user_profile__is_active=True,
            user_profile__bot_type=UserProfile.EMBEDDED_BOT,
        ).values_list("user_profile_id", flat=True)
    )


def pear_bot_user_id_has_pear_service(user_id: int) -> bool:
    return user_id in pear_bot_user_ids_from_user_ids([user_id])
