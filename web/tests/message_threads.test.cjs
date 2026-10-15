"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const render_message_controls = require("../templates/message_controls.hbs");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const dom = new JSDOM(
    "<!doctype html><html><body><main class='app-main'><section id='primary-shell'><div class='message_row' data-message-id='31'><div class='message_controls'><button class='message_thread_button'></button></div><div class='message-thread-host' hidden></div></div><div class='message_row' data-message-id='41'><div class='message_controls'><button class='message_thread_button'></button></div><div class='message-thread-host' hidden></div></div></section><div class='column-right'><aside id='message-thread-pane' hidden></aside></div></main></body></html>",
    {pretendToBeVisual: true},
);
const {window} = dom;
window.HTMLElement.prototype.to_$ = function () {
    const $element = $(".message-thread-test-element");
    $element.get = () => this;
    return $element;
};
window.HTMLElement.prototype.scrollIntoView = () => {};
window.HTMLElement.prototype.getClientRects = () => [{top: 0, bottom: 40}];
window.HTMLElement.prototype.getBoundingClientRect = function () {
    if (this.classList.contains("message-thread-pane-body")) {
        return {top: 0, bottom: 500};
    }
    return {top: 10, bottom: 50};
};
let next_animation_frame_id = 1;
let animation_frame_callbacks = [];
window.requestAnimationFrame = (callback) => {
    const id = next_animation_frame_id;
    next_animation_frame_id += 1;
    animation_frame_callbacks.push({id, callback});
    return id;
};
window.cancelAnimationFrame = (id) => {
    animation_frame_callbacks = animation_frame_callbacks.filter((frame) => frame.id !== id);
};
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    HTMLElement: window.HTMLElement,
    Element: window.Element,
    Node: window.Node,
    Event: window.Event,
    MouseEvent: window.MouseEvent,
    KeyboardEvent: window.KeyboardEvent,
    CSS: {escape: (value) => value},
})) {
    set_global(name, value);
}

let controller_options;
let destroyed_controller_count = 0;
let desktop_viewport = false;
let message_lists_created = [];
let rendered_message_lists = [];
let narrow_requests = [];
let summary_requests = [];
let summary_batch_requests = [];
let cancelled_retry_data = [];
let compose_creations = [];
let read_message_ids = [];
let viewport_focused = true;

class FakeMessageListData {
    constructor(options) {
        this.filter = options.filter;
        this.messages = [];
        this.fetch_status = {
            can_load_older_messages: () => false,
            has_found_oldest: () => true,
        };
    }

    num_items() {
        return this.messages.length;
    }
}

class FakeMessageList {
    constructor(options) {
        this.data = options.data;
        this.options = options;
        this.view = {
            $list: {
                find() {
                    return {
                        last() {
                            return {get: () => undefined};
                        },
                    };
                },
            },
        };
        const mount = options.view_options.mount;
        const list = document.createElement("div");
        list.className = "message-list";
        list.dataset.filter = JSON.stringify(this.data.filter.public_terms());
        mount.append(list);
        this.element = list;
        message_lists_created.push(this);
    }

    add_messages(messages) {
        const predicate = this.data.filter.predicate();
        const matching_messages = messages.filter((message) => predicate(message));
        this.data.messages.push(...matching_messages);
        for (const message of matching_messages) {
            const row = document.createElement("div");
            row.className = "message_row";
            row.dataset.messageId = String(message.id);
            this.element.append(row);
        }
    }

    all_messages() {
        return [...this.data.messages];
    }
}

