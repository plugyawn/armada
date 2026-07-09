// The workspace tabs (design: pear/PEAR_CANVAS_TABS_DESIGN.md, T1).
// Chat is the message feed; Canvas is CANVAS.md made comfortable;
// Files is the worktree made visible. One strip, rendered in the
// recipient bar and mirrored at the top of the pane, switches between
// them without leaving the narrow.

import $ from "jquery";
import * as tippy from "tippy.js";
import * as z from "zod/mini";

import render_pear_workspace_pane from "../templates/pear_workspace_pane.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import {message_render_response_schema} from "./message_store.ts";
import * as pear_canvas from "./pear_canvas.ts";
import * as pear_canvas_editor from "./pear_canvas_editor.ts";
import {postprocess_content} from "./postprocess_content.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as stream_data from "./stream_data.ts";
import {user_settings} from "./user_settings.ts";

export type PearWorkspaceTab = "chat" | "canvas" | "files";

const workspace_files_schema = z.object({
    pear_workspace_files: z.object({
        files: z.array(
            z.object({
                path: z.string(),
                status: z.string(),
                size: z.nullish(z.number()),
                commit_subject: z.optional(z.string()),
                commit_time: z.nullish(z.number()),
            }),
        ),
        truncated: z.boolean(),
        canvas_path: z.nullish(z.string()),
    }),
});

const workspace_file_schema = z.object({
    pear_workspace_file: z.object({path: z.string(), content: z.string()}),
});

const canvas_response_schema = z.object({
    pear_canvas: z.object({
        content: z.string(),
        version: z.string(),
    }),
});

let current_tab: PearWorkspaceTab = "chat";
let current_context_key = "";
let current_stream: string | undefined;
let current_topic: string | undefined;
let has_workspace = false;

function context_key(stream: string | undefined, topic: string | undefined): string {
    return JSON.stringify([stream?.toLowerCase() ?? "", topic?.toLowerCase() ?? ""]);
}

function $pane(): JQuery {
    return $("#pear-workspace-pane");
}

function ensure_pane(): JQuery {
    let $existing = $pane();
    if ($existing.length > 0) {
        return $existing;
    }
    const $feed = $("#message_feed_container");
    if ($feed.length === 0) {
        return $existing;
    }
    $feed.after($("<div>").attr("id", "pear-workspace-pane").prop("hidden", true));
    $existing = $pane();
    const pane = $existing[0];
    if (pane !== undefined) {
        // Shield Zulip's global hotkeys at the pane's bubble phase:
        // editor handlers (ProseMirror, the Shift+Enter set gesture)
        // have already run by the time the event gets here. A
        // document-capture stopPropagation, as this first shipped,
        // silenced the pane's own listeners too.
        pane.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                set_tab("chat");
                return;
            }
            event.stopPropagation();
        });
    }
    return $existing;
}

export function pane_is_open(): boolean {
    return current_tab !== "chat";
}

export function refresh_tab_strips(): void {
    update_tab_strips();
}

function update_tab_strips(): void {
    $(".pear-workspace-tab").each(function (this: HTMLElement) {
        const tab = $(this).attr("data-pear-tab");
        $(this).toggleClass("pear-workspace-tab-selected", tab === current_tab);
    });
    $(".pear-workspace-tabs").toggleClass("pear-workspace-tabs-hidden", !has_workspace);
}

// The shell (header + both tab bodies) persists per topic so tab
// flips are instant: the editor keeps its mounted instance while the
// pane is hidden, and only a context change tears it down.
let pane_shell_key: string | undefined;

// The topic bar IS the chat's recipient bar, promoted to page
// chrome: one sticky instance above both the feed and the workspace
// pane, so nothing about it changes when tabs switch. Its content is
// a live mirror of the feed's own bar (which the feed then renders
// date-only underneath), and pear_panel's updater keeps the mirror's
// cluster fresh through the same selectors it uses everywhere.
let topic_bar_signature = "";

function $topic_bar(): JQuery {
    return $("#pear-topic-bar");
}

function ensure_topic_bar(): JQuery {
    const $existing = $topic_bar();
    if ($existing.length > 0) {
        return $existing;
    }
    const $feed = $("#message_feed_container");
    if ($feed.length === 0) {
        return $existing;
    }
    $feed.before($("<div>").attr("id", "pear-topic-bar").prop("hidden", true));
    return $topic_bar();
}

function source_topic_header(): HTMLElement | undefined {
    if (current_stream === undefined || current_topic === undefined) {
        return undefined;
    }
    const stream_id = stream_data.get_stream_id(current_stream);
    return [
        ...document.querySelectorAll<HTMLElement>(
            "#message_feed_container .message_header_stream",
        ),
    ].find(
        (element) =>
            element.getAttribute("data-topic-name") === current_topic &&
            (stream_id === undefined ||
                Number.parseInt(element.getAttribute("data-stream-id") ?? "", 10) === stream_id),
    );
}

function set_topic_bar_height(height: number): void {
    document
        .querySelector<HTMLElement>(".column-middle-inner")
        ?.style.setProperty("--pear-topic-bar-height", `${height}px`);
}

function sync_topic_bar(): void {
    const $bar = ensure_topic_bar();
    if ($bar.length === 0) {
        return;
    }
    // The bar substitutes for the feed's own sticky header only
    // while the pane covers the feed; in chat, Zulip's native bar
    // (identical content, identical pinned position) owns the page,
    // keeping scroll anchoring untouched.
    const active =
        has_workspace &&
        pane_is_open() &&
        current_stream !== undefined &&
        current_topic !== undefined;
    $(".column-middle-inner").toggleClass("pear-topic-bar-active", active);
    if (!active) {
        $bar.attr("hidden", "hidden").empty();
        topic_bar_signature = "";
        set_topic_bar_height(0);
        return;
    }
    const source = source_topic_header();
    if (source === undefined) {
        // Nothing to mirror yet (feed still rendering); keep the
        // last bar rather than flashing empty.
        if ($bar.children().length === 0) {
            $bar.attr("hidden", "hidden");
            set_topic_bar_height(0);
        }
        return;
    }
    const signature = `${current_context_key}|${source.textContent ?? ""}`;
    if (signature !== topic_bar_signature || $bar.children().length === 0) {
        topic_bar_signature = signature;
        const clone = source.cloneNode(true);
        if (clone instanceof HTMLElement) {
            $bar.empty();
            $bar[0]?.append(clone);
            // The freshly mirrored cluster needs its labels filled.
            document.dispatchEvent(new CustomEvent("pear:pane-shell-rendered"));
        }
    }
    $bar.removeAttr("hidden");
    set_topic_bar_height($bar[0]?.offsetHeight ?? 0);
}

