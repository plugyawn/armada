// The workspace tabs (design: pear/PEAR_CANVAS_TABS_DESIGN.md, T1).
// Chat is the message feed; Canvas is CANVAS.md made comfortable;
// Files is the worktree made visible. One strip, rendered in the
// recipient bar and mirrored at the top of the pane, switches between
// them without leaving the narrow.

import $ from "jquery";
import * as z from "zod/mini";

import render_pear_workspace_pane from "../templates/pear_workspace_pane.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as pear_canvas from "./pear_canvas.ts";
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
let canvas_version = "";
let canvas_dirty = false;
let canvas_save_timer: ReturnType<typeof setTimeout> | undefined;

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
    $feed.after('<div id="pear-workspace-pane" hidden></div>');
    $existing = $pane();
    return $existing;
}

export function pane_is_open(): boolean {
    return current_tab !== "chat";
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
        void load_canvas();
    } else if (current_tab === "files") {
        void load_files();
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
export function sync(opts: {
    stream: string | undefined;
    topic: string | undefined;
    has_workspace: boolean;
}): void {
    const key = context_key(opts.stream, opts.topic);
    const context_changed = key !== current_context_key;
    current_context_key = key;
    current_stream = opts.stream;
    current_topic = opts.topic;
    has_workspace = opts.has_workspace;
    if (context_changed && pane_is_open()) {
        // A new narrow always lands on Chat.
        current_tab = "chat";
        hide_pane();
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
    const ordered = [...files].sort((a, b) => {
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

async function load_files(): Promise<void> {
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

// ---------------- Canvas ----------------

function set_canvas_status(text: string, failed = false): void {
    const $status = $pane().find(".pear-canvas-status");
    $status.text(text);
    $status.toggleClass("pear-canvas-status-failed", failed);
}

async function load_canvas(): Promise<void> {
    if (current_stream === undefined || current_topic === undefined) {
        return;
    }
    set_canvas_status($t({defaultMessage: "loading"}));
    void channel.get({
        url: "/json/pear/canvas",
        data: {stream: current_stream, topic: current_topic},
        success(raw_data) {
            const parsed = canvas_response_schema.safeParse(raw_data);
            if (!parsed.success || current_tab !== "canvas") {
                return;
            }
            canvas_version = parsed.data.pear_canvas.version;
            canvas_dirty = false;
            const $editor = $pane().find(".pear-canvas-editor");
            $editor.val(parsed.data.pear_canvas.content);
            set_canvas_status("");
            $editor.trigger("focus");
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                set_canvas_status($t({defaultMessage: "could not load"}), true);
            }
        },
    });
}

function save_canvas(): void {
    if (current_stream === undefined || current_topic === undefined || !canvas_dirty) {
        return;
    }
    const content = String($pane().find(".pear-canvas-editor").val() ?? "");
    if (!pear_canvas.content_within_byte_limit(content, 200_000)) {
        set_canvas_status($t({defaultMessage: "too large to save"}), true);
        return;
    }
    canvas_dirty = false;
    set_canvas_status($t({defaultMessage: "saving"}));
    void channel.put({
        url: "/json/pear/canvas",
        data: {
            stream: current_stream,
            topic: current_topic,
            content,
            base_version: canvas_version,
        },
        success(raw_data) {
            const parsed = canvas_response_schema.safeParse(raw_data);
            if (parsed.success) {
                canvas_version = parsed.data.pear_canvas.version;
            }
            set_canvas_status($t({defaultMessage: "saved"}));
        },
        error(xhr) {
            canvas_dirty = true;
            if (xhr.status === 409 || xhr.responseText.includes("version")) {
                set_canvas_status(
                    $t({defaultMessage: "changed elsewhere — copy your text and reload"}),
                    true,
                );
                return;
            }
            if (xhr.readyState !== 0) {
                set_canvas_status($t({defaultMessage: "could not save"}), true);
            }
        },
    });
}

function schedule_canvas_save(): void {
    canvas_dirty = true;
    set_canvas_status($t({defaultMessage: "editing"}));
    if (canvas_save_timer !== undefined) {
        clearTimeout(canvas_save_timer);
    }
    canvas_save_timer = setTimeout(() => {
        canvas_save_timer = undefined;
        save_canvas();
    }, 900);
}

function flush_canvas_save(): void {
    if (canvas_save_timer !== undefined) {
        clearTimeout(canvas_save_timer);
        canvas_save_timer = undefined;
    }
    save_canvas();
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
    $("body").on("input", ".pear-canvas-editor", () => {
        schedule_canvas_save();
    });
    // Keys inside the pane never leak to the feed's hotkeys; Esc
    // returns to Chat.
    document.addEventListener(
        "keydown",
        (event) => {
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
