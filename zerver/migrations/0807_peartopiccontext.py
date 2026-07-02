# Generated manually for Pear topic context coordination.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0806_pear_worker_run_event_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="PearTopicContext",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("stream_name", models.CharField(max_length=60)),
                ("topic_name", models.CharField(max_length=60)),
                ("model", models.TextField(blank=True)),
                ("github_repo", models.TextField(blank=True)),
                ("workdir", models.TextField(blank=True)),
                (
                    "date_created",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "parent_context",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="child_contexts",
                        to="zerver.peartopiccontext",
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.realm",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["realm", "stream_name", "topic_name"],
                        name="zerver_pear_topic_context",
                    ),
                    models.Index(
                        fields=["realm", "date_updated"],
                        name="zerver_pear_topic_updated",
                    ),
                ],
                "unique_together": {("realm", "stream_name", "topic_name")},
            },
        ),
    ]
