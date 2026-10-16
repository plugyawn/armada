from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0820_agentrun_card"),
    ]

    operations = [
        # Existing runs record no version, so their Codex threads, started
        # before the version was recorded, are not resumed: each topic's
        # next run starts a fresh thread with the current tools.
        migrations.AddField(
            model_name="agentrun",
            name="codex_thread_version",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
