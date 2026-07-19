import {$t} from "./i18n.ts";
import * as modals from "./modals.ts";
import * as overlay_util from "./overlay_util.ts";
import * as overlays from "./overlays.ts";
import * as pear_run_state from "./pear_run_state.ts";
import * as pear_run_trace from "./pear_run_trace.ts";
import * as ui_util from "./ui_util.ts";

type PaneElements = {
    pane: HTMLElement;
    title: HTMLElement;
    role: HTMLElement;
    status: HTMLElement;
    tabs: HTMLElement;
    trace_root: HTMLElement;
    trace_host: HTMLElement;
    trace_status: HTMLElement;
    close: HTMLButtonElement;
};

type TraceEnhancer = (root: HTMLElement) => void;

let pane_elements: PaneElements | undefined;
let open_run_id: number | undefined;
let selected_thread_id: string | undefined;
let trace_thread_id: string | undefined;
let trace_controller: pear_run_trace.PearRunTraceController | undefined;
let anchor_element: HTMLElement | undefined;
let anchor_message_id: number | undefined;
let anchor_thread_id: string | undefined;
let pane_trace_enhancer: TraceEnhancer | undefined;
let mobile_scroll_locked = false;
let initialized = false;
let tabs_signature = "";
let pane_render_frame: number | undefined;
let pane_open_focus_frame: number | undefined;
let pane_restore_focus_frame: number | undefined;

function hash_thread_id(thread_id: string): number {
    let hash = 2_166_136_261;
    for (const char of thread_id) {
        hash ^= char.codePointAt(0) ?? 0;
        hash = Math.imul(hash, 16_777_619);
    }
    return hash >>> 0;
}

export function sigil_cells_for_testing(thread_id: string): boolean[] {
    const hash = hash_thread_id(thread_id);
    const cells = Array.from({length: 9}, () => false);
    for (let row = 0; row < 3; row += 1) {
        const edge = (hash & (1 << (row * 2))) !== 0;
        cells[row * 3] = edge;
        cells[row * 3 + 2] = edge;
        cells[row * 3 + 1] = (hash & (1 << (row * 2 + 1))) !== 0;
    }
    cells[4] = true;
    if (cells.filter(Boolean).length < 4) {
        cells[0] = true;
        cells[2] = true;
        cells[7] = true;
    }
    return cells;
}

export function status_tone_for_testing(status: string): "active" | "failed" | "idle" {
    const normalized = status.trim().toLowerCase();
    if (["active", "busy", "running", "working"].includes(normalized)) {
        return "active";
    }
    if (["error", "failed", "cancelled"].includes(normalized)) {
        return "failed";
    }
    return "idle";
}

export function display_name_for_testing(
    agent: pear_run_state.PearAgentThread,
    index: number,
): string {
    return (
        agent.nickname?.trim() ||
        agent.role?.trim() ||
        agent.name?.trim() ||
        $t({defaultMessage: "Agent {number}"}, {number: index + 1})
    );
}

export function selection_after_update_for_testing(
    agents: pear_run_state.PearAgentThread[],
    selected: string | undefined,
): string | undefined {
    if (selected !== undefined && agents.some((agent) => agent.thread_id === selected)) {
        return selected;
    }
    return agents[0]?.thread_id;
}

function create_sigil(agent: pear_run_state.PearAgentThread): HTMLElement {
    const sigil = document.createElement("span");
    sigil.className = `pear-agent-sigil pear-agent-sigil-tone-${hash_thread_id(agent.thread_id) % 6}`;
    sigil.setAttribute("aria-hidden", "true");
    for (const active of sigil_cells_for_testing(agent.thread_id)) {
        const cell = document.createElement("i");
        cell.className = active ? "pear-agent-sigil-cell pear-agent-sigil-cell-on" : "pear-agent-sigil-cell";
        sigil.append(cell);
    }
    const status = document.createElement("span");
    status.className = `pear-agent-status pear-agent-status-${status_tone_for_testing(agent.status)}`;
    sigil.append(status);
    return sigil;
}

function agent_signature(agent: pear_run_state.PearAgentThread): string {
    return [
        agent.thread_id,
        agent.nickname ?? "",
        agent.role ?? "",
        agent.name ?? "",
        agent.status,
    ].join("\u0000");
}

function document_is_available(): boolean {
    return typeof document !== "undefined" && typeof document.querySelector === "function";
}

