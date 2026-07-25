from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0808_native_message_threads"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pearworkspacenode",
            name="parent_node",
        ),
        migrations.RemoveField(
            model_name="pearworkspacenode",
            name="source_node",
        ),
    ]
