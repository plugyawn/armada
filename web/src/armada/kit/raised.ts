// Raised things: menu, picker, tooltip, dialog, palette, sheet. Opaque,
// with the one shadow and a 1 px edge. Each opens in the top layer
// (keys.ts: open_raised); `still` draws one in place instead, as the
// showroom shows it.

import {button} from "./controls.ts";
import type {Common} from "./controls.ts";
import {glyph} from "./icons.ts";
import {close_on_choice, palette_keys, rove_item, roving, stop_of} from "./keys.ts";
import {n} from "./tree.ts";
import type {Child, Tree} from "./tree.ts";

type Still = {still?: boolean | undefined};

// A raised surface waits, closed, in the top layer's popover state.
const popover = (props: Still): "auto" | undefined => (props.still ? undefined : "auto");

type Row = Exclude<MenuItem, "gap">;
const rows_of = (items: MenuItem[]): Row[] => items.filter((item) => item !== "gap");

// The group's Tab stop among its rows: the current value's, else the first.
function stop_row(items: MenuItem[]): Row | undefined {
    const rows = rows_of(items);
    return rows[
        stop_of(rows.map((row) => ({current: row.current ?? false, disabled: row.disabled})))
    ];
}

export type MenuItem =
    | "gap"
    | (Common & {
          label: string;
          // A caption beside the label: a model's id.
          caption?: string | undefined;
          // Its key, or the place it leads to, in the second ink.
          key_hint?: string | undefined;
          // The current value carries a tick.
          current?: boolean | undefined;
          // The highlighted row is a neutral fill.
          highlighted?: boolean | undefined;
          // For a palette's row, which its input names as the active one.
          id?: string | undefined;
      });

// `stop`: the row the keyboard rests on in a group of rows; a palette's
// rows are never a Tab stop (focus stays in its input).
function menu_item(item: MenuItem, role: string, stop: Row | undefined | "none"): Tree {
    if (item === "gap") {
        return n("div", {class: "armada-menu-gap", role: "separator"});
    }
    return n(
        "button",
        {
            type: "button",
            class: "armada-menu-item armada-hover",
            role,
            id: item.id,
            ...(stop === "none" ? {tabindex: -1} : rove_item(item === stop)),
            disabled: item.disabled,
            "data-highlighted": item.highlighted,
            "data-force": item.force,
            "aria-checked": item.current === undefined ? undefined : String(item.current),
        },
        [
            n("span", {}, [item.label]),
            item.caption === undefined
                ? undefined
                : n("small", {class: "armada-menu-caption"}, [item.caption]),
            item.key_hint === undefined && !item.current
                ? undefined
                : n("span", {class: "armada-menu-key"}, [
                      item.key_hint,
                      item.current ? glyph("check") : undefined,
                  ]),
        ],
        {on: {click: item.on_press}},
    );
}

export function menu(props: Still & {label: string; items: MenuItem[]}): Tree {
    const keys = roving("vertical");
    const stop = stop_row(props.items);
    return n(
        "div",
        {
            class: "armada-raised armada-menu",
            role: "menu",
            "aria-label": props.label,
            popover: popover(props),
            ...keys.attributes,
        },
        props.items.map((item) => menu_item(item, "menuitem", stop)),
        {on: {...keys.on, click: close_on_choice}},
    );
}

// The model and effort picker: the models with their ids and a tick on the
// current one, the effort words, one caption of context use. A press on a
// model or an effort commits at once.
export function picker(
    props: Still & {
        label: string;
        models: MenuItem[];
        efforts: {label: string; steps: (Common & {label: string; selected: boolean})[]};
        caption: string;
    },
): Tree {
    const models = roving("vertical");
    const efforts = roving("horizontal");
    const stop = stop_row(props.models);
    const step_stop = stop_of(
        props.efforts.steps.map((step) => ({current: step.selected, disabled: step.disabled})),
    );
    return n(
        "div",
        {
            class: "armada-raised armada-picker",
            role: "dialog",
            "aria-label": props.label,
            popover: popover(props),
            ...models.attributes,
        },
        [
            props.models.map((item) => menu_item(item, "menuitemradio", stop)),
            n(
                "div",
                {
                    class: "armada-picker-steps",
                    role: "group",
                    "aria-label": props.efforts.label,
                    ...efforts.attributes,
                },
                props.efforts.steps.map((step, index) =>
                    n(
                        "button",
                        {
                            type: "button",
                            class: "armada-picker-step armada-hover",
                            "data-hover": "wide",
                            ...rove_item(index === step_stop),
                            disabled: step.disabled,
                            "aria-pressed": String(step.selected),
                        },
                        [n("span", {}, [step.label])],
                        {on: {click: step.on_press}},
                    ),
                ),
                {on: efforts.on},
            ),
            n("span", {class: "armada-picker-caption"}, [props.caption]),
        ],
        {on: models.on},
    );
}

// A tooltip as a still; the live one is the window's one tooltip
// (motion/tip.ts).
export function tooltip(props: {text: string; key_hint?: string | undefined}): Tree {
    return n("div", {class: "armada-tip", role: "tooltip"}, [
        n("span", {}, [props.text]),
        props.key_hint === undefined
            ? undefined
            : n("kbd", {class: "armada-tip-key"}, [props.key_hint]),
    ]);
}

// A dialog: for what cannot be undone. `modal` draws it on the platform's
// <dialog>, which the caller opens with showModal(); without it, it is a
// still.
export function dialog(props: {
    title: string;
    body: string;
    buttons: (Common & {label: string; kind?: "danger" | undefined})[];
    modal?: boolean | undefined;
}): Tree {
    return n(
        props.modal ? "dialog" : "div",
        {
            class: "armada-raised armada-dialog",
            role: props.modal ? undefined : "dialog",
            "aria-label": props.title,
        },
        [
            n("p", {class: "armada-dialog-title"}, [props.title]),
            n("p", {class: "armada-dialog-body"}, [props.body]),
            n(
                "div",
                {class: "armada-dialog-buttons"},
                props.buttons.map((item) => button(item)),
            ),
        ],
    );
}

// The palette: a line of input and the rows it found.
// Focus stays in the input; the arrows move the highlighted row and
// Return presses it (keys.ts).
export function palette(props: Still & {label: string; input: Child; items: MenuItem[]}): Tree {
    return n(
        "div",
        {
            class: "armada-raised armada-palette",
            role: "dialog",
            "aria-label": props.label,
            popover: popover(props),
        },
        [
            n("div", {class: "armada-palette-input"}, [glyph("search"), props.input]),
            n(
                "div",
                {class: "armada-palette-rows", role: "listbox", "aria-label": props.label},
                props.items.map((item) => menu_item(item, "option", "none")),
            ),
        ],
        {on: {keydown: palette_keys}},
    );
}

// A sheet: what the column holds, below the width at which it stands.
export function sheet(props: Still & {label: string; children: Child[]}): Tree {
    return n(
        "div",
        {
            class: "armada-raised armada-sheet",
            role: "dialog",
            "aria-label": props.label,
            popover: popover(props),
        },
        props.children,
    );
}
