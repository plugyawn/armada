"""The subset of JSON Schema that Armada's contracts are written in,
checked without a dependency: types, const and enum, string lengths and
patterns, number bounds, array lengths and items (prefixItems too),
object properties, propertyNames, required and additionalProperties,
local $refs, and oneOf and anyOf. The artifact contract (zerver/lib/armada_artifact.py)
and the plugin contract (zerver/lib/armada_plugin_validation.py) are
checked with it. An error names the first failing path, as $.a.b[2].
"""

import json
import re
from typing import Any


def json_schema_error(root: dict[str, Any], value: object) -> str | None:
    """The first way value fails the schema root, or None if it passes.
    A $ref is resolved within root."""
    return _validation_error(root, root, value, path="$")


def _schema_reference(root: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: object = root
    for encoded in reference[2:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def _has_type(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    return False


def _schema_variant_for_type(
    root: dict[str, Any], variants: list[object], value: object
) -> dict[str, Any] | None:
    """The variant a value that fits none was meant for, whose error names
    what is wrong with it: the object variant whose "type" property is
    the value's, or else the one variant of the value's own JSON type."""
    resolved_variants = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        reference = variant.get("$ref")
        resolved = _schema_reference(root, reference) if isinstance(reference, str) else variant
        if isinstance(resolved, dict):
            resolved_variants.append((variant, resolved))
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        typed = [
            variant
            for variant, resolved in resolved_variants
            if isinstance(resolved.get("type"), str) and _has_type(value, resolved["type"])
        ]
        return typed[0] if len(typed) == 1 else None
    value_type = value["type"]
    for variant, resolved in resolved_variants:
        properties = resolved.get("properties")
        if not isinstance(properties, dict):
            continue
        type_schema = properties.get("type")
        if isinstance(type_schema, dict) and type_schema.get("const") == value_type:
            return variant
    return None


def _value_preview(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return encoded if len(encoded) <= 120 else encoded[:117] + "..."


def _validation_error(
    root: dict[str, Any], schema: dict[str, Any], value: object, *, path: str
) -> str | None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target = _schema_reference(root, reference)
        if target is None:
            return f"{path}: contract contains an unresolved reference {reference!r}"
        return _validation_error(root, target, value, path=path)
    variants = schema.get("oneOf")
    if isinstance(variants, list):
        valid_variants = [
            variant
            for variant in variants
            if isinstance(variant, dict)
            and _validation_error(root, variant, value, path=path) is None
        ]
        if len(valid_variants) == 1:
            return None
        if len(valid_variants) > 1:
            return f"{path}: value matches multiple mutually exclusive contract variants"
        preferred = _schema_variant_for_type(root, variants, value)
        if preferred is not None:
            return _validation_error(root, preferred, value, path=path)
        return f"{path}: value does not match any allowed contract variant"
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        errors = [
            _validation_error(root, variant, value, path=path)
            for variant in variants
            if isinstance(variant, dict)
        ]
        if any(error is None for error in errors):
            return None
        return errors[0] if errors else f"{path}: value does not match any allowed contract variant"
    if "const" in schema and value != schema["const"]:
        return f"{path}: expected {_value_preview(schema['const'])}, got {_value_preview(value)}"
    choices = schema.get("enum")
    if isinstance(choices, list) and value not in choices:
        return f"{path}: expected one of {_value_preview(choices)}, got {_value_preview(value)}"
    raw_type = schema.get("type")
    types = raw_type if isinstance(raw_type, list) else [] if raw_type is None else [raw_type]
    if types and not any(
        isinstance(expected, str) and _has_type(value, expected) for expected in types
    ):
        return f"{path}: expected type {' or '.join(str(expected) for expected in types)}"
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            return f"{path}: string must contain at least {minimum} characters"
        if isinstance(maximum, int) and len(value) > maximum:
            return f"{path}: string exceeds {maximum} characters"
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return f"{path}: value {_value_preview(value)} does not match {pattern!r}"
    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(minimum, int | float) and value < minimum:
            return f"{path}: value must be >= {minimum}"
        if isinstance(maximum, int | float) and value > maximum:
            return f"{path}: value must be <= {maximum}"
        if isinstance(exclusive_minimum, int | float) and value <= exclusive_minimum:
            return f"{path}: value must be > {exclusive_minimum}"
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            unit = "item" if minimum == 1 else "items"
            return f"{path}: array must contain at least {minimum} {unit}"
        if isinstance(maximum, int) and len(value) > maximum:
            return f"{path}: array exceeds {maximum} items"
        prefix = schema.get("prefixItems")
        if isinstance(prefix, list):
            for index, child_schema in enumerate(prefix):
                if index >= len(value):
                    return f"{path}: array is missing item {index}"
                if not isinstance(child_schema, dict):
                    return f"{path}[{index}]: contract item schema is invalid"
                error = _validation_error(root, child_schema, value[index], path=f"{path}[{index}]")
                if error is not None:
                    return error
        start = len(prefix) if isinstance(prefix, list) else 0
        items = schema.get("items")
        if items is False and len(value) > start:
            return f"{path}: array contains unsupported trailing items"
        if isinstance(items, dict):
            for index, item in enumerate(value[start:], start=start):
                error = _validation_error(root, items, item, path=f"{path}[{index}]")
                if error is not None:
                    return error
    if isinstance(value, dict):
        minimum = schema.get("minProperties")
        maximum = schema.get("maxProperties")
        if isinstance(minimum, int) and len(value) < minimum:
            return f"{path}: object must contain at least {minimum} properties"
        if isinstance(maximum, int) and len(value) > maximum:
            return f"{path}: object exceeds {maximum} properties"
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    return f"{path}: missing required property {key!r}"
        names = schema.get("propertyNames")
        if isinstance(names, dict):
            for key in value:
                error = _validation_error(root, names, key, path=f"{path}.{key}")
                if error is not None:
                    return error
        properties = schema.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        for key, child in value.items():
            child_path = f"{path}.{key}"
            child_schema = property_schemas.get(key)
            if isinstance(child_schema, dict):
                error = _validation_error(root, child_schema, child, path=child_path)
                if error is not None:
                    return error
                continue
            additional = schema.get("additionalProperties")
            if additional is False:
                return f"{child_path}: unsupported property"
            if isinstance(additional, dict):
                error = _validation_error(root, additional, child, path=child_path)
                if error is not None:
                    return error
    return None
