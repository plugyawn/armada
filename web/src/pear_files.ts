import $ from "jquery";

import * as message_view from "./message_view.ts";
import * as narrow_state from "./narrow_state.ts";
import * as popover_menus from "./popover_menus.ts";

export function show_topic_files(): void {
    const stream_id = narrow_state.stream_id();
    const topic = narrow_state.topic();
    if (stream_id === undefined || topic === undefined) {
        // Files are per-topic, so the navbar button is a no-op
        // outside a channel-topic narrow.
        return;
    }
    // Same channel/topic terms as message_view.narrow_by_topic, plus
    // has:attachment so the narrow shows only messages with files.
    message_view.show(
        [
            {operator: "channel", operand: stream_id.toString()},
            {operator: "topic", operand: topic},
            {operator: "has", operand: "attachment"},
        ],
        {trigger: "pear-files", change_hash: true},
    );
}

export function initialize(): void {
    if (typeof document === "undefined") {
        // Node test environments mock the DOM minimally; this module
        // only makes sense in a browser.
        return;
    }

    // Body-delegated handlers work even though the menu is rendered
    // lazily inside the Pear runtime popover.
    $("body").on("click", "#pear-runtime-menu-popover .pear-files-menu-link", (e) => {
        e.preventDefault();
        e.stopPropagation();
        popover_menus.hide_current_popover_if_visible(
            popover_menus.popover_instances.pear_runtime_menu,
        );
        show_topic_files();
    });
}