function pane_is_mobile(): boolean {
    // The side pane replaces Zulip's ordinary right column. Below the
    // breakpoint where that column is stable, use the same DOM as a
    // full-screen dialog instead of squeezing the message/compose column.
    return !ui_util.matches_viewport_state("gte_xl_min");
}

function focusable_elements(pane: HTMLElement): HTMLElement[] {
    return [
        ...pane.querySelectorAll<HTMLElement>(
            'button:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
        ),
    ].filter(
        (element) =>
            element.isConnected &&
            element.closest('[hidden], [inert], [aria-hidden="true"]') === null &&
            element.getClientRects().length > 0,
    );
}

function update_pane_layout(): void {
    const elements = pane_elements;
    if (elements === undefined || elements.pane.hidden) {
        return;
    }
    const mobile = pane_is_mobile();
    elements.pane.classList.toggle("pear-agent-pane-mobile", mobile);
    document.body.classList.toggle("pear-agent-pane-mobile-open", mobile);
    elements.pane.setAttribute("role", mobile ? "dialog" : "complementary");
    if (mobile) {
        elements.pane.setAttribute("aria-modal", "true");
        if (!mobile_scroll_locked) {
            overlay_util.disable_scrolling();
            mobile_scroll_locked = true;
        }
    } else {
        elements.pane.removeAttribute("aria-modal");
        if (mobile_scroll_locked) {
            overlay_util.enable_scrolling();
            mobile_scroll_locked = false;
        }
    }
}

function create_pane_elements(pane: HTMLElement): PaneElements {
    pane.replaceChildren();
    const header = document.createElement("header");
    header.className = "pear-agent-pane-header";
    const heading_group = document.createElement("div");
    heading_group.className = "pear-agent-pane-heading";
    const eyebrow = document.createElement("span");
    eyebrow.className = "pear-agent-pane-eyebrow";
    eyebrow.textContent = $t({defaultMessage: "Agent thread"});
    const title = document.createElement("h2");
    title.id = "pear-agent-pane-title";
    const role = document.createElement("span");
    role.className = "pear-agent-pane-role";
    heading_group.append(eyebrow, title, role);
    const close = document.createElement("button");
    close.className = "pear-agent-pane-close";
    close.type = "button";
    close.setAttribute("aria-label", $t({defaultMessage: "Close agent thread"}));
    close.textContent = "×";
    header.append(heading_group, close);

    const tabs = document.createElement("div");
    tabs.className = "pear-agent-pane-tabs";
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", $t({defaultMessage: "Agent threads"}));

    const body = document.createElement("section");
    body.className = "pear-agent-pane-body";
    const status = document.createElement("div");
    status.className = "pear-agent-pane-activity";
    status.setAttribute("aria-live", "polite");
    const trace_root = document.createElement("div");
    trace_root.className = "pear-agent-pane-trace pear-message-surface pear-message";
    trace_root.id = "pear-agent-pane-panel";
    trace_root.setAttribute("role", "tabpanel");
    const message = document.createElement("div");
    message.className = "pear-agent-pane-message message_content";
    const trace_status = document.createElement("span");
    trace_status.className = "pear-run-trace-status";
    trace_status.setAttribute("aria-live", "polite");
    trace_status.setAttribute("role", "status");
    const trace_host = document.createElement("div");
    trace_host.className = "pear-run-trace";
    message.append(trace_status, trace_host);
    trace_root.append(message);
    body.append(status, trace_root);
    pane.append(header, tabs, body);
    pane.setAttribute("aria-labelledby", title.id);
    return {pane, title, role, status, tabs, trace_root, trace_host, trace_status, close};
}

function ensure_initialized(): PaneElements | undefined {
    if (!document_is_available()) {
        return undefined;
    }
    const pane = document.querySelector<HTMLElement>("#pear-agent-pane");
    if (pane === null) {
        return undefined;
    }
    if (pane_elements === undefined) {
        pane_elements = create_pane_elements(pane);
    }
    if (!initialized) {
        initialized = true;
        pane_elements.close.addEventListener("click", () => close());
        pane_elements.tabs.addEventListener("click", (event) => {
            const tab = (event.target as Element).closest<HTMLButtonElement>("[role=tab]");
            if (tab?.dataset["threadId"] !== undefined) {
                select_thread(tab.dataset["threadId"], true);
            }
        });
        pane_elements.tabs.addEventListener("keydown", handle_tablist_keydown);
        document.addEventListener("keydown", handle_document_keydown);
        window.addEventListener("resize", update_pane_layout);
        modals.register_pre_open_hook(() => close({restore_focus: false}));
        overlays.register_pre_open_hook(() => close({restore_focus: false}));
    }
    return pane_elements;
}

