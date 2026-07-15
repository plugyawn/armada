#!/usr/bin/env python3

"""Reset disposable Pear tables onto the current migration baseline.

This is a deployment-only bridge for the pre-public migration squash. It
preserves the rest of the Zulip database and deliberately does not attempt to
translate scratch Pear runs, workspaces, monitors, or thread IDs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


LEGACY_PEAR_MIGRATIONS = (
    "0805_pearworkerrun",
    "0806_pear_worker_run_event_state",
    "0807_peartopiccontext",
    "0808_peartopiccontext_reasoning_effort",
    "0809_pear_durability",
    "0810_peartopiccontext_response_policy",
    "0811_peartopiccontext_ambient_default",
    "0812_pear_managed_gittree",
    "0813_pear_canvas_ask_session_kind",
    "0814_pear_monitor",
    "0815_pearworkspacenode_content_revision",
    "0816_pear_remove_canvas_ask",
    "0817_pear_workspace_entry_mutation_receipt",
    "0818_pearworkerrun_tool_count",
    "0819_rename_pearmonitor_block_spec",
    "0820_pearmonitor_artifact_spec_db_column",
    "0821_clear_legacy_pear_codex_threads",
    "0822_clear_pre_graph_pear_codex_threads",
    "0823_clear_pre_steps_pear_codex_threads",
    "0824_pearworkerrun_ambient_triggered",
)
CURRENT_PEAR_MIGRATION = "0805_pear_runtime"
PEAR_TABLES = (
    "zerver_pearmonitor",
    "zerver_pearworkspaceentrymutationreceipt",
    "zerver_pearworkerrunevent",
    "zerver_pearworkerrun",
    "zerver_peartopiccontext",
    "zerver_pearworkspacenode",
    "zerver_pearproject",
)


def classify_pear_migrations(applied_names: set[str]) -> str:
    legacy = applied_names.intersection(LEGACY_PEAR_MIGRATIONS)
    current = CURRENT_PEAR_MIGRATION in applied_names
    if legacy and current:
        return "mixed"
    if legacy:
        return "legacy"
    if current:
        return "current"
    return "empty"


def load_django(repo_root: Path) -> Any:
    sys.path.insert(0, str(repo_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.settings")
    import django

    django.setup()
    from django.db import connection

    return connection


def applied_pear_migrations(connection: Any) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM django_migrations WHERE app = %s AND name = ANY(%s)",
            ["zerver", [*LEGACY_PEAR_MIGRATIONS, CURRENT_PEAR_MIGRATION]],
        )
        return {str(row[0]) for row in cursor.fetchall()}


def reset_pear_schema(connection: Any) -> None:
    from django.db import transaction

    quoted_tables = ", ".join(connection.ops.quote_name(table) for table in PEAR_TABLES)
    migration_names = [*LEGACY_PEAR_MIGRATIONS, CURRENT_PEAR_MIGRATION]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {quoted_tables} CASCADE")
        cursor.execute(
            "DELETE FROM django_migrations WHERE app = %s AND name = ANY(%s)",
            ["zerver", migration_names],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "reset"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    connection = load_django(args.repo_root.resolve())
    before = applied_pear_migrations(connection)
    if args.action == "status":
        print(json.dumps({"state": classify_pear_migrations(before), "applied": sorted(before)}))
        return 0
    if not args.yes:
        parser.error("reset is destructive and requires --yes")
    reset_pear_schema(connection)
    print(
        json.dumps(
            {
                "state_before": classify_pear_migrations(before),
                "state_after": classify_pear_migrations(applied_pear_migrations(connection)),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
