"""Later and Send when, on the server: a scheduled message or a reminder
can wait for an agent run to end instead of for a time (BRIEF statement
41; design/r5/MICRO-FEATURES.md). Zulip's own scheduled messages and
reminders do everything else: such a message is stored, listed, edited,
cancelled and delivered as theirs are. What is Armada's is the wait
(AgentRunWait), and its release when the run ends."""

from datetime import datetime

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.actions.scheduled_messages import try_deliver_one_scheduled_message
from zerver.lib.armada_run_access import get_visible_armada_worker_run
from zerver.lib.exceptions import (
    DeliveryTimeNotInFutureError,
    IncompatibleParametersError,
    JsonableError,
)
from zerver.lib.request import RequestVariableMissingError
from zerver.lib.timestamp import timestamp_to_datetime
from zerver.models import ScheduledMessage, UserProfile
from zerver.models.bots import AgentRun, AgentRunWait

# A run in these states has not ended; done, failed and cancelled have.
ARMADA_LIVE_RUN_STATUSES = frozenset((AgentRun.QUEUED, AgentRun.RUNNING))


def access_run_to_wait_for(user_profile: UserProfile, run_id: int) -> AgentRun:
    """A run this person may see, which has not ended yet: a message
    waiting for a run that has already ended would never be sent."""
    run = get_visible_armada_worker_run(user_profile, run_id)
    if run.status not in ARMADA_LIVE_RUN_STATUSES:
        raise JsonableError(_("This run has already ended."))
    return run


def check_delivery_condition(
    user_profile: UserProfile,
    scheduled_delivery_timestamp: int | None,
    agent_run_id: int | None,
) -> tuple[datetime | None, AgentRun | None]:
    """When a new scheduled message or reminder is to be sent, as the API
    gives it: at a time in the future, or when a live run ends."""
    if agent_run_id is None:
        if scheduled_delivery_timestamp is None:
            raise RequestVariableMissingError("scheduled_delivery_timestamp")
        deliver_at = timestamp_to_datetime(scheduled_delivery_timestamp)
        if deliver_at <= timezone_now():
            raise DeliveryTimeNotInFutureError
        return deliver_at, None
    if scheduled_delivery_timestamp is not None:
        raise IncompatibleParametersError(["scheduled_delivery_timestamp", "agent_run_id"])
    return None, access_run_to_wait_for(user_profile, agent_run_id)


def release_messages_waiting_for_run(run: AgentRun) -> None:
    """Called in the transaction that first moves a run into done,
    failed or cancelled. Every message waiting for the run becomes due
    now, and its wait is deleted in the same transaction, so a failed run
    that is requeued and then finishes releases nothing a second time.
    Once the transaction commits, the messages are delivered at once.
    Zulip's delivery worker, which looks once a minute, delivers anything
    that this misses."""
    released_ids = list(
        ScheduledMessage.objects.filter(
            agent_run_wait__run=run, delivered=False, failed=False
        ).values_list("id", flat=True)
    )
    AgentRunWait.objects.filter(run=run).delete()
    if not released_ids:
        return
    ScheduledMessage.objects.filter(id__in=released_ids).update(scheduled_timestamp=timezone_now())
    transaction.on_commit(lambda: deliver_released_messages(released_ids), robust=True)


def deliver_released_messages(scheduled_message_ids: list[int]) -> None:
    # Each call delivers one message, or records why it could not, in
    # its own transaction, as the delivery worker does; a message the
    # worker took first is skipped.
    while try_deliver_one_scheduled_message(scheduled_message_ids):
        pass
