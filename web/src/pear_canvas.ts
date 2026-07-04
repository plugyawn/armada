import $ from "jquery";
import * as z from "zod/mini";

import render_pear_canvas_modal from "../templates/pear_canvas_modal.hbs";

import * as channel from "./channel.ts";
import * as dialog_widget from "./dialog_widget.ts";
import {$t, $t_html} from "./i18n.ts";
import {message_render_response_schema} from "./message_store.ts";
import * as narrow_state from "./narrow_state.ts";
import * as popover_menus from "./popover_menus.ts";
import {postprocess_content} from "./postprocess_content.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as ui_report from "./ui_report.ts";

// Matches the `pear_canvas` payload returned by both the
// `GET /json/pear/canvas` and `PUT /json/pear/canvas` endpoints
// (zerver/views/pear.py: pear_canvas_payload). The PUT endpoint
// returns HTTP 409 with the same payload in `responseJSON` when
// `base_version` no longer matches the document on disk.
const pear_canvas_schema = z.object({
    content: z.string(),
    version: z.string(),
    relative_path: z.string(),
    exists: z.boolean(),
    max_bytes: z.number(),
});

const pear_canvas_response_schema = z.object({
    pear_canvas: pear_canvas_schema,
});

type PearCanvas = z.infer<typeof pear_canvas_schema>;

// What the read view should show for a given canvas body: either the
// fixed "empty" placeholder text, or the Markdown source to render.
type ReadViewPlan = {mode: "empty"; text: string} | {mode: "render"; content: string};

// The subset of a fresh canvas payload that a save conflict adopts,
// plus the signal that the user's editor draft must be preserved.
type CanvasConflictResolution = {
    base_version: string;
    max_bytes: number;
    relative_path: string;
    content: string;
    keep_draft: true;
};

// Stream/topic for the currently open canvas modal; the canvas is
// per-topic, so we pin the narrow that the modal was opened from.
let open_context: {stream: string; topic: string} | undefined;
let base_version: string | undefined;
let canvas_max_bytes: number | undefined;
// Used to drop stale async responses after the modal is closed or
// the read view has been re-rendered.
let render_sequence = 0;

// --- Pure, DOM-free helpers (unit-tested in pear_canvas.test.cjs) ---

export function utf8_byte_length(text: string): number {
    return new TextEncoder().encode(text).length;
}

// The canvas may not exceed max_bytes when measured as UTF-8, matching
// the server-side check in put_pear_canvas_backend.
export function content_within_byte_limit(content: string, max_bytes: number): boolean {
    return utf8_byte_length(content) <= max_bytes;
}

// A canvas that is blank or whitespace-only shows a placeholder rather
// than an (empty) rendered-Markdown body.
export function is_canvas_empty(content: string): boolean {
    return content.trim() === "";
}

export function read_view_plan(content: string): ReadViewPlan {
    if (is_canvas_empty(content)) {
        return {mode: "empty", text: $t({defaultMessage: "This canvas is empty."})};
    }
    return {mode: "render", content};
}

// Extract the canvas document from a `{pear_canvas: {...}}` response
// body. Unknown keys (e.g. the server's workdir_path/path) are stripped.
export function parse_canvas_response(raw: unknown): PearCanvas {
    return pear_canvas_response_schema.parse(raw).pear_canvas;
}

// On an HTTP 409 CAS conflict, the PUT response carries the fresh
// document. We adopt its version and max_bytes for the next save
// attempt, but keep_draft signals that the caller must NOT overwrite
// the user's editor text. Returns undefined when the body doesn't
// parse, in which case the generic save-failed error is left visible.
export function resolve_canvas_conflict(
    response_json: unknown,
): CanvasConflictResolution | undefined {
    const parsed = pear_canvas_response_schema.safeParse(response_json);
    if (!parsed.success) {
        return undefined;
    }
    const fresh = parsed.data.pear_canvas;
    return {
        base_version: fresh.version,
        max_bytes: fresh.max_bytes,
        relative_path: fresh.relative_path,
        content: fresh.content,
        keep_draft: true,
    };
}

