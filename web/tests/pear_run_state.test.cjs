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

test("active result message self-expires without another panel refresh", () => {
    const changes = [];
    const unsubscribe = pear_run_state.on_active_run_result_messages_changed((change) =>
        changes.push(change),
    );
    try {
        pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 5}]);

        clock.tick(14_999);
        assert.ok(pear_run_state.is_active_run_result_message(5));

        clock.tick(1);
        assert.ok(!pear_run_state.is_active_run_result_message(5));
        assert.deepEqual(
            changes.map((change) => change.removed_message_ids),
            [[], [5]],
        );
    } finally {
        unsubscribe();
    }
});

test("active result message refresh prevents stale expiry", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 6}]);
    clock.tick(10_000);
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 6}]);

    clock.tick(5_000);
    assert.ok(pear_run_state.is_active_run_result_message(6));
});

test("expiry fires exactly TTL after the last refresh", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 7}]);
    clock.tick(10_000);
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 7}]);

    // The timer armed at t=0 fires at t=15s, sees fresh data, and must
    // re-arm for the remaining 10s — not another full TTL, which would
    // drift expiry to t=30s.
    clock.tick(14_999);
    assert.ok(pear_run_state.is_active_run_result_message(7));
    clock.tick(1);
    assert.ok(!pear_run_state.is_active_run_result_message(7));
    assert.equal(pear_run_state.active_result_message_expiry_delay_for_testing(Date.now()), 0);
});
