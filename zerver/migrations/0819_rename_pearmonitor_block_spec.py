from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def clear_legacy_pear_monitors(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    del schema_editor
    apps.get_model("zerver", "PearMonitor").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("zerver", "0818_pearworkerrun_tool_count")]

    operations = [
        migrations.RenameField(
            model_name="pearmonitor",
            old_name="block_spec",
            new_name="artifact_spec",
        ),
        migrations.RunPython(clear_legacy_pear_monitors, migrations.RunPython.noop),
    ]
