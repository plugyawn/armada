"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const render_message_controls = require("../templates/message_controls.hbs");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const dom = new JSDOM(
    "<!doctype html><html><body><main class='app-main'></main><div class='message_row' data-message-id='31'><div class='message_controls'><button class='message_thread_button'></button></div><div class='message-thread-host' hidden></div></div><aside id='message-thread-pane' hidden></aside></body></html>",
    {pretendToBeVisual: true},
);
const {window} = dom;
window.HTMLElement.prototype.to_$ = () => $(".message-thread-test-element");
window.HTMLElement.prototype.scrollIntoView = () => {};
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
    SubmitEvent: window.SubmitEvent,
    CSS: {escape: (value) => value},
})) {
    set_global(name, value);
}

let disable_scrolling_count = 0;
let enable_scrolling_count = 0;
let controller_options;
let destroyed_count = 0;
let desktop_viewport = false;
const get_requests = [];
const post_requests = [];

mock_esm("../src/channel", {
    get(options) {
        get_requests.push(options);
        return {abort() {}};
    },
    post(options) {
        post_requests.push(options);
        return {abort() {}};
    },
});
mock_esm("../src/modals", {register_pre_open_hook() {}});
mock_esm("../src/overlays", {register_pre_open_hook() {}});
mock_esm("../src/overlay_util", {
    disable_scrolling() {
        disable_scrolling_count += 1;
    },
    enable_scrolling() {
        enable_scrolling_count += 1;
    },
});
mock_esm("../src/rendered_markdown", {update_elements() {}});
mock_esm("../src/timerender", {
    format_time_modern: () => "10:00",
    relative_time_string_from_date: () => "2h ago",
});
mock_esm("../src/ui_util", {
    matches_viewport_state: () => desktop_viewport,
    parse_html(html) {
        const template = document.createElement("template");
        template.innerHTML = html;
        return template.content;
    },
});
mock_esm("../src/pear_run_trace", {
    create_controller(options) {
        controller_options = options;
        return {
            destroy() {
                destroyed_count += 1;
            },
            load() {},
            mark_dirty() {},
            get dirty() {
                return false;
            },
            get failed() {
                return false;
            },
            get in_flight() {
                return false;
            },
            get loaded() {
                return true;
            },
            get request_state() {
                return "ready";
            },
        };
    },
});

const pear_run_state = zrequire("pear_run_state");
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

function summary({count = 1, last_sender = participant} = {}) {
    return {
        reply_count: count,
        last_reply_at: 100,
        last_sender,
        participants: [participant],
    };
}

function reply({id = 91, content = "<p>Thread reply</p>"} = {}) {
    return {
        id,
        message_id: 31,
        sender_id: 7,
        sender_full_name: "Ada Lovelace",
        avatar_url: "/avatar/7",
        content,
        content_type: "text/html",
        timestamp: 100,
    };
}

function reset_dom() {
    message_threads.clear_for_testing();
    pear_run_state.clear_for_testing();
    message_store.set_messages_for_tests([
        {
            message: {
                id: 31,
                sender_id: 5,
                sender_full_name: "Pear",
                avatar_url: "/avatar/5",
                content: "<p>Original thread message</p>",
                timestamp: 90,
                is_pear_message: true,
            },
        },
    ]);
    const host = document.querySelector(".message-thread-host");
    host.replaceChildren();
    host.hidden = true;
    delete host.dataset.signature;
    const textarea = document.querySelector(".message-thread-composer-input");
    if (textarea !== null) {
        textarea.value = "";
    }
    disable_scrolling_count = 0;
    enable_scrolling_count = 0;
    controller_options = undefined;
    destroyed_count = 0;
    desktop_viewport = false;
    get_requests.length = 0;
    post_requests.length = 0;
    animation_frame_callbacks = [];
}

function flush_animation_frame() {
    const callbacks = animation_frame_callbacks;
    animation_frame_callbacks = [];
    for (const {callback} of callbacks) {
        callback(0);
    }
}

function render_row() {
    const row = document.querySelector(".message_row");
    message_threads.render_for_message_row(row, 31, () => {});
    return row;
}

function open_from_toolbar() {
    const row = render_row();
    row.querySelector(".message_thread_button").click();
    return document.querySelector("#message-thread-pane");
}

