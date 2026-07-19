import $ from "jquery";
import * as z from "zod/mini";

import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as message_store from "./message_store.ts";
import * as modals from "./modals.ts";
import * as overlay_util from "./overlay_util.ts";
import * as overlays from "./overlays.ts";
import * as pear_run_state from "./pear_run_state.ts";
import * as pear_run_trace from "./pear_run_trace.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as timerender from "./timerender.ts";
import * as ui_util from "./ui_util.ts";

const HUMAN_THREAD_KEY = "replies";
const REPLY_PAGE_SIZE = 50;

const message_thread_reply_schema = z.object({
    id: z.number(),
    message_id: z.number(),
    sender_id: z.number(),
    sender_full_name: z.string(),
    avatar_url: z.nullish(z.string()),
    content: z.string(),
    content_type: z.literal("text/html"),
    timestamp: z.number(),
});

const message_thread_response_schema = z.object({
    reply_summary: z.nullable(message_store.message_thread_reply_summary_schema),
    replies: z.array(message_thread_reply_schema),
    has_more: z.boolean(),
});

const message_thread_post_response_schema = z.object({
    reply_summary: message_store.message_thread_reply_summary_schema,
    reply: message_thread_reply_schema,
});

export const message_thread_event_schema = z.object({
    type: z.literal("message_thread"),
    message_id: z.number(),
    reply_summary: message_store.message_thread_reply_summary_schema,
    reply: message_thread_reply_schema,
});

type MessageThreadReply = z.infer<typeof message_thread_reply_schema>;
type MessageThreadEvent = z.infer<typeof message_thread_event_schema>;
type TraceEnhancer = (root: HTMLElement) => void;

type ReplyState = {
    summary?: message_store.MessageThreadReplySummary | undefined;
    replies: Map<number, MessageThreadReply>;
    draft: string;
    has_more: boolean;
    loaded: boolean;
    failed: boolean;
    request?: JQuery.jqXHR<unknown> | undefined;
    request_generation: number;
    posting: boolean;
    post_failed: boolean;
};

type PaneElements = {
    pane: HTMLElement;
    title: HTMLElement;
    meta: HTMLElement;
    tabs: HTMLElement;
    panel: HTMLElement;
    activity: HTMLElement;
    replies: HTMLElement;
    reply_status: HTMLElement;
    reply_list: HTMLElement;
    older: HTMLButtonElement;
    composer: HTMLFormElement;
    textarea: HTMLTextAreaElement;
    submit: HTMLButtonElement;
    trace_root: HTMLElement;
    trace_host: HTMLElement;
    trace_status: HTMLElement;
    close: HTMLButtonElement;
};

const reply_state_by_message_id = new Map<number, ReplyState>();
const trace_enhancer_by_row = new WeakMap<HTMLElement, TraceEnhancer>();
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
let initialized = false;
let tabs_signature = "";
let pane_render_frame: number | undefined;
let pane_open_focus_frame: number | undefined;
let pane_restore_focus_frame: number | undefined;

function reply_state(message_id: number): ReplyState {
    let state = reply_state_by_message_id.get(message_id);
    if (state === undefined) {
        state = {
            replies: new Map(),
            draft: "",
            has_more: false,
            loaded: false,
            failed: false,
            request_generation: 0,
            posting: false,
            post_failed: false,
        };
        reply_state_by_message_id.set(message_id, state);
    }
    return state;
}

function summary_is_at_least_as_new(
    incoming: message_store.MessageThreadReplySummary,
    current: message_store.MessageThreadReplySummary | undefined,
): boolean {
    return (
        current === undefined ||
        incoming.reply_count > current.reply_count ||
        (incoming.reply_count === current.reply_count &&
            incoming.last_reply_at >= current.last_reply_at)
    );
}

export function remember_reply_summary(
    message_id: number,
    summary: message_store.MessageThreadReplySummary | undefined,
): void {
    if (summary === undefined) {
        return;
    }
    const state = reply_state(message_id);
    if (!summary_is_at_least_as_new(summary, state.summary)) {
        return;
    }
    state.summary = summary;
    const message = message_store.get(message_id);
    if (message !== undefined) {
        message.reply_summary = summary;
    }
}

