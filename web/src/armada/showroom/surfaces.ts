// Each surface's own section of the showroom, from its showroom.tsx (the
// frame's seams, PLAN.md rule 3). A surface with no specimens yet has no
// section.

import {specimens as artifacts} from "../surfaces/artifacts/showroom.tsx";
import {specimens as band} from "../surfaces/band/showroom.tsx";
import {specimens as changes} from "../surfaces/changes/showroom.tsx";
import {specimens as checklist} from "../surfaces/checklist/showroom.tsx";
import {specimens as column} from "../surfaces/column/showroom.tsx";
import {specimens as composer} from "../surfaces/composer/showroom.tsx";
import {specimens as dialogs} from "../surfaces/dialogs/showroom.tsx";
import {specimens as emoji} from "../surfaces/emoji/showroom.tsx";
import {specimens as files} from "../surfaces/files/showroom.tsx";
import {specimens as header} from "../surfaces/header/showroom.tsx";
import {specimens as home} from "../surfaces/home/showroom.tsx";
import {specimens as lists} from "../surfaces/lists/showroom.tsx";
import {specimens as nav} from "../surfaces/nav/showroom.tsx";
import {specimens as palette} from "../surfaces/palette/showroom.tsx";
import {specimens as readme} from "../surfaces/readme/showroom.tsx";
import {specimens as settings} from "../surfaces/settings/showroom.tsx";
import {specimens as trace} from "../surfaces/trace/showroom.tsx";

import type {Section} from "./fixtures.ts";

const SURFACES = {
    nav,
    header,
    band,
    composer,
    column,
    checklist,
    lists,
    trace,
    home,
    readme,
    files,
    changes,
    artifacts,
    emoji,
    palette,
    dialogs,
    settings,
};

export function surface_sections(): Section[] {
    return Object.entries(SURFACES)
        .filter(([, specimens]) => specimens.length > 0)
        .map(([id, specimens]) => ({
            id: `surface-${id}`,
            title: id,
            columns: 1,
            items: specimens.map((specimen) => ({caption: specimen.label, body: specimen.tree})),
        }));
}
