import $ from "jquery";

import {$t} from "./i18n.ts";

export type SidebarTab = "agents" | "people";

/* The right sidebar is one workbench surface with two tabs: Agents (the
   agent roster plus the thread pane) and People (the classic user list,
   pear rail, and wall). Tab state lives here; visibility is CSS-driven
   off body.right-sidebar-tab-agents. */

let current_tab: SidebarTab = "people";
const listeners = new Set<(tab: SidebarTab) => void>();

export function current(): SidebarTab {
    return current_tab;
}

function sync_dom(): void {
    document.body.classList.toggle("right-sidebar-tab-agents", current_tab === "agents");
    for (const button of document.querySelectorAll<HTMLElement>(".right-sidebar-tab")) {
        const selected = button.dataset["tab"] === current_tab;
        button.classList.toggle("right-sidebar-tab-selected", selected);
        button.setAttribute("aria-selected", String(selected));
    }
    // Context fallback: the tab's own label. When a thread is open on the
    // Agents tab, message_threads renders the conversation motif instead.
    if (!document.body.classList.contains("message-thread-pane-open") || current_tab === "people") {
        const context = document.querySelector<HTMLElement>("#right-sidebar-context");
        const icon = document.querySelector<HTMLElement>("#right-sidebar-context-icon");
        if (context !== null && icon !== null) {
            context.textContent =
                current_tab === "agents"
                    ? $t({defaultMessage: "Agents"})
                    : $t({defaultMessage: "People"});
            icon.hidden = true;
        }
    }
}

export function refresh(): void {
    sync_dom();
}

export function set_tab(tab: SidebarTab): void {
    if (tab === current_tab) {
        return;
    }
    current_tab = tab;
    sync_dom();
    for (const listener of listeners) {
        listener(tab);
    }
}

export function on_tab_changed(listener: (tab: SidebarTab) => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

export function initialize(): void {
    $(".right-sidebar-tabs").on("click", ".right-sidebar-tab", (event) => {
        const tab = $(event.currentTarget).attr("data-tab");
        if (tab === "agents" || tab === "people") {
            set_tab(tab);
        }
    });
    sync_dom();
}

export function clear_for_testing(): void {
    listeners.clear();
    current_tab = "people";
}