function merge_reply(state: ReplyState, reply: MessageThreadReply): boolean {
    if (state.replies.has(reply.id)) {
        return false;
    }
    state.replies.set(reply.id, reply);
    return true;
}

function ordered_replies(state: ReplyState): MessageThreadReply[] {
    return [...state.replies.values()].sort(
        (left, right) => left.timestamp - right.timestamp || left.id - right.id,
    );
}

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
): string {
    if (selected === HUMAN_THREAD_KEY) {
        return HUMAN_THREAD_KEY;
    }
    if (selected !== undefined && agents.some((agent) => agent.thread_id === selected)) {
        return selected;
    }
    return agents[0]?.thread_id ?? HUMAN_THREAD_KEY;
}

export function parse_thread_event_for_testing(raw: unknown): MessageThreadEvent | undefined {
    const result = message_thread_event_schema.safeParse(raw);
    return result.success ? result.data : undefined;
}

export function reply_state_count_for_testing(): number {
    return reply_state_by_message_id.size;
}

function create_sigil(agent: pear_run_state.PearAgentThread): HTMLElement {
    const sigil = document.createElement("span");
    sigil.className = `message-thread-agent-sigil message-thread-agent-sigil-tone-${hash_thread_id(agent.thread_id) % 6}`;
    sigil.setAttribute("aria-hidden", "true");
    for (const active of sigil_cells_for_testing(agent.thread_id)) {
        const cell = document.createElement("i");
        cell.className = active
            ? "message-thread-agent-sigil-cell message-thread-agent-sigil-cell-on"
            : "message-thread-agent-sigil-cell";
        sigil.append(cell);
    }
    const status = document.createElement("span");
    status.className = `message-thread-agent-status message-thread-agent-status-${status_tone_for_testing(agent.status)}`;
    sigil.append(status);
    return sigil;
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
    participant: z.infer<typeof message_store.message_thread_participant_schema>,
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
        summary.last_reply_at,
        summary.last_sender?.user_id ?? "",
        ...summary.participants.map((participant) =>
            [participant.user_id, participant.full_name, participant.avatar_url ?? ""].join("\u0002"),
        ),
    ].join("\u0001");
}

