from unittest import TestCase, main

from tools.pear_deploy.rebaseline_pear_schema import (
    CURRENT_PEAR_MIGRATION,
    LEGACY_PEAR_MIGRATIONS,
    classify_pear_migrations,
)


class PearSchemaRebaselineTest(TestCase):
    def test_classifies_known_migration_states(self) -> None:
        self.assertEqual(classify_pear_migrations(set()), "empty")
        self.assertEqual(classify_pear_migrations({CURRENT_PEAR_MIGRATION}), "current")
        self.assertEqual(classify_pear_migrations({LEGACY_PEAR_MIGRATIONS[0]}), "legacy")
        self.assertEqual(
            classify_pear_migrations({LEGACY_PEAR_MIGRATIONS[-1], CURRENT_PEAR_MIGRATION}),
            "mixed",
        )

    def test_ignores_unrelated_migrations(self) -> None:
        self.assertEqual(classify_pear_migrations({"0804_backfill_user_created_audit_logs"}), "empty")


if __name__ == "__main__":
    main()
