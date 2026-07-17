"use strict";

const assert = require("node:assert/strict");

const {clock, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pear_run_state = zrequire("pear_run_state");

function test(label, f) {
    run_test(label, () => {
        pear_run_state.clear_for_testing();
        clock.reset();
        try {
            f();
        } finally {
            pear_run_state.clear_for_testing();
        }
    });
}

test("set_active_run_result_messages tracks only active result messages", () => {
    pear_run_state.set_active_run_result_messages([
        {status: "queued", result_message_id: 1},
        {status: "running", result_message_id: 2},
        {status: "done", result_message_id: 3},
        {status: "running", result_message_id: null},
    ]);

    assert.ok(pear_run_state.is_active_run_result_message(1));
    assert.ok(pear_run_state.is_active_run_result_message(2));
    assert.ok(!pear_run_state.is_active_run_result_message(3));
});

test("ambient run ids are retained by result message", () => {
    pear_run_state.set_active_run_result_messages([
        {id: 41, status: "done", ambient_triggered: true, result_message_id: 301},
        {id: 42, status: "done", ambient_triggered: false, result_message_id: 302},
    ]);

    assert.equal(pear_run_state.get_ambient_run_id(301), 41);
    assert.equal(pear_run_state.get_ambient_run_id(302), undefined);

    pear_run_state.set_active_run_result_messages([
        {id: 41, status: "done", ambient_triggered: false, result_message_id: 301},
    ]);
    assert.equal(pear_run_state.get_ambient_run_id(301), undefined);
});

test("done run clears active result message immediately", () => {
    const changes = [];
    const unsubscribe = pear_run_state.on_active_run_result_messages_changed((change) =>
        changes.push(change),
    );
    try {
        pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 4}]);
        assert.ok(pear_run_state.is_active_run_result_message(4));

        pear_run_state.set_active_run_result_messages([{status: "done", result_message_id: 4}]);
        assert.ok(!pear_run_state.is_active_run_result_message(4));
        assert.deepEqual(
            changes.map((change) => change.removed_message_ids),
            [[], [4]],
        );
    } finally {
        unsubscribe();
    }
});

test("quiet active result message remains active until a terminal event", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 6}]);
    clock.tick(60_000);
    assert.ok(pear_run_state.is_active_run_result_message(6));
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 10,
        run_id: 2,
        event_type: "done",
        status: "done",
        result_message_id: 6,
        stream: "devel",
        topic: "speed",
        current_activity: "Completed",
        tool_count: 3,
        plan: null,
        overview: null,
        has_visible_assistant_text: null,
        usage: {total_tokens: 1200},
        duration_seconds: 12.5,
        trace_available: true,
        run_date_created: 100,
        date_created: 110,
    });
    assert.ok(!pear_run_state.is_active_run_result_message(6));
    assert.deepEqual(pear_run_state.get_run_footer(6), {
        status: "done",
        duration_seconds: 12.5,
        total_tokens: 1200,
        tool_count: 3,
    });
});

test("stale hydration cannot overwrite a newer push event", () => {
    pear_run_state.remember_runs([
        {
            id: 9,
            status: "running",
            ambient_triggered: false,
            result_message_id: 90,
            stream: "devel",
            topic: "speed",
            date_created: 90,
            date_updated: 100,
        },
    ]);
    pear_run_state.set_active_run_result_messages([
        {
            id: 9,
            status: "running",
            ambient_triggered: false,
            result_message_id: 90,
            date_updated: 100,
        },
    ]);
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 20,
        run_id: 9,
        event_type: "done",
        status: "done",
        result_message_id: 90,
        stream: "devel",
        topic: "speed",
        current_activity: "Completed",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: null,
        usage: {total_tokens: 100},
        duration_seconds: 2,
        trace_available: true,
        run_date_created: 90,
        date_created: 110,
    });

    const stale_run = {
        id: 9,
        status: "running",
        ambient_triggered: false,
        result_message_id: 90,
        stream: "devel",
        topic: "speed",
        date_created: 90,
        date_updated: 105,
    };
    pear_run_state.remember_runs([stale_run]);
    pear_run_state.set_active_run_result_messages([stale_run]);

    assert.equal(pear_run_state.get_run_view(90).status, "done");
    assert.ok(!pear_run_state.is_active_run_result_message(90));
});

test("typed assistant-visible push retires the placeholder immediately", () => {
    pear_run_state.remember_runs([
        {
            id: 12,
            status: "running",
            ambient_triggered: false,
            result_message_id: 120,
            date_updated: 100,
            live: {has_visible_assistant_text: false, updated_at: 1},
        },
    ]);
    assert.equal(pear_run_state.get_run_view(120).has_visible_assistant_text, false);

    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 20,
        run_id: 12,
        event_type: "assistant_visible",
        status: "running",
        result_message_id: 120,
        stream: "devel",
        topic: "speed",
        current_activity: "Assistant response started",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 90,
        date_created: 200,
    });
    assert.equal(pear_run_state.get_run_view(120).has_visible_assistant_text, true);

    pear_run_state.remember_runs([
        {
            id: 12,
            status: "running",
            ambient_triggered: false,
            result_message_id: 120,
            date_updated: 150,
            live: {has_visible_assistant_text: false, updated_at: 199},
        },
    ]);
    assert.equal(pear_run_state.get_run_view(120).has_visible_assistant_text, true);
    assert.equal(pear_run_state.get_run_view(120).updated_at, 200);
});

test("plan updates clear overview copy from the previous step", () => {
    pear_run_state.remember_runs([
        {
            id: 13,
            status: "running",
            ambient_triggered: false,
            result_message_id: 130,
            plan: [{step: "Inspect", status: "in_progress"}],
            overview: "Reading four files",
            date_updated: 100,
        },
    ]);
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 21,
        run_id: 13,
        event_type: "transcript",
        status: "running",
        result_message_id: 130,
        stream: "devel",
        topic: "speed",
        current_activity: "Plan updated",
        tool_count: 1,
        plan: [{step: "Summarize", status: "in_progress"}],
        overview: null,
        has_visible_assistant_text: null,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 90,
        date_created: 201,
    });

    assert.equal(pear_run_state.get_run_view(130).overview, null);
});
