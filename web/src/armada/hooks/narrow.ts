// The place in front, as message_view_header.ts learns it on every
// change of view: one call, in the task that changes place.

import {go, is_place, route} from "../state/route.ts";

export function on_place(place: string): void {
    if (!is_place(place)) {
        return;
    }
    const current = route.get();
    go(place, place === current.place ? current.view : "chat");
}