function active_run(): pear_run_state.PearRunLiveView | undefined {
    return open_run_id === undefined ? undefined : pear_run_state.get_run_view_by_id(open_run_id);
}

function selected_agent(): pear_run_state.PearAgentThread | undefined {
    return active_run()?.agents?.find((agent) => agent.thread_id === selected_thread_id);
}

function render_tabs(agents: pear_run_state.PearAgentThread[]): void {
    const elements = pane_elements;
    if (elements === undefined) {
        return;
    }
    const signature = `${selected_thread_id ?? ""}\u0001${agents.map(agent_signature).join("\u0001")}`;
    if (signature === tabs_signature) {
        return;
    }
    tabs_signature = signature;
    const restore_tab_focus = elements.tabs.contains(document.activeElement);
    const tabs = agents.map((agent, index) => {
        const tab = document.createElement("button");
        const selected = agent.thread_id === selected_thread_id;
        const name = display_name_for_testing(agent, index);
        tab.className = "pear-agent-pane-tab";
        tab.type = "button";
        tab.dataset["threadId"] = agent.thread_id;
        tab.id = `pear-agent-pane-tab-${index}`;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(selected));
        tab.setAttribute("aria-controls", elements.trace_root.id);
        tab.setAttribute("tabindex", selected ? "0" : "-1");
        tab.setAttribute("title", name);
        tab.append(create_sigil(agent));
        const label = document.createElement("span");
        label.className = "pear-agent-pane-tab-label";
        label.textContent = name;
        tab.append(label);
        return tab;
    });
    elements.tabs.replaceChildren(...tabs);
    const selected_tab = elements.tabs.querySelector<HTMLElement>(
        "[role=tab][aria-selected=true]",
    );
    if (selected_tab !== null) {
        elements.trace_root.setAttribute("aria-labelledby", selected_tab.id);
    }
    if (restore_tab_focus) {
        selected_tab?.focus({preventScroll: true});
    }
}

function mount_selected_trace(): void {
    const elements = pane_elements;
    const agent = selected_agent();
    if (elements === undefined || agent === undefined || open_run_id === undefined) {
        return;
    }
    if (trace_thread_id === agent.thread_id && trace_controller !== undefined) {
        return;
    }
    trace_controller?.destroy();
    trace_thread_id = agent.thread_id;
    elements.trace_host.replaceChildren();
    elements.trace_host.dataset["pearThreadId"] = agent.thread_id;
    trace_controller = pear_run_trace.create_controller({
        run_id: open_run_id,
        thread_id: agent.thread_id,
        host: elements.trace_host,
        status: elements.trace_status,
        after_render() {
            pane_trace_enhancer?.(elements.trace_root);
        },
        on_state_change(controller) {
            elements.trace_root.toggleAttribute("aria-busy", controller.in_flight);
            elements.trace_root.dataset["state"] = controller.request_state;
        },
    });
    trace_controller.load();
}

function render_pane(): void {
    const elements = ensure_initialized();
    const run = active_run();
    const agents = run?.agents ?? [];
    if (elements === undefined || agents.length === 0) {
        close();
        return;
    }
    selected_thread_id = selection_after_update_for_testing(agents, selected_thread_id);
    const agent = selected_agent();
    if (agent === undefined) {
        close();
        return;
    }
    const index = agents.indexOf(agent);
    elements.title.textContent = display_name_for_testing(agent, index);
    elements.role.textContent = agent.role?.trim() ?? "";
    elements.status.textContent =
        agent.current_activity?.trim() ||
        $t({defaultMessage: "Status: {status}"}, {status: agent.status});
    render_tabs(agents);
    mount_selected_trace();
    update_pane_layout();
}

function schedule_pane_render(): void {
    if (pane_render_frame !== undefined) {
        return;
    }
    pane_render_frame = window.requestAnimationFrame(() => {
        pane_render_frame = undefined;
        render_pane();
    });
}

