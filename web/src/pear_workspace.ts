import $ from "jquery";

import render_pear_workspace_pane from "../templates/pear_workspace_pane.hbs";

import * as blueslip from "./blueslip.ts";

export type PearWorkspaceTab = "chat" | "workspace";

type WorkspaceShellController = {
    ready: Promise<void>;
    refresh: () => void;
    destroy: () => Promise<void>;
};

let current_tab: PearWorkspaceTab = "chat";
let current_context_key = "";
let current_stream: string | undefined;
let current_topic: string | undefined;
let current_workspace_id: string | undefined;
let pane_shell_key: string | undefined;
let shell: WorkspaceShellController | undefined;
let shell_generation = 0;
let shell_teardown: Promise<void> = Promise.resolve();
let previous_run_active = false;
let initialized = false;

function context_key(stream: string | undefined, topic: string | undefined): string {
    return JSON.stringify([stream?.toLowerCase() ?? "", topic?.toLowerCase() ?? ""]);
}

function has_workspace(): boolean {
    return current_workspace_id !== undefined;
}

function $pane(): JQuery {
    return $("#pear-workspace-pane");
}

function $feed_column(): JQuery {
    return $("#message_feed_container").closest(".column-middle-inner");
}

function feed_is_clamped(): boolean {
    return $("#message_feed_container").hasClass("pear-feed-clamped");
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
    $existing[0]?.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            set_tab("chat");
            return;
        }
        // Workspace editors own their keymaps. Do not let Zulip's global
        // hotkeys interpret an editor gesture after it bubbles out.
        event.stopPropagation();
    });
    return $existing;
}

function update_tab_strips(): void {
    $(".pear-workspace-mode-tab").each(function (this: HTMLElement) {
        const selected = $(this).attr("data-pear-tab") === current_tab;
        $(this).toggleClass("pear-workspace-tab-selected", selected);
        $(this).attr("aria-selected", String(selected));
        this.tabIndex = selected ? 0 : -1;
    });
    $(".pear-workspace-tabs").toggleClass("pear-workspace-tabs-hidden", !has_workspace());
}

function measure_topic_bar_height(): number {
    const headers = [
        ...document.querySelectorAll<HTMLElement>("#message_feed_container .message_header_stream"),
    ];
    return headers.at(-1)?.offsetHeight ?? 0;
}

function pin_clamped_feed(): void {
    const feed = $("#message_feed_container")[0];
    if (feed === undefined) {
        return;
    }
    const headers = [...feed.querySelectorAll<HTMLElement>(".message_header_stream")];
    const bar = headers.at(-1);
    if (bar !== undefined) {
        feed.scrollTop += bar.getBoundingClientRect().top - feed.getBoundingClientRect().top;
    }
}

let topic_bar_heartbeat = 0;

function sync_topic_bar(): void {
    const $feed = $("#message_feed_container");
    if ($feed.length === 0) {
        return;
    }
    const active =
        has_workspace() &&
        pane_is_open() &&
        current_stream !== undefined &&
        current_topic !== undefined;
    const was_active = $feed.hasClass("pear-feed-clamped");
    $feed_column().toggleClass("pear-topic-bar-active", active);
    $feed.toggleClass("pear-feed-clamped", active);
    if (!active) {
        if (was_active) {
            $feed_column()[0]?.style.removeProperty("--pear-topic-bar-height");
        }
        topic_bar_heartbeat = 0;
        return;
    }
    topic_bar_heartbeat += 1;
    if (was_active && topic_bar_heartbeat % 8 !== 1) {
        return;
    }
    const height = measure_topic_bar_height();
    if (height > 0) {
        $feed_column()[0]?.style.setProperty("--pear-topic-bar-height", `${height}px`);
    }
    pin_clamped_feed();
}

function ensure_pane_shell(): HTMLElement | undefined {
    const $container = ensure_pane();
    if ($container.length === 0) {
        return undefined;
    }
    if (pane_shell_key !== current_context_key || $container.children().length === 0) {
        $container.html(render_pear_workspace_pane({}));
        pane_shell_key = current_context_key;
        document.dispatchEvent(new CustomEvent("pear:pane-shell-rendered"));
    }
    return $container.find(".pear-workspace-host")[0];
}

async function destroy_shell(): Promise<void> {
    shell_generation += 1;
    const previous = shell;
    shell = undefined;
    const teardown = async (): Promise<void> => {
        if (previous !== undefined) {
            await previous.destroy();
        }
    };
    const previous_teardown = shell_teardown;
    shell_teardown = (async () => {
        try {
            await previous_teardown;
        } catch {
            // A failed stale teardown must not prevent the next shell from
            // releasing its resources.
        }
        await teardown();
    })();
    await shell_teardown;
}

