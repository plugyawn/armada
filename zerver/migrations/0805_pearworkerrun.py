# Generated manually for Pear worker run coordination.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0804_backfill_user_created_audit_logs"),
    ]

    operations = [
        migrations.CreateModel(
            name="PearWorkerRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("trigger_message_id", models.IntegerField()),
                ("stream_name", models.CharField(max_length=60)),
                ("topic_name", models.CharField(max_length=60)),
                (
                    "session_kind",
                    models.CharField(
                        choices=[("shared", "shared"), ("side", "side")],
                        db_index=True,
                        default="shared",
                        max_length=16,
                    ),
                ),
                ("source_stream_name", models.CharField(blank=True, max_length=60)),
                ("source_topic_name", models.CharField(blank=True, max_length=60)),
                ("source_message_id", models.IntegerField(null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "queued"),
                            ("running", "running"),
                            ("done", "done"),
                            ("failed", "failed"),
                            ("cancelled", "cancelled"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("summary", models.TextField(blank=True)),
                ("current_activity", models.TextField(blank=True)),
                ("runtime_label", models.TextField(blank=True)),
                ("model", models.TextField(blank=True)),
                ("sandbox", models.CharField(blank=True, max_length=64)),
                ("approval_policy", models.CharField(blank=True, max_length=64)),
                ("workdir", models.TextField(blank=True)),
                ("codex_thread_id", models.TextField(blank=True)),
                ("result_message_id", models.IntegerField(null=True)),
                ("error", models.TextField(null=True)),
                (
                    "date_created",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                ("date_finished", models.DateTimeField(null=True)),
                (
                    "bot_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.realm",
                    ),
                ),
                (
                    "requesting_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pear_requested_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["realm", "status", "date_created"],
                        name="zerver_pear_run_realm_status",
                    ),
                    models.Index(
                        fields=["realm", "session_kind", "date_created"],
                        name="zerver_pear_run_realm_kind",
                    ),
                ],
                "unique_together": {("bot_profile", "trigger_message_id")},
            },
        ),
        migrations.CreateModel(
            name="PearWorkerRunEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("event_type", models.CharField(db_index=True, max_length=64)),
                ("summary", models.TextField(blank=True)),
                ("payload", models.JSONField(default=dict)),
                (
                    "date_created",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="zerver.pearworkerrun",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["run", "date_created"],
                        name="zerver_pear_run_event_run_date",
                    ),
                ],
            },
        ),
    ]
