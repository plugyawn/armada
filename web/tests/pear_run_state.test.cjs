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

test("done run clears active result message immediately", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 4}]);
    assert.ok(pear_run_state.is_active_run_result_message(4));

    pear_run_state.set_active_run_result_messages([{status: "done", result_message_id: 4}]);
    assert.ok(!pear_run_state.is_active_run_result_message(4));
});

test("active result message self-expires without another panel refresh", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 5}]);

    clock.tick(14_999);
    assert.ok(pear_run_state.is_active_run_result_message(5));

    clock.tick(1);
    assert.ok(!pear_run_state.is_active_run_result_message(5));
});

test("active result message refresh prevents stale expiry", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 6}]);
    clock.tick(10_000);
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 6}]);

    clock.tick(5_000);
    assert.ok(pear_run_state.is_active_run_result_message(6));
});
