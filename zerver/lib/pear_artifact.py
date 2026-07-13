from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from zerver.lib.pear_artifact_contract import PEAR_ARTIFACT_SCHEMA

PEAR_ARTIFACT_SUBMESSAGE_TYPE = "pear_artifact"
PEAR_ARTIFACT_TOOL_RESULT_LABEL = "Artifact"
PEAR_ARTIFACT_SERIALIZED_LIMIT = 64_000
PEAR_ARTIFACT_NODE_LIMIT = 128
PEAR_ARTIFACT_DEPTH_LIMIT = 8
PEAR_ARTIFACT_TOTAL_TEXT_LIMIT = 12_000


class PearArtifactError(ValueError):
    pass


def _schema_reference(reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: object = PEAR_ARTIFACT_SCHEMA
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


def _validates(schema: dict[str, Any], value: object) -> bool:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target = _schema_reference(reference)
        return target is not None and _validates(target, value)
    variants = schema.get("oneOf")
    if isinstance(variants, list):
        return (
            sum(isinstance(variant, dict) and _validates(variant, value) for variant in variants)
            == 1
        )
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        return any(isinstance(variant, dict) and _validates(variant, value) for variant in variants)
    if "const" in schema and value != schema["const"]:
        return False
    choices = schema.get("enum")
    if isinstance(choices, list) and value not in choices:
        return False
    raw_type = schema.get("type")
    types = raw_type if isinstance(raw_type, list) else [] if raw_type is None else [raw_type]
    if types and not any(
        isinstance(expected, str) and _has_type(value, expected) for expected in types
    ):
        return False
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return False
    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(minimum, int | float) and value < minimum:
            return False
        if isinstance(maximum, int | float) and value > maximum:
            return False
        if isinstance(exclusive_minimum, int | float) and value <= exclusive_minimum:
            return False
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        prefix = schema.get("prefixItems")
        if isinstance(prefix, list):
            for index, child_schema in enumerate(prefix):
                if (
                    index >= len(value)
                    or not isinstance(child_schema, dict)
                    or not _validates(child_schema, value[index])
                ):
                    return False
        start = len(prefix) if isinstance(prefix, list) else 0
        items = schema.get("items")
        if items is False and len(value) > start:
            return False
        if isinstance(items, dict) and any(not _validates(items, item) for item in value[start:]):
            return False
    if isinstance(value, dict):
        maximum = schema.get("maxProperties")
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        required = schema.get("required")
        if isinstance(required, list) and any(
            key not in value for key in required if isinstance(key, str)
        ):
            return False
        properties = schema.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        for key, child in value.items():
            child_schema = property_schemas.get(key)
            if isinstance(child_schema, dict):
                if not _validates(child_schema, child):
                    return False
                continue
            additional = schema.get("additionalProperties")
            if additional is False:
                return False
            if isinstance(additional, dict) and not _validates(additional, child):
                return False
    return True


def _iter_nodes(node: dict[str, Any], *, depth: int = 1) -> Iterable[tuple[dict[str, Any], int]]:
    yield node, depth
    children = node.get("children")
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict):
            yield from _iter_nodes(child, depth=depth + 1)


