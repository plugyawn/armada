import $ from "jquery";
import assert from "minimalistic-assert";

import render_legacy_topic_header_controls from "../templates/legacy_topic_header_controls.hbs";
import render_message_view_header from "../templates/message_view_header.hbs";

import * as armada_narrow from "./armada/hooks/narrow.ts"; // armada:
import type {Filter} from "./filter.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as inbox_util from "./inbox_util.ts";
import * as legacy_panel from "./legacy_panel.ts";
import * as legacy_workspace from "./legacy_workspace.ts";
import * as narrow_state from "./narrow_state.ts";
import {page_params} from "./page_params.ts";
import * as peer_data from "./peer_data.ts";
import * as recent_view_util from "./recent_view_util.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as search from "./search.ts";
import * as state_data from "./state_data.ts";
import {current_user} from "./state_data.ts";
import * as stream_data from "./stream_data.ts";
import type {StreamSubscription} from "./sub_store.ts";

type MessageViewHeaderContext = {
    title?: string | undefined;
    title_html?: string | undefined;
    description?: string;
    is_spectator?: boolean;
    sub_count?: string | number;
    formatted_sub_count?: string;
    rendered_narrow_description?: string;
    is_admin?: boolean;
    stream?: StreamSubscription;
    stream_settings_link?: string;
} & (
    | {
          zulip_icon: string;
      }
    | {
          icon: string | undefined;
      }
);

function get_message_view_header_context(filter: Filter | undefined): MessageViewHeaderContext {
    if (recent_view_util.is_visible()) {
        return {
            title: $t({defaultMessage: "Recent conversations"}),
            description: $t({defaultMessage: "Overview of ongoing conversations."}),
            zulip_icon: "recent",
        };
    }

    if (inbox_util.is_visible() && !inbox_util.is_channel_view()) {
        return {
            title: $t({defaultMessage: "Inbox"}),
            description: $t({
                defaultMessage: "Overview of your conversations with unread messages.",
            }),
            zulip_icon: "inbox",
        };
    }

    // TODO: If we're not in the recent or inbox view, there should be
    // a message feed with a declared filter in the center pane. But
    // because of an initialization order bug, this function gets
    // called with a filter of `undefined` when loading the web app
    // with, say, inbox as the home view.
    //
    // TODO: Refactor this function to move the inbox/recent cases
    // into the caller, and this function can always get a Filter object.
    //
    // TODO: This ideally doesn't need a special case, we can just use
    // `filter.get_description` for it.
    if (filter === undefined || filter.is_in_home()) {
        let description;
        if (page_params.is_spectator) {
            description = $t({
                defaultMessage: "All your messages.",
            });
        } else {
            description = $t({
                defaultMessage: "All your messages except those in muted channels and topics.",
            });
        }
        return {
            title: $t({defaultMessage: "Combined feed"}),
            description,
            zulip_icon: "all-messages",
        };
    }

    const title = filter.get_title();
    const description = filter.get_description()?.description;
    assert(title !== undefined);
    const context = filter.add_icon_data({
        title,
        description,
        is_spectator: page_params.is_spectator,
    });

    if (filter.has_operator("channel")) {
        const current_stream = stream_data.get_sub_by_id_string(
            filter.terms_with_operator("channel")[0]!.operand,
        );
        if (!current_stream) {
            return {
                ...context,
                sub_count: "0",
                formatted_sub_count: "0",
                rendered_narrow_description: $t({
                    defaultMessage: "This channel does not exist or is private.",
                }),
            };
        }

        // We can now be certain that the narrow
        // involves a stream which exists and
        // the current user can access.
        const sub_count = peer_data.get_subscriber_count(current_stream.stream_id);
        return {
            ...context,
            is_admin: current_user.is_admin,
            rendered_narrow_description: current_stream.rendered_description,
            sub_count,
            stream: current_stream,
            stream_settings_link: hash_util.channels_settings_edit_url(current_stream, "general"),
        };
    }

    return context;
}

export function colorize_message_view_header(): void {
    const current_sub = narrow_state.stream_sub();
    if (!current_sub) {
        return;
    }
    $("#message_view_header .navbar-icon").css("color", current_sub.color);
}

function append_and_display_title_area(context: MessageViewHeaderContext): void {
    const $message_view_header_elem = $("#message_view_header");
    $message_view_header_elem.html(render_message_view_header(context));
    if (context.stream_settings_link) {
        colorize_message_view_header();
    }
    $message_view_header_elem.removeClass("notdisplayed");
    $(document).trigger("armada_message_view_header_rendered");
    const $content = $message_view_header_elem.find("span.rendered_markdown");
    if ($content) {
        // Update syntax like stream names, emojis, mentions, timestamps.
        rendered_markdown.update_elements($content);
    }
}

function build_message_view_header(filter: Filter | undefined): void {
    // This makes sure we don't waste time appending
    // message_view_header on a template where it's never used
    if (filter && !filter.is_common_narrow()) {
        search.open_search_bar_and_close_narrow_description();
    } else {
        const context = get_message_view_header_context(filter);
        append_and_display_title_area(context);
        search.close_search();
    }
}