mock_esm("../src/message_list", {MessageList: FakeMessageList});
mock_esm("../src/message_list_data", {MessageListData: FakeMessageListData});
mock_esm("../src/message_lists", {
    add_rendered_message_list(list) {
        rendered_message_lists.push(list);
    },
    delete_message_list(list) {
        rendered_message_lists = rendered_message_lists.filter((candidate) => candidate !== list);
    },
});
mock_esm("../src/message_fetch", {
    cancel_retries_for_message_list_data(data) {
        cancelled_retry_data.push(data);
    },
    load_messages(options) {
        summary_requests.push(options);
    },
    load_messages_by_ids(options) {
        summary_batch_requests.push(options);
    },
    load_messages_for_narrow(options) {
        narrow_requests.push(options);
    },
    maybe_load_older_messages(options) {
        options.cont?.();
    },
});
mock_esm("../src/compose_editor", {
    create(options) {
        const form = document.createElement("form");
        form.className = "compose-editor";
        const textarea = document.createElement("textarea");
        textarea.className = "message-textarea";
        textarea.value = options.content ?? "";
        form.append(textarea);
        options.$host.get(0).replaceChildren(form);
        const editor = {
            element: form,
            textarea,
            focus() {
                textarea.focus();
            },
            get_content() {
                return textarea.value;
            },
            notify_content_changed() {
                editor.external_change_count += 1;
            },
            set_content(content) {
                textarea.value = content;
                options.on_draft_change?.(content);
            },
            refresh_recipient() {
                editor.recipient_refresh_count += 1;
            },
            recipient_refresh_count: 0,
            external_change_count: 0,
            send() {
                return true;
            },
            destroy() {
                form.remove();
            },
        };
        compose_creations.push({options, editor});
        return editor;
    },
});
mock_esm("../src/unread_ops", {
    viewport_is_visible_and_focused() {
        return viewport_focused;
    },
    notify_server_message_read(message) {
        read_message_ids.push(message.id);
        message.unread = false;
    },
    notify_server_messages_read(messages) {
        read_message_ids.push(...messages.map((message) => message.id));
        for (const message of messages) {
            message.unread = false;
        }
    },
});
mock_esm("../src/modals", {register_pre_open_hook() {}});
mock_esm("../src/overlays", {register_pre_open_hook() {}});
mock_esm("../src/timerender", {relative_time_string_from_date: () => "2h ago"});
mock_esm("../src/ui_util", {matches_viewport_state: () => desktop_viewport});
mock_esm("../src/legacy_run_trace", {
    create_controller(options) {
        controller_options = options;
        return {
            destroy() {
                destroyed_controller_count += 1;
            },
            load() {},
            mark_dirty() {},
            get in_flight() {
                return false;
            },
            get request_state() {
                return "ready";
            },
        };
    },
});

const legacy_run_state = zrequire("legacy_run_state");
const message_store = zrequire("message_store");
const message_threads = zrequire("message_threads");

const agents = [
    {
        thread_id: "child-a",
        nickname: "Scout",
        role: "repository mapper",
        status: "active",
        current_activity: "Reading files",
    },
    {
        thread_id: "child-b",
        nickname: "Verifier",
        role: "test runner",
        status: "idle",
        current_activity: "Tests passed",
    },
];

const participant = {
    user_id: 7,
    full_name: "Ada Lovelace",
    avatar_url: "/avatar/7",
};

function summary({count = 1, unread_count = count, last_sender = participant} = {}) {
    return {
        reply_count: count,
        unread_reply_count: unread_count,
        last_reply_at: 100,
        last_sender,
        participants: [participant],
    };
}

function root_message() {
    return {
        id: 31,
        type: "stream",
        stream_id: 9,
        topic: "native threads",
        sender_id: 5,
        sender_full_name: "Pear",
        avatar_url: "/avatar/5",
        content: "<p>Original thread message</p>",
        timestamp: 90,
        is_agent_message: true,
        display_recipient: "devel",
    };
}

function child_message() {
    return {
        ...root_message(),
        id: 41,
        sender_id: 7,
        sender_full_name: "Ada Lovelace",
        content: "<p>Ordinary child message</p>",
        thread_root_message_id: 31,
        unread: true,
    };
}

function reset_dom() {
    message_threads.clear_for_testing();
    legacy_run_state.clear_for_testing();
    message_store.set_messages_for_tests([{message: root_message()}, {message: child_message()}]);
    for (const host of document.querySelectorAll(".message-thread-host")) {
        host.replaceChildren();
        host.hidden = true;
        delete host.dataset.signature;
    }
    document.querySelector("#message-thread-pane").hidden = true;
    controller_options = undefined;
    destroyed_controller_count = 0;
    desktop_viewport = false;
    message_lists_created = [];
    rendered_message_lists = [];
    narrow_requests = [];
    summary_requests = [];
    summary_batch_requests = [];
    cancelled_retry_data = [];
    compose_creations = [];
    read_message_ids = [];
    viewport_focused = true;
    animation_frame_callbacks = [];
}

function flush_animation_frame() {
    const callbacks = animation_frame_callbacks;
    animation_frame_callbacks = [];
    for (const {callback} of callbacks) {
        callback(0);
    }
}

function row_for(message_id) {
    return document.querySelector(`.message_row[data-message-id="${message_id}"]`);
}

