"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

mock_esm("../src/sent_messages", {
    get_new_local_id() {
        return "local-1";
    },
});
mock_esm("../src/server_events_state", {queue_id: "queue-1"});
mock_esm("../src/state_data", {current_user: {user_id: 42}});

let sent;
let on_success;
let on_error;
mock_esm("../src/transmit", {
    send_message(request, success, error) {
        sent = request;
        on_success = success;
        on_error = error;
    },
});

const actions = zrequire("legacy_artifact_actions");

run_test("sends a visible topic message through the normal transmit path", () => {
    let result = "";
    actions.send_armada_artifact_action(
        {stream: "devel", topic: "attention"},
        "Explain the highlighted edge",
        {
            success() {
                result = "success";
            },
            error(message) {
                result = message;
            },
        },
    );
    assert.deepEqual(sent, {
        type: "stream",
        local_id: "local-1",
        sender_id: 42,
        queue_id: "queue-1",
        to: "devel",
        topic: "attention",
        content: "@**Pear** Explain the highlighted edge",
    });
    on_success();
    assert.equal(result, "success");
    on_error("failed", "BAD_REQUEST");
    assert.equal(result, "failed");
});

run_test("rejects an empty action without sending", () => {
    sent = undefined;
    let error = "";
    actions.send_armada_artifact_action({stream: "devel", topic: "attention"}, "   ", {
        success() {},
        error: (message) => (error = message),
    });
    assert.equal(sent, undefined);
    assert.equal(error, "empty action");
});