export function initialize(): void {
    render_title_area();

    const hide_stream_settings_button_width_threshold = 620;
    $("body").on("mouseenter mouseleave", ".narrow_description", function (event) {
        const $view_description_elt = $(this);
        const window_width = $(window).width()!;
        let hover_timeout;

        if (event.type === "mouseenter") {
            if (!$view_description_elt.hasClass("view-description-extended")) {
                const current_width = $view_description_elt.outerWidth();
                // Set fixed width for word-wrap to work
                $view_description_elt.css("width", current_width + "px");
            }
            hover_timeout = setTimeout(() => {
                $view_description_elt.addClass("view-description-extended");
                $(".top-navbar-container").addClass(
                    "top-navbar-container-allow-description-extension",
                );

                if (window_width <= hide_stream_settings_button_width_threshold) {
                    $(".message-header-stream-settings-button").hide();
                    // Let it expand naturally on smaller screens
                    $view_description_elt.css("width", "");
                }
            }, 250);
            $view_description_elt.data("hover_timeout", hover_timeout);
        } else if (event.type === "mouseleave") {
            // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
            hover_timeout = $view_description_elt.data("hover_timeout");
            if (typeof hover_timeout === "number") {
                // Clear any pending hover_timeout to prevent unexpected behavior
                clearTimeout(hover_timeout);
            }
            $view_description_elt.addClass("leaving-extended-view-description");

            // Wait for the reverse animation duration before cleaning up
            setTimeout(() => {
                $view_description_elt.removeClass("view-description-extended");
                $view_description_elt.removeClass("leaving-extended-view-description");
                if (window_width <= hide_stream_settings_button_width_threshold) {
                    $(".message-header-stream-settings-button").show();
                    $view_description_elt.css("width", "");
                } else {
                    // Reset to flexbox-determined width
                    $view_description_elt.css("width", "");
                }
            }, 100);
        }
    });
}

// The kind of place in front, for the frame: Home is Inbox and Recent; a
// topic of a channel bound to a repository is a worktree; any other list
// of conversations is drawn as a channel.
function current_place(filter: Filter | undefined): string {
    if (
        recent_view_util.is_visible() ||
        (inbox_util.is_visible() && !inbox_util.is_channel_view())
    ) {
        return "home";
    }
    if (filter === undefined) {
        return "home";
    }
    if (filter.has_operator("dm")) {
        return "dm";
    }
    const stream_id = narrow_state.stream_id(filter);
    const topic = narrow_state.topic(filter);
    if (
        stream_id !== undefined &&
        topic !== undefined &&
        state_data.stream_has_armada_project(stream_id)
    ) {
        return "worktree";
    }
    return "channel";
}

// The header's facts belong to the worktree in front. Its views are drawn
// in the task that changes place, before the worktree's context arrives;
// legacy_panel.ts fills the same element in place when it does.
function render_worktree_facts(filter: Filter | undefined, place: string): void {
    const $facts = $("[data-armada=header-facts]");
    const stream_id = filter && narrow_state.stream_id(filter);
    const topic = filter && narrow_state.topic(filter);
    if (place !== "worktree" || stream_id === undefined || topic === undefined) {
        $facts.removeAttr("data-stream-id data-topic-name");
        $facts.find(".legacy-topic-header-status").remove();
        // Leaving a worktree closes its document views: nothing of it is
        // left in the pane of the place in front.
        legacy_workspace.sync({stream: undefined, topic: undefined, workspaceId: undefined});
        return;
    }
    $facts.attr({"data-stream-id": stream_id, "data-topic-name": topic});
    const control_key = JSON.stringify({stream_id, topic});
    $facts
        .find(".legacy-topic-header-status")
        .filter((_index, element) => $(element).attr("data-legacy-header-key") !== control_key)
        .remove();
    if ($facts.find(".legacy-topic-header-status").length === 0) {
        $facts
            .find(".recipient_bar_controls")
            .before($(render_legacy_topic_header_controls({control_key})));
        legacy_workspace.refresh_tab_strips();
        // A worktree already known fills its values in the same task.
        legacy_panel.refresh_topic_header_controls();
    }
}

// Line 2 of the header, where a worktree has its views: one sentence, the
// channel's description or what the view is.
function render_sentence(filter: Filter | undefined, place: string): void {
    let sentence = "";
    if (place !== "worktree" && (filter === undefined || filter.is_common_narrow())) {
        const context = get_message_view_header_context(filter);
        sentence = context.stream?.description ?? context.description ?? context.title ?? "";
    }
    $("[data-armada=sentence]").text(sentence);
}

export function render_title_area(): void {
    const filter = narrow_state.filter();
    build_message_view_header(filter);
    const place = current_place(filter);
    render_worktree_facts(filter, place);
    render_sentence(filter, place);
    armada_narrow.on_place(place);
}

// This function checks if "modified_sub" which is the stream whose values
// have been updated is the same as the stream which is currently
// narrowed and rerenders if necessary
export function maybe_rerender_title_area_for_stream(modified_stream_id: number): void {
    const current_stream_id = narrow_state.stream_id();

    if (current_stream_id === modified_stream_id) {
        render_title_area();
    }
}

// The navbar title shows a user's name when narrowed to a DM that
// includes them or to messages sent by them, so we refresh it for a
// rename of such a user.
export function maybe_update_navbar_title_for_user(modified_user_id: number): void {
    const filter = narrow_state.filter();
    if (filter === undefined) {
        return;
    }
    // Only common narrows show a name in the title; others show a search
    // bar and have no title to update.
    if (!filter.is_common_narrow()) {
        return;
    }
    const narrowed_to_dm_with_user = narrow_state.pm_ids_set(filter).has(modified_user_id);
    const sender_id = filter.terms_with_operator("sender")[0]?.operand;
    const narrowed_to_messages_sent_by_user = sender_id === modified_user_id;

    if (narrowed_to_dm_with_user || narrowed_to_messages_sent_by_user) {
        // Update just the title text rather than rebuilding the whole
        // header with render_title_area(), which would close or reset a
        // search bar the user has open to refine the narrow. For these
        // narrows the title is plain text and the icon doesn't depend on
        // the renamed user.
        const title = filter.get_title();
        assert(title !== undefined);
        $("#message_view_header .message-header-navbar-title").text(title);
    }
}
