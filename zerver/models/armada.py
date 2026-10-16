from django.db import models
from django.db.models import CASCADE, SET_NULL
from django.utils.timezone import now as timezone_now

from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.messages import Attachment
from zerver.models.realms import Realm
from zerver.models.streams import Stream
from zerver.models.users import UserProfile


class Partner(models.Model):
    """A person's partner: the creature they chose as their face, and the
    experience that levels it up (zerver/lib/partner.py, which is the
    only writer). Levels are computed from xp when read, never stored."""

    user_profile = models.OneToOneField(UserProfile, on_delete=CASCADE, related_name="+")
    realm = models.ForeignKey(Realm, on_delete=CASCADE, related_name="+")

    # Species are the pack's ids (species.json in the sprite directory).
    species = models.CharField(max_length=16, null=True)
    chosen = models.CharField(max_length=16, null=True)
    shiny = models.BooleanField(default=False)
    held = models.BooleanField(default=False)
    later = models.BooleanField(default=False)
    chosen_at = models.DateTimeField(null=True)
    evolved_at = models.DateTimeField(null=True)
    evolved_kind = models.CharField(max_length=16, null=True)

    xp = models.PositiveIntegerField(default=0)
    active_days = models.PositiveIntegerField(default=0)
    # The open day, in the person's time zone, and what it has counted
    # against the day's caps.
    day = models.DateField(null=True)
    day_active = models.BooleanField(default=False)
    day_messages = models.PositiveIntegerField(default=0)
    day_runs = models.PositiveIntegerField(default=0)
    day_reactions = models.PositiveIntegerField(default=0)

    # A fold reads only what is past these.
    message_watermark = models.BigIntegerField(default=0)
    reaction_watermark = models.BigIntegerField(default=0)
    runs_watermark = models.DateTimeField(null=True)
    folded_at = models.DateTimeField(null=True)


class MessageClientKey(models.Model):
    """A send's client key (POST /json/messages with client_key), kept a
    day so that a repeat of the send returns the first message instead of
    sending it again (zerver/lib/armada_client_keys.py)."""

    user_profile = models.ForeignKey(UserProfile, on_delete=CASCADE, related_name="+")
    client_key = models.CharField(max_length=64)
    # Set in the transaction that claims the key, so a committed row
    # always has it. Not a foreign key: a repeat answers with the first
    # message's id even after that message is deleted.
    message_id = models.BigIntegerField(null=True)
    date_created = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_profile", "client_key"], name="zerver_messageclientkey_user_key"
            ),
        ]


class Page(models.Model):
    """A person's page: a short bio over their posts
    (zerver/lib/armada_pages.py). A person with no row has an empty
    bio."""

    MAX_BIO_LENGTH = 280

    user_profile = models.OneToOneField(UserProfile, on_delete=CASCADE, related_name="+")
    realm = models.ForeignKey(Realm, on_delete=CASCADE, related_name="+")
    bio = models.CharField(max_length=MAX_BIO_LENGTH, default="")
    date_updated = models.DateTimeField(default=timezone_now)


class Post(models.Model):
    """A post on its author's page, in Markdown and rendered as messages
    are. A draft is seen only by its author."""

    MAX_TITLE_LENGTH = 200
    MAX_CONTENT_LENGTH = 50000

    realm = models.ForeignKey(Realm, on_delete=CASCADE, related_name="+")
    author = models.ForeignKey(UserProfile, on_delete=CASCADE, related_name="+")
    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    content = models.TextField()
    rendered_content = models.TextField()
    draft = models.BooleanField(default=False)
    # Drafted by the agent for its author, who publishes it or not.
    drafted_by_agent = models.BooleanField(default=False)
    date_created = models.DateTimeField(default=timezone_now)
    date_updated = models.DateTimeField(default=timezone_now)
    # The uploads its content links to, claimed as a message claims
    # its own: whoever may read the post may read them, and they are
    # not unclaimed while it holds them.
    attachments = models.ManyToManyField(Attachment, related_name="armada_posts")

    class Meta:
        indexes = [
            models.Index(
                fields=["author", "-date_created"],
                name="zerver_post_author_created",
            ),
        ]

    def is_visible_to(self, user_id: int) -> bool:
        """visible_posts (zerver/lib/armada_pages.py) for one post: a
        published post, or a draft of the person's own."""
        return not self.draft or self.author_id == user_id


