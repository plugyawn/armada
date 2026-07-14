from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def clear_pre_steps_codex_threads(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    PearWorkerRun = apps.get_model("zerver", "PearWorkerRun")
    PearMonitor = apps.get_model("zerver", "PearMonitor")
    PearWorkerRun.objects.exclude(codex_thread_id="").update(codex_thread_id="")
    PearMonitor.objects.exclude(codex_thread_id="").update(codex_thread_id="")


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0822_clear_pre_graph_pear_codex_threads"),
    ]

    operations = [
        migrations.RunPython(clear_pre_steps_codex_threads, migrations.RunPython.noop),
    ]
