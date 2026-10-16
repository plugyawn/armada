import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0821_agentrun_codex_thread_version"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentRunWait",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="waits",
                        to="zerver.agentrun",
                    ),
                ),
                (
                    "scheduled_message",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_run_wait",
                        to="zerver.scheduledmessage",
                    ),
                ),
            ],
        ),
    ]
