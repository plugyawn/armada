// In-place DOM patching for Pear surfaces.
//
// The Pear panel used to replace its entire subtree with
// `$el.html(...)` on every 125ms live tick, which killed in-flight
// clicks, focus, tooltips, and the side-request textarea draft.
// `patch_html` instead reconciles the rendered HTML into the live
// subtree, only touching nodes that actually changed, so stable
// elements keep their identity (and therefore their focus, CSS
// animations, tippy instances, and user-entered form state) across
// ticks.
//
// The reconciliation is intentionally simple: children are matched in
// order by a key of tag name + id (or the full class list when there
// is no id), which is enough for template output where conditional
// blocks appear and disappear between renders. It is not a general
// keyed-list differ; sibling reorderings degrade to remove+insert.

const ELEMENT_NODE = 1;

function is_element(node: Node): node is Element {
    return node.nodeType === ELEMENT_NODE;
}

// Tag-name based guards; instanceof checks would break in the node
// test environment, where the browser element classes are not global.
function is_input(element: Element): element is HTMLInputElement {
    return element.tagName === "INPUT";
}

function is_select(element: Element): element is HTMLSelectElement {
    return element.tagName === "SELECT";
}

function node_key(node: Node): string {
    if (is_element(node)) {
        const id = node.getAttribute("id");
        if (id !== null) {
            return `${node.tagName}#${id}`;
        }
        return `${node.tagName}.${node.getAttribute("class") ?? ""}`;
    }
    return `#node-type-${node.nodeType}`;
}

function sync_attributes(live: Element, next: Element): void {
    // Snapshot names before mutating the live attribute map.
    for (const name of live.getAttributeNames()) {
        if (!next.hasAttribute(name)) {
            live.removeAttribute(name);
        }
    }
    for (const name of next.getAttributeNames()) {
        const value = next.getAttribute(name)!;
        if (live.getAttribute(name) !== value) {
            live.setAttribute(name, value);
        }
    }
}

function is_focused(element: Element): boolean {
    return element.ownerDocument.activeElement === element;
}

function sync_form_state(live: Element, next: Element): void {
    if (live.tagName === "TEXTAREA") {
        // A textarea's value is user-owned draft state; never clobber
        // it from a poll tick.
        return;
    }
    if (is_focused(live)) {
        // Don't fight the user for a control they are interacting
        // with (e.g. an open select or a slider mid-drag).
        return;
    }
    if (is_input(live) && is_input(next)) {
        if (live.value !== next.value) {
            live.value = next.value;
        }
        if (live.checked !== next.checked) {
            live.checked = next.checked;
        }
    } else if (is_select(live) && is_select(next) && live.value !== next.value) {
        live.value = next.value;
    }
}

function patch_node(live: Node, next: Node): void {
    if (is_element(live) && is_element(next)) {
        sync_attributes(live, next);
        if (live.tagName !== "TEXTAREA") {
            patch_children(live, next);
        }
        sync_form_state(live, next);
        return;
    }
    if (live.nodeValue !== next.nodeValue) {
        live.nodeValue = next.nodeValue;
    }
}

function live_has_later_match(live: Element, start_index: number, key: string): boolean {
    for (let i = start_index; i < live.childNodes.length; i += 1) {
        if (node_key(live.childNodes[i]!) === key) {
            return true;
        }
    }
    return false;
}

function next_has_later_match(next_children: Node[], start_index: number, key: string): boolean {
    for (let i = start_index; i < next_children.length; i += 1) {
        if (node_key(next_children[i]!) === key) {
            return true;
        }
    }
    return false;
}

function patch_children(live: Element, next: Element): void {
    // Snapshot the new children up front; moving one into the live
    // tree mutates next.childNodes.
    const next_children = [...next.childNodes];
    for (const [i, next_child] of next_children.entries()) {
        const live_child = live.childNodes[i];
        if (live_child === undefined) {
            live.append(next_child);
            continue;
        }
        const next_key = node_key(next_child);
        if (node_key(live_child) === next_key) {
            patch_node(live_child, next_child);
        } else if (next_has_later_match(next_children, i + 1, node_key(live_child))) {
            // The live child is still wanted later in the new render,
            // so the new render inserted a node here; keep the live
            // child in place for a later iteration.
            live_child.before(next_child);
        } else if (live_has_later_match(live, i + 1, next_key)) {
            // The new render removed the nodes before the later
            // match; drop live children until the match lines up.
            while (node_key(live.childNodes[i]!) !== next_key) {
                live.childNodes[i]!.remove();
            }
            patch_node(live.childNodes[i]!, next_child);
        } else {
            // No relationship in either direction: replace outright.
            live_child.replaceWith(next_child);
        }
    }
    while (live.childNodes.length > next_children.length) {
        live.lastChild!.remove();
    }
}

export function patch_html(root: Element, html: string): void {
    const scratch = root.ownerDocument.createElement("div");
    scratch.innerHTML = html;
    patch_children(root, scratch);
}
