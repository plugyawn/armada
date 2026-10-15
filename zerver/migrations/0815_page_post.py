import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0814_messageclientkey"),
    ]

    operations = [
        migrations.CreateModel(
            name="Page",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("bio", models.CharField(default="", max_length=280)),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.realm",
                    ),
                ),
                (
                    "user_profile",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Post",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("title", models.CharField(max_length=200)),
                ("content", models.TextField()),
                ("rendered_content", models.TextField()),
                ("draft", models.BooleanField(default=False)),
                ("drafted_by_agent", models.BooleanField(default=False)),
                ("date_created", models.DateTimeField(default=django.utils.timezone.now)),
                ("date_updated", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
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
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["author", "-date_created"], name="zerver_post_author_created"
                    )
                ],
            },
        ),
    ]
