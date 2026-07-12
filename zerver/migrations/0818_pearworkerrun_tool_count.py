from collections import defaultdict

from django.db import migrations, models


def backfill_pear_worker_run_tool_counts(apps, schema_editor) -> None:
    PearWorkerRun = apps.get_model("zerver", "PearWorkerRun")
    PearWorkerRunEvent = apps.get_model("zerver", "PearWorkerRunEvent")
    counts: dict[int, int] = defaultdict(int)
    events = PearWorkerRunEvent.objects.filter(event_type="tool_results").values_list(
        "run_id", "payload"
    )
    for run_id, payload in events.iterator():
        tool_count = payload.get("tool_count") if isinstance(payload, dict) else None
        if isinstance(tool_count, int):
            counts[run_id] += tool_count
    runs = list(PearWorkerRun.objects.filter(id__in=counts))
    for run in runs:
        run.tool_count = counts[run.id]
    PearWorkerRun.objects.bulk_update(runs, ["tool_count"])


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0817_pear_workspace_entry_mutation_receipt"),
    ]

    operations = [
        migrations.AddField(
            model_name="pearworkerrun",
            name="tool_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(backfill_pear_worker_run_tool_counts, migrations.RunPython.noop),
    ]
