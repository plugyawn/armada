// Where the person is: the place in front and its view. The frame draws
// from it (data-place, data-view on the plane) and the column chooses its
// sections by it. Written by hooks/narrow.ts until A1's router owns it.

import {store} from "./store.ts";

// Every place is the same frame; these are the four kinds of it.
export type Place = "worktree" | "channel" | "dm" | "home";

// A worktree's four peer views. Elsewhere the view is always chat.
export type View = "chat" | "readme" | "files" | "changes";

export type Route = {place: Place; view: View};

export const PLACES: readonly Place[] = ["worktree", "channel", "dm", "home"];
export const VIEWS: readonly View[] = ["chat", "readme", "files", "changes"];

const PLACE_NAMES = new Set<string>(PLACES);
const VIEW_NAMES = new Set<string>(VIEWS);

export function is_place(value: string): value is Place {
    return PLACE_NAMES.has(value);
}

export function is_view(value: string): value is View {
    return VIEW_NAMES.has(value);
}

export function same_route(a: Route, b: Route): boolean {
    return a.place === b.place && a.view === b.view;
}

export const route = store<Route>({place: "home", view: "chat"}, same_route);

export function go(place: Place, view: View = "chat"): void {
    route.set({place, view: place === "worktree" ? view : "chat"});
}

export function show_view(view: View): void {
    go(route.get().place, view);
}

// Files and Changes lay out on the whole composition and fold the column
// into its heading; Chat and README keep the column.
export function folds_column(value: Route): boolean {
    return value.view === "files" || value.view === "changes";
}
