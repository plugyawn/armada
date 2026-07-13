import {PEAR_ARTIFACT_SCHEMA} from "./pear_artifact_contract_generated.ts";

export const PEAR_ARTIFACT_SUBMESSAGE_TYPE = "pear_artifact";

type JsonSchema = Record<string, unknown>;
type JsonObject = Record<string, unknown>;

function is_json_object(value: unknown): value is JsonObject {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export type PearArtifactAction = {label: string; prompt: string};
export type PearArtifactBlock = JsonObject & {type: string};
export type PearArtifactDocument = {
    version: 1;
    title?: string;
    data?: JsonObject;
    blocks: PearArtifactBlock[];
    actions?: PearArtifactAction[];
};

export type PearArtifactRenderOptions = {
    density: "chat" | "wall";
    onAction?: (action: PearArtifactAction, button: HTMLButtonElement) => void;
    onPin?: (button: HTMLButtonElement) => void;
};

export type PearArtifactModule = {
    parse_pear_artifact: typeof parse_pear_artifact;
    mount_pear_artifact: typeof mount_pear_artifact;
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
        if (typeof pattern === "string" && !new RegExp(pattern, "u").test(value)) {
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
        const max = schema["maxProperties"];
        if (typeof max === "number" && keys.length > max) {
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
    const definitions = PEAR_ARTIFACT_SCHEMA["$defs"];
    if (!is_json_object(definitions)) {
        return false;
    }
    const node_definition = definitions["node"];
    if (!is_json_object(node_definition)) {
        return false;
    }
    const allowed_properties_by_type = node_definition["x-pear-type-properties"];
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

export function parse_pear_artifact(raw: string): PearArtifactDocument | undefined {
    if (new TextEncoder().encode(raw).length > 64000) {
        return undefined;
    }
    let value: unknown;
    try {
        value = JSON.parse(raw);
    } catch {
        return undefined;
    }
    if (!validates(PEAR_ARTIFACT_SCHEMA, value, PEAR_ARTIFACT_SCHEMA)) {
        return undefined;
    }
    if (!is_json_object(value) || value["version"] !== 1 || !Array.isArray(value["blocks"])) {
        return undefined;
    }
    const blocks: PearArtifactBlock[] = [];
    for (const block of value["blocks"]) {
        if (!is_json_object(block) || typeof block["type"] !== "string") {
            return undefined;
        }
        blocks.push({...block, type: block["type"]});
    }
    const document: PearArtifactDocument = {version: 1, blocks};
    if (typeof value["title"] === "string") {
        document.title = value["title"];
    }
    if (is_json_object(value["data"])) {
        document.data = value["data"];
    }
    if (Array.isArray(value["actions"])) {
        document.actions = value["actions"].filter(
            (action): action is PearArtifactAction =>
                is_json_object(action) &&
                typeof action["label"] === "string" &&
                typeof action["prompt"] === "string",
        );
    }
    if (
        document.blocks.some((block) => block.type === "scene" && !is_json_object(block["root"])) ||
        document.blocks.some(
            (block) =>
                block.type === "scene" &&
                is_json_object(block["root"]) &&
                !semantic_scene_is_valid(block["root"]),
        )
    ) {
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
    from: string;
    to: string;
    directed: boolean;
    label?: string;
    state?: string;
};

function graph_distance_ranks(
    node_ids: Iterable<string>,
    connectors: Connector[],
): Map<string, number> {
    const neighbours = new Map([...node_ids].map((id) => [id, [] as string[]]));
    for (const connector of connectors) {
        neighbours.get(connector.from)?.push(connector.to);
        neighbours.get(connector.to)?.push(connector.from);
    }
    const ranks = new Map<string, number>();
    const remaining = new Set(neighbours.keys());
    let component_offset = 0;
    while (remaining.size > 0) {
        const root = remaining.values().next().value!;
        const queue = [root];
        ranks.set(root, component_offset);
        remaining.delete(root);
        let component_max = component_offset;
        for (let index = 0; index < queue.length; index += 1) {
            const id = queue[index]!;
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
    const nodes = [...host.querySelectorAll<HTMLElement>(":scope > [data-pear-artifact-node]")];
    const node_by_id = new Map(
        nodes.flatMap((node) => {
            const id = node.dataset["pearArtifactNode"];
            return id === undefined ? [] : [[id, node] as const];
        }),
    );
    if (node_by_id.size === 0) {
        return;
    }
    const local_connectors = connectors.filter(
        (connector) => node_by_id.has(connector.from) && node_by_id.has(connector.to),
    );
    if (local_connectors.some((connector) => !connector.directed)) {
        position_graph_nodes(
            host,
            node_by_id,
            graph_distance_ranks(node_by_id.keys(), local_connectors),
        );
        return;
    }
    const incoming = new Map([...node_by_id.keys()].map((id) => [id, 0]));
    const outgoing = new Map([...node_by_id.keys()].map((id) => [id, [] as string[]]));
    for (const connector of local_connectors) {
        incoming.set(connector.to, (incoming.get(connector.to) ?? 0) + 1);
        outgoing.get(connector.from)?.push(connector.to);
    }
    const ranks = new Map<string, number>();
    const queue = [...node_by_id.keys()].filter((id) => incoming.get(id) === 0);
    for (const id of queue) {
        ranks.set(id, 0);
    }
    for (let index = 0; index < queue.length; index += 1) {
        const id = queue[index]!;
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
        );
        return;
    }
    position_graph_nodes(host, node_by_id, ranks);
}

function position_graph_nodes(
    host: HTMLElement,
    node_by_id: Map<string, HTMLElement>,
    ranks: Map<string, number>,
): void {
    const columns = Math.max(...ranks.values()) + 1;
    const nodes_by_rank = Array.from({length: columns}, () => [] as HTMLElement[]);
    for (const [id, node] of node_by_id) {
        nodes_by_rank[ranks.get(id) ?? 0]!.push(node);
    }
    const rows = Math.max(1, ...nodes_by_rank.map((rank_nodes) => rank_nodes.length));
    host.style.setProperty("--pear-artifact-graph-columns", String(columns));
    host.style.setProperty("--pear-artifact-graph-rows", String(rows));
    for (const [column, rank_nodes] of nodes_by_rank.entries()) {
        for (const [index, node] of rank_nodes.entries()) {
            node.style.gridColumn = String(column + 1);
            node.style.gridRow =
                rank_nodes.length === 1
                    ? `1 / span ${rows}`
                    : String(Math.round((index * (rows - 1)) / (rank_nodes.length - 1)) + 1);
            node.style.setProperty("--pear-artifact-order", String(column * rows + index));
        }
    }
}

let connector_instance = 0;

function render_mark(node: JsonObject, data: JsonObject): HTMLElement {
    const host = element(
        "div",
        `pear-artifact-mark pear-artifact-mark-${scalar_text(node["mark"])}`,
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
            const bar = element("span", "pear-artifact-bar");
            bar.style.setProperty(
                "--pear-artifact-value",
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
            circle.setAttribute("class", "pear-artifact-series-point");
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
                    `pear-artifact-series-arc pear-artifact-cat-${(index % 6) + 1}`,
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
            node["mark"] === "area" ? "pear-artifact-series-area" : "pear-artifact-series-line",
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
): HTMLElement | undefined {
    const repeat = node["repeat"];
    if (is_json_object(repeat)) {
        const items = data_value(data, repeat["source"]);
        const limit = typeof repeat["limit"] === "number" ? repeat["limit"] : 16;
        const repeated = element("div", "pear-artifact-repeat");
        if (!Array.isArray(items)) {
            return repeated;
        }
        const template = {...node};
        delete template["repeat"];
        for (const [index, item] of items.slice(0, limit).entries()) {
            const rendered = render_scene_node(template, {...data, item, index}, connectors);
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
        if (typeof node["label"] === "string") {
            connector.label = node["label"];
        }
        if (typeof node["state"] === "string") {
            connector.state = node["state"];
        }
        connectors.push(connector);
        return undefined;
    }
    const host = element("div", `pear-artifact-node pear-artifact-${String(type)}`);
    if (typeof node["id"] === "string") {
        host.dataset["pearArtifactNode"] = node["id"];
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
        host.addEventListener("click", activate);
        host.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activate();
            }
        });
    }
    switch (type) {
        case "group": {
            host.dataset["layout"] = String(node["layout"]);
            if (typeof node["columns"] === "number") {
                host.style.setProperty("--pear-artifact-columns", String(node["columns"]));
            }
            if (typeof node["align"] === "string") {
                host.dataset["align"] = node["align"];
            }
            const children = node["children"];
            if (Array.isArray(children)) {
                for (const child of children) {
                    if (is_json_object(child)) {
                        const rendered = render_scene_node(child, data, connectors);
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
            host.textContent = scalar_text(value);

            break;
        }
        case "shape": {
            host.dataset["shape"] = scalar_text(node["shape"]);
            if (typeof node["label"] === "string") {
                host.append(element("span", "pear-artifact-shape-label", node["label"]));
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
            path.setAttribute("class", "pear-artifact-path-line");
            svg.append(path);
            host.append(svg);

            break;
        }
        case "control": {
            const control = scalar_text(node["control"]);
            const label = scalar_text(node["label"]);
            const action = scalar_text(node["action"]);
            if (control === "toggle") {
                const button = element("button", "pear-artifact-control-button", label);
                button.type = "button";
                if (action !== "") {
                    button.dataset["action"] = action;
                }
                button.setAttribute("aria-pressed", String(Boolean(node["value"])));
                button.addEventListener("click", () => {
                    button.setAttribute(
                        "aria-pressed",
                        String(button.getAttribute("aria-pressed") !== "true"),
                    );
                });
                host.append(button);
            } else if (control === "range") {
                const label_node = element("label", "pear-artifact-control-label", label);
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
                const button = element("button", "pear-artifact-control-button", render_label());
                button.type = "button";
                if (action !== "") {
                    button.dataset["action"] = action;
                }
                button.addEventListener("click", () => {
                    if (control === "stepper") {
                        const step = Number(node["step"] ?? 1);
                        const maximum = Number(node["max"] ?? Infinity);
                        value = Math.min(maximum, value + step);
                        button.textContent = render_label();
                    } else {
                        button.dataset["activated"] = "true";
                    }
                });
                host.append(button);
            }

            break;
        }
        // No default
    }
    return host;
}

function mount_connectors(scene: HTMLElement, connectors: Connector[]): void {
    if (connectors.length === 0) {
        return;
    }
    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("class", "pear-artifact-connectors");
    overlay.setAttribute("aria-hidden", "true");
    const marker_id = `pear-artifact-arrow-${connector_instance}`;
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
    arrow.setAttribute("class", "pear-artifact-connector-arrow");
    marker.append(arrow);
    definitions.append(marker);
    overlay.append(definitions);
    scene.prepend(overlay);
    const draw = (): void => {
        overlay.replaceChildren(definitions);
        const bounds = scene.getBoundingClientRect();
        overlay.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
        for (const [index, connector] of connectors.entries()) {
            const from = scene.querySelector<HTMLElement>(
                `[data-pear-artifact-node="${CSS.escape(connector.from)}"]`,
            );
            const to = scene.querySelector<HTMLElement>(
                `[data-pear-artifact-node="${CSS.escape(connector.to)}"]`,
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
            line.style.setProperty("--pear-artifact-order", String(index));
            if (connector.state !== undefined) {
                line.dataset["state"] = connector.state;
            }
            line.setAttribute(
                "class",
                connector.directed
                    ? "pear-artifact-connector is-directed"
                    : "pear-artifact-connector",
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
                label.setAttribute("class", "pear-artifact-connector-label");
                label.setAttribute("text-anchor", "middle");
                label.textContent = connector.label;
                overlay.append(label);
            }
        }
    };
    let draw_scheduled = false;
    const schedule_draw = (): void => {
        if (draw_scheduled) {
            return;
        }
        draw_scheduled = true;
        requestAnimationFrame(() => {
            draw_scheduled = false;
            draw();
        });
    };
    schedule_draw();
    if (typeof ResizeObserver !== "undefined") {
        new ResizeObserver(schedule_draw).observe(scene);
    }
}

function render_scene(block: PearArtifactBlock, data: JsonObject): HTMLElement {
    const scene = element("div", "pear-artifact-scene");
    scene.dataset["aspect"] = scalar_text(block["aspect"] ?? "wide");
    scene.setAttribute("role", "img");
    scene.setAttribute("aria-label", scalar_text(block["label"]));
    const connectors: Connector[] = [];
    const raw_root = block["root"];
    const root = is_json_object(raw_root)
        ? render_scene_node(raw_root, data, connectors)
        : undefined;
    if (root !== undefined) {
        scene.append(root);
    }
    for (const graph of scene.querySelectorAll<HTMLElement>(
        '.pear-artifact-group[data-layout="graph"]',
    )) {
        layout_graph_group(graph, connectors);
    }
    mount_connectors(scene, connectors);
    return scene;
}

function render_block(block: PearArtifactBlock, data: JsonObject): HTMLElement {
    const host = element("section", `pear-artifact-block pear-artifact-block-${block.type}`);
    switch (block.type) {
        case "copy": {
            if (typeof block["eyebrow"] === "string") {
                host.append(element("div", "pear-artifact-eyebrow", block["eyebrow"]));
            }
            if (typeof block["heading"] === "string") {
                host.append(element("h3", "pear-artifact-heading", block["heading"]));
            }
            host.append(element("p", "pear-artifact-copy", scalar_text(block["body"])));

            break;
        }
        case "scene": {
            host.append(render_scene(block, data));

            break;
        }
        case "metric": {
            host.append(element("div", "pear-artifact-label", scalar_text(block["label"])));
            const value = element(
                "div",
                "pear-artifact-metric",
                scalar_text(data_value(data, block["bind"]) ?? block["value"]),
            );
            if (block["unit"]) {
                value.append(element("span", "pear-artifact-unit", scalar_text(block["unit"])));
            }
            host.append(value);
            if (typeof block["delta"] === "number") {
                const delta = block["delta"];
                const delta_node = element(
                    "div",
                    "pear-artifact-delta",
                    `${delta > 0 ? "+" : ""}${delta}`,
                );
                delta_node.dataset["direction"] = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
                host.append(delta_node);
            }

            break;
        }
        case "meter": {
            host.append(element("div", "pear-artifact-label", scalar_text(block["label"])));
            const total =
                typeof block["total"] === "number" && block["total"] > 0 ? block["total"] : 100;
            const raw_current = data_value(data, block["bind"]) ?? block["value"];
            const numeric_current = Number(raw_current);
            const current = Number.isFinite(numeric_current)
                ? numeric_current
                : Number(block["value"]);
            const percent = Math.max(0, Math.min(100, (current / total) * 100));
            const track = element("div", "pear-artifact-meter");
            const fill = element("span", "pear-artifact-meter-fill");
            fill.style.setProperty("--pear-artifact-progress", String(percent / 100));
            track.append(fill);
            host.append(track);
            const unit = scalar_text(block["unit"]);
            host.append(
                element(
                    "div",
                    "pear-artifact-meter-meta",
                    `${current} / ${total}${unit === "" ? "" : ` ${unit}`}`,
                ),
            );
            if (block["detail"]) {
                host.append(element("div", "pear-artifact-detail", scalar_text(block["detail"])));
            }

            break;
        }
        case "series": {
            const label = element("div", "pear-artifact-label", scalar_text(block["label"]));
            if (block["unit"]) {
                label.append(
                    element("span", "pear-artifact-series-unit", scalar_text(block["unit"])),
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
            host.append(element("div", "pear-artifact-label", scalar_text(block["label"])));
            const pre = element("pre", "pear-artifact-log");
            const lines = data_value(data, block["bind"]);
            const rendered_lines = Array.isArray(lines)
                ? lines
                : Array.isArray(block["lines"])
                  ? block["lines"]
                  : [];
            pre.textContent = rendered_lines.map((line) => scalar_text(line)).join("\n");
            host.append(pre);

            break;
        }
        case "key_value": {
            host.append(element("div", "pear-artifact-label", scalar_text(block["label"])));
            const list = element("dl", "pear-artifact-key-values");
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

function authored_node_count(document_value: PearArtifactDocument): number {
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

export function render_pear_artifact(
    document_value: PearArtifactDocument,
    options: PearArtifactRenderOptions,
): HTMLElement {
    const render_started = globalThis.performance?.now() ?? Date.now();
    const article = element("article", "pear-artifact");
    article.dataset["density"] = options.density;
    article.dataset["lifecycle"] = "ready";
    if (document_value.title !== undefined) {
        const header = element("header", "pear-artifact-header");
        header.append(
            element("span", "pear-artifact-kind", "explainer"),
            element("h2", "pear-artifact-title", document_value.title),
        );
        article.append(header);
    }
    const body = element("div", "pear-artifact-body");
    const data = document_value.data ?? {};
    body.append(...document_value.blocks.map((block) => render_block(block, data)));
    article.append(body);
    const document_actions = document_value.actions ?? [];
    if (document_actions.length > 0 || options.onPin) {
        const actions = element("footer", "pear-artifact-actions");
        for (const action of document_actions) {
            const button = element("button", "pear-artifact-action", action.label);
            button.type = "button";
            button.addEventListener("click", () => options.onAction?.(action, button));
            actions.append(button);
        }
        if (options.onPin) {
            const spacer = element("span", "pear-artifact-action-spacer");
            const pin = element("button", "pear-artifact-pin", "↗ pin to wall");
            pin.type = "button";
            pin.addEventListener("click", () => options.onPin?.(pin));
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

export function mount_pear_artifact(
    $host: JQuery,
    document_value: PearArtifactDocument,
    options: PearArtifactRenderOptions,
): HTMLElement {
    const artifact = render_pear_artifact(document_value, options);
    $host.empty();
    $host[0]?.append(artifact);
    return artifact;
}
