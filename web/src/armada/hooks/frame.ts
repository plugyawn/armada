// The frame as old modules see it: which view of a worktree is in front.
// The old workspace module calls it when its views change.

import {is_view, show_view} from "../state/route.ts";

export function set_view(view: string): void {
    if (is_view(view)) {
        show_view(view);
    }
}
