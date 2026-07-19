import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0807_message_threads"),
    ]

    operations = [
        migrations.AddField(
            model_name="archivedmessage",
            name="thread_root_message_id",
            field=models.IntegerField(null=True),
        ),
        migrations.AddField(
            model_name="message",
            name="thread_root",
            field=models.ForeignKey(
                db_column="thread_root_message_id",
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="thread_replies",
                to="zerver.message",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["thread_root", "id"],
                name="zerver_message_thread_root_id",
            ),
        ),
        migrations.DeleteModel(name="MessageThreadReply"),
        migrations.DeleteModel(name="MessageThread"),
    ]
