// Sanitized inline SVG for agent-authored drawings.
//
// SVG is declarative markup, so unlike pear-artifact iframes it can be
// sanitized instead of sandboxed: we parse the source as XML and
// rebuild the tree, copying only allowlisted elements and attributes
// onto nodes we create ourselves with createElementNS. Nothing from
// the parsed document is adopted, which forecloses namespace and
// prefix tricks wholesale. Inline rendering is the point — the
// drawing inherits the pear design tokens (light/dark for free), the
// pixel faces, and the .psvg motion classes.
//
// Anything actively hostile (script, foreignObject, event handlers,
// javascript:/data: values, external url() or href) rejects the whole
// document; the fenced source then stays visible as a code block —
// an honest failure the author can read and fix. Merely unknown
// elements and attributes are dropped silently so newer authors
// degrade gracefully.

const SVG_NS = "http://www.w3.org/2000/svg";

export const PEAR_SVG_SOURCE_LIMIT = 65536;
const PEAR_SVG_NODE_LIMIT = 4000;

const ALLOWED_ELEMENTS = new Set([
    // structure
    "svg",
    "g",
    "defs",
    "symbol",
    "use",
    "title",
    "desc",
    // shapes
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    // text
    "text",
    "tspan",
    "textPath",
    // paint servers and geometry helpers
    "linearGradient",
    "radialGradient",
    "stop",
    "pattern",
    "marker",
    "clipPath",
    "mask",
    // SMIL animation (script-free, author-timed)
    "animate",
    "animateTransform",
    "animateMotion",
    "mpath",
    "set",
]);

// Elements whose presence anywhere in the source — even inside a
// subtree we would drop — marks the document as hostile.
const HOSTILE_ELEMENTS = new Set([
    "script",
    "foreignobject",
    "iframe",
    "embed",
    "object",
    "handler",
    "annotation-xml",
]);

// Elements that keep their character data when rebuilt.
const TEXT_BEARING_ELEMENTS = new Set(["text", "tspan", "textPath", "title", "desc"]);

const ALLOWED_ATTRIBUTES = new Set([
    // identity and layout
    "id",
    "class",
    "transform",
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
    // references (fragment-only; validated separately)
    "href",
    // paint
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
    "pointer-events",
    // text
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "letter-spacing",
    "text-anchor",
    "dominant-baseline",
    "alignment-baseline",
    "baseline-shift",
    "textLength",
    "lengthAdjust",
    "startOffset",
    // paint server / marker / clip plumbing
    "gradientUnits",
    "gradientTransform",
    "spreadMethod",
    "patternUnits",
    "patternContentUnits",
    "patternTransform",
    "markerWidth",
    "markerHeight",
    "markerUnits",
    "refX",
    "refY",
    "orient",
    "clipPathUnits",
    "maskUnits",
    "maskContentUnits",
    "clip-path",
    "clip-rule",
    "mask",
    "marker-start",
    "marker-mid",
    "marker-end",
    // SMIL
    "attributeName",
    "values",
    "keyTimes",
    "keySplines",
    "calcMode",
    "from",
    "to",
    "by",
    "dur",
    "begin",
    "end",
    "repeatCount",
    "repeatDur",
    "additive",
    "accumulate",
    "restart",
    "type",
    "keyPoints",
    // accessibility
    "role",
    "aria-label",
    "aria-hidden",
]);

// Attributes SMIL may target: any allowlisted presentation attribute
// except the reference-bearing ones.
const UNANIMATABLE_ATTRIBUTES = new Set(["href", "id", "class", "begin", "end", "attributeName"]);

