import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from zerver.lib.armada_plugin_contract import ARMADA_PLUGIN_MANIFEST_SCHEMA, ARMADA_WIDGET_SCHEMA
from zerver.lib.armada_plugin_validation import (
    canonicalize_plugin_manifest,
    canonicalize_widget_document,
)
from zerver.lib.armada_skills import ARMADA_BUILTIN_SKILLS_DIR
from zerver.lib.armada_worker import armada_provision_codex_skills
from zerver.lib.test_classes import ZulipTestCase

SKILL = ARMADA_BUILTIN_SKILLS_DIR / "armada-plugin"
TEMPLATE = SKILL / "template"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class PluginSkillTest(ZulipTestCase):
    """The agent's skill for making plugins (zerver/codex_skills/armada-plugin):
    its template is a plugin Armada accepts, with the personal options the
    skill asks for, and the skill reaches the agent with its contract."""

    def test_the_template_is_a_plugin_armada_accepts(self) -> None:
        manifest = canonicalize_plugin_manifest(read_json(TEMPLATE / "armada-plugin.json"))
        document, _encoded = canonicalize_widget_document(
            read_json(TEMPLATE / "fixtures" / "queue.json")
        )
        [widget] = manifest["provides"]["widgets"]
        self.assertEqual(manifest["provides"]["tools"]["path"], "/mcp")
        data = document["data"]
        # The figure at rest fits its cell, as the skill says it must.
        figure = data[document["rest"]["figure"].removeprefix("data.")]
        self.assertLessEqual(len(figure), len(document["rest"]["sizer"]))
        # The personal options the skill asks for point at the document's data.
        options = widget["options"]
        self.assertEqual(sorted(options), ["alerts", "facets", "figures"])
        for option in options["figures"]:
            self.assertIsInstance(data[option["figure"].removeprefix("data.")], str)
        for option in options["alerts"]:
            self.assertIsInstance(data[option["value"].removeprefix("data.")], int)
        rows = data[document["blocks"][0]["bind"].removeprefix("data.")]
        for option in options["facets"]:
            self.assertTrue(all(option["key"] in row["facets"] for row in rows))

    def test_the_templates_program_is_python(self) -> None:
        sources = sorted(TEMPLATE.rglob("*.py"))
        self.assertEqual(
            [path.relative_to(TEMPLATE).as_posix() for path in sources],
            ["armada_push.py", "plugin.py", "tests/test_plugin.py"],
        )
        for path in sources:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_the_skill_reaches_the_agent_with_its_contract(self) -> None:
        with TemporaryDirectory() as codex_home, self.settings(ARMADA_CODEX_SKILLS_DIR=None):
            self.assertEqual(armada_provision_codex_skills(codex_home), ["armada-plugin"])
            provisioned = Path(codex_home) / "skills" / "armada-plugin"
            skill = (provisioned / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(skill.startswith("---\nname: armada-plugin\ndescription: "))
            reference = provisioned / "reference"
            self.assertEqual(
                read_json(reference / "manifest.schema.json"), ARMADA_PLUGIN_MANIFEST_SCHEMA
            )
            self.assertEqual(read_json(reference / "widget.schema.json"), ARMADA_WIDGET_SCHEMA)
            for example in ("wandb/armada-plugin.json", "gpus/armada-plugin.json"):
                canonicalize_plugin_manifest(read_json(reference / "examples" / example))
            self.assertTrue((provisioned / "template" / "plugin.py").is_file())
