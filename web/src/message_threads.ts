import $ from "jquery";

import render_message_thread_pane from "../templates/message_thread_pane.hbs";

import * as compose_editor from "./compose_editor.ts";
import type {ResolvedComposeRecipient} from "./compose_send.ts";
import {Filter} from "./filter.ts";
import {$t} from "./i18n.ts";
import * as message_fetch from "./message_fetch.ts";
import {MessageList} from "./message_list.ts";
import {MessageListData} from "./message_list_data.ts";
import * as message_lists from "./message_lists.ts";
import * as message_store from "./message_store.ts";
import * as message_thread_hooks from "./message_thread_hooks.ts";
import * as modals from "./modals.ts";
import * as overlay_util from "./overlay_util.ts";
import * as overlays from "./overlays.ts";
import {
    create_sigil,
    display_name_for_testing,
    status_tone_for_testing,
} from "./pear_agent_sigil.ts";
import * as pear_run_state from "./pear_run_state.ts";
import * as pear_run_trace from "./pear_run_trace.ts";
import * as people from "./people.ts";
import * as right_sidebar_tabs from "./right_sidebar_tabs.ts";
import type {UpdateMessageEvent} from "./server_event_types.ts";
import * as stream_data from "./stream_data.ts";
import * as timerender from "./timerender.ts";
import * as ui_util from "./ui_util.ts";
import * as unread_ops from "./unread_ops.ts";

const HUMAN_THREAD_KEY = message_thread_hooks.HUMAN_THREAD_KEY;
type TraceEnhancer = (root: HTMLElement) => void;

type HumanThreadView = {
    message_id: number;
    reply_list: MessageList;
    editor: compose_editor.ComposeEditor;
    visibility_observer?: IntersectionObserver | undefined;
};

type RootThreadView = {
    message_id: number;
    root_list: MessageList;
};

type PaneAgentEntry = {
    agent: pear_run_state.PearAgentThread;
    run: pear_run_state.PearRunLiveView;
};

type PaneElements = {
    pane: HTMLElement;
    title: HTMLElement;
    stream_icon: HTMLElement;
    agent_bar: HTMLElement;
    agent_prev: HTMLButtonElement;
    agent_current: HTMLButtonElement;
    agent_next: HTMLButtonElement;
    body: HTMLElement;
    panel: HTMLElement;
    activity: HTMLElement;
    replies: HTMLElement;
    root: HTMLElement;
    separator: HTMLElement;
    reply_status: HTMLElement;
    reply_list: HTMLElement;
    older: HTMLButtonElement;
    composer: HTMLElement;
    trace_root: HTMLElement;
    trace_host: HTMLElement;
    trace_status: HTMLElement;
    close: HTMLButtonElement;
};

const root_load_callbacks_by_message_id = new Map<number, ((loaded: boolean) => void)[]>();
const root_load_data_by_message_id = new Map<number, MessageListData>();
const pending_root_by_anchor_message_id = new Map<number, number>();
const trace_enhancer_by_row = new WeakMap<HTMLElement, TraceEnhancer>();
let human_thread_view: HumanThreadView | undefined;
let root_thread_view: RootThreadView | undefined;
let pane_elements: PaneElements | undefined;
let open_message_id: number | undefined;
let selected_thread_key: string | undefined;
let trace_thread_id: string | undefined;
let trace_controller: pear_run_trace.PearRunTraceController | undefined;
let anchor_element: HTMLElement | undefined;
let anchor_message_id: number | undefined;
let anchor_thread_key: string | undefined;
let pane_trace_enhancer: TraceEnhancer | undefined;
let mobile_scroll_locked = false;
let mobile_inerted_elements: HTMLElement[] = [];
let sidebar_hidden_before_pane = false;
let initialized = false;
let agent_bar_signature = "";
let pane_render_frame: number | undefined;
let pane_open_focus_frame: number | undefined;
let pane_restore_focus_frame: number | undefined;

export {
    create_sigil,
    display_name_for_testing,
    sigil_cells_for_testing,
    status_tone_for_testing,
} from "./pear_agent_sigil.ts";

export function selection_after_update_for_testing(
    agents: pear_run_state.PearAgentThread[],
    selected: string | undefined,
): string {
    if (selected === HUMAN_THREAD_KEY) {
        return HUMAN_THREAD_KEY;
    }
    if (selected !== undefined && agents.some((agent) => agent.thread_id === selected)) {
        return selected;
    }
    return agents[0]?.thread_id ?? HUMAN_THREAD_KEY;
}

export function cycle_order_for_testing(agents: pear_run_state.PearAgentThread[]): string[] {
    // The human thread (Main) always leads; agents follow in stable
    // first-seen spawn order.
    return [HUMAN_THREAD_KEY, ...agents.map((agent) => agent.thread_id)];
}

export function cycle_selection_for_testing(
    order: string[],
    selected: string | undefined,
    delta: number,
): string {
    const index = selected === undefined ? -1 : order.indexOf(selected);
    const start = index === -1 ? 0 : index;
    return order[(start + delta + order.length) % order.length] ?? HUMAN_THREAD_KEY;
}

export function agent_is_dimmed_for_testing(agent: pear_run_state.PearAgentThread): boolean {
    // Closed/finished agents are shown dimmed in the cycler.
    return status_tone_for_testing(agent.status) !== "active";
}

function initials(full_name: string): string {
    return full_name
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase() ?? "")
        .join("");
}