async function mount_shell(): Promise<void> {
    const workspace_id = current_workspace_id;
    const host = ensure_pane_shell();
    if (workspace_id === undefined || host === undefined || shell !== undefined) {
        return;
    }
    shell_generation += 1;
    const generation = shell_generation;
    host.setAttribute("aria-busy", "true");
    host.textContent = "Loading workspace…";
    try {
        // A context switch may replace this host while the previous editor is
        // still flushing its teardown. Never mount two live editor adapters.
        await shell_teardown;
        if (
            generation !== shell_generation ||
            current_tab !== "workspace" ||
            current_workspace_id !== workspace_id ||
            !host.isConnected
        ) {
            return;
        }
        const module = await import("./pear_workspace_shell.ts");
        if (
            generation !== shell_generation ||
            current_tab !== "workspace" ||
            current_workspace_id !== workspace_id ||
            !host.isConnected
        ) {
            return;
        }
        const mounted = module.create_pear_workspace_shell({root: host, workspaceId: workspace_id});
        shell = mounted;
        await mounted.ready;
        if (generation === shell_generation && shell === mounted) {
            host.removeAttribute("aria-busy");
        }
    } catch (error) {
        if (generation !== shell_generation || !host.isConnected) {
            return;
        }
        host.removeAttribute("aria-busy");
        host.textContent = "Workspace failed to load. Return to Chat and try again.";
        blueslip.error("Pear workspace shell failed to load", {error});
    }
}

let saved_chat_scroll: number | undefined;

function show_pane(): void {
    if (ensure_pane_shell() === undefined) {
        return;
    }
    if (!feed_is_clamped()) {
        saved_chat_scroll = window.scrollY;
    }
    $pane().removeAttr("hidden");
    sync_topic_bar();
    window.scrollTo(0, 0);
    void mount_shell();
    update_tab_strips();
}

function hide_pane(): void {
    $pane().attr("hidden", "hidden");
    sync_topic_bar();
    if (saved_chat_scroll === undefined) {
        return;
    }
    const target =
        saved_chat_scroll <= 1 ? document.documentElement.scrollHeight : saved_chat_scroll;
    saved_chat_scroll = undefined;
    window.scrollTo(0, target);
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            window.scrollTo(0, target);
        });
    });
}

function teardown_context(): void {
    void destroy_shell();
    pane_shell_key = undefined;
    $pane().empty();
}

function remembered_tab(key: string): PearWorkspaceTab {
    try {
        return sessionStorage.getItem(`pear-tab:${key}`) === "workspace" ? "workspace" : "chat";
    } catch {
        return "chat";
    }
}

function remember_tab(tab: PearWorkspaceTab): void {
    try {
        sessionStorage.setItem(`pear-tab:${current_context_key}`, tab);
    } catch {
        // Session storage can be unavailable; the current view still works.
    }
}

export function pane_is_open(): boolean {
    return current_tab === "workspace";
}

export function refresh_tab_strips(): void {
    update_tab_strips();
}

export function set_tab(tab: PearWorkspaceTab): void {
    if (tab === current_tab || (tab === "workspace" && !has_workspace())) {
        return;
    }
    current_tab = tab;
    remember_tab(tab);
    update_tab_strips();
    if (tab === "workspace") {
        show_pane();
    } else {
        hide_pane();
    }
    sync_topic_bar();
}

export function sync(options: {
    stream: string | undefined;
    topic: string | undefined;
    workspaceId: string | undefined;
}): void {
    const next_key = context_key(options.stream, options.topic);
    const requested_workspace_id = options.workspaceId?.trim();
    const next_workspace_id = requested_workspace_id === "" ? undefined : requested_workspace_id;
    const context_changed = next_key !== current_context_key;
    const workspace_changed = next_workspace_id !== current_workspace_id;
    if (context_changed || workspace_changed) {
        teardown_context();
    }
    current_context_key = next_key;
    current_stream = options.stream;
    current_topic = options.topic;
    current_workspace_id = next_workspace_id;

    if (context_changed) {
        if (pane_is_open()) {
            current_tab = "chat";
            hide_pane();
        }
        const restored = remembered_tab(next_key);
        if (restored === "workspace" && has_workspace()) {
            current_tab = "workspace";
            show_pane();
        }
    } else if (workspace_changed && pane_is_open()) {
        if (has_workspace()) {
            show_pane();
        } else {
            current_tab = "chat";
            hide_pane();
        }
    }
    if (!has_workspace() && pane_is_open()) {
        current_tab = "chat";
        hide_pane();
    }
    sync_topic_bar();
    update_tab_strips();
}

export function refresh_if_open_on_settle(run_active: boolean): void {
    const settled = previous_run_active && !run_active;
    previous_run_active = run_active;
    if (settled && pane_is_open()) {
        shell?.refresh();
    }
}

function on_workspace_tab_click(this: HTMLElement, event: JQuery.ClickEvent): void {
    event.preventDefault();
    const tab = $(this).attr("data-pear-tab");
    if (tab === "chat" || tab === "workspace") {
        set_tab(tab);
    }
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    $("body").on("click", ".pear-workspace-mode-tab", on_workspace_tab_click);
}