function render_row(message_id = 31) {
    const row = row_for(message_id);
    message_threads.render_for_message_row(row, message_id, () => {});
    return row;
}

function remember_agents() {
    legacy_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
}

run_test("sigils and agent selection are deterministic", () => {
    const cells = message_threads.sigil_cells_for_testing("child-a");
    assert.deepEqual(cells, message_threads.sigil_cells_for_testing("child-a"));
    for (let row = 0; row < 3; row += 1) {
        assert.equal(cells[row * 3], cells[row * 3 + 2]);
    }
    assert.equal(message_threads.display_name_for_testing(agents[0], 0), "Scout");
    assert.equal(message_threads.selection_after_update_for_testing(agents, "child-b"), "child-b");
    assert.equal(message_threads.selection_after_update_for_testing(agents, "missing"), "child-a");
    assert.equal(message_threads.status_tone_for_testing("running"), "active");
    assert.equal(message_threads.status_tone_for_testing("failed"), "failed");
});

run_test("cycler order, wrap, and dimming are deterministic", () => {
    // The human thread leads; agents follow in first-seen spawn order.
    const order = message_threads.cycle_order_for_testing(agents);
    assert.deepEqual(order, ["replies", "child-a", "child-b"]);
    assert.equal(message_threads.cycle_selection_for_testing(order, "replies", 1), "child-a");
    assert.equal(message_threads.cycle_selection_for_testing(order, "replies", -1), "child-b");
    assert.equal(message_threads.cycle_selection_for_testing(order, "child-a", -1), "replies");
    assert.equal(message_threads.cycle_selection_for_testing(order, "child-b", 1), "replies");
    assert.equal(message_threads.cycle_selection_for_testing(order, "missing", 1), "child-a");
    assert.equal(message_threads.cycle_selection_for_testing(order, undefined, -1), "child-b");
    // Active agents are bright; anything closed, finished, or failed dims.
    assert.equal(message_threads.agent_is_dimmed_for_testing(agents[0]), false);
    assert.equal(message_threads.agent_is_dimmed_for_testing(agents[1]), true);
    assert.equal(
        message_threads.agent_is_dimmed_for_testing({thread_id: "x", status: "done"}),
        true,
    );
    assert.equal(
        message_threads.agent_is_dimmed_for_testing({thread_id: "x", status: "failed"}),
        true,
    );
});

run_test("message controls expose one real thread button only for durable messages", () => {
    const durable = render_message_controls({
        is_archived: false,
        msg: {locally_echoed: false, sent_by_me: false, starred: false},
    });
    assert.match(durable, /message_thread_button/);
    assert.match(durable, /message-thread-reply-glyph/);
    assert.doesNotMatch(
        render_message_controls({
            is_archived: true,
            msg: {locally_echoed: false, sent_by_me: false, starred: false},
        }),
        /message_thread_button/,
    );
});

run_test("human replies mount canonical message lists and the shared composer", () => {
    reset_dom();
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    assert.equal(pane.hidden, false);
    assert.equal(message_lists_created.length, 2);
    assert.deepEqual(message_lists_created[0].data.filter.public_terms(), [
        {operator: "id", operand: "31", negated: false},
    ]);
    assert.deepEqual(message_lists_created[1].data.filter.public_terms(), [
        {operator: "thread", operand: 31, negated: false},
    ]);
    for (const list of message_lists_created) {
        assert.equal(list.options.view_options.manages_primary_viewport, false);
        assert.equal(list.options.view_options.show_recipient_headers, false);
        assert.equal(list.options.view_options.show_thread_previews, false);
        assert.equal(list.options.view_options.suppress_row_chrome, true);
    }
    assert.equal(compose_creations.length, 1);
    assert.equal(compose_creations[0].options.thread_root_message_id, 31);
    assert.equal(narrow_requests.length, 1);
    assert.equal(narrow_requests[0].anchor, "newest");
    assert.equal(
        message_lists_created[0].options.view_options.aria_label,
        "translated: Thread root message",
    );
    assert.equal(message_lists_created[1].options.view_options.role, "list");
    assert.equal(message_lists_created[1].options.view_options.aria_label, "translated: Replies");
    assert.deepEqual(read_message_ids, []);

    // Without runs there is no agent bar; the header echoes the feed's
    // topic header (colored hash + conversation) and the composer is
    // pinned outside the scroll region.
    assert.equal(pane.querySelector(".message-thread-agent-bar").hidden, true);
    assert.equal(
        pane.querySelector("#message-thread-pane-title").textContent,
        "devel > native threads",
    );
    const composer_host = pane.querySelector(".message-thread-composer-host");
    assert.equal(composer_host.hidden, false);
    assert.equal(composer_host.parentElement, pane);
    assert.equal(pane.querySelector(".message-thread-pane-body").contains(composer_host), false);

    message_lists_created[1].add_messages([child_message()]);
    narrow_requests[0].cont();
    assert.deepEqual([...new Set(read_message_ids)], [41]);
    flush_animation_frame();
    assert.equal(document.activeElement, compose_creations[0].editor.textarea);
});

