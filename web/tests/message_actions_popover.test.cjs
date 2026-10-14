"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

let popover_config;
const compose_reply = mock_esm("../src/compose_reply", {
    get_highlighted_message_ids: () => undefined,
    quote_messages: noop,
});
const message_edit = mock_esm("../src/message_edit", {
    currently_editing_messages: new Map(),
    start: noop,
});
const message_thread_hooks = mock_esm("../src/message_thread_hooks", {
    get_compose_target: /* istanbul ignore next */ () => undefined,
});
const message_lists = mock_esm("../src/message_lists");
mock_esm("../src/ui_util", {parse_html: (html) => html});
let context_message_list;
mock_esm("../src/popover_menus_data", {
    get_actions_popover_content_context(_message_id, message_list) {
        context_message_list = message_list;
        return {
            message_id: 41,
            stream_id: undefined,
            editability_menu_item: undefined,
            move_message_menu_item: undefined,
            view_source_menu_item: undefined,
            should_display_mark_as_unread: false,
            should_display_remind_me_option: false,
            should_display_collapse: false,
            should_display_uncollapse: false,
            should_display_quote_message: false,
            conversation_time_url: "#near/41",
            should_display_delete_option: false,
            should_display_read_receipts_option: false,
            should_display_add_reaction_option: false,
            should_display_message_report_option: false,
            not_for_pear_run_id: undefined,
        };
    },
});
let mark_unread_options;
mock_esm("../src/unread_ops", {
    mark_as_unread_from_here(message_id, message_list) {
        mark_unread_options = {message_id, message_list};
    },
});
mock_esm("../src/popover_menus", {
    popover_instances: {},
    register_popover_menu(_selector, config) {
        popover_config = config;
    },
    on_show_prep: noop,
    hide_current_popover_if_visible: noop,
});
const message_actions_popover = zrequire("message_actions_popover");

run_test(
    "secondary action popover keeps quote and edit in the owning thread list",
    ({override}) => {
        const $row = $.create("secondary-action-row");
        $row.attr("data-message-id", "41");
        $row.set_closest_results(".overlay-message-row", []);
        const $owner_row = $.create("secondary-owner-row");
        const thread_compose_target = {
            textarea: {},
            notify_content_changed: noop,
        };
        const owner = {
            view: {manages_primary_viewport: false},
            get_row(message_id) {
                assert.equal(message_id, 41);
                return $owner_row;
            },
        };
        message_lists.current = {
            view: {manages_primary_viewport: true},
            /* istanbul ignore next */
            get_row() {
                throw new Error("secondary action must not use message_lists.current");
            },
        };
        override(message_lists, "get_message_list_for_row", (candidate) => {
            assert.equal(candidate.get(0), $row.get(0));
            return owner;
        });
        override(message_thread_hooks, "get_compose_target", (message_id) => {
            assert.equal(message_id, 41);
            return thread_compose_target;
        });
        let quote_options;
        override(compose_reply, "quote_messages", (options) => {
            quote_options = options;
        });
        let edited_row;
        override(message_edit, "start", (candidate) => {
            edited_row = candidate;
        });

        message_actions_popover.initialize({message_reminder_click_handler: noop});
        const handlers = new Map();
        const $popper = $.create("secondary-actions-popper");
        $popper.one = (event_type, selector, handler) => {
            assert.equal(event_type, "click");
            handlers.set(selector, handler);
            return $popper;
        };
        $popper.on = () => $popper;
        const $reference = $.create("secondary-actions-reference");
        $reference.set_closest_results(".message_row", $row);
        const instance = {
            reference: {to_$: () => $reference},
            popper: {to_$: () => $popper},
            setContent: noop,
        };
        popover_config.onShow(instance);
        assert.equal(context_message_list, owner);
        popover_config.onMount(instance);

        const event = {preventDefault: noop, stopPropagation: noop};
        handlers.get(".respond_button")(event);
        assert.equal(quote_options.message_id, 41);
        assert.equal(quote_options.thread_compose_target, thread_compose_target);

        const $edit_action = $.create("secondary-edit-action");
        $edit_action.attr("data-message-id", "41");
        handlers.get(".popover_edit_message, .popover_view_source")({
            ...event,
            currentTarget: $edit_action.get(0),
        });
        assert.equal(edited_row.get(0), $owner_row.get(0));

        const $mark_unread_action = $.create("secondary-mark-unread-action");
        $mark_unread_action.attr("data-message-id", "41");
        handlers.get(".mark_as_unread")({
            ...event,
            currentTarget: $mark_unread_action.get(0),
        });
        assert.deepEqual(mark_unread_options, {message_id: 41, message_list: owner});
    },
);
