// The kit's components are drawn once, as plain descriptions of elements.
// `build` (dom.ts) turns one into elements for the feed, and view.tsx turns
// the same one into Preact nodes for chrome, so the two cannot drift.

export type Handler = (event: Event) => void;
export type AttributeValue = string | number | boolean | null | undefined;

export type Tree = {
    tag: string;
    attributes: Record<string, string | number>;
    on: Record<string, Handler>;
    ref: ((element: Element) => void) | undefined;
    children: Child[];
};

export type Child = Tree | string | number | false | null | undefined | Child[];

export type Extras = {
    on?: Record<string, Handler | undefined> | undefined;
    ref?: ((element: Element) => void) | undefined;
};

// `true` is an attribute with no value; `false`, `null` and `undefined` are
// no attribute. State that assistive technology reads ("aria-pressed") is
// passed as the strings "true" and "false".
export function n(
    tag: string,
    attributes: Record<string, AttributeValue> = {},
    children: Child[] = [],
    extras: Extras = {},
): Tree {
    const present: Record<string, string | number> = {};
    for (const [name, value] of Object.entries(attributes)) {
        if (value === true) {
            present[name] = "";
        } else if (value !== false && value !== null && value !== undefined) {
            present[name] = value;
        }
    }
    const on: Record<string, Handler> = {};
    for (const [type, handler] of Object.entries(extras.on ?? {})) {
        if (handler !== undefined) {
            on[type] = handler;
        }
    }
    return {tag, attributes: present, on, ref: extras.ref, children};
}

export function is_tree(child: Child): child is Tree {
    return typeof child === "object" && child !== null && !Array.isArray(child);
}

// Children in document order, with nothing that draws nothing.
export function flatten(children: Child[]): (Tree | string)[] {
    const flat: (Tree | string)[] = [];
    for (const child of children) {
        if (Array.isArray(child)) {
            flat.push(...flatten(child));
        } else if (is_tree(child)) {
            flat.push(child);
        } else if (typeof child === "string" || typeof child === "number") {
            flat.push(String(child));
        }
    }
    return flat;
}

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set(["svg", "use", "g", "path", "circle", "rect"]);

export function build(tree: Tree): Element {
    const element = SVG_TAGS.has(tree.tag)
        ? document.createElementNS(SVG_NAMESPACE, tree.tag)
        : document.createElement(tree.tag);
    for (const [name, value] of Object.entries(tree.attributes)) {
        if (name === "style" && (element instanceof HTMLElement || element instanceof SVGElement)) {
            // As Preact writes it, so that both idioms serialise alike.
            element.style.cssText = String(value);
        } else {
            element.setAttribute(name, String(value));
        }
    }
    for (const [type, handler] of Object.entries(tree.on)) {
        element.addEventListener(type, handler);
    }
    for (const child of flatten(tree.children)) {
        element.append(typeof child === "string" ? document.createTextNode(child) : build(child));
    }
    tree.ref?.(element);
    return element;
}

// What the feed and the showroom keep is an HTML element; the sprite's
// drawings are built inside one.
export function build_html(tree: Tree): HTMLElement {
    const element = build(tree);
    if (!(element instanceof HTMLElement)) {
        throw new TypeError(`<${tree.tag}> is not an HTML element`);
    }
    return element;
}

// A still of a state, for the showroom and for tests of the stylesheet:
// the component drawn as it is under the pointer, pressed, or with the
// keyboard's ring.
export type Force = "hover" | "press" | "focus";

export function forced(tree: Tree, force: Force | undefined): Tree {
    return force === undefined
        ? tree
        : {...tree, attributes: {...tree.attributes, "data-force": force}};
}

// Views and places answer on the press, with the primary button only, and
// on Return and Space from the keyboard.
export function on_press_down(handler: Handler | undefined): Record<string, Handler> {
    if (handler === undefined) {
        return {};
    }
    return {
        pointerdown(event) {
            if (event instanceof MouseEvent && event.button === 0) {
                handler(event);
            }
        },
        keydown(event) {
            if (event instanceof KeyboardEvent && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                handler(event);
            }
        },
    };
}
