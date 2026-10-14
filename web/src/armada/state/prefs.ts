// What one device remembers about how its person likes the window: the
// nav's width, dragged between 200 and 320 in whole pixels (SPEC-ENGINEERING
// section 15, assumption 8). The frame reads it; nothing writes it yet.

import {store} from "./store.ts";

export const NAV_WIDTH = {least: 200, most: 320, standard: 248} as const;

export type Prefs = {nav_width: number};

export function clamp_nav_width(width: number): number {
    if (!Number.isFinite(width)) {
        return NAV_WIDTH.standard;
    }
    return Math.min(NAV_WIDTH.most, Math.max(NAV_WIDTH.least, Math.round(width)));
}

export function prefs_from(saved: {nav_width?: unknown} | undefined): Prefs {
    return {nav_width: clamp_nav_width(Number(saved?.nav_width))};
}

export const prefs = store<Prefs>(
    {nav_width: NAV_WIDTH.standard},
    (a: Prefs, b: Prefs) => a.nav_width === b.nav_width,
);
