# INTERIM BETA: the second interface at /beta/, served until the
# rebuild's conversation lands; then delete this file with
# web/src/armada/beta/ (see web/src/armada/beta/README.md for the
# whole list).
import orjson
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from zerver.decorator import zulip_login_required
from zerver.lib.pear import PEAR_QUESTION_TIMEOUT_SECONDS
from zerver.lib.stream_subscription import get_subscribed_stream_ids_for_user
from zerver.models import PearWorkerRun, Stream, UserProfile

# The live runs the page is told about. pear/runs takes 20 ids a
# request, so this is a handful of requests at most; a realm with more
# live runs than this has Home show the newest.
ARMADA_BETA_LIVE_RUN_LIMIT = 100


def live_run_ids_for_user(user_profile: UserProfile) -> list[int]:
    """The shared runs that are queued or running in the channels this
    person is subscribed to, newest first. Home reads these (through
    pear/runs?run_ids=...) instead of asking every worktree for its
    runs. A run is placed in a channel the way
    check_can_access_pear_worker_run places it, so every id here is one
    pear/runs will let this person read."""
    subscribed = {
        name.lower()
        for name in Stream.objects.filter(
            id__in=get_subscribed_stream_ids_for_user(user_profile)
        ).values_list("name", flat=True)
    }
    runs = (
        PearWorkerRun.objects.select_related("workspace_node")
        .filter(
            realm_id=user_profile.realm_id,
            session_kind=PearWorkerRun.SHARED,
            status__in=[PearWorkerRun.QUEUED, PearWorkerRun.RUNNING],
        )
        .order_by("-date_created", "-id")
    )
    ids = []
    for run in runs:
        stream_name = (
            run.workspace_node.stream_name
            if run.workspace_node is not None and run.workspace_node.stream_name
            else run.stream_name
        )
        if stream_name.lower() in subscribed:
            ids.append(run.id)
            if len(ids) == ARMADA_BETA_LIVE_RUN_LIMIT:
                break
    return ids


@zulip_login_required
def armada_beta(request: HttpRequest) -> HttpResponse:
    """The new design on this server's real data, for the signed-in
    person. It registers an event queue over the session (/json/register)
    like the old interface, and every write goes through /json/ with the
    session's CSRF token. The page is given two facts it cannot ask for:
    how long a run waits for an answer to its question, and which runs
    are live now."""
    user_profile = request.user
    assert isinstance(user_profile, UserProfile)
    return render(
        request,
        "zerver/armada_beta.html",
        context={
            "doc_root_title": "Armada beta",
            "question_timeout_seconds": PEAR_QUESTION_TIMEOUT_SECONDS,
            "live_run_ids": orjson.dumps(live_run_ids_for_user(user_profile)).decode(),
        },
    )