function ensure_pane_shell(): boolean {
    const $container = ensure_pane();
    if ($container.length === 0) {
        return false;
    }
    if (pane_shell_key === current_context_key && $container.children().length > 0) {
        return true;
    }
    void pear_canvas_editor.destroy();
    canvas_loaded = false;
    $container.html(render_pear_workspace_pane({}));
    pane_shell_key = current_context_key;
    return true;
}

function show_pane(): void {
    if (!ensure_pane_shell()) {
        return;
    }
    $("#message_feed_container").hide();
    $pane().removeAttr("hidden");
    const $canvas_body = $pane().find(".pear-canvas-body");
    const $files_body = $pane().find(".pear-files-body");
    $canvas_body.toggleClass("pear-body-hidden", current_tab !== "canvas");
    $files_body.toggleClass("pear-body-hidden", current_tab !== "files");
    if (current_tab === "canvas") {
        if (pear_canvas_editor.is_mounted() && canvas_loaded) {
            // Instant from the live instance; revalidate quietly.
            reload_canvas_content();
        } else {
            load_canvas();
        }
    } else if (current_tab === "files") {
        load_files();
    }
    update_tab_strips();
}

function hide_pane(): void {
    flush_canvas_save();
    $pane().attr("hidden", "hidden");
    $("#message_feed_container").show();
}

function teardown_pane_context(): void {
    flush_canvas_save();
    canvas_loaded = false;
    void pear_canvas_editor.destroy();
    pane_shell_key = undefined;
    $pane().empty();
}

export function set_tab(tab: PearWorkspaceTab): void {
    if (tab === current_tab) {
        return;
    }
    if (tab !== "chat" && !has_workspace) {
        return;
    }
    if (current_tab === "canvas") {
        flush_canvas_save();
    }
    current_tab = tab;
    try {
        sessionStorage.setItem(`pear-tab:${current_context_key}`, tab);
    } catch {
        // Session storage can be unavailable; the tab just won't be
        // remembered.
    }
    update_tab_strips();
    if (tab === "chat") {
        hide_pane();
    } else {
        show_pane();
    }
    sync_topic_bar();
}

// Called from pear_panel on every header render; cheap and idempotent.
// has_workspace === undefined means "context still loading — keep the
// last known answer": flapping to hidden during fetch gaps made the
// strip flicker (live report).
export function sync(opts: {
    stream: string | undefined;
    topic: string | undefined;
    has_workspace: boolean | undefined;
}): void {
    const key = context_key(opts.stream, opts.topic);
    const context_changed = key !== current_context_key;
    if (context_changed) {
        // Commit and save any open editor while the canvas context
        // still points at the topic it was loaded from, then drop
        // the old topic's shell and editor instance.
        teardown_pane_context();
        files_collapsed_dirs = new Set();
        last_files_payload = undefined;
    }
    current_context_key = key;
    current_stream = opts.stream;
    current_topic = opts.topic;
    if (context_changed) {
        has_workspace = opts.has_workspace ?? false;
    } else if (opts.has_workspace !== undefined) {
        has_workspace = opts.has_workspace;
    }
    if (context_changed) {
        // Returning to a topic restores its remembered tab; fresh
        // topics land on Chat.
        let remembered: string | null = null;
        try {
            remembered = sessionStorage.getItem(`pear-tab:${key}`);
        } catch {
            remembered = null;
        }
        if (pane_is_open()) {
            current_tab = "chat";
            hide_pane();
        }
        if ((remembered === "canvas" || remembered === "files") && (opts.has_workspace ?? false)) {
            current_tab = remembered;
            update_tab_strips();
            show_pane();
        }
    }
    if (!has_workspace && pane_is_open()) {
        current_tab = "chat";
        hide_pane();
    }
    sync_topic_bar();
    update_tab_strips();
}

// ---------------- Files ----------------

type FileEntry = {
    path: string;
    status: string;
    size?: number | null | undefined;
    commit_subject?: string | undefined;
    commit_time?: number | null | undefined;
};

function short_relative_time(unix_seconds: number): string {
    const seconds = Math.max(0, Date.now() / 1000 - unix_seconds);
    if (seconds < 3600) {
        return `${Math.max(1, Math.floor(seconds / 60))}m`;
    }
    if (seconds < 86_400) {
        return `${Math.floor(seconds / 3600)}h`;
    }
    if (seconds < 86_400 * 30) {
        return `${Math.floor(seconds / 86_400)}d`;
    }
    return `${Math.floor(seconds / (86_400 * 30))}mo`;
}

