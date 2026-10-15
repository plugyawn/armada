from django.conf import settings
from django.db import connection, migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models.functions import Lower
from psycopg2.sql import SQL, Identifier

# The agent's six models lose the old code name: (old model, new model,
# old table, new table, old verbose name, new verbose name).
RENAMED_MODELS = [
    (
        "PearWorkerRunEvent",
        "AgentRunEvent",
        "zerver_pearworkerrunevent",
        "zerver_agentrunevent",
        "pear worker run event",
        "agent run event",
    ),
    (
        "PearWorkspaceEntryMutationReceipt",
        "WorkspaceEntryMutationReceipt",
        "zerver_pearworkspaceentrymutationreceipt",
        "zerver_workspaceentrymutationreceipt",
        "pear workspace entry mutation receipt",
        "workspace entry mutation receipt",
    ),
    (
        "PearMonitor",
        "AgentMonitor",
        "zerver_pearmonitor",
        "zerver_agentmonitor",
        "pear monitor",
        "agent monitor",
    ),
    (
        "PearWorkerRun",
        "AgentRun",
        "zerver_pearworkerrun",
        "zerver_agentrun",
        "pear worker run",
        "agent run",
    ),
    (
        "PearWorkspaceNode",
        "Workspace",
        "zerver_pearworkspacenode",
        "zerver_workspace",
        "pear workspace node",
        "workspace",
    ),
    (
        "PearProject",
        "Repository",
        "zerver_pearproject",
        "zerver_repository",
        "pear project",
        "repository",
    ),
]

# Indexes named in Meta: (model, old name, new name).
RENAMED_INDEXES = [
    ("agentrun", "zerver_pear_run_realm_status", "zerver_agentrun_realm_status"),
    ("agentrun", "zerver_pear_run_realm_kind", "zerver_agentrun_realm_kind"),
    ("agentrun", "zerver_pear_run_st_hb", "zerver_agentrun_st_hb"),
    ("agentrunevent", "zerver_pear_run_event_run_date", "zerver_agentrunevent_run_date"),
    ("agentmonitor", "zerver_pear_monitor_topic", "zerver_agentmonitor_topic"),
    ("agentmonitor", "zerver_pear_monitor_due", "zerver_agentmonitor_due"),
    ("repository", "zerver_pear_project_stream", "zerver_repository_stream"),
    ("repository", "zerver_pear_project_updated", "zerver_repository_updated"),
    ("workspace", "zerver_pear_ws_project_upd", "zerver_workspace_project_upd"),
    ("workspace", "zerver_pear_ws_lifecycle", "zerver_workspace_lifecycle"),
    ("workspaceentrymutationreceipt", "zerver_pear_ws_mut_created", "zerver_wsreceipt_created"),
    ("workspaceentrymutationreceipt", "zerver_pear_ws_mut_state", "zerver_wsreceipt_state"),
]
OLD_SCOPE_CONSTRAINT = "zerver_pear_ws_scope_ci_uniq"
NEW_SCOPE_CONSTRAINT = "zerver_workspace_scope_ci_uniq"
NAMED_IN_META = {OLD_SCOPE_CONSTRAINT, NEW_SCOPE_CONSTRAINT}
NAMED_IN_META.update(name for _, old, new in RENAMED_INDEXES for name in (old, new))

PERMISSION_ACTIONS = ["add", "change", "delete", "view"]


def postgres_object_name(table: str, columns: str | None, label: str) -> str:
    """The name PostgreSQL chooses for a constraint, index or sequence
    that was created without one (makeObjectName in its source):
    the table and column parts are shortened, the longer first, until
    the name fits in 63 bytes."""
    overhead = len(label) + 1 + (1 if columns else 0)
    available = 63 - overhead
    table_chars = len(table)
    column_chars = len(columns or "")
    while table_chars + column_chars > available:
        if table_chars > column_chars:
            table_chars -= 1
        else:
            column_chars -= 1
    name = table[:table_chars]
    if columns:
        name += "_" + columns[:column_chars]
    return f"{name}_{label}"


def derived_names(
    schema_editor: BaseDatabaseSchemaEditor,
    table: str,
    columns: list[str],
    foreign_key: tuple[str, str] | None,
) -> list[str]:
    """Every name Django or PostgreSQL derives from a table for an
    unnamed object on these columns, in a fixed order, so that the
    same position names the same kind of object for any table."""
    suffixes = ["", "_like", "_uniq"]
    if foreign_key is not None:
        suffixes.append("_fk_{}_{}".format(*foreign_key))
    joined_columns = "_".join(columns)
    # Django names these objects with this private method.
    return [
        *(
            schema_editor._create_index_name(table, columns, suffix=suffix)  # type: ignore[attr-defined] # not in django-stubs
            for suffix in suffixes
        ),
        postgres_object_name(table, None, "pkey"),
        postgres_object_name(table, joined_columns, "key"),
        postgres_object_name(table, joined_columns, "check"),
    ]


