// The kit as Preact components, for chrome: each is the same description
// dom.ts builds for the feed, turned into Preact's nodes.

import {h} from "preact";
import type {JSX} from "preact";

import {flatten} from "./tree.ts";
import type {Tree} from "./tree.ts";

// Attributes Preact sets as properties, where "" would read as false.
const BOOLEAN_PROPERTIES = new Set(["disabled", "hidden", "inert", "open"]);

export function to_vnode(tree: Tree): JSX.Element {
    const props: Record<string, unknown> = {};
    for (const [name, value] of Object.entries(tree.attributes)) {
        props[name] =
            value === "" && BOOLEAN_PROPERTIES.has(name) ? true : value;
    }
    for (const [type, handler] of Object.entries(tree.on)) {
        props[`on${type}`] = handler;
    }
    if (tree.ref !== undefined) {
        const {ref} = tree;
        props["ref"] = (element: Element | null) => {
            if (element !== null) {
                ref(element);
            }
        };
    }
    const children = flatten(tree.children).map((child) =>
        typeof child === "string" ? child : to_vnode(child),
    );
    return h(tree.tag, props, ...children);
}

// A recipe as a component: <Bubble content={…} />.
export function component<Props>(
    recipe: (props: Props) => Tree,
): (props: Props) => JSX.Element {
    return (props) => to_vnode(recipe(props));
}

// A description drawn in place, for what takes more than one argument.
export function View({tree}: {tree: Tree}): JSX.Element {
    return to_vnode(tree);
}