run_test("pane paints cached replies before the fetch resolves", () => {
    reset_dom();
    render_row().querySelector(".message_thread_button").click();
    const reply_list = message_lists_created[1];
    // The cached reply is painted at mount -- no network wait -- and the
    // narrow fetch still reconciles afterwards.
    assert.deepEqual(
        reply_list.all_messages().map((message) => message.id),
        [41],
    );
    assert.equal(reply_list.element.querySelectorAll(".message_row").length, 1);
    assert.equal(narrow_requests.length, 1);
});

run_test("secondary reply rows expose their owning thread compose target", () => {
    reset_dom();
    render_row().querySelector(".message_thread_button").click();
    const target = message_threads.get_compose_target(41);
    assert.equal(target.textarea, compose_creations[0].editor.textarea);
    target.notify_content_changed();
    assert.equal(compose_creations[0].editor.external_change_count, 1);
});

run_test("agent cycler mounts traces and replies on demand", () => {
    reset_dom();
    remember_agents();
    render_row().querySelector(".message-thread-agent-launch").click();
    const pane = document.querySelector("#message-thread-pane");
    const current = pane.querySelector(".message-thread-agent-current");
    const prev = pane.querySelector(".message-thread-agent-cycle-prev");
    const next = pane.querySelector(".message-thread-agent-cycle-next");
    // Opening an agent thread mounts only the root anchor and the trace.
    assert.equal(pane.querySelector(".message-thread-agent-bar").hidden, false);
    assert.equal(message_lists_created.length, 1);
    assert.equal(compose_creations.length, 0);
    assert.equal(narrow_requests.length, 0);
    assert.equal(controller_options.thread_id, "child-a");
    assert.equal(current.dataset.threadKey, "child-a");
    assert.equal(current.textContent.includes("Scout"), true);
    assert.equal(current.textContent.includes("(repository mapper)"), true);
    assert.equal(current.textContent.includes("· active"), true);
    assert.equal(current.classList.contains("message-thread-agent-current-dimmed"), false);

    // Cycling backwards from the first agent lands on the human thread.
    prev.click();
    assert.equal(current.dataset.threadKey, undefined);
    assert.equal(current.textContent, "translated: Replies");
    assert.equal(message_lists_created.length, 2);
    assert.equal(compose_creations.length, 1);
    assert.equal(narrow_requests.length, 1);

    // Replies → child-a → child-b; the idle agent renders dimmed.
    next.click();
    assert.equal(current.dataset.threadKey, "child-a");
    next.click();
    assert.equal(current.dataset.threadKey, "child-b");
    assert.equal(current.classList.contains("message-thread-agent-current-dimmed"), true);
    assert.equal(rendered_message_lists.length, 1);
    assert.equal(destroyed_controller_count, 2);
    assert.equal(controller_options.thread_id, "child-b");

    // The bar wraps: child-b → Replies.
    next.click();
    assert.equal(current.dataset.threadKey, undefined);
});

run_test("agent view hides the composer entirely", () => {
    reset_dom();
    remember_agents();
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    const composer_host = pane.querySelector(".message-thread-composer-host");
    const replies = pane.querySelector(".message-thread-replies");
    const trace_root = pane.querySelector(".message-thread-agent-trace");
    assert.equal(composer_host.hidden, false);
    assert.equal(compose_creations.length, 1);
    assert.equal(trace_root.hidden, true);

    pane.querySelector(".message-thread-agent-cycle-next").click();
    assert.equal(composer_host.hidden, true);
    assert.equal(replies.hidden, true);
    assert.equal(trace_root.hidden, false);
    assert.equal(controller_options.thread_id, "child-a");

    pane.querySelector(".message-thread-agent-cycle-prev").click();
    assert.equal(composer_host.hidden, false);
    assert.equal(replies.hidden, false);
    assert.equal(trace_root.hidden, true);
    assert.equal(compose_creations.length, 2);
});

