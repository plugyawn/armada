from __future__ import annotations

import json
from unittest import TestCase

from zerver.lib.pear_artifact import PearArtifactError, canonicalize_pear_artifact


def valid_document() -> dict[str, object]:
    return {
        "version": 1,
        "title": "force and motion",
        "data": {"force": 12},
        "blocks": [
            {
                "type": "scene",
                "label": "A force connects two bodies",
                "root": {
                    "type": "group",
                    "layout": "row",
                    "children": [
                        {"type": "shape", "id": "a", "shape": "circle", "label": "A"},
                        {"type": "shape", "id": "b", "shape": "circle", "label": "B"},
                        {
                            "type": "connector",
                            "from": "a",
                            "to": "b",
                            "directed": True,
                        },
                    ],
                },
            }
        ],
        "actions": [{"label": "Explain", "prompt": "Explain the force relationship."}],
    }


class PearArtifactValidationTest(TestCase):
    def test_valid_document_is_canonical(self) -> None:
        document, encoded = canonicalize_pear_artifact(valid_document())
        self.assertEqual(document["version"], 1)
        self.assertEqual(json.loads(encoded), document)
        self.assertNotIn('": ', encoded)

    def test_visual_and_executable_escape_hatches_are_rejected(self) -> None:
        for key, value in [
            ("style", "color:red"),
            ("svg", "<svg><script/></svg>"),
            ("url", "https://example.com/tracker"),
            ("onclick", "fetch('/secrets')"),
        ]:
            document = valid_document()
            root = document["blocks"][0]["root"]  # type: ignore[index]
            root[key] = value  # type: ignore[index]
            with self.assertRaises(PearArtifactError):
                canonicalize_pear_artifact(document)

    def test_schema_errors_identify_the_exact_invalid_property(self) -> None:
        document = {
            "version": 1,
            "blocks": [
                {
                    "type": "meter",
                    "label": "Force",
                    "value": 8,
                    "total": 0,
                }
            ],
        }
        with self.assertRaisesRegex(
            PearArtifactError, r"\$\.blocks\[0\]\.total: value must be > 0"
        ):
            canonicalize_pear_artifact(document)

        document["blocks"][0]["total"] = 20  # type: ignore[index]
        document["blocks"][0]["style"] = "green"  # type: ignore[index]
        with self.assertRaisesRegex(
            PearArtifactError, r"\$\.blocks\[0\]\.style: unsupported property"
        ):
            canonicalize_pear_artifact(document)

    def test_duplicate_ids_and_missing_connector_targets_are_rejected(self) -> None:
        document = valid_document()
        children = document["blocks"][0]["root"]["children"]  # type: ignore[index]
        children[1]["id"] = "a"  # type: ignore[index]
        with self.assertRaisesRegex(PearArtifactError, "duplicate"):
            canonicalize_pear_artifact(document)

        document = valid_document()
        children = document["blocks"][0]["root"]["children"]  # type: ignore[index]
        children[2]["to"] = "missing"  # type: ignore[index]
        with self.assertRaisesRegex(PearArtifactError, "missing node"):
            canonicalize_pear_artifact(document)

    def test_rejects_primitive_fields_that_the_renderer_would_ignore(self) -> None:
        for ignored in (
            {"children": [{"type": "text", "text": "hidden"}]},
            {"points": [[0, 0], [1, 1]]},
            {"control": "button"},
        ):
            document = valid_document()
            shape = document["blocks"][0]["root"]["children"][0]  # type: ignore[index]
            shape.update(ignored)  # type: ignore[union-attr]
            with (
                self.subTest(ignored=ignored),
                self.assertRaisesRegex(PearArtifactError, "unsupported properties"),
            ):
                canonicalize_pear_artifact(document)

    def test_node_depth_and_source_budgets_are_enforced(self) -> None:
        document = valid_document()
        root = document["blocks"][0]["root"]  # type: ignore[index]
        for _index in range(9):
            child = {"type": "group", "layout": "column", "children": []}
            root["children"] = [child]  # type: ignore[index]
            root = child
        root["children"] = [{"type": "text", "text": "end"}]
        with self.assertRaisesRegex(PearArtifactError, "depth"):
            canonicalize_pear_artifact(document)

        document = valid_document()
        document["blocks"] = [{"type": "copy", "body": "x" * 70_000}]
        with self.assertRaisesRegex(PearArtifactError, "exceeds"):
            canonicalize_pear_artifact(document)

    def test_repeat_is_bounded_and_cannot_duplicate_graph_identity(self) -> None:
        document = {
            "version": 1,
            "data": {"items": [1, 2, 3]},
            "blocks": [
                {
                    "type": "scene",
                    "label": "Three values",
                    "root": {
                        "type": "text",
                        "bind": "data.item",
                        "repeat": {"source": "data.items", "limit": 3},
                    },
                }
            ],
        }
        canonicalize_pear_artifact(document)

        document["blocks"][0]["root"]["id"] = "duplicated"  # type: ignore[index]
        with self.assertRaisesRegex(PearArtifactError, "cannot contain ids"):
            canonicalize_pear_artifact(document)

        document["blocks"][0]["root"].pop("id")  # type: ignore[index]
        document["blocks"][0]["root"]["repeat"]["limit"] = 64  # type: ignore[index]
        document["blocks"][0]["root"]["children"] = [  # type: ignore[index]
            {"type": "text", "text": "nested"},
            {"type": "text", "text": "nested"},
        ]
        with self.assertRaisesRegex(PearArtifactError, "expanded scene"):
            canonicalize_pear_artifact(document)

    def test_derive_accepts_data_bindings_without_one_of_ambiguity(self) -> None:
        document = {
            "version": 1,
            "data": {"force": 12, "mass": 3},
            "blocks": [
                {
                    "type": "scene",
                    "label": "Acceleration",
                    "root": {
                        "type": "text",
                        "derive": {"op": "divide", "args": ["data.force", "data.mass"]},
                    },
                }
            ],
        }
        canonical, _encoded = canonicalize_pear_artifact(document)
        self.assertEqual(canonical["blocks"][0]["root"]["derive"]["op"], "divide")