function cancel_focus_frame(frame: number | undefined): void {
    if (frame !== undefined) {
        window.cancelAnimationFrame(frame);
    }
}

function restore_launch_focus(
    anchor: HTMLElement | undefined,
    message_id: number | undefined,
    thread_id: string | undefined,
): void {
    const fallback_badge =
        message_id === undefined || thread_id === undefined
            ? undefined
            : document.querySelector<HTMLElement>(
                  `.message_row[data-message-id="${message_id}"] .pear-agent-thread-badge[data-thread-id="${CSS.escape(thread_id)}"]`,
              );
    const launch_target =
        anchor?.isConnected === true && anchor.closest("[hidden]") === null
            ? anchor
            : fallback_badge?.closest("[hidden]") === null
              ? fallback_badge
              : undefined;
    if (launch_target?.isConnected === true) {
        launch_target.focus({preventScroll: true});
        return;
    }
    const row =
        message_id === undefined
            ? undefined
            : document.querySelector<HTMLElement>(`.message_row[data-message-id="${message_id}"]`);
    if (row === undefined || row === null) {
        return;
    }
    const had_tabindex = row.hasAttribute("tabindex");
    if (!had_tabindex) {
        row.setAttribute("tabindex", "-1");
        row.addEventListener(
            "blur",
            () => {
                row.removeAttribute("tabindex");
            },
            {once: true},
        );
    }
    row.focus({preventScroll: true});
}

function select_thread(thread_id: string, focus_tab: boolean): void {
    if (thread_id === selected_thread_id && trace_controller !== undefined) {
        return;
    }
    selected_thread_id = thread_id;
    render_pane();
    if (focus_tab) {
        pane_elements?.tabs
            .querySelector<HTMLElement>(`[data-thread-id="${CSS.escape(thread_id)}"]`)
            ?.focus({preventScroll: true});
    }
}

function handle_tablist_keydown(event: KeyboardEvent): void {
    const tab = (event.target as Element).closest<HTMLButtonElement>("[role=tab]");
    if (tab === null || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
    }
    const tabs = [...pane_elements!.tabs.querySelectorAll<HTMLButtonElement>("[role=tab]")];
    const index = tabs.indexOf(tab);
    const next_index =
        event.key === "Home"
            ? 0
            : event.key === "End"
              ? tabs.length - 1
              : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const thread_id = tabs[next_index]?.dataset["threadId"];
    if (thread_id !== undefined) {
        select_thread(thread_id, true);
        event.preventDefault();
    }
}

function handle_document_keydown(event: KeyboardEvent): void {
    const elements = pane_elements;
    if (elements === undefined || elements.pane.hidden) {
        return;
    }
    if (event.key === "Escape") {
        close();
        event.preventDefault();
        return;
    }
    if (event.key !== "Tab" || !pane_is_mobile()) {
        return;
    }
    const focusable = focusable_elements(elements.pane);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) {
        event.preventDefault();
        elements.pane.focus({preventScroll: true});
    } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus({preventScroll: true});
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus({preventScroll: true});
    }
}

function open(
    run_id: number,
    thread_id: string,
    anchor: HTMLElement,
    enhance_trace: TraceEnhancer,
): void {
    const elements = ensure_initialized();
    if (elements === undefined) {
        return;
    }
    cancel_focus_frame(pane_restore_focus_frame);
    pane_restore_focus_frame = undefined;
    anchor_element = anchor;
    anchor_message_id = pear_run_state.get_run_view_by_id(run_id)?.result_message_id ?? undefined;
    anchor_thread_id = thread_id;
    pane_trace_enhancer = enhance_trace;
    open_run_id = run_id;
    selected_thread_id = thread_id;
    elements.pane.hidden = false;
    elements.pane.setAttribute("aria-hidden", "false");
    document.body.classList.add("pear-agent-pane-open");
    render_pane();
    cancel_focus_frame(pane_open_focus_frame);
    const opening_run_id = run_id;
    pane_open_focus_frame = window.requestAnimationFrame(() => {
        pane_open_focus_frame = undefined;
        if (elements.pane.hidden || open_run_id !== opening_run_id) {
            return;
        }
        elements.tabs
            .querySelector<HTMLElement>(`[data-thread-id="${CSS.escape(thread_id)}"]`)
            ?.focus({preventScroll: true});
    });
}