function create_avatar(
    participant: {
        user_id: number;
        full_name: string;
        avatar_url?: string | null | undefined;
    },
    class_name: string,
): HTMLElement {
    if (participant.avatar_url) {
        const avatar = document.createElement("img");
        avatar.className = class_name;
        avatar.src = participant.avatar_url;
        avatar.alt = "";
        avatar.setAttribute("aria-hidden", "true");
        return avatar;
    }
    const fallback = document.createElement("span");
    fallback.className = `${class_name} message-thread-avatar-fallback`;
    fallback.textContent = initials(participant.full_name);
    fallback.setAttribute("aria-hidden", "true");
    return fallback;
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

function summary_signature(summary: message_store.MessageThreadReplySummary | undefined): string {
    if (summary === undefined) {
        return "";
    }
    return [
        summary.reply_count,
        summary.unread_reply_count,
        summary.last_reply_at,
        summary.last_sender?.user_id ?? "",
        ...summary.participants.map((participant) =>
            [participant.user_id, participant.full_name, participant.avatar_url ?? ""].join(
                "\u0002",
            ),
        ),
    ].join("\u0001");
}

function document_is_available(): boolean {
    return typeof document !== "undefined";
}

function noop_trace_enhancer(_root: HTMLElement): void {
    return;
}

function pane_is_mobile(): boolean {
    return !ui_util.matches_viewport_state("gte_xl_min");
}

function focusable_elements(pane: HTMLElement): HTMLElement[] {
    return [
        ...pane.querySelectorAll<HTMLElement>(
            'button:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
        ),
    ].filter(
        (element) =>
            element.isConnected &&
            element.closest('[hidden], [inert], [aria-hidden="true"]') === null &&
            element.getClientRects().length > 0,
    );
}

function set_mobile_background_inert(enabled: boolean): void {
    if (!enabled) {
        for (const element of mobile_inerted_elements) {
            element.removeAttribute("inert");
        }
        mobile_inerted_elements = [];
        return;
    }
    const pane = pane_elements?.pane;
    // The pane lives inside .column-right; the modal background is everything
    // the column does not contain (its .app-main siblings) plus the pane's
    // own siblings inside the column (the user list).
    const column = pane?.parentElement;
    const parent = column?.parentElement;
    if (
        pane === undefined ||
        column === null ||
        column === undefined ||
        parent === null ||
        parent === undefined
    ) {
        return;
    }
    set_mobile_background_inert(false);
    for (const sibling of parent.children) {
        if (
            sibling instanceof HTMLElement &&
            sibling !== column &&
            !sibling.hasAttribute("inert")
        ) {
            sibling.setAttribute("inert", "");
            mobile_inerted_elements.push(sibling);
        }
    }
    for (const sibling of column.children) {
        if (sibling instanceof HTMLElement && sibling !== pane && !sibling.hasAttribute("inert")) {
            sibling.setAttribute("inert", "");
            mobile_inerted_elements.push(sibling);
        }
    }
}

function update_pane_layout(): void {
    const elements = pane_elements;
    if (elements === undefined || elements.pane.hidden) {
        return;
    }
    const mobile = pane_is_mobile();
    elements.pane.classList.toggle("message-thread-pane-mobile", mobile);
    document.body.classList.toggle("message-thread-pane-mobile-open", mobile);
    elements.pane.setAttribute("role", mobile ? "dialog" : "complementary");
    if (mobile) {
        elements.pane.setAttribute("aria-modal", "true");
        set_mobile_background_inert(true);
        if (!mobile_scroll_locked) {
            overlay_util.disable_scrolling();
            mobile_scroll_locked = true;
        }
    } else {
        elements.pane.removeAttribute("aria-modal");
        set_mobile_background_inert(false);
        if (mobile_scroll_locked) {
            overlay_util.enable_scrolling();
            mobile_scroll_locked = false;
        }
    }
}

function required_element(root: ParentNode, selector: string): HTMLElement {
    const element = root.querySelector<HTMLElement>(selector);
    if (element === null) {
        throw new Error(`message thread pane template is missing ${selector}`);
    }
    return element;
}

function required_button(root: ParentNode, selector: string): HTMLButtonElement {
    const element = root.querySelector<HTMLButtonElement>(selector);
    if (element?.tagName !== "BUTTON") {
        throw new TypeError(`message thread pane template ${selector} is not a button`);
    }
    return element;
}

function create_pane_elements(pane: HTMLElement): PaneElements {
    pane.replaceChildren();
    pane.innerHTML = render_message_thread_pane();
    const title = required_element(pane, "#message-thread-pane-title");
    pane.setAttribute("aria-labelledby", title.id);
    return {
        pane,
        title,
        stream_icon: required_element(pane, ".message-thread-pane-stream-icon"),
        agent_bar: required_element(pane, ".message-thread-agent-bar"),
        agent_prev: required_button(pane, ".message-thread-agent-cycle-prev"),
        agent_current: required_button(pane, ".message-thread-agent-current"),
        agent_next: required_button(pane, ".message-thread-agent-cycle-next"),
        body: required_element(pane, ".message-thread-pane-body"),
        panel: required_element(pane, "#message-thread-pane-panel"),
        activity: required_element(pane, ".message-thread-pane-activity"),
        replies: required_element(pane, ".message-thread-replies"),
        root: required_element(pane, ".message-thread-root"),
        separator: required_element(pane, ".message-thread-reply-separator"),
        reply_status: required_element(pane, ".message-thread-reply-status"),
        reply_list: required_element(pane, ".message-thread-reply-list"),
        older: required_button(pane, ".message-thread-load-older"),
        composer: required_element(pane, ".message-thread-composer-host"),
        trace_root: required_element(pane, ".message-thread-agent-trace"),
        trace_host: required_element(pane, ".pear-run-trace"),
        trace_status: required_element(pane, ".pear-run-trace-status"),
        close: required_button(pane, ".message-thread-pane-close"),
    };
}

function ensure_initialized(): PaneElements | undefined {
    if (!document_is_available()) {
        return undefined;
    }
    const pane = document.querySelector<HTMLElement>("#message-thread-pane");
    if (pane === null) {
        return undefined;
    }
    pane_elements ??= create_pane_elements(pane);
    if (!initialized) {
        initialized = true;
        pane_elements.close.addEventListener("click", () => {
            close();
        });
        pane_elements.agent_prev.addEventListener("click", () => {
            cycle_thread(-1, false);
        });
        pane_elements.agent_next.addEventListener("click", () => {
            cycle_thread(1, false);
        });
        pane_elements.agent_current.addEventListener("click", () => {
            cycle_thread(1, false);
        });
        pane_elements.older.addEventListener("click", load_earlier_replies);
        pane_elements.body.addEventListener("scroll", mark_visible_thread_replies_read, {
            passive: true,
        });
        document.addEventListener("click", handle_thread_button_click);
        document.addEventListener("keydown", handle_document_keydown);
        window.addEventListener("resize", update_pane_layout);
        window.addEventListener("focus", mark_visible_thread_replies_read);
        modals.register_pre_open_hook(() => {
            close({restore_focus: false});
        });
        overlays.register_pre_open_hook(() => {
            close({restore_focus: false});
        });
    }
    return pane_elements;
}

function run_views_for_root(
    message_id: number,
    replies = message_store.get_thread_replies(message_id),
): pear_run_state.PearRunLiveView[] {
    const messages = [message_store.get(message_id), ...replies].filter(
        (message) => message !== undefined,
    );
    const runs = new Map<number, pear_run_state.PearRunLiveView>();
    for (const message of messages) {
        const run = pear_run_state.get_run_view(message.id);
        if (run !== undefined) {
            runs.set(run.id, run);
        }
    }
    return [...runs.values()];
}

function agent_entries_for_open_message(): PaneAgentEntry[] {
    if (open_message_id === undefined) {
        return [];
    }
    const entries = new Map<string, PaneAgentEntry>();
    for (const run of run_views_for_root(open_message_id)) {
        for (const agent of run.agents ?? []) {
            entries.set(agent.thread_id, {agent, run});
        }
    }
    return [...entries.values()];
}

function render_agent_bar(agent_entries: PaneAgentEntry[]): void {
    const elements = pane_elements;
    if (elements === undefined || open_message_id === undefined) {
        return;
    }
    const selected_entry = agent_entries.find(({agent}) => agent.thread_id === selected_thread_key);
    const signature = `${selected_thread_key ?? ""}\u0001${agent_entries.length}\u0001${selected_entry === undefined ? "" : agent_signature(selected_entry.agent)}`;
    if (signature === agent_bar_signature) {
        return;
    }
    agent_bar_signature = signature;
    elements.agent_bar.hidden = agent_entries.length === 0;
    const current = elements.agent_current;
    current.replaceChildren();
    if (selected_entry === undefined) {
        delete current.dataset["threadKey"];
        current.classList.remove("message-thread-agent-current-dimmed");
        const label = document.createElement("span");
        label.className = "message-thread-agent-current-label";
        label.textContent = $t({defaultMessage: "Replies"});
        current.append(label);
        return;
    }
    const {agent} = selected_entry;
    const index = agent_entries.indexOf(selected_entry);
    current.dataset["threadKey"] = agent.thread_id;
    current.classList.toggle(
        "message-thread-agent-current-dimmed",
        agent_is_dimmed_for_testing(agent),
    );
    current.append(create_sigil(agent));
    const name = document.createElement("span");
    name.className = "message-thread-agent-current-label";
    name.textContent = display_name_for_testing(agent, index);
    current.append(name);
    const role = agent.role?.trim();
    if (role !== undefined && role !== "") {
        const role_element = document.createElement("span");
        role_element.className = "message-thread-agent-current-role";
        role_element.textContent = `(${role})`;
        current.append(role_element);
    }
    const status = document.createElement("span");
    status.className = "message-thread-agent-current-status";
    status.textContent = `· ${agent.status}`;
    current.append(status);
}

function resolved_recipient_for_message(message: message_store.Message): ResolvedComposeRecipient {
    if (message.type === "stream") {
        return {type: "stream", stream_id: message.stream_id, topic: message.topic};
    }
    const user_ids = people.pm_with_user_ids(message) ?? [];
    const display_recipient = Array.isArray(message.display_recipient)
        ? message.display_recipient
        : [];
    return {
        type: "private",
        user_ids,
        recipient_emails: display_recipient
            .filter((recipient) => user_ids.includes(recipient.id))
            .map((recipient) => recipient.email)
            .join(","),
    };
}

function destroy_human_thread_view(): void {
    if (human_thread_view === undefined) {
        return;
    }
    const {reply_list, editor, visibility_observer} = human_thread_view;
    visibility_observer?.disconnect();
    editor.destroy();
    message_fetch.cancel_retries_for_message_list_data(reply_list.data);
    message_lists.delete_message_list(reply_list);
    human_thread_view = undefined;
}

function destroy_root_thread_view(): void {
    if (root_thread_view === undefined) {
        return;
    }
    message_fetch.cancel_retries_for_message_list_data(root_thread_view.root_list.data);
    message_lists.delete_message_list(root_thread_view.root_list);
    root_thread_view = undefined;
}

function update_reply_list_chrome(): void {
    const elements = pane_elements;
    const view = human_thread_view;
    if (elements === undefined || view === undefined) {
        return;
    }
    // The pushed reply summary is the only reply count in the pane; a local
    // message list is a window onto the thread, not a count source.
    const count = message_store.get(view.message_id)?.reply_summary?.reply_count ?? 0;
    elements.separator.hidden = count === 0;
    elements.separator.textContent = $t(
        {defaultMessage: "{count, plural, one {# reply} other {# replies}}"},
        {count},
    );
    elements.older.hidden = !view.reply_list.data.fetch_status.can_load_older_messages();
    elements.older.disabled = false;
    elements.reply_status.textContent =
        count === 0 && view.reply_list.data.fetch_status.has_found_oldest()
            ? $t({defaultMessage: "No replies yet."})
            : "";
}

function ensure_root_message_loaded(message_id: number, callback: (loaded: boolean) => void): void {
    if (message_store.get(message_id) !== undefined) {
        callback(true);
        return;
    }
    const callbacks = root_load_callbacks_by_message_id.get(message_id);
    if (callbacks !== undefined) {
        callbacks.push(callback);
        return;
    }
    root_load_callbacks_by_message_id.set(message_id, [callback]);
    const data = new MessageListData({
        filter: new Filter([{operator: "id", operand: String(message_id)}]),
        excludes_muted_topics: false,
        excludes_muted_users: false,
    });
    root_load_data_by_message_id.set(message_id, data);
    function finish(loaded: boolean): void {
        if (root_load_data_by_message_id.get(message_id) !== data) {
            return;
        }
        root_load_data_by_message_id.delete(message_id);
        for (const [anchor_message_id, root_message_id] of pending_root_by_anchor_message_id) {
            if (root_message_id === message_id) {
                pending_root_by_anchor_message_id.delete(anchor_message_id);
            }
        }
        const pending = root_load_callbacks_by_message_id.get(message_id) ?? [];
        root_load_callbacks_by_message_id.delete(message_id);
        render_registered_rows(message_id);
        for (const pending_callback of pending) {
            pending_callback(loaded);
        }
    }
    message_fetch.load_messages({
        anchor: message_id,
        num_before: 0,
        num_after: 0,
        msg_list_data: data,
        suppress_connection_error: true,
        cont() {
            finish(message_store.get(message_id) !== undefined);
        },
        on_error() {
            finish(false);
        },
    });
}

function cancel_root_load(message_id: number): void {
    const data = root_load_data_by_message_id.get(message_id);
    if (data !== undefined) {
        message_fetch.cancel_retries_for_message_list_data(data);
        root_load_data_by_message_id.delete(message_id);
    }
    const pending = root_load_callbacks_by_message_id.get(message_id) ?? [];
    root_load_callbacks_by_message_id.delete(message_id);
    for (const [anchor_message_id, root_message_id] of pending_root_by_anchor_message_id) {
        if (root_message_id === message_id) {
            pending_root_by_anchor_message_id.delete(anchor_message_id);
        }
    }
    for (const callback of pending) {
        callback(false);
    }
}

function cached_unread_reply_count(
    message_id: number,
    replies = message_store.get_thread_replies(message_id),
): number {
    return replies.filter((message) => message.unread).length;
}

function unread_reply_count(
    message_id: number,
    replies = message_store.get_thread_replies(message_id),
): number {
    return (
        message_store.get(message_id)?.reply_summary?.unread_reply_count ??
        cached_unread_reply_count(message_id, replies)
    );
}

function set_unread_reply_count(message_id: number, count: number): boolean {
    const summary = message_store.get(message_id)?.reply_summary;
    if (summary === undefined) {
        return false;
    }
    // Only clamped below, not at reply_count: the local reply_count may be
    // stale while a summary push is in flight, and clamping against it
    // would drop unread increments for replies that just arrived. The
    // pushed summary re-clamps on apply.
    const next_count = Math.max(0, count);
    if (summary.unread_reply_count === next_count) {
        return false;
    }
    summary.unread_reply_count = next_count;
    // The summary remains the canonical Message projection. This generation is
    // only a fence against a fetch that began before this local mutation.
    message_store.mark_thread_reply_lifecycle([message_id]);
    render_registered_rows(message_id);
    return true;
}

function adjust_unread_reply_count(message_id: number, delta: number): void {
    const current = unread_reply_count(message_id);
    set_unread_reply_count(message_id, current + delta);
}

function mark_visible_thread_replies_read(): void {
    const elements = pane_elements;
    const view = human_thread_view;
    if (
        elements === undefined ||
        view === undefined ||
        selected_thread_key !== HUMAN_THREAD_KEY ||
        elements.pane.hidden ||
        !unread_ops.viewport_is_visible_and_focused()
    ) {
        return;
    }
    const viewport = elements.body.getBoundingClientRect();
    const visible_messages: message_store.Message[] = [];
    for (const row of elements.reply_list.querySelectorAll<HTMLElement>(".message_row")) {
        const message_id = Number(row.dataset["messageId"]);
        const message = message_store.get(message_id);
        if (!message?.unread) {
            continue;
        }
        const bounds = row.getBoundingClientRect();
        if (bounds.bottom > viewport.top && bounds.top < viewport.bottom) {
            visible_messages.push(message);
        }
    }
    if (visible_messages.length === 0) {
        return;
    }
    adjust_unread_reply_count(view.message_id, -visible_messages.length);
    unread_ops.notify_server_messages_read(visible_messages);
    for (const message of visible_messages) {
        render_registered_rows(message.id);
    }
    render_registered_rows(view.message_id);
}

function observe_reply_rows(): void {
    const view = human_thread_view;
    if (view === undefined) {
        return;
    }
    view.visibility_observer?.disconnect();
    if (typeof IntersectionObserver !== "undefined") {
        view.visibility_observer = new IntersectionObserver(
            () => {
                mark_visible_thread_replies_read();
            },
            {root: pane_elements?.body ?? null, threshold: 0.01},
        );
        for (const row of pane_elements?.reply_list.querySelectorAll(".message_row") ?? []) {
            view.visibility_observer.observe(row);
        }
    }
    mark_visible_thread_replies_read();
}

export function handle_added_messages(messages: message_store.Message[]): void {
    const root_ids = new Set<number>();
    for (const message of messages) {
        if (message.thread_root_message_id !== undefined) {
            root_ids.add(message.thread_root_message_id);
        }
        render_registered_rows(message.id);
    }
    // New replies change their roots' summaries. The server pushes the fresh
    // summary as a message_thread_summary event; here we only fence in-flight
    // fetches and keep the local unread count current until it arrives.
    message_store.mark_thread_reply_lifecycle(root_ids);
    for (const message of messages) {
        if (message.thread_root_message_id !== undefined && message.unread) {
            adjust_unread_reply_count(message.thread_root_message_id, 1);
        }
    }
    if (human_thread_view !== undefined && root_ids.has(human_thread_view.message_id)) {
        update_reply_list_chrome();
        observe_reply_rows();
    }
    for (const root_id of root_ids) {
        render_registered_rows(root_id);
    }
}

export function handle_summary_event(event: message_store.MessageThreadSummaryEvent): void {
    const root_message_id = event.root_message_id;
    if (!message_store.apply_thread_summary(root_message_id, event.summary)) {
        return;
    }
    render_registered_rows(root_message_id);
    if (open_message_id === root_message_id) {
        schedule_pane_render();
    }
}

export function handle_updated_messages(events: UpdateMessageEvent[]): void {
    const root_ids = new Set<number>();
    for (const event of events) {
        for (const message_id of event.message_ids) {
            const message = message_store.get(message_id);
            root_ids.add(message?.thread_root_message_id ?? message_id);
            render_registered_rows(message_id);
        }
    }
    for (const root_id of root_ids) {
        render_registered_rows(root_id);
    }
    if (open_message_id !== undefined && root_ids.has(open_message_id)) {
        human_thread_view?.editor.refresh_recipient();
        schedule_pane_render();
    }
}

export function handle_read_flag_update(message_ids: number[], marked_read: boolean): void {
    const root_deltas = new Map<number, number>();
    const root_ids = new Set<number>();
    for (const message_id of message_ids) {
        const message = message_store.get(message_id);
        if (message === undefined) {
            // Replies we never cached cannot be attributed to a root
            // locally; the next summary hydration reconciles them.
            continue;
        }
        const root_id = message.thread_root_message_id;
        if (root_id === undefined) {
            continue;
        }
        root_ids.add(root_id);
        if (marked_read ? message.unread : !message.unread) {
            root_deltas.set(root_id, (root_deltas.get(root_id) ?? 0) + (marked_read ? -1 : 1));
        }
    }
    for (const [root_id, delta] of root_deltas) {
        adjust_unread_reply_count(root_id, delta);
    }
    for (const root_id of root_ids) {
        render_registered_rows(root_id);
    }
}

export function handle_topic_marked_read(stream_id: number, topic: string): void {
    const root_ids = message_store
        .get_thread_roots()
        .filter(
            (message) =>
                message.type === "stream" &&
                message.stream_id === stream_id &&
                message.topic.toLocaleLowerCase() === topic.toLocaleLowerCase(),
        )
        .map((message) => message.id);
    for (const root_id of root_ids) {
        set_unread_reply_count(root_id, 0);
    }
}

export function get_compose_target(
    message_id: number,
): message_thread_hooks.ThreadComposeTarget | undefined {
    const view = human_thread_view;
    const message = message_store.get(message_id);
    if (
        view === undefined ||
        selected_thread_key !== HUMAN_THREAD_KEY ||
        message === undefined ||
        (message.id !== view.message_id && message.thread_root_message_id !== view.message_id)
    ) {
        return undefined;
    }
    return {
        textarea: view.editor.textarea,
        notify_content_changed: view.editor.notify_content_changed,
    };
}

export function handle_deleted_messages(message_ids: number[]): void {
    const deleted_ids = new Set(message_ids);
    if (open_message_id !== undefined && deleted_ids.has(open_message_id)) {
        close({restore_focus: false});
    }
    const root_ids = new Set<number>();
    const root_unread_deltas = new Map<number, number>();
    for (const message_id of message_ids) {
        const message = message_store.get(message_id);
        if (message === undefined) {
            // Replies we never cached cannot be attributed to a root locally;
            // the pushed summary still updates the root's other fields.
            continue;
        }
        const root_id = message.thread_root_message_id;
        if (root_id === undefined || deleted_ids.has(root_id)) {
            continue;
        }
        root_ids.add(root_id);
        if (message.unread) {
            root_unread_deltas.set(root_id, (root_unread_deltas.get(root_id) ?? 0) - 1);
        }
    }
    // Deleting replies changes their roots' summaries. The server pushes the
    // fresh summary as a message_thread_summary event (with no unread
    // count); fence in-flight fetches and settle the local unread count here.
    // This must run before unread_ops flips the deleted messages' unread
    // booleans, so the dispatch order in server_events_dispatch.js matters.
    message_store.mark_thread_reply_lifecycle(root_ids);
    for (const [root_id, delta] of root_unread_deltas) {
        adjust_unread_reply_count(root_id, delta);
    }
    for (const root_id of root_ids) {
        render_registered_rows(root_id);
    }
}

function secondary_view_options(): {
    manages_primary_viewport: false;
    show_recipient_headers: false;
    show_thread_previews: false;
    suppress_row_chrome: true;
} {
    return {
        manages_primary_viewport: false,
        show_recipient_headers: false,
        show_thread_previews: false,
        suppress_row_chrome: true,
    };
}

function mount_root_thread_view(message_id: number): void {
    const elements = pane_elements;
    const root_message = message_store.get(message_id);
    if (elements === undefined || root_message === undefined) {
        return;
    }
    if (root_thread_view?.message_id === message_id) {
        return;
    }
    destroy_root_thread_view();
    elements.root.replaceChildren();
    const root_list = new MessageList({
        data: new MessageListData({
            filter: new Filter([{operator: "id", operand: String(message_id)}]),
            excludes_muted_topics: false,
            excludes_muted_users: false,
        }),
        reading_prevented: true,
        view_options: {
            ...secondary_view_options(),
            mount: elements.root,
            role: "list",
            aria_label: $t({defaultMessage: "Thread root message"}),
        },
    });
    message_lists.add_rendered_message_list(root_list);
    root_list.add_messages([root_message], {}, true);
    if (root_message.unread && unread_ops.viewport_is_visible_and_focused()) {
        unread_ops.notify_server_message_read(root_message);
    }
    root_thread_view = {message_id, root_list};
}

function mount_human_thread_view(message_id: number): void {
    const elements = pane_elements;
    const root_message = message_store.get(message_id);
    if (elements === undefined || root_message === undefined) {
        return;
    }
    if (human_thread_view?.message_id === message_id) {
        return;
    }
    destroy_human_thread_view();
    elements.reply_list.replaceChildren();
    elements.reply_status.textContent = $t({defaultMessage: "Loading replies…"});
    elements.older.hidden = true;

    const reply_list = new MessageList({
        data: new MessageListData({
            filter: new Filter([{operator: "thread", operand: message_id}]),
            excludes_muted_topics: false,
            excludes_muted_users: false,
        }),
        view_options: {
            ...secondary_view_options(),
            mount: elements.reply_list,
            role: "list",
            aria_label: $t({defaultMessage: "Replies"}),
        },
    });
    message_lists.add_rendered_message_list(reply_list);

    // Paint from the client store immediately -- the replies we already
    // know about (events, prior fetches) render with no network wait,
    // and the narrow fetch below reconciles anything missing. This is
    // what makes the pane feel as instant as the feed.
    const cached_replies = message_store.get_thread_replies(message_id);
    if (cached_replies.length > 0) {
        reply_list.add_messages(cached_replies, {}, true);
        update_reply_list_chrome();
        observe_reply_rows();
    }

    const editor = compose_editor.create({
        $host: $(elements.composer),
        get_recipient() {
            const current_root = message_store.get(message_id);
            return current_root === undefined
                ? undefined
                : resolved_recipient_for_message(current_root);
        },
        thread_root_message_id: message_id,
        placeholder: $t({defaultMessage: "Reply…"}),
        aria_label: $t({defaultMessage: "Reply to this message"}),
        send_label: $t({defaultMessage: "Send reply"}),
    });
    human_thread_view = {message_id, reply_list, editor};
    message_fetch.load_messages_for_narrow({
        anchor: "newest",
        msg_list: reply_list,
        cont() {
            if (human_thread_view?.reply_list !== reply_list) {
                return;
            }
            update_reply_list_chrome();
            observe_reply_rows();
        },
    });
}

function load_earlier_replies(): void {
    const view = human_thread_view;
    const elements = pane_elements;
    if (view === undefined || elements === undefined) {
        return;
    }
    elements.older.disabled = true;
    message_fetch.maybe_load_older_messages({
        msg_list: view.reply_list,
        msg_list_data: view.reply_list.data,
        cont() {
            if (human_thread_view?.reply_list === view.reply_list) {
                update_reply_list_chrome();
                observe_reply_rows();
            }
        },
    });
}

function mount_selected_agent_trace(selected: PaneAgentEntry): void {
    const elements = pane_elements;
    if (elements === undefined) {
        return;
    }
    const {agent, run} = selected;
    if (trace_thread_id === agent.thread_id && trace_controller !== undefined) {
        return;
    }
    trace_controller?.destroy();
    trace_thread_id = agent.thread_id;
    elements.trace_host.replaceChildren();
    elements.trace_host.dataset["pearThreadId"] = agent.thread_id;
    trace_controller = pear_run_trace.create_controller({
        run_id: run.id,
        thread_id: agent.thread_id,
        host: elements.trace_host,
        status: elements.trace_status,
        after_render() {
            pane_trace_enhancer?.(elements.trace_root);
        },
        on_state_change(controller) {
            elements.panel.toggleAttribute("aria-busy", controller.in_flight);
            elements.trace_root.dataset["state"] = controller.request_state;
        },
    });
    trace_controller.load();
}

function render_pane(options: {scroll_replies_to_end?: boolean} = {}): void {
    const elements = ensure_initialized();
    if (elements === undefined || open_message_id === undefined) {
        close();
        return;
    }
    // The header echoes the feed's topic header: the stream's colored hash
    // plus the conversation. The mobile sheet shows it in the pane's own
    // header; on desktop the pane is headerless and the sidebar's filter
    // row carries the tabs.
    const root_message = message_store.get(open_message_id);
    if (root_message?.type === "stream" && typeof root_message.display_recipient === "string") {
        const stream_name = root_message.display_recipient;
        const topic = root_message.topic.trim();
        elements.title.textContent = topic === "" ? stream_name : `${stream_name} > ${topic}`;
        elements.stream_icon.style.color = stream_data.get_color(
            stream_data.get_sub_by_name(stream_name)?.stream_id,
        );
    } else {
        elements.title.textContent = $t({defaultMessage: "Direct messages"});
        elements.stream_icon.style.color = "";
    }
    const agent_entries = agent_entries_for_open_message();
    const agents = agent_entries.map(({agent}) => agent);
    selected_thread_key = selection_after_update_for_testing(agents, selected_thread_key);
    mount_root_thread_view(open_message_id);
    render_agent_bar(agent_entries);
    if (selected_thread_key === HUMAN_THREAD_KEY) {
        mount_human_thread_view(open_message_id);
        trace_controller?.destroy();
        trace_controller = undefined;
        trace_thread_id = undefined;
        elements.replies.hidden = false;
        elements.composer.hidden = false;
        elements.trace_root.hidden = true;
        elements.activity.hidden = true;
        elements.panel.setAttribute("aria-label", $t({defaultMessage: "Replies"}));
        update_reply_list_chrome();
        if (options.scroll_replies_to_end === true) {
            human_thread_view?.reply_list.view.$list
                .find(".message_row")
                .last()
                .get(0)
                ?.scrollIntoView({block: "end"});
        }
    } else {
        destroy_human_thread_view();
        const selected = agent_entries.find(({agent}) => agent.thread_id === selected_thread_key);
        if (selected === undefined) {
            selected_thread_key = HUMAN_THREAD_KEY;
            render_pane();
            return;
        }
        const {agent} = selected;
        const index = agents.indexOf(agent);
        elements.panel.setAttribute(
            "aria-label",
            display_name_for_testing(agent, Math.max(index, 0)),
        );
        elements.replies.hidden = true;
        elements.composer.hidden = true;
        elements.trace_root.hidden = false;
        const current_activity = agent.current_activity?.trim();
        // The cycler already carries the status; the activity line only
        // appears when the agent reports live activity.
        if (current_activity !== undefined && current_activity !== "") {
            elements.activity.hidden = false;
            elements.activity.textContent = current_activity;
        } else {
            elements.activity.hidden = true;
            elements.activity.textContent = "";
        }
        mount_selected_agent_trace(selected);
    }
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
    thread_key: string | undefined,
): void {
    const selector =
        thread_key === HUMAN_THREAD_KEY
            ? ".message-thread-reply-launch"
            : `.message-thread-agent-launch[data-thread-id="${CSS.escape(thread_key ?? "")}"]`;
    const fallback =
        message_id === undefined
            ? undefined
            : document.querySelector<HTMLElement>(
                  `.message_row[data-message-id="${message_id}"] ${selector}`,
              );
    const launch_target =
        anchor?.isConnected === true && anchor.closest("[hidden]") === null
            ? anchor
            : fallback?.closest("[hidden]") === null
              ? fallback
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

function select_thread(thread_key: string, focus_current: boolean): void {
    if (
        thread_key === selected_thread_key &&
        (thread_key === HUMAN_THREAD_KEY || trace_controller !== undefined)
    ) {
        return;
    }
    selected_thread_key = thread_key;
    render_pane({scroll_replies_to_end: thread_key === HUMAN_THREAD_KEY});
    if (focus_current && !pane_elements?.agent_bar.hidden) {
        pane_elements?.agent_current.focus({preventScroll: true});
    }
}

function cycle_thread(delta: number, focus_current: boolean): void {
    if (open_message_id === undefined) {
        return;
    }
    const order = cycle_order_for_testing(agent_entries_for_open_message().map(({agent}) => agent));
    if (order.length < 2) {
        return;
    }
    select_thread(cycle_selection_for_testing(order, selected_thread_key, delta), focus_current);
}

function thread_composer_has_content(): boolean {
    return human_thread_view !== undefined && human_thread_view.editor.textarea.value.trim() !== "";
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
    if (
        event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.shiftKey &&
        (event.key === "ArrowLeft" || event.key === "ArrowRight")
    ) {
        // codex-cli-style thread cycling; a non-empty composer keeps
        // Alt+arrow for its own text navigation.
        if (thread_composer_has_content()) {
            return;
        }
        cycle_thread(event.key === "ArrowRight" ? 1 : -1, true);
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
    } else if (!elements.pane.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus({preventScroll: true});
    } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus({preventScroll: true});
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus({preventScroll: true});
    }
}

function open(
    message_id: number,
    thread_key: string,
    anchor: HTMLElement,
    enhance_trace: TraceEnhancer,
    options: {focus_composer?: boolean} = {},
): void {
    const elements = ensure_initialized();
    if (elements === undefined) {
        return;
    }
    cancel_focus_frame(pane_restore_focus_frame);
    pane_restore_focus_frame = undefined;
    if (open_message_id !== message_id) {
        destroy_human_thread_view();
        destroy_root_thread_view();
    }
    anchor_element = anchor;
    anchor_message_id = message_id;
    anchor_thread_key = thread_key;
    pane_trace_enhancer = enhance_trace;
    open_message_id = message_id;
    selected_thread_key = thread_key;
    // Opening a thread switches the sidebar to the Agents tab, where the
    // pane lives; the tab strip switches back to People.
    right_sidebar_tabs.set_tab("agents");
    if (document.body.classList.contains("hide-right-sidebar")) {
        // The pane borrows the sidebar's slot while it is open: restore
        // the slot (without persisting a preference change) so the pane
        // never overlays the feed's collapsed-margin edge; close()
        // returns the loan.
        sidebar_hidden_before_pane = true;
        document.body.classList.remove("hide-right-sidebar");
        document.querySelector("#userlist-toggle-button")?.setAttribute("aria-expanded", "true");
    }
    elements.pane.hidden = false;
    elements.pane.setAttribute("aria-hidden", "false");
    document.body.classList.add("message-thread-pane-open");
    render_pane({scroll_replies_to_end: thread_key === HUMAN_THREAD_KEY});
    cancel_focus_frame(pane_open_focus_frame);
    const opening_message_id = message_id;
    pane_open_focus_frame = window.requestAnimationFrame(() => {
        pane_open_focus_frame = undefined;
        if (elements.pane.hidden || open_message_id !== opening_message_id) {
            return;
        }
        if (options.focus_composer === true && thread_key === HUMAN_THREAD_KEY) {
            human_thread_view?.editor.focus();
        } else if (thread_key !== HUMAN_THREAD_KEY && !elements.agent_bar.hidden) {
            elements.agent_current.focus({preventScroll: true});
        } else {
            elements.close.focus({preventScroll: true});
        }
    });
}

function open_after_root_loaded(
    message_id: number,
    thread_key: string,
    anchor: HTMLElement,
    enhance_trace: TraceEnhancer,
    options: {focus_composer?: boolean} = {},
): void {
    const anchor_message_id = Number(
        anchor.closest<HTMLElement>(".message_row")?.dataset["messageId"],
    );
    if (Number.isSafeInteger(anchor_message_id) && anchor_message_id !== message_id) {
        pending_root_by_anchor_message_id.set(anchor_message_id, message_id);
    }
    anchor.setAttribute("aria-busy", "true");
    ensure_root_message_loaded(message_id, (loaded) => {
        anchor.removeAttribute("aria-busy");
        if (loaded) {
            open(message_id, thread_key, anchor, enhance_trace, options);
        }
    });
}

function handle_thread_button_click(event: MouseEvent): void {
    if (!(event.target instanceof Element)) {
        return;
    }
    const button = event.target.closest<HTMLElement>(".message_thread_button");
    const row = button?.closest<HTMLElement>(".message_row");
    const message_id = Number(row?.dataset["messageId"]);
    if (button === null || row === undefined || row === null || !Number.isSafeInteger(message_id)) {
        return;
    }
    const root_message_id = message_store.get(message_id)?.thread_root_message_id ?? message_id;
    open_after_root_loaded(
        root_message_id,
        HUMAN_THREAD_KEY,
        button,
        trace_enhancer_by_row.get(row) ?? noop_trace_enhancer,
        {
            focus_composer: true,
        },
    );
    event.preventDefault();
    event.stopPropagation();
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
    const focus_thread_key = anchor_thread_key;
    destroy_human_thread_view();
    destroy_root_thread_view();
    trace_controller?.destroy();
    trace_controller = undefined;
    trace_thread_id = undefined;
    open_message_id = undefined;
    selected_thread_key = undefined;
    agent_bar_signature = "";
    elements.pane.hidden = true;
    elements.pane.setAttribute("aria-hidden", "true");
    set_mobile_background_inert(false);
    document.body.classList.remove("message-thread-pane-open", "message-thread-pane-mobile-open");
    if (sidebar_hidden_before_pane) {
        // Return the sidebar-slot loan from open().
        sidebar_hidden_before_pane = false;
        document.body.classList.add("hide-right-sidebar");
        document.querySelector("#userlist-toggle-button")?.setAttribute("aria-expanded", "false");
    }
    // Restore the tab's own label in the sidebar header context.
    right_sidebar_tabs.refresh();
    if (mobile_scroll_locked) {
        overlay_util.enable_scrolling();
        mobile_scroll_locked = false;
    }
    const anchor = anchor_element;
    anchor_element = undefined;
    anchor_message_id = undefined;
    anchor_thread_key = undefined;
    pane_trace_enhancer = undefined;
    if (options.restore_focus !== false) {
        pane_restore_focus_frame = window.requestAnimationFrame(() => {
            pane_restore_focus_frame = undefined;
            restore_launch_focus(anchor, focus_message_id, focus_thread_key);
        });
    }
}

export function destroy_for_message(message_id: number): void {
    const pending_root_id = pending_root_by_anchor_message_id.get(message_id);
    pending_root_by_anchor_message_id.delete(message_id);
    if (pending_root_id !== undefined) {
        cancel_root_load(pending_root_id);
    }
    cancel_root_load(message_id);
    if (anchor_message_id === message_id) {
        close({restore_focus: false});
    }
}

export function open_for_draft(thread_root_message_id: number): void {
    const elements = ensure_initialized();
    if (elements === undefined) {
        return;
    }
    const row = document.querySelector<HTMLElement>(
        `.message_row[data-message-id="${thread_root_message_id}"]`,
    );
    const anchor =
        row?.querySelector<HTMLElement>(".message-thread-reply-launch, .message_thread_button") ??
        (document.activeElement instanceof HTMLElement ? document.activeElement : elements.pane);
    open_after_root_loaded(
        thread_root_message_id,
        HUMAN_THREAD_KEY,
        anchor,
        trace_enhancer_by_row.get(row ?? elements.pane) ?? noop_trace_enhancer,
        {focus_composer: true},
    );
}

function create_reply_preview(
    message_id: number,
    summary: message_store.MessageThreadReplySummary,
    unread_count: number,
    enhance_trace: TraceEnhancer,
): HTMLElement {
    const cluster = document.createElement("div");
    cluster.className = "message-thread-reply-preview";
    const avatars = document.createElement("span");
    avatars.className = "message-thread-reply-avatars";
    for (const participant of summary.participants.slice(0, 4)) {
        avatars.append(create_avatar(participant, "message-thread-preview-avatar"));
    }
    const launch = document.createElement("button");
    launch.className = "message-thread-reply-launch";
    launch.type = "button";
    launch.textContent = $t(
        {defaultMessage: "{count, plural, one {# reply} other {# replies}}"},
        {count: summary.reply_count},
    );
    launch.setAttribute(
        "aria-label",
        $t(
            {defaultMessage: "Open {count, plural, one {# reply} other {# replies}}"},
            {count: summary.reply_count},
        ),
    );
    launch.addEventListener("click", () => {
        open_after_root_loaded(message_id, HUMAN_THREAD_KEY, launch, enhance_trace);
    });
    if (unread_count > 0) {
        const unread = document.createElement("span");
        unread.className = "message-thread-unread-count";
        unread.textContent = String(unread_count);
        unread.setAttribute(
            "aria-label",
            $t(
                {defaultMessage: "{count, plural, one {# unread reply} other {# unread replies}}"},
                {count: unread_count},
            ),
        );
        launch.append(unread);
    }
    const last_reply = document.createElement("span");
    last_reply.className = "message-thread-last-reply";
    last_reply.textContent = $t(
        {defaultMessage: "Last reply {time}"},
        {
            time: timerender.relative_time_string_from_date(
                new Date(summary.last_reply_at * 1000),
                true,
            ),
        },
    );
    cluster.append(avatars, launch, last_reply);
    return cluster;
}

function create_agent_preview(
    message_id: number,
    agents: pear_run_state.PearAgentThread[],
    enhance_trace: TraceEnhancer,
): HTMLElement {
    const cluster = document.createElement("div");
    cluster.className = "message-thread-agent-preview";
    const badges = document.createElement("span");
    badges.className = "message-thread-agent-badges";
    for (const [index, agent] of agents.slice(0, 4).entries()) {
        const button = document.createElement("button");
        const name = display_name_for_testing(agent, index);
        button.className = "message-thread-agent-launch";
        button.type = "button";
        button.dataset["threadId"] = agent.thread_id;
        button.setAttribute("aria-label", $t({defaultMessage: "Open {name}"}, {name}));
        button.setAttribute("title", `${name} · ${agent.status}`);
        button.append(create_sigil(agent));
        button.addEventListener("click", () => {
            open_after_root_loaded(message_id, agent.thread_id, button, enhance_trace);
        });
        badges.append(button);
    }
    const count = document.createElement("button");
    count.className = "message-thread-agent-count";
    count.type = "button";
    count.textContent = $t(
        {defaultMessage: "{count, plural, one {# agent} other {# agents}}"},
        {count: agents.length},
    );
    const first_agent = agents[0];
    if (first_agent !== undefined) {
        count.addEventListener("click", () => {
            open_after_root_loaded(message_id, first_agent.thread_id, count, enhance_trace);
        });
    }
    cluster.append(badges, count);
    return cluster;
}

export function render_for_message_row(
    row: HTMLElement,
    message_id: number,
    enhance_trace: TraceEnhancer,
): void {
    ensure_initialized();
    const host = row.querySelector<HTMLElement>(".message-thread-host");
    if (host === null) {
        return;
    }
    if (row.closest("#message-thread-pane") !== null) {
        // Rows inside the pane render chrome-stripped; the pane itself is
        // the thread UI, so a launch strip would recurse into it.
        host.hidden = true;
        host.replaceChildren();
        delete host.dataset["signature"];
        return;
    }
    trace_enhancer_by_row.set(row, enhance_trace);
    const message = message_store.get(message_id);
    if (message === undefined) {
        host.hidden = true;
        host.replaceChildren();
        delete host.dataset["signature"];
        return;
    }
    const root_message_id = message?.thread_root_message_id ?? message_id;
    if (message.thread_root_message_id !== undefined) {
        const signature = `child\u0001${root_message_id}\u0001${String(message.unread)}`;
        if (host.dataset["signature"] !== signature) {
            host.dataset["signature"] = signature;
            host.hidden = false;
            const strip = document.createElement("div");
            strip.className = "message-thread-strip message-thread-child-context";
            strip.setAttribute("role", "group");
            strip.setAttribute("aria-label", $t({defaultMessage: "Message thread"}));
            const launch = document.createElement("button");
            launch.className = "message-thread-reply-launch message-thread-child-launch";
            launch.type = "button";
            launch.textContent = $t({defaultMessage: "View thread"});
            if (message.unread) {
                const unread = document.createElement("span");
                unread.className = "message-thread-unread-dot";
                unread.setAttribute("aria-label", $t({defaultMessage: "Unread reply"}));
                launch.append(unread);
            }
            launch.addEventListener("click", () => {
                open_after_root_loaded(root_message_id, HUMAN_THREAD_KEY, launch, enhance_trace, {
                    focus_composer: true,
                });
            });
            strip.append(launch);
            host.replaceChildren(strip);
        }
        if (message_store.get(root_message_id) === undefined) {
            pending_root_by_anchor_message_id.set(message_id, root_message_id);
            ensure_root_message_loaded(root_message_id, (loaded) => {
                if (loaded && row.isConnected) {
                    render_for_message_row(row, message_id, enhance_trace);
                }
            });
        }
        return;
    }
    const root_message = message_store.get(root_message_id);
    const summary = root_message?.reply_summary;
    const replies = message_store.get_thread_replies(root_message_id);
    const runs = run_views_for_root(root_message_id, replies);
    const agents_by_thread_id = new Map<string, pear_run_state.PearAgentThread>();
    for (const run of runs) {
        for (const agent of run.agents ?? []) {
            agents_by_thread_id.set(agent.thread_id, agent);
        }
    }
    const agents = [...agents_by_thread_id.values()];
    if (summary === undefined && agents.length === 0) {
        host.hidden = true;
        host.replaceChildren();
        delete host.dataset["signature"];
        return;
    }
    const unread_count = unread_reply_count(root_message_id, replies);
    const signature = `${summary_signature(summary)}\u0001${unread_count}\u0001${runs.map((run) => run.id).join(",")}\u0001${agents.map((agent) => agent_signature(agent)).join("\u0001")}`;
    if (host.dataset["signature"] === signature) {
        return;
    }
    host.dataset["signature"] = signature;
    host.hidden = false;
    const strip = document.createElement("div");
    strip.className = "message-thread-strip";
    strip.setAttribute("role", "group");
    strip.setAttribute("aria-label", $t({defaultMessage: "Message threads"}));
    if (summary !== undefined) {
        strip.append(create_reply_preview(root_message_id, summary, unread_count, enhance_trace));
    }
    if (summary !== undefined && agents.length > 0) {
        const divider = document.createElement("span");
        divider.className = "message-thread-strip-divider";
        divider.setAttribute("aria-hidden", "true");
        strip.append(divider);
    }
    if (agents.length > 0) {
        strip.append(create_agent_preview(root_message_id, agents, enhance_trace));
    }
    host.replaceChildren(strip);
}

function render_registered_rows(message_id: number): void {
    for (const row of document.querySelectorAll<HTMLElement>(
        `.message_row[data-message-id="${message_id}"]`,
    )) {
        const enhancer = trace_enhancer_by_row.get(row);
        if (enhancer !== undefined) {
            render_for_message_row(row, message_id, enhancer);
        }
    }
}

pear_run_state.on_run_state_changed((run, event) => {
    if (!document_is_available()) {
        return;
    }
    const message_id = run.result_message_id;
    if (typeof message_id !== "number") {
        return;
    }
    const root_message_id = message_store.get(message_id)?.thread_root_message_id ?? message_id;
    render_registered_rows(message_id);
    render_registered_rows(root_message_id);
    if (root_message_id !== open_message_id) {
        return;
    }
    if (event?.event_type === "agent" && event.agent_thread_id === selected_thread_key) {
        trace_controller?.mark_dirty(true);
    }
    schedule_pane_render();
});

export function clear_for_testing(): void {
    close({restore_focus: false});
    destroy_human_thread_view();
    destroy_root_thread_view();
    root_load_callbacks_by_message_id.clear();
    for (const data of root_load_data_by_message_id.values()) {
        message_fetch.cancel_retries_for_message_list_data(data);
    }
    root_load_data_by_message_id.clear();
    pending_root_by_anchor_message_id.clear();
}

message_thread_hooks.register({
    close,
    destroy_for_message,
    get_compose_target,
    handle_topic_marked_read,
    open_for_draft,
    open_thread: open_after_root_loaded,
    render_for_message_row,
});
