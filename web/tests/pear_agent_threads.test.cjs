"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM(
    "<!doctype html><html><body><main class='app-main'></main><div class='message_row pear-message' data-message-id='31'><div class='pear-agent-thread-host' hidden></div></div><aside id='pear-agent-pane' hidden></aside></body></html>",
    {pretendToBeVisual: true},
);
const {window} = dom;
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
    KeyboardEvent: window.KeyboardEvent,
    CSS: {escape: (value) => value},
})) {
    set_global(name, value);
}

let disable_scrolling_count = 0;
let enable_scrolling_count = 0;
let controller_options;
let destroyed_count = 0;
let mark_dirty_count = 0;
let desktop_viewport = false;
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
mock_esm("../src/ui_util", {matches_viewport_state: () => desktop_viewport});
mock_esm("../src/pear_run_trace", {
    create_controller(options) {
        controller_options = options;
        return {
            destroy() {
                destroyed_count += 1;
            },
            load() {},
            mark_dirty() {
                mark_dirty_count += 1;
            },
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
const pear_agent_threads = zrequire("pear_agent_threads");

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

function reset_dom() {
    pear_agent_threads.close({restore_focus: false});
    pear_run_state.clear_for_testing();
    const host = document.querySelector(".pear-agent-thread-host");
    host.replaceChildren();
    host.hidden = true;
    delete host.dataset.signature;
    disable_scrolling_count = 0;
    enable_scrolling_count = 0;
    controller_options = undefined;
    destroyed_count = 0;
    mark_dirty_count = 0;
    desktop_viewport = false;
    animation_frame_callbacks = [];
}

function push_event({
    sequence,
    event_type,
    agent_thread_id,
    agent = null,
    next_agents = null,
    status = "running",
}) {
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence,
        run_id: 4,
        event_type,
        status,
        result_message_id: 31,
        stream: "devel",
        topic: "agents",
        current_activity: "Working",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 10,
        date_created: 10 + sequence,
        agent_thread_id,
        agent,
        agents: next_agents,
    });
}

function flush_animation_frame() {
    const callbacks = animation_frame_callbacks;
    animation_frame_callbacks = [];
    for (const {callback} of callbacks) {
        callback(0);
    }
}

run_test("sigils are deterministic, symmetric, and agent selection is stable", () => {
    const cells = pear_agent_threads.sigil_cells_for_testing("child-a");
    assert.deepEqual(cells, pear_agent_threads.sigil_cells_for_testing("child-a"));
    for (let row = 0; row < 3; row += 1) {
        assert.equal(cells[row * 3], cells[row * 3 + 2]);
    }
    assert.equal(pear_agent_threads.display_name_for_testing(agents[0], 0), "Scout");
    assert.equal(
        pear_agent_threads.selection_after_update_for_testing(agents, "child-b"),
        "child-b",
    );
    assert.equal(
        pear_agent_threads.selection_after_update_for_testing(agents, "missing"),
        "child-a",
    );
    assert.equal(pear_agent_threads.status_tone_for_testing("running"), "active");
    assert.equal(pear_agent_threads.status_tone_for_testing("failed"), "failed");
});

run_test("no-agent runs stay absent; child badges open one reusable mobile pane", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    const host = document.querySelector(".pear-agent-thread-host");
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    assert.equal(host.hidden, true);

    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    assert.equal(host.hidden, false);
    assert.equal(host.querySelectorAll(".pear-agent-thread-badge").length, 2);

    const first = host.querySelector(".pear-agent-thread-badge");
    first.click();
    const pane = document.querySelector("#pear-agent-pane");
    assert.equal(pane.hidden, false);
    assert.equal(pane.getAttribute("role"), "dialog");
    assert.equal(pane.getAttribute("aria-modal"), "true");
    assert.equal(controller_options.thread_id, "child-a");
    assert.equal(disable_scrolling_count, 1);
    const first_tab = pane.querySelector("[role=tab]");
    assert.equal(first_tab.getAttribute("aria-controls"), "pear-agent-pane-panel");
    assert.equal(
        pane.querySelector("[role=tabpanel]").getAttribute("aria-labelledby"),
        first_tab.id,
    );

    pane.querySelectorAll("[role=tab]")[1].click();
    assert.equal(document.querySelectorAll("#pear-agent-pane").length, 1);
    assert.equal(controller_options.thread_id, "child-b");
    assert.equal(destroyed_count, 1);
    assert.equal(
        pane.querySelector("[role=tabpanel]").getAttribute("aria-labelledby"),
        "pear-agent-pane-tab-1",
    );

    first.focus();
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    flush_animation_frame();
    assert.equal(pane.hidden, true);
    assert.equal(document.activeElement, first);
    assert.equal(enable_scrolling_count, 1);
});

run_test("only events from the selected native agent refresh its open trace", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    row.querySelector(".pear-agent-thread-badge").click();

    push_event({sequence: 1, event_type: "transcript", agent_thread_id: null});
    push_event({sequence: 2, event_type: "agent", agent_thread_id: "child-b", next_agents: agents});
    assert.equal(mark_dirty_count, 0);

    push_event({sequence: 3, event_type: "agent", agent_thread_id: "child-a", next_agents: agents});
    assert.equal(mark_dirty_count, 1);
});

