from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from zerver.lib.armada_skills import reconcile_armada_codex_skills


class ArmadaSkillsTest(TestCase):
    def test_provisions_only_skill_directories_and_writes_manifest(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as codex_home:
            source = Path(source_root)
            skill = source / "workspace"
            skill.mkdir()
            (skill / "SKILL.md").write_text("# workspace\n", encoding="utf-8")
            ignored = source / "notes"
            ignored.mkdir()
            (ignored / "README.md").write_text("not a skill\n", encoding="utf-8")

            self.assertEqual(reconcile_armada_codex_skills(source, codex_home), ["workspace"])

            skills_home = Path(codex_home) / "skills"
            self.assertEqual(
                (skills_home / "workspace" / "SKILL.md").read_text(encoding="utf-8"),
                "# workspace\n",
            )
            self.assertFalse((skills_home / "notes").exists())
            self.assertEqual(
                json.loads(
                    (skills_home / ".armada-managed-skills.json").read_text(encoding="utf-8")
                ),
                {"version": 1, "skills": ["workspace"]},
            )

    def test_reconciles_retired_and_stale_managed_files(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as codex_home:
            source = Path(source_root)
            skills_home = Path(codex_home) / "skills"
            for name in ("alpha", "beta"):
                skill = source / name
                skill.mkdir()
                (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

            self.assertEqual(
                reconcile_armada_codex_skills(source, codex_home),
                ["alpha", "beta"],
            )
            third_party = skills_home / "third-party"
            third_party.mkdir()
            (third_party / "SKILL.md").write_text("# user owned\n", encoding="utf-8")
            (skills_home / "beta" / "stale.txt").write_text("remove me\n", encoding="utf-8")
            (source / "alpha" / "SKILL.md").unlink()
            (source / "beta" / "SKILL.md").write_text("# beta updated\n", encoding="utf-8")
            gamma = source / "gamma"
            gamma.mkdir()
            (gamma / "SKILL.md").write_text("# gamma\n", encoding="utf-8")

            self.assertEqual(
                reconcile_armada_codex_skills(source, codex_home),
                ["beta", "gamma"],
            )

            self.assertFalse((skills_home / "alpha").exists())
            self.assertFalse((skills_home / "beta" / "stale.txt").exists())
            self.assertEqual(
                (skills_home / "beta" / "SKILL.md").read_text(encoding="utf-8"),
                "# beta updated\n",
            )
            self.assertEqual(
                (third_party / "SKILL.md").read_text(encoding="utf-8"),
                "# user owned\n",
            )

    def test_refuses_symlinked_manifest_without_touching_target(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as codex_home:
            source = Path(source_root)
            skill = source / "safe-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("# safe\n", encoding="utf-8")
            skills_home = Path(codex_home) / "skills"
            skills_home.mkdir()
            outside_manifest = Path(codex_home) / "outside.json"
            outside_manifest.write_text('{"version": 1, "skills": []}\n', encoding="utf-8")
            (skills_home / ".armada-managed-skills.json").symlink_to(outside_manifest)

            with self.assertRaisesRegex(OSError, "must not be a symbolic link"):
                reconcile_armada_codex_skills(source, codex_home)

            self.assertEqual(
                outside_manifest.read_text(encoding="utf-8"),
                '{"version": 1, "skills": []}\n',
            )

    def test_replaces_a_destination_symlink_without_following_it(self) -> None:
        with TemporaryDirectory() as source_root, TemporaryDirectory() as codex_home:
            source = Path(source_root)
            skill = source / "workspace"
            skill.mkdir()
            (skill / "SKILL.md").write_text("# managed\n", encoding="utf-8")
            skills_home = Path(codex_home) / "skills"
            skills_home.mkdir()
            outside = Path(codex_home) / "outside"
            outside.mkdir()
            (outside / "SKILL.md").write_text("# outside\n", encoding="utf-8")
            (skills_home / "workspace").symlink_to(outside, target_is_directory=True)

            reconcile_armada_codex_skills(source, codex_home)

            self.assertFalse((skills_home / "workspace").is_symlink())
            self.assertEqual(
                (skills_home / "workspace" / "SKILL.md").read_text(encoding="utf-8"),
                "# managed\n",
            )
            self.assertEqual(
                (outside / "SKILL.md").read_text(encoding="utf-8"),
                "# outside\n",
            )
