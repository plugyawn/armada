// Pear git-native topbar, Row A: renders "# stream  ⎇ topic" for the
// current stream/topic narrow (R3: topic == branch), with a
// branch-switcher dropdown fed by the SAME store the left sidebar
// reads (stream_topic_history), so the two never diverge. The runtime
// chip, mismatch chip, and Row C tabs mount into this row in later P5d
// slices; this slice owns only the context + branch switcher.
//
// The outer #pear-topbar div (navbar.hbs) is stable across narrow
// changes — only its innerHTML is replaced — so the DropdownWidget's
// tippy.delegate stays bound.

import $ from "jquery";

import render_pear_topbar from "../templates/pear_topbar.hbs";

import * as browser_history from "./browser_history.ts";
import * as dropdown_widget from "./dropdown_widget.ts";
import type {Option} from "./dropdown_widget.ts";
import * as hash_util from "./hash_util.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_runtime_controls from "./pear_runtime_controls.ts";
import * as popover_menus from "./popover_menus.ts";
import * as server_events_state from "./server_events_state.ts";
import * as stream_topic_history from "./stream_topic_history.ts";
import * as stream_topic_history_util from "./stream_topic_history_util.ts";
import * as typing_data from "./typing_data.ts";
import * as unread from "./unread.ts";
import * as util from "./util.ts";

let branch_widget: dropdown_widget.DropdownWidget | undefined;

function topic_is_live(stream_id: number, topic: string): boolean {
    // Same signal the feed's "Pear is working" dots use; valid for ANY
    // subscribed topic, unlike pear_runs_store whose poll is scoped to
    // the current narrow.
    return typing_data
        .get_topic_typists(stream_id, topic)
        .some((user_id) => server_events_state.is_pear_bot_user_id(user_id));
}

function branch_options(): Option[] {
    const stream_id = narrow_state.stream_id();
    if (stream_id === undefined) {
        return [];
    }
    return stream_topic_history.get_recent_topic_names(stream_id).map((topic) => ({
        // The empty string ("general chat") is a legal topic id; keep
        // the raw name here and in narrow/unread calls, display only.
        unique_id: topic,
        name: util.get_final_topic_display_name(topic),
        aliases: [topic],
    }));
}

function decorate_options($popper: JQuery): void {
    const stream_id = narrow_state.stream_id();
    if (stream_id === undefined) {
        return;
    }
    $popper.find(".list-item").each(function () {
        const topic = $(this).attr("data-unique-id");
        if (topic === undefined) {
            return;
        }
        const $name = $(this).find(".dropdown-list-item-name");
        $name.prepend('<span class="pear-branch-glyph" aria-hidden="true">⎇&nbsp;</span>');
        const unread_count = unread.num_unread_for_topic(stream_id, topic);
        if (unread_count > 0) {
            // .text() — topic-derived counts are numeric, but never
            // string-concatenate user content into HTML.
            $name.append($('<span class="pear-branch-unread">').text(String(unread_count)));
        }
        if (topic_is_live(stream_id, topic)) {
            $name.append('<span class="pear-branch-live-dot" aria-hidden="true"></span>');
        }
    });
}

export function initialize(): void {
    const $container = $("#pear-topbar");
    if ($container.length === 0) {
        // node-test environment guard
        return;
    }
    branch_widget = new dropdown_widget.DropdownWidget({
        // Trigger element (pear_topbar.hbs) carries the default
        // selector id="pear-branch-switcher_widget".
        widget_name: "pear-branch-switcher",
        get_options: branch_options,
        item_click_callback(_event, instance, widget) {
            const topic = widget.value();
            if (typeof topic !== "string") {
                return;
            }
            popover_menus.hide_current_popover_if_visible(instance);
            const stream_id = narrow_state.stream_id();
            if (stream_id !== undefined) {
                browser_history.go_to_location(hash_util.by_stream_topic_url(stream_id, topic));
            }
        },
        on_mount_callback(instance) {
            decorate_options($(instance.popper));
        },
        // #pear-topbar is stable; only its innerHTML is re-rendered, so
        // this delegate target stays valid across narrow changes.
        $events_container: $container,
        unique_id_type: "string",
        // The trigger label is owned by pear_topbar.hbs, never by
        // DropdownWidget.render().
    });
    branch_widget.setup();
    pear_runtime_controls.initialize();
}

export function rerender(): void {
    const $container = $("#pear-topbar");
    if ($container.length === 0) {
        return;
    }
    const $stock_header = $("#message_view_header");
    const filter = narrow_state.filter();
    const stream_id = narrow_state.stream_id();
    const sub = narrow_state.stream_sub();
    // Own only stream / stream+topic common narrows. Everything else
    // — including the search-bar fallback for uncommon narrows
    // (message_view_header.ts build_message_view_header) — keeps the
    // stock header.
    if (
        filter === undefined ||
        !filter.is_common_narrow() ||
        stream_id === undefined ||
        sub === undefined
    ) {
        $container.addClass("notdisplayed").empty();
        $stock_header.removeClass("notdisplayed");
        // Clears the cached context + hides the runtime/mismatch chips
        // for non-pear narrows.
        pear_runtime_controls.update_chip();
        return;
    }
    const topic = narrow_state.topic();
    $container.html(
        render_pear_topbar({
            stream_name: sub.name,
            stream_url: hash_util.by_stream_url(stream_id),
            topic_display_name:
                topic === undefined ? undefined : util.get_final_topic_display_name(topic),
            has_topic: topic !== undefined,
        }),
    );
    $container.removeClass("notdisplayed");
    $stock_header.addClass("notdisplayed");
    // Highlight the current branch when the dropdown opens. current_value
    // is read inside onShow before on_show_callback fires, so set it here.
    if (branch_widget !== undefined) {
        branch_widget.current_value = topic;
    }
    // Warm the same topic store the sidebar uses; get_options re-reads
    // it at open time, so no callback wiring is needed.
    stream_topic_history_util.get_server_history(stream_id, () => {
        // no-op; the store is read lazily on dropdown open
    });
    // Single choke point: refresh the runtime chip + mismatch chip in
    // the right cluster this rerender just rebuilt (P5d-3).
    pear_runtime_controls.update_chip();
}
