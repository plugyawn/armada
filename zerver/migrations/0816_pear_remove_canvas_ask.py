from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def migrate_canvas_ask_runs(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    PearWorkerRun = apps.get_model("zerver", "PearWorkerRun")
    PearWorkerRun.objects.filter(session_kind="canvas_ask").update(session_kind="side")


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0815_pearworkspacenode_content_revision"),
    ]

    operations = [
        migrations.RunPython(
            migrate_canvas_ask_runs,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="pearworkerrun",
            name="session_kind",
            field=models.CharField(
                choices=[
                    ("shared", "shared"),
                    ("side", "side"),
                    ("monitor", "monitor"),
                ],
                db_index=True,
                default="shared",
                max_length=16,
            ),
        ),
    ]