// --- End pure helpers ---

function modal_is_open_for(stream: string, topic: string): boolean {
    return (
        open_context?.stream === stream &&
        open_context.topic === topic &&
        $("#pear-canvas-modal").length > 0
    );
}

function editor_content(): string {
    const value = $("#pear-canvas-modal .pear-canvas-editor").val();
    return typeof value === "string" ? value : "";
}

function update_byte_info(): void {
    if (canvas_max_bytes === undefined) {
        return;
    }
    $("#pear-canvas-modal .pear-canvas-byte-info").text(
        $t(
            {defaultMessage: "{bytes} / {max_bytes} bytes"},
            {bytes: utf8_byte_length(editor_content()), max_bytes: canvas_max_bytes},
        ),
    );
}

function render_read_view(content: string): void {
    render_sequence += 1;
    const sequence = render_sequence;
    const $rendered = $("#pear-canvas-modal .pear-canvas-rendered");
    if ($rendered.length === 0) {
        return;
    }
    const plan = read_view_plan(content);
    if (plan.mode === "empty") {
        $rendered.text(plan.text);
        return;
    }
    // Render the Markdown document server-side, like the compose
    // box preview does.
    void channel.post({
        url: "/json/messages/render",
        data: {content: plan.content},
        success(raw_data) {
            if (sequence !== render_sequence) {
                return;
            }
            const data = message_render_response_schema.parse(raw_data);
            const $target = $("#pear-canvas-modal .pear-canvas-rendered");
            $target.html(postprocess_content(data.rendered));
            rendered_markdown.update_elements($target);
        },
        error() {
            if (sequence !== render_sequence) {
                return;
            }
            // Fall back to showing the raw Markdown source.
            $("#pear-canvas-modal .pear-canvas-rendered").text(content);
        },
    });
}

function apply_canvas(doc: PearCanvas): void {
    base_version = doc.version;
    canvas_max_bytes = doc.max_bytes;
    const $modal = $("#pear-canvas-modal");
    $modal.find(".dialog_heading").text(doc.relative_path);
    $modal.find(".pear-canvas-editor").val(doc.content);
    $modal.find(".pear-canvas-edit-toggle").removeClass("hide");
    update_byte_info();
    render_read_view(doc.content);
}

function stop_loading_indicator(): void {
    const $loading = $("#pear-canvas-modal .pear-canvas-loading");
    if ($loading.length > 0) {
        $loading
            .empty()
            .removeClass("pear-canvas-loading-active pear-liveness-active")
            .addClass("hide");
        $(document).trigger("pear_liveness_markers_changed");
    }
}

function load_canvas(stream: string, topic: string): void {
    const $loading = $("#pear-canvas-modal .pear-canvas-loading");
    if ($loading.length > 0) {
        $loading
            .empty()
            .removeClass("hide")
            .addClass("pear-canvas-loading-active pear-liveness-active");
        $("<span>")
            .addClass("pear-ui-glyph pear-thinking-glyph")
            .attr("aria-hidden", "true")
            .text("✦")
            .appendTo($loading);
        $("<span>")
            .text($t({defaultMessage: "Thinking"}))
            .appendTo($loading);
        $(document).trigger("pear_liveness_markers_changed");
    }
    void channel.get({
        url: "/json/pear/canvas",
        data: {stream, topic},
        success(raw_data) {
            if (!modal_is_open_for(stream, topic)) {
                return;
            }
            stop_loading_indicator();
            apply_canvas(parse_canvas_response(raw_data));
        },
        error(xhr) {
            if (!modal_is_open_for(stream, topic)) {
                return;
            }
            stop_loading_indicator();
            ui_report.error(
                $t_html({defaultMessage: "Failed to load the Pear canvas"}),
                xhr,
                $("#dialog_error"),
            );
        },
    });
}

