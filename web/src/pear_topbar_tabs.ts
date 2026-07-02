// Pear git-native topbar, Row C: the "Messages | Canvas | Files" tab
// strip under Row A (P5d-5). Messages is the normal conversation feed;
// Files narrows the same conversation to has:attachment; Canvas swaps
// the feed for the topic's CANVAS.md (fetched/saved via
// GET/PUT /json/pear/canvas with base_version compare-and-swap).
//
// The row mounts into #navbar-fixed-container so it inherits the frame
// inset, and body.pear-tabs-visible bumps --pear-topbar-extra-height so
// feed padding and sticky headers follow automatically. Selection is
// synced from pear_topbar.rerender() (the single narrow-change choke
// point) via handle_narrow_change().

import $ from "jquery";

import render_pear_canvas_view from "../templates/pear_canvas_view.hbs";

import * as channel from "./channel.ts";
import * as components from "./components.ts";
import type {Toggle} from "./components.ts";
import type {Filter} from "./filter.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import {postprocess_content} from "./postprocess_content.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import type {NarrowTerm} from "./state_data.ts";

type PearCanvas = {
    content: string;
    version: string;
    relative_path: string;
    exists: boolean;
    max_bytes: number;
};

let toggler: Toggle | undefined;
// components.toggle fires the callback for the initial `selected` index
// at creation AND for every programmatic goto(); this flag swallows
// those so a narrow-driven sync can't recurse into message_view.show.
let syncing = false;
// The canvas document from the last GET/PUT; its `version` is the CAS
// token for the next save.
let canvas: PearCanvas | undefined;
// `${stream_id}\n${topic}` the canvas surface was opened for.
let canvas_key = "";
let canvas_open = false;

function narrow_conversation_key(): string {
    return `${narrow_state.stream_id()}\n${narrow_state.topic()}`;
}

function conversation_terms(with_attachment: boolean): NarrowTerm[] | undefined {
    const stream_id = narrow_state.stream_id();
    const topic = narrow_state.topic();
    if (stream_id === undefined || topic === undefined) {
        return undefined;
    }
    const terms: NarrowTerm[] = [
        // The channel operand is the stream ID as a string, matching
        // message_view.ts narrow_by_topic.
        {operator: "channel", operand: stream_id.toString()},
        {operator: "topic", operand: topic},
    ];
    if (with_attachment) {
        terms.push({operator: "has", operand: "attachment"});
    }
    return terms;
}

function ensure_canvas_view(): JQuery | undefined {
    let $view = $("#pear-canvas-view");
    if ($view.length > 0) {
        return $view;
    }
    const $feed = $("#message_feed_container");
    if ($feed.length === 0) {
        // Guarded for the node-test environment, where the middle
        // column does not exist.
        return undefined;
    }
    $view = $("<div>").attr("id", "pear-canvas-view").addClass("notdisplayed");
    $feed.after($view);
    return $view;
}

function show_canvas_notice(message: string): void {
    $("#pear-canvas-view .pear-canvas-stale-notice").text(message).removeClass("notdisplayed");
}

function render_canvas_markdown(content: string): void {
    const $target = $("#pear-canvas-view .pear-canvas-rendered");
    if ($target.length === 0) {
        return;
    }
    if (content.trim() === "") {
        $target.empty();
        return;
    }
    // Server-side rendering keeps the canvas view authoritative for
    // backend-only syntax (same tradeoff as the compose preview).
    void channel.post({
        url: "/json/messages/render",
        data: {content},
        success(raw_data) {
            const {rendered} = raw_data as {rendered: string};
            const $rendered = $("#pear-canvas-view .pear-canvas-rendered");
            if ($rendered.length === 0) {
                return;
            }
            $rendered.html(postprocess_content(rendered));
            // Activate timestamps, mentions, katex, code buttons, etc.
            rendered_markdown.update_elements($rendered);
        },
    });
}

