import uuid

from django.db import models
from django.db.models import CASCADE, SET_NULL
from django.utils.timezone import now as timezone_now

from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.users import UserProfile

# Interfaces for services
# They provide additional functionality like parsing message to obtain query URL, data to be sent to URL,
# and parsing the response.
GENERIC_INTERFACE = "GenericService"
SLACK_INTERFACE = "SlackOutgoingWebhookService"


# A Service corresponds to either an outgoing webhook bot or an embedded bot.
# The type of Service is determined by the bot_type field of the referenced
# UserProfile.
#
# If the Service is an outgoing webhook bot:
# - name is any human-readable identifier for the Service
# - base_url is the address of the third-party site
# - token is used for authentication with the third-party site
#
# If the Service is an embedded bot:
# - name is the canonical name for the type of bot (e.g. 'xkcd' for an instance
#   of the xkcd bot); multiple embedded bots can have the same name, but all
#   embedded bots with the same name will run the same code
# - base_url and token are currently unused
class Service(models.Model):
    name = models.CharField(max_length=UserProfile.MAX_NAME_LENGTH)
    # Bot user corresponding to the Service.  The bot_type of this user
    # determines the type of service.  If non-bot services are added later,
    # user_profile can also represent the owner of the Service.
    user_profile = models.ForeignKey(UserProfile, on_delete=CASCADE)
    base_url = models.TextField()
    token = models.TextField()
    # Interface / API version of the service.
    interface = models.PositiveSmallIntegerField(default=1)

    # Valid interfaces are {generic, zulip_bot_service, slack}
    GENERIC = 1
    SLACK = 2

    ALLOWED_INTERFACE_TYPES = [
        GENERIC,
        SLACK,
    ]
    # N.B. If we used Django's choice=... we would get this for free (kinda)
    _interfaces: dict[int, str] = {
        GENERIC: GENERIC_INTERFACE,
        SLACK: SLACK_INTERFACE,
    }

    def interface_name(self) -> str:
        # Raises KeyError if invalid
        return self._interfaces[self.interface]


def get_bot_services(user_profile_id: int) -> list[Service]:
    return list(Service.objects.filter(user_profile_id=user_profile_id))


def get_service_profile(user_profile_id: int, service_name: str) -> Service:
    return Service.objects.get(user_profile_id=user_profile_id, name=service_name)


class BotStorageData(models.Model):
    bot_profile = models.ForeignKey(UserProfile, on_delete=CASCADE)
    key = models.TextField(db_index=True)
    value = models.TextField()

    class Meta:
        unique_together = ("bot_profile", "key")


class BotConfigData(models.Model):
    bot_profile = models.ForeignKey(UserProfile, on_delete=CASCADE)
    key = models.TextField(db_index=True)
    value = models.TextField()

    class Meta:
        unique_together = ("bot_profile", "key")


class PearWorkerRun(models.Model):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    SHARED = "shared"
    SIDE = "side"

    STATUS_CHOICES = [
        (QUEUED, "queued"),
        (RUNNING, "running"),
        (DONE, "done"),
        (FAILED, "failed"),
        (CANCELLED, "cancelled"),
    ]

    SESSION_KIND_CHOICES = [
        (SHARED, "shared"),
        (SIDE, "side"),
    ]

    realm = models.ForeignKey("zerver.Realm", on_delete=CASCADE)
    bot_profile = models.ForeignKey(UserProfile, on_delete=CASCADE)
    requesting_user = models.ForeignKey(
        UserProfile,
        null=True,
        on_delete=SET_NULL,
        related_name="pear_requested_runs",
    )
    trigger_message_id = models.IntegerField()
    stream_name = models.CharField(max_length=60)
    topic_name = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH)
    session_kind = models.CharField(
        max_length=16, choices=SESSION_KIND_CHOICES, default=SHARED, db_index=True
    )
    source_stream_name = models.CharField(max_length=60, blank=True)
    source_topic_name = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH, blank=True)
    source_message_id = models.IntegerField(null=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, db_index=True)
    summary = models.TextField(blank=True)
    current_activity = models.TextField(blank=True)
    runtime_label = models.TextField(blank=True)
    model = models.TextField(blank=True)
    sandbox = models.CharField(max_length=64, blank=True)
    approval_policy = models.CharField(max_length=64, blank=True)
    workdir = models.TextField(blank=True)
    workspace_node = models.ForeignKey(
        "zerver.PearWorkspaceNode",
        null=True,
        on_delete=SET_NULL,
        related_name="worker_runs",
    )
    codex_thread_id = models.TextField(blank=True)
    result_message_id = models.IntegerField(null=True)
    error = models.TextField(null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    heartbeat_at = models.DateTimeField(null=True)
    cancel_requested_at = models.DateTimeField(null=True)
    date_created = models.DateTimeField(default=timezone_now, db_index=True)
    date_updated = models.DateTimeField(default=timezone_now)
    date_finished = models.DateTimeField(null=True)

    class Meta:
        unique_together = ("bot_profile", "trigger_message_id")
        indexes = [
            models.Index(
                fields=["realm", "status", "date_created"],
                name="zerver_pear_run_realm_status",
            ),
            models.Index(
                fields=["realm", "session_kind", "date_created"],
                name="zerver_pear_run_realm_kind",
            ),
            models.Index(
                fields=["realm", "status", "heartbeat_at"],
                name="zerver_pear_run_st_hb",
            ),
        ]


class PearWorkerRunEvent(models.Model):
    run = models.ForeignKey(PearWorkerRun, on_delete=CASCADE, related_name="events")
    event_type = models.CharField(max_length=64, db_index=True)
    summary = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    date_created = models.DateTimeField(default=timezone_now, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["run", "date_created"],
                name="zerver_pear_run_event_run_date",
            ),
        ]


