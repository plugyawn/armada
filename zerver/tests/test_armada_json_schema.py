from typing import Any

from zerver.lib.armada_json_schema import json_schema_error
from zerver.lib.test_classes import ZulipTestCase


class JsonSchemaTest(ZulipTestCase):
    """The JSON Schema subset Armada's contracts are written in
    (zerver/lib/armada_json_schema.py), each keyword on its own."""

    def error(self, schema: dict[str, Any], value: object) -> str | None:
        return json_schema_error(schema, value)

    def test_references_resolve_within_the_root(self) -> None:
        root = {"$defs": {"name": {"type": "string"}}, "$ref": "#/$defs/name"}
        self.assertIsNone(self.error(root, "wandb"))
        self.assertEqual(self.error(root, 3), "$: expected type string")
        self.assertEqual(
            self.error({"$ref": "https://example.com/schema"}, 3),
            "$: contract contains an unresolved reference 'https://example.com/schema'",
        )
        self.assertEqual(
            self.error({"$ref": "#/$defs/missing"}, 3),
            "$: contract contains an unresolved reference '#/$defs/missing'",
        )

    def test_types(self) -> None:
        for kind, good, bad in (
            ("null", None, 0),
            ("array", [], {}),
            ("object", {}, []),
            ("boolean", True, 1),
            ("integer", 1, 1.5),
            ("number", 1.5, True),
            ("string", "a", 1),
        ):
            self.assertIsNone(self.error({"type": kind}, good))
            self.assertEqual(self.error({"type": kind}, bad), f"$: expected type {kind}")
        self.assertEqual(self.error({"type": "date"}, "2026-10-16"), "$: expected type date")
        self.assertIsNone(self.error({"type": ["string", "null"]}, None))

    def test_one_of_names_the_variant_of_the_same_type(self) -> None:
        root: dict[str, Any] = {
            "oneOf": [
                5,
                {"$ref": "#/$defs/nowhere"},
                {"required": ["x"]},
                {"properties": {"type": {"const": "a"}}, "required": ["y"]},
            ]
        }
        self.assertEqual(self.error(root, {"type": "a"}), "$: missing required property 'y'")
        self.assertEqual(
            self.error(root, {"type": "b"}), "$: value does not match any allowed contract variant"
        )
        self.assertEqual(
            self.error({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1),
            "$: value matches multiple mutually exclusive contract variants",
        )
        self.assertIsNone(self.error({"anyOf": [{"type": "number"}, {"type": "integer"}]}, 1))

    def test_strings_and_numbers(self) -> None:
        self.assertEqual(
            self.error({"minLength": 2}, "a"), "$: string must contain at least 2 characters"
        )
        self.assertEqual(self.error({"maxLength": 2}, "abc"), "$: string exceeds 2 characters")
        self.assertEqual(self.error({"pattern": "^a"}, "ba"), "$: value \"ba\" does not match '^a'")
        self.assertEqual(self.error({"minimum": 1}, 0), "$: value must be >= 1")
        self.assertEqual(self.error({"maximum": 1}, 2), "$: value must be <= 1")
        self.assertEqual(self.error({"exclusiveMinimum": 0}, 0), "$: value must be > 0")

    def test_arrays(self) -> None:
        pair = {"prefixItems": [{"type": "number"}, {"type": "number"}], "items": False}
        self.assertIsNone(self.error(pair, [1, 2]))
        self.assertEqual(self.error(pair, [1]), "$: array is missing item 1")
        self.assertEqual(self.error(pair, [1, "2"]), "$[1]: expected type number")
        self.assertEqual(
            self.error(pair, [1, 2, 3]), "$: array contains unsupported trailing items"
        )
        self.assertEqual(
            self.error({"prefixItems": [True]}, [1]), "$[0]: contract item schema is invalid"
        )
        self.assertEqual(self.error({"minItems": 2}, [1]), "$: array must contain at least 2 items")
        self.assertEqual(self.error({"minItems": 1}, []), "$: array must contain at least 1 item")
        self.assertEqual(self.error({"maxItems": 1}, [1, 2]), "$: array exceeds 1 items")
        self.assertEqual(
            self.error({"items": {"type": "string"}}, ["a", 2]), "$[1]: expected type string"
        )

    def test_objects(self) -> None:
        self.assertEqual(
            self.error({"minProperties": 1}, {}), "$: object must contain at least 1 properties"
        )
        self.assertEqual(
            self.error({"maxProperties": 1}, {"a": 1, "b": 2}), "$: object exceeds 1 properties"
        )
        self.assertEqual(self.error({"required": ["a"]}, {}), "$: missing required property 'a'")
        closed = {"properties": {"a": {"type": "string"}}, "additionalProperties": False}
        self.assertEqual(self.error(closed, {"a": 1}), "$.a: expected type string")
        self.assertEqual(self.error(closed, {"b": 1}), "$.b: unsupported property")
        typed = {"additionalProperties": {"type": "number"}}
        self.assertEqual(self.error(typed, {"b": "1"}), "$.b: expected type number")
        self.assertEqual(self.error({"const": 1}, 2), "$: expected 1, got 2")
        self.assertEqual(self.error({"enum": ["a"]}, "b"), '$: expected one of ["a"], got "b"')