function enter_edit_mode(): void {
    if (canvas === undefined) {
        return;
    }
    const $view = $("#pear-canvas-view");
    $view.find(".pear-canvas-editor").val(canvas.content).removeClass("notdisplayed");
    $view.find(".pear-canvas-rendered").addClass("notdisplayed");
    $view.find(".pear-canvas-edit-button").addClass("notdisplayed");
    $view.find(".pear-canvas-save-button, .pear-canvas-cancel-button").removeClass("notdisplayed");
    $view.find(".pear-canvas-editor").trigger("focus");
}

function exit_edit_mode(): void {
    const $view = $("#pear-canvas-view");
    $view.find(".pear-canvas-editor").addClass("notdisplayed");
    $view.find(".pear-canvas-rendered").removeClass("notdisplayed");
    $view.find(".pear-canvas-edit-button").removeClass("notdisplayed");
    $view.find(".pear-canvas-save-button, .pear-canvas-cancel-button").addClass("notdisplayed");
    $view.find(".pear-canvas-stale-notice").addClass("notdisplayed");
}

function load_canvas(): void {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined) {
        return;
    }
    const key = narrow_conversation_key();
    void channel.get({
        url: "/json/pear/canvas",
        data: {stream, topic},
        success(raw_data) {
            if (!canvas_open || canvas_key !== key) {
                // Closed or narrowed away while the request was in flight.
                return;
            }
            canvas = (raw_data as {pear_canvas: PearCanvas}).pear_canvas;
            const $view = ensure_canvas_view();
            if ($view === undefined) {
                return;
            }
            $view.html(render_pear_canvas_view({relative_path: canvas.relative_path}));
            render_canvas_markdown(canvas.content);
        },
        error() {
            if (!canvas_open || canvas_key !== key) {
                return;
            }
            ensure_canvas_view()?.text($t({defaultMessage: "Could not load canvas."}));
        },
    });
}

function save_canvas(): void {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined || canvas === undefined) {
        return;
    }
    const $view = $("#pear-canvas-view");
    const new_content = String($view.find(".pear-canvas-editor").val() ?? "");
    // The server hard-errors above max_bytes; catch it client-side.
    if (new TextEncoder().encode(new_content).length > canvas.max_bytes) {
        show_canvas_notice($t({defaultMessage: "Canvas is too large to save."}));
        return;
    }
    const $controls = $view.find(".pear-canvas-save-button, .pear-canvas-cancel-button");
    $controls.prop("disabled", true);
    void channel.put({
        url: "/json/pear/canvas",
        data: {
            stream,
            topic,
            content: new_content,
            // CAS token from the last GET/PUT.
            base_version: canvas.version,
        },
        success(raw_data) {
            canvas = (raw_data as {pear_canvas: PearCanvas}).pear_canvas;
            exit_edit_mode();
            render_canvas_markdown(canvas.content);
        },
        error(xhr) {
            if (xhr.status === 409 && xhr.responseJSON !== undefined) {
                // CAS miss: the 409 payload carries the fresh document.
                // Adopt its version so the next save wins, but KEEP the
                // user's textarea text — overwriting it would silently
                // discard their edit.
                const fresh = (xhr.responseJSON as {pear_canvas?: PearCanvas}).pear_canvas;
                if (fresh !== undefined) {
                    canvas = fresh;
                }
                show_canvas_notice(
                    $t({
                        defaultMessage:
                            "This canvas changed on disk. Saving again will overwrite those changes.",
                    }),
                );
            } else {
                show_canvas_notice($t({defaultMessage: "Saving the canvas failed."}));
            }
        },
        complete() {
            $controls.prop("disabled", false);
        },
    });
}

function open_canvas(): void {
    const $view = ensure_canvas_view();
    if ($view === undefined) {
        return;
    }
    const key = narrow_conversation_key();
    if (canvas_open && canvas_key === key) {
        // Re-clicking the already-selected tab; don't blow away an edit
        // in progress with a re-fetch.
        return;
    }
    canvas_key = key;
    canvas_open = true;
    // CSS hides #message_feed_container + #compose; never .hide(), so
    // we don't fight views_util's inline show()/hide() styles.
    $("body").addClass("pear-canvas-open");
    $view.removeClass("notdisplayed");
    // The GET runs git + message backfill server-side, so fetch only on
    // actual tab open — never on narrow change.
    load_canvas();
}