function toggle_edit_mode(): void {
    const $modal = $("#pear-canvas-modal");
    const $editor = $modal.find(".pear-canvas-editor");
    const to_edit_mode = $editor.hasClass("hide");
    $editor.toggleClass("hide", !to_edit_mode);
    $modal.find(".pear-canvas-rendered").toggleClass("hide", to_edit_mode);
    $modal
        .find(".pear-canvas-edit-toggle .action-button-label")
        .text(to_edit_mode ? $t({defaultMessage: "View"}) : $t({defaultMessage: "Edit"}));
    if (to_edit_mode) {
        $editor.trigger("focus");
    }
}

function validate_before_save(): boolean {
    if (
        open_context === undefined ||
        base_version === undefined ||
        canvas_max_bytes === undefined
    ) {
        // The document hasn't finished loading; there is nothing
        // safe to save yet.
        return false;
    }
    if (!content_within_byte_limit(editor_content(), canvas_max_bytes)) {
        ui_report.client_error(
            $t_html(
                {defaultMessage: "The canvas is too large to save ({max_bytes} byte limit)."},
                {max_bytes: canvas_max_bytes},
            ),
            $("#dialog_error"),
        );
        return false;
    }
    return true;
}

function handle_save_conflict(xhr: JQuery.jqXHR): void {
    const resolution = resolve_canvas_conflict(xhr.responseJSON);
    if (resolution === undefined) {
        // Leave the generic save-failed error visible.
        return;
    }
    // Adopt the fresh document version for the next save attempt,
    // but keep the user's draft in the textarea untouched
    // (resolution.keep_draft).
    base_version = resolution.base_version;
    canvas_max_bytes = resolution.max_bytes;
    $("#pear-canvas-modal .dialog_heading").text(resolution.relative_path);
    render_read_view(resolution.content);
    update_byte_info();
    ui_report.client_error(
        $t_html({
            defaultMessage:
                "This canvas changed on disk. Review the latest version, then save again — your draft is preserved in the editor.",
        }),
        $("#dialog_error"),
    );
}

function save_canvas(): void {
    if (open_context === undefined || base_version === undefined) {
        return;
    }
    dialog_widget.submit_api_request(
        channel.put,
        "/json/pear/canvas",
        {
            stream: open_context.stream,
            topic: open_context.topic,
            content: editor_content(),
            base_version,
        },
        {
            failure_msg_html: $t_html({defaultMessage: "Failed to save the Pear canvas"}),
            error_continuation(xhr) {
                if (xhr.status === 409) {
                    handle_save_conflict(xhr);
                }
            },
        },
    );
}

function reset_state(): void {
    open_context = undefined;
    base_version = undefined;
    canvas_max_bytes = undefined;
    render_sequence += 1;
}

function open_canvas(): void {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined) {
        // The canvas is per-topic, so the navbar button is a no-op
        // outside a channel-topic narrow.
        return;
    }
    open_context = {stream, topic};
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Pear canvas"}),
        modal_content_html: render_pear_canvas_modal(),
        modal_submit_button_text: $t({defaultMessage: "Save changes"}),
        id: "pear-canvas-modal",
        update_submit_disabled_state_on_change: true,
        validate_input: validate_before_save,
        on_click: save_canvas,
        post_render() {
            load_canvas(stream, topic);
        },
        on_hidden() {
            reset_state();
        },
    });
}

export function initialize(): void {
    if (typeof document === "undefined") {
        // Node test environments mock the DOM minimally; this module
        // only makes sense in a browser.
        return;
    }

    // Body-delegated handlers work even though the menu is rendered
    // lazily inside the Pear runtime popover.
    $("body").on("click", "#pear-runtime-menu-popover .pear-canvas-menu-link", (e) => {
        e.preventDefault();
        e.stopPropagation();
        popover_menus.hide_current_popover_if_visible(
            popover_menus.popover_instances.pear_runtime_menu,
        );
        open_canvas();
    });
    $("body").on("click", "#pear-canvas-modal .pear-canvas-edit-toggle", (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle_edit_mode();
    });
    $("body").on("input", "#pear-canvas-modal .pear-canvas-editor", () => {
        update_byte_info();
    });
}
