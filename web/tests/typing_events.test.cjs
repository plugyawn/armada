"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {make_bot, make_user} = require("./lib/example_user.cjs");
const {make_message_list} = require("./lib/message_list.cjs");
const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const settings_data = mock_esm("../src/settings_data");

const message_lists = zrequire("message_lists");
const people = zrequire("people");
const server_events_state = zrequire("server_events_state");
const {set_current_user, set_realm} = zrequire("state_data");
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

const pear = make_bot({
    email: "pear-bot@example.com",
    full_name: "Pear",
    user_id: 12,
});

people.add_active_user(anna);
people.add_active_user(vronsky);
people.add_active_user(levin);
people.add_active_user(kitty);
people.add_active_user(pear);

run_test("render_notifications_for_narrow", ({override, mock_template}) => {
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

run_test("render_notifications_for_narrow Pear typing", ({override, mock_template}) => {
    typing_data.clear_for_testing();
    override(current_user, "user_id", anna.user_id);
    override(settings_data, "user_can_access_all_other_users", () => true);
    server_events_state.initialize({
        queue_id: null,
        pear_bot_user_ids: [pear.user_id],
        assert_get_events_running() {},
        restart_get_events() {},
    });

    const group = [anna.user_id, pear.user_id];
    const conversation_key = typing_data.get_direct_message_conversation_key(group);
    message_lists.set_current(make_message_list([{operator: "dm", operand: group}]));
    typing_data.add_typist(conversation_key, pear.user_id);

    mock_template("typing_notifications.hbs", true, (args, rendered_html) => {
        assert.deepEqual(args.users, []);
        assert.equal(args.pear_working, true);
        assert.equal(args.several_users, false);
        return rendered_html;
    });

    const $typing_notifications = $("#typing_notifications");
    typing_events.render_notifications_for_narrow();
    assert.ok($typing_notifications.visible());
    assert.ok($typing_notifications.html().includes("Pear is working…"));
    assert.ok($typing_notifications.html().includes("pear-typing-notification"));
    assert.ok(!$typing_notifications.html().includes("Pear is typing…"));
});

run_test("render_notifications_for_narrow Pear plus people typing", ({override, mock_template}) => {
    typing_data.clear_for_testing();
    override(current_user, "user_id", anna.user_id);
    override(settings_data, "user_can_access_all_other_users", () => true);
    server_events_state.initialize({
        queue_id: null,
        pear_bot_user_ids: [pear.user_id],
        assert_get_events_running() {},
        restart_get_events() {},
    });

    const group = [anna.user_id, vronsky.user_id, levin.user_id, pear.user_id];
    const conversation_key = typing_data.get_direct_message_conversation_key(group);
    message_lists.set_current(make_message_list([{operator: "dm", operand: group}]));
    typing_data.add_typist(conversation_key, pear.user_id);
    typing_data.add_typist(conversation_key, vronsky.user_id);
    typing_data.add_typist(conversation_key, levin.user_id);

    mock_template("typing_notifications.hbs", false, (args) => {
        assert.deepEqual(args.users, [vronsky, levin]);
        assert.equal(args.pear_working, true);
        assert.equal(args.several_users, false);
        return "Pear plus people";
    });

    const $typing_notifications = $("#typing_notifications");
    typing_events.render_notifications_for_narrow();
    assert.ok($typing_notifications.visible());
    assert.equal($typing_notifications.html(), "Pear plus people");
});