const FRAGMENT_REFERENCE = /^#([\w.:-]+)$/;
const URL_FUNCTION = /url\(\s*(['"]?)#([\w.:-]+)\1\s*\)/gi;
// SMIL clock values only — no event-based begin ("click", "id.end")
// in v1, so animations cannot become click handlers.
const SMIL_CLOCK_CLAUSE = /^\s*(indefinite|[+-]?\d+(\.\d+)?(h|min|s|ms)?|(\d+:)+\d+(\.\d+)?)\s*$/;

let instance_counter = 0;

function is_svg_root(element: SVGElement): element is SVGSVGElement {
    return element.localName === "svg";
}

function is_hostile_attribute(name: string, value: string): boolean {
    if (name.toLowerCase().startsWith("on")) {
        return true;
    }
    const compact = value.replaceAll(/\s/g, "").toLowerCase();
    const script_scheme = ["java", "script:"].join("");
    if (compact.includes(script_scheme) || compact.includes("data:")) {
        return true;
    }
    // url() may only reference local fragments; anything else fetches
    // on render.
    return compact.replaceAll(/url\(['"]?#[\w.:-]+['"]?\)/g, "").includes("url(");
}

// Pre-scan the parsed tree: hostile content anywhere — even inside a
// subtree the rebuild would drop — rejects the whole document.
function scan_is_hostile(root: Element): boolean {
    const stack: Element[] = [root];
    while (stack.length > 0) {
        const element = stack.pop()!;
        if (HOSTILE_ELEMENTS.has(element.localName.toLowerCase())) {
            return true;
        }
        for (const attribute of element.attributes) {
            if (is_hostile_attribute(attribute.name, attribute.value)) {
                return true;
            }
        }
        for (const child of element.children) {
            stack.push(child);
        }
    }
    return false;
}

function collect_ids(root: Element): Map<string, string> {
    const prefix = `psvg-i${instance_counter}-`;
    const id_map = new Map<string, string>();
    const stack: Element[] = [root];
    while (stack.length > 0) {
        const element = stack.pop()!;
        const id = element.getAttribute("id");
        if (id !== null && /^[\w.:-]+$/.test(id)) {
            id_map.set(id, `${prefix}${id}`);
        }
        for (const child of element.children) {
            stack.push(child);
        }
    }
    return id_map;
}

// Rewrite url(#id) references through the id map; any url() that is
// not a resolvable local fragment poisons the document.
function rewrite_url_functions(value: string, id_map: Map<string, string>): string | undefined {
    let poisoned = false;
    const rewritten = value.replaceAll(URL_FUNCTION, (_match, _quote, id: string) => {
        const mapped = id_map.get(id);
        if (mapped === undefined) {
            poisoned = true;
            return "url(#unresolved)";
        }
        return `url(#${mapped})`;
    });
    if (poisoned) {
        return undefined;
    }
    // Any url( survivor did not match the local-fragment shape.
    if (/url\(/i.test(rewritten.replaceAll(/url\(#psvg-i\d+-[\w.:-]+\)/g, ""))) {
        return undefined;
    }
    return rewritten;
}

function sanitized_attribute_value(
    name: string,
    value: string,
    id_map: Map<string, string>,
): string | undefined {
    if (name === "id") {
        return id_map.get(value);
    }
    if (name === "href") {
        const match = FRAGMENT_REFERENCE.exec(value);
        const mapped = match === null ? undefined : id_map.get(match[1]!);
        return mapped === undefined ? undefined : `#${mapped}`;
    }
    if (name === "class") {
        // Only the design-system vocabulary: arbitrary app classes
        // could restyle or reposition the drawing into the page.
        const kept = value.split(/\s+/).filter((token) => /^psvg(-[\w-]+)?$/.test(token));
        return kept.length > 0 ? kept.join(" ") : undefined;
    }
    if (name === "attributeName") {
        return ALLOWED_ATTRIBUTES.has(value) && !UNANIMATABLE_ATTRIBUTES.has(value)
            ? value
            : undefined;
    }
    if (name === "begin" || name === "end") {
        return value.split(";").every((clause) => SMIL_CLOCK_CLAUSE.test(clause))
            ? value
            : undefined;
    }
    return rewrite_url_functions(value, id_map);
}

function rebuild_element(
    source: Element,
    target_document: Document,
    id_map: Map<string, string>,
    budget: {nodes: number},
): SVGElement | undefined {
    if (!ALLOWED_ELEMENTS.has(source.localName)) {
        return undefined;
    }
    budget.nodes += 1;
    if (budget.nodes > PEAR_SVG_NODE_LIMIT) {
        throw new Error("pear-svg node budget exceeded");
    }
    const rebuilt = target_document.createElementNS(SVG_NS, source.localName);
    for (const attribute of source.attributes) {
        // Accept both SVG2 href and legacy xlink:href, emitting plain
        // href; every other namespaced attribute is dropped.
        const name =
            attribute.prefix === "xlink" && attribute.localName === "href"
                ? "href"
                : attribute.prefix === null
                  ? attribute.name
                  : undefined;
        if (name === undefined || !ALLOWED_ATTRIBUTES.has(name)) {
            continue;
        }
        const value = sanitized_attribute_value(name, attribute.value, id_map);
        if (value === undefined) {
            if (name === "href") {
                // A dangling reference renders as a broken drawing;
                // drop the whole element instead.
                return undefined;
            }
            continue;
        }
        rebuilt.setAttribute(name, value);
    }
    if (TEXT_BEARING_ELEMENTS.has(source.localName)) {
        for (const child of source.childNodes) {
            if (child.nodeType === 3) {
                rebuilt.append(target_document.createTextNode(child.nodeValue ?? ""));
            } else if (child instanceof Element) {
                const rebuilt_child = rebuild_element(child, target_document, id_map, budget);
                if (rebuilt_child !== undefined) {
                    rebuilt.append(rebuilt_child);
                }
            }
        }
    } else {
        for (const child of source.children) {
            const rebuilt_child = rebuild_element(child, target_document, id_map, budget);
            if (rebuilt_child !== undefined) {
                rebuilt.append(rebuilt_child);
            }
        }
    }
    return rebuilt;
}

// Returns a detached, freshly built <svg> element, or undefined when
// the source is malformed or hostile (caller leaves the fence
// visible).
export function sanitize_pear_svg(source: string): SVGSVGElement | undefined {
    if (typeof DOMParser !== "function" || typeof document === "undefined") {
        return undefined;
    }
    if (source.length > PEAR_SVG_SOURCE_LIMIT) {
        return undefined;
    }
    // DOCTYPE machinery (entity expansion) has no legitimate use in
    // authored SVG.
    if (/<!(?:DOCTYPE|ENTITY)/i.test(source)) {
        return undefined;
    }
    let parsed: Document;
    try {
        parsed = new DOMParser().parseFromString(source, "image/svg+xml");
    } catch {
        return undefined;
    }
    const root = parsed.documentElement;
    if (root?.localName !== "svg" || parsed.querySelector("parsererror") !== null) {
        return undefined;
    }
    if (scan_is_hostile(root)) {
        return undefined;
    }
    // The drawing must declare its own coordinate system.
    if (
        !root.hasAttribute("viewBox") &&
        !(root.hasAttribute("width") && root.hasAttribute("height"))
    ) {
        return undefined;
    }
    instance_counter += 1;
    const id_map = collect_ids(root);
    let rebuilt: SVGElement | undefined;
    try {
        rebuilt = rebuild_element(root, document, id_map, {nodes: 0});
    } catch {
        return undefined;
    }
    if (rebuilt === undefined) {
        return undefined;
    }
    if (!is_svg_root(rebuilt)) {
        return undefined;
    }
    const svg = rebuilt;
    svg.setAttribute("class", ["psvg", svg.getAttribute("class") ?? ""].join(" ").trim());
    svg.setAttribute("role", "img");
    // Drawings are documents, not controls; nothing inside is
    // focusable or clickable.
    svg.setAttribute("focusable", "false");
    return svg;
}
