"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const requests = [];
let stream = "devel";
let topic = "topic-a";
mock_esm("../src/channel", {
    get(options) {
        requests.push(options);
    },
});
mock_esm("../src/narrow_state", {
    stream_name: () => stream,
    topic: () => topic,
});

const pear_panel = zrequire("pear_panel");
const pear_run_state = zrequire("pear_run_state");

run_test("stale narrow callbacks cannot replace runs or surface errors", () => {
    requests.length = 0;
    stream = "devel";
    topic = "topic-a";
    pear_panel.set_refresh_state_for_testing({runs: [{id: 41}]});

    pear_panel.refresh();
    assert.equal(requests.length, 1);
    topic = "topic-b";
    requests[0].success({pear_runs: [], pear_topic_context: null});
    requests[0].error({readyState: 4, status: 500, responseText: "stale"});

    assert.deepEqual(pear_panel.refresh_state_for_testing(), {run_ids: [41], error: ""});
    requests[0].complete();
});

run_test("coalesced refresh issues one strongest follow-up", () => {
    requests.length = 0;
    stream = "devel";
    topic = "topic-a";
    pear_panel.set_refresh_state_for_testing({runs: []});

    pear_panel.refresh({live: true});
    pear_panel.refresh({force_context: true});
    pear_panel.refresh({live: true});
    assert.equal(requests.length, 1);

    requests[0].complete();
    assert.equal(requests.length, 2);
    assert.equal(requests[1].data.include_context, true);
    requests[1].complete();
    assert.equal(requests.length, 2);
});

run_test("unknown-run snapshot overlays newer reducer state", () => {
    pear_run_state.clear_for_testing();
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 50,
        run_id: 77,
        event_type: "done",
        status: "done",
        result_message_id: 770,
        stream: "devel",
        topic: "topic-a",
        current_activity: "Completed",
        tool_count: 4,
        plan: [{step: "Verify", status: "completed"}],
        overview: "Verified",
        has_visible_assistant_text: true,
        usage: {total_tokens: 900},
        duration_seconds: 8,
        trace_available: true,
        run_date_created: 100,
        date_created: 150,
    });

    const reconciled = pear_panel.reconcile_run_snapshot_for_testing(
        {
            id: 77,
            snapshot_sequence: 49,
            status: "running",
            result_message_id: 770,
            current_activity: "Starting",
            plan: null,
            overview: null,
            tool_count: 0,
            usage: null,
            live: null,
        },
        undefined,
    );

    assert.equal(reconciled.status, "done");
    assert.equal(reconciled.current_activity, "Completed");
    assert.equal(reconciled.tool_count, 4);
    assert.equal(reconciled.usage.total_tokens, 900);
    pear_run_state.clear_for_testing();
});