run_test("reply divider counts only the pushed summary", () => {
    reset_dom();
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    const separator = pane.querySelector(".message-thread-reply-separator");
    const reply_status = pane.querySelector(".message-thread-reply-status");
    // A cached reply list alone is not a count source (the mount seeds
    // it from the store); the divider still waits for the summary.
    narrow_requests[0].cont();
    assert.equal(message_lists_created[1].data.num_items(), 1);
    assert.equal(separator.hidden, true);
    assert.equal(reply_status.textContent, "translated: No replies yet.");

    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: {
            reply_count: 3,
            last_reply_at: 100,
            last_sender: participant,
            participants: [participant],
        },
    });
    flush_animation_frame();
    assert.equal(separator.hidden, false);
    assert.equal(separator.textContent, "translated: 3 replies");
});

run_test("launch strips never render inside the pane", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary({count: 2, unread_count: 1});
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    // The pane's root anchor row shares the feed row's message id but must
    // stay bare even when rendered through the hook directly.
    const pane_root_row = pane.querySelector(".message-thread-root .message_row");
    assert.notEqual(pane_root_row, null);
    const host = document.createElement("div");
    host.className = "message-thread-host";
    pane_root_row.append(host);
    message_threads.render_for_message_row(pane_root_row, 31, () => {});
    assert.equal(host.hidden, true);
    assert.equal(host.children.length, 0);

    // The feed row for the same message still renders its strip.
    const feed_row = row_for(31);
    message_threads.render_for_message_row(feed_row, 31, () => {});
    assert.notEqual(feed_row.querySelector(".message-thread-reply-launch"), null);
});

run_test("header title is stable across thread selections", () => {
    reset_dom();
    remember_agents();
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    const title = pane.querySelector("#message-thread-pane-title");
    assert.equal(title.textContent, "devel > native threads");
    pane.querySelector(".message-thread-agent-cycle-next").click();
    assert.equal(title.textContent, "devel > native threads");
    pane.querySelector(".message-thread-agent-cycle-prev").click();
    assert.equal(title.textContent, "devel > native threads");
});

run_test("pane borrows the collapsed sidebar's slot and returns it on close", () => {
    reset_dom();
    render_row();
    document.body.classList.add("hide-right-sidebar");
    document.querySelector(".message_row .message_thread_button").click();
    assert.equal(document.body.classList.contains("hide-right-sidebar"), false);
    assert.equal(document.body.classList.contains("message-thread-pane-open"), true);
    message_threads.close();
    assert.equal(document.body.classList.contains("hide-right-sidebar"), true);

    // Without a prior collapse, close leaves the sidebar alone.
    document.body.classList.remove("hide-right-sidebar");
    document.querySelector(".message_row .message_thread_button").click();
    message_threads.close();
    assert.equal(document.body.classList.contains("hide-right-sidebar"), false);
});

run_test("Alt+arrow cycles threads only while the composer is empty", () => {
    reset_dom();
    remember_agents();
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    const current = pane.querySelector(".message-thread-agent-current");
    const press = (key) => {
        document.dispatchEvent(
            new window.KeyboardEvent("keydown", {key, altKey: true, bubbles: true}),
        );
    };
    press("ArrowRight");
    assert.equal(current.dataset.threadKey, "child-a");
    press("ArrowRight");
    assert.equal(current.dataset.threadKey, "child-b");
    // Wraps back to the human thread, then backwards to the last agent.
    press("ArrowRight");
    assert.equal(current.dataset.threadKey, undefined);
    press("ArrowLeft");
    assert.equal(current.dataset.threadKey, "child-b");

    // A non-empty composer keeps Alt+arrow for its own text navigation.
    pane.querySelector(".message-thread-agent-cycle-prev").click();
    pane.querySelector(".message-thread-agent-cycle-prev").click();
    assert.equal(current.dataset.threadKey, undefined);
    const textarea = compose_creations.at(-1).editor.textarea;
    textarea.value = "draft";
    press("ArrowRight");
    assert.equal(current.dataset.threadKey, undefined);
    textarea.value = "";
    press("ArrowRight");
    assert.equal(current.dataset.threadKey, "child-a");
});

