"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");

const get_requests = [];
mock_esm("../src/channel", {
    get(options) {
        get_requests.push(options);
    },
});
mock_esm("../src/retry_backoff", {get_retry_backoff_seconds: () => 5});
mock_esm("../src/message_util", {do_unread_count_updates() {}});
mock_esm("../src/direct_message_group_data", {process_loaded_messages() {}});
mock_esm("../src/stream_list", {update_streams_sidebar_for_messages() {}});
mock_esm("../src/unread_ops", {process_visible() {}});
let closed_connection_banners = 0;
let opened_connection_banners = 0;
mock_esm("../src/popup_banners", {
    close_connection_error_popup_banner() {
        closed_connection_banners += 1;
    },
    open_connection_error_popup_banner() {
        opened_connection_banners += 1;
    },
});

const {Filter} = zrequire("filter");
const {MessageListData} = zrequire("message_list_data");
const message_fetch = zrequire("message_fetch");
const message_lists = zrequire("message_lists");
const message_store = zrequire("message_store");
const people = zrequire("people");
const {set_current_user, set_realm} = zrequire("state_data");
const {initialize_user_settings} = zrequire("user_settings");

initialize_user_settings({user_settings: {}});
const me = {user_id: 30, email: "me@example.com", full_name: "Me Myself", is_guest: false};
people.add_active_user(me);
people.initialize_current_user(me.user_id);
set_current_user({user_id: me.user_id, is_guest: false});
set_realm({});

function raw_stream_message(id, thread_root_message_id) {
    return {
        avatar_url: null,
        client: "test suite",
        content: "<p>thread reply</p>",
        content_type: "text/html",
        display_recipient: "devel",
        id,
        is_me_message: false,
        reactions: [],
        sender_email: "ada@example.com",
        sender_full_name: "Ada Lovelace",
        sender_id: 7,
        submessages: [],
        timestamp: 100,
        flags: ["read"],
        type: "stream",
        stream_id: 9,
        subject: "native threads",
        topic_links: [],
        thread_root_message_id,
    };
}

run_test("get_parameters_for_message_fetch_api date anchor", () => {
    const msg_list_data = new MessageListData({
        excludes_muted_topics: false,
        filter: new Filter([]),
    });

    const anchor_date = new Date("2024-01-02T00:00:00.000Z").toISOString();
    const params = message_fetch.get_parameters_for_message_fetch_api({
        anchor: "date",
        anchor_date,
        num_before: 0,
        num_after: 0,
        cont() {},
        msg_list_data,
    });
    assert.equal(params.anchor, "date");
    assert.equal(params.anchor_date, anchor_date);
    assert.equal(params.exclude_thread_replies, true);

    blueslip.expect("error", "Missing anchor_date for date anchor fetch");
    const missing_date = message_fetch.get_parameters_for_message_fetch_api({
        anchor: "date",
        num_before: 0,
        num_after: 0,
        cont() {},
        msg_list_data,
    });
    assert.equal(missing_date.anchor_date, undefined);
});

run_test("secondary message lists own independent retry timers", () => {
    const original_set_timeout = globalThis.setTimeout;
    const original_clear_timeout = globalThis.clearTimeout;
    const timers = new Map();
    const cleared = [];
    let next_timer = 1;
    globalThis.setTimeout = (callback) => {
        const timer = next_timer;
        next_timer += 1;
        timers.set(timer, callback);
        return timer;
    };
    globalThis.clearTimeout = (timer) => {
        cleared.push(timer);
        timers.delete(timer);
    };
    try {
        get_requests.length = 0;
        const first = new MessageListData({
            excludes_muted_topics: false,
            filter: new Filter([{operator: "id", operand: "1"}]),
        });
        const second = new MessageListData({
            excludes_muted_topics: false,
            filter: new Filter([{operator: "thread", operand: 1}]),
        });
        const base_options = {anchor: "newest", num_before: 0, num_after: 0, cont() {}};
        message_fetch.load_messages({...base_options, msg_list_data: first});
        message_fetch.load_messages({...base_options, msg_list_data: second});
        get_requests[0].error({status: 500});
        get_requests[1].error({status: 500});
        assert.deepEqual([...timers.keys()], [1, 2]);

        message_fetch.cancel_retries_for_message_list_data(first);
        assert.deepEqual(cleared, [1]);
        assert.deepEqual([...timers.keys()], [2]);
    } finally {
        globalThis.setTimeout = original_set_timeout;
        globalThis.clearTimeout = original_clear_timeout;
    }
});