function close_canvas(): void {
    if (!canvas_open) {
        return;
    }
    canvas_open = false;
    canvas = undefined;
    $("body").removeClass("pear-canvas-open");
    $("#pear-canvas-view").addClass("notdisplayed");
}

function show_terms(terms: NarrowTerm[]): void {
    // Avoid a startup module cycle: message_view imports enough of the
    // message-list stack to trip filter.ts initialization when pulled
    // through pear_topbar -> pear_topbar_tabs.
    void import("./message_view.ts").then((message_view) => {
        message_view.show(terms, {trigger: "pear-topbar-tabs", change_hash: true});
    });
}

function handle_tab_click(key: string): void {
    switch (key) {
        case "messages": {
            close_canvas();
            // Leaving Files: strip has:attachment.
            if (narrow_state.filter()?.has_operand("has", "attachment")) {
                const terms = conversation_terms(false);
                if (terms !== undefined) {
                    show_terms(terms);
                }
            }
            break;
        }
        case "canvas": {
            open_canvas();
            break;
        }
        case "files": {
            close_canvas();
            if (!narrow_state.filter()?.has_operand("has", "attachment")) {
                const terms = conversation_terms(true);
                if (terms !== undefined) {
                    show_terms(terms);
                }
            }
            break;
        }
    }
}

export function initialize(): void {
    if (typeof document === "undefined") {
        // node-test environment guard
        return;
    }
    const $navbar = $("#navbar-fixed-container");
    if ($navbar.length === 0) {
        return;
    }
    const $row = $("<div>").attr("id", "pear-topbar-tabs").addClass("notdisplayed");
    syncing = true;
    toggler = components.toggle({
        html_class: "pear-tabs",
        // Programmatic goto() on narrow sync must not steal focus from
        // the compose box (components.toggle focuses the tab otherwise).
        child_wants_focus: true,
        // Fires the callback once at creation; swallowed by `syncing`.
        selected: 0,
        values: [
            {label: $t({defaultMessage: "Messages"}), key: "messages"},
            {label: $t({defaultMessage: "Canvas"}), key: "canvas"},
            {label: $t({defaultMessage: "Files"}), key: "files"},
        ],
        callback(_label, key) {
            if (syncing) {
                return;
            }
            handle_tab_click(key);
        },
    });
    syncing = false;
    toggler.get().appendTo($row);
    // Below #header-container, inside the fixed navbar, so the row
    // inherits the frame inset and pushes feed padding via
    // --pear-topbar-extra-height.
    $navbar.append($row);

    // The canvas view is created dynamically, so its controls are
    // body-delegated.
    $("body").on("click", "#pear-canvas-view .pear-canvas-edit-button", (e) => {
        e.preventDefault();
        enter_edit_mode();
    });
    $("body").on("click", "#pear-canvas-view .pear-canvas-save-button", (e) => {
        e.preventDefault();
        save_canvas();
    });
    $("body").on("click", "#pear-canvas-view .pear-canvas-cancel-button", (e) => {
        e.preventDefault();
        exit_edit_mode();
    });
}

// Called from pear_topbar.rerender() (the single narrow-change choke
// point) on every narrow/view change.
export function handle_narrow_change(filter: Filter | undefined): void {
    if (toggler === undefined) {
        // node-test environment, or initialize() has not run.
        return;
    }
    const in_conversation =
        filter !== undefined && filter.has_operator("channel") && filter.has_operator("topic");
    $("body").toggleClass("pear-tabs-visible", in_conversation);
    $("#pear-topbar-tabs").toggleClass("notdisplayed", !in_conversation);
    if (!in_conversation) {
        close_canvas();
        return;
    }
    if (narrow_conversation_key() !== canvas_key) {
        // Different conversation: drop any open canvas state.
        close_canvas();
    }
    const wants_files = filter.has_operand("has", "attachment");
    if (wants_files) {
        // The canvas surface belongs to the plain conversation narrow,
        // not the Files narrow.
        close_canvas();
    }
    syncing = true;
    toggler.goto(wants_files ? "files" : canvas_open ? "canvas" : "messages");
    syncing = false;
}