def _validate_scene(root: dict[str, Any]) -> None:
    nodes = list(_iter_nodes(root))
    if len(nodes) > PEAR_ARTIFACT_NODE_LIMIT:
        raise PearArtifactError(f"scene exceeds {PEAR_ARTIFACT_NODE_LIMIT} nodes")
    if max((depth for _node, depth in nodes), default=0) > PEAR_ARTIFACT_DEPTH_LIMIT:
        raise PearArtifactError(f"scene exceeds depth {PEAR_ARTIFACT_DEPTH_LIMIT}")

    def expanded_node_count(node: dict[str, Any]) -> int:
        children = node.get("children")
        subtree = (
            1 + sum(expanded_node_count(child) for child in children if isinstance(child, dict))
            if isinstance(children, list)
            else 1
        )
        repeat = node.get("repeat")
        multiplier = repeat.get("limit", 16) if isinstance(repeat, dict) else 1
        return subtree * multiplier

    if expanded_node_count(root) > PEAR_ARTIFACT_NODE_LIMIT:
        raise PearArtifactError(f"expanded scene exceeds {PEAR_ARTIFACT_NODE_LIMIT} nodes")

    ids: set[str] = set()
    connector_refs: list[tuple[str, str]] = []
    total_text = 0
    node_schema = PEAR_ARTIFACT_SCHEMA["$defs"]["node"]
    allowed_properties_by_type = node_schema["x-pear-type-properties"]
    for node, _depth in nodes:
        if "repeat" in node:
            repeated_nodes = list(_iter_nodes(node))
            if any(
                repeated.get("type") == "connector" or isinstance(repeated.get("id"), str)
                for repeated, _repeated_depth in repeated_nodes
            ):
                raise PearArtifactError("repeated scene subtrees cannot contain ids or connectors")
        node_id = node.get("id")
        if isinstance(node_id, str):
            if node_id in ids:
                raise PearArtifactError(f"duplicate scene node id: {node_id}")
            ids.add(node_id)
        for key in ("text", "label", "ariaLabel"):
            value = node.get(key)
            if isinstance(value, str):
                total_text += len(value)
        node_type = node.get("type")
        allowed_properties = allowed_properties_by_type.get(node_type)
        if not isinstance(allowed_properties, list):
            raise PearArtifactError(f"unsupported scene node type: {node_type}")
        ignored_properties = set(node) - set(allowed_properties)
        if ignored_properties:
            names = ", ".join(sorted(ignored_properties))
            raise PearArtifactError(f"{node_type} node has unsupported properties: {names}")
        if node_type == "group":
            if node.get("layout") not in {"row", "column", "grid", "overlay", "free"}:
                raise PearArtifactError("group nodes require a layout")
            if not node.get("children"):
                raise PearArtifactError("group nodes require children")
        elif node_type == "text":
            if (
                not isinstance(node.get("text"), str)
                and not isinstance(node.get("bind"), str)
                and not isinstance(node.get("derive"), dict)
            ):
                raise PearArtifactError("text nodes require text, bind, or derive")
        elif node_type == "shape":
            if not isinstance(node.get("shape"), str):
                raise PearArtifactError("shape nodes require shape")
        elif node_type == "connector":
            start, end = node.get("from"), node.get("to")
            if not isinstance(start, str) or not isinstance(end, str):
                raise PearArtifactError("connector nodes require from and to ids")
            connector_refs.append((start, end))
        elif node_type == "path":
            points = node.get("points")
            if not isinstance(points, list) or len(points) < 2:
                raise PearArtifactError("path nodes require at least two points")
        elif node_type == "symbol":
            if not isinstance(node.get("symbol"), str):
                raise PearArtifactError("symbol nodes require symbol")
        elif node_type == "mark":
            if not isinstance(node.get("mark"), str) or not (
                isinstance(node.get("values"), list) or isinstance(node.get("bind"), str)
            ):
                raise PearArtifactError("mark nodes require mark plus values or bind")
        elif node_type == "control":
            if not isinstance(node.get("control"), str) or not isinstance(node.get("label"), str):
                raise PearArtifactError("control nodes require control and label")
            minimum, maximum = node.get("min"), node.get("max")
            if (
                node.get("control") in {"range", "stepper"}
                and isinstance(minimum, int | float)
                and isinstance(maximum, int | float)
                and minimum > maximum
            ):
                raise PearArtifactError("control min cannot exceed max")
    if total_text > PEAR_ARTIFACT_TOTAL_TEXT_LIMIT:
        raise PearArtifactError("scene text budget exceeded")
    for start, end in connector_refs:
        if start not in ids or end not in ids:
            raise PearArtifactError(f"connector references missing node: {start} -> {end}")


def canonicalize_pear_artifact(value: object) -> tuple[dict[str, Any], str]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise PearArtifactError(f"artifact is not finite JSON: {error}") from error
    if len(encoded.encode()) > PEAR_ARTIFACT_SERIALIZED_LIMIT:
        raise PearArtifactError(f"artifact exceeds {PEAR_ARTIFACT_SERIALIZED_LIMIT} bytes")
    decoded: object = json.loads(encoded)
    if not _validates(PEAR_ARTIFACT_SCHEMA, decoded):
        raise PearArtifactError("document does not match the Pear artifact contract")
    assert isinstance(decoded, dict)
    for block in decoded["blocks"]:
        if block["type"] == "scene":
            _validate_scene(block["root"])
    return decoded, encoded


def pear_artifact_node_count(document: dict[str, Any]) -> int:
    return sum(
        sum(1 for _node, _depth in _iter_nodes(block["root"]))
        for block in document.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "scene"
    )


def artifact_tool_result(encoded: str) -> dict[str, str]:
    return {"label": PEAR_ARTIFACT_TOOL_RESULT_LABEL, "value": encoded}


def artifacts_from_tool_results(tool_results: Iterable[dict[str, str]]) -> list[str]:
    artifacts: list[str] = []
    for result in tool_results:
        if result.get("label") != PEAR_ARTIFACT_TOOL_RESULT_LABEL:
            continue
        try:
            _document, encoded = canonicalize_pear_artifact(json.loads(result.get("value", "")))
        except (json.JSONDecodeError, PearArtifactError):
            continue
        artifacts.append(encoded)
    return artifacts
