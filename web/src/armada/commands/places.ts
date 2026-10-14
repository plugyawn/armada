// Where one can go: the kinds of place, each the same frame, and which of
// them are conversations (a band and a field under the pane). The nav (B1)
// and the palette (A5) list the places themselves.

import type {Place} from "../state/route.ts";

export type PlaceKind = {
    place: Place;
    // Home is not a conversation: it has no band and no field.
    conversation: boolean;
    // Only a worktree has views; elsewhere line 2 holds one sentence.
    views: boolean;
};

export const PLACE_KINDS: Record<Place, PlaceKind> = {
    worktree: {place: "worktree", conversation: true, views: true},
    channel: {place: "channel", conversation: true, views: false},
    dm: {place: "dm", conversation: true, views: false},
    home: {place: "home", conversation: false, views: false},
};
