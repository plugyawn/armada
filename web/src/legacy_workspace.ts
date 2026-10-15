import $ from "jquery";

import render_legacy_workspace_pane from "../templates/legacy_workspace_pane.hbs";

import * as armada_frame from "./armada/hooks/frame.ts"; // armada:
import * as blueslip from "./blueslip.ts";
import {
    read_armada_workspace_location,
    write_armada_workspace_location,
} from "./legacy_workspace_location.ts";
import {
    begin_armada_workspace_metric,
    finish_armada_workspace_metric,
} from "./legacy_workspace_metrics.ts";

export type ArmadaWorkspaceTab = "chat" | "readme" | "files";
type ArmadaWorkspaceRoom = Exclude<ArmadaWorkspaceTab, "chat">;

type WorkspaceShellController = {
    ready: Promise<void>;
    refresh: () => void;
    openQuickOpen: () => void;
    applyLocation: () => void;
    setRoom: (room: ArmadaWorkspaceRoom) => void;
    destroy: () => Promise<void>;
};

const quick_open_event_name = "armada:workspace-quick-open";

let current_tab: ArmadaWorkspaceTab = "chat";
let current_context_key = "";
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
    return $("#legacy-workspace-pane");
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
    $feed.after(
        $("<div>")
            .attr({id: "legacy-workspace-pane", "data-armada": "workspace-scroller"})
            .prop("hidden", true),
    );
    $existing = $pane();
    $existing[0]?.addEventListener("keydown", (event) => {
        if (
            event.key.toLocaleLowerCase() === "p" &&
            (event.ctrlKey || event.metaKey) &&
            !event.altKey
        ) {
            // Let Zulip's hotkey registry translate Cmd/Ctrl+P into the
            // cancellable workspace quick-open event.
            return;
        }
        if (event.key === "Escape" && !event.defaultPrevented) {
            event.preventDefault();
            event.stopPropagation();
            set_tab("chat");
            focus_workspace_tab("chat");
            return;
        }
        // Workspace editors own their keymaps. Do not let Zulip's global
        // hotkeys interpret an editor gesture after it bubbles out.
        event.stopPropagation();
    });
    return $existing;
}

function update_tab_strips(): void {
    $(".legacy-workspace-mode-tab").each(function (this: HTMLElement) {
        const selected = $(this).attr("data-legacy-tab") === current_tab;
        $(this).toggleClass("legacy-workspace-tab-selected", selected);
        $(this).attr("aria-selected", String(selected));
        this.tabIndex = selected ? 0 : -1;
    });
    // The views stand on every worktree from the frame of entry, before
    // the worktree's context has arrived (topic-entry-stability).
    $(".legacy-workspace-tabs").removeClass("legacy-workspace-tabs-hidden");
}

function ensure_pane_shell(): HTMLElement | undefined {
    const $container = ensure_pane();
    if ($container.length === 0) {
        return undefined;
    }
    if (pane_shell_key !== current_context_key || $container.children().length === 0) {
        $container.html(render_legacy_workspace_pane({}));
        pane_shell_key = current_context_key;
        document.dispatchEvent(new CustomEvent("armada:pane-shell-rendered"));
    }
    return $container.find(".legacy-workspace-host")[0];
}

// The shell, its editors and its stylesheet live in a lazy chunk. Fetch
// it while the browser is idle in any topic that has a worktree, so the
// first README or Files click only has to wait for data.
let shell_module_preloaded = false;

function preload_shell_module(): void {
    if (
        shell_module_preloaded ||
        !has_workspace() ||
        globalThis.process?.env["NODE_ENV"] === "test"
    ) {
        return;
    }
    shell_module_preloaded = true;
    const preload = (): void => {
        void import("./legacy_workspace_shell.ts");
    };
    if (typeof requestIdleCallback === "function") {
        requestIdleCallback(preload, {timeout: 2000});
    } else {
        setTimeout(preload, 300);
    }
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
    let mounted: WorkspaceShellController | undefined;
    host.setAttribute("aria-busy", "true");
    host.textContent = "Loading workspace…";
    try {
        // A context switch may replace this host while the previous editor is
        // still flushing its teardown. Never mount two live editor adapters.
        await shell_teardown;
        if (
            generation !== shell_generation ||
            current_tab === "chat" ||
            current_workspace_id !== workspace_id ||
            !host.isConnected
        ) {
            return;
        }
        const module = await import("./legacy_workspace_shell.ts");
        if (
            generation !== shell_generation ||
            !(["readme", "files"] as const).includes(current_tab) ||
            current_workspace_id !== workspace_id ||
            !host.isConnected
        ) {
            return;
        }
        mounted = module.create_armada_workspace_shell({
            root: host,
            workspaceId: workspace_id,
            initialRoom: current_tab,
        });
        shell = mounted;
        await mounted.ready;
        finish_armada_workspace_metric("workspace_click_to_shell");
        if (generation === shell_generation && shell === mounted) {
            host.removeAttribute("aria-busy");
        }
    } catch (error) {
        if (generation !== shell_generation || !host.isConnected) {
            return;
        }
        if (mounted !== undefined && shell === mounted) {
            shell = undefined;
            try {
                await mounted.destroy();
            } catch {
                // The failed shell is already detached; retry must remain available.
            }
        }
        host.removeAttribute("aria-busy");
        const failure = document.createElement("div");
        failure.className = "legacy-workspace-load-error";
        failure.setAttribute("role", "alert");
        const message = document.createElement("span");
        message.textContent = "Workspace failed to load.";
        const retry = document.createElement("button");
        retry.type = "button";
        retry.textContent = "Retry Workspace";
        retry.addEventListener("click", () => {
            host.tabIndex = -1;
            host.focus();
            void mount_shell();
        });
        failure.append(message, retry);
        host.replaceChildren(failure);
        blueslip.error("Pear workspace shell failed to load", {error});
    }
}

