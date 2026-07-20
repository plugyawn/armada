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

const narrow_state = mock_esm("../src/narrow_state.ts");
const pear_run_state = mock_esm("../src/pear_run_state.ts");
const message_thread_hooks = mock_esm("../src/message_thread_hooks.ts");
const right_sidebar_tabs = mock_esm("../src/right_sidebar_tabs.ts", {
    current: () => "agents",
});

const pear_agent_roster = zrequire("pear_agent_roster");

function agent(thread_id, nickname, status, current_activity) {
    return {thread_id, nickname, role: "default", status, current_activity};
}

function run(id, result_message_id, agents, date_created = 100) {
    return {
        id,
        result_message_id,
        agents,
        date_created,
        status: "done",
        stream: "devel",
        topic: "attention",
    };
}

run_test("roster lists live agents first and opens the pane on click", () => {
    narrow_state.stream_name = () => "devel";
    narrow_state.topic = () => "attention";
    pear_run_state.runs_for_context = (stream, topic) => {
        assert.equal(stream, "devel");
        assert.equal(topic, "attention");
        return [
            run(1, 501, [agent("t-idle", "Zeno", "idle")]),
            run(2, 502, [agent("t-active", "Newton", "running", "reading the pad")], 200),
        ];
    };
    pear_run_state.on_run_state_changed = () => () => {};

    pear_agent_roster.render();
    const host = document.querySelector("#pear-agent-roster");
    const rows = host.querySelectorAll(".pear-agent-roster-row");
    assert.equal(rows.length, 2);
    // Live first regardless of run recency.
    assert.ok(rows[0].textContent.includes("Newton"));
    assert.ok(rows[0].textContent.includes("reading the pad"));
    assert.ok(rows[1].textContent.includes("Zeno"));

    const opened = [];
    message_thread_hooks.open_thread = (message_id, thread_key, anchor) => {
        opened.push({message_id, thread_key, anchor});
    };
    rows[0].click();
    assert.deepEqual(opened, [{message_id: 502, thread_key: "t-active", anchor: rows[0]}]);

    // Signature gate: a second render with the same data is a DOM no-op.
    const first_html = host.innerHTML;
    pear_agent_roster.render();
    assert.equal(host.innerHTML, first_html);

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
