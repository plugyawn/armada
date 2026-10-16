"""Who may see an agent run: the one rule that the run views, Later and
Send when (zerver/lib/armada_later.py) and the agent's tools share."""

from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.streams import access_stream_by_name
from zerver.models import UserProfile
from zerver.models.bots import AgentRun


def check_can_access_armada_worker_run(
    user_profile: UserProfile,
    run: AgentRun,
    *,
    accessible_streams: set[str] | None = None,
) -> None:
    """Raises unless this person may see the run. accessible_streams holds
    the channels already checked for this person, by upper-cased name, so
    that runs of one channel cost one check."""
    if run.realm_id != user_profile.realm_id:
        raise JsonableError(_("Run not found."))
    if run.session_kind == AgentRun.SIDE:
        if run.requesting_user_id != user_profile.id:
            raise JsonableError(_("Run not found."))
        return
    stream_name = (
        run.workspace_node.stream_name
        if run.workspace_node is not None and run.workspace_node.stream_name
        else run.stream_name
    )
    if accessible_streams is not None and stream_name.upper() in accessible_streams:
        return
    access_stream_by_name(user_profile, stream_name)
    if accessible_streams is not None:
        accessible_streams.add(stream_name.upper())


def get_visible_armada_worker_run(
    user_profile: UserProfile,
    run_id: int,
) -> AgentRun:
    try:
        run = AgentRun.objects.select_related(
            "bot_profile",
            "requesting_user",
            "workspace_node",
        ).get(id=run_id, realm=user_profile.realm)
    except AgentRun.DoesNotExist as error:
        raise JsonableError(_("Run not found.")) from error
    check_can_access_armada_worker_run(user_profile, run)
    return run
