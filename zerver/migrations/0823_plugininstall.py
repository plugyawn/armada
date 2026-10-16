import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0822_agentrunwait"),
    ]

    operations = [
        migrations.CreateModel(
            name="PluginInstall",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("plugin_id", models.CharField(max_length=32)),
                ("name", models.CharField(max_length=40)),
                ("version", models.CharField(max_length=32)),
                (
                    "source_kind",
                    models.CharField(
                        choices=[("github", "github"), ("local", "local"), ("remote", "remote")],
                        default="github",
                        max_length=8,
                    ),
                ),
                ("source", models.CharField(default="", max_length=300)),
                ("source_path", models.CharField(default="", max_length=200)),
                ("sha", models.CharField(default="", max_length=40)),
                ("manifest", models.JSONField()),
                ("settings", models.JSONField(default=dict)),
                ("state", models.PositiveSmallIntegerField(default=2)),
                ("state_detail", models.TextField(default="")),
                ("failures", models.PositiveSmallIntegerField(default=0)),
                ("port", models.PositiveIntegerField(null=True, unique=True)),
                ("token_salt", models.CharField(max_length=32)),
                ("made_in_topic", models.CharField(max_length=60, null=True)),
                ("made_in_message_id", models.BigIntegerField(null=True)),
                ("disabled", models.BooleanField(default=False)),
                ("widget_defaults", models.JSONField(default=dict)),
                ("disabled_tools", models.JSONField(default=list)),
                ("generation", models.PositiveIntegerField(default=1)),
                ("date_installed", models.DateTimeField(default=django.utils.timezone.now)),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                ("date_removed", models.DateTimeField(null=True)),
                (
                    "bot",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "installed_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "made_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "made_by_run",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="zerver.agentrun",
                    ),
                ),
                (
                    "made_in_stream",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="zerver.stream",
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.realm",
                    ),
                ),
                (
                    "source_repository",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="zerver.repository",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PluginLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("kind", models.CharField(max_length=32)),
                ("value", models.CharField(max_length=200)),
                ("topic", models.CharField(max_length=60, null=True)),
                ("date_created", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "install",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.plugininstall",
                    ),
                ),
                (
                    "stream",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.stream",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PluginSecret",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=32)),
                ("ciphertext", models.TextField()),
                ("hint", models.CharField(default="", max_length=4)),
                ("date_set", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "install",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.plugininstall",
                    ),
                ),
                (
                    "set_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PluginWidgetState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("widget", models.CharField(max_length=32)),
                ("item", models.CharField(default="", max_length=64)),
                ("document", models.JSONField()),
                ("version", models.PositiveIntegerField(default=1)),
                ("date_fed", models.DateTimeField(default=django.utils.timezone.now)),
                ("date_sent", models.DateTimeField(null=True)),
                ("pending", models.BooleanField(default=False)),
                ("stale", models.BooleanField(default=False)),
                (
                    "install",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.plugininstall",
                    ),
                ),
                (
                    "link",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.pluginlink",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="plugininstall",
            constraint=models.UniqueConstraint(
                fields=("realm", "plugin_id"), name="zerver_plugininstall_realm_plugin"
            ),
        ),
        migrations.AddConstraint(
            model_name="pluginlink",
            constraint=models.UniqueConstraint(
                fields=("install", "kind", "value", "stream", "topic"),
                name="zerver_pluginlink_place",
                nulls_distinct=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="pluginsecret",
            constraint=models.UniqueConstraint(
                fields=("install", "name"), name="zerver_pluginsecret_name"
            ),
        ),
        migrations.AddConstraint(
            model_name="pluginwidgetstate",
            constraint=models.UniqueConstraint(
                fields=("install", "widget", "link", "item"),
                name="zerver_pluginwidgetstate_scope",
                nulls_distinct=False,
            ),
        ),
    ]
