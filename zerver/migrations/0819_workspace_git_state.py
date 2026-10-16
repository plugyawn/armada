from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0818_agent_topic_lookup_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="git_state",
            field=models.JSONField(default=dict),
        ),
    ]