function document_is_available(): boolean {
    return typeof document !== "undefined" && typeof document.querySelector === "function";
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
    header.className = "message-thread-pane-header";
    const heading_group = document.createElement("div");
    heading_group.className = "message-thread-pane-heading";
    const eyebrow = document.createElement("span");
    eyebrow.className = "message-thread-pane-eyebrow";
    eyebrow.textContent = $t({defaultMessage: "Conversation"});
    const title = document.createElement("h2");
    title.id = "message-thread-pane-title";
    title.textContent = $t({defaultMessage: "Thread"});
    const meta = document.createElement("span");
    meta.className = "message-thread-pane-meta";
    heading_group.append(eyebrow, title, meta);
    const close_button = document.createElement("button");
    close_button.className = "message-thread-pane-close";
    close_button.type = "button";
    close_button.setAttribute("aria-label", $t({defaultMessage: "Close thread"}));
    close_button.textContent = "×";
    header.append(heading_group, close_button);

    const tabs = document.createElement("div");
    tabs.className = "message-thread-pane-tabs";
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", $t({defaultMessage: "Message threads"}));

    const body = document.createElement("section");
    body.className = "message-thread-pane-body";
    const activity = document.createElement("div");
    activity.className = "message-thread-pane-activity";
    activity.setAttribute("aria-live", "polite");
    const panel = document.createElement("div");
    panel.className = "message-thread-pane-panel";
    panel.id = "message-thread-pane-panel";
    panel.setAttribute("role", "tabpanel");

    const replies = document.createElement("div");
    replies.className = "message-thread-replies";
    const reply_status = document.createElement("div");
    reply_status.className = "message-thread-reply-status";
    reply_status.setAttribute("role", "status");
    reply_status.setAttribute("aria-live", "polite");
    const older = document.createElement("button");
    older.className = "message-thread-load-older";
    older.type = "button";
    older.textContent = $t({defaultMessage: "Load earlier replies"});
    older.hidden = true;
    const reply_list = document.createElement("div");
    reply_list.className = "message-thread-reply-list";
    reply_list.setAttribute("role", "feed");
    reply_list.setAttribute("aria-label", $t({defaultMessage: "Replies"}));
    const composer = document.createElement("form");
    composer.className = "message-thread-composer";
    const textarea = document.createElement("textarea");
    textarea.className = "message-thread-composer-input";
    textarea.rows = 3;
    textarea.placeholder = $t({defaultMessage: "Reply…"});
    textarea.setAttribute("aria-label", $t({defaultMessage: "Reply to this message"}));
    const submit = document.createElement("button");
    submit.className = "message-thread-composer-submit";
    submit.type = "submit";
    submit.textContent = $t({defaultMessage: "Reply"});
    composer.append(textarea, submit);
    replies.append(reply_status, older, reply_list, composer);

    const trace_root = document.createElement("div");
    trace_root.className = "message-thread-agent-trace pear-message-surface pear-message";
    const message = document.createElement("div");
    message.className = "message-thread-agent-message message_content";
    const trace_status = document.createElement("span");
    trace_status.className = "pear-run-trace-status";
    trace_status.setAttribute("aria-live", "polite");
    trace_status.setAttribute("role", "status");
    const trace_host = document.createElement("div");
    trace_host.className = "pear-run-trace";
    message.append(trace_status, trace_host);
    trace_root.append(message);

    panel.append(replies, trace_root);
    body.append(activity, panel);
    pane.append(header, tabs, body);
    pane.setAttribute("aria-labelledby", title.id);
    return {
        pane,
        title,
        meta,
        tabs,
        panel,
        activity,
        replies,
        reply_status,
        reply_list,
        older,
        composer,
        textarea,
        submit,
        trace_root,
        trace_host,
        trace_status,
        close: close_button,
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
    if (pane_elements === undefined) {
        pane_elements = create_pane_elements(pane);
    }
    if (!initialized) {
        initialized = true;
        pane_elements.close.addEventListener("click", () => close());
        pane_elements.tabs.addEventListener("click", (event) => {
            const tab = (event.target as Element).closest<HTMLButtonElement>("[role=tab]");
            if (tab?.dataset["threadKey"] !== undefined) {
                select_thread(tab.dataset["threadKey"], true);
            }
        });
        pane_elements.tabs.addEventListener("keydown", handle_tablist_keydown);
        pane_elements.older.addEventListener("click", load_earlier_replies);
        pane_elements.composer.addEventListener("submit", submit_reply);
        pane_elements.textarea.addEventListener("input", sync_composer_state);
        document.addEventListener("click", handle_thread_button_click);
        pane_elements.textarea.addEventListener("keydown", (event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                pane_elements?.composer.requestSubmit();
                event.preventDefault();
            }
        });
        document.addEventListener("keydown", handle_document_keydown);
        window.addEventListener("resize", update_pane_layout);
        modals.register_pre_open_hook(() => close({restore_focus: false}));
        overlays.register_pre_open_hook(() => close({restore_focus: false}));
    }
    return pane_elements;
}

function active_run(): pear_run_state.PearRunLiveView | undefined {
    return open_message_id === undefined ? undefined : pear_run_state.get_run_view(open_message_id);
}

function agents_for_open_message(): pear_run_state.PearAgentThread[] {
    return active_run()?.agents ?? [];
}

function selected_agent(): pear_run_state.PearAgentThread | undefined {
    return agents_for_open_message().find((agent) => agent.thread_id === selected_thread_key);
}

