"""A plugin asking the agent (design/r9/plugins, ROUND2 section C):
POST /api/v1/armada/runs, for an install's own bot.

A plugin's post never starts a run, because Zulip drops service events from
bot senders. So a plugin that wants the agent to look at something, such as
W&B's "Ask Onix about a crash", asks here. The run acts for the person who
installed the plugin, with that person's rights, and only in a place the
plugin is linked to: the bot acts within what the design gives it.
"""

from django.db.models import Q
from django.utils.translation import gettext as _

from zerver.lib.addressee import Addressee
from zerver.lib.armada_gate import get_selected_armada_bot_user_id_for_realm
from zerver.lib.armada_message_tools import ArmadaWhere, start_armada_run
from zerver.lib.exceptions import JsonableError
from zerver.models import PluginInstall, PluginLink, UserProfile

# A brief is a request, not a document.
ARMADA_PLUGIN_RUN_BRIEF_LIMIT = 4000


def start_plugin_run(bot: UserProfile, *, stream_id: int, topic: str, brief: str) -> int:
    """Start a run for the person who installed bot's plugin, in a linked
    place. Returns the brief's message id."""
    install = (
        PluginInstall.objects.select_related("installed_by", "realm")
        .filter(bot=bot, realm=bot.realm)
        .first()
    )
    if install is None:
        raise JsonableError(
            _("Only a plugin's bot starts a run here; people ask the agent with a message.")
        )
    if install.disabled or install.state in (PluginInstall.REMOVED, PluginInstall.DRAFT):
        raise JsonableError(_("{name} is turned off.").format(name=install.name))
    asker = install.installed_by
    if asker is None or not asker.is_active:
        raise JsonableError(
            _("Nobody who installed {name} can ask for it any more.").format(name=install.name)
        )
    brief = brief.strip()
    if not brief or len(brief) > ARMADA_PLUGIN_RUN_BRIEF_LIMIT:
        raise JsonableError(
            _("A brief is between 1 and {limit} characters.").format(
                limit=ARMADA_PLUGIN_RUN_BRIEF_LIMIT
            )
        )
    # A channel's link holds for every topic in it; a topic's for that topic.
    link = (
        PluginLink.objects.select_related("stream")
        .filter(install=install, stream_id=stream_id)
        .filter(Q(topic__isnull=True) | Q(topic__iexact=topic.strip()))
        .first()
    )
    if link is None:
        raise JsonableError(
            _("{name} is not linked there, so it cannot ask the agent there.").format(
                name=install.name
            )
        )
    agent_id = get_selected_armada_bot_user_id_for_realm(install.realm_id)
    if agent_id is None:
        raise JsonableError(_("This organization has no agent to ask."))
    agent = UserProfile.objects.get(id=agent_id)
    where = ArmadaWhere(
        stream=link.stream,
        topic=Addressee.for_stream(link.stream, topic.strip()).topic_name(),
        root=None,
    )
    origin = _("Asked by {plugin} for {person}.").format(
        plugin=f"@_**{bot.full_name}|{bot.id}**",
        person=f"@_**{asker.full_name}|{asker.id}**",
    )
    return start_armada_run(asker, agent, where, f"{brief}\n\n{origin}")
