from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0823_clear_pre_steps_pear_codex_threads"),
    ]

    operations = [
        migrations.AddField(
            model_name="pearworkerrun",
            name="ambient_triggered",
            field=models.BooleanField(default=False),
        ),
    ]