function render_tabs(agents: pear_run_state.PearAgentThread[]): void {
    const elements = pane_elements;
    if (elements === undefined || open_message_id === undefined) {
        return;
    }
    const summary = reply_state(open_message_id).summary;
    const signature = `${selected_thread_key ?? ""}\u0001${summary_signature(summary)}\u0001${agents.map(agent_signature).join("\u0001")}`;
    if (signature === tabs_signature) {
        return;
    }
    tabs_signature = signature;
    const restore_tab_focus = elements.tabs.contains(document.activeElement);
    const tabs: HTMLButtonElement[] = [];
    const replies_tab = document.createElement("button");
    replies_tab.className = "message-thread-pane-tab message-thread-pane-replies-tab";
    replies_tab.type = "button";
    replies_tab.dataset["threadKey"] = HUMAN_THREAD_KEY;
    replies_tab.id = "message-thread-pane-tab-replies";
    replies_tab.setAttribute("role", "tab");
    replies_tab.setAttribute("aria-selected", String(selected_thread_key === HUMAN_THREAD_KEY));
    replies_tab.setAttribute("aria-controls", elements.panel.id);
    replies_tab.setAttribute("tabindex", selected_thread_key === HUMAN_THREAD_KEY ? "0" : "-1");
    const reply_label = document.createElement("span");
    reply_label.className = "message-thread-pane-tab-label";
    reply_label.textContent = $t({defaultMessage: "Replies"});
    replies_tab.append(reply_label);
    if ((summary?.reply_count ?? 0) > 0) {
        const count = document.createElement("span");
        count.className = "message-thread-pane-tab-count";
        count.textContent = String(summary!.reply_count);
        replies_tab.append(count);
    }
    tabs.push(replies_tab);
    for (const [index, agent] of agents.entries()) {
        const tab = document.createElement("button");
        const selected = agent.thread_id === selected_thread_key;
        const name = display_name_for_testing(agent, index);
        tab.className = "message-thread-pane-tab";
        tab.type = "button";
        tab.dataset["threadKey"] = agent.thread_id;
        tab.id = `message-thread-pane-tab-agent-${index}`;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(selected));
        tab.setAttribute("aria-controls", elements.panel.id);
        tab.setAttribute("tabindex", selected ? "0" : "-1");
        tab.setAttribute("title", name);
        tab.append(create_sigil(agent));
        const label = document.createElement("span");
        label.className = "message-thread-pane-tab-label";
        label.textContent = name;
        tab.append(label);
        tabs.push(tab);
    }
    elements.tabs.replaceChildren(...tabs);
    const selected_tab = elements.tabs.querySelector<HTMLElement>(
        "[role=tab][aria-selected=true]",
    );
    if (selected_tab !== null) {
        elements.panel.setAttribute("aria-labelledby", selected_tab.id);
    }
    if (restore_tab_focus) {
        selected_tab?.focus({preventScroll: true});
    }
}

function render_reply(reply: MessageThreadReply): HTMLElement {
    const article = document.createElement("article");
    article.className = "message-thread-reply";
    article.dataset["replyId"] = String(reply.id);
    article.setAttribute("aria-label", $t({defaultMessage: "Reply from {name}"}, {name: reply.sender_full_name}));
    const participant = {
        user_id: reply.sender_id,
        full_name: reply.sender_full_name,
        avatar_url: reply.avatar_url,
    };
    article.append(create_avatar(participant, "message-thread-reply-avatar"));
    const main = document.createElement("div");
    main.className = "message-thread-reply-main";
    const header = document.createElement("header");
    header.className = "message-thread-reply-header";
    const sender = document.createElement("strong");
    sender.textContent = reply.sender_full_name;
    const time = document.createElement("time");
    time.dateTime = new Date(reply.timestamp * 1000).toISOString();
    time.textContent = timerender.format_time_modern(reply.timestamp * 1000);
    header.append(sender, time);
    const content = document.createElement("div");
    content.className = "message-thread-reply-content rendered_markdown";
    content.append(ui_util.parse_html(reply.content));
    rendered_markdown.update_elements($(content));
    main.append(header, content);
    article.append(main);
    return article;
}

function sync_composer_state(): void {
    const elements = pane_elements;
    if (elements === undefined || open_message_id === undefined) {
        return;
    }
    const state = reply_state(open_message_id);
    state.draft = elements.textarea.value;
    if (state.post_failed) {
        state.post_failed = false;
        elements.reply_status.textContent = "";
    }
    elements.submit.disabled = state.posting || elements.textarea.value.trim() === "";
}