function format_file_size(size: number): string {
    if (size < 1024) {
        return `${size} B`;
    }
    if (size < 1024 * 1024) {
        return `${(size / 1024).toFixed(1)} KB`;
    }
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

let files_collapsed_dirs = new Set<string>();
let last_files_payload:
    | {files: FileEntry[]; canvas_path: string | null; truncated: boolean}
    | undefined;

const FILE_TYPE_GLYPHS: [RegExp, string][] = [
    [/\.(?:md|markdown|txt|rst)$/i, "\u25A4"], // ▤ prose
    [/\.(?:png|jpe?g|gif|webp|svg)$/i, "\u25C9"], // ◉ image
    [/\.(?:json|ya?ml|toml|ini|cfg|lock)$/i, "\u25A6"], // ▦ config
    [/\.(?:py|ts|tsx|js|jsx|rs|go|c|cc|cpp|h|java|rb|sh|swift|kt|html|css)$/i, "\u25C6"], // ◆ code
];

function file_type_glyph(path: string): string {
    for (const [pattern, glyph] of FILE_TYPE_GLYPHS) {
        if (pattern.test(path)) {
            return glyph;
        }
    }
    return "\u00B7";
}

function file_language(path: string): string {
    const extension = path.split(".").at(-1)?.toLowerCase() ?? "";
    const languages: Record<string, string> = {
        py: "python",
        ts: "typescript",
        tsx: "typescript",
        js: "javascript",
        jsx: "javascript",
        rs: "rust",
        rb: "ruby",
        cc: "cpp",
        cpp: "cpp",
        kt: "kotlin",
        yml: "yaml",
        sh: "bash",
    };
    return languages[extension] ?? extension;
}

function file_status_chip(file: FileEntry, is_canvas: boolean): JQuery | undefined {
    if (is_canvas) {
        return $("<span>")
            .addClass("pear-files-chip pear-files-chip-canvas")
            .text($t({defaultMessage: "canvas"}));
    }
    if (file.status === "changed") {
        return $("<span>")
            .addClass("pear-files-chip pear-files-chip-changed")
            .text($t({defaultMessage: "modified"}));
    }
    if (file.status === "untracked") {
        return $("<span>")
            .addClass("pear-files-chip pear-files-chip-untracked")
            .text($t({defaultMessage: "new"}));
    }
    return undefined;
}

function render_files_tree(
    files: FileEntry[],
    canvas_path: string | null,
    truncated: boolean,
): void {
    const $body = $pane().find(".pear-files-body");
    if ($body.length === 0) {
        return;
    }
    last_files_payload = {files, canvas_path, truncated};
    const $sheet = $("<div>").addClass("pear-files-sheet");
    if (files.length === 0) {
        $sheet.append(
            $("<div>")
                .addClass("pear-files-empty")
                .text(
                    $t({
                        defaultMessage:
                            "This branch has no files yet — ask Pear to create some, or open the Canvas.",
                    }),
                ),
        );
        $body.empty().append($sheet);
        return;
    }
    const $list = $("<ul>").addClass("pear-files-list");
    const seen_dirs = new Set<string>();
    const ordered = files.toSorted((a, b) => {
        const canvas_first = Number(b.path === canvas_path) - Number(a.path === canvas_path);
        if (canvas_first !== 0) {
            return canvas_first;
        }
        return a.path.localeCompare(b.path);
    });
    const hidden_by_collapse = (path: string): boolean =>
        [...files_collapsed_dirs].some((dir) => path.startsWith(`${dir}/`));
    for (const file of ordered) {
        const parts = file.path.split("/");
        for (let depth = 0; depth < parts.length - 1; depth += 1) {
            const dir = parts.slice(0, depth + 1).join("/");
            if (!seen_dirs.has(dir)) {
                seen_dirs.add(dir);
                if (depth > 0 && hidden_by_collapse(dir)) {
                    continue;
                }
                const collapsed = files_collapsed_dirs.has(dir);
                const $dir = $("<li>")
                    .addClass("pear-files-dir")
                    .attr("data-pear-dir", dir)
                    .toggleClass("pear-files-dir-collapsed", collapsed);
                $dir.css("--pear-file-depth", `${depth}`);
                $dir.append(
                    $("<i>").addClass(
                        `zulip-icon pear-files-dir-icon ${
                            collapsed ? "zulip-icon-triangle-right" : "zulip-icon-triangle-down"
                        }`,
                    ),
                    $("<span>").addClass("pear-files-name").text(parts[depth]!),
                );
                $list.append($dir);
            }
        }
        if (hidden_by_collapse(file.path)) {
            continue;
        }
        const depth = parts.length - 1;
        const is_canvas = file.path === canvas_path;
        const $row = $("<li>")
            .addClass("pear-files-row")
            .attr("data-pear-file", file.path)
            .toggleClass("pear-files-row-canvas", is_canvas);
        $row.css("--pear-file-depth", `${depth}`);
        $row.append(
            $("<span>").addClass("pear-files-glyph").text(file_type_glyph(file.path)),
            $("<span>").addClass("pear-files-name").text(parts[depth]!),
        );
        const $chip = file_status_chip(file, is_canvas);
        if ($chip !== undefined) {
            $row.append($chip);
        }
        if (file.commit_subject !== undefined && file.commit_subject !== "") {
            $row.append(
                $("<span>").addClass("pear-files-commit").text(file.commit_subject),
            );
        }
        if (typeof file.commit_time === "number") {
            $row.append(
                $("<span>")
                    .addClass("pear-files-when")
                    .text(short_relative_time(file.commit_time)),
            );
        }
        const $actions = $("<span>").addClass("pear-files-actions");
        $actions.append(
            $("<button>")
                .attr("type", "button")
                .addClass("pear-files-copy tippy-zulip-delayed-tooltip")
                .attr("data-pear-file", file.path)
                .attr("aria-label", $t({defaultMessage: "Copy path"}))
                .attr("data-tippy-content", $t({defaultMessage: "Copy path"}))
                .append($("<i>").addClass("zulip-icon zulip-icon-copy")),
            $("<button>")
                .attr("type", "button")
                .addClass("pear-files-download tippy-zulip-delayed-tooltip")
                .attr("data-pear-file", file.path)
                .attr("aria-label", $t({defaultMessage: "Download"}))
                .attr("data-tippy-content", $t({defaultMessage: "Download"}))
                .append($("<i>").addClass("zulip-icon zulip-icon-arrow-down")),
        );
        $row.append($actions);
        $list.append($row);
    }
    $sheet.append($list);
    if (truncated) {
        $sheet.append(
            $("<div>")
                .addClass("pear-files-truncated")
                .text($t({defaultMessage: "Only the first 500 files are shown."})),
        );
    }
    $body.empty().append($sheet);
    const readme = ordered.find((file) => /^readme\.(?:md|markdown)$/i.test(file.path));
    if (readme !== undefined) {
        render_files_readme(readme.path, $sheet);
    }
}

function render_files_readme(path: string, $sheet: JQuery): void {
    if (current_stream === undefined || current_topic === undefined) {
        return;
    }
    void channel.get({
        url: "/json/pear/workspace-file",
        data: {stream: current_stream, topic: current_topic, path},
        success(raw_data) {
            const parsed = workspace_file_schema.safeParse(raw_data);
            if (!parsed.success || current_tab !== "files") {
                return;
            }
            void channel.post({
                url: "/json/messages/render",
                data: {content: parsed.data.pear_workspace_file.content},
                success(raw_rendered) {
                    if (current_tab !== "files" || $sheet.closest("body").length === 0) {
                        return;
                    }
                    const rendered = message_render_response_schema.parse(raw_rendered);
                    const $readme = $("<div>").addClass("pear-files-readme");
                    $readme.append(
                        $("<div>").addClass("pear-files-readme-title").text(path),
                    );
                    const $content = $("<div>").addClass(
                        "pear-files-readme-content rendered_markdown",
                    );
                    $content.html(postprocess_content(rendered.rendered));
                    rendered_markdown.update_elements($content);
                    $readme.append($content);
                    $sheet.append($readme);
                },
            });
        },
    });
}

function show_file_preview(path: string): void {
    if (current_stream === undefined || current_topic === undefined) {
        return;
    }
    const $body = $pane().find(".pear-files-body");
    if ($body.length === 0) {
        return;
    }
    void channel.get({
        url: "/json/pear/workspace-file",
        data: {stream: current_stream, topic: current_topic, path},
        success(raw_data) {
            const parsed = workspace_file_schema.safeParse(raw_data);
            if (!parsed.success || current_tab !== "files") {
                return;
            }
            const content = parsed.data.pear_workspace_file.content;
            const is_markdown = /\.(?:md|markdown)$/i.test(path);
            const fence = "~".repeat(6);
            const source = is_markdown
                ? content
                : `${fence}${file_language(path)}\n${content}\n${fence}`;
            void channel.post({
                url: "/json/messages/render",
                data: {content: source},
                success(raw_rendered) {
                    if (current_tab !== "files") {
                        return;
                    }
                    const rendered = message_render_response_schema.parse(raw_rendered);
                    const $sheet = $("<div>").addClass("pear-files-sheet pear-files-preview");
                    const $header = $("<div>").addClass("pear-files-preview-header");
                    $header.append(
                        $("<button>")
                            .attr("type", "button")
                            .addClass("pear-files-back")
                            .text($t({defaultMessage: "\u2190 files"})),
                        $("<span>").addClass("pear-files-preview-path").text(path),
                        $("<span>")
                            .addClass("pear-files-preview-meta")
                            .text(
                                [
                                    $t(
                                        {defaultMessage: "{lines} lines"},
                                        {lines: content.split("\n").length},
                                    ),
                                    format_file_size(
                                        new Blob([content]).size,
                                    ),
                                ].join(" · "),
                            ),
                        $("<button>")
                            .attr("type", "button")
                            .addClass("pear-files-download")
                            .attr("data-pear-file", path)
                            .attr("aria-label", $t({defaultMessage: "Download"}))
                            .append($("<i>").addClass("zulip-icon zulip-icon-arrow-down")),
                    );
                    const $content = $("<div>").addClass(
                        "pear-files-preview-content rendered_markdown",
                    );
                    $content.html(postprocess_content(rendered.rendered));
                    rendered_markdown.update_elements($content);
                    $sheet.append($header, $content);
                    $body.empty().append($sheet);
                },
            });
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to preview workspace file: ${xhr.responseText}`);
            }
        },
    });
}

function load_files(): void {
    if (current_stream === undefined || current_topic === undefined) {
        return;
    }
    void channel.get({
        url: "/json/pear/workspace-files",
        data: {stream: current_stream, topic: current_topic},
        success(raw_data) {
            const parsed = workspace_files_schema.safeParse(raw_data);
            if (!parsed.success || current_tab !== "files") {
                return;
            }
            const payload = parsed.data.pear_workspace_files;
            render_files_tree(payload.files, payload.canvas_path ?? null, payload.truncated);
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to list workspace files: ${xhr.responseText}`);
            }
        },
    });
}