export function close(options: {restore_focus?: boolean} = {}): void {
    const elements = pane_elements;
    if (elements === undefined || elements.pane.hidden) {
        return;
    }
    if (pane_render_frame !== undefined) {
        window.cancelAnimationFrame(pane_render_frame);
        pane_render_frame = undefined;
    }
    cancel_focus_frame(pane_open_focus_frame);
    pane_open_focus_frame = undefined;
    cancel_focus_frame(pane_restore_focus_frame);
    pane_restore_focus_frame = undefined;
    const focus_message_id = anchor_message_id;
    const focus_thread_id = anchor_thread_id;
    trace_controller?.destroy();
    trace_controller = undefined;
    trace_thread_id = undefined;
    open_run_id = undefined;
    selected_thread_id = undefined;
    tabs_signature = "";
    elements.pane.hidden = true;
    elements.pane.setAttribute("aria-hidden", "true");
    document.body.classList.remove("pear-agent-pane-open");
    document.body.classList.remove("pear-agent-pane-mobile-open");
    if (mobile_scroll_locked) {
        overlay_util.enable_scrolling();
        mobile_scroll_locked = false;
    }
    const anchor = anchor_element;
    anchor_element = undefined;
    anchor_message_id = undefined;
    anchor_thread_id = undefined;
    pane_trace_enhancer = undefined;
    if (options.restore_focus !== false) {
        // Message-row presence rendering is also rAF-batched. Restore on the
        // following frame so we never focus a launch badge that the same
        // event is about to replace.
        pane_restore_focus_frame = window.requestAnimationFrame(() => {
            pane_restore_focus_frame = undefined;
            restore_launch_focus(anchor, focus_message_id, focus_thread_id);
        });
    }
}

export function destroy_for_message(message_id: number): void {
    if (anchor_message_id === message_id) {
        close({restore_focus: false});
    }
}

export function render_for_message_row(
    row: HTMLElement,
    message_id: number,
    enhance_trace: TraceEnhancer,
): void {
    const host = row.querySelector<HTMLElement>(".pear-agent-thread-host");
    if (host === null) {
        return;
    }
    const run = pear_run_state.get_run_view(message_id);
    const agents = run?.agents ?? [];
    if (run === undefined || agents.length === 0) {
        host.hidden = true;
        host.replaceChildren();
        delete host.dataset["signature"];
        return;
    }
    const signature = `${run.id}\u0001${agents.map(agent_signature).join("\u0001")}`;
    if (host.dataset["signature"] === signature) {
        return;
    }
    host.dataset["signature"] = signature;
    host.hidden = false;
    const strip = document.createElement("div");
    strip.className = "pear-agent-thread-strip";
    strip.setAttribute("role", "group");
    strip.setAttribute(
        "aria-label",
        $t(
            {defaultMessage: "{count, plural, one {# agent thread} other {# agent threads}}"},
            {count: agents.length},
        ),
    );
    const badges = document.createElement("span");
    badges.className = "pear-agent-thread-badges";
    for (const [index, agent] of agents.slice(0, 4).entries()) {
        const button = document.createElement("button");
        const name = display_name_for_testing(agent, index);
        button.className = "pear-agent-thread-badge";
        button.type = "button";
        button.dataset["threadId"] = agent.thread_id;
        button.setAttribute("aria-label", $t({defaultMessage: "Open {name}"}, {name}));
        button.setAttribute("title", `${name} · ${agent.status}`);
        button.append(create_sigil(agent));
        button.addEventListener("click", () =>
            open(run.id, agent.thread_id, button, enhance_trace),
        );
        badges.append(button);
    }
    const count = document.createElement("button");
    count.className = "pear-agent-thread-count";
    count.type = "button";
    count.textContent = $t(
        {defaultMessage: "{count, plural, one {# agent} other {# agents}}"},
        {count: agents.length},
    );
    count.addEventListener("click", () =>
        open(run.id, agents[0]!.thread_id, count, enhance_trace),
    );
    strip.append(badges, count);
    host.replaceChildren(strip);
}

pear_run_state.on_run_state_changed((run, event) => {
    if (!document_is_available()) {
        return;
    }
    if (run.id !== open_run_id) {
        return;
    }
    if (
        event?.event_type === "agent" &&
        event.agent_thread_id === selected_thread_id
    ) {
        trace_controller?.mark_dirty(true);
    }
    schedule_pane_render();
});