function render_replies(options: {scroll_to_end?: boolean} = {}): void {
    const elements = pane_elements;
    if (elements === undefined || open_message_id === undefined) {
        return;
    }
    const state = reply_state(open_message_id);
    const replies = ordered_replies(state);
    const signature = replies.map((reply) => reply.id).join(",");
    if (elements.reply_list.dataset["signature"] !== signature) {
        elements.reply_list.dataset["signature"] = signature;
        elements.reply_list.replaceChildren(...replies.map(render_reply));
    }
    elements.older.hidden = !state.has_more && !state.failed;
    elements.older.disabled = state.request !== undefined;
    elements.older.textContent = state.failed
        ? $t({defaultMessage: "Retry loading replies"})
        : $t({defaultMessage: "Load earlier replies"});
    if (state.request !== undefined && replies.length === 0) {
        elements.reply_status.textContent = $t({defaultMessage: "Loading replies…"});
    } else if (state.failed && replies.length === 0) {
        elements.reply_status.textContent = $t({defaultMessage: "Could not load replies."});
    } else if (state.post_failed) {
        elements.reply_status.textContent = $t({defaultMessage: "Could not send reply."});
    } else if (state.loaded && replies.length === 0) {
        elements.reply_status.textContent = $t({defaultMessage: "No replies yet."});
    } else {
        elements.reply_status.textContent = "";
    }
    elements.submit.disabled = state.posting || elements.textarea.value.trim() === "";
    elements.textarea.disabled = state.posting;
    if (options.scroll_to_end === true) {
        elements.reply_list.lastElementChild?.scrollIntoView({block: "end"});
    }
}

function load_replies(before?: number): void {
    if (open_message_id === undefined) {
        return;
    }
    const message_id = open_message_id;
    const state = reply_state(message_id);
    if (state.request !== undefined) {
        return;
    }
    state.failed = false;
    state.request_generation += 1;
    const generation = state.request_generation;
    state.request = channel.get({
        url: `/json/messages/${message_id}/thread`,
        data: {limit: REPLY_PAGE_SIZE, ...(before === undefined ? {} : {before})},
        success(raw_data) {
            if (generation !== state.request_generation) {
                return;
            }
            const result = message_thread_response_schema.safeParse(raw_data);
            state.request = undefined;
            if (!result.success) {
                state.failed = true;
                if (open_message_id === message_id && selected_thread_key === HUMAN_THREAD_KEY) {
                    render_replies();
                }
                return;
            }
            remember_reply_summary(message_id, result.data.reply_summary ?? undefined);
            for (const reply of result.data.replies) {
                merge_reply(state, reply);
            }
            state.has_more = result.data.has_more;
            state.loaded = true;
            state.failed = false;
            render_registered_rows(message_id);
            if (open_message_id === message_id && selected_thread_key === HUMAN_THREAD_KEY) {
                render_pane();
            }
        },
        error() {
            if (generation !== state.request_generation) {
                return;
            }
            state.request = undefined;
            state.failed = true;
            if (open_message_id === message_id && selected_thread_key === HUMAN_THREAD_KEY) {
                render_replies();
            }
        },
    });
    render_replies();
}

function load_earlier_replies(): void {
    if (open_message_id === undefined) {
        return;
    }
    const first_reply = ordered_replies(reply_state(open_message_id))[0];
    load_replies(first_reply?.id);
}

