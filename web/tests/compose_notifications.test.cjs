"use strict";

const {strict: assert} = require("node:assert");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const message_lists = mock_esm("../src/message_lists.ts");
const narrow_state = mock_esm("../src/narrow_state.ts");
const onboarding_steps = mock_esm("../src/onboarding_steps.ts", {
    ONE_TIME_NOTICES_TO_DISPLAY: new Set(),
});

const compose_notifications = zrequire("compose_notifications");
const people = zrequire("people");
const {initialize_user_settings} = zrequire("user_settings");

const user_settings = {};
initialize_user_settings({user_settings});

const me = {
    email: "me@example.com",
    user_id: 30,
    full_name: "Me Myself",
};

people.add_active_user(me);
people.initialize_current_user(me.user_id);

function base_message() {
    return {
        id: 101,
        type: "stream",
        sender_id: me.user_id,
        stream_id: 5,
        topic: "thread-pane",
    };
}

run_test("notify_local_mixes skips thread replies entirely", () => {
    // Thread replies render in the thread side pane and are excluded from
    // the main feed; without the guard this jumps (reloads) the feed on
    // every pane reply send when web_navigate_to_sent_message is on.
    user_settings.web_navigate_to_sent_message = true;

    const message = {...base_message(), thread_root_message_id: 99};

    let narrowed_to;
    compose_notifications.notify_local_mixes([message], false, {
        narrow_to_recipient(message_id) {
            narrowed_to = message_id;
        },
    });
    assert.equal(narrowed_to, undefined);
});

run_test("notify_local_mixes skips thread reply banner when navigation disabled", () => {
    // With navigation off, the failure mode was the "outside your current
    // view" banner on every reply; the guard must skip that too. Any
    // banner rendering would throw here since templates are unstubbed.
    user_settings.web_navigate_to_sent_message = false;

    const message = {...base_message(), thread_root_message_id: 99};

    let narrowed_to;
    compose_notifications.notify_local_mixes([message], false, {
        narrow_to_recipient(message_id) {
            narrowed_to = message_id;
        },
    });
    assert.equal(narrowed_to, undefined);
});

run_test("notify_local_mixes still narrows for ordinary out-of-view messages", ({override}) => {
    user_settings.web_navigate_to_sent_message = true;

    override(message_lists, "current", {
        get_row: () => ({length: 0}),
    });
    override(narrow_state, "filter", () => undefined);
    assert.equal(onboarding_steps.ONE_TIME_NOTICES_TO_DISPLAY.size, 0);

    const message = base_message();

    let narrowed_to;
    compose_notifications.notify_local_mixes([message], false, {
        narrow_to_recipient(message_id) {
            narrowed_to = message_id;
        },
    });
    assert.equal(narrowed_to, message.id);
});
