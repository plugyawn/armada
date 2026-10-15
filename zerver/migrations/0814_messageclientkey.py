import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0813_partner"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageClientKey",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("client_key", models.CharField(max_length=64)),
                ("message_id", models.BigIntegerField(null=True)),
                ("date_created", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "user_profile",
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
                        fields=("user_profile", "client_key"),
                        name="zerver_messageclientkey_user_key",
                    )
                ],
            },
        ),
    ]
