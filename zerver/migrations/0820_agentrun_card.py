from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def backfill_run_cards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Build each existing run's card from its events, as recording them
    would have. The rules live with the recording code, which this
    imports: they read only an event's type, payload, id and date, and a
    run's id and Codex thread, none of which a later migration changes."""
    from zerver.lib.armada_run_card import rebuild_armada_run_card

    AgentRun = apps.get_model("zerver", "AgentRun")
    AgentRunEvent = apps.get_model("zerver", "AgentRunEvent")
    for run in AgentRun.objects.only("id", "codex_thread_id").iterator():
        events = list(AgentRunEvent.objects.filter(run_id=run.id).order_by("id"))
        if events:
            AgentRun.objects.filter(id=run.id).update(
                last_event_id=events[-1].id,
                card=rebuild_armada_run_card(run, events),
            )


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0819_workspace_git_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrun",
            name="card",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="agentrun",
            name="last_event_id",
            field=models.IntegerField(null=True),
        ),
        migrations.RunPython(
            backfill_run_cards,
            reverse_code=migrations.RunPython.noop,
            elidable=True,
        ),
    ]
