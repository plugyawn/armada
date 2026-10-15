from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json, NonNegativeInt

from zerver.lib.armada_read_receipts import (
    ARMADA_READ_RECEIPTS_BATCH_LIMIT,
    get_armada_read_receipts,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.models import UserProfile


@typed_endpoint
def get_armada_read_receipts_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    message_ids: Json[list[NonNegativeInt]],
) -> HttpResponse:
    if len(message_ids) > ARMADA_READ_RECEIPTS_BATCH_LIMIT:
        raise JsonableError(
            _("Too many messages: at most {limit} at a time.").format(
                limit=ARMADA_READ_RECEIPTS_BATCH_LIMIT
            )
        )
    if not user_profile.realm.enable_read_receipts:
        raise JsonableError(_("Read receipts are disabled in this organization."))
    return json_success(
        request, {"read_receipts": get_armada_read_receipts(user_profile, message_ids)}
    )
