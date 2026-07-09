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
import {postprocess_content} from "./postprocess_content.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import {user_settings} from "./user_settings.ts";

export type PearWorkspaceTab = "chat" | "canvas" | "files";

const workspace_files_schema = z.object({
    pear_workspace_files: z.object({
        files: z.array(z.object({path: z.string(), status: z.string()})),
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

function render_pane_shell(): void {
    const $container = ensure_pane();
    if ($container.length === 0) {
        return;
    }
    $container.html(
        render_pear_workspace_pane({
            context_label: `${current_stream ?? ""} › ${current_topic ?? ""}`,
            is_canvas: current_tab === "canvas",
            is_files: current_tab === "files",
        }),
    );
}

function show_pane(): void {
    render_pane_shell();
    $("#message_feed_container").hide();
    $pane().removeAttr("hidden");
    if (current_tab === "canvas") {
        load_canvas();
    } else if (current_tab === "files") {
        load_files();
    }
}

function hide_pane(): void {
    flush_canvas_save();
    $pane().attr("hidden", "hidden");
    $("#message_feed_container").show();
}

export function set_tab(tab: PearWorkspaceTab): void {
    if (tab === current_tab) {
        return;
    }
    if (tab !== "chat" && !has_workspace) {
        return;
    }
    const was_open = pane_is_open();
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
    } else if (was_open) {
        flush_canvas_save();
        show_pane();
    } else {
        show_pane();
    }
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
        // still points at the topic it was loaded from.
        flush_canvas_save();
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
    update_tab_strips();
}

// ---------------- Files ----------------

type FileEntry = {path: string; status: string};

function render_files_tree(
    files: FileEntry[],
    canvas_path: string | null,
    truncated: boolean,
): void {
    const $body = $pane().find(".pear-workspace-pane-body");
    if ($body.length === 0) {
        return;
    }
    if (files.length === 0) {
        $body.html(
            `<div class="pear-files-empty">${$t({
                defaultMessage:
                    "This branch has no files yet — ask Pear to create some, or open the Canvas.",
            })}</div>`,
        );
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
    for (const file of ordered) {
        const parts = file.path.split("/");
        for (let depth = 0; depth < parts.length - 1; depth += 1) {
            const dir = parts.slice(0, depth + 1).join("/");
            if (!seen_dirs.has(dir)) {
                seen_dirs.add(dir);
                const $dir = $("<li>").addClass("pear-files-dir");
                $dir.css("--pear-file-depth", `${depth}`);
                $dir.append(
                    $("<i>").addClass("zulip-icon zulip-icon-triangle-down pear-files-dir-icon"),
                    $("<span>").addClass("pear-files-name").text(parts[depth]!),
                );
                $list.append($dir);
            }
        }
        const depth = parts.length - 1;
        const is_canvas = file.path === canvas_path;
        const $row = $("<li>")
            .addClass("pear-files-row")
            .attr("data-pear-file", file.path)
            .toggleClass("pear-files-row-canvas", is_canvas)
            .toggleClass("pear-files-row-untracked", file.status === "untracked");
        $row.css("--pear-file-depth", `${depth}`);
        const $name = $("<span>").addClass("pear-files-name").text(parts[depth]!);
        if (file.status === "changed") {
            $name.append($("<span>").addClass("pear-files-dirty").text("*"));
        }
        const $meta = $("<span>")
            .addClass("pear-files-meta")
            .text(
                is_canvas
                    ? $t({defaultMessage: "canvas"})
                    : file.status === "clean"
                      ? ""
                      : file.status,
            );
        const $download = $("<button>")
            .attr("type", "button")
            .addClass("pear-files-download")
            .attr("data-pear-file", file.path)
            .attr("aria-label", $t({defaultMessage: "Download"}))
            .append($("<i>").addClass("zulip-icon zulip-icon-arrow-down"));
        $row.append($name, $meta, $download);
        $list.append($row);
    }
    $body.empty().append($list);
    if (truncated) {
        $body.append(
            $("<div>")
                .addClass("pear-files-truncated")
                .text($t({defaultMessage: "Only the first 500 files are shown."})),
        );
    }
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
// The topic whose file is loaded. Saves, asks, and refetches bind to
// this, never to current_stream/current_topic: the narrow can change
// while an editor is open, and a canvas must only ever write to the
// topic it was loaded from (live report: a narrow switch mid-edit
// wrote one topic's canvas into another).
let canvas_context: {stream: string; topic: string; key: string} | undefined;
const rendered_block_cache = new Map<string, string>();
let canvas_render_generation = 0;

// ---------------- Asks ----------------
// A bracketed span in a committed block is a request to the agent:
// "[request](label)" resolves into a popover aside; bare "[request]"
// resolves in place. Real links, checkboxes, footnotes, and images
// keep their markdown meaning (PEAR_CANVAS_TABS_DESIGN.md §B).

type CanvasAsk = {request: string; label: string};

const PEAR_ASK_PENDING_TTL_MS = 10 * 60 * 1000;
// Keyed by `${context}|${request}|${label}` → dispatch timestamp.
const pending_asks = new Map<string, number>();
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

function queue_new_asks(previous_source: string, committed_source: string): void {
    const previous = new Set(
        parse_asks_from_source(previous_source).map((ask) => ask_span_text(ask)),
    );
    for (const ask of parse_asks_from_source(committed_source)) {
        if (previous.has(ask_span_text(ask)) || pending_asks.has(ask_key(ask))) {
            continue;
        }
        ask_dispatch_queue.push(ask);
    }
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
        pending_asks.set(ask_key(ask), Date.now());
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
                redecorate_pending_asks();
            },
            error(xhr) {
                pending_asks.delete(ask_key(ask));
                redecorate_pending_asks();
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
    for (const [key, started_at] of pending_asks) {
        if (!key.startsWith(context_prefix)) {
            continue;
        }
        const [, request = "", label = ""] = key.split("|");
        const span = ask_span_text({request, label});
        if (!document_text.includes(span) || now - started_at > PEAR_ASK_PENDING_TTL_MS) {
            pending_asks.delete(key);
        }
    }
}

function wrap_pending_ask_text_nodes(root: HTMLElement, span_text: string): void {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const matches: Text[] = [];
    let node = walker.nextNode();
    while (node !== null) {
        if (
            node instanceof Text &&
            node.textContent !== null &&
            node.textContent.includes(span_text) &&
            !(node.parentElement?.closest(".pear-ask-pending, code, a") instanceof HTMLElement)
        ) {
            matches.push(node);
        }
        node = walker.nextNode();
    }
    for (const text_node of matches) {
        const text = text_node.textContent ?? "";
        const start = text.indexOf(span_text);
        const target = text_node.splitText(start);
        target.splitText(span_text.length);
        const $wrapped = $("<span>")
            .addClass("pear-ask-pending")
            .attr("title", $t({defaultMessage: "Pear is working on this ask"}))
            .text(span_text);
        $(target).replaceWith($wrapped);
    }
}

function decorate_block_asks($target: JQuery, source: string): void {
    // Resolved asides first: rewrite #aside-N links into popover chips.
    $target.find("a").each(function (this: HTMLElement) {
        const href = $(this).attr("href") ?? "";
        const aside_match = /#(aside-\d+)$/.exec(href);
        if (aside_match?.[1] !== undefined && aside_sections.has(aside_match[1])) {
            $(this)
                .addClass("pear-aside-chip")
                .attr("data-pear-aside", aside_match[1])
                .removeAttr("target")
                .removeAttr("rel");
        }
    });
    // Then pending asks: shimmer across the bracket interior.
    const root = $target[0];
    if (!(root instanceof HTMLElement)) {
        return;
    }
    for (const ask of parse_asks_from_source(source)) {
        if (!pending_asks.has(ask_key(ask))) {
            continue;
        }
        const span_text = ask_span_text(ask);
        wrap_pending_ask_text_nodes(root, span_text);
        // Labeled asks may render as an anchor when markdown treats
        // the label as a relative href; mark that shape too.
        $target.find("a").each(function (this: HTMLElement) {
            if (($(this).attr("href") ?? "") === ask.label && $(this).text() === ask.request) {
                $(this).addClass("pear-ask-pending");
            }
        });
    }
}

function redecorate_pending_asks(): void {
    for (const [index, block] of canvas_blocks.entries()) {
        const $block = $pane().find(`.pear-canvas-block[data-block-index="${index}"]`);
        const $target = $block.find(".pear-canvas-block-rendered");
        if ($target.length > 0) {
            $target.find(".pear-ask-pending").each(function (this: HTMLElement) {
                this.replaceWith(document.createTextNode($(this).text()));
            });
            decorate_block_asks($target, block.source);
        }
    }
}

// ---------------- Asides ----------------
// The agent persists popover answers under a trailing "## Asides"
// section ("#### aside-N" per answer). Those blocks stay in the
// document but never render in the block list; their content
// surfaces through the chips.

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
                inst.destroy();
            },
        });
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

function render_block_markdown(index: number, source: string): void {
    const key = block_cache_key(source);
    const cached = rendered_block_cache.get(key);
    const generation = canvas_render_generation;
    const apply = (html: string): void => {
        if (generation !== canvas_render_generation) {
            return;
        }
        const $block = $pane().find(`.pear-canvas-block[data-block-index="${index}"]`);
        const $target = $block.find(".pear-canvas-block-rendered");
        if ($target.length === 0) {
            return;
        }
        $target.html(html);
        rendered_markdown.update_elements($target);
        decorate_block_asks($target, source);
    };
    if (cached !== undefined) {
        apply(cached);
        return;
    }
    void channel.post({
        url: "/json/messages/render",
        data: {content: source},
        success(raw_data) {
            const data = message_render_response_schema.parse(raw_data);
            const html = postprocess_content(data.rendered);
            rendered_block_cache.set(key, html);
            apply(html);
        },
        error() {
            const $block = $pane().find(`.pear-canvas-block[data-block-index="${index}"]`);
            $block.find(".pear-canvas-block-rendered").text(source);
        },
    });
}

function build_canvas_dom(): void {
    const $body = $pane().find(".pear-canvas-body");
    if ($body.length === 0) {
        return;
    }
    canvas_render_generation += 1;
    rebuild_aside_sections();
    const asides_start = asides_start_index();
    const $list = $("<div>").addClass("pear-canvas-blocks");
    for (const index of canvas_blocks.keys()) {
        if (asides_start !== -1 && index >= asides_start) {
            // Agent-managed aside storage; surfaces through chips.
            continue;
        }
        $list.append(
            $("<button>")
                .attr("type", "button")
                .addClass("pear-canvas-seam")
                .attr("data-seam-index", `${index}`)
                .attr("aria-label", $t({defaultMessage: "Insert a block"}))
                .append($("<span>").addClass("pear-canvas-seam-plus").text("+")),
        );
        const $block = $("<div>")
            .addClass("pear-canvas-block")
            .attr("data-block-index", `${index}`);
        $block.append($("<div>").addClass("pear-canvas-block-gutter"));
        $block.append($("<div>").addClass("pear-canvas-block-rendered rendered_markdown"));
        $list.append($block);
    }
    const $ghost = $("<div>")
        .addClass("pear-canvas-block pear-canvas-block-ghost")
        .attr("data-block-index", `${canvas_blocks.length}`)
        .append($("<div>").addClass("pear-canvas-block-gutter"))
        .append(
            $("<div>")
                .addClass("pear-canvas-ghost-prompt")
                .text($t({defaultMessage: "write…"})),
        );
    $list.append($ghost);
    $body.empty().append($list);
    for (const [index, block] of canvas_blocks.entries()) {
        if (asides_start !== -1 && index >= asides_start) {
            continue;
        }
        render_block_markdown(index, block.source);
    }
}

function open_block_editor(index: number, initial_source?: string): void {
    commit_open_block();
    const is_new = index >= canvas_blocks.length;
    const source = initial_source ?? (is_new ? "" : (canvas_blocks[index]?.source ?? ""));
    const $block = $pane().find(`.pear-canvas-block[data-block-index="${index}"]`);
    if ($block.length === 0) {
        return;
    }
    $block.addClass("pear-canvas-block-editing").removeClass("pear-canvas-block-ghost");
    const $area = $("<textarea>")
        .addClass("pear-canvas-block-source")
        .attr("spellcheck", "true")
        .attr(
            "placeholder",
            $t({defaultMessage: "Write markdown — [ask Pear anything] hands that span to the agent"}),
        )
        .val(source);
    $block.find(".pear-canvas-block-rendered, .pear-canvas-ghost-prompt").remove();
    $block.append($area);
    autosize_block_editor($area);
    $area.trigger("focus");
    const el = $area[0];
    if (el instanceof HTMLTextAreaElement) {
        el.setSelectionRange(el.value.length, el.value.length);
    }
}

function autosize_block_editor($area: JQuery): void {
    const el = $area[0];
    if (!(el instanceof HTMLTextAreaElement)) {
        return;
    }
    el.style.height = "auto";
    el.style.height = `${Math.max(el.scrollHeight + 2, 44)}px`;
}

function commit_open_block(options: {cancel?: boolean} = {}): void {
    const $editing = $pane().find(".pear-canvas-block-editing");
    if ($editing.length === 0) {
        return;
    }
    const index = Number.parseInt($editing.attr("data-block-index") ?? "", 10);
    const $area = $editing.find(".pear-canvas-block-source");
    const source = String($area.val() ?? "").trim();
    if (options.cancel === true) {
        build_canvas_dom();
        return;
    }
    if (Number.isNaN(index)) {
        return;
    }
    const previous_source = index < canvas_blocks.length ? (canvas_blocks[index]?.source ?? "") : "";
    const next_blocks = [...canvas_blocks];
    let committed_index = index;
    if (index >= next_blocks.length) {
        if (source !== "") {
            // New prose lands before the agent-managed asides tail.
            const asides_start = asides_start_index();
            committed_index = asides_start === -1 ? next_blocks.length : asides_start;
            next_blocks.splice(committed_index, 0, {source});
        }
    } else if (source === "") {
        next_blocks.splice(index, 1);
    } else {
        next_blocks[index] = {source};
    }
    const changed = join_canvas_blocks(next_blocks) !== join_canvas_blocks(canvas_blocks);
    canvas_blocks = next_blocks;
    if (changed && source !== "") {
        queue_new_asks(previous_source, source);
    }
    build_canvas_dom();
    if (changed) {
        const $committed = $pane().find(`.pear-canvas-block[data-block-index="${committed_index}"]`);
        $committed.addClass("pear-canvas-block-settle");
        save_canvas_document();
    }
}

function insert_block_at(index: number): void {
    commit_open_block();
    canvas_blocks.splice(index, 0, {source: ""});
    build_canvas_dom();
    open_block_editor(index, "");
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
            canvas_version = parsed.data.pear_canvas.version;
            canvas_blocks = split_canvas_blocks(parsed.data.pear_canvas.content);
            canvas_loaded = true;
            prune_pending_asks();
            set_canvas_status("");
            build_canvas_dom();
            $pane().find(".pear-workspace-pane-body").trigger("focus");
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
    const content = join_canvas_blocks(canvas_blocks);
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
            set_canvas_status($t({defaultMessage: "saved"}));
            // Asks dispatch only once their span is durably in the
            // file, so the agent always finds what it must replace.
            dispatch_queued_asks();
        },
        error(xhr) {
            if (xhr.status === 409 && !retrying) {
                // Someone (often the agent) changed the file. Block-
                // level rebase: refetch, re-split, and replay this
                // document's block set only if the fresh doc doesn't
                // collide with it; otherwise adopt the fresh doc.
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
                        const fresh_blocks = split_canvas_blocks(fresh.data.pear_canvas.content);
                        const mine = new Set(canvas_blocks.map((block) => block.source));
                        const merged = [...fresh_blocks];
                        for (const block of canvas_blocks) {
                            if (!merged.some((entry) => entry.source === block.source)) {
                                merged.push(block);
                            }
                        }
                        void mine;
                        canvas_blocks = merged;
                        build_canvas_dom();
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
    commit_open_block();
}

// The panel's refresh cycle tells us about run activity; when a run
// settles (an ask or any agent edit may have rewritten the file),
// reload the canvas — unless a block is mid-edit.
let last_run_active = false;

export function refresh_canvas_if_open_on_settle(run_active: boolean): void {
    const settled = last_run_active && !run_active;
    last_run_active = run_active;
    if (
        settled &&
        current_tab === "canvas" &&
        $pane().find(".pear-canvas-block-editing").length === 0
    ) {
        load_canvas();
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
        if ($(event.target).closest(".pear-files-download").length > 0) {
            return;
        }
        event.preventDefault();
        set_tab("canvas");
    });
    $("body").on("click", ".pear-aside-chip", function (this: HTMLElement, event) {
        // The aside answer opens as a popover, not a navigation.
        event.preventDefault();
        event.stopPropagation();
        show_aside_popover(this);
    });
    $("body").on("click", ".pear-canvas-block-rendered", function (this: HTMLElement, event) {
        // Links and interactive markdown inside a block still work.
        if ($(event.target).closest("a, button, .spoiler-header").length > 0) {
            return;
        }
        const index = Number.parseInt(
            $(this).closest(".pear-canvas-block").attr("data-block-index") ?? "",
            10,
        );
        if (!Number.isNaN(index)) {
            open_block_editor(index);
        }
    });
    $("body").on("click", ".pear-canvas-block-ghost", function (this: HTMLElement) {
        const index = Number.parseInt($(this).attr("data-block-index") ?? "", 10);
        if (!Number.isNaN(index)) {
            open_block_editor(index, "");
        }
    });
    $("body").on("click", ".pear-canvas-seam", function (this: HTMLElement, event) {
        event.preventDefault();
        const index = Number.parseInt($(this).attr("data-seam-index") ?? "", 10);
        if (!Number.isNaN(index)) {
            insert_block_at(index);
        }
    });
    $("body").on("input", ".pear-canvas-block-source", function (this: HTMLElement) {
        autosize_block_editor($(this));
    });
    $("body").on("keydown", ".pear-canvas-block-source", (event) => {
        if (event.key === "Enter" && event.shiftKey) {
            // The notebook gesture: commit the cell.
            event.preventDefault();
            event.stopPropagation();
            commit_open_block();
        } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            commit_open_block({cancel: true});
        }
    });
    $("body").on("blur", ".pear-canvas-block-source", () => {
        // Clicking away commits too — autosave spirit; Esc is the
        // explicit cancel.
        setTimeout(() => {
            const editing = document.activeElement?.closest(".pear-canvas-block-editing");
            if (!editing) {
                commit_open_block();
            }
        }, 0);
    });
    // Keys inside the pane never leak to the feed's hotkeys; Esc
    // returns to Chat.
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
                return;
            }
            if (!pane_is_open()) {
                return;
            }
            const target = event.target;
            const inside_pane =
                target instanceof Element && target.closest("#pear-workspace-pane") !== null;
            if (event.key === "Escape" && inside_pane) {
                event.preventDefault();
                event.stopPropagation();
                set_tab("chat");
                return;
            }
            if (inside_pane) {
                event.stopPropagation();
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
