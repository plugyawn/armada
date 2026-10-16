from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    """Index a topic's worktree and runs the way every lookup asks for
    them: Django compiles stream_name__iexact to UPPER(stream_name), which
    the Lower() unique index could not serve, so each lookup scanned the
    realm's rows. The new unique index is built before the old one goes,
    so a failure leaves the old one in place."""

    atomic = False

    dependencies = [
        ("zerver", "0817_claim_uploads_of_existing_posts"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="agentrun",
            index=models.Index(
                models.F("realm"),
                Upper("stream_name"),
                Upper("topic_name"),
                name="zerver_agentrun_topic",
            ),
        ),
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.UniqueConstraint(
                models.F("realm"),
                Upper("stream_name"),
                Upper("topic_name"),
                name="zerver_workspace_scope_uniq",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="workspace",
            name="zerver_workspace_scope_ci_uniq",
        ),
    ]
