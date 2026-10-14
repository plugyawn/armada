// Controls: what a person presses. Every one has a name, takes the hover
// layer or is its own fill, and is at least 24 px in each direction.

import type {GlyphName} from "./icons/names.ts";
import {cell, glyph} from "./icons.ts";
import {rove_item, roving, stop_of} from "./keys.ts";
import {n, on_press_down} from "./tree.ts";
import type {Force, Handler, Tree} from "./tree.ts";

export type Common = {
    on_press?: Handler | undefined;
    disabled?: boolean | undefined;
    // The tooltip's words and its key.
    tip?: string | undefined;
    key_hint?: string | undefined;
    force?: Force | undefined;
};

function common(props: Common): Record<string, string | boolean | undefined> {
    return {
        type: "button",
        disabled: props.disabled,
        "data-tip": props.tip,
        "data-key": props.key_hint,
        "data-force": props.force,
    };
}

function pressed(value: boolean | undefined): string | undefined {
    return value === undefined ? undefined : String(value);
}

// Glyph control: 28 square (24 when small), the glyph in the second ink.
export function glyph_control(
    props: Common & {
        glyph: GlyphName;
        label: string;
        pressed?: boolean | undefined;
        expanded?: boolean | undefined;
        small?: boolean | undefined;
    },
): Tree {
    return n(
        "button",
        {
            ...common(props),
            class: "armada-control armada-hover",
            "data-shape": "square",
            "data-hover": "square",
            "data-size": props.small ? "small" : undefined,
            "aria-label": props.label,
            "aria-pressed": pressed(props.pressed),
            "aria-expanded": pressed(props.expanded),
        },
        [glyph(props.glyph)],
        {on: {click: props.on_press}},
    );
}

// Text control: a verb in Strong, always, so it is never mistaken for the
// sentence beside it.
export function text_control(
    props: Common & {label: string; pressed?: boolean | undefined; glyph?: GlyphName | undefined},
): Tree {
    return n(
        "button",
        {
            ...common(props),
            class: "armada-text-control armada-hover",
            "data-hover": "wide",
            "aria-pressed": pressed(props.pressed),
        },
        [props.glyph === undefined ? undefined : glyph(props.glyph), n("span", {}, [props.label])],
        {on: {click: props.on_press}},
    );
}

// The default button where there is a field: the verb Return performs now.
// As a word it stands in a cell as wide as the longest verb; as a glyph it
// is the same small bubble, 32 square. `state` is for a still: in a field
// the stylesheet reads it from the field's words and focus.
export type SendProps = Common & {
    verb: string;
    verbs: string[];
    form?: "word" | "glyph" | undefined;
    glyph?: GlyphName | undefined;
    state?: "armed" | "waiting" | undefined;
};

export function send_button(props: SendProps): Tree {
    const as_glyph = props.form === "glyph" && props.glyph !== undefined;
    return n(
        "button",
        {
            ...common({...props, tip: props.tip ?? props.verb}),
            class: "armada-send armada-dip",
            "data-form": as_glyph ? "glyph" : "word",
            "data-state": props.state,
            "aria-label": props.verb,
        },
        [as_glyph ? glyph(props.glyph!) : cell(props.verb, props.verbs)],
        {on: {click: props.on_press}},
    );
}

// The default button where there is no field: a word on the accent.
export function accent_button(
    props: Common & {label: string; sizers?: string[] | undefined},
): Tree {
    return n(
        "button",
        {...common(props), class: "armada-accent"},
        [cell(props.label, props.sizers ?? [])],
        {on: {click: props.on_press}},
    );
}

export function button(props: Common & {label: string; kind?: "danger" | undefined}): Tree {
    return n(
        "button",
        {...common(props), class: "armada-button", "data-kind": props.kind},
        [n("span", {}, [props.label])],
        {on: {click: props.on_press}},
    );
}

