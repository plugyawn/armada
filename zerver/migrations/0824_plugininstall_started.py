from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0823_plugininstall"),
    ]

    operations = [
        migrations.AddField(
            model_name="plugininstall",
            name="date_fed",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="plugininstall",
            name="date_started",
            field=models.DateTimeField(null=True),
        ),
    ]