run_test("child launch and previews resolve to the direct root", () => {
    reset_dom();
    const root = message_store.get(31);
    root.reply_summary = summary();
    remember_agents();
    const child_row = render_row(41);
    assert.equal(
        child_row.querySelector(".message-thread-host").textContent,
        "translated: View thread",
    );
    assert.equal(child_row.querySelector(".message-thread-agent-launch"), null);
    child_row.querySelector(".message-thread-child-launch").click();
    assert.equal(compose_creations[0].options.thread_root_message_id, 31);
    assert.deepEqual(message_lists_created[0].data.filter.public_terms(), [
        {operator: "id", operand: "31", negated: false},
    ]);
    assert.deepEqual(message_lists_created[1].data.filter.public_terms(), [
        {operator: "thread", operand: 31, negated: false},
    ]);
});

run_test("ordinary add and delete events update summaries locally without fetches", () => {
    reset_dom();
    render_row().querySelector(".message_thread_button").click();
    read_message_ids = [];
    message_threads.handle_added_messages([child_message()]);
    // The reply is seeded in the open pane's list and visible, so it is
    // marked read; still no summary fetches fire.
    assert.deepEqual(read_message_ids, [41]);
    assert.equal(summary_batch_requests.length, 0);
    // The root has no summary yet; the pushed summary event installs one.
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: {
            reply_count: 1,
            last_reply_at: 100,
            last_sender: participant,
            participants: [participant],
        },
    });
    assert.equal(message_store.get(31).reply_summary.reply_count, 1);
    // The seeded reply was visible at mount and marked read, so the
    // preserved local unread count is zero.
    assert.equal(message_store.get(31).reply_summary.unread_reply_count, 0);
    assert.equal(summary_batch_requests.length, 0);

    message_threads.handle_deleted_messages([31, 41]);
    assert.equal(document.querySelector("#message-thread-pane").hidden, true);
    assert.equal(summary_batch_requests.length, 0);
});

run_test("pushed summary updates rows and preserves the local unread count", () => {
    reset_dom();
    const root = message_store.get(31);
    root.reply_summary = summary({count: 1, unread_count: 1});
    const row = render_row();
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: {
            reply_count: 3,
            last_reply_at: 200,
            last_sender: participant,
            participants: [participant],
        },
    });
    const applied = message_store.get(31).reply_summary;
    assert.equal(applied.reply_count, 3);
    assert.equal(applied.last_reply_at, 200);
    assert.equal(applied.unread_reply_count, 1);
    assert.equal(row.querySelector(".message-thread-unread-count").textContent, "1");
    assert.equal(row.querySelector(".message-thread-reply-launch").textContent.includes("3"), true);
    assert.equal(summary_batch_requests.length, 0);
});

run_test("pushed summary clamps preserved unread to the new reply count", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary({count: 2, unread_count: 2});
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: {
            reply_count: 1,
            last_reply_at: 200,
            last_sender: participant,
            participants: [participant],
        },
    });
    const applied = message_store.get(31).reply_summary;
    assert.equal(applied.reply_count, 1);
    assert.equal(applied.unread_reply_count, 1);
});

run_test("pushed null summary clears the root preview", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary();
    const row = render_row();
    assert.notEqual(row.querySelector(".message-thread-reply-launch"), null);
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: null,
    });
    assert.equal(message_store.get(31).reply_summary, undefined);
    assert.equal(row.querySelector(".message-thread-host").hidden, true);
});

run_test("pushed summary for an uncached root is ignored", () => {
    reset_dom();
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 999,
        summary: {
            reply_count: 2,
            last_reply_at: 200,
            last_sender: participant,
            participants: [participant],
        },
    });
    assert.equal(message_store.get(999), undefined);
    assert.equal(summary_batch_requests.length, 0);
});

run_test("pushed summary re-renders the open pane", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary({count: 1, unread_count: 1});
    remember_agents();
    render_row().querySelector(".message_thread_button").click();
    const pane = document.querySelector("#message-thread-pane");
    assert.equal(pane.hidden, false);
    const separator = pane.querySelector(".message-thread-reply-separator");
    assert.equal(separator.hidden, false);
    assert.equal(separator.textContent, "translated: 1 reply");
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: {
            reply_count: 5,
            last_reply_at: 200,
            last_sender: participant,
            participants: [participant],
        },
    });
    flush_animation_frame();
    assert.equal(message_store.get(31).reply_summary.reply_count, 5);
    assert.equal(separator.textContent, "translated: 5 replies");
    // The divider is the only reply count in the pane; the header carries
    // only the conversation context.
    const heading_text = pane
        .querySelector(".message-thread-pane-heading")
        .textContent.replaceAll(/\s+/g, " ")
        .trim();
    assert.equal(heading_text, "# devel > native threads");
});

