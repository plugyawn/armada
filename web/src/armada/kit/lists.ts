// Lists: one grammar for every list in the column and in a sheet, the
// checklist, the sections of the column, and Home's rows.

import {text_control} from "./controls.ts";
import type {Common} from "./controls.ts";
import type {MarkName} from "./icons/names.ts";
import {figure, mark} from "./icons.ts";
import {n} from "./tree.ts";
import type {Child, Force, Tree} from "./tree.ts";

export type ListRowProps = {
    // The 20 px leading cell: a mark, a glyph, a face, the sigil.
    lead: Tree;
    label: Child;
    // Where it is: a second line under the label makes the row 44 high.
    second?: string | undefined;
    // One trailing thing: a duration, a count, a time, a verb.
    trailing?: Child;
    // First ink for what is live or needs me.
    live?: boolean | undefined;
};

export function list_row(props: ListRowProps): Tree {
    return n(
        "li",
        {
            class: "armada-list-row",
            "data-live": props.live,
            "data-lines": props.second === undefined ? undefined : 2,
        },
        [
            props.lead,
            n("span", {class: "armada-list-label"}, [
                props.label,
                props.second === undefined
                    ? undefined
                    : n("span", {class: "armada-list-sub"}, [props.second]),
            ]),
            n("span", {class: "armada-list-trailing"}, [props.trailing]),
        ],
    );
}

// A duration, a count or a time on a row's trailing edge. One that changes
// stands in fixed cells.
export function list_figure(value: string, options: {cells?: boolean | undefined} = {}): Tree {
    return n("span", {class: "armada-list-figure"}, [options.cells ? figure(value) : value]);
}

export type Step =
    | {
          kind: "step";
          state: MarkName;
          title: string;
          duration?: string | undefined;
      }
    | {
          // What the step in hand is doing, and what happened in it: at
          // the label's edge, its mark beside its words, at most one verb.
          kind: "event";
          words: Child;
          mark?: MarkName | undefined;
          // New since I looked.
          fresh?: boolean | undefined;
          verb?: (Common & {label: string}) | undefined;
      };

function step_row(step: Step): Tree {
    if (step.kind === "step") {
        return n("li", {class: "armada-step", "data-state": step.state}, [
            mark(step.state),
            n("span", {class: "armada-list-label"}, [step.title]),
            n("span", {class: "armada-list-trailing"}, [
                step.duration === undefined ? undefined : list_figure(step.duration, {cells: true}),
            ]),
        ]);
    }
    return n("li", {class: "armada-step", "data-state": "event", "data-fresh": step.fresh}, [
        n("i"),
        n("span", {class: "armada-list-label"}, [
            step.mark === undefined ? undefined : mark(step.mark),
            n("span", {}, [step.words]),
        ]),
        n("span", {class: "armada-list-trailing"}, [
            step.verb === undefined ? undefined : text_control(step.verb),
        ]),
    ]);
}

// Which rows a list of `slots` rows draws, and how many it only counts.
// The checklist folds its earliest rows so the live step stays in sight;
// every other list keeps its head.
export function fit<Row>(
    rows: Row[],
    slots: number | undefined,
    keep: "head" | "tail",
): {shown: Row[]; hidden: number} {
    if (slots === undefined || rows.length <= slots) {
        return {shown: rows, hidden: 0};
    }
    const shown = keep === "head" ? rows.slice(0, slots) : rows.slice(rows.length - slots);
    return {shown, hidden: rows.length - slots};
}

// The quiet caption in the gap under a list: "2 more", "1 earlier".
function more(props: (Common & {label: string}) | undefined, hidden: number): Tree | undefined {
    if (props === undefined || hidden === 0) {
        return undefined;
    }
    return n(
        "button",
        {
            type: "button",
            class: "armada-more armada-hover",
            "data-hover": "wide",
            "data-force": props.force,
        },
        [n("span", {}, [props.label])],
        {on: {click: props.on_press}},
    );
}

