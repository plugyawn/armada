"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {make_user} = require("./lib/example_user.cjs");
const {make_message_list} = require("./lib/message_list.cjs");
const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const settings_data = mock_esm("../src/settings_data");

const message_lists = zrequire("message_lists");
const people = zrequire("people");
const {set_current_user, set_pear_bot_user_ids, set_realm} = zrequire("state_data");
const typing_data = zrequire("typing_data");
const typing_events = zrequire("typing_events");

const current_user = {};
set_current_user(current_user);
set_realm(make_realm());

const anna = make_user({
    email: "anna@example.com",
    full_name: "Anna Karenina",
    user_id: 8,
});

const vronsky = make_user({
    email: "vronsky@example.com",
    full_name: "Alexei Vronsky",
    user_id: 9,
});

const levin = make_user({
    email: "levin@example.com",
    full_name: "Konstantin Levin",
    user_id: 10,
});

const kitty = make_user({
    email: "kitty@example.com",
    full_name: "Kitty S",
    user_id: 11,
});

people.add_active_user(anna);
people.add_active_user(vronsky);
people.add_active_user(levin);
people.add_active_user(kitty);

run_test("render_notifications_for_narrow", ({override, mock_template}) => {
    set_pear_bot_user_ids([]);
    override(current_user, "user_id", anna.user_id);
    override(settings_data, "user_can_access_all_other_users", () => true);
    const group = [anna.user_id, vronsky.user_id, levin.user_id, kitty.user_id];
    const conversation_key = typing_data.get_direct_message_conversation_key(group);
    message_lists.set_current(make_message_list([{operator: "dm", operand: group}]));

    const $typing_notifications = $("#typing_notifications");

    mock_template("typing_notifications.hbs", true, (_args, rendered_html) => rendered_html);

    // Having only two(<MAX_USERS_TO_DISPLAY_NAME) typists, both of them
    // should be rendered but not 'Several people are typing…'
    typing_data.add_typist(conversation_key, anna.user_id);
    typing_data.add_typist(conversation_key, vronsky.user_id);
    typing_events.render_notifications_for_narrow();
    assert.ok($typing_notifications.visible());
    assert.ok($typing_notifications.html().includes(`${anna.full_name} is typing…`));
    assert.ok($typing_notifications.html().includes(`${vronsky.full_name} is typing…`));
    assert.ok(!$typing_notifications.html().includes("Several people are typing…"));

    // Having 3(=MAX_USERS_TO_DISPLAY_NAME) typists should also display only names
    typing_data.add_typist(conversation_key, levin.user_id);
    typing_events.render_notifications_for_narrow();
    assert.ok($typing_notifications.visible());
    assert.ok($typing_notifications.html().includes(`${anna.full_name} is typing…`));
    assert.ok($typing_notifications.html().includes(`${vronsky.full_name} is typing…`));
    assert.ok($typing_notifications.html().includes(`${levin.full_name} is typing…`));
    assert.ok(!$typing_notifications.html().includes("Several people are typing…"));

    // Having 4(>MAX_USERS_TO_DISPLAY_NAME) typists should display "Several people are typing…"
    typing_data.add_typist(conversation_key, kitty.user_id);
    typing_events.render_notifications_for_narrow();
    assert.ok($typing_notifications.visible());
    assert.ok($typing_notifications.html().includes("Several people are typing…"));
    assert.ok(!$typing_notifications.html().includes(`${anna.full_name} is typing…`));
    assert.ok(!$typing_notifications.html().includes(`${vronsky.full_name} is typing…`));
    assert.ok(!$typing_notifications.html().includes(`${levin.full_name} is typing…`));
    assert.ok(!$typing_notifications.html().includes(`${kitty.full_name} is typing…`));

    // #typing_notifications should be hidden when there are no typists.
    typing_data.remove_typist(conversation_key, anna.user_id);
    typing_data.remove_typist(conversation_key, vronsky.user_id);
    typing_data.remove_typist(conversation_key, levin.user_id);
    typing_data.remove_typist(conversation_key, kitty.user_id);
    typing_events.render_notifications_for_narrow();
    assert.ok(!$typing_notifications.visible());

    // #typing_notifications should be hidden for inaccessible users.
    override(settings_data, "user_can_access_all_other_users", () => false);
    const inaccessible_user = people.add_inaccessible_user(20);
    typing_data.add_typist(conversation_key, inaccessible_user.user_id);
    typing_data.add_typist(conversation_key, 21);
    typing_events.render_notifications_for_narrow();
    assert.ok(!$typing_notifications.visible());
});

run_test("display_notification tracks Pear typists without a working line", ({override}) => {
    override(current_user, "user_id", anna.user_id);
    const group = [anna.user_id, vronsky.user_id];
    message_lists.set_current(make_message_list([{operator: "dm", operand: group}]));

    const $typing_notifications = $("#typing_notifications");

    set_pear_bot_user_ids([vronsky.user_id]);
    typing_events.display_notification({
        id: 1,
        type: "typing",
        op: "start",
        message_type: "direct",
        sender: {
            email: vronsky.email,
            user_id: vronsky.user_id,
        },
        recipients: [
            {email: anna.email, user_id: anna.user_id},
            {email: vronsky.email, user_id: vronsky.user_id},
        ],
    });

    // Pear's typing signal is tracked (it drives run-state cleanup) but
    // deliberately renders nothing: the streaming transcript's animated
    // "Thinking ✦" line and the topic header already show the live state.
    assert.ok(!$typing_notifications.visible());
    set_pear_bot_user_ids([]);
    typing_data.clear_for_testing();
});

run_test(
    "render_notifications_for_narrow keeps Pear out of the people list",
    ({override, mock_template}) => {
        override(current_user, "user_id", anna.user_id);
        override(settings_data, "user_can_access_all_other_users", () => true);
        const group = [anna.user_id, vronsky.user_id];
        const conversation_key = typing_data.get_direct_message_conversation_key(group);
        message_lists.set_current(make_message_list([{operator: "dm", operand: group}]));

        const $typing_notifications = $("#typing_notifications");
        let rendered_context;
        mock_template("typing_notifications.hbs", false, (context) => {
            rendered_context = context;
            return "<ul>typing</ul>";
        });

        set_pear_bot_user_ids([vronsky.user_id]);
        typing_data.add_typist(conversation_key, vronsky.user_id);
        typing_data.add_typist(conversation_key, anna.user_id);
        typing_events.render_notifications_for_narrow();

        // Only the human typist renders; the Pear bot is filtered out.
        assert.ok($typing_notifications.visible());
        assert.equal(rendered_context.users.length, 1);
        assert.equal(rendered_context.users[0].user_id, anna.user_id);
        set_pear_bot_user_ids([]);
        typing_data.clear_for_testing();
    },
);
