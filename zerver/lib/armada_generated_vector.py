from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DefusedET

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ARMADA_SVG_SOURCE_LIMIT = 1_000_000
ARMADA_SVG_NODE_LIMIT = 5_000
ARMADA_SVG_TEXT_LIMIT = 100_000
ARMADA_SVG_DEPTH_LIMIT = 64

ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "defs",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "linearGradient",
        "radialGradient",
        "stop",
        "marker",
        "clipPath",
    }
)
HOSTILE_ELEMENTS = frozenset(
    {"script", "foreignobject", "iframe", "embed", "object", "handler", "annotation-xml"}
)
TEXT_ELEMENTS = frozenset({"text", "tspan", "title", "desc"})
ALLOWED_ATTRIBUTES = frozenset(
    {
        "id",
        "transform",
        "version",
        "viewBox",
        "preserveAspectRatio",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "dx",
        "dy",
        "rotate",
        "offset",
        "pathLength",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "opacity",
        "color",
        "stop-color",
        "stop-opacity",
        "paint-order",
        "vector-effect",
        "shape-rendering",
        "visibility",
        "display",
        "font",
        "font-family",
        "font-size",
        "font-size-adjust",
        "font-weight",
        "font-style",
        "letter-spacing",
        "text-anchor",
        "dominant-baseline",
        "alignment-baseline",
        "baseline-shift",
        "textLength",
        "lengthAdjust",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "markerWidth",
        "markerHeight",
        "markerUnits",
        "refX",
        "refY",
        "orient",
        "clipPathUnits",
        "clip-path",
        "clip-rule",
        "marker-start",
        "marker-mid",
        "marker-end",
        "ascent",
        "descent",
        "role",
        "aria-label",
        "aria-hidden",
        "style",
    }
)
ALLOWED_STYLE_PROPERTIES = frozenset(
    {"font", "font-family", "font-size", "font-weight", "font-style", "letter-spacing"}
)
HOSTILE_VALUE = re.compile(r"(?:javascript|data|https?|file)\s*:", re.IGNORECASE)
URL_REFERENCE = re.compile(r"url\(\s*(['\"]?)#([^)'\"\s]+)\1\s*\)", re.IGNORECASE)


class ArmadaGeneratedVectorError(ValueError):
    pass


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _safe_style(value: str) -> str | None:
    kept: list[str] = []
    for declaration in value.split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            return None
        property_name, property_value = declaration.split(":", 1)
        property_name = property_name.strip().lower()
        property_value = property_value.strip()
        if property_name not in ALLOWED_STYLE_PROPERTIES:
            continue
        if (
            not property_value
            or len(property_value) > 200
            or re.search(r"[{}<>\\]", property_value)
            or "url(" in property_value.lower()
            or "expression" in property_value.lower()
            or "@" in property_value
        ):
            return None
        kept.append(f"{property_name}: {property_value}")
    return "; ".join(kept) or None