class PluginInstall(models.Model):
    """A plugin installed in a realm (design/r9/plugins): a repository at a
    pinned commit, with its manifest, the settings the team gave it, its own
    bot, and the state the plugin host reports. Everything a plugin
    registers hangs from this row, so deleting it takes everything back
    (zerver/lib/armada_plugins.py is the only writer, the host aside)."""

    # A required setting or secret is missing.
    WAITING = 1
    STARTING = 2
    RUNNING = 3
    # Five failures in a row, as the plugin host counts them.
    FAILED = 4
    # Kept for a week, so that Remove can be undone, then purged.
    REMOVED = 5
    # Being made or checked (ROUND2.md): only its maker sees it, it runs
    # only in a trial, and it holds its id.
    DRAFT = 6
    STATE_NAMES = {
        WAITING: "waiting",
        STARTING: "starting",
        RUNNING: "running",
        FAILED: "failed",
        REMOVED: "removed",
        DRAFT: "draft",
    }

    # Where its code comes from: a repository on GitHub, a repository on
    # this server (the team's plugins repository), or nowhere, for a
    # plugin its author runs elsewhere.
    GITHUB = "github"
    LOCAL = "local"
    REMOTE = "remote"
    SOURCE_KINDS = [GITHUB, LOCAL, REMOTE]

    realm = models.ForeignKey(Realm, on_delete=CASCADE, related_name="+")
    # The manifest's id, as "wandb".
    plugin_id = models.CharField(max_length=32)
    name = models.CharField(max_length=40)
    version = models.CharField(max_length=32)
    source_kind = models.CharField(
        max_length=8, choices=[(kind, kind) for kind in SOURCE_KINDS], default=GITHUB
    )
    # "github.com/<owner>/<repo>"; a remote plugin's address; for a local
    # source with no repository row, a path on disk, where
    # ARMADA_PLUGIN_LOCAL_SOURCES allows it; "" for the team's repository.
    source = models.CharField(max_length=300, default="")
    source_repository = models.ForeignKey(
        "zerver.Repository", on_delete=SET_NULL, null=True, related_name="+"
    )
    # The plugin's directory in its repository, as "plugins/eval-queue";
    # "" for the repository's root.
    source_path = models.CharField(max_length=200, default="")
    # "" for a remote plugin, which has no code here to pin.
    sha = models.CharField(max_length=40, default="")
    manifest = models.JSONField()
    settings = models.JSONField(default=dict)
    state = models.PositiveSmallIntegerField(default=STARTING)
    # The missing names while waiting; the last lines when it failed.
    state_detail = models.TextField(default="")
    failures = models.PositiveSmallIntegerField(default=0)
    # On 127.0.0.1, given by the host at the first start and kept.
    port = models.PositiveIntegerField(null=True, unique=True)
    # The token Codex and Armada present to the plugin is derived from this
    # and the server's key (armada_plugin_token), and never stored.
    token_salt = models.CharField(max_length=32)
    bot = models.OneToOneField(UserProfile, on_delete=CASCADE, related_name="+")
    # The person the install was done for: by hand, or by the agent.
    installed_by = models.ForeignKey(UserProfile, on_delete=SET_NULL, null=True, related_name="+")
    # Who asked the agent to make it, in which run and from which thread;
    # no maker means it came from the store.
    made_by = models.ForeignKey(UserProfile, on_delete=SET_NULL, null=True, related_name="+")
    made_by_run = models.ForeignKey(
        "zerver.AgentRun", on_delete=SET_NULL, null=True, related_name="+"
    )
    made_in_stream = models.ForeignKey(Stream, on_delete=SET_NULL, null=True, related_name="+")
    made_in_topic = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH, null=True)
    made_in_message_id = models.BigIntegerField(null=True)
    disabled = models.BooleanField(default=False)
    # The team's default for each of its widgets: {key: {"nav": "shown" |
    # "offered" | "off", "column": bool}}. A person's own choices are
    # their preferences, not this.
    widget_defaults = models.JSONField(default=dict)
    # The tools the team turned off for the agent.
    disabled_tools = models.JSONField(default=list)
    # Raised by every change the running process must be restarted for.
    generation = models.PositiveIntegerField(default=1)
    date_installed = models.DateTimeField(default=timezone_now)
    date_updated = models.DateTimeField(default=timezone_now)
    date_removed = models.DateTimeField(null=True)
    # When the host last saw it come up, and when it first pushed a widget
    # after that: Recent's "Started" line (zerver/lib/armada_widgets.py).
    date_started = models.DateTimeField(null=True)
    date_fed = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "plugin_id"], name="zerver_plugininstall_realm_plugin"
            ),
        ]


class PluginSecret(models.Model):
    """A secret an install's manifest asks for, encrypted with the server's
    plugin key. Only the plugin host decrypts it, into that plugin's
    environment; people see its last four characters."""

    install = models.ForeignKey(PluginInstall, on_delete=CASCADE, related_name="+")
    # The manifest's name for it, as "api_key".
    name = models.CharField(max_length=32)
    ciphertext = models.TextField()
    # The last four characters of a long secret, or "".
    hint = models.CharField(max_length=4, default="")
    set_by = models.ForeignKey(UserProfile, on_delete=SET_NULL, null=True, related_name="+")
    date_set = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["install", "name"], name="zerver_pluginsecret_name"),
        ]


class PluginLink(models.Model):
    """A value the plugin understands (a W&B project, a pool of machines),
    joined to a channel, or to one topic of it."""

    install = models.ForeignKey(PluginInstall, on_delete=CASCADE, related_name="+")
    # The manifest's name for the kind of link, as "project".
    kind = models.CharField(max_length=32)
    value = models.CharField(max_length=200)
    stream = models.ForeignKey(Stream, on_delete=CASCADE, related_name="+")
    # None links the whole channel, every worktree under it.
    topic = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH, null=True)
    created_by = models.ForeignKey(UserProfile, on_delete=SET_NULL, null=True, related_name="+")
    date_created = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["install", "kind", "value", "stream", "topic"],
                name="zerver_pluginlink_place",
                nulls_distinct=False,
            ),
        ]


class PluginWidgetState(models.Model):
    """The latest document a plugin pushed for one of its widgets, in one
    scope: the nav (no link), a link's place, or an item opened from a
    list (zerver/lib/armada_widgets.py)."""

    install = models.ForeignKey(PluginInstall, on_delete=CASCADE, related_name="+")
    # The manifest's key for the widget, as "runs".
    widget = models.CharField(max_length=32)
    link = models.ForeignKey(PluginLink, on_delete=CASCADE, null=True, related_name="+")
    # A list row's key, for a widget opened from that row; "" otherwise.
    item = models.CharField(max_length=64, default="")
    document = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    date_fed = models.DateTimeField(default=timezone_now)
    # When the last armada_widget event for it went, and whether a newer
    # document waits for its two seconds to pass.
    date_sent = models.DateTimeField(null=True)
    pending = models.BooleanField(default=False)
    # Not fed for three times its cadence.
    stale = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["install", "widget", "link", "item"],
                name="zerver_pluginwidgetstate_scope",
                nulls_distinct=False,
            ),
        ]