run_test("activity deltas update the pane without rebuilding its tabs", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    row.querySelector(".pear-agent-thread-badge").click();
    const pane = document.querySelector("#pear-agent-pane");
    const first_tab = pane.querySelector("[role=tab]");
    flush_animation_frame();

    push_event({
        sequence: 1,
        event_type: "agent",
        agent_thread_id: "child-a",
        agent: {
            thread_id: "child-a",
            current_activity: "Indexing symbols",
        },
    });
    push_event({
        sequence: 2,
        event_type: "agent",
        agent_thread_id: "child-a",
        agent: {
            thread_id: "child-a",
            current_activity: "Comparing symbols",
        },
    });
    assert.equal(animation_frame_callbacks.length, 1, "pane updates coalesce into one frame");
    flush_animation_frame();

    assert.equal(pane.querySelector(".pear-agent-pane-activity").textContent, "Comparing symbols");
    assert.equal(pane.querySelector("[role=tab]"), first_tab);
    assert.equal(mark_dirty_count, 2);
});

run_test("removing the last child closes the pane and restores its launch focus", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    const badge = row.querySelector(".pear-agent-thread-badge");
    badge.click();
    const pane = document.querySelector("#pear-agent-pane");
    pane.querySelector(".pear-agent-pane-close").focus();

    push_event({
        sequence: 1,
        event_type: "agent",
        agent_thread_id: "child-a",
        next_agents: [],
    });
    flush_animation_frame();

    const host = row.querySelector(".pear-agent-thread-host");
    host.replaceChildren();
    host.hidden = true;
    flush_animation_frame();

    assert.equal(pane.hidden, true);
    assert.equal(document.activeElement, row);
});

run_test("closing before the opening frame cannot focus the hidden pane", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    const badge = row.querySelector(".pear-agent-thread-badge");
    badge.click();
    pear_agent_threads.close();
    flush_animation_frame();

    assert.equal(document.querySelector("#pear-agent-pane").hidden, true);
    assert.equal(document.activeElement, badge);
});

run_test("the mobile focus trap ignores controls inside collapsed trace content", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    row.querySelector(".pear-agent-thread-badge").click();
    const pane = document.querySelector("#pear-agent-pane");
    const collapsed = document.createElement("div");
    collapsed.setAttribute("aria-hidden", "true");
    const hidden_button = document.createElement("button");
    collapsed.append(hidden_button);
    pane.querySelector(".pear-run-trace").append(collapsed);
    const visible_buttons = [...pane.querySelectorAll("button")].filter(
        (button) => !collapsed.contains(button),
    );
    for (const button of [...visible_buttons, hidden_button]) {
        button.getClientRects = () => [{}];
    }
    const last_visible = visible_buttons.at(-1);
    last_visible.focus();

    document.dispatchEvent(new KeyboardEvent("keydown", {key: "Tab", bubbles: true}));

    assert.equal(document.activeElement, visible_buttons[0]);
});

run_test("the same singleton becomes a non-modal side pane on desktop", () => {
    reset_dom();
    desktop_viewport = true;
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    row.querySelector(".pear-agent-thread-badge").click();

    const pane = document.querySelector("#pear-agent-pane");
    assert.equal(pane.getAttribute("role"), "complementary");
    assert.equal(pane.hasAttribute("aria-modal"), false);
    assert.equal(document.querySelectorAll("#pear-agent-pane").length, 1);
    assert.equal(disable_scrolling_count, 0);
});

run_test("settled child traces remain reconstructible and read-only", () => {
    reset_dom();
    const row = document.querySelector(".message_row");
    pear_run_state.remember_runs([
        {
            id: 4,
            status: "running",
            ambient_triggered: false,
            result_message_id: 31,
            agents,
        },
    ]);
    pear_agent_threads.render_for_message_row(row, 31, () => {});
    const badge = row.querySelector(".pear-agent-thread-badge");
    badge.click();

    push_event({
        sequence: 1,
        event_type: "done",
        agent_thread_id: null,
        status: "done",
    });
    assert.deepEqual(pear_run_state.get_run_view_by_id(4).agents, agents);
    pear_agent_threads.close({restore_focus: false});
    badge.click();

    const pane = document.querySelector("#pear-agent-pane");
    assert.equal(controller_options.thread_id, "child-a");
    assert.equal(pane.querySelector("textarea, input, [contenteditable=true]"), null);
});
