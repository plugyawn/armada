import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0816_pear_remove_canvas_ask"),
    ]

    operations = [
        migrations.CreateModel(
            name="PearWorkspaceEntryMutationReceipt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("lookup_digest", models.CharField(max_length=64, unique=True)),
                ("request_digest", models.CharField(max_length=64, unique=True)),
                (
                    "operation",
                    models.CharField(
                        choices=[
                            ("create", "create"),
                            ("rename", "rename"),
                            ("delete", "delete"),
                        ],
                        max_length=16,
                    ),
                ),
                ("requested_revision", models.PositiveBigIntegerField()),
                ("request_payload", models.JSONField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("prepared", "prepared"),
                            ("applied", "applied"),
                            ("complete", "complete"),
                        ],
                        default="prepared",
                        max_length=16,
                    ),
                ),
                ("application_payload", models.JSONField(default=dict)),
                ("result_payload", models.JSONField(default=dict)),
                (
                    "date_created",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pear_workspace_entry_mutation_receipts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace_node",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entry_mutation_receipts",
                        to="zerver.pearworkspacenode",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["workspace_node", "date_created"],
                        name="zerver_pear_ws_mut_created",
                    )
                ],
            },
        ),
    ]
