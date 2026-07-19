"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let transport_records = [];
let viewport_resets = 0;
let reload_resets = 0;
let process_as_command = false;
mock_esm("../src/compose_send", {
    build_message_data(args) {
        return {
            type: "stream",
            sender_id: 7,
            queue_id: "queue",
            stream_id: args.recipient.stream_id,
            topic: args.recipient.topic,
            to: `[${args.recipient.stream_id}]`,
            content: args.content,
            draft_id: args.draft_id,
            ...(args.thread_root_message_id === undefined
                ? {}
                : {thread_root_message_id: args.thread_root_message_id}),
        };
    },
    send_message(args) {
        transport_records.push(args.message_data);
        args.on_success(
            {
                ...args.message_data,
                local_id: "local-1",
                locally_echoed: false,
                resend: false,
            },
            {id: 99},
        );
    },
    finalize_message_success() {},
});
mock_esm("../src/echo", {try_deliver_locally: () => undefined});
mock_esm("../src/message_events", {insert_new_messages() {}});
mock_esm("../src/message_viewport", {
    bottom_of_feed: {
        reset() {
            viewport_resets += 1;
        },
    },
});
mock_esm("../src/reload", {
    maybe_reset_pending_reload_timeout() {
        reload_resets += 1;
    },
});
mock_esm("../src/zcommand", {process: () => process_as_command});

const compose_controller = zrequire("compose_controller");

function exercise_adapter(thread_root_message_id) {
    const lifecycle = [];
    const controller = compose_controller.create({
        get_content() {
            lifecycle.push("content");
            return "same content";
        },
        get_destination() {
            lifecycle.push("destination");
            return {
                recipient: {type: "stream", stream_id: 12, topic: "same topic"},
                ...(thread_root_message_id === undefined ? {} : {thread_root_message_id}),
            };
        },
        validate() {
            lifecycle.push("validate");
            return true;
        },
        save_draft(is_sending_saving) {
            lifecycle.push(`draft:${is_sending_saving}`);
            return "draft-1";
        },
        is_busy() {
            lifecycle.push("is-busy");
            return false;
        },
        set_busy(busy) {
            lifecycle.push(`busy:${busy}`);
        },
        clear() {
            lifecycle.push("clear");
        },
        show_send_error() {
            lifecycle.push("error");
        },
        stop_typing() {
            lifecycle.push("stop-typing");
        },
        before_finish() {
            lifecycle.push("before-finish");
        },
        before_send() {
            lifecycle.push("before-send");
        },
        after_success() {
            lifecycle.push("after-success");
        },
    });
    assert.equal(controller.finish(), true);
    return lifecycle;
}

run_test("main and thread adapters share one lifecycle with an additive thread destination", () => {
    transport_records = [];
    viewport_resets = 0;
    reload_resets = 0;
    process_as_command = false;
    const main_lifecycle = exercise_adapter(undefined);
    const thread_lifecycle = exercise_adapter(901);
    assert.deepEqual(thread_lifecycle, main_lifecycle);
    assert.deepEqual(transport_records[1], {...transport_records[0], thread_root_message_id: 901});
    assert.equal(viewport_resets, 2);
    assert.equal(reload_resets, 2);
});

run_test("the shared controller handles commands before validation for every adapter", () => {
    process_as_command = true;
    let validated = false;
    let cleared = false;
    const controller = compose_controller.create({
        get_content: () => "/ping",
        get_destination: () => undefined,
        validate() {
            validated = true;
            return false;
        },
        save_draft: () => undefined,
        is_busy: () => false,
        set_busy() {},
        clear() {
            cleared = true;
        },
        show_send_error() {},
        stop_typing() {},
    });
    assert.equal(controller.finish(), undefined);
    assert.equal(validated, false);
    assert.equal(cleared, true);
});
