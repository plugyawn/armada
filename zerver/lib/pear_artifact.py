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
PEAR_ARTIFACT_MEDIA_BLOCK_LIMIT = 2


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


def _schema_variant_for_type(variants: list[object], value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        return None
    value_type = value["type"]
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        reference = variant.get("$ref")
        resolved = _schema_reference(reference) if isinstance(reference, str) else variant
        if not isinstance(resolved, dict):
            continue
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


def _validation_error(schema: dict[str, Any], value: object, *, path: str = "$") -> str | None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target = _schema_reference(reference)
        if target is None:
            return f"{path}: contract contains an unresolved reference {reference!r}"
        return _validation_error(target, value, path=path)
    variants = schema.get("oneOf")
    if isinstance(variants, list):
        valid_variants = [
            variant
            for variant in variants
            if isinstance(variant, dict) and _validation_error(variant, value, path=path) is None
        ]
        if len(valid_variants) == 1:
            return None
        if len(valid_variants) > 1:
            return f"{path}: value matches multiple mutually exclusive contract variants"
        preferred = _schema_variant_for_type(variants, value)
        if preferred is not None:
            return _validation_error(preferred, value, path=path)
        return f"{path}: value does not match any allowed contract variant"
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        errors = [
            _validation_error(variant, value, path=path)
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
                error = _validation_error(child_schema, value[index], path=f"{path}[{index}]")
                if error is not None:
                    return error
        start = len(prefix) if isinstance(prefix, list) else 0
        items = schema.get("items")
        if items is False and len(value) > start:
            return f"{path}: array contains unsupported trailing items"
        if isinstance(items, dict):
            for index, item in enumerate(value[start:], start=start):
                error = _validation_error(items, item, path=f"{path}[{index}]")
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
        properties = schema.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        for key, child in value.items():
            child_path = f"{path}.{key}"
            child_schema = property_schemas.get(key)
            if isinstance(child_schema, dict):
                error = _validation_error(child_schema, child, path=child_path)
                if error is not None:
                    return error
                continue
            additional = schema.get("additionalProperties")
            if additional is False:
                return f"{child_path}: unsupported property"
            if isinstance(additional, dict):
                error = _validation_error(additional, child, path=child_path)
                if error is not None:
                    return error
    return None


def _validates(schema: dict[str, Any], value: object) -> bool:
    return _validation_error(schema, value) is None


def _iter_nodes(node: dict[str, Any], *, depth: int = 1) -> Iterable[tuple[dict[str, Any], int]]:
    yield node, depth
    children = node.get("children")
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict):
            yield from _iter_nodes(child, depth=depth + 1)


def _validate_scene(root: dict[str, Any]) -> int:
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
            if node.get("layout") not in {"row", "column", "grid", "graph", "overlay", "free"}:
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
    return total_text


def _validate_scene_steps(block: dict[str, Any], scene_text: int) -> None:
    steps = block.get("steps")
    if not isinstance(steps, list):
        return
    root = block.get("root")
    assert isinstance(root, dict)
    target_types = {
        node_id: str(node.get("type"))
        for node, _depth in _iter_nodes(root)
        if isinstance((node_id := node.get("id")), str)
    }
    total_text = scene_text
    for step_index, step in enumerate(steps):
        assert isinstance(step, dict)
        label = step.get("label")
        if isinstance(label, str):
            total_text += len(label)
        changes = step.get("changes")
        assert isinstance(changes, list)
        seen_targets: set[str] = set()
        for change in changes:
            assert isinstance(change, dict)
            target = change.get("target")
            assert isinstance(target, str)
            target_type = target_types.get(target)
            if target_type is None:
                raise PearArtifactError(f"scene step references missing target: {target}")
            if target in seen_targets:
                raise PearArtifactError(
                    f"scene step {step_index + 1} changes target {target} more than once"
                )
            seen_targets.add(target)
            if "state" in change and target_type not in {"shape", "connector"}:
                raise PearArtifactError(
                    "scene step state changes require a shape or connector target"
                )
            if "label" in change and target_type != "shape":
                raise PearArtifactError("scene step label changes require a shape target")
            if "text" in change and target_type != "text":
                raise PearArtifactError("scene step text changes require a text target")
            for key in ("label", "text"):
                value = change.get(key)
                if isinstance(value, str):
                    total_text += len(value)
    if total_text > PEAR_ARTIFACT_TOTAL_TEXT_LIMIT:
        raise PearArtifactError("scene step text budget exceeded")


def _validate_media(block: dict[str, Any], *, allow_unresolved_media: bool) -> None:
    source_keys = {"source", "sourceDark", "posterSource", "posterSourceDark"}
    resolved_keys = {"src", "srcDark", "poster", "posterDark"}
    present_source_keys = source_keys & block.keys()
    present_resolved_keys = resolved_keys & block.keys()
    if allow_unresolved_media:
        if "source" not in block or present_resolved_keys:
            raise PearArtifactError("authored media requires source and cannot provide upload URLs")
    elif "src" not in block or present_source_keys:
        raise PearArtifactError("persisted media requires src and cannot contain workspace paths")

    primary = block.get("source") if allow_unresolved_media else block.get("src")
    dark = block.get("sourceDark") if allow_unresolved_media else block.get("srcDark")
    poster = block.get("posterSource") if allow_unresolved_media else block.get("poster")
    poster_dark = (
        block.get("posterSourceDark") if allow_unresolved_media else block.get("posterDark")
    )
    assert isinstance(primary, str)
    kind = block.get("kind")
    primary_extension = primary.rsplit(".", 1)[-1].lower()
    if kind == "image" and primary_extension not in {"png", "svg", "webp"}:
        raise PearArtifactError("image media requires a PNG, SVG, or WebP source")
    if kind == "video" and primary_extension != "webm":
        raise PearArtifactError("video media requires a WebM source")
    if kind == "image" and (poster is not None or poster_dark is not None):
        raise PearArtifactError("image media cannot provide a video poster")
    if kind == "video" and poster_dark is not None and poster is None:
        raise PearArtifactError("a dark video poster requires a default poster")
    for poster_value in (poster, poster_dark):
        if isinstance(poster_value, str) and poster_value.rsplit(".", 1)[-1].lower() not in {
            "png",
            "webp",
        }:
            raise PearArtifactError("video posters require PNG or WebP sources")
    if dark is not None:
        assert isinstance(dark, str)
        dark_extension = dark.rsplit(".", 1)[-1].lower()
        if dark_extension != primary_extension:
            raise PearArtifactError("light and dark media sources require the same file type")


def canonicalize_pear_artifact(
    value: object, *, allow_unresolved_media: bool = False
) -> tuple[dict[str, Any], str]:
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
    schema_error = _validation_error(PEAR_ARTIFACT_SCHEMA, decoded)
    if schema_error is not None:
        raise PearArtifactError(schema_error)
    assert isinstance(decoded, dict)
    media_blocks = [block for block in decoded["blocks"] if block["type"] == "media"]
    if len(media_blocks) > PEAR_ARTIFACT_MEDIA_BLOCK_LIMIT:
        raise PearArtifactError(f"artifact exceeds {PEAR_ARTIFACT_MEDIA_BLOCK_LIMIT} media blocks")
    for block in decoded["blocks"]:
        if block["type"] == "scene":
            scene_text = _validate_scene(block["root"])
            _validate_scene_steps(block, scene_text)
        elif block["type"] == "media":
            _validate_media(block, allow_unresolved_media=allow_unresolved_media)
    return decoded, encoded


def pear_artifact_node_count(document: dict[str, Any]) -> int:
    return sum(
        sum(1 for _node, _depth in _iter_nodes(block["root"]))
        for block in document.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "scene"
    )


def artifact_tool_result(encoded: str) -> dict[str, str]:
    return {"label": PEAR_ARTIFACT_TOOL_RESULT_LABEL, "value": encoded}


def artifacts_from_tool_results(
    tool_results: Iterable[dict[str, str]], *, allow_unresolved_media: bool = False
) -> list[str]:
    artifacts: list[str] = []
    for result in tool_results:
        if result.get("label") != PEAR_ARTIFACT_TOOL_RESULT_LABEL:
            continue
        try:
            _document, encoded = canonicalize_pear_artifact(
                json.loads(result.get("value", "")),
                allow_unresolved_media=allow_unresolved_media,
            )
        except (json.JSONDecodeError, PearArtifactError):
            continue
        artifacts.append(encoded)
    return artifacts
