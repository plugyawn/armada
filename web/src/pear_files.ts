import $ from "jquery";

import * as message_view from "./message_view.ts";
import * as narrow_state from "./narrow_state.ts";

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

    // Body-delegated handlers work even if the navbar has not been
    // rendered yet when this module is initialized.
    $("body").on("click", "#pear-files-button .header-button", (e) => {
        e.preventDefault();
        e.stopPropagation();
        show_topic_files();
    });
    $("body").on("keydown", "#pear-files-button .header-button", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            show_topic_files();
        }
    });
}
