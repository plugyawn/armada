import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0824_plugininstall_started"),
    ]

    operations = [
        migrations.CreateModel(
            name="PluginWidgetPreference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("widget", models.CharField(max_length=32)),
                ("shown", models.BooleanField(null=True)),
                ("position", models.PositiveSmallIntegerField(null=True)),
                ("label", models.CharField(default="", max_length=24)),
                ("rest_figure", models.CharField(default="", max_length=32)),
                ("filters", models.JSONField(default=dict)),
                ("alerts", models.JSONField(default=dict)),
                ("in_column", models.BooleanField(null=True)),
                ("seen_at", models.DateTimeField(null=True)),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "install",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.plugininstall",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "install", "widget"),
                        name="zerver_pluginwidgetpreference_widget",
                    )
                ],
            },
        ),
    ]
