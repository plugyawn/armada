from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0815_page_post"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="attachments",
            field=models.ManyToManyField(related_name="armada_posts", to="zerver.attachment"),
        ),
    ]