function download_file(path: string): void {
    if (current_stream === undefined || current_topic === undefined) {
        return;
    }
    void channel.get({
        url: "/json/pear/workspace-file",
        data: {stream: current_stream, topic: current_topic, path},
        success(raw_data) {
            const parsed = workspace_file_schema.safeParse(raw_data);
            if (!parsed.success) {
                return;
            }
            const blob = new Blob([parsed.data.pear_workspace_file.content], {
                type: "text/plain;charset=utf-8",
            });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = path.split("/").at(-1) ?? path;
            anchor.click();
            URL.revokeObjectURL(url);
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to download workspace file: ${xhr.responseText}`);
            }
        },
    });
}

// ---------------- Canvas (block model, design T2 §A) ----------------
// The file stays plain markdown; the UI derives blocks (split at blank
// lines outside fences; headings start blocks). Shift+Enter commits a
// block: the doc reassembles, saves through the versioned PUT, and the
// block re-renders in place.

type CanvasBlock = {source: string};

let canvas_blocks: CanvasBlock[] = [];
let canvas_version = "";
let canvas_loaded = false;
// Last known file per topic: reopening a canvas paints instantly
// from here while a background fetch reconciles.
const canvas_content_cache = new Map<string, {content: string; version: string}>();
const CANVAS_CONTENT_CACHE_LIMIT = 20;

function remember_canvas_content(key: string, content: string, version: string): void {
    canvas_content_cache.delete(key);
    canvas_content_cache.set(key, {content, version});
    while (canvas_content_cache.size > CANVAS_CONTENT_CACHE_LIMIT) {
        const oldest = canvas_content_cache.keys().next().value;
        if (oldest === undefined) {
            break;
        }
        canvas_content_cache.delete(oldest);
    }
}
// The topic whose file is loaded. Saves, asks, and refetches bind to
// this, never to current_stream/current_topic: the narrow can change
// while an editor is open, and a canvas must only ever write to the
// topic it was loaded from (live report: a narrow switch mid-edit
// wrote one topic's canvas into another).
let canvas_context: {stream: string; topic: string; key: string} | undefined;

// ---------------- Asks ----------------
// A bracketed span in a committed block is a request to the agent:
// "[request](label)" resolves into a popover aside; bare "[request]"
// resolves in place. Real links, checkboxes, footnotes, and images
// keep their markdown meaning (PEAR_CANVAS_TABS_DESIGN.md §B).

type CanvasAsk = {request: string; label: string};

const PEAR_ASK_PENDING_TTL_MS = 10 * 60 * 1000;
// Keyed by `${context}|${request}|${label}`.
const pending_asks = new Map<string, CanvasAsk & {started_at: number}>();
let ask_dispatch_queue: CanvasAsk[] = [];
const aside_sections = new Map<string, string>();
const rendered_aside_cache = new Map<string, string>();

function ask_key(ask: CanvasAsk): string {
    return `${canvas_context?.key ?? current_context_key}|${ask.request}|${ask.label}`;
}

function ask_span_text(ask: CanvasAsk): string {
    return ask.label === "" ? `[${ask.request}]` : `[${ask.request}](${ask.label})`;
}

function target_is_real_link(target: string): boolean {
    return /^(?:[a-z][a-z0-9+.-]*:|\/|#|\.\.?\/)/i.test(target.trim());
}

function parse_asks_from_source(source: string): CanvasAsk[] {
    if (/^\s{0,3}(?:```|~~~)/.test(source)) {
        // Fenced blocks are code, never asks.
        return [];
    }
    const asks: CanvasAsk[] = [];
    const span_re = /\[([^[\]\n]+)\](?:\(([^()\n]+)\))?/g;
    for (const match of source.matchAll(span_re)) {
        const request = match[1] ?? "";
        const label = (match[2] ?? "").trim();
        const before = source.slice(0, match.index);
        const after = source.slice(match.index + match[0].length);
        if (/^[x ]$/i.test(request) || request.startsWith("^")) {
            // Checkbox or footnote.
            continue;
        }
        if (before.endsWith("!") || before.endsWith("]")) {
            // Image, or the reference half of [text][ref].
            continue;
        }
        if (label === "" && after.startsWith("[")) {
            // The text half of a reference-style link.
            continue;
        }
        const line_start = before.lastIndexOf("\n") + 1;
        const backticks = before.slice(line_start).split("`").length - 1;
        if (backticks % 2 === 1) {
            // Inside an inline code span.
            continue;
        }
        if (label !== "" && target_is_real_link(label)) {
            // A real markdown link (including resolved #aside-N ones).
            continue;
        }
        asks.push({request: request.trim(), label});
    }
    return asks;
}

function dispatch_queued_asks(): void {
    if (canvas_context === undefined) {
        ask_dispatch_queue = [];
        return;
    }
    const queue = ask_dispatch_queue;
    ask_dispatch_queue = [];
    for (const ask of queue) {
        const block_source =
            canvas_blocks.find((block) => block.source.includes(ask_span_text(ask)))?.source ?? "";
        pending_asks.set(ask_key(ask), {...ask, started_at: Date.now()});
        void channel.post({
            url: "/json/pear/canvas-ask",
            data: {
                stream: canvas_context.stream,
                topic: canvas_context.topic,
                request: ask.request,
                label: ask.label,
                block: block_source,
            },
            success() {
                set_canvas_status($t({defaultMessage: "pear is on it"}));
                pear_canvas_editor.refresh_decorations();
                ensure_pending_ask_poll();
            },
            error(xhr) {
                pending_asks.delete(ask_key(ask));
                pear_canvas_editor.refresh_decorations();
                if (xhr.readyState !== 0) {
                    set_canvas_status($t({defaultMessage: "ask failed"}), true);
                }
            },
        });
    }
}

function prune_pending_asks(): void {
    const document_text = join_canvas_blocks(canvas_blocks);
    const now = Date.now();
    const context_prefix = `${canvas_context?.key ?? current_context_key}|`;
    for (const [key, entry] of pending_asks) {
        if (!key.startsWith(context_prefix)) {
            continue;
        }
        const span = ask_span_text(entry);
        if (!document_text.includes(span) || now - entry.started_at > PEAR_ASK_PENDING_TTL_MS) {
            pending_asks.delete(key);
        }
    }
}

// Ask runs answer by editing the file, without any topic message —
// so nothing event-driven announces their settle. While asks are
// pending and the canvas is open, poll the file; the reload prunes
// resolved asks, which ends the poll.
let ask_poll_timer: ReturnType<typeof setInterval> | undefined;

function context_has_pending_asks(): boolean {
    const prefix = `${canvas_context?.key ?? current_context_key}|`;
    return [...pending_asks.keys()].some((key) => key.startsWith(prefix));
}

function ensure_pending_ask_poll(): void {
    if (ask_poll_timer !== undefined) {
        return;
    }
    ask_poll_timer = setInterval(() => {
        if (current_tab !== "canvas" || !context_has_pending_asks()) {
            if (ask_poll_timer !== undefined) {
                clearInterval(ask_poll_timer);
                ask_poll_timer = undefined;
            }
            return;
        }
        reload_canvas_content();
    }, 8000);
}

// The editor's decoration plugin asks which spans should shimmer.
function pending_ask_spans(): pear_canvas_editor.PendingAskSpans {
    const literals: string[] = [];
    const labeled = new Map<string, string>();
    const context_prefix = `${canvas_context?.key ?? current_context_key}|`;
    for (const [key, entry] of pending_asks) {
        if (!key.startsWith(context_prefix)) {
            continue;
        }
        literals.push(ask_span_text(entry));
        if (entry.label !== "") {
            labeled.set(entry.label, entry.request);
        }
    }
    return {literals, labeled};
}

function show_aside_popover(chip: HTMLElement): void {
    if (chip.getAttribute("data-pear-aside-open") === "true") {
        return;
    }
    const aside_id = chip.getAttribute("data-pear-aside") ?? "";
    const source = aside_sections.get(aside_id);
    if (source === undefined) {
        return;
    }
    const open = (html: string): void => {
        const $content = $("<div>").addClass("pear-aside-popover rendered_markdown").html(html);
        rendered_markdown.update_elements($content);
        chip.setAttribute("data-pear-aside-open", "true");
        // trigger: "manual" never auto-hides, so dismissal is ours:
        // any press outside the popover closes it (live report — an
        // undismissable popover walls off the canvas).
        let dismiss: ((event: MouseEvent) => void) | undefined;
        const instance = tippy.default(chip, {
            content: $content[0]!,
            trigger: "manual",
            interactive: true,
            appendTo: () => document.body,
            placement: "bottom-start",
            maxWidth: 460,
            theme: "pear-aside",
            onHidden(inst) {
                chip.removeAttribute("data-pear-aside-open");
                if (dismiss !== undefined) {
                    document.removeEventListener("mousedown", dismiss, {capture: true});
                }
                inst.destroy();
            },
        });
        dismiss = (event: MouseEvent): void => {
            const target = event.target;
            if (
                target instanceof Node &&
                (instance.popper.contains(target) || chip.contains(target))
            ) {
                return;
            }
            instance.hide();
        };
        document.addEventListener("mousedown", dismiss, {capture: true});
        instance.show();
    };
    const cache_key = `${aside_id}:${block_cache_key(source)}`;
    const cached = rendered_aside_cache.get(cache_key);
    if (cached !== undefined) {
        open(cached);
        return;
    }
    void channel.post({
        url: "/json/messages/render",
        data: {content: source},
        success(raw_data) {
            const data = message_render_response_schema.parse(raw_data);
            const html = postprocess_content(data.rendered);
            rendered_aside_cache.set(cache_key, html);
            open(html);
        },
    });
}

function is_asides_heading(source: string): boolean {
    return /^##\s+asides\s*$/i.test(source.trim());
}

function asides_start_index(): number {
    return canvas_blocks.findIndex((block) => is_asides_heading(block.source));
}

function rebuild_aside_sections(): void {
    aside_sections.clear();
    const start = asides_start_index();
    if (start === -1) {
        return;
    }
    let current_id: string | undefined;
    let current_lines: string[] = [];
    const flush = (): void => {
        if (current_id !== undefined && current_lines.length > 0) {
            aside_sections.set(current_id, current_lines.join("\n\n"));
        }
        current_lines = [];
    };
    for (const block of canvas_blocks.slice(start + 1)) {
        const [first_line = "", ...rest] = block.source.split("\n");
        const heading = /^####\s+(aside-\d+)\s*$/.exec(first_line.trim());
        if (heading?.[1] !== undefined) {
            flush();
            current_id = heading[1];
            const body = rest.join("\n").trim();
            if (body !== "") {
                current_lines.push(body);
            }
        } else if (/^#{1,6}\s/.test(first_line) && !is_asides_heading(block.source)) {
            // An ordinary heading ends the aside; only its own
            // answer belongs in the popover.
            flush();
            current_id = undefined;
        } else if (current_id !== undefined) {
            current_lines.push(block.source);
        }
    }
    flush();
}

function split_canvas_blocks(text: string): CanvasBlock[] {
    const lines = text.replaceAll("\r\n", "\n").split("\n");
    const groups: string[][] = [];
    let current: string[] = [];
    let in_fence = false;
    for (const line of lines) {
        if (/^\s{0,3}(?:```|~~~)/.test(line)) {
            in_fence = !in_fence;
        }
        if (!in_fence && line.trim() === "" && !/^\s{0,3}(?:```|~~~)/.test(line)) {
            if (current.length > 0) {
                groups.push(current);
                current = [];
            }
            continue;
        }
        if (!in_fence && /^#{1,6}\s/.test(line) && current.length > 0) {
            groups.push(current);
            current = [];
        }
        current.push(line);
    }
    if (current.length > 0) {
        groups.push(current);
    }
    return groups.map((group) => ({source: group.join("\n").trim()}));
}

function join_canvas_blocks(blocks: CanvasBlock[]): string {
    const sources = blocks.map((block) => block.source.trim()).filter((source) => source !== "");
    return sources.length > 0 ? sources.join("\n\n") + "\n" : "";
}

function block_cache_key(source: string): string {
    let hash = 5381;
    for (const char of source) {
        hash = (hash * 33 + (char.codePointAt(0) ?? 0)) % 4_294_967_296;
    }
    return `${hash}:${source.length}`;
}

function set_canvas_status(text: string, failed = false): void {
    const $status = $pane().find(".pear-canvas-status");
    $status.text(text);
    $status.toggleClass("pear-canvas-status-failed", failed);
}

function unescape_ask_brackets(markdown: string): string {
    // remark-stringify conservatively escapes "[" in prose, which
    // would break ask spans and the agent's span-replacement
    // contract; the canvas dialect treats brackets as literal.
    let in_fence = false;
    return markdown
        .split("\n")
        .map((line) => {
            if (/^\s{0,3}(?:```|~~~)/.test(line)) {
                in_fence = !in_fence;
            }
            if (in_fence) {
                return line;
            }
            return line.replaceAll("\\[", "[").replaceAll("\\]", "]");
        })
        .join("\n");
}

function current_editor_content(): string | undefined {
    const markdown = pear_canvas_editor.get_markdown();
    return markdown === undefined ? undefined : unescape_ask_brackets(markdown);
}

let last_saved_content = "";
let canvas_dirty = false;
let canvas_save_timer: ReturnType<typeof setTimeout> | undefined;
// After we replace the editor's content programmatically (remote
// refresh, conflict merge), the listener reports the reserialized
// form once; adopt it as the baseline instead of treating it as an
// edit.
let adopt_baseline_until = 0;

function handle_markdown_updated(markdown: string): void {
    if (!canvas_loaded) {
        return;
    }
    const content = unescape_ask_brackets(markdown);
    if (Date.now() < adopt_baseline_until) {
        adopt_baseline_until = 0;
        last_saved_content = content;
        canvas_dirty = false;
        return;
    }
    if (content === last_saved_content) {
        canvas_dirty = false;
        return;
    }
    canvas_dirty = true;
    if (canvas_save_timer !== undefined) {
        clearTimeout(canvas_save_timer);
    }
    canvas_save_timer = setTimeout(() => {
        canvas_save_timer = undefined;
        save_canvas_document();
    }, 2500);
}

// Shift+Enter is the "set" gesture: any bracketed ask in the
// document that is not already running goes to the agent, once the
// save that carries it lands.
function handle_set_gesture(): void {
    const content = current_editor_content();
    if (content === undefined || canvas_context === undefined) {
        return;
    }
    for (const block of split_canvas_blocks(content)) {
        for (const ask of parse_asks_from_source(block.source)) {
            if (
                !pending_asks.has(ask_key(ask)) &&
                !ask_dispatch_queue.some((queued) => ask_key(queued) === ask_key(ask))
            ) {
                ask_dispatch_queue.push(ask);
            }
        }
    }
    flush_canvas_save();
}

function apply_remote_canvas_content(content: string): void {
    canvas_blocks = split_canvas_blocks(content);
    rebuild_aside_sections();
    prune_pending_asks();
    last_saved_content = content;
    canvas_dirty = false;
    adopt_baseline_until = Date.now() + 500;
    pear_canvas_editor.set_markdown(content);
    pear_canvas_editor.refresh_decorations();
}

function adopt_canvas_content(content: string, version: string): void {
    canvas_version = version;
    canvas_blocks = split_canvas_blocks(content);
    rebuild_aside_sections();
    prune_pending_asks();
    last_saved_content = content;
    canvas_dirty = false;
    if (context_has_pending_asks()) {
        ensure_pending_ask_poll();
    }
}

function mount_canvas_editor(content: string, context_key_at_request: string): void {
    const body = $pane().find(".pear-canvas-body")[0];
    if (!(body instanceof HTMLElement)) {
        return;
    }
    void (async () => {
        try {
            const mounted = await pear_canvas_editor.mount(body, {
                markdown: content,
                placeholder: $t({
                    defaultMessage: "Write, or [ask Pear anything] and press Shift+Enter",
                }),
                on_markdown_updated: handle_markdown_updated,
                on_set_gesture: handle_set_gesture,
                get_pending_spans: pending_ask_spans,
            });
            if (mounted && canvas_context?.key === context_key_at_request) {
                canvas_loaded = true;
                set_canvas_status("");
                // Whatever we painted from may be stale; reconcile.
                reload_canvas_content();
            }
        } catch {
            set_canvas_status($t({defaultMessage: "could not load"}), true);
        }
    })();
}

function load_canvas(): void {
    if (current_stream === undefined || current_topic === undefined) {
        return;
    }
    canvas_loaded = false;
    const request_context = {
        stream: current_stream,
        topic: current_topic,
        key: current_context_key,
    };
    canvas_context = request_context;
    const cached = canvas_content_cache.get(request_context.key);
    if (cached !== undefined) {
        // Instant paint from the last known file; the post-mount
        // reconcile fetch brings in anything newer.
        adopt_canvas_content(cached.content, cached.version);
        mount_canvas_editor(cached.content, request_context.key);
        return;
    }
    set_canvas_status($t({defaultMessage: "loading"}));
    void channel.get({
        url: "/json/pear/canvas",
        data: {stream: request_context.stream, topic: request_context.topic},
        success(raw_data) {
            const parsed = canvas_response_schema.safeParse(raw_data);
            if (
                !parsed.success ||
                current_tab !== "canvas" ||
                canvas_context?.key !== request_context.key
            ) {
                return;
            }
            const content = parsed.data.pear_canvas.content;
            remember_canvas_content(
                request_context.key,
                content,
                parsed.data.pear_canvas.version,
            );
            adopt_canvas_content(content, parsed.data.pear_canvas.version);
            mount_canvas_editor(content, request_context.key);
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                set_canvas_status($t({defaultMessage: "could not load"}), true);
            }
        },
    });
}

function save_canvas_document(retrying = false): void {
    if (canvas_context === undefined || !canvas_loaded) {
        return;
    }
    const save_context = canvas_context;
    const content = current_editor_content() ?? last_saved_content;
    if (!pear_canvas.content_within_byte_limit(content, 200_000)) {
        set_canvas_status($t({defaultMessage: "too large to save"}), true);
        return;
    }
    set_canvas_status($t({defaultMessage: "saving"}));
    void channel.put({
        url: "/json/pear/canvas",
        data: {
            stream: save_context.stream,
            topic: save_context.topic,
            content,
            base_version: canvas_version,
        },
        success(raw_data) {
            const parsed = canvas_response_schema.safeParse(raw_data);
            if (parsed.success) {
                canvas_version = parsed.data.pear_canvas.version;
            }
            last_saved_content = content;
            canvas_dirty = false;
            canvas_blocks = split_canvas_blocks(content);
            rebuild_aside_sections();
            remember_canvas_content(save_context.key, content, canvas_version);
            set_canvas_status($t({defaultMessage: "saved"}));
            // Asks dispatch only once their span is durably in the
            // file, so the agent always finds what it must replace.
            dispatch_queued_asks();
        },
        error(xhr) {
            if (xhr.status === 409 && !retrying) {
                // Someone (often the agent) changed the file:
                // paragraph-level rebase — fresh document first, any
                // novel local blocks appended, then one retry.
                void channel.get({
                    url: "/json/pear/canvas",
                    data: {stream: save_context.stream, topic: save_context.topic},
                    success(raw_fresh) {
                        const fresh = canvas_response_schema.safeParse(raw_fresh);
                        if (!fresh.success) {
                            set_canvas_status($t({defaultMessage: "could not save"}), true);
                            return;
                        }
                        canvas_version = fresh.data.pear_canvas.version;
                        const merged = split_canvas_blocks(fresh.data.pear_canvas.content);
                        for (const block of split_canvas_blocks(content)) {
                            if (!merged.some((entry) => entry.source === block.source)) {
                                merged.push(block);
                            }
                        }
                        apply_remote_canvas_content(join_canvas_blocks(merged));
                        set_canvas_status($t({defaultMessage: "merged remote changes"}));
                        save_canvas_document(true);
                    },
                    error() {
                        set_canvas_status($t({defaultMessage: "could not save"}), true);
                    },
                });
                return;
            }
            if (xhr.readyState !== 0) {
                set_canvas_status($t({defaultMessage: "could not save"}), true);
            }
        },
    });
}

function flush_canvas_save(): void {
    if (canvas_save_timer !== undefined) {
        clearTimeout(canvas_save_timer);
        canvas_save_timer = undefined;
    }
    if (canvas_dirty) {
        save_canvas_document();
    } else {
        dispatch_queued_asks();
    }
}

function reload_canvas_content(): void {
    if (canvas_context === undefined || !canvas_loaded) {
        return;
    }
    const request_context = canvas_context;
    void channel.get({
        url: "/json/pear/canvas",
        data: {stream: request_context.stream, topic: request_context.topic},
        success(raw_data) {
            const parsed = canvas_response_schema.safeParse(raw_data);
            if (
                !parsed.success ||
                current_tab !== "canvas" ||
                canvas_context?.key !== request_context.key ||
                canvas_dirty
            ) {
                return;
            }
            canvas_version = parsed.data.pear_canvas.version;
            remember_canvas_content(
                request_context.key,
                parsed.data.pear_canvas.content,
                parsed.data.pear_canvas.version,
            );
            if (parsed.data.pear_canvas.content !== last_saved_content) {
                apply_remote_canvas_content(parsed.data.pear_canvas.content);
            }
        },
        error() {
            // A missed refresh self-heals on the next settle.
        },
    });
}

// The panel's refresh cycle tells us about run activity; when a run
// settles (an ask or any agent edit may have rewritten the file),
// refresh the canvas — unless the user has unsaved edits.
let last_run_active = false;

export function refresh_canvas_if_open_on_settle(run_active: boolean): void {
    const settled = last_run_active && !run_active;
    last_run_active = run_active;
    if (settled && current_tab === "canvas" && !canvas_dirty) {
        reload_canvas_content();
    }
}

// ---------------- Wiring ----------------

export function initialize(): void {
    if (typeof document === "undefined") {
        return;
    }
    $("body").on("click", ".pear-workspace-tab", function (this: HTMLElement, event) {
        event.preventDefault();
        event.stopPropagation();
        const tab = $(this).attr("data-pear-tab");
        if (tab === "chat" || tab === "canvas" || tab === "files") {
            set_tab(tab);
        }
    });
    $("body").on("click", ".pear-files-download", function (this: HTMLElement, event) {
        event.preventDefault();
        event.stopPropagation();
        const path = $(this).attr("data-pear-file");
        if (path !== undefined) {
            download_file(path);
        }
    });
    $("body").on("click", ".pear-files-row-canvas", (event) => {
        if ($(event.target).closest("button").length > 0) {
            return;
        }
        event.preventDefault();
        set_tab("canvas");
    });
    $("body").on("click", ".pear-files-row", function (this: HTMLElement, event) {
        if (
            $(this).hasClass("pear-files-row-canvas") ||
            $(event.target).closest("button").length > 0
        ) {
            return;
        }
        const path = $(this).attr("data-pear-file");
        if (path !== undefined) {
            show_file_preview(path);
        }
    });
    $("body").on("click", ".pear-files-dir", function (this: HTMLElement) {
        const dir = $(this).attr("data-pear-dir");
        if (dir === undefined || last_files_payload === undefined) {
            return;
        }
        if (files_collapsed_dirs.has(dir)) {
            files_collapsed_dirs.delete(dir);
        } else {
            files_collapsed_dirs.add(dir);
        }
        render_files_tree(
            last_files_payload.files,
            last_files_payload.canvas_path,
            last_files_payload.truncated,
        );
    });
    $("body").on("click", ".pear-files-back", () => {
        if (last_files_payload !== undefined) {
            render_files_tree(
                last_files_payload.files,
                last_files_payload.canvas_path,
                last_files_payload.truncated,
            );
        } else {
            load_files();
        }
    });
    $("body").on("click", ".pear-files-copy", function (this: HTMLElement, event) {
        event.preventDefault();
        event.stopPropagation();
        const path = $(this).attr("data-pear-file");
        if (path !== undefined) {
            void navigator.clipboard.writeText(path);
            set_canvas_status($t({defaultMessage: "path copied"}));
        }
    });
    $("body").on("click", ".pear-aside-chip", function (this: HTMLElement, event) {
        // The aside answer opens as a popover, not a navigation.
        event.preventDefault();
        event.stopPropagation();
        show_aside_popover(this);
    });
    // ⌘1/2/3 switch tabs from anywhere in the app.
    document.addEventListener(
        "keydown",
        (event) => {
            if (
                (event.metaKey || event.ctrlKey) &&
                !event.shiftKey &&
                !event.altKey &&
                has_workspace &&
                ["1", "2", "3"].includes(event.key)
            ) {
                event.preventDefault();
                event.stopPropagation();
                set_tab((["chat", "canvas", "files"] as const)[Number(event.key) - 1]!);
            }
        },
        true,
    );
    // The compose console spans tabs: with the pane open, a plain
    // Enter-send returns to Chat to watch the message land; ⌘Enter
    // sends and stays (design: "queue it on chat async").
    document.addEventListener(
        "keydown",
        (event) => {
            if (!pane_is_open() || event.key !== "Enter" || event.shiftKey) {
                return;
            }
            const target = event.target;
            if (!(target instanceof Element) || target.id !== "compose-textarea") {
                return;
            }
            const sends = event.metaKey || event.ctrlKey || user_settings.enter_sends;
            if (!sends) {
                return;
            }
            if (!(event.metaKey || event.ctrlKey)) {
                set_tab("chat");
            }
        },
        true,
    );
}
