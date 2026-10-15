import katex from "katex";

import {ARMADA_ARTIFACT_SCHEMA} from "./armada_artifact_contract_generated.ts";
import {replace_armada_artifact_host} from "./legacy_artifact_lifecycle.ts";

export const ARMADA_ARTIFACT_SUBMESSAGE_TYPE = "artifact";

type JsonSchema = Record<string, unknown>;
type JsonObject = Record<string, unknown>;

function is_json_object(value: unknown): value is JsonObject {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export type ArmadaArtifactAction = {label: string; prompt: string};
export type ArmadaArtifactBlock = JsonObject & {type: string};
export type ArmadaArtifactDocument = {
    version: 1;
    title?: string;
    data?: JsonObject;
    blocks: ArmadaArtifactBlock[];
    actions?: ArmadaArtifactAction[];
};

export type ArmadaArtifactRenderOptions = {
    density: "chat" | "wall";
    onAction?: (action: ArmadaArtifactAction, button: HTMLButtonElement) => void;
    onGeometryChange?: (
        artifact: HTMLElement,
        previous_height: number,
        next_height: number,
    ) => void;
    onPin?: (button: HTMLButtonElement) => void;
};

export type ArmadaArtifactModule = {
    parse_armada_artifact: typeof parse_armada_artifact;
    mount_armada_artifact: typeof mount_armada_artifact;
};

function schema_reference(root: JsonSchema, reference: string): JsonSchema | undefined {
    if (!reference.startsWith("#/")) {
        return undefined;
    }
    let current: unknown = root;
    for (const encoded of reference.slice(2).split("/")) {
        const part = encoded.replaceAll("~1", "/").replaceAll("~0", "~");
        if (!is_json_object(current) || !(part in current)) {
            return undefined;
        }
        current = current[part];
    }
    return is_json_object(current) ? current : undefined;
}

function has_type(value: unknown, expected: string): boolean {
    switch (expected) {
        case "null":
            return value === null;
        case "array":
            return Array.isArray(value);
        case "object":
            return typeof value === "object" && value !== null && !Array.isArray(value);
        case "integer":
            return typeof value === "number" && Number.isInteger(value);
        case "number":
            return typeof value === "number" && Number.isFinite(value);
        case "string":
            return typeof value === "string";
        case "boolean":
            return typeof value === "boolean";
        default:
            return false;
    }
}

const compiled_schema_patterns = new WeakMap<JsonSchema, RegExp>();

function schema_pattern(schema: JsonSchema, pattern: string): RegExp {
    const cached = compiled_schema_patterns.get(schema);
    if (cached !== undefined) {
        return cached;
    }
    const compiled = new RegExp(pattern, "u");
    compiled_schema_patterns.set(schema, compiled);
    return compiled;
}

function validates(schema: JsonSchema, value: unknown, root: JsonSchema): boolean {
    const reference = schema["$ref"];
    if (typeof reference === "string") {
        const target = schema_reference(root, reference);
        return target !== undefined && validates(target, value, root);
    }
    const variants = schema["oneOf"];
    if (Array.isArray(variants)) {
        return (
            variants.filter((variant) => is_json_object(variant) && validates(variant, value, root))
                .length === 1
        );
    }
    const inclusive_variants = schema["anyOf"];
    if (Array.isArray(inclusive_variants)) {
        return inclusive_variants.some(
            (variant) => is_json_object(variant) && validates(variant, value, root),
        );
    }
    if ("const" in schema && value !== schema["const"]) {
        return false;
    }
    const choices = schema["enum"];
    if (Array.isArray(choices) && !choices.includes(value)) {
        return false;
    }
    const raw_type = schema["type"];
    const types = Array.isArray(raw_type) ? raw_type : raw_type === undefined ? [] : [raw_type];
    if (
        types.length > 0 &&
        !types.some((type) => typeof type === "string" && has_type(value, type))
    ) {
        return false;
    }
    if (typeof value === "string") {
        const min = schema["minLength"];
        const max = schema["maxLength"];
        const pattern = schema["pattern"];
        if (
            (typeof min === "number" && value.length < min) ||
            (typeof max === "number" && value.length > max)
        ) {
            return false;
        }
        if (typeof pattern === "string" && !schema_pattern(schema, pattern).test(value)) {
            return false;
        }
    }
    if (typeof value === "number") {
        const minimum = schema["minimum"];
        const maximum = schema["maximum"];
        const exclusive_minimum = schema["exclusiveMinimum"];
        if (
            (typeof minimum === "number" && value < minimum) ||
            (typeof maximum === "number" && value > maximum) ||
            (typeof exclusive_minimum === "number" && value <= exclusive_minimum)
        ) {
            return false;
        }
    }
    if (Array.isArray(value)) {
        const min = schema["minItems"];
        const max = schema["maxItems"];
        if (
            (typeof min === "number" && value.length < min) ||
            (typeof max === "number" && value.length > max)
        ) {
            return false;
        }
        const prefix = schema["prefixItems"];
        if (
            Array.isArray(prefix) &&
            prefix.some(
                (item_schema, index) =>
                    !is_json_object(item_schema) || !validates(item_schema, value[index], root),
            )
        ) {
            return false;
        }
        const items = schema["items"];
        const start = Array.isArray(prefix) ? prefix.length : 0;
        if (items === false && value.length > start) {
            return false;
        }
        if (
            is_json_object(items) &&
            value.slice(start).some((item) => !validates(items, item, root))
        ) {
            return false;
        }
    }
    if (is_json_object(value)) {
        const object = value;
        const keys = Object.keys(object);
        const min = schema["minProperties"];
        const max = schema["maxProperties"];
        if (
            (typeof min === "number" && keys.length < min) ||
            (typeof max === "number" && keys.length > max)
        ) {
            return false;
        }
        const required = schema["required"];
        if (
            Array.isArray(required) &&
            required.some((key) => typeof key === "string" && !(key in object))
        ) {
            return false;
        }
        const properties = schema["properties"];
        const property_schemas = is_json_object(properties) ? properties : {};
        for (const [key, child] of Object.entries(object)) {
            const child_schema = property_schemas[key];
            if (is_json_object(child_schema)) {
                if (!validates(child_schema, child, root)) {
                    return false;
                }
                continue;
            }
            const additional = schema["additionalProperties"];
            if (additional === false) {
                return false;
            }
            if (is_json_object(additional) && !validates(additional, child, root)) {
                return false;
            }
        }
    }
    return true;
}

function semantic_scene_is_valid(root: JsonObject): boolean {
    const seen = new Set<string>();
    const connectors: [string, string][] = [];
    let count = 0;
    let text_length = 0;
    const definitions = ARMADA_ARTIFACT_SCHEMA["$defs"];
    if (!is_json_object(definitions)) {
        return false;
    }
    const node_definition = definitions["node"];
    if (!is_json_object(node_definition)) {
        return false;
    }
    const allowed_properties_by_type = node_definition["x-armada-type-properties"];
    if (!is_json_object(allowed_properties_by_type)) {
        return false;
    }
    const properties_by_type: JsonObject = allowed_properties_by_type;
    function expanded_node_count(node: JsonObject): number {
        const children = node["children"];
        let subtree = 1;
        if (Array.isArray(children)) {
            for (const child of children) {
                if (is_json_object(child)) {
                    subtree += expanded_node_count(child);
                }
            }
        }
        const repeat = node["repeat"];
        const multiplier = is_json_object(repeat) ? Number(repeat["limit"] ?? 16) : 1;
        return subtree * multiplier;
    }
    function visit(node: JsonObject, depth: number): boolean {
        count += 1;
        if (count > 128 || depth > 8) {
            return false;
        }
        const id = node["id"];
        if ("repeat" in node) {
            const repeated_nodes: JsonObject[] = [];
            function collect_repeated(current: JsonObject): void {
                repeated_nodes.push(current);
                const repeated_children = current["children"];
                if (Array.isArray(repeated_children)) {
                    for (const repeated_child of repeated_children) {
                        if (is_json_object(repeated_child)) {
                            collect_repeated(repeated_child);
                        }
                    }
                }
            }
            collect_repeated(node);
            if (
                repeated_nodes.some(
                    (repeated) =>
                        repeated["type"] === "connector" || typeof repeated["id"] === "string",
                )
            ) {
                return false;
            }
        }
        if (typeof id === "string") {
            if (seen.has(id)) {
                return false;
            }
            seen.add(id);
        }
        for (const key of ["text", "label", "ariaLabel"]) {
            if (typeof node[key] === "string") {
                text_length += node[key].length;
            }
        }
        const type = node["type"];
        const allowed_properties = properties_by_type[String(type)];
        if (
            !Array.isArray(allowed_properties) ||
            Object.keys(node).some((key) => !allowed_properties.includes(key))
        ) {
            return false;
        }
        const children = node["children"];
        if (
            type === "group" &&
            (!Array.isArray(children) ||
                children.length === 0 ||
                typeof node["layout"] !== "string")
        ) {
            return false;
        }
        if (
            type === "text" &&
            typeof node["text"] !== "string" &&
            typeof node["bind"] !== "string" &&
            !is_json_object(node["derive"])
        ) {
            return false;
        }
        if (type === "shape" && typeof node["shape"] !== "string") {
            return false;
        }
        if (type === "connector") {
            if (typeof node["from"] !== "string" || typeof node["to"] !== "string") {
                return false;
            }
            connectors.push([node["from"], node["to"]]);
        }
        if (type === "path" && (!Array.isArray(node["points"]) || node["points"].length < 2)) {
            return false;
        }
        if (type === "symbol" && typeof node["symbol"] !== "string") {
            return false;
        }
        if (
            type === "mark" &&
            (typeof node["mark"] !== "string" ||
                (!Array.isArray(node["values"]) && typeof node["bind"] !== "string"))
        ) {
            return false;
        }
        if (
            type === "control" &&
            (typeof node["control"] !== "string" || typeof node["label"] !== "string")
        ) {
            return false;
        }
        if (
            type === "control" &&
            (node["control"] === "range" || node["control"] === "stepper") &&
            typeof node["min"] === "number" &&
            typeof node["max"] === "number" &&
            node["min"] > node["max"]
        ) {
            return false;
        }
        return (
            !Array.isArray(children) ||
            children.every((child) => is_json_object(child) && visit(child, depth + 1))
        );
    }
    return (
        expanded_node_count(root) <= 128 &&
        visit(root, 1) &&
        text_length <= 12000 &&
        connectors.every(([from, to]) => seen.has(from) && seen.has(to))
    );
}

function semantic_scene_steps_are_valid(block: JsonObject): boolean {
    const raw_steps = block["steps"];
    if (raw_steps === undefined) {
        return true;
    }
    const root = block["root"];
    if (!Array.isArray(raw_steps) || !is_json_object(root)) {
        return false;
    }
    const target_types = new Map<string, string>();
    function collect(node: JsonObject): void {
        if (typeof node["id"] === "string" && typeof node["type"] === "string") {
            target_types.set(node["id"], node["type"]);
        }
        const children = node["children"];
        if (Array.isArray(children)) {
            for (const child of children) {
                if (is_json_object(child)) {
                    collect(child);
                }
            }
        }
    }
    collect(root);
    let total_text = 0;
    function count_root_text(node: JsonObject): void {
        for (const key of ["text", "label", "ariaLabel"]) {
            if (typeof node[key] === "string") {
                total_text += node[key].length;
            }
        }
        const children = node["children"];
        if (Array.isArray(children)) {
            for (const child of children) {
                if (is_json_object(child)) {
                    count_root_text(child);
                }
            }
        }
    }
    count_root_text(root);
    for (const raw_step of raw_steps) {
        if (!is_json_object(raw_step) || !Array.isArray(raw_step["changes"])) {
            return false;
        }
        if (typeof raw_step["label"] === "string") {
            total_text += raw_step["label"].length;
        }
        const seen_targets = new Set<string>();
        for (const raw_change of raw_step["changes"]) {
            if (!is_json_object(raw_change) || typeof raw_change["target"] !== "string") {
                return false;
            }
            const target = raw_change["target"];
            const target_type = target_types.get(target);
            if (target_type === undefined || seen_targets.has(target)) {
                return false;
            }
            seen_targets.add(target);
            if (
                ("state" in raw_change && target_type !== "shape" && target_type !== "connector") ||
                ("label" in raw_change && target_type !== "shape") ||
                ("text" in raw_change && target_type !== "text")
            ) {
                return false;
            }
            for (const key of ["label", "text"]) {
                if (typeof raw_change[key] === "string") {
                    total_text += raw_change[key].length;
                }
            }
        }
    }
    return total_text <= 12000;
}

function semantic_media_is_valid(block: JsonObject): boolean {
    const source_fields = ["source", "sourceDark", "posterSource", "posterSourceDark"];
    if (source_fields.some((field) => field in block) || typeof block["src"] !== "string") {
        return false;
    }
    const kind = block["kind"];
    const extension = block["src"].split(".").at(-1)?.toLowerCase();
    if (
        (kind === "image" && extension !== "png" && extension !== "svg" && extension !== "webp") ||
        (kind === "video" && extension !== "webm")
    ) {
        return false;
    }
    if (kind === "image" && ("poster" in block || "posterDark" in block)) {
        return false;
    }
    if ("posterDark" in block && !("poster" in block)) {
        return false;
    }
    return [block["poster"], block["posterDark"]].every((value) => {
        if (value === undefined) {
            return true;
        }
        const poster_extension = typeof value === "string" ? value.split(".").at(-1) : undefined;
        return poster_extension === "png" || poster_extension === "webp";
    });
}

export function parse_armada_artifact(raw: string): ArmadaArtifactDocument | undefined {
    if (new TextEncoder().encode(raw).length > 64000) {
        return undefined;
    }
    let value: unknown;
    try {
        value = JSON.parse(raw);
    } catch {
        return undefined;
    }
    if (!validates(ARMADA_ARTIFACT_SCHEMA, value, ARMADA_ARTIFACT_SCHEMA)) {
        return undefined;
    }
    if (!is_json_object(value) || value["version"] !== 1 || !Array.isArray(value["blocks"])) {
        return undefined;
    }
    const blocks: ArmadaArtifactBlock[] = [];
    for (const block of value["blocks"]) {
        if (!is_json_object(block) || typeof block["type"] !== "string") {
            return undefined;
        }
        blocks.push({...block, type: block["type"]});
    }
    const document: ArmadaArtifactDocument = {version: 1, blocks};
    if (typeof value["title"] === "string") {
        document.title = value["title"];
    }
    if (is_json_object(value["data"])) {
        document.data = value["data"];
    }
    if (Array.isArray(value["actions"])) {
        document.actions = value["actions"].filter(
            (action): action is ArmadaArtifactAction =>
                is_json_object(action) &&
                typeof action["label"] === "string" &&
                typeof action["prompt"] === "string",
        );
    }
    if (
        document.blocks.some(
            (block) =>
                block.type === "scene" &&
                (!is_json_object(block["root"]) ||
                    !semantic_scene_is_valid(block["root"]) ||
                    !semantic_scene_steps_are_valid(block)),
        )
    ) {
        return undefined;
    }
    const media_blocks = document.blocks.filter((block) => block.type === "media");
    if (media_blocks.length > 2 || media_blocks.some((block) => !semantic_media_is_valid(block))) {
        return undefined;
    }
    return document;
}

function element(tag: "button", class_name?: string, text?: string): HTMLButtonElement;
function element(tag: string, class_name?: string, text?: string): HTMLElement;
function element(tag: string, class_name?: string, text?: string): HTMLElement {
    const node = document.createElement(tag);
    if (class_name !== undefined) {
        node.className = class_name;
    }
    if (text !== undefined) {
        node.textContent = text;
    }
    return node;
}

function scalar_text(value: unknown): string {
    return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : "";
}

const artifact_math_pattern = /```math[\t ]*\n([\s\S]*?)```|\$\$([\s\S]*?)\$\$/gu;

function set_artifact_text(host: HTMLElement, source: string): void {
    host.replaceChildren();
    host.dataset["legacyArtifactSource"] = source;
    let offset = 0;
    for (const match of source.matchAll(artifact_math_pattern)) {
        const index = match.index;
        if (index > offset) {
            host.append(document.createTextNode(source.slice(offset, index)));
        }
        const display_mode = match[1] !== undefined;
        const tex = (match[1] ?? match[2] ?? "").trim();
        const math = element(
            "span",
            display_mode
                ? "legacy-artifact-math legacy-artifact-math-display"
                : "legacy-artifact-math",
        );
        try {
            katex.render(tex, math, {
                displayMode: display_mode,
                maxExpand: 1000,
                maxSize: 10,
                strict: "ignore",
                throwOnError: true,
                trust: false,
            });
        } catch {
            math.classList.add("is-invalid");
            math.textContent = match[0];
        }
        host.append(math);
        offset = index + match[0].length;
    }
    if (offset < source.length) {
        host.append(document.createTextNode(source.slice(offset)));
    }
}

function artifact_accessible_text(source: string): string {
    return source.replaceAll(
        artifact_math_pattern,
        (_match, display: string | undefined, inline: string | undefined) =>
            (display ?? inline ?? "").trim(),
    );
}

function dark_theme_is_active(): boolean {
    const root = document.documentElement;
    return (
        root.classList.contains("dark-theme") ||
        (root.classList.contains("color-scheme-automatic") &&
            window.matchMedia?.("(prefers-color-scheme: dark)").matches)
    );
}

function watch_theme(controller: AbortController, callback: () => void): void {
    const color_query = window.matchMedia?.("(prefers-color-scheme: dark)");
    color_query?.addEventListener("change", callback, {signal: controller.signal});
    if (typeof MutationObserver === "undefined") {
        return;
    }
    const observer = new MutationObserver(callback);
    observer.observe(document.documentElement, {attributes: true, attributeFilter: ["class"]});
    controller.signal.addEventListener(
        "abort",
        () => {
            observer.disconnect();
        },
        {once: true},
    );
}

function themed_media_value(block: ArmadaArtifactBlock, field: string): string {
    const dark_value = block[`${field}Dark`];
    if (dark_theme_is_active() && typeof dark_value === "string") {
        return dark_value;
    }
    return scalar_text(block[field]);
}

function data_value(data: JsonObject, path: unknown): unknown {
    if (typeof path !== "string" || !path.startsWith("data.")) {
        return undefined;
    }
    let current: unknown = data;
    for (const part of path.slice(5).split(".")) {
        if (!is_json_object(current) || !(part in current)) {
            return undefined;
        }
        current = current[part];
    }
    return current;
}

function derive_value(derive: unknown, data: JsonObject): unknown {
    if (!is_json_object(derive)) {
        return undefined;
    }
    const op = derive["op"];
    const raw_args = derive["args"];
    if (typeof op !== "string" || !Array.isArray(raw_args)) {
        return undefined;
    }
    const args: unknown[] = [];
    for (const raw_arg of raw_args) {
        const arg: unknown = raw_arg;
        args.push(typeof arg === "string" && arg.startsWith("data.") ? data_value(data, arg) : arg);
    }
    const numbers = args.map(Number);
    const numeric_ops = new Set([
        "add",
        "subtract",
        "multiply",
        "divide",
        "min",
        "max",
        "clamp",
        "greater",
        "less",
    ]);
    if (numeric_ops.has(op) && numbers.some((value) => !Number.isFinite(value))) {
        return undefined;
    }
    switch (op) {
        case "add": {
            let result = 0;
            for (const value of numbers) {
                result += value;
            }
            return result;
        }
        case "subtract": {
            let result = numbers[0] ?? 0;
            for (const value of numbers.slice(1)) {
                result -= value;
            }
            return result;
        }
        case "multiply": {
            let result = 1;
            for (const value of numbers) {
                result *= value;
            }
            return result;
        }
        case "divide": {
            let result = numbers[0] ?? 0;
            for (const value of numbers.slice(1)) {
                if (value === 0) {
                    return undefined;
                }
                result /= value;
            }
            return result;
        }
        case "min":
            return Math.min(...numbers);
        case "max":
            return Math.max(...numbers);
        case "clamp":
            return Math.min(
                numbers[2] ?? Infinity,
                Math.max(numbers[1] ?? -Infinity, numbers[0] ?? 0),
            );
        case "equal":
            return args[0] === args[1];
        case "greater":
            return (numbers[0] ?? 0) > (numbers[1] ?? 0);
        case "less":
            return (numbers[0] ?? 0) < (numbers[1] ?? 0);
        case "and":
            return args.every(Boolean);
        case "or":
            return args.some(Boolean);
        case "not":
            return !args[0];
        default:
            return undefined;
    }
}

type Connector = {
    id?: string;
    from: string;
    to: string;
    directed: boolean;
    label?: string;
    state?: string;
    current_state?: string;
};

function graph_distance_ranks(
    node_ids: Iterable<string>,
    connectors: Connector[],
): Map<string, number> {
    const neighbours = new Map<string, string[]>();
    for (const id of node_ids) {
        neighbours.set(id, []);
    }
    for (const connector of connectors) {
        neighbours.get(connector.from)?.push(connector.to);
        neighbours.get(connector.to)?.push(connector.from);
    }
    const ranks = new Map<string, number>();
    const remaining = new Set(neighbours.keys());
    let component_offset = 0;
    while (remaining.size > 0) {
        const root = remaining.values().next().value;
        if (root === undefined) {
            break;
        }
        const queue = [root];
        ranks.set(root, component_offset);
        remaining.delete(root);
        let component_max = component_offset;
        let queue_index = 0;
        while (queue_index < queue.length) {
            const id = queue[queue_index];
            queue_index += 1;
            if (id === undefined) {
                continue;
            }
            const rank = ranks.get(id) ?? component_offset;
            component_max = Math.max(component_max, rank);
            for (const target of neighbours.get(id) ?? []) {
                if (!remaining.has(target)) {
                    continue;
                }
                ranks.set(target, rank + 1);
                remaining.delete(target);
                queue.push(target);
            }
        }
        component_offset = component_max + 2;
    }
    return ranks;
}

function layout_graph_group(host: HTMLElement, connectors: Connector[]): void {
    const nodes = [...host.querySelectorAll<HTMLElement>(":scope > [data-legacy-artifact-node]")];
    const node_by_id = new Map(
        nodes.flatMap((node) => {
            const id = node.dataset["legacyArtifactNode"];
            return id === undefined ? [] : [[id, node] as const];
        }),
    );
    if (node_by_id.size === 0) {
        return;
    }
    const local_connectors = connectors.filter(
        (connector) => node_by_id.has(connector.from) && node_by_id.has(connector.to),
    );
    const stack_dense_chain = graph_should_stack_dense_chain(node_by_id, local_connectors);
    host.dataset["graphDirection"] = stack_dense_chain ? "vertical" : "horizontal";
    if (local_connectors.some((connector) => !connector.directed)) {
        position_graph_nodes(
            host,
            node_by_id,
            graph_distance_ranks(node_by_id.keys(), local_connectors),
            local_connectors,
            stack_dense_chain,
        );
        return;
    }
    const incoming = new Map([...node_by_id.keys()].map((id) => [id, 0]));
    const outgoing = new Map<string, string[]>();
    for (const id of node_by_id.keys()) {
        outgoing.set(id, []);
    }
    for (const connector of local_connectors) {
        incoming.set(connector.to, (incoming.get(connector.to) ?? 0) + 1);
        outgoing.get(connector.from)?.push(connector.to);
    }
    const ranks = new Map<string, number>();
    const queue = [...node_by_id.keys()].filter((id) => incoming.get(id) === 0);
    for (const id of queue) {
        ranks.set(id, 0);
    }
    let queue_index = 0;
    while (queue_index < queue.length) {
        const id = queue[queue_index];
        queue_index += 1;
        if (id === undefined) {
            continue;
        }
        const rank = ranks.get(id) ?? 0;
        for (const target of outgoing.get(id) ?? []) {
            ranks.set(target, Math.max(ranks.get(target) ?? 0, rank + 1));
            const remaining = (incoming.get(target) ?? 1) - 1;
            incoming.set(target, remaining);
            if (remaining === 0) {
                queue.push(target);
            }
        }
    }
    if (ranks.size !== node_by_id.size) {
        position_graph_nodes(
            host,
            node_by_id,
            graph_distance_ranks(node_by_id.keys(), local_connectors),
            local_connectors,
            stack_dense_chain,
        );
        return;
    }
    position_graph_nodes(host, node_by_id, ranks, local_connectors, stack_dense_chain);
}

function graph_should_stack_dense_chain(
    node_by_id: Map<string, HTMLElement>,
    connectors: Connector[],
): boolean {
    if (node_by_id.size < 4 || connectors.length !== node_by_id.size - 1) {
        return false;
    }
    const incoming = new Map([...node_by_id.keys()].map((id) => [id, 0]));
    const outgoing = new Map([...node_by_id.keys()].map((id) => [id, 0]));
    for (const connector of connectors) {
        if (!connector.directed) {
            return false;
        }
        incoming.set(connector.to, (incoming.get(connector.to) ?? 0) + 1);
        outgoing.set(connector.from, (outgoing.get(connector.from) ?? 0) + 1);
    }
    if (
        [...incoming.values()].some((count) => count > 1) ||
        [...outgoing.values()].some((count) => count > 1) ||
        [...incoming.values()].filter((count) => count === 0).length !== 1 ||
        [...outgoing.values()].filter((count) => count === 0).length !== 1
    ) {
        return false;
    }
    let source_length = 0;
    for (const node of node_by_id.values()) {
        const label = node.querySelector<HTMLElement>(".legacy-artifact-shape-label");
        source_length += (label?.dataset["legacyArtifactSource"] ?? label?.textContent ?? "")
            .length;
    }
    return source_length > Math.max(72, node_by_id.size * 16);
}

function position_graph_nodes(
    host: HTMLElement,
    node_by_id: Map<string, HTMLElement>,
    ranks: Map<string, number>,
    connectors: Connector[],
    stack_dense_chain = false,
): void {
    const columns = Math.max(...ranks.values()) + 1;
    const nodes_by_rank = Array.from(
        {length: columns},
        (): {id: string; node: HTMLElement}[] => [],
    );
    for (const [id, node] of node_by_id) {
        nodes_by_rank[ranks.get(id) ?? 0]!.push({id, node});
    }
    for (let column = 1; column < nodes_by_rank.length; column += 1) {
        const previous = new Map(
            nodes_by_rank[column - 1]!.map(({id}, index) => [id, index] as const),
        );
        const crossing_score = (id: string): number => {
            const positions = connectors.flatMap((connector) => {
                if (connector.from === id && previous.has(connector.to)) {
                    return [previous.get(connector.to)!];
                }
                if (connector.to === id && previous.has(connector.from)) {
                    return [previous.get(connector.from)!];
                }
                return [];
            });
            return positions.length === 0
                ? Number.POSITIVE_INFINITY
                : positions.reduce((sum, position) => sum + position, 0) / positions.length;
        };
        nodes_by_rank[column]!.sort((left, right) => {
            const delta = crossing_score(left.id) - crossing_score(right.id);
            return Number.isNaN(delta) ? 0 : delta;
        });
    }
    if (stack_dense_chain) {
        host.style.setProperty("--legacy-artifact-graph-columns", "1");
        host.style.setProperty("--legacy-artifact-graph-rows", String(columns));
        for (const [row, rank_nodes] of nodes_by_rank.entries()) {
            for (const {node} of rank_nodes) {
                node.style.setProperty("--legacy-artifact-grid-column", "1");
                node.style.setProperty("--legacy-artifact-grid-row", String(row + 1));
                node.style.setProperty("--legacy-artifact-order", String(row));
            }
        }
        return;
    }
    const rows = Math.max(1, ...nodes_by_rank.map((rank_nodes) => rank_nodes.length));
    host.style.setProperty("--legacy-artifact-graph-columns", String(columns));
    host.style.setProperty("--legacy-artifact-graph-rows", String(rows));
    for (const [column, rank_nodes] of nodes_by_rank.entries()) {
        for (const [index, {node}] of rank_nodes.entries()) {
            node.style.setProperty("--legacy-artifact-grid-column", String(column + 1));
            node.style.setProperty(
                "--legacy-artifact-grid-row",
                rank_nodes.length === 1
                    ? `1 / span ${rows}`
                    : String(Math.round((index * (rows - 1)) / (rank_nodes.length - 1)) + 1),
            );
            node.style.setProperty("--legacy-artifact-order", String(column * rows + index));
        }
    }
}

let connector_instance = 0;
let scene_step_instance = 0;
let raw_document_instance = 0;
const artifact_lifecycles = new WeakMap<HTMLElement, AbortController>();

function render_mark(node: JsonObject, data: JsonObject): HTMLElement {
    const host = element(
        "div",
        `legacy-artifact-mark legacy-artifact-mark-${scalar_text(node["mark"])}`,
    );
    const bound = data_value(data, node["bind"]);
    const values = Array.isArray(bound)
        ? bound
        : Array.isArray(node["values"])
          ? node["values"]
          : [];
    const numbers = values
        .map(Number)
        .filter((value) => Number.isFinite(value))
        .slice(0, 256);
    const low = Math.min(...numbers, 0);
    const high = Math.max(...numbers, 1);
    const range = Math.max(high - low, Number.EPSILON);
    if (node["mark"] === "bar") {
        for (const value of numbers) {
            const bar = element("span", "legacy-artifact-bar");
            bar.style.setProperty(
                "--legacy-artifact-value",
                String(Math.max(0, (value - low) / range)),
            );
            host.append(bar);
        }
        return host;
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 40");
    svg.setAttribute("aria-hidden", "true");
    const points = numbers.map((value, index) => ({
        x: numbers.length === 1 ? 50 : (index / (numbers.length - 1)) * 100,
        y: 38 - ((value - low) / range) * 34,
    }));
    if (node["mark"] === "point") {
        for (const point of points) {
            const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            circle.setAttribute("cx", String(point.x));
            circle.setAttribute("cy", String(point.y));
            circle.setAttribute("r", "2.5");
            circle.setAttribute("class", "legacy-artifact-series-point");
            svg.append(circle);
        }
    } else if (node["mark"] === "arc") {
        const positive = numbers.map((value) => Math.max(0, value));
        const total = positive.reduce((sum, value) => sum + value, 0);
        let offset = 0;
        if (total > 0) {
            for (const [index, value] of positive.entries()) {
                const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                const fraction = value / total;
                circle.setAttribute("cx", "50");
                circle.setAttribute("cy", "20");
                circle.setAttribute("r", "15");
                circle.setAttribute("pathLength", "1");
                circle.setAttribute("stroke-dasharray", `${fraction} ${1 - fraction}`);
                circle.setAttribute("stroke-dashoffset", String(-offset));
                circle.setAttribute(
                    "class",
                    `legacy-artifact-series-arc legacy-artifact-cat-${(index % 6) + 1}`,
                );
                svg.append(circle);
                offset += fraction;
            }
        }
    } else {
        const polyline = document.createElementNS(
            "http://www.w3.org/2000/svg",
            node["mark"] === "area" ? "polygon" : "polyline",
        );
        const point_text = points.map((point) => `${point.x},${point.y}`).join(" ");
        polyline.setAttribute(
            "points",
            node["mark"] === "area" ? `0,40 ${point_text} 100,40` : point_text,
        );
        polyline.setAttribute(
            "class",
            node["mark"] === "area" ? "legacy-artifact-series-area" : "legacy-artifact-series-line",
        );
        svg.append(polyline);
    }
    host.append(svg);
    return host;
}

function render_scene_node(
    node: JsonObject,
    data: JsonObject,
    connectors: Connector[],
    controller: AbortController,
): HTMLElement | undefined {
    const repeat = node["repeat"];
    if (is_json_object(repeat)) {
        const items = data_value(data, repeat["source"]);
        const limit = typeof repeat["limit"] === "number" ? repeat["limit"] : 16;
        const repeated = element("div", "legacy-artifact-repeat");
        if (!Array.isArray(items)) {
            return repeated;
        }
        const template = {...node};
        delete template["repeat"];
        for (const [index, item] of items.slice(0, limit).entries()) {
            const rendered = render_scene_node(
                template,
                {...data, item, index},
                connectors,
                controller,
            );
            if (rendered !== undefined) {
                rendered.dataset["repeatIndex"] = String(index);
                repeated.append(rendered);
            }
        }
        return repeated;
    }
    const type = node["type"];
    if (type === "connector") {
        const connector: Connector = {
            from: String(node["from"]),
            to: String(node["to"]),
            directed: node["directed"] !== false,
        };
        if (typeof node["id"] === "string") {
            connector.id = node["id"];
        }
        if (typeof node["label"] === "string") {
            connector.label = node["label"];
        }
        if (typeof node["state"] === "string") {
            connector.state = node["state"];
        }
        connectors.push(connector);
        return undefined;
    }
    const host = element("div", `legacy-artifact-node legacy-artifact-${String(type)}`);
    if (typeof node["id"] === "string") {
        host.dataset["legacyArtifactNode"] = node["id"];
        host.dataset["legacyArtifactTarget"] = node["id"];
    }
    if (typeof node["state"] === "string") {
        host.dataset["state"] = node["state"];
    }
    if (typeof node["ariaLabel"] === "string") {
        host.setAttribute("aria-label", node["ariaLabel"]);
    }
    if (typeof node["transition"] === "string") {
        host.dataset["transition"] = node["transition"];
    }
    if (typeof node["interaction"] === "string" && node["interaction"] !== "none") {
        host.dataset["interaction"] = node["interaction"];
        host.tabIndex = 0;
        host.setAttribute("role", "button");
        host.setAttribute("aria-pressed", "false");
        const activate = (): void => {
            host.setAttribute("aria-pressed", String(host.getAttribute("aria-pressed") !== "true"));
        };
        host.addEventListener("click", activate, {signal: controller.signal});
        host.addEventListener(
            "keydown",
            (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    activate();
                }
            },
            {signal: controller.signal},
        );
    }
    switch (type) {
        case "group": {
            host.dataset["layout"] = String(node["layout"]);
            if (typeof node["columns"] === "number") {
                host.style.setProperty("--legacy-artifact-columns", String(node["columns"]));
            }
            if (typeof node["align"] === "string") {
                host.dataset["align"] = node["align"];
            }
            const children = node["children"];
            if (Array.isArray(children)) {
                for (const child of children) {
                    if (is_json_object(child)) {
                        const rendered = render_scene_node(child, data, connectors, controller);
                        if (rendered !== undefined) {
                            host.append(rendered);
                        }
                    }
                }
            }
            break;
        }
        case "text": {
            host.dataset["textRole"] = scalar_text(node["textRole"] ?? "body");
            const value =
                derive_value(node["derive"], data) ??
                data_value(data, node["bind"]) ??
                node["text"];
            set_artifact_text(host, scalar_text(value));

            break;
        }
        case "shape": {
            host.dataset["shape"] = scalar_text(node["shape"]);
            if (typeof node["label"] === "string") {
                const label = element("span", "legacy-artifact-shape-label");
                set_artifact_text(label, node["label"]);
                if (node["shape"] === "circle" && node["label"].includes("\n")) {
                    host.dataset["labelDensity"] = "long";
                }
                host.append(label);
            }

            break;
        }
        case "symbol": {
            host.dataset["symbol"] = scalar_text(node["symbol"]);
            host.setAttribute("aria-hidden", node["ariaLabel"] ? "false" : "true");

            break;
        }
        case "mark": {
            host.append(render_mark(node, data));

            break;
        }
        case "path": {
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            svg.setAttribute("viewBox", "0 0 100 100");
            svg.setAttribute("aria-hidden", "true");
            const path = document.createElementNS(
                "http://www.w3.org/2000/svg",
                node["closed"] ? "polygon" : "polyline",
            );
            const raw_points = node["points"];
            const points: string[] = [];
            if (Array.isArray(raw_points)) {
                for (const raw_point of raw_points) {
                    const point: unknown = raw_point;
                    if (
                        Array.isArray(point) &&
                        typeof point[0] === "number" &&
                        typeof point[1] === "number"
                    ) {
                        points.push(
                            `${Math.max(0, Math.min(100, point[0]))},${Math.max(0, Math.min(100, point[1]))}`,
                        );
                    }
                }
            }
            path.setAttribute("points", points.join(" "));
            path.setAttribute("class", "legacy-artifact-path-line");
            svg.append(path);
            host.append(svg);

            break;
        }
        case "control": {
            const control = scalar_text(node["control"]);
            const label = scalar_text(node["label"]);
            const action = scalar_text(node["action"]);
            if (control === "toggle") {
                const button = element("button", "legacy-artifact-control-button", label);
                button.type = "button";
                if (action !== "") {
                    button.dataset["action"] = action;
                }
                button.setAttribute("aria-pressed", String(Boolean(node["value"])));
                button.addEventListener(
                    "click",
                    () => {
                        button.setAttribute(
                            "aria-pressed",
                            String(button.getAttribute("aria-pressed") !== "true"),
                        );
                    },
                    {signal: controller.signal},
                );
                host.append(button);
            } else if (control === "range") {
                const label_node = element("label", "legacy-artifact-control-label", label);
                const input = document.createElement("input");
                input.type = "range";
                input.min = scalar_text(node["min"] ?? 0);
                input.max = scalar_text(node["max"] ?? 100);
                input.step = scalar_text(node["step"] ?? 1);
                input.value = scalar_text(node["value"] ?? node["min"] ?? 0);
                if (action !== "") {
                    input.dataset["action"] = action;
                }
                label_node.append(input);
                host.append(label_node);
            } else {
                let value = Number(node["value"] ?? node["min"] ?? 0);
                const render_label = (): string =>
                    control === "stepper" && Number.isFinite(value) ? `${label}  ${value}` : label;
                const button = element("button", "legacy-artifact-control-button", render_label());
                button.type = "button";
                if (action !== "") {
                    button.dataset["action"] = action;
                }
                button.addEventListener(
                    "click",
                    () => {
                        if (control === "stepper") {
                            const step = Number(node["step"] ?? 1);
                            const maximum = Number(node["max"] ?? Infinity);
                            value = Math.min(maximum, value + step);
                            button.textContent = render_label();
                        } else {
                            button.dataset["activated"] = "true";
                        }
                    },
                    {signal: controller.signal},
                );
                host.append(button);
            }

            break;
        }
        // No default
    }
    return host;
}

function mount_connectors(
    scene: HTMLElement,
    connectors: Connector[],
    controller: AbortController,
): () => void {
    const {signal} = controller;
    if (connectors.length === 0) {
        return () => undefined;
    }
    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("class", "legacy-artifact-connectors");
    overlay.setAttribute("aria-hidden", "true");
    const marker_id = `legacy-artifact-arrow-${connector_instance}`;
    connector_instance += 1;
    const definitions = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", marker_id);
    marker.setAttribute("viewBox", "0 0 8 8");
    marker.setAttribute("refX", "7");
    marker.setAttribute("refY", "4");
    marker.setAttribute("markerWidth", "7");
    marker.setAttribute("markerHeight", "7");
    marker.setAttribute("orient", "auto-start-reverse");
    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrow.setAttribute("d", "M0 0 L8 4 L0 8 Z");
    arrow.setAttribute("class", "legacy-artifact-connector-arrow");
    marker.append(arrow);
    definitions.append(marker);
    overlay.append(definitions);
    scene.prepend(overlay);
    let was_connected = false;
    const draw = (): void => {
        if (signal.aborted) {
            return;
        }
        if (scene.isConnected) {
            was_connected = true;
        } else if (was_connected) {
            controller.abort();
            return;
        }
        overlay.replaceChildren(definitions);
        const bounds = scene.getBoundingClientRect();
        overlay.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
        for (const [index, connector] of connectors.entries()) {
            const from = scene.querySelector<HTMLElement>(
                `[data-legacy-artifact-node="${CSS.escape(connector.from)}"]`,
            );
            const to = scene.querySelector<HTMLElement>(
                `[data-legacy-artifact-node="${CSS.escape(connector.to)}"]`,
            );
            if (from === null || to === null) {
                continue;
            }
            const start = from.getBoundingClientRect();
            const end = to.getBoundingClientRect();
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            const start_x = start.left + start.width / 2 - bounds.left;
            const start_y = start.top + start.height / 2 - bounds.top;
            const end_x = end.left + end.width / 2 - bounds.left;
            const end_y = end.top + end.height / 2 - bounds.top;
            const delta_x = end_x - start_x;
            const delta_y = end_y - start_y;
            const start_scale = Math.min(
                delta_x === 0 ? Infinity : Math.max(0, start.width / 2 - 4) / Math.abs(delta_x),
                delta_y === 0 ? Infinity : Math.max(0, start.height / 2 - 4) / Math.abs(delta_y),
            );
            const end_scale = Math.min(
                delta_x === 0 ? Infinity : Math.max(0, end.width / 2 - 4) / Math.abs(delta_x),
                delta_y === 0 ? Infinity : Math.max(0, end.height / 2 - 4) / Math.abs(delta_y),
            );
            const x1 = start_x + delta_x * (Number.isFinite(start_scale) ? start_scale : 0);
            const y1 = start_y + delta_y * (Number.isFinite(start_scale) ? start_scale : 0);
            const x2 = end_x - delta_x * (Number.isFinite(end_scale) ? end_scale : 0);
            const y2 = end_y - delta_y * (Number.isFinite(end_scale) ? end_scale : 0);
            line.setAttribute("x1", String(x1));
            line.setAttribute("y1", String(y1));
            line.setAttribute("x2", String(x2));
            line.setAttribute("y2", String(y2));
            line.setAttribute("pathLength", "1");
            line.style.setProperty("--legacy-artifact-order", String(index));
            if (connector.id !== undefined) {
                line.dataset["legacyArtifactTarget"] = connector.id;
            }
            const state = connector.current_state ?? connector.state;
            if (state !== undefined) {
                line.dataset["state"] = state;
            }
            line.setAttribute(
                "class",
                connector.directed
                    ? "legacy-artifact-connector is-directed"
                    : "legacy-artifact-connector",
            );
            if (connector.directed) {
                line.setAttribute("marker-end", `url(#${marker_id})`);
            }
            overlay.append(line);
            if (connector.label !== undefined) {
                const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
                const length = Math.max(Math.hypot(delta_x, delta_y), 1);
                let normal_x = -delta_y / length;
                let normal_y = delta_x / length;
                if (normal_y > 0) {
                    normal_x *= -1;
                    normal_y *= -1;
                }
                label.setAttribute("x", String((x1 + x2) / 2 + normal_x * 9));
                label.setAttribute("y", String((y1 + y2) / 2 + normal_y * 9));
                label.setAttribute("class", "legacy-artifact-connector-label");
                label.setAttribute("text-anchor", "middle");
                label.textContent = connector.label;
                overlay.append(label);
            }
        }
    };
    let draw_scheduled = false;
    const schedule_draw = (): void => {
        if (draw_scheduled || signal.aborted) {
            return;
        }
        draw_scheduled = true;
        requestAnimationFrame(() => {
            draw_scheduled = false;
            if (!signal.aborted) {
                draw();
            }
        });
    };
    schedule_draw();
    if (typeof ResizeObserver !== "undefined") {
        const observer = new ResizeObserver(() => {
            if (!scene.isConnected && was_connected) {
                controller.abort();
                return;
            }
            schedule_draw();
        });
        observer.observe(scene);
        signal.addEventListener(
            "abort",
            () => {
                observer.disconnect();
            },
            {once: true},
        );
    }
    return schedule_draw;
}

type SceneTargetSnapshot = {
    element: HTMLElement;
    state?: string;
    label?: string;
    text?: string;
};

function set_semantic_state(element: Element, state: string | undefined): void {
    if (state === undefined) {
        element.removeAttribute("data-state");
    } else {
        element.setAttribute("data-state", state);
    }
}

function set_shape_label(element: HTMLElement, value: string | undefined): void {
    const existing = element.querySelector<HTMLElement>(".legacy-artifact-shape-label");
    if (value === undefined) {
        existing?.remove();
        return;
    }
    const label = existing ?? element.ownerDocument.createElement("span");
    if (existing === null) {
        element.append(label);
    }
    label.className = "legacy-artifact-shape-label";
    set_artifact_text(label, value);
}

function mount_scene_steps(
    scene: HTMLElement,
    raw_steps: unknown,
    connectors: Connector[],
    schedule_connector_draw: () => void,
    controller: AbortController,
): HTMLElement | undefined {
    if (!Array.isArray(raw_steps)) {
        return undefined;
    }
    const steps = raw_steps.filter(is_json_object);
    if (steps.length < 2) {
        return undefined;
    }
    const target_ids = new Set<string>();
    for (const step of steps) {
        const changes = step["changes"];
        if (!Array.isArray(changes)) {
            continue;
        }
        for (const change of changes) {
            if (is_json_object(change) && typeof change["target"] === "string") {
                target_ids.add(change["target"]);
            }
        }
    }
    const snapshots = new Map<string, SceneTargetSnapshot>();
    for (const target of target_ids) {
        const target_element = scene.querySelector<HTMLElement>(
            `[data-legacy-artifact-target="${CSS.escape(target)}"]`,
        );
        if (target_element === null) {
            continue;
        }
        const snapshot: SceneTargetSnapshot = {
            element: target_element,
        };
        if (target_element.dataset["state"] !== undefined) {
            snapshot.state = target_element.dataset["state"];
        }
        const label = target_element.querySelector<HTMLElement>(".legacy-artifact-shape-label");
        if (label !== null) {
            snapshot.label = label.dataset["legacyArtifactSource"] ?? label.textContent ?? "";
        }
        if (target_element.classList.contains("legacy-artifact-text")) {
            snapshot.text =
                target_element.dataset["legacyArtifactSource"] ?? target_element.textContent ?? "";
        }
        snapshots.set(target, snapshot);
    }
    const connector_by_id = new Map(
        connectors.flatMap((connector) =>
            connector.id === undefined ? [] : [[connector.id, connector] as const],
        ),
    );
    const timeline = element("div", "legacy-artifact-timeline");
    timeline.setAttribute("role", "group");
    timeline.setAttribute("aria-label", "Scene playback");
    const previous = element("button", "legacy-artifact-step-button is-previous");
    previous.type = "button";
    previous.setAttribute("aria-label", "Previous step");
    const play = element("button", "legacy-artifact-step-button is-play");
    play.type = "button";
    play.setAttribute("aria-label", "Play animation");
    const meta = element("div", "legacy-artifact-step-meta");
    const status = element("div", "legacy-artifact-step-status");
    status.setAttribute("aria-hidden", "true");
    const announcement = element("div", "legacy-artifact-step-announcement");
    const announcement_id = `legacy-artifact-step-${scene_step_instance}`;
    scene_step_instance += 1;
    announcement.id = announcement_id;
    announcement.setAttribute("aria-live", "polite");
    announcement.setAttribute("aria-atomic", "true");
    scene.setAttribute("aria-describedby", announcement_id);
    const track = element("div", "legacy-artifact-step-track");
    track.tabIndex = 0;
    track.setAttribute("role", "slider");
    track.setAttribute("aria-label", "Scene step");
    track.setAttribute("aria-orientation", "horizontal");
    track.setAttribute("aria-valuemin", "1");
    track.setAttribute("aria-valuemax", String(steps.length));
    const fill = element("span", "legacy-artifact-step-fill");
    track.append(fill);
    meta.append(status, track, announcement);
    const next = element("button", "legacy-artifact-step-button is-next");
    next.type = "button";
    next.setAttribute("aria-label", "Next step");
    timeline.append(previous, play, meta, next);

    let current_index = 0;
    let playing = false;
    let visible = false;
    let wants_autoplay = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const signal = controller.signal;
    const motion_query =
        typeof globalThis.matchMedia === "function"
            ? globalThis.matchMedia("(prefers-reduced-motion: reduce)")
            : undefined;
    const sync_connectors = (): void => {
        for (const connector of connector_by_id.values()) {
            const line = scene.querySelector(
                `[data-legacy-artifact-target="${CSS.escape(connector.id!)}"]`,
            );
            if (line !== null) {
                set_semantic_state(line, connector.current_state ?? connector.state);
            }
        }
    };
    const apply_step = (index: number): void => {
        current_index = Math.max(0, Math.min(steps.length - 1, index));
        for (const snapshot of snapshots.values()) {
            set_semantic_state(snapshot.element, snapshot.state);
            if (snapshot.element.classList.contains("legacy-artifact-shape")) {
                set_shape_label(snapshot.element, snapshot.label);
            }
            if (snapshot.text !== undefined) {
                set_artifact_text(snapshot.element, snapshot.text);
            }
        }
        for (const connector of connector_by_id.values()) {
            if (connector.state === undefined) {
                delete connector.current_state;
            } else {
                connector.current_state = connector.state;
            }
        }
        for (const step of steps.slice(0, current_index + 1)) {
            const changes = step["changes"];
            if (!Array.isArray(changes)) {
                continue;
            }
            for (const change of changes) {
                if (!is_json_object(change) || typeof change["target"] !== "string") {
                    continue;
                }
                const target = change["target"];
                const connector = connector_by_id.get(target);
                if (connector !== undefined && typeof change["state"] === "string") {
                    connector.current_state = change["state"];
                }
                const snapshot = snapshots.get(target);
                if (snapshot === undefined) {
                    continue;
                }
                if (typeof change["state"] === "string") {
                    set_semantic_state(snapshot.element, change["state"]);
                }
                if (typeof change["label"] === "string") {
                    set_shape_label(snapshot.element, change["label"]);
                }
                if (typeof change["text"] === "string") {
                    set_artifact_text(snapshot.element, change["text"]);
                }
            }
        }
        sync_connectors();
        schedule_connector_draw();
        const current_step = steps[current_index];
        const step_label = artifact_accessible_text(scalar_text(current_step?.["label"]));
        status.textContent = `${current_index + 1}/${steps.length}  ${step_label}`;
        const descriptions: string[] = [];
        const current_changes = current_step?.["changes"];
        if (Array.isArray(current_changes)) {
            for (const change of current_changes) {
                if (!is_json_object(change) || typeof change["target"] !== "string") {
                    continue;
                }
                if (typeof change["state"] === "string") {
                    descriptions.push(`${change["target"]} is ${change["state"]}`);
                }
                if (typeof change["label"] === "string") {
                    descriptions.push(
                        `${change["target"]} label: ${artifact_accessible_text(change["label"])}`,
                    );
                }
                if (typeof change["text"] === "string") {
                    descriptions.push(
                        `${change["target"]} text: ${artifact_accessible_text(change["text"])}`,
                    );
                }
            }
        }
        announcement.textContent = `Step ${current_index + 1} of ${steps.length}: ${step_label}.${
            descriptions.length > 0 ? ` ${descriptions.join(". ")}.` : ""
        }`;
        fill.style.setProperty(
            "--legacy-artifact-step-progress",
            String((current_index + 1) / steps.length),
        );
        track.setAttribute("aria-valuenow", String(current_index + 1));
        track.setAttribute("aria-valuetext", step_label);
        previous.disabled = current_index === 0;
        next.disabled = current_index === steps.length - 1;
    };
    const schedule_next = (): void => {
        if (!playing || signal.aborted) {
            return;
        }
        timer = setTimeout(() => {
            timer = undefined;
            if (signal.aborted) {
                return;
            }
            if (!scene.isConnected) {
                controller.abort();
                return;
            }
            if (document.hidden || motion_query?.matches || !visible) {
                set_playing(false);
                return;
            }
            if (current_index >= steps.length - 1) {
                wants_autoplay = false;
                set_playing(false);
                return;
            }
            apply_step(current_index + 1);
            schedule_next();
        }, 1050);
    };
    const set_playing = (value: boolean, user_initiated = false): void => {
        if (timer !== undefined) {
            clearTimeout(timer);
            timer = undefined;
        }
        playing =
            value &&
            !signal.aborted &&
            scene.isConnected &&
            !document.hidden &&
            !motion_query?.matches &&
            (visible || user_initiated);
        play.dataset["playing"] = playing ? "true" : "false";
        play.setAttribute("aria-label", playing ? "Pause animation" : "Play animation");
        if (playing) {
            schedule_next();
        }
    };
    const select_step = (index: number): void => {
        wants_autoplay = false;
        set_playing(false);
        apply_step(index);
    };
    previous.addEventListener(
        "click",
        () => {
            select_step(current_index - 1);
        },
        {signal},
    );
    next.addEventListener(
        "click",
        () => {
            select_step(current_index + 1);
        },
        {signal},
    );
    track.addEventListener(
        "keydown",
        (event) => {
            switch (event.key) {
                case "ArrowDown":
                case "ArrowLeft": {
                    event.preventDefault();
                    select_step(current_index - 1);
                    break;
                }
                case "ArrowRight":
                case "ArrowUp": {
                    event.preventDefault();
                    select_step(current_index + 1);
                    break;
                }
                case "Home": {
                    event.preventDefault();
                    select_step(0);
                    break;
                }
                case "End": {
                    event.preventDefault();
                    select_step(steps.length - 1);
                    break;
                }
            }
        },
        {signal},
    );
    play.addEventListener(
        "click",
        () => {
            if (playing) {
                wants_autoplay = false;
                set_playing(false);
                return;
            }
            if (current_index === steps.length - 1) {
                apply_step(0);
            }
            wants_autoplay = true;
            set_playing(true, true);
        },
        {signal},
    );
    apply_step(0);
    let visibility_observer: IntersectionObserver | undefined;
    if (typeof IntersectionObserver !== "undefined") {
        visibility_observer = new IntersectionObserver((entries) => {
            if (!scene.isConnected) {
                controller.abort();
                return;
            }
            visible = entries.some((entry) => entry.target === scene && entry.isIntersecting);
            if (visible && wants_autoplay) {
                set_playing(true);
            } else if (!visible) {
                set_playing(false);
            }
        });
        visibility_observer.observe(scene);
    }
    const handle_visibility_change = (): void => {
        if (document.hidden) {
            set_playing(false);
        } else if (visible && wants_autoplay) {
            set_playing(true);
        }
    };
    const sync_reduced_motion = (): void => {
        const transition = motion_query?.matches ? "none" : "";
        fill.style.transition = transition;
        for (const node of scene.querySelectorAll<HTMLElement>("[data-transition]")) {
            node.style.transition = transition;
        }
    };
    const handle_motion_change = (): void => {
        sync_reduced_motion();
        if (motion_query?.matches) {
            wants_autoplay = false;
            set_playing(false);
        }
    };
    sync_reduced_motion();
    document.addEventListener("visibilitychange", handle_visibility_change);
    motion_query?.addEventListener("change", handle_motion_change);
    signal.addEventListener(
        "abort",
        () => {
            if (timer !== undefined) {
                clearTimeout(timer);
                timer = undefined;
            }
            playing = false;
            play.dataset["playing"] = "false";
            play.setAttribute("aria-label", "Play animation");
            visibility_observer?.disconnect();
            document.removeEventListener("visibilitychange", handle_visibility_change);
            motion_query?.removeEventListener("change", handle_motion_change);
        },
        {once: true},
    );
    return timeline;
}

function render_scene(
    block: ArmadaArtifactBlock,
    data: JsonObject,
    controller: AbortController,
): HTMLElement {
    const frame = element("div", "legacy-artifact-scene-frame");
    const scene = element("div", "legacy-artifact-scene");
    if (typeof block["aspect"] === "string") {
        scene.dataset["aspect"] = block["aspect"];
    }
    const connectors: Connector[] = [];
    const raw_root = block["root"];
    const root = is_json_object(raw_root)
        ? render_scene_node(raw_root, data, connectors, controller)
        : undefined;
    if (root !== undefined) {
        scene.append(root);
    }
    for (const graph of scene.querySelectorAll<HTMLElement>(
        '.legacy-artifact-group[data-layout="graph"]',
    )) {
        layout_graph_group(graph, connectors);
    }
    const schedule_connector_draw = mount_connectors(scene, connectors, controller);
    frame.append(scene);
    const timeline = mount_scene_steps(
        scene,
        block["steps"],
        connectors,
        schedule_connector_draw,
        controller,
    );
    if (timeline !== undefined) {
        frame.append(timeline);
    }
    scene.setAttribute(
        "role",
        timeline !== undefined ||
            scene.querySelector(
                '[data-interaction]:not([data-interaction="none"]), button, input',
            ) !== null
            ? "group"
            : "img",
    );
    scene.setAttribute("aria-label", scalar_text(block["label"]));
    return frame;
}

function render_media(block: ArmadaArtifactBlock, controller: AbortController): HTMLElement {
    const figure = element("figure", "legacy-artifact-media");
    if (typeof block["aspect"] === "string") {
        figure.dataset["aspect"] = block["aspect"];
    }
    figure.dataset["status"] = "loading";
    const frame = element("div", "legacy-artifact-media-frame");
    const status = element("div", "legacy-artifact-media-status", "Loading visual…");
    status.setAttribute("role", "status");
    const set_intrinsic_geometry = (width: number, height: number): void => {
        if (
            figure.dataset["aspect"] !== undefined ||
            !Number.isFinite(width) ||
            !Number.isFinite(height) ||
            width <= 0 ||
            height <= 0
        ) {
            return;
        }
        const ratio = width / height;
        const aspect =
            ratio < 0.85 ? "tall" : ratio < 1.2 ? "square" : ratio < 1.6 ? "standard" : "wide";
        figure.dataset["intrinsicAspect"] = aspect;
        frame.style.setProperty("--legacy-artifact-media-ratio", `${ratio}`);
    };
    const set_ready = (): void => {
        figure.dataset["status"] = "ready";
        status.textContent = "";
    };
    const set_failed = (): void => {
        figure.dataset["status"] = "failed";
        status.textContent = "Visual unavailable";
    };

    if (block["kind"] === "image") {
        const image = document.createElement("img");
        image.className = "legacy-artifact-media-content";
        image.alt = scalar_text(block["alt"]);
        image.loading = "lazy";
        image.decoding = "async";
        const update_source = (): void => {
            const source = themed_media_value(block, "src");
            if (image.getAttribute("src") !== source) {
                figure.dataset["status"] = "loading";
                status.textContent = "Loading visual…";
                image.src = source;
            }
        };
        image.addEventListener(
            "load",
            () => {
                set_intrinsic_geometry(image.naturalWidth, image.naturalHeight);
                set_ready();
            },
            {signal: controller.signal},
        );
        image.addEventListener("error", set_failed, {signal: controller.signal});
        watch_theme(controller, update_source);
        update_source();
        frame.append(image);
    } else {
        const video = document.createElement("video");
        video.className = "legacy-artifact-media-content";
        video.setAttribute("aria-label", scalar_text(block["alt"]));
        video.controls = true;
        video.muted = true;
        video.playsInline = true;
        video.preload = "metadata";
        video.loop = block["motion"] === "loop";
        let has_played = false;
        let is_visible = false;
        const reduced_motion_query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
        const may_autoplay = (): boolean =>
            block["motion"] !== "manual" && !reduced_motion_query?.matches;
        const play_video = async (): Promise<void> => {
            try {
                await video.play();
            } catch {
                // Native controls remain available when autoplay is denied.
            }
        };
        const update_playback = (): void => {
            if (!is_visible || !may_autoplay() || (block["motion"] === "once" && has_played)) {
                video.pause();
                return;
            }
            has_played = true;
            void play_video();
        };
        const update_source = (): void => {
            const source = themed_media_value(block, "src");
            const poster = themed_media_value(block, "poster");
            if (video.getAttribute("src") !== source) {
                video.pause();
                has_played = false;
                figure.dataset["status"] = "loading";
                status.textContent = "Loading visual…";
                video.src = source;
            }
            if (poster) {
                video.poster = poster;
            } else {
                video.removeAttribute("poster");
            }
            update_playback();
        };
        video.addEventListener(
            "loadeddata",
            () => {
                set_intrinsic_geometry(video.videoWidth, video.videoHeight);
                set_ready();
            },
            {signal: controller.signal},
        );
        video.addEventListener("error", set_failed, {signal: controller.signal});
        video.addEventListener("ended", () => (has_played = true), {signal: controller.signal});
        reduced_motion_query?.addEventListener("change", update_playback, {
            signal: controller.signal,
        });
        const observer =
            typeof IntersectionObserver === "undefined"
                ? undefined
                : new IntersectionObserver((entries) => {
                      if (!video.isConnected) {
                          controller.abort();
                          return;
                      }
                      is_visible = entries.some((entry) => entry.isIntersecting);
                      update_playback();
                  });
        observer?.observe(video);
        controller.signal.addEventListener(
            "abort",
            () => {
                observer?.disconnect();
                video.pause();
            },
            {once: true},
        );
        watch_theme(controller, update_source);
        update_source();
        frame.append(video);
    }
    frame.append(status);
    figure.append(frame);
    if (typeof block["caption"] === "string" && block["caption"] !== "") {
        const caption = element("figcaption", "legacy-artifact-media-caption");
        set_artifact_text(caption, block["caption"]);
        figure.append(caption);
    }
    return figure;
}

function render_block(
    block: ArmadaArtifactBlock,
    data: JsonObject,
    controller: AbortController,
): HTMLElement {
    const host = element("section", `legacy-artifact-block legacy-artifact-block-${block.type}`);
    switch (block.type) {
        case "copy": {
            if (typeof block["eyebrow"] === "string") {
                host.append(element("div", "legacy-artifact-eyebrow", block["eyebrow"]));
            }
            if (typeof block["heading"] === "string") {
                host.append(element("h3", "legacy-artifact-heading", block["heading"]));
            }
            const copy = element("div", "legacy-artifact-copy");
            set_artifact_text(copy, scalar_text(block["body"]));
            host.append(copy);

            break;
        }
        case "scene": {
            host.append(render_scene(block, data, controller));

            break;
        }
        case "media": {
            host.append(render_media(block, controller));

            break;
        }
        case "metric": {
            host.append(element("div", "legacy-artifact-label", scalar_text(block["label"])));
            const value = element(
                "div",
                "legacy-artifact-metric",
                scalar_text(data_value(data, block["bind"]) ?? block["value"]),
            );
            if (block["unit"]) {
                value.append(element("span", "legacy-artifact-unit", scalar_text(block["unit"])));
            }
            host.append(value);
            if (typeof block["delta"] === "number") {
                const delta = block["delta"];
                const delta_node = element(
                    "div",
                    "legacy-artifact-delta",
                    `${delta > 0 ? "+" : ""}${delta}`,
                );
                delta_node.dataset["direction"] = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
                host.append(delta_node);
            }

            break;
        }
        case "meter": {
            host.append(element("div", "legacy-artifact-label", scalar_text(block["label"])));
            const total =
                typeof block["total"] === "number" && block["total"] > 0 ? block["total"] : 100;
            const raw_current = data_value(data, block["bind"]) ?? block["value"];
            const numeric_current = Number(raw_current);
            const current = Number.isFinite(numeric_current)
                ? numeric_current
                : Number(block["value"]);
            const percent = Math.max(0, Math.min(100, (current / total) * 100));
            const track = element("div", "legacy-artifact-meter");
            const fill = element("span", "legacy-artifact-meter-fill");
            fill.style.setProperty("--legacy-artifact-progress", String(percent / 100));
            track.append(fill);
            host.append(track);
            const unit = scalar_text(block["unit"]);
            host.append(
                element(
                    "div",
                    "legacy-artifact-meter-meta",
                    `${current} / ${total}${unit === "" ? "" : ` ${unit}`}`,
                ),
            );
            if (block["detail"]) {
                host.append(element("div", "legacy-artifact-detail", scalar_text(block["detail"])));
            }

            break;
        }
        case "series": {
            const label = element("div", "legacy-artifact-label", scalar_text(block["label"]));
            if (block["unit"]) {
                label.append(
                    element("span", "legacy-artifact-series-unit", scalar_text(block["unit"])),
                );
            }
            host.append(label);
            host.append(
                render_mark(
                    {type: "mark", mark: "line", values: block["values"], bind: block["bind"]},
                    data,
                ),
            );

            break;
        }
        case "log": {
            host.append(element("div", "legacy-artifact-label", scalar_text(block["label"])));
            const pre = element("pre", "legacy-artifact-log");
            pre.textContent = artifact_log_text(block, data);
            host.append(pre);

            break;
        }
        case "key_value": {
            host.append(element("div", "legacy-artifact-label", scalar_text(block["label"])));
            const list = element("dl", "legacy-artifact-key-values");
            const items = block["items"];
            if (Array.isArray(items)) {
                for (const item of items) {
                    if (is_json_object(item)) {
                        list.append(
                            element("dt", undefined, scalar_text(item["key"])),
                            element("dd", undefined, scalar_text(item["value"])),
                        );
                    }
                }
            }
            host.append(list);

            break;
        }
        // No default
    }
    if (typeof block["state"] === "string") {
        host.dataset["state"] = block["state"];
    }
    return host;
}

function artifact_log_text(block: ArmadaArtifactBlock, data: JsonObject): string {
    const bound_lines = data_value(data, block["bind"]);
    const lines = Array.isArray(bound_lines)
        ? bound_lines
        : Array.isArray(block["lines"])
          ? block["lines"]
          : [];
    return lines.map((line) => scalar_text(line)).join("\n");
}

function set_utility_control_presentation(
    control: HTMLElement,
    icon: string,
    aria_label: string,
): void {
    const glyph = element("i", `zulip-icon zulip-icon-${icon}`);
    glyph.setAttribute("aria-hidden", "true");
    control.replaceChildren(glyph);
    control.setAttribute("aria-label", aria_label);
    control.setAttribute("data-tippy-content", aria_label);
}

function utility_button(
    icon: string,
    aria_label: string,
    controller: AbortController,
    action: (button: HTMLButtonElement) => void | Promise<void>,
): HTMLButtonElement {
    const button = element(
        "button",
        "legacy-artifact-action legacy-artifact-utility tippy-zulip-tooltip",
    );
    button.type = "button";
    set_utility_control_presentation(button, icon, aria_label);
    button.addEventListener(
        "click",
        () => {
            void (async () => {
                try {
                    await action(button);
                } catch {
                    set_utility_control_presentation(button, "circle-x", "Unavailable");
                }
            })();
        },
        {signal: controller.signal},
    );
    return button;
}

async function copy_artifact_text(text: string, button: HTMLButtonElement): Promise<void> {
    try {
        await navigator.clipboard.writeText(text);
        set_utility_control_presentation(button, "check", "Copied");
    } catch {
        set_utility_control_presentation(button, "circle-x", "Copy unavailable");
    }
}

function render_artifact_utilities(
    article: HTMLElement,
    body: HTMLElement,
    document_value: ArmadaArtifactDocument,
    data: JsonObject,
    controller: AbortController,
): HTMLElement[] {
    const controls: HTMLElement[] = [];
    for (const [index, block] of document_value.blocks.entries()) {
        if (block.type !== "copy" && block.type !== "log") {
            continue;
        }
        const text =
            block.type === "copy" ? scalar_text(block["body"]) : artifact_log_text(block, data);
        const kind = block.type === "copy" ? "text" : "log";
        controls.push(
            utility_button(
                "copy",
                `Copy ${kind} block ${index + 1}`,
                controller,
                async (button) => {
                    await copy_artifact_text(text, button);
                },
            ),
        );
    }

    const media_blocks = document_value.blocks.filter((block) => block.type === "media");
    const media_hosts = article.querySelectorAll<HTMLElement>(".legacy-artifact-media");
    for (const [index, block] of media_blocks.entries()) {
        const media_host = media_hosts[index];
        const media_frame = media_host?.querySelector<HTMLElement>(".legacy-artifact-media-frame");
        if (media_frame?.requestFullscreen !== undefined) {
            controls.push(
                utility_button(
                    "expand-both-diagonals",
                    `Expand media ${index + 1}`,
                    controller,
                    async () => {
                        await media_frame.requestFullscreen();
                    },
                ),
            );
        }
        const download_source = themed_media_value(block, "src");
        if (download_source !== "") {
            const download = document.createElement("a");
            download.className =
                "legacy-artifact-action legacy-artifact-utility tippy-zulip-tooltip";
            download.download = "";
            download.href = download_source;
            set_utility_control_presentation(download, "download", `Download media ${index + 1}`);
            download.addEventListener(
                "click",
                () => {
                    download.href = themed_media_value(block, "src");
                },
                {signal: controller.signal},
            );
            controls.push(download);
        }
    }

    const raw = element("pre", "legacy-artifact-log legacy-artifact-raw-json");
    const raw_id = `legacy-artifact-raw-${raw_document_instance}`;
    raw_document_instance += 1;
    raw.id = raw_id;
    raw.hidden = true;
    body.append(raw);
    const raw_toggle = utility_button("code", "View raw artifact JSON", controller, (button) => {
        raw.hidden = !raw.hidden;
        if (!raw.hidden && raw.textContent === "") {
            raw.textContent = JSON.stringify(document_value, undefined, 2);
        }
        button.setAttribute("aria-expanded", String(!raw.hidden));
        set_utility_control_presentation(
            button,
            "code",
            raw.hidden ? "View raw artifact JSON" : "Hide raw artifact JSON",
        );
    });
    raw_toggle.setAttribute("aria-controls", raw_id);
    raw_toggle.setAttribute("aria-expanded", "false");
    controls.push(raw_toggle);
    return controls;
}

function authored_node_count(document_value: ArmadaArtifactDocument): number {
    let count = 0;
    function visit(node: JsonObject): void {
        count += 1;
        const children = node["children"];
        if (Array.isArray(children)) {
            for (const child of children) {
                if (is_json_object(child)) {
                    visit(child);
                }
            }
        }
    }
    for (const block of document_value.blocks) {
        if (block.type === "scene") {
            const root = block["root"];
            if (is_json_object(root)) {
                visit(root);
            }
        }
    }
    return count;
}

export function render_armada_artifact(
    document_value: ArmadaArtifactDocument,
    options: ArmadaArtifactRenderOptions,
): HTMLElement {
    const render_started = globalThis.performance?.now() ?? Date.now();
    const article = element("article", "legacy-artifact");
    const controller = new AbortController();
    artifact_lifecycles.set(article, controller);
    article.dataset["density"] = options.density;
    article.dataset["lifecycle"] = "ready";
    if (document_value.title !== undefined) {
        const header = element("header", "legacy-artifact-header");
        header.append(
            element("span", "legacy-artifact-kind", "explainer"),
            element("h2", "legacy-artifact-title", document_value.title),
        );
        article.append(header);
    }
    const body = element("div", "legacy-artifact-body");
    const data = document_value.data ?? {};
    body.append(...document_value.blocks.map((block) => render_block(block, data, controller)));
    article.append(body);
    const utility_controls = render_artifact_utilities(
        article,
        body,
        document_value,
        data,
        controller,
    );
    const document_actions = document_value.actions ?? [];
    if (document_actions.length > 0 || options.onPin || utility_controls.length > 0) {
        const actions = element("footer", "legacy-artifact-actions");
        for (const action of document_actions) {
            const button = element("button", "legacy-artifact-action", action.label);
            button.type = "button";
            button.addEventListener("click", () => options.onAction?.(action, button), {
                signal: controller.signal,
            });
            actions.append(button);
        }
        actions.append(...utility_controls);
        if (options.onPin) {
            const spacer = element("span", "legacy-artifact-action-spacer");
            const pin = element("button", "legacy-artifact-pin", "↗ pin to wall");
            pin.type = "button";
            pin.addEventListener("click", () => options.onPin?.(pin), {
                signal: controller.signal,
            });
            actions.append(spacer, pin);
        }
        article.append(actions);
    }
    article.dataset["artifactBlocks"] = String(document_value.blocks.length);
    article.dataset["artifactNodes"] = String(authored_node_count(document_value));
    article.dataset["renderMs"] = Math.max(
        0,
        (globalThis.performance?.now() ?? Date.now()) - render_started,
    ).toFixed(2);
    return article;
}

export function mount_armada_artifact(
    $host: JQuery,
    document_value: ArmadaArtifactDocument,
    options: ArmadaArtifactRenderOptions,
): HTMLElement {
    const artifact = render_armada_artifact(document_value, options);
    const controller = artifact_lifecycles.get(artifact);
    replace_armada_artifact_host($host, artifact, () => {
        controller?.abort();
        artifact_lifecycles.delete(artifact);
    });
    if (
        controller !== undefined &&
        options.onGeometryChange !== undefined &&
        typeof ResizeObserver !== "undefined"
    ) {
        let previous_height: number | undefined;
        const observer = new ResizeObserver((entries) => {
            const entry = entries.find(({target}) => target === artifact);
            if (entry === undefined) {
                return;
            }
            const next_height = entry.contentRect.height;
            if (previous_height !== undefined && previous_height !== next_height) {
                options.onGeometryChange?.(artifact, previous_height, next_height);
            }
            previous_height = next_height;
        });
        observer.observe(artifact);
        controller.signal.addEventListener(
            "abort",
            () => {
                observer.disconnect();
            },
            {once: true},
        );
    }
    return artifact;
}