run_test("unread reply arrivals increment the local count without a fetch", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary({count: 1, unread_count: 1});
    render_row();
    message_threads.handle_added_messages([child_message()]);
    assert.equal(message_store.get(31).reply_summary.unread_reply_count, 2);
    // The push, not the arrival path, owns the reply count.
    assert.equal(message_store.get(31).reply_summary.reply_count, 1);
    assert.equal(summary_batch_requests.length, 0);

    const read_child = {...child_message(), id: 42, unread: false};
    message_threads.handle_added_messages([read_child]);
    assert.equal(message_store.get(31).reply_summary.unread_reply_count, 2);
});

run_test("cold root summary renders unread replies without cached children", () => {
    reset_dom();
    const root = root_message();
    root.reply_summary = summary({count: 4, unread_count: 3});
    message_store.set_messages_for_tests([{message: root}]);
    const row = render_row();
    assert.equal(row.querySelector(".message-thread-unread-count").textContent, "3");
    assert.deepEqual(message_store.get_thread_replies(31), []);
});

run_test("visible pane reads settle the authoritative root badge immediately", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary({unread_count: 1});
    const row = render_row();
    assert.equal(row.querySelector(".message-thread-unread-count").textContent, "1");
    row.querySelector(".message-thread-reply-launch").click();
    message_lists_created[1].add_messages([child_message()]);
    narrow_requests[0].cont();
    assert.deepEqual([...new Set(read_message_ids)], [41]);
    assert.equal(message_store.get(31).reply_summary.unread_reply_count, 0);
    assert.equal(row.querySelector(".message-thread-unread-count"), null);
});

run_test("live child read and delete events settle the root summary locally", () => {
    reset_dom();
    message_store.get(31).reply_summary = summary({unread_count: 1});
    render_row();
    message_threads.handle_read_flag_update([41], true);
    assert.equal(message_store.get(31).reply_summary.unread_reply_count, 0);
    assert.equal(summary_batch_requests.length, 0);
    message_threads.handle_deleted_messages([41]);
    assert.equal(summary_batch_requests.length, 0);
    // The server then pushes the post-delete summary; the last reply's
    // deletion arrives as a null summary.
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: null,
    });
    assert.equal(message_store.get(31).reply_summary, undefined);
});

run_test("cold child read and delete events wait for the pushed summary", () => {
    reset_dom();
    const root = root_message();
    root.reply_summary = summary({count: 2, unread_count: 2});
    message_store.set_messages_for_tests([{message: root}]);
    // Uncached messages cannot be attributed to a root locally, so no
    // immediate mutation and no fetch happen; the pushed summary event (or
    // the next hydration) is what reconciles the root.
    message_threads.handle_read_flag_update([404], true);
    assert.equal(summary_batch_requests.length, 0);
    assert.equal(root.reply_summary.unread_reply_count, 2);
    message_threads.handle_deleted_messages([405]);
    assert.equal(summary_batch_requests.length, 0);
    assert.equal(root.reply_summary.reply_count, 2);
    message_threads.handle_summary_event({
        id: 1,
        type: "message_thread_summary",
        root_message_id: 31,
        summary: {
            reply_count: 1,
            last_reply_at: 200,
            last_sender: participant,
            participants: [participant],
        },
    });
    assert.equal(root.reply_summary.reply_count, 1);
    assert.equal(root.reply_summary.unread_reply_count, 1);
});

run_test("topic read settles cold authoritative summaries without a fetch", () => {
    reset_dom();
    const root = root_message();
    root.reply_summary = summary({count: 2, unread_count: 2});
    message_store.set_messages_for_tests([{message: root}]);
    render_row();
    message_threads.handle_topic_marked_read(9, "NATIVE THREADS");
    assert.equal(root.reply_summary.unread_reply_count, 0);
    assert.equal(summary_batch_requests.length, 0);
});

run_test("moved roots update the live composer recipient", () => {
    reset_dom();
    render_row().querySelector(".message_thread_button").click();
    assert.deepEqual(compose_creations[0].options.get_recipient(), {
        type: "stream",
        stream_id: 9,
        topic: "native threads",
    });
    message_store.get(31).topic = "moved topic";
    message_threads.handle_updated_messages([{message_id: 31, message_ids: [31, 41]}]);
    assert.deepEqual(compose_creations[0].options.get_recipient(), {
        type: "stream",
        stream_id: 9,
        topic: "moved topic",
    });
    assert.equal(compose_creations[0].editor.recipient_refresh_count, 1);
});

