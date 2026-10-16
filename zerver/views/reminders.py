from django.http import HttpRequest, HttpResponse
from pydantic import Json, NonNegativeInt

from zerver.actions.reminders import do_delete_reminder, schedule_reminder_for_message
from zerver.lib.armada_later import check_delivery_condition
from zerver.lib.reminders import access_reminder
from zerver.lib.request import RequestNotes
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import UserProfile


@typed_endpoint
def create_reminders_message_backend(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    message_id: Json[int],
    scheduled_delivery_timestamp: Json[int] | None = None,
    agent_run_id: Json[int] | None = None,
    note: str | None = None,
) -> HttpResponse:
    deliver_at, agent_run = check_delivery_condition(
        user_profile, scheduled_delivery_timestamp, agent_run_id
    )

    client = RequestNotes.get_notes(request).client
    assert client is not None

    reminder_id = schedule_reminder_for_message(
        user_profile,
        client,
        message_id,
        deliver_at,
        note=note or "",
        agent_run=agent_run,
    )
    return json_success(request, data={"reminder_id": reminder_id})


@typed_endpoint
def delete_reminder(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    reminder_id: PathOnly[NonNegativeInt],
) -> HttpResponse:
    reminder = access_reminder(user_profile, reminder_id)
    do_delete_reminder(user_profile, reminder)
    return json_success(request)
