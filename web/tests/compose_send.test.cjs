"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let reified_message;
mock_esm("../src/echo", {
    reify_message_id(local_id, message_id) {
        reified_message = {local_id, message_id};
    },
    display_slow_send_loading_spinner() {},
});
let deleted_draft_ids;
mock_esm("../src/drafts", {
    draft_model: {
        deleteDrafts(draft_ids) {
            deleted_draft_ids = draft_ids;
        },
    },
});

let transmitted;
let transmit_success;
let transmit_error;
let transmit_count = 0;
mock_esm("../src/transmit", {
    send_message(request, on_success, on_error) {
        transmit_count += 1;
        transmitted = request;
        transmit_success = on_success;
        transmit_error = on_error;
    },
});
let local_id_count = 0;
mock_esm("../src/sent_messages", {
    get_new_local_id() {
        local_id_count += 1;
        return `loc-${local_id_count}`;
    },
});
let event_loop_assertion;
let event_loop_assertion_count = 0;
mock_esm("../src/server_events_state", {
    queue_id: "queue-4",
    assert_get_events_running(message) {
        event_loop_assertion_count += 1;
        event_loop_assertion = message;
    },
});
const {set_current_user, set_realm} = zrequire("state_data");
set_current_user({user_id: 42});
set_realm(make_realm({realm_topics_policy: "allow_empty_topic"}));
const compose_send = zrequire("compose_send");

function reset() {
    transmitted = undefined;
    transmit_success = undefined;
    transmit_error = undefined;
    event_loop_assertion = undefined;
    transmit_count = 0;
    local_id_count = 0;
    event_loop_assertion_count = 0;
    reified_message = undefined;
    deleted_draft_ids = undefined;
}

run_test("build_message_data resolves a threaded stream recipient", () => {
    const request = compose_send.build_message_data({
        recipient: {type: "stream", stream_id: 17, topic: "design"},
        content: "A canonical reply",
        draft_id: "draft-2",
        agent_followup: true,
        thread_root_message_id: 901,
    });
    assert.deepEqual(request, {
        type: "stream",
        sender_id: 42,
        queue_id: "queue-4",
        stream_id: 17,
        topic: "design",
        to: "[17]",
        content: "A canonical reply",
        draft_id: "draft-2",
        agent_followup: "true",
        thread_root_message_id: 901,
    });
});

run_test("build_message_data resolves and sorts direct-message recipients", () => {
    const request = compose_send.build_message_data({
        recipient: {
            type: "private",
            user_ids: [42, 7],
            recipient_emails: "me@example.com, friend@example.com",
        },
        content: "Private reply",
    });
    assert.deepEqual(request, {
        type: "private",
        sender_id: 42,
        queue_id: "queue-4",
        stream_id: undefined,
        topic: "",
        to: "[42,7]",
        reply_to: "me@example.com, friend@example.com",
        private_message_recipient: "me@example.com, friend@example.com",
        to_user_ids: "7,42",
        content: "Private reply",
    });
});

run_test("send_message owns the shared transmit lifecycle", () => {
    reset();
    const message_data = compose_send.build_message_data({
        recipient: {type: "stream", stream_id: 17, topic: "design"},
        content: "Shared lifecycle",
        thread_root_message_id: 901,
    });
    let successful_send;
    let response;
    const sent = compose_send.send_message({
        message_data,
        on_success(sent_message, data) {
            successful_send = sent_message;
            response = data;
        },
        on_error() {
            throw new Error("unexpected send error");
        },
    });
    assert.equal(transmit_count, 1);
    assert.deepEqual(transmitted, sent);
    assert.equal(sent.local_id, "loc-1");
    assert.equal(sent.locally_echoed, false);
    assert.equal(sent.thread_root_message_id, 901);
    assert.equal(
        event_loop_assertion,
        "Restarting get_events because it was not running during send",
    );

    transmit_success({id: 902});
    assert.deepEqual(successful_send, sent);
    assert.deepEqual(response, {id: 902});

    let error_result;
    compose_send.send_message({
        message_data,
        on_success() {
            throw new Error("unexpected send success");
        },
        on_error(message, code, sent_message, local_message) {
            error_result = {message, code, sent_message, local_message};
        },
    });
    transmit_error("Network down", "BAD_REQUEST");
    assert.deepEqual(error_result, {
        message: "Network down",
        code: "BAD_REQUEST",
        sent_message: transmitted,
        local_message: undefined,
    });
    assert.equal(local_id_count, 2);
    assert.equal(event_loop_assertion_count, 2);
});

run_test("successful sends share local-echo reification and draft cleanup", () => {
    reset();
    compose_send.finalize_message_success(
        {local_id: "loc-thread", draft_id: "draft-thread"},
        {id: 903},
    );
    assert.deepEqual(reified_message, {local_id: "loc-thread", message_id: 903});
    assert.deepEqual(deleted_draft_ids, ["draft-thread"]);
});
