// The column: one host beside the conversation on every place, from the
// header's second line to the field's foot (SPEC-ENGINEERING section 2).
// Its geometry is the frame's (frame/frame.css); this file says what it
// holds. B4 builds the checklist and the borrowing, B8 the lists; until
// then a place's column is its headings, with no rows.

import "./column.css";

import {render} from "preact";

import {$t} from "../../../i18n.ts";
import {View, recipes} from "../../kit/index.ts";
import type {Place} from "../../state/route.ts";

export const name = "column";

export type SectionId =
    | "checklist"
    | "changes"
    | "runs"
    | "people"
    | "threads"
    | "shared"
    | "together"
    | "running"
    | "pull-requests";

// The first registry: the sections of each place, top to bottom
// (SPEC-DESIGN section 2, "The column"). The first one's heading is the
// column's heading, on the header's second line.
export const SECTIONS: Record<Place, readonly SectionId[]> = {
    worktree: ["checklist", "changes", "runs", "people"],
    channel: ["threads", "shared", "people"],
    dm: ["together", "shared", "people"],
    home: ["running", "pull-requests", "people"],
};

// The second registry: what may borrow the column, one thing at a time,
// in place of the sections (a step's trace, a past run's checklist, an
// opened artifact or monitor, a thread opened beside). B4 fills it.
export type Borrower = {id: string; label: () => string};
export const BORROWERS = new Map<string, Borrower>();

const HEADINGS: Record<SectionId, () => string> = {
    checklist: () => $t({defaultMessage: "Checklist"}),
    changes: () => $t({defaultMessage: "Changes"}),
    runs: () => $t({defaultMessage: "Runs"}),
    people: () => $t({defaultMessage: "People"}),
    threads: () => $t({defaultMessage: "Threads"}),
    shared: () => $t({defaultMessage: "Shared"}),
    together: () => $t({defaultMessage: "Together"}),
    running: () => $t({defaultMessage: "Running"}),
    "pull-requests": () => $t({defaultMessage: "Pull requests"}),
};

export function heading_of(id: SectionId): string {
    return HEADINGS[id]();
}

// Draws the place's column into the frame's host. `open` is what a press
// on its heading does while the column cannot show its lists (folded, or
// with no rows yet): the frame opens the old rail in a sheet.
export function mount(host: Element, place: Place, open: () => void): void {
    const [first, ...rest] = SECTIONS[place];
    const tree = recipes.column({
        heading: recipes.heading({word: heading_of(first!), on_press: open}),
        children: rest.map((id, index) =>
            recipes.section({
                heading: heading_of(id),
                rows: [],
                foot: index === rest.length - 1,
            }),
        ),
    });
    render(<View tree={tree} />, host);
}