function remember_agents() {
    pear_run_state.remember_runs([
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

run_test("message controls expose one real thread button only for durable messages", () => {
    const durable = render_message_controls({
        is_archived: false,
        msg: {locally_echoed: false, sent_by_me: false, starred: false},
    });
    assert.match(durable, /message_thread_button/);
    assert.match(durable, /zulip-icon-message-square-text/);
    assert.doesNotMatch(
        render_message_controls({
            is_archived: true,
            msg: {locally_echoed: false, sent_by_me: false, starred: false},
        }),
        /message_thread_button/,
    );
    assert.doesNotMatch(
        render_message_controls({
            is_archived: false,
            msg: {locally_echoed: true, sent_by_me: false, starred: false},
        }),
        /message_thread_button/,
    );
});

run_test("ordinary rows do not allocate thread state", () => {
    reset_dom();
    const row = render_row();
    assert.equal(row.querySelector(".message-thread-host").hidden, true);
    assert.equal(message_threads.reply_state_count_for_testing(), 0);
});

run_test("the toolbar opens an empty thread lazily and focuses its composer", () => {
    reset_dom();
    const pane = open_from_toolbar();
    assert.equal(pane.hidden, false);
    assert.equal(pane.getAttribute("role"), "dialog");
    assert.equal(get_requests.length, 1);
    assert.equal(get_requests[0].url, "/json/messages/31/thread");
    get_requests[0].success({reply_summary: null, replies: [], has_more: false});
    flush_animation_frame();
    assert.equal(document.activeElement, pane.querySelector(".message-thread-composer-input"));
    assert.match(pane.querySelector(".message-thread-reply-status").textContent, /No replies/);
    assert.equal(pane.querySelector(".message-thread-pane-tabs").hidden, true);
    assert.equal(pane.querySelectorAll(".message-thread-format-button").length, 6);
    assert.equal(
        pane.querySelector(".message-thread-root .message-thread-entry-content").textContent,
        "Original thread message",
    );
    assert.equal(
        pane.querySelectorAll(".message-thread-root .message-thread-entry-pear").length,
        1,
    );
    assert.equal(message_threads.reply_state_count_for_testing(), 1);
});

run_test("nullable last sender parses and human and agent previews share one strip", () => {
    reset_dom();
    assert.notEqual(
        message_threads.parse_thread_event_for_testing({
            type: "message_thread",
            message_id: 31,
            reply_summary: summary({last_sender: null}),
            reply: reply(),
        }),
        undefined,
    );
    message_threads.remember_reply_summary(31, summary({last_sender: null}));
    remember_agents();
    const row = render_row();
    const host = row.querySelector(".message-thread-host");
    assert.equal(host.hidden, false);
    assert.equal(host.querySelectorAll(".message-thread-strip").length, 1);
    assert.equal(host.querySelectorAll(".message-thread-reply-preview").length, 1);
    assert.equal(host.querySelectorAll(".message-thread-agent-preview").length, 1);
    assert.equal(host.querySelectorAll(".message-thread-agent-launch").length, 2);
    assert.match(host.textContent, /1 reply/);
    assert.match(host.textContent, /2 agents/);
});

run_test("composer enables on input and POST and live event deduplicate one reply", () => {
    reset_dom();
    const pane = open_from_toolbar();
    get_requests[0].success({reply_summary: null, replies: [], has_more: false});
    const textarea = pane.querySelector(".message-thread-composer-input");
    const submit = pane.querySelector(".message-thread-composer-submit");
    assert.equal(submit.disabled, true);
    textarea.value = "Hello thread";
    textarea.dispatchEvent(new Event("input", {bubbles: true}));
    assert.equal(submit.disabled, false);
    submit.click();
    assert.equal(post_requests.length, 1);
    assert.deepEqual(post_requests[0].data, {content: "Hello thread"});
    post_requests[0].success({reply_summary: summary(), reply: reply()});
    message_threads.apply_message_thread_event({
        type: "message_thread",
        message_id: 31,
        reply_summary: summary(),
        reply: reply(),
    });
    assert.equal(pane.querySelectorAll(".message-thread-reply").length, 1);
    assert.equal(
        pane.querySelector(".message-thread-reply .message-thread-entry-content").textContent,
        "Thread reply",
    );
    assert.equal(document.querySelector(".message-thread-reply-preview").hidden, false);
});

run_test("failed POST remains visible and can be retried", () => {
    reset_dom();
    const pane = open_from_toolbar();
    get_requests[0].success({reply_summary: null, replies: [], has_more: false});
    const textarea = pane.querySelector(".message-thread-composer-input");
    textarea.value = "Retry me";
    textarea.dispatchEvent(new Event("input", {bubbles: true}));
    pane.querySelector(".message-thread-composer-submit").click();
    post_requests[0].error();
    assert.match(pane.querySelector(".message-thread-reply-status").textContent, /Could not send/);
    assert.equal(textarea.disabled, false);
});

run_test("the shared composer keeps its draft bound to the parent message", () => {
    reset_dom();
    const pane = open_from_toolbar();
    get_requests[0].success({reply_summary: null, replies: [], has_more: false});
    const textarea = pane.querySelector(".message-thread-composer-input");
    textarea.value = "Still drafting";
    textarea.dispatchEvent(new Event("input", {bubbles: true}));
    document.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    flush_animation_frame();
    document.querySelector(".message_thread_button").click();
    assert.equal(textarea.value, "Still drafting");
});

run_test("agent previews open as tabs in the same reusable pane", () => {
    reset_dom();
    remember_agents();
    const row = render_row();
    row.querySelector(".message-thread-agent-launch").click();
    const pane = document.querySelector("#message-thread-pane");
    assert.equal(pane.hidden, false);
    assert.equal(pane.querySelectorAll("[role=tab]").length, 3);
    assert.equal(controller_options.thread_id, "child-a");
    pane.querySelectorAll("[role=tab]")[2].click();
    assert.equal(controller_options.thread_id, "child-b");
    assert.equal(destroyed_count, 1);
    assert.equal(document.querySelectorAll("#message-thread-pane").length, 1);
});

run_test("desktop reuses the pane as a non-modal complement", () => {
    reset_dom();
    desktop_viewport = true;
    remember_agents();
    const row = render_row();
    row.querySelector(".message-thread-agent-launch").click();
    const pane = document.querySelector("#message-thread-pane");
    assert.equal(pane.getAttribute("role"), "complementary");
    assert.equal(pane.hasAttribute("aria-modal"), false);
    assert.equal(disable_scrolling_count, 0);
});

run_test("Escape closes the mobile pane and restores launch focus", () => {
    reset_dom();
    remember_agents();
    const row = render_row();
    const launch = row.querySelector(".message-thread-agent-launch");
    launch.click();
    document.dispatchEvent(new window.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    flush_animation_frame();
    assert.equal(document.querySelector("#message-thread-pane").hidden, true);
    assert.equal(document.activeElement, launch);
    assert.equal(enable_scrolling_count, 1);
});