// The chat's scroller stays where it is, under the pane, so it keeps its
// place by itself (the frame's kept panes; frame/frame.css).
function show_pane(): void {
    if (ensure_pane_shell() === undefined) {
        return;
    }
    $pane().removeAttr("hidden");
    armada_frame.set_view(current_tab);
    void mount_shell();
    if (current_tab !== "chat") {
        shell?.setRoom(current_tab);
    }
    if (shell !== undefined) {
        finish_armada_workspace_metric("workspace_click_to_shell");
    }
    update_tab_strips();
}

function hide_pane(): void {
    $pane().attr("hidden", "hidden");
    armada_frame.set_view("chat");
}

function teardown_context(): void {
    void destroy_shell();
    pane_shell_key = undefined;
    $pane().empty();
}

function remembered_tab(key: string): ArmadaWorkspaceTab {
    try {
        const remembered = sessionStorage.getItem(`legacy-tab:${key}`);
        if (remembered === "readme" || remembered === "files") {
            return remembered;
        }
        return remembered === "workspace" ? "readme" : "chat";
    } catch {
        return "chat";
    }
}

function remember_tab(tab: ArmadaWorkspaceTab): void {
    try {
        sessionStorage.setItem(`legacy-tab:${current_context_key}`, tab);
    } catch {
        // Session storage can be unavailable; the current view still works.
    }
}

export function pane_is_open(): boolean {
    return current_tab !== "chat";
}

export function refresh_tab_strips(): void {
    update_tab_strips();
}

export function set_tab(tab: ArmadaWorkspaceTab, update_history = true): void {
    if (tab === current_tab || (tab !== "chat" && !has_workspace())) {
        return;
    }
    current_tab = tab;
    if (tab !== "chat") {
        begin_armada_workspace_metric("workspace_click_to_shell");
    }
    if (update_history) {
        write_armada_workspace_location(
            {view: tab, workspaceId: tab === "chat" ? null : (current_workspace_id ?? null)},
            "push",
        );
    }
    remember_tab(tab);
    update_tab_strips();
    if (tab !== "chat") {
        show_pane();
    } else {
        hide_pane();
    }
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
    current_workspace_id = next_workspace_id;

    if (context_changed) {
        if (pane_is_open()) {
            current_tab = "chat";
            hide_pane();
        }
        const requested_location = read_armada_workspace_location();
        const restored =
            requested_location.view !== "chat" &&
            requested_location.workspaceId === next_workspace_id
                ? requested_location.view
                : remembered_tab(next_key);
        if (restored !== "chat" && has_workspace()) {
            current_tab = restored;
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
    update_tab_strips();
    preload_shell_module();
    write_armada_workspace_location(
        {
            view: current_tab,
            workspaceId: current_tab === "chat" ? null : (current_workspace_id ?? null),
        },
        "replace",
    );
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
    const tab = $(this).attr("data-legacy-tab");
    if (tab === "chat" || tab === "readme" || tab === "files") {
        set_tab(tab);
    }
}

// The views of the place in front: the header's (legacy_header.ts).
function active_workspace_tab_strip(): Element | undefined {
    return document.querySelector("[data-armada=header] .legacy-workspace-tabs") ?? undefined;
}

function focus_workspace_tab(tab: ArmadaWorkspaceTab, strip?: Element | null): void {
    (strip ?? active_workspace_tab_strip())
        ?.querySelector<HTMLButtonElement>(`.legacy-workspace-mode-tab[data-legacy-tab="${tab}"]`)
        ?.focus();
}

function on_workspace_tab_keydown(this: HTMLButtonElement, event: JQuery.KeyDownEvent): void {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
    }
    const strip = this.closest(".legacy-workspace-tabs");
    if (strip === null) {
        return;
    }
    const tabs = [
        ...strip.querySelectorAll<HTMLButtonElement>(".legacy-workspace-mode-tab[data-legacy-tab]"),
    ];
    const current_index = tabs.indexOf(this);
    if (current_index === -1 || tabs.length === 0) {
        return;
    }
    let next_index: number;
    switch (event.key) {
        case "Home":
            next_index = 0;
            break;
        case "End":
            next_index = tabs.length - 1;
            break;
        case "ArrowLeft":
            next_index = (current_index - 1 + tabs.length) % tabs.length;
            break;
        default:
            next_index = (current_index + 1) % tabs.length;
    }
    const next_tab = tabs[next_index]?.dataset["legacyTab"];
    if (next_tab !== "chat" && next_tab !== "readme" && next_tab !== "files") {
        return;
    }
    event.preventDefault();
    event.stopPropagation();
    set_tab(next_tab);
    focus_workspace_tab(next_tab, strip);
}

async function mount_and_open_quick_open(): Promise<void> {
    if (current_tab === "readme") {
        set_tab("files");
    }
    await mount_shell();
    shell?.openQuickOpen();
}

function on_quick_open_event(event: Event): void {
    if (!pane_is_open() || !has_workspace()) {
        return;
    }
    event.preventDefault();
    if (current_tab === "readme") {
        set_tab("files");
    }
    if (shell !== undefined) {
        shell.openQuickOpen();
        return;
    }
    void mount_and_open_quick_open();
}

function on_popstate(): void {
    const location = read_armada_workspace_location();
    set_tab(location.view, false);
    shell?.applyLocation();
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    $("body").on("click", ".legacy-workspace-mode-tab", on_workspace_tab_click);
    $("body").on("keydown", ".legacy-workspace-mode-tab", on_workspace_tab_keydown);
    document.addEventListener(quick_open_event_name, on_quick_open_event);
    window.addEventListener("popstate", on_popstate);
}
