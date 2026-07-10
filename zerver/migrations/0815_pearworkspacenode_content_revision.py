from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0814_pear_monitor"),
    ]

    operations = [
        migrations.AddField(
            model_name="pearworkspacenode",
            name="content_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
