from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models.functions import Lower


def clear_disposable_pear_state(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Drop prerelease scratch state before enforcing logical topic identity.

    Old monitor scopes and Codex thread ids must not survive and reconnect to a
    newly canonicalized worktree. Run events and mutation receipts cascade with
    their owning rows.
    """
    PearWorkerRun = apps.get_model("zerver", "PearWorkerRun")
    PearMonitor = apps.get_model("zerver", "PearMonitor")
    PearWorkspaceNode = apps.get_model("zerver", "PearWorkspaceNode")
    PearWorkerRun.objects.all().delete()
    PearMonitor.objects.all().delete()
    PearWorkspaceNode.objects.all().delete()


class Migration(migrations.Migration):
    # PostgreSQL rejects DDL on a table with pending trigger events.  The
    # scratch-state purge intentionally deletes rows from this table before
    # replacing its uniqueness constraint, so those two phases must commit
    # separately during the pre-release cutover.
    atomic = False

    dependencies = [
        ("zerver", "0805_pear_runtime"),
    ]

    operations = [
        migrations.RunPython(
            clear_disposable_pear_state,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterUniqueTogether(
            name="pearworkspacenode",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="pearworkspacenode",
            constraint=models.UniqueConstraint(
                "realm",
                Lower("stream_name"),
                Lower("topic_name"),
                name="zerver_pear_ws_scope_ci_uniq",
            ),
        ),
    ]