run_test("one-shot secondary fetch errors terminate without global banners or retry timers", () => {
    const original_set_timeout = globalThis.setTimeout;
    let timer_count = 0;
    /* istanbul ignore next */
    globalThis.setTimeout = () => {
        timer_count += 1;
        return timer_count;
    };
    try {
        get_requests.length = 0;
        closed_connection_banners = 0;
        opened_connection_banners = 0;
        const errors = [];
        const msg_list_data = new MessageListData({
            excludes_muted_topics: false,
            filter: new Filter([{operator: "id", operand: "9"}]),
        });
        message_fetch.load_messages({
            anchor: 9,
            num_before: 0,
            num_after: 0,
            cont() {},
            msg_list_data,
            suppress_connection_error: true,
            on_error(xhr) {
                errors.push(xhr.status);
            },
        });
        get_requests[0].error({status: 400});
        assert.deepEqual(errors, [400]);
        assert.equal(timer_count, 0);
        assert.equal(closed_connection_banners, 0);
        assert.equal(opened_connection_banners, 0);
    } finally {
        globalThis.setTimeout = original_set_timeout;
    }
});

run_test("exact message hydration deduplicates one message_ids request", () => {
    get_requests.length = 0;
    const errors = [];
    message_fetch.load_messages_by_ids({
        message_ids: [31, 32, 31],
        cont() {},
        suppress_connection_error: true,
        on_error(xhr) {
            errors.push(xhr.status);
        },
    });
    assert.equal(get_requests.length, 1);
    assert.equal(get_requests[0].url, "/json/messages");
    assert.deepEqual(get_requests[0].data, {
        message_ids: "[31,32]",
        client_gravatar: true,
        exclude_thread_replies: true,
        allow_empty_topic_name: true,
    });
    get_requests[0].error({status: 400});
    assert.deepEqual(errors, [400]);
});

run_test("result batches hydrate all missing thread roots once before rendering", () => {
    message_store.clear_for_testing();
    get_requests.length = 0;
    let completed = false;
    const msg_list_data = new MessageListData({
        excludes_muted_topics: false,
        filter: new Filter([{operator: "is", operand: "mentioned"}]),
    });
    message_fetch.load_messages({
        anchor: "newest",
        num_before: 0,
        num_after: 0,
        msg_list_data,
        /* istanbul ignore next */
        cont() {
            completed = true;
        },
    });
    get_requests[0].success({
        anchor: 41,
        found_newest: false,
        found_oldest: false,
        found_anchor: false,
        history_limited: false,
        messages: [raw_stream_message(41, 31), raw_stream_message(42, 32)],
        result: "success",
        msg: "",
    });
    assert.equal(completed, false);
    assert.equal(get_requests.length, 2);
    assert.equal(get_requests[1].data.message_ids, "[31,32]");
});

run_test("registered secondary lists keep their fetch render path", () => {
    // Live secondary lists (the thread pane's reply list) are registered in
    // message_lists.rendered_message_lists; their fetch results must reach
    // add_messages, not be demoted to a data-only cache update like stale
    // narrows are.
    message_store.clear_for_testing();
    get_requests.length = 0;

    function secondary_list(id) {
        const data = new MessageListData({
            excludes_muted_topics: false,
            filter: new Filter([{operator: "thread", operand: 1149}]),
        });
        const rendered_ids = [];
        const msg_list = {
            id,
            data,
            should_preserve_current_rendered_state: () => false,
            add_messages(messages) {
                rendered_ids.push(...messages.map((message) => message.id));
            },
        };
        return {msg_list, data, rendered_ids};
    }

    function success_payload(messages) {
        return {
            anchor: 1151,
            found_newest: true,
            found_oldest: true,
            found_anchor: true,
            history_limited: false,
            messages,
            result: "success",
            msg: "",
        };
    }

    const live = secondary_list(77);
    message_lists.add_rendered_message_list(live.msg_list);
    message_fetch.load_messages({
        anchor: "newest",
        num_before: 60,
        num_after: 150,
        msg_list: live.msg_list,
        msg_list_data: live.data,
        cont() {},
    });
    get_requests[0].success(
        success_payload([raw_stream_message(1150, 1149), raw_stream_message(1151, 1149)]),
    );
    // The uncached root is hydrated by exact ID before rendering.
    assert.equal(get_requests.length, 2);
    assert.equal(get_requests[1].data.message_ids, "[1149]");
    get_requests[1].success(success_payload([raw_stream_message(1149)]));
    assert.deepEqual(live.rendered_ids, [1150, 1151]);
    message_lists.rendered_message_lists.delete(live.msg_list.id);

    // Control: an unregistered stale list is demoted to a data-only update.
    const stale = secondary_list(78);
    message_fetch.load_messages({
        anchor: "newest",
        num_before: 60,
        num_after: 150,
        msg_list: stale.msg_list,
        msg_list_data: stale.data,
        cont() {},
    });
    assert.equal(get_requests.length, 3);
    get_requests[2].success(
        success_payload([raw_stream_message(1150, 1149), raw_stream_message(1151, 1149)]),
    );
    assert.deepEqual(stale.rendered_ids, []);
    assert.equal(stale.data.num_items(), 2);
});