class PearProject(models.Model):
    UNCONFIGURED = "unconfigured"
    READY = "ready"
    FAILED = "failed"

    CLONE_STATUS_CHOICES = [
        (UNCONFIGURED, "unconfigured"),
        (READY, "ready"),
        (FAILED, "failed"),
    ]

    realm = models.ForeignKey("zerver.Realm", on_delete=CASCADE)
    stream = models.ForeignKey("zerver.Stream", on_delete=CASCADE)
    repository_url = models.TextField(blank=True)
    default_base_ref = models.TextField(blank=True)
    default_base_commit = models.CharField(max_length=64, blank=True)
    repo_cache_path = models.TextField(blank=True)
    clone_status = models.CharField(
        max_length=16,
        choices=CLONE_STATUS_CHOICES,
        default=UNCONFIGURED,
        db_index=True,
    )
    clone_error = models.TextField(blank=True)
    date_created = models.DateTimeField(default=timezone_now, db_index=True)
    date_updated = models.DateTimeField(default=timezone_now)

    class Meta:
        unique_together = ("realm", "stream")
        indexes = [
            models.Index(
                fields=["realm", "stream"],
                name="zerver_pear_project_stream",
            ),
            models.Index(
                fields=["realm", "date_updated"],
                name="zerver_pear_project_updated",
            ),
        ]


class PearWorkspaceNode(models.Model):
    ACTIVE = "active"
    ARCHIVED = "archived"
    BLOCKED = "blocked"

    LIFECYCLE_STATUS_CHOICES = [
        (ACTIVE, "active"),
        (ARCHIVED, "archived"),
        (BLOCKED, "blocked"),
    ]

    realm = models.ForeignKey("zerver.Realm", on_delete=CASCADE)
    project = models.ForeignKey(
        PearProject,
        null=True,
        on_delete=SET_NULL,
        related_name="workspace_nodes",
    )
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    stream_name = models.CharField(max_length=60)
    topic_name = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH)
    parent_node = models.ForeignKey(
        "self",
        null=True,
        on_delete=SET_NULL,
        related_name="child_nodes",
    )
    source_node = models.ForeignKey(
        "self",
        null=True,
        on_delete=SET_NULL,
        related_name="side_nodes",
    )
    workdir = models.TextField(blank=True)
    branch_name = models.TextField(blank=True)
    base_ref = models.TextField(blank=True)
    base_commit = models.CharField(max_length=64, blank=True)
    head_commit = models.CharField(max_length=64, blank=True)
    upstream_ref = models.TextField(blank=True)
    dirty = models.BooleanField(default=False)
    changed_count = models.PositiveIntegerField(default=0)
    lifecycle_status = models.CharField(
        max_length=16,
        choices=LIFECYCLE_STATUS_CHOICES,
        default=ACTIVE,
        db_index=True,
    )
    isolation_username = models.TextField(blank=True)
    model = models.TextField(blank=True)
    reasoning_effort = models.CharField(max_length=16, blank=True)
    response_policy = models.CharField(max_length=16, default="ambient")
    is_paused = models.BooleanField(default=False)
    date_created = models.DateTimeField(default=timezone_now, db_index=True)
    date_updated = models.DateTimeField(default=timezone_now)

    class Meta:
        indexes = [
            models.Index(
                fields=["realm", "stream_name", "topic_name"],
                name="zerver_pear_ws_location",
            ),
            models.Index(
                fields=["project", "date_updated"],
                name="zerver_pear_ws_project_upd",
            ),
            models.Index(
                fields=["realm", "lifecycle_status"],
                name="zerver_pear_ws_lifecycle",
            ),
        ]


class PearTopicContext(models.Model):
    realm = models.ForeignKey("zerver.Realm", on_delete=CASCADE)
    stream_name = models.CharField(max_length=60)
    topic_name = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH)
    parent_context = models.ForeignKey(
        "self",
        null=True,
        on_delete=SET_NULL,
        related_name="child_contexts",
    )
    model = models.TextField(blank=True)
    reasoning_effort = models.CharField(max_length=16, blank=True)
    response_policy = models.CharField(max_length=16, default="ambient")
    github_repo = models.TextField(blank=True)
    workdir = models.TextField(blank=True)
    workspace_node = models.ForeignKey(
        PearWorkspaceNode,
        null=True,
        on_delete=SET_NULL,
        related_name="legacy_topic_contexts",
    )
    is_paused = models.BooleanField(default=False)
    date_created = models.DateTimeField(default=timezone_now, db_index=True)
    date_updated = models.DateTimeField(default=timezone_now)

    class Meta:
        unique_together = ("realm", "stream_name", "topic_name")
        indexes = [
            models.Index(
                fields=["realm", "stream_name", "topic_name"],
                name="zerver_pear_topic_context",
            ),
            models.Index(
                fields=["realm", "date_updated"],
                name="zerver_pear_topic_updated",
            ),
        ]