function submit_reply(event: SubmitEvent): void {
    event.preventDefault();
    const elements = pane_elements;
    if (elements === undefined || open_message_id === undefined) {
        return;
    }
    const message_id = open_message_id;
    const content = elements.textarea.value.trim();
    const state = reply_state(message_id);
    if (content === "" || state.posting) {
        return;
    }
    state.posting = true;
    state.post_failed = false;
    render_replies();
    void channel.post({
        url: `/json/messages/${message_id}/thread`,
        data: {content},
        success(raw_data) {
            state.posting = false;
            const result = message_thread_post_response_schema.safeParse(raw_data);
            if (!result.success) {
                state.post_failed = true;
                render_replies();
                return;
            }
            remember_reply_summary(message_id, result.data.reply_summary);
            merge_reply(state, result.data.reply);
            state.loaded = true;
            state.post_failed = false;
            state.draft = "";
            render_registered_rows(message_id);
            if (open_message_id === message_id) {
                elements.textarea.value = "";
            }
            if (open_message_id === message_id && selected_thread_key === HUMAN_THREAD_KEY) {
                render_pane({scroll_replies_to_end: true});
                elements.textarea.focus({preventScroll: true});
            }
        },
        error() {
            state.posting = false;
            state.post_failed = true;
            if (open_message_id === message_id && selected_thread_key === HUMAN_THREAD_KEY) {
                render_replies();
            }
        },
    });
}