run_test("child-only rows hydrate the exact root before opening", () => {
    reset_dom();
    message_store.set_messages_for_tests([{message: child_message()}]);
    const child_row = render_row(41);
    assert.equal(
        child_row.querySelector(".message-thread-host").textContent,
        "translated: View thread",
    );
    assert.equal(summary_requests.length, 1);
    assert.deepEqual(summary_requests[0].msg_list_data.filter.public_terms(), [
        {operator: "id", operand: "31", negated: false},
    ]);
    message_store.set_messages_for_tests([{message: root_message()}, {message: child_message()}]);
    summary_requests[0].cont();
    child_row.querySelector(".message-thread-child-launch").click();
    assert.equal(compose_creations[0].options.thread_root_message_id, 31);
});

run_test("child hydration clears failure state, retries on demand, and cancels on teardown", () => {
    reset_dom();
    message_store.set_messages_for_tests([{message: child_message()}]);
    const child_row = render_row(41);
    const launch = child_row.querySelector(".message-thread-child-launch");
    launch.click();
    assert.equal(launch.getAttribute("aria-busy"), "true");
    assert.equal(summary_requests[0].suppress_connection_error, true);
    summary_requests[0].on_error({status: 400});
    assert.equal(launch.hasAttribute("aria-busy"), false);
    assert.equal(document.querySelector("#message-thread-pane").hidden, true);

    launch.click();
    assert.equal(summary_requests.length, 2);
    assert.equal(launch.getAttribute("aria-busy"), "true");
    const retry_data = summary_requests[1].msg_list_data;
    message_threads.destroy_for_message(41);
    assert.equal(launch.hasAttribute("aria-busy"), false);
    assert.equal(cancelled_retry_data.includes(retry_data), true);
});

run_test("unread replies are marked only while visible and focused", () => {
    reset_dom();
    viewport_focused = false;
    render_row().querySelector(".message_thread_button").click();
    message_lists_created[1].add_messages([child_message()]);
    narrow_requests[0].cont();
    assert.deepEqual(read_message_ids, []);
    viewport_focused = true;
    window.dispatchEvent(new Event("focus"));
    assert.deepEqual([...new Set(read_message_ids)], [41]);
});

run_test("agent cycler aggregates side runs attached to replies", () => {
    reset_dom();
    remember_agents();
    legacy_run_state.remember_runs([
        {
            id: 5,
            status: "running",
            ambient_triggered: false,
            result_message_id: 41,
            agents: [
                {
                    thread_id: "side-agent",
                    nickname: "Sidecar",
                    role: "reviewer",
                    status: "active",
                },
            ],
        },
    ]);
    render_row().querySelector(".message-thread-agent-launch").click();
    const pane = document.querySelector("#message-thread-pane");
    const current = pane.querySelector(".message-thread-agent-current");
    const next = pane.querySelector(".message-thread-agent-cycle-next");
    // Stable first-seen spawn order: child-a, child-b, then the side agent.
    assert.equal(current.dataset.threadKey, "child-a");
    next.click();
    assert.equal(current.dataset.threadKey, "child-b");
    next.click();
    assert.equal(controller_options.run_id, 5);
    assert.equal(controller_options.thread_id, "side-agent");
    assert.equal(current.dataset.threadKey, "side-agent");
    // The cycler wraps back to the human thread.
    next.click();
    assert.equal(current.dataset.threadKey, undefined);
    assert.equal(current.textContent, "translated: Replies");
});

run_test("desktop and mobile reuse one accessible pane shell", () => {
    reset_dom();
    desktop_viewport = true;
    remember_agents();
    render_row().querySelector(".message-thread-agent-launch").click();
    const pane = document.querySelector("#message-thread-pane");
    assert.equal(pane.getAttribute("role"), "complementary");
    assert.equal(pane.hasAttribute("aria-modal"), false);

    desktop_viewport = false;
    window.dispatchEvent(new Event("resize"));
    assert.equal(pane.getAttribute("role"), "dialog");
    assert.equal(document.querySelector("#primary-shell").hasAttribute("inert"), true);
    document.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    flush_animation_frame();
    assert.equal(pane.hidden, true);
    assert.equal(document.querySelector("#primary-shell").hasAttribute("inert"), false);
});
