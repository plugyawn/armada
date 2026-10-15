"""Realm-wide maps of Pear workspace bindings, for sidebar affordances.

The heavyweight per-topic context lives in zerver.lib.armada_worker; this
module answers only the cheap realm- and stream-scoped questions the web
client needs to decorate its navigation: which channels are bound to a
repository, and which topics in a channel have a worktree.
"""

from zerver.models import Realm
from zerver.models.bots import PearProject, PearWorkspaceNode


def pear_project_stream_ids_for_realm(realm: Realm) -> list[int]:
    return sorted(PearProject.objects.filter(realm=realm).values_list("stream_id", flat=True))


def pear_workspace_topics_for_stream(realm: Realm, stream_name: str) -> list[dict[str, str]]:
    nodes = PearWorkspaceNode.objects.filter(
        realm=realm,
        stream_name__iexact=stream_name,
        lifecycle_status=PearWorkspaceNode.ACTIVE,
    ).order_by("topic_name")
    return [{"topic": node.topic_name, "branch": node.branch_name} for node in nodes]