function mount_selected_agent_trace(): void {
    const elements = pane_elements;
    const agent = selected_agent();
    const run = active_run();
    if (elements === undefined || agent === undefined || run === undefined) {
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
    const state = reply_state(open_message_id);
    const agents = agents_for_open_message();
    selected_thread_key = selection_after_update_for_testing(agents, selected_thread_key);
    render_tabs(agents);
    if (selected_thread_key === HUMAN_THREAD_KEY) {
        trace_controller?.destroy();
        trace_controller = undefined;
        trace_thread_id = undefined;
        elements.replies.hidden = false;
        elements.trace_root.hidden = true;
        elements.activity.hidden = true;
        elements.meta.textContent = $t(
            {defaultMessage: "{count, plural, one {# reply} other {# replies}}"},
            {count: state.summary?.reply_count ?? 0},
        );
        render_replies(
            options.scroll_replies_to_end === undefined
                ? {}
                : {scroll_to_end: options.scroll_replies_to_end},
        );
        if (!state.loaded && state.request === undefined) {
            load_replies();
        }
    } else {
        const agent = selected_agent();
        if (agent === undefined) {
            selected_thread_key = HUMAN_THREAD_KEY;
            render_pane();
            return;
        }
        const index = agents.indexOf(agent);
        elements.meta.textContent = [display_name_for_testing(agent, index), agent.role?.trim()]
            .filter(Boolean)
            .join(" · ");
        elements.activity.hidden = false;
        elements.activity.textContent =
            agent.current_activity?.trim() ||
            $t({defaultMessage: "Status: {status}"}, {status: agent.status});
        elements.replies.hidden = true;
        elements.trace_root.hidden = false;
        mount_selected_agent_trace();
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
        row.addEventListener("blur", () => row.removeAttribute("tabindex"), {once: true});
    }
    row.focus({preventScroll: true});
}

function select_thread(thread_key: string, focus_tab: boolean): void {
    if (thread_key === selected_thread_key) {
        if (thread_key === HUMAN_THREAD_KEY || trace_controller !== undefined) {
            return;
        }
    }
    selected_thread_key = thread_key;
    render_pane({scroll_replies_to_end: thread_key === HUMAN_THREAD_KEY});
    if (focus_tab) {
        pane_elements?.tabs
            .querySelector<HTMLElement>(`[data-thread-key="${CSS.escape(thread_key)}"]`)
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
    const thread_key = tabs[next_index]?.dataset["threadKey"];
    if (thread_key !== undefined) {
        select_thread(thread_key, true);
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
    if (open_message_id !== undefined) {
        reply_state(open_message_id).draft = elements.textarea.value;
    }
    anchor_element = anchor;
    anchor_message_id = message_id;
    anchor_thread_key = thread_key;
    pane_trace_enhancer = enhance_trace;
    open_message_id = message_id;
    selected_thread_key = thread_key;
    const state = reply_state(message_id);
    if (state.summary === undefined) {
        state.summary = message_store.get(message_id)?.reply_summary;
    }
    elements.textarea.value = state.draft;
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
            elements.textarea.focus({preventScroll: true});
        } else {
            elements.tabs
                .querySelector<HTMLElement>(`[data-thread-key="${CSS.escape(thread_key)}"]`)
                ?.focus({preventScroll: true});
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
    if (
        button === null ||
        row === undefined ||
        row === null ||
        !Number.isSafeInteger(message_id)
    ) {
        return;
    }
    open(
        message_id,
        HUMAN_THREAD_KEY,
        button,
        trace_enhancer_by_row.get(row) ?? (() => {}),
        {focus_composer: true},
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
    if (open_message_id !== undefined) {
        reply_state(open_message_id).draft = elements.textarea.value;
    }
    trace_controller?.destroy();
    trace_controller = undefined;
    trace_thread_id = undefined;
    open_message_id = undefined;
    selected_thread_key = undefined;
    tabs_signature = "";
    elements.pane.hidden = true;
    elements.pane.setAttribute("aria-hidden", "true");
    document.body.classList.remove("message-thread-pane-open");
    document.body.classList.remove("message-thread-pane-mobile-open");
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
    if (anchor_message_id === message_id) {
        close({restore_focus: false});
    }
}

function create_reply_preview(
    message_id: number,
    summary: message_store.MessageThreadReplySummary,
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
    launch.addEventListener("click", () =>
        open(message_id, HUMAN_THREAD_KEY, launch, enhance_trace),
    );
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
        button.addEventListener("click", () =>
            open(message_id, agent.thread_id, button, enhance_trace),
        );
        badges.append(button);
    }
    const count = document.createElement("button");
    count.className = "message-thread-agent-count";
    count.type = "button";
    count.textContent = $t(
        {defaultMessage: "{count, plural, one {# agent} other {# agents}}"},
        {count: agents.length},
    );
    count.addEventListener("click", () =>
        open(message_id, agents[0]!.thread_id, count, enhance_trace),
    );
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
    trace_enhancer_by_row.set(row, enhance_trace);
    const message = message_store.get(message_id);
    remember_reply_summary(message_id, message?.reply_summary);
    const summary = reply_state_by_message_id.get(message_id)?.summary ?? message?.reply_summary;
    const run = pear_run_state.get_run_view(message_id);
    const agents = run?.agents ?? [];
    if (summary === undefined && agents.length === 0) {
        host.hidden = true;
        host.replaceChildren();
        delete host.dataset["signature"];
        return;
    }
    const signature = `${summary_signature(summary)}\u0001${run?.id ?? ""}\u0001${agents.map(agent_signature).join("\u0001")}`;
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
        strip.append(create_reply_preview(message_id, summary, enhance_trace));
    }
    if (summary !== undefined && agents.length > 0) {
        const divider = document.createElement("span");
        divider.className = "message-thread-strip-divider";
        divider.setAttribute("aria-hidden", "true");
        strip.append(divider);
    }
    if (run !== undefined && agents.length > 0) {
        strip.append(create_agent_preview(message_id, agents, enhance_trace));
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

export function apply_message_thread_event(raw_event: unknown): void {
    const event = message_thread_event_schema.parse(raw_event);
    remember_reply_summary(event.message_id, event.reply_summary);
    merge_reply(reply_state(event.message_id), event.reply);
    render_registered_rows(event.message_id);
    if (open_message_id === event.message_id) {
        render_pane({scroll_replies_to_end: selected_thread_key === HUMAN_THREAD_KEY});
    }
}

pear_run_state.on_run_state_changed((run, event) => {
    if (!document_is_available()) {
        return;
    }
    const message_id = run.result_message_id;
    if (typeof message_id === "number") {
        render_registered_rows(message_id);
    }
    if (message_id !== open_message_id) {
        return;
    }
    if (event?.event_type === "agent" && event.agent_thread_id === selected_thread_key) {
        trace_controller?.mark_dirty(true);
    }
    schedule_pane_render();
});

export function clear_for_testing(): void {
    close({restore_focus: false});
    for (const state of reply_state_by_message_id.values()) {
        state.request_generation += 1;
        state.request?.abort();
    }
    reply_state_by_message_id.clear();
}
