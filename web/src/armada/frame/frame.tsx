// Mounts what script draws into the frame's static cells (the template
// draws the cells themselves, in their final geometry, before any script):
// More, and the column for the place in front. It writes `data-place` and
// `data-view` on the plane in the task that changes them, and nothing
// else: no width, no breakpoint, no composition (frame.css computes those).

import "./frame.css";

import {render} from "preact";

import {$t} from "../../i18n.ts";
import * as legacy_column from "../bridge/legacy_column.ts";
import {GlyphControl} from "../kit/index.ts";
import {route} from "../state/route.ts";
import type {Place, Route} from "../state/route.ts";
import * as column from "../surfaces/column/index.tsx";

import {refresh_scroll_edge, watch_scroll_edge} from "./after_layout.ts";

function cell(name: string): HTMLElement {
    const element = document.querySelector<HTMLElement>(
        `[data-armada=${name}]`,
    );
    if (element === null) {
        throw new Error(`the frame has no ${name}`);
    }
    return element;
}

export function mount(): void {
    const plane = cell("plane");
    const header = cell("header");
    const pane = cell("pane");
    const column_host = cell("column");

    render(
        <GlyphControl
            glyph="more"
            label={$t({defaultMessage: "More"})}
            on_press={legacy_column.toggle}
        />,
        cell("more"),
    );

    let drawn_place: Place | undefined;
    const draw = ({place, view}: Route): void => {
        plane.dataset["place"] = place;
        plane.dataset["view"] = view;
        if (place !== drawn_place) {
            drawn_place = place;
            column.mount(column_host, place, legacy_column.open);
        }
        refresh_scroll_edge(pane, header);
    };
    draw(route.get());
    route.subscribe(draw);
    watch_scroll_edge(pane, header);
}