def rename_derived_objects(schema_editor: BaseDatabaseSchemaEditor, *, to_new_names: bool) -> None:
    """Give the renamed tables' unnamed indexes, constraints and
    sequences the names they would have had if the table had always
    had its current name.

    Django's RenameModel renames only the table, and re-creates the
    foreign keys that point at it under names derived from whichever
    table names hold at that moment.  Each object is recognised by
    matching its name against the names derivable from either of its
    table's two names; any name that matches neither, apart from the
    ones Meta sets, stops the migration rather than being left behind.
    """
    with connection.cursor() as cursor:
        for _, _, old_table, new_table, _, _ in RENAMED_MODELS:
            table = new_table if to_new_names else old_table
            constraints = connection.introspection.get_constraints(cursor, table)
            for name, info in constraints.items():
                if name in NAMED_IN_META:
                    continue
                columns = info["columns"]
                foreign_key = info["foreign_key"]
                candidates_old = derived_names(schema_editor, old_table, columns, foreign_key)
                candidates_new = derived_names(schema_editor, new_table, columns, foreign_key)
                target = candidates_new if to_new_names else candidates_old
                if name in target:
                    continue
                if name in candidates_old:
                    new_name = target[candidates_old.index(name)]
                elif name in candidates_new:
                    new_name = target[candidates_new.index(name)]
                else:
                    raise AssertionError(f"Unrecognised name {name} on table {table}")
                if info["check"] or foreign_key is not None:
                    query = SQL("ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")
                else:
                    # Renaming the index of a primary key or a unique
                    # constraint renames the constraint with it.
                    query = SQL("ALTER INDEX {old} RENAME TO {new}")
                cursor.execute(
                    query.format(
                        table=Identifier(table), old=Identifier(name), new=Identifier(new_name)
                    )
                )

            for sequence in connection.introspection.get_sequences(cursor, table):
                old_name = postgres_object_name(old_table, sequence["column"], "seq")
                new_name = postgres_object_name(new_table, sequence["column"], "seq")
                current, wanted = (old_name, new_name) if to_new_names else (new_name, old_name)
                if sequence["name"] == wanted:
                    continue
                if sequence["name"] != current:
                    raise AssertionError(
                        f"Unrecognised sequence {sequence['name']} on table {table}"
                    )
                cursor.execute(
                    SQL("ALTER SEQUENCE {old} RENAME TO {new}").format(
                        old=Identifier(current), new=Identifier(wanted)
                    )
                )


def rename_derived_objects_to_new_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_derived_objects(schema_editor, to_new_names=True)


def rename_derived_objects_to_old_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_derived_objects(schema_editor, to_new_names=False)


def rename_permissions(apps: StateApps, *, to_new_names: bool) -> None:
    """Django renames the models' content types on RenameModel, but not
    the permissions it created for them."""
    Permission = apps.get_model("auth", "Permission")
    for old_model, new_model, _, _, old_verbose, new_verbose in RENAMED_MODELS:
        old = (old_model.lower(), old_verbose)
        new = (new_model.lower(), new_verbose)
        (from_model, _), (to_model, to_verbose) = (old, new) if to_new_names else (new, old)
        for action in PERMISSION_ACTIONS:
            Permission.objects.filter(
                content_type__app_label="zerver", codename=f"{action}_{from_model}"
            ).update(codename=f"{action}_{to_model}", name=f"Can {action} {to_verbose}")


def rename_permissions_to_new_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_permissions(apps, to_new_names=True)


def rename_permissions_to_old_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_permissions(apps, to_new_names=False)


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0809_remove_pearworkspacenode_legacy_relationships"),
    ]

    operations = [
        # Backwards, this runs last, once every table has its old name
        # again; forwards it does nothing.
        migrations.RunPython(
            migrations.RunPython.noop,
            reverse_code=rename_derived_objects_to_old_names,
            elidable=True,
        ),
        # The models that point at others first, so that the foreign
        # keys Django re-creates for a renamed target already take
        # their table's new name.
        *(
            migrations.RenameModel(old_name=old_model, new_name=new_model)
            for old_model, new_model, _, _, _, _ in RENAMED_MODELS
        ),
        migrations.AlterField(
            model_name="agentrun",
            name="requesting_user",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="requested_agent_runs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="agentmonitor",
            name="bot_profile",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="agent_bot_monitors",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="agentmonitor",
            name="creator",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="agent_monitors",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="workspaceentrymutationreceipt",
            name="actor",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="workspace_entry_mutation_receipts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        *(
            migrations.RenameIndex(model_name=model_name, new_name=new_name, old_name=old_name)
            for model_name, old_name, new_name in RENAMED_INDEXES
        ),
        # Django has no operation that renames a constraint.  This one
        # is a unique index on expressions, so the database renames the
        # index, and the state replaces the constraint.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER INDEX zerver_pear_ws_scope_ci_uniq RENAME TO zerver_workspace_scope_ci_uniq",
                    reverse_sql="ALTER INDEX zerver_workspace_scope_ci_uniq RENAME TO zerver_pear_ws_scope_ci_uniq",
                ),
            ],
            state_operations=[
                migrations.RemoveConstraint(model_name="workspace", name=OLD_SCOPE_CONSTRAINT),
                migrations.AddConstraint(
                    model_name="workspace",
                    constraint=models.UniqueConstraint(
                        "realm",
                        Lower("stream_name"),
                        Lower("topic_name"),
                        name=NEW_SCOPE_CONSTRAINT,
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            rename_derived_objects_to_new_names,
            reverse_code=migrations.RunPython.noop,
            elidable=True,
        ),
        migrations.RunPython(
            rename_permissions_to_new_names,
            reverse_code=rename_permissions_to_old_names,
            elidable=True,
        ),
    ]
