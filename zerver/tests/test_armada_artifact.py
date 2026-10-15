from __future__ import annotations

import json
from typing import cast
from unittest import TestCase

from zerver.lib.armada_artifact import ArmadaArtifactError, canonicalize_armada_artifact


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


class ArmadaArtifactValidationTest(TestCase):
    def test_valid_document_is_canonical(self) -> None:
        document, encoded = canonicalize_armada_artifact(valid_document())
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
            root[key] = value
            with self.assertRaises(ArmadaArtifactError):
                canonicalize_armada_artifact(document)

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
            ArmadaArtifactError, r"\$\.blocks\[0\]\.total: value must be > 0"
        ):
            canonicalize_armada_artifact(document)

        document["blocks"][0]["total"] = 20  # type: ignore[index]
        document["blocks"][0]["style"] = "green"  # type: ignore[index]
        with self.assertRaisesRegex(
            ArmadaArtifactError, r"\$\.blocks\[0\]\.style: unsupported property"
        ):
            canonicalize_armada_artifact(document)

    def test_duplicate_ids_and_missing_connector_targets_are_rejected(self) -> None:
        document = valid_document()
        children = document["blocks"][0]["root"]["children"]  # type: ignore[index]
        children[1]["id"] = "a"
        with self.assertRaisesRegex(ArmadaArtifactError, "duplicate"):
            canonicalize_armada_artifact(document)

        document = valid_document()
        children = document["blocks"][0]["root"]["children"]  # type: ignore[index]
        children[2]["to"] = "missing"
        with self.assertRaisesRegex(ArmadaArtifactError, "missing node"):
            canonicalize_armada_artifact(document)

    def test_graph_layout_and_semantic_connector_state_are_canonical(self) -> None:
        document = valid_document()
        root = document["blocks"][0]["root"]  # type: ignore[index]
        root["layout"] = "graph"
        root["children"][2]["state"] = "selected"
        canonical, _encoded = canonicalize_armada_artifact(document)
        canonical_root = canonical["blocks"][0]["root"]
        self.assertEqual(canonical_root["layout"], "graph")
        self.assertEqual(canonical_root["children"][2]["state"], "selected")

    def test_circle_labels_stay_compact(self) -> None:
        document = valid_document()
        shape = document["blocks"][0]["root"]["children"][0]  # type: ignore[index]
        shape["label"] = "retract\n$$x_{k+1}=R_{x_k}(v_k)$$"
        with self.assertRaisesRegex(ArmadaArtifactError, "circle labels must be compact"):
            canonicalize_armada_artifact(document)

    def test_coordinate_free_layout_is_rejected(self) -> None:
        document = valid_document()
        root = document["blocks"][0]["root"]  # type: ignore[index]
        root["layout"] = "free"
        with self.assertRaisesRegex(ArmadaArtifactError, "expected one of.*overlay"):
            canonicalize_armada_artifact(document)

    def test_scene_steps_are_bounded_and_reference_typed_targets(self) -> None:
        document = valid_document()
        block = document["blocks"][0]  # type: ignore[index]
        children = block["root"]["children"]
        children[2]["id"] = "force_edge"
        block["steps"] = [
            {
                "label": "Start at A",
                "changes": [
                    {"target": "a", "state": "running", "label": "A  0"},
                ],
            },
            {
                "label": "Reach B",
                "changes": [
                    {"target": "a", "state": "success"},
                    {"target": "force_edge", "state": "selected"},
                    {"target": "b", "state": "running", "label": "B  1"},
                ],
            },
        ]
        canonical, _encoded = canonicalize_armada_artifact(document)
        self.assertEqual(canonical["blocks"][0]["steps"][1]["label"], "Reach B")

        block["steps"][1]["changes"][0]["target"] = "missing"
        with self.assertRaisesRegex(ArmadaArtifactError, "missing target"):
            canonicalize_armada_artifact(document)

        block["steps"][1]["changes"][0] = {
            "target": "force_edge",
            "label": "not a shape",
        }
        with self.assertRaisesRegex(ArmadaArtifactError, "shape target"):
            canonicalize_armada_artifact(document)

        block["steps"][1]["changes"][0] = {"target": "force_edge"}
        with self.assertRaisesRegex(ArmadaArtifactError, "at least 2 properties"):
            canonicalize_armada_artifact(document)

        children.append({"type": "text", "id": "caption", "text": "caption"})
        block["steps"][1]["changes"][0] = {
            "target": "caption",
            "state": "warning",
        }
        with self.assertRaisesRegex(ArmadaArtifactError, "shape or connector target"):
            canonicalize_armada_artifact(document)

    def test_scene_and_step_text_share_one_budget(self) -> None:
        document = valid_document()
        block = document["blocks"][0]  # type: ignore[index]
        children = block["root"]["children"]
        children.extend(
            {"type": "text", "id": f"copy_{index}", "text": "x" * 1000} for index in range(11)
        )
        block["steps"] = [
            {"label": "One", "changes": [{"target": "copy_0", "text": "y" * 1000}]},
            {"label": "Two", "changes": [{"target": "copy_1", "text": "z" * 1000}]},
        ]
        with self.assertRaisesRegex(ArmadaArtifactError, "text budget"):
            canonicalize_armada_artifact(document)

    def test_rejects_primitive_fields_that_the_renderer_would_ignore(self) -> None:
        for ignored in (
            {"children": [{"type": "text", "text": "hidden"}]},
            {"points": [[0, 0], [1, 1]]},
            {"control": "button"},
        ):
            document = valid_document()
            shape = document["blocks"][0]["root"]["children"][0]  # type: ignore[index]
            shape.update(ignored)
            with (
                self.subTest(ignored=ignored),
                self.assertRaisesRegex(ArmadaArtifactError, "unsupported properties"),
            ):
                canonicalize_armada_artifact(document)

    def test_node_depth_and_source_budgets_are_enforced(self) -> None:
        document = valid_document()
        root = document["blocks"][0]["root"]  # type: ignore[index]
        for _index in range(9):
            child = {"type": "group", "layout": "column", "children": []}
            root["children"] = [child]
            root = child
        root["children"] = [{"type": "text", "text": "end"}]
        with self.assertRaisesRegex(ArmadaArtifactError, "depth"):
            canonicalize_armada_artifact(document)

        document = valid_document()
        document["blocks"] = [{"type": "copy", "body": "x" * 70_000}]
        with self.assertRaisesRegex(ArmadaArtifactError, "exceeds"):
            canonicalize_armada_artifact(document)

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
        canonicalize_armada_artifact(document)

        document["blocks"][0]["root"]["id"] = "duplicated"  # type: ignore[index]
        with self.assertRaisesRegex(ArmadaArtifactError, "cannot contain ids"):
            canonicalize_armada_artifact(document)

        document["blocks"][0]["root"].pop("id")  # type: ignore[index]
        document["blocks"][0]["root"]["repeat"]["limit"] = 64  # type: ignore[index]
        document["blocks"][0]["root"]["children"] = [  # type: ignore[index]
            {"type": "text", "text": "nested"},
            {"type": "text", "text": "nested"},
        ]
        with self.assertRaisesRegex(ArmadaArtifactError, "expanded scene"):
            canonicalize_armada_artifact(document)

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
        canonical, _encoded = canonicalize_armada_artifact(document)
        self.assertEqual(canonical["blocks"][0]["root"]["derive"]["op"], "divide")

    def test_media_sources_exist_only_at_the_tool_boundary(self) -> None:
        authored = {
            "version": 1,
            "blocks": [
                {
                    "type": "media",
                    "kind": "video",
                    "source": ".armada/artifacts/wave.webm",
                    "sourceDark": ".armada/artifacts/wave-dark.webm",
                    "posterSource": ".armada/artifacts/wave.webp",
                    "posterSourceDark": ".armada/artifacts/wave-dark.webp",
                    "alt": "A wave packet disperses over time.",
                    "motion": "once",
                }
            ],
        }
        canonicalize_armada_artifact(authored, allow_unresolved_media=True)
        with self.assertRaisesRegex(ArmadaArtifactError, "persisted media requires src"):
            canonicalize_armada_artifact(authored)

        persisted = {
            "version": 1,
            "blocks": [
                {
                    "type": "media",
                    "kind": "image",
                    "src": "/user_uploads/1/ab/diagram.webp",
                    "srcDark": "/user_uploads/1/cd/diagram-dark.webp",
                    "alt": "A constraint diagram.",
                }
            ],
        }
        canonicalize_armada_artifact(persisted)
        with self.assertRaisesRegex(ArmadaArtifactError, "cannot provide upload URLs"):
            canonicalize_armada_artifact(persisted, allow_unresolved_media=True)

        vector = {
            "version": 1,
            "blocks": [
                {
                    "type": "media",
                    "kind": "image",
                    "source": ".armada/artifacts/lattice.svg",
                    "sourceDark": ".armada/artifacts/lattice-dark.svg",
                    "alt": "A vector crystal lattice.",
                }
            ],
        }
        canonicalize_armada_artifact(vector, allow_unresolved_media=True)

        del cast(list[dict[str, str]], vector["blocks"])[0]["sourceDark"]
        with self.assertRaisesRegex(ArmadaArtifactError, "light and dark sources"):
            canonicalize_armada_artifact(vector, allow_unresolved_media=True)

    def test_media_types_and_count_are_bounded(self) -> None:
        media = {
            "type": "media",
            "kind": "image",
            "source": ".armada/artifacts/not-an-image.webm",
            "alt": "Wrong type.",
        }
        with self.assertRaisesRegex(ArmadaArtifactError, "PNG, SVG, or WebP"):
            canonicalize_armada_artifact(
                {"version": 1, "blocks": [media]}, allow_unresolved_media=True
            )

        media["kind"] = "video"
        media["sourceDark"] = ".armada/artifacts/not-an-image-dark.webm"
        media["posterSource"] = ".armada/artifacts/not-an-image.webp"
        media["posterSourceDark"] = ".armada/artifacts/not-an-image-dark.webp"
        document = {"version": 1, "blocks": [dict(media), dict(media), dict(media)]}
        with self.assertRaisesRegex(ArmadaArtifactError, "2 media blocks"):
            canonicalize_armada_artifact(document, allow_unresolved_media=True)

    def test_video_media_requires_both_themes_and_posters(self) -> None:
        media = {
            "type": "media",
            "kind": "video",
            "source": ".armada/artifacts/step.webm",
            "alt": "A tangent vector is retracted onto the manifold.",
        }
        with self.assertRaisesRegex(ArmadaArtifactError, "light and dark WebM"):
            canonicalize_armada_artifact(
                {"version": 1, "blocks": [media]}, allow_unresolved_media=True
            )
