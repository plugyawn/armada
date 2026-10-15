import json
from datetime import date, datetime, timezone
from typing import Any

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

# Until this migration each person's partner was JSON in a BotStorageData
# row under the realm's agent bot, keyed "partner:<user_id>".
PARTNER_KEY_PREFIX = "partner:"
# The agent's bots are the embedded bots with this service.
AGENT_SERVICE = "armada"
TIMES = ("chosen_at", "evolved_at", "runs_watermark", "folded_at")
FIELDS = (
    "species",
    "chosen",
    "shiny",
    "held",
    "later",
    "chosen_at",
    "evolved_at",
    "evolved_kind",
    "xp",
    "active_days",
    "day",
    "day_active",
    "day_messages",
    "day_runs",
    "day_reactions",
    "message_watermark",
    "reaction_watermark",
    "runs_watermark",
    "folded_at",
)


def column_values(row: dict[str, Any]) -> dict[str, Any]:
    values = {key: row[key] for key in FIELDS if row.get(key) is not None}
    for key in TIMES:
        if key in values:
            values[key] = datetime.fromtimestamp(values[key], tz=timezone.utc)
    if "day" in values:
        values["day"] = date.fromisoformat(values["day"])
    return values


def row_values(partner: Any) -> dict[str, Any]:
    row = {key: getattr(partner, key) for key in FIELDS}
    for key in TIMES:
        if row[key] is not None:
            row[key] = row[key].timestamp()
    if row["day"] is not None:
        row["day"] = row["day"].isoformat()
    return row


def move_partners_to_their_table(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    BotStorageData = apps.get_model("zerver", "BotStorageData")
    Partner = apps.get_model("zerver", "Partner")
    Service = apps.get_model("zerver", "Service")
    UserProfile = apps.get_model("zerver", "UserProfile")
    # Only the agent's bots held partners: any other bot may keep its own
    # keys under the same prefix, and they stay where they are.
    stored = BotStorageData.objects.filter(
        bot_profile_id__in=Service.objects.filter(name=AGENT_SERVICE).values("user_profile_id"),
        key__startswith=PARTNER_KEY_PREFIX,
    )
    rows: dict[int, dict[str, Any]] = {}
    for key, value in stored.values_list("key", "value"):
        row = json.loads(value)
        user_id = int(key.removeprefix(PARTNER_KEY_PREFIX))
        # A realm whose agent bot changed may hold a person's row under
        # two bots; experience only grows, so the larger is the newer.
        if user_id not in rows or row.get("xp", 0) > rows[user_id].get("xp", 0):
            rows[user_id] = row
    realms = dict(UserProfile.objects.filter(id__in=list(rows)).values_list("id", "realm_id"))
    Partner.objects.bulk_create(
        Partner(user_profile_id=user_id, realm_id=realms[user_id], **column_values(row))
        for user_id, row in rows.items()
        # A person deleted since keeps no partner.
        if user_id in realms
    )
    stored.delete()


def move_partners_back_to_bot_storage(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Each realm's rows go back under its first agent bot, the one the
    server picks when no bot is configured; a realm with no agent bot had
    partners switched off, and its rows are dropped with the table."""
    BotStorageData = apps.get_model("zerver", "BotStorageData")
    Partner = apps.get_model("zerver", "Partner")
    Service = apps.get_model("zerver", "Service")
    # Ordered from the highest id down, so that each realm keeps its lowest.
    agents: dict[int, int] = dict(
        Service.objects.filter(
            name=AGENT_SERVICE, user_profile__is_active=True, user_profile__is_bot=True
        )
        .order_by("-user_profile_id")
        .values_list("user_profile__realm_id", "user_profile_id")
    )
    BotStorageData.objects.bulk_create(
        BotStorageData(
            bot_profile_id=agents[partner.realm_id],
            key=f"{PARTNER_KEY_PREFIX}{partner.user_profile_id}",
            value=json.dumps(row_values(partner), sort_keys=True),
        )
        for partner in Partner.objects.filter(realm_id__in=list(agents))
    )


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0812_rename_agent_data"),
    ]

    operations = [
        migrations.CreateModel(
            name="Partner",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("species", models.CharField(max_length=16, null=True)),
                ("chosen", models.CharField(max_length=16, null=True)),
                ("shiny", models.BooleanField(default=False)),
                ("held", models.BooleanField(default=False)),
                ("later", models.BooleanField(default=False)),
                ("chosen_at", models.DateTimeField(null=True)),
                ("evolved_at", models.DateTimeField(null=True)),
                ("evolved_kind", models.CharField(max_length=16, null=True)),
                ("xp", models.PositiveIntegerField(default=0)),
                ("active_days", models.PositiveIntegerField(default=0)),
                ("day", models.DateField(null=True)),
                ("day_active", models.BooleanField(default=False)),
                ("day_messages", models.PositiveIntegerField(default=0)),
                ("day_runs", models.PositiveIntegerField(default=0)),
                ("day_reactions", models.PositiveIntegerField(default=0)),
                ("message_watermark", models.BigIntegerField(default=0)),
                ("reaction_watermark", models.BigIntegerField(default=0)),
                ("runs_watermark", models.DateTimeField(null=True)),
                ("folded_at", models.DateTimeField(null=True)),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.realm",
                    ),
                ),
                (
                    "user_profile",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            move_partners_to_their_table,
            reverse_code=move_partners_back_to_bot_storage,
            elidable=True,
        ),
    ]