export type Choice = {
    label: string;
    selected: boolean;
    on_press?: Handler | undefined;
    force?: Force | undefined;
};

// The selected choice is the group's one Tab stop; the arrows move
// between the choices and Return or Space chooses (keys.ts).
const choice_stop = (choices: Choice[]): number =>
    stop_of(choices.map((choice) => ({current: choice.selected})));

// A pane's switch: segments with one thumb.
export function segments(props: {label: string; options: Choice[]}): Tree {
    const keys = roving("horizontal");
    const stop = choice_stop(props.options);
    return n(
        "div",
        {
            class: "armada-segments",
            role: "group",
            "aria-label": props.label,
            ...keys.attributes,
        },
        props.options.map((option, index) =>
            n(
                "button",
                {
                    type: "button",
                    class: "armada-segment armada-hover",
                    "data-hover": "wide",
                    "data-force": option.force,
                    ...rove_item(index === stop),
                    "aria-pressed": String(option.selected),
                },
                [n("span", {}, [option.label])],
                {on: on_press_down(option.on_press)},
            ),
        ),
        {on: keys.on},
    );
}

// The views of a place. A view is chosen, not done: a selector is Text.
export function views(props: {
    label: string;
    views: (Choice & {count?: number | undefined})[];
}): Tree {
    const keys = roving("horizontal");
    const stop = choice_stop(props.views);
    return n(
        "div",
        {class: "armada-views", role: "group", "aria-label": props.label, ...keys.attributes},
        props.views.map((view, index) =>
            n(
                "button",
                {
                    type: "button",
                    class: "armada-view armada-hover",
                    "data-hover": "wide",
                    "data-force": view.force,
                    ...rove_item(index === stop),
                    "aria-current": view.selected ? "page" : undefined,
                },
                [
                    n("span", {}, [view.label]),
                    view.count === undefined
                        ? undefined
                        : n("span", {class: "armada-view-count"}, [view.count]),
                ],
                {on: on_press_down(view.on_press)},
            ),
        ),
        {on: keys.on},
    );
}

export function toggle(props: Common & {label: string; on: boolean}): Tree {
    return n(
        "button",
        {
            ...common(props),
            class: "armada-switch",
            role: "switch",
            "aria-checked": String(props.on),
            "aria-label": props.label,
        },
        [],
        {on: {click: props.on_press}},
    );
}

export function checkbox(props: Common & {label: string; checked: boolean}): Tree {
    return n(
        "button",
        {
            ...common(props),
            class: "armada-checkbox",
            role: "checkbox",
            "aria-checked": String(props.checked),
            "aria-label": props.label,
        },
        [glyph("check", {size: 12})],
        {on: {click: props.on_press}},
    );
}

// Stop, and Jump to latest: a glyph and a word on a resting fill.
export function stop_control(
    props: Common & {
        label: string;
        glyph: GlyphName;
        role?: "jump" | undefined;
        // Its cell is kept while it does not show.
        off?: boolean | undefined;
    },
): Tree {
    return n(
        "button",
        {
            ...common(props),
            class: "armada-stop armada-hover",
            "data-hover": "wide",
            "data-role": props.role,
            "data-off": props.off,
            tabindex: props.off ? -1 : undefined,
            "aria-hidden": props.off ? "true" : undefined,
        },
        [glyph(props.glyph), n("span", {}, [props.label])],
        {on: {click: props.on_press}},
    );
}

// A question's option: a button with an edge and its number. 1 to 9 answer
// while the field is empty.
export function option(
    props: Common & {number: number; label: string; selected?: boolean | undefined},
): Tree {
    return n(
        "button",
        {
            ...common(props),
            class: "armada-option armada-hover",
            "data-hover": "wide",
            "aria-pressed": pressed(props.selected),
        },
        [n("span", {class: "armada-option-number"}, [props.number]), n("span", {}, [props.label])],
        {on: {click: props.on_press}},
    );
}