export type ChecklistProps = {
    label: string;
    steps: Step[];
    // Seven in the column: a step that starts or ends moves no row.
    slots?: number | undefined;
    // Names the rows that have no slot ("1 earlier"); pressing it opens
    // them.
    more?: (Common & {label: string}) | undefined;
};

export function checklist(props: ChecklistProps): Tree {
    const {shown, hidden} = fit(props.steps, props.slots, "tail");
    return n("div", {class: "armada-checklist-host"}, [
        n(
            "ol",
            {
                class: "armada-checklist",
                "aria-label": props.label,
                "data-slots": props.slots !== undefined,
                style: props.slots === undefined ? undefined : `--slots:${props.slots}`,
            },
            shown.map((step) => step_row(step)),
        ),
        more(props.more, hidden),
    ]);
}

export type SectionProps = {
    heading: string;
    // At most one trailing thing: totals and one verb.
    trailing?: Child;
    rows: ListRowProps[];
    // One sentence when it holds nothing; the heading stays.
    empty?: string | undefined;
    slots?: number | undefined;
    more?: (Common & {label: string}) | undefined;
    // The last section stands on the column's foot, level with the field.
    foot?: boolean | undefined;
    // The section that holds a history takes the room that is left.
    grow?: boolean | undefined;
};

export function section(props: SectionProps): Tree {
    const {shown, hidden} = fit(props.rows, props.slots, "head");
    return n(
        "section",
        {class: "armada-section", "data-foot": props.foot, "data-grow": props.grow},
        [
            n("div", {class: "armada-section-heading"}, [
                n("span", {}, [props.heading]),
                props.trailing === undefined
                    ? undefined
                    : n("span", {class: "armada-section-trailing"}, [props.trailing]),
            ]),
            props.rows.length === 0 && props.empty !== undefined
                ? n("p", {class: "armada-empty"}, [props.empty])
                : n(
                      "ul",
                      {
                          class: "armada-list",
                          "data-slots": props.slots !== undefined,
                          style: props.slots === undefined ? undefined : `--slots:${props.slots}`,
                      },
                      shown.map((row) => list_row(row)),
                  ),
            more(props.more, hidden),
        ],
    );
}

// The column: its first heading, then its sections, 20 apart.
export function column(props: {
    heading: Tree;
    folded?: boolean | undefined;
    children: Child[];
}): Tree {
    return n("aside", {class: "armada-column", "data-folded": props.folded}, [
        n("div", {class: "armada-column-heading"}, [props.heading]),
        n("div", {class: "armada-column-body"}, props.children),
    ]);
}

export function empty(sentence: string): Tree {
    return n("p", {class: "armada-empty"}, [sentence]);
}

// ---- Home

export type HomeRowProps = {
    // The 28 px gutter object: a mark, a face, the sigil.
    lead: Tree;
    sentence: string;
    place: string;
    // The verb on a row that needs me; the time on the rest.
    verb?: (Common & {label: string}) | undefined;
    time?: string | undefined;
    selected?: boolean | undefined;
    force?: Force | undefined;
};

export function home_row(props: HomeRowProps): Tree {
    return n(
        "div",
        {
            class: "armada-home-row armada-hover",
            role: "listitem",
            "data-needs": props.verb !== undefined,
            "data-force": props.force,
            "aria-selected": props.selected ? "true" : undefined,
        },
        [
            n("span", {class: "armada-home-gutter"}, [props.lead]),
            n("div", {class: "armada-home-words"}, [
                n("p", {class: "armada-home-sentence"}, [props.sentence]),
                n("span", {class: "armada-home-place"}, [props.place]),
            ]),
            props.verb === undefined
                ? n("time", {class: "armada-home-time"}, [props.time])
                : n("span", {class: "armada-home-verb"}, [text_control(props.verb)]),
        ],
    );
}

export function home_heading(label: string): Tree {
    return n("h2", {class: "armada-home-heading"}, [label]);
}
