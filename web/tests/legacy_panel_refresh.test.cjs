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

const legacy_panel = zrequire("legacy_panel");
const legacy_run_state = zrequire("legacy_run_state");

run_test("transient status errors never force the workbench open", () => {
    assert.equal(
        legacy_panel.workbench_open_for_testing({
            context_key_value: "devel\\ntopic-a",
            manually_open: false,
        }),
        false,
    );
    assert.equal(
        legacy_panel.workbench_open_for_testing({
            context_key_value: "devel\\ntopic-a",
            manually_open: true,
        }),
        true,
    );
});

run_test("liveness reconciliation covers active runs and exact unbound placeholders", () => {
    assert.equal(
        legacy_panel.liveness_reconciliation_owner_for_testing({
            run_id: 7,
            run_status: "running",
            placeholder_message_id: 70,
        }),
        "run:7",
    );
    assert.equal(
        legacy_panel.liveness_reconciliation_owner_for_testing({
            placeholder_message_id: 70,
        }),
        "placeholder:70",
    );
    assert.equal(
        legacy_panel.liveness_reconciliation_owner_for_testing({
            run_id: 7,
            run_status: "done",
        }),
        undefined,
    );
    assert.equal(
        legacy_panel.liveness_reconciliation_needs_timer_for_testing({
            owner: "placeholder:70",
            attempted: false,
            timer_armed: false,
        }),
        true,
    );
    assert.equal(
        legacy_panel.liveness_reconciliation_needs_timer_for_testing({
            owner: "placeholder:70",
            attempted: true,
            timer_armed: false,
        }),
        false,
    );
    assert.equal(
        legacy_panel.liveness_reconciliation_needs_timer_for_testing({
            owner: "run:7",
            attempted: false,
            timer_armed: true,
        }),
        false,
    );
});

run_test("rail queue depth excludes the currently displayed active run", () => {
    assert.equal(
        legacy_panel.armada_rail_queue_depth_for_testing([
            {status: "running"},
            {status: "queued"},
            {status: "queued"},
            {status: "done"},
        ]),
        2,
    );
    assert.equal(legacy_panel.armada_rail_queue_depth_for_testing([{status: "running"}]), 0);
});

run_test("dismissed status errors stay quiet until server truth changes", () => {
    assert.equal(
        legacy_panel.visible_panel_error_for_testing("Pear status is unavailable.", undefined),
        "Pear status is unavailable.",
    );
    assert.equal(
        legacy_panel.visible_panel_error_for_testing(
            "Pear status is unavailable.",
            "Pear status is unavailable.",
        ),
        "",
    );
    assert.equal(
        legacy_panel.visible_panel_error_for_testing(
            "Pear returned an invalid runs payload.",
            "Pear status is unavailable.",
        ),
        "Pear returned an invalid runs payload.",
    );
});

run_test("plan rail keeps current context and marks hidden steps", () => {
    const steps = legacy_panel.armada_rail_plan_steps_for_testing([
        {
            id: 1,
            status: "running",
            plan: [
                {step: "one", status: "completed"},
                {step: "two", status: "completed"},
                {step: "three", status: "in_progress"},
                {step: "four", status: "pending"},
                {step: "five", status: "pending"},
            ],
        },
    ]);
    assert.deepEqual(
        steps.map((step) => [step.step, step.status_class, step.is_current === true]),
        [
            ["…", "legacy-rail-plan-ellipsis", false],
            ["two", "legacy-rail-plan-done", false],
            ["three", "legacy-rail-plan-active", true],
            ["four", "legacy-rail-plan-pending", false],
            ["…", "legacy-rail-plan-ellipsis", false],
        ],
    );
});

run_test("planless active rail exposes one current Working step", () => {
    const steps = legacy_panel.armada_rail_plan_steps_for_testing([{id: 1, status: "queued"}]);
    assert.deepEqual(
        steps.map((step) => [step.step, step.status_class, step.is_current]),
        [["translated: Working", "legacy-rail-plan-active", true]],
    );
});

run_test("stale narrow callbacks cannot replace runs or surface errors", () => {
    requests.length = 0;
    stream = "devel";
    topic = "topic-a";
    legacy_panel.set_refresh_state_for_testing({runs: [{id: 41}]});

    legacy_panel.refresh();
    assert.equal(requests.length, 1);
    topic = "topic-b";
    requests[0].success({armada_runs: [], armada_topic_context: null});
    requests[0].error({readyState: 4, status: 500, responseText: "stale"});

    assert.deepEqual(legacy_panel.refresh_state_for_testing(), {run_ids: [41], error: ""});
    requests[0].complete();
});

run_test("coalesced refresh issues one strongest follow-up", () => {
    requests.length = 0;
    stream = "devel";
    topic = "topic-a";
    legacy_panel.set_refresh_state_for_testing({runs: []});

    legacy_panel.refresh({live: true});
    legacy_panel.refresh({force_context: true});
    legacy_panel.refresh({live: true});
    assert.equal(requests.length, 1);

    requests[0].complete();
    assert.equal(requests.length, 2);
    assert.equal(requests[1].data.include_context, true);
    requests[1].complete();
    assert.equal(requests.length, 2);
});

run_test("unknown-run snapshot overlays newer reducer state", () => {
    legacy_run_state.clear_for_testing();
    legacy_run_state.apply_armada_run_event({
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

    const reconciled = legacy_panel.reconcile_run_snapshot_for_testing(
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
    legacy_run_state.clear_for_testing();
});
