"use strict";

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");
const {page_params} = require("./lib/zpage_params.cjs");

mock_esm("../src/loading", {
    destroy_indicator: noop,
});
set_global("addEventListener", noop);

const channel = mock_esm("../src/channel");
mock_esm("../src/reload_state", {
    is_in_progress() {
        return false;
    },
});
page_params.test_suite = false;

// we also directly write to pointer
set_global("pointer", {});

const echo = mock_esm("../src/echo", {
    process_from_server(messages) {
        return messages;
    },
});

mock_esm("../src/popup_banners", {
    close_connection_error_popup_banner() {},
});

mock_esm("../src/stream_events", {
    update_property() {
        throw new Error("subs update error");
    },
});

mock_esm("../src/sent_messages", {
    report_event_received() {},
    messages: new Map(),
});

const message_events = mock_esm("../src/message_events", {
    insert_new_messages() {
        throw new Error("insert error");
    },
    update_messages() {
        throw new Error("update error");
    },
});
const message_threads = mock_esm("../src/message_threads");

const message_store = zrequire("message_store");
const server_events = zrequire("server_events");

const message = {
    id: 1,
    sender_id: 2,
    content: "hello",
    recipient_id: 3,
    timestamp: 100000000,
    client: "website",
    subject: "server_test",
    topic_links: [],
    is_me_message: false,
    reactions: [
        {
            emoji_name: "foo",
            emoji_code: "bar",
            reaction_type: "unicode_emoji",
            user: {
                email: "user1@foo.com",
                id: 1,
                full_name: "aaron",
            },
            user_id: 1,
        },
    ],
    submessages: [],
    sender_full_name: "user1",
    sender_email: "user2@foo.com",
    display_recipient: "test",
    type: "stream",
    stream_id: 1,
    avatar_url: "bar",
    content_type: "text/html",
};

server_events.finished_initial_fetch();

run_test("message_event", ({override}) => {
    const event = {
        type: "message",
        message,
        flags: [],
    };

    let inserted;
    let inserted_messages;
    let thread_messages;
    override(message_events, "insert_new_messages", (message_data) => {
        const messages = message_data.raw_messages;
        assert.equal(messages[0].content, event.message.content);
        inserted = true;
        inserted_messages = messages;
        return messages;
    });
    override(message_threads, "handle_added_messages", (messages) => {
        thread_messages = messages;
    });

    server_events._get_events_success([event]);
    assert.ok(inserted);
    assert.equal(thread_messages, inserted_messages);
});

run_test("locally echoed message updates thread lifecycle", ({override}) => {
    const event = {
        type: "message",
        message: {...message, id: 2, thread_root_message_id: 1},
        flags: [],
        local_message_id: "local-thread-reply",
    };
    const reified_message = {
        id: 2,
        thread_root_message_id: 1,
    };

    override(echo, "process_from_server", (messages) => {
        assert.equal(messages[0].local_id, event.local_message_id);
        return [];
    });
    message_store.clear_for_testing();
    message_store.update_message_cache({message: reified_message});
    let thread_messages;
    override(message_threads, "handle_added_messages", (messages) => {
        thread_messages = messages;
    });

    server_events._get_events_success([event]);
    assert.deepEqual(thread_messages, [reified_message]);
    message_store.clear_for_testing();
});

run_test("thread summary events are dispatched after new messages", ({override}) => {
    const summary_event = {
        id: 2,
        type: "message_thread_summary",
        root_message_id: 1,
        summary: {
            reply_count: 1,
            last_reply_at: 100000001,
            last_sender: null,
            participants: [],
        },
    };
    const call_order = [];
    override(message_events, "insert_new_messages", (message_data) => {
        call_order.push("insert_new_messages");
        return message_data.raw_messages;
    });
    override(message_threads, "handle_added_messages", () => {
        call_order.push("handle_added_messages");
    });
    override(message_threads, "handle_summary_event", (event) => {
        call_order.push("handle_summary_event");
        assert.equal(event.root_message_id, summary_event.root_message_id);
    });

    server_events._get_events_success([
        {type: "message", id: 1, message, flags: []},
        summary_event,
    ]);
    assert.deepEqual(call_order, [
        "insert_new_messages",
        "handle_added_messages",
        "handle_summary_event",
    ]);
});

run_test("replayed message and summary batches stay ordered across reconnects", ({override}) => {
    const replayed_batch = [
        {
            type: "message",
            id: 3,
            message: {...message, id: 3, thread_root_message_id: 1},
            flags: [],
        },
        {
            id: 4,
            type: "message_thread_summary",
            root_message_id: 1,
            summary: {
                reply_count: 2,
                last_reply_at: 100000002,
                last_sender: null,
                participants: [],
            },
        },
    ];
    const call_order = [];
    override(message_events, "insert_new_messages", (message_data) => {
        call_order.push("insert_new_messages");
        return message_data.raw_messages;
    });
    override(message_threads, "handle_added_messages", () => {
        call_order.push("handle_added_messages");
    });
    override(message_threads, "handle_summary_event", (event) => {
        call_order.push("handle_summary_event");
        assert.equal(event.root_message_id, 1);
    });

    // A reconnect on the same queue can replay events the client has not yet
    // acknowledged. Each redelivered batch must still insert the reply before
    // applying the summary that describes it.
    channel.get = (options) => {
        options.success({events: replayed_batch});
    };
    server_events.restart_get_events();
    assert.deepEqual(call_order, [
        "insert_new_messages",
        "handle_added_messages",
        "handle_summary_event",
    ]);

    call_order.length = 0;
    server_events.restart_get_events();
    assert.deepEqual(call_order, [
        "insert_new_messages",
        "handle_added_messages",
        "handle_summary_event",
    ]);
    channel.get = () => {};
});

// Start blueslip tests here

const setup = () => {
    server_events.finished_initial_fetch();
};

run_test("event_dispatch_error", () => {
    setup();

    const data = {events: [{type: "stream", op: "update", id: 1, other: "thing"}]};
    channel.get = (options) => {
        options.success(data);
    };

    blueslip.expect("error", "Failed to process an event");

    server_events.restart_get_events();

    const logs = blueslip.get_test_logs("error");
    assert.equal(logs.length, 1);
    assert.equal(logs[0].more_info.event.type, "stream");
    assert.equal(logs[0].more_info.event.op, "update");
    assert.equal(logs[0].more_info.event.id, 1);
    assert.equal(logs[0].more_info.other, undefined);
});

run_test("event_new_message_error", () => {
    setup();

    const data = {events: [{type: "message", id: 1, other: "thing", message}]};
    channel.get = (options) => {
        options.success(data);
    };

    blueslip.expect("error", "Failed to insert new messages");

    server_events.restart_get_events();

    const logs = blueslip.get_test_logs("error");
    assert.equal(logs.length, 1);
    assert.equal(logs[0].more_info, undefined);
});

run_test("event_edit_message_error", () => {
    setup();
    const data = {events: [{type: "update_message", id: 1, other: "thing"}]};
    channel.get = (options) => {
        options.success(data);
    };
    blueslip.expect("error", "Failed to update messages");

    server_events.restart_get_events();

    const logs = blueslip.get_test_logs("error");
    assert.equal(logs.length, 1);
    assert.equal(logs[0].more_info, undefined);
});