def sanitize_armada_generated_vector(data: bytes) -> bytes:
    """Rebuild generated vector media without adopting authored XML nodes."""
    if not data or len(data) > ARMADA_SVG_SOURCE_LIMIT or b"\x00" in data:
        raise ArmadaGeneratedVectorError("SVG source exceeds its bounded input contract")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArmadaGeneratedVectorError("SVG source must be UTF-8") from error
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", source, re.IGNORECASE):
        raise ArmadaGeneratedVectorError("SVG document declarations are not allowed")
    try:
        root = DefusedET.fromstring(source)
    except ET.ParseError as error:
        raise ArmadaGeneratedVectorError("SVG source is malformed") from error
    if _local_name(root.tag) != "svg":
        raise ArmadaGeneratedVectorError("SVG source must have an svg root")

    nodes = list(root.iter())
    if len(nodes) > ARMADA_SVG_NODE_LIMIT:
        raise ArmadaGeneratedVectorError("SVG node budget exceeded")
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > ARMADA_SVG_DEPTH_LIMIT:
            raise ArmadaGeneratedVectorError("SVG depth budget exceeded")
        stack.extend((child, depth + 1) for child in node)
    text_total = sum(len(node.text or "") + len(node.tail or "") for node in nodes)
    if text_total > ARMADA_SVG_TEXT_LIMIT:
        raise ArmadaGeneratedVectorError("SVG text budget exceeded")

    ids: list[str] = []
    for node in nodes:
        element_name = _local_name(node.tag)
        if element_name.lower() in HOSTILE_ELEMENTS:
            raise ArmadaGeneratedVectorError(f"hostile SVG element rejected: {element_name}")
        for raw_name, value in node.attrib.items():
            name = _local_name(raw_name)
            if name.lower().startswith("on") or HOSTILE_VALUE.search(value):
                raise ArmadaGeneratedVectorError(f"hostile SVG attribute rejected: {name}")
            compact = URL_REFERENCE.sub("", value)
            if "url(" in compact.lower():
                raise ArmadaGeneratedVectorError("external SVG resource rejected")
            if name == "href" and value and not value.startswith("#"):
                raise ArmadaGeneratedVectorError("external SVG link rejected")
            if name == "id" and element_name in ALLOWED_ELEMENTS:
                if value in ids:
                    raise ArmadaGeneratedVectorError("SVG contains a duplicate id")
                ids.append(value)

    digest = hashlib.sha256(data).hexdigest()[:12]
    id_map = {value: f"armada-vector-{digest}-{index}" for index, value in enumerate(ids)}

    def rewrite_value(value: str) -> str:
        unresolved = False

        def replace_reference(match: re.Match[str]) -> str:
            nonlocal unresolved
            replacement = id_map.get(match.group(2))
            if replacement is None:
                unresolved = True
                return ""
            return f"url(#{replacement})"

        rewritten = URL_REFERENCE.sub(replace_reference, value)
        if unresolved:
            raise ArmadaGeneratedVectorError("SVG contains an unresolved local reference")
        return rewritten

    def rebuild(source_node: ET.Element) -> ET.Element | None:
        element_name = _local_name(source_node.tag)
        if element_name not in ALLOWED_ELEMENTS:
            return None
        target = ET.Element(f"{{{SVG_NAMESPACE}}}{element_name}")
        for raw_name, value in source_node.attrib.items():
            name = _local_name(raw_name)
            if name not in ALLOWED_ATTRIBUTES:
                continue
            if name == "id":
                replacement = id_map.get(value)
                if replacement is not None:
                    target.set("id", replacement)
                continue
            if name == "style":
                style = _safe_style(value)
                if style is not None:
                    target.set("style", style)
                continue
            target.set(name, rewrite_value(value))
        if element_name in TEXT_ELEMENTS:
            target.text = source_node.text
        for source_child in source_node:
            child = rebuild(source_child)
            if child is None:
                continue
            if element_name in TEXT_ELEMENTS:
                child.tail = source_child.tail
            target.append(child)
        return target

    try:
        sanitized = rebuild(root)
    except RecursionError as error:
        raise ArmadaGeneratedVectorError("SVG depth budget exceeded") from error
    if sanitized is None:
        raise ArmadaGeneratedVectorError("SVG root was rejected")
    if "viewBox" not in sanitized.attrib and not {
        "width",
        "height",
    }.issubset(sanitized.attrib):
        raise ArmadaGeneratedVectorError("SVG requires a coordinate system")
    sanitized.set("role", sanitized.attrib.get("role", "img"))
    ET.register_namespace("", SVG_NAMESPACE)
    output = ET.tostring(sanitized, encoding="utf-8", short_empty_elements=True)
    if len(output) > ARMADA_SVG_SOURCE_LIMIT:
        raise ArmadaGeneratedVectorError("sanitized SVG exceeds its output budget")
    return output
