"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const dom = new JSDOM(
    `<!doctype html><html><body><div id="pear-agent-roster" hidden></div></body></html>`,
);
set_global("document", dom.window.document);
set_global("window", dom.window);

const narrow_state = mock_esm("../src/narrow_state.ts");
const pear_run_state = mock_esm("../src/pear_run_state.ts");
const message_thread_hooks = mock_esm("../src/message_thread_hooks.ts", {
    HUMAN_THREAD_KEY: "replies",
});
mock_esm("../src/timerender", {relative_time_string_from_date: () => "2h ago"});
const right_sidebar_tabs = mock_esm("../src/right_sidebar_tabs.ts", {
    current: () => "agents",
    on_tab_changed: () => () => {},
});

const pear_agent_roster = zrequire("pear_agent_roster");

function agent(thread_id, nickname, status, current_activity) {
    return {thread_id, nickname, role: "default", status, current_activity};
}

function run(id, result_message_id, agents, overrides = {}) {
    return {
        id,
        result_message_id,
        agents,
        date_created: 100,
        status: "done",
        stream: "devel",
        topic: "attention",
        tool_count: 2,
        usage: {total_tokens: 75700, duration_seconds: 68},
        ...overrides,
    };
}

run_test("roster groups agents under turn headers, live turns first", () => {
    narrow_state.stream_name = () => "devel";
    narrow_state.topic = () => "attention";
    pear_run_state.runs_for_context = () => [
        run(1, 501, [agent("t-idle", "Zeno", "idle")], {date_created: 300}),
        run(2, 502, [agent("t-active", "Newton", "running", "reading the pad")], {
            status: "running",
            date_created: 200,
        }),
    ];
    pear_run_state.on_run_state_changed = () => () => {};

    pear_agent_roster.render();
    const host = document.querySelector("#pear-agent-roster");
    const headers = host.querySelectorAll(".pear-agent-roster-turn");
    const rows = host.querySelectorAll(".pear-agent-roster-row");
    assert.equal(headers.length, 2);
    assert.equal(rows.length, 2);
    // Live turn first even though it is the older run.
    assert.ok(headers[0].classList.contains("pear-agent-roster-turn-live"));
    assert.equal(headers[0].textContent, "run 1m 8s · translated: 2 tools · 75.7k tokens · 2h ago");
    assert.ok(!headers[1].classList.contains("pear-agent-roster-turn-live"));
    assert.ok(rows[0].textContent.includes("Newton"));
    assert.ok(rows[0].textContent.includes("reading the pad"));
    assert.ok(rows[1].textContent.includes("Zeno"));

    // Turn header opens the turn at Replies; agent row opens the agent.
    const opened = [];
    message_thread_hooks.open_thread = (message_id, thread_key) => {
        opened.push({message_id, thread_key});
    };
    headers[0].click();
    rows[0].click();
    assert.deepEqual(opened, [
        {message_id: 502, thread_key: "replies"},
        {message_id: 502, thread_key: "t-active"},
    ]);

    pear_agent_roster.clear_for_testing();
});

run_test("roster empty state names how to get agents", () => {
    narrow_state.stream_name = () => "devel";
    narrow_state.topic = () => "attention";
    pear_run_state.runs_for_context = () => [];
    pear_run_state.on_run_state_changed = () => () => {};

    pear_agent_roster.render();
    const empty = document.querySelector(".pear-agent-roster-empty");
    assert.ok(empty.textContent.includes("No agents"));
    pear_agent_roster.clear_for_testing();
});

run_test("switching to Agents lands on the newest live turn once", () => {
    const live_run = run(9, 909, [agent("t-live", "Newton", "running")], {status: "running"});
    narrow_state.stream_name = () => "devel";
    narrow_state.topic = () => "attention";
    pear_run_state.runs_for_context = () => [live_run];
    const tab_listeners = [];
    right_sidebar_tabs.on_tab_changed = (listener) => {
        tab_listeners.push(listener);
        return () => {};
    };
    pear_run_state.on_run_state_changed = () => () => {};
    const opened = [];
    message_thread_hooks.open_thread = (message_id, thread_key) => {
        opened.push({message_id, thread_key});
    };

    pear_agent_roster.initialize();
    const on_tab = tab_listeners[0];
    on_tab("agents");
    assert.deepEqual(opened, [{message_id: 909, thread_key: "replies"}]);

    // Same turn is not re-opened while it is still live.
    on_tab("people");
    on_tab("agents");
    assert.equal(opened.length, 1);

    pear_agent_roster.clear_for_testing();
});
