"use strict";

const assert = require("node:assert/strict");

const {mock_cjs, mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

mock_cjs(
    "clipboard",
    class Clipboard {
        on() {
            return this;
        }
    },
);
mock_esm("../src/mouse_drag", {is_drag: () => false});
mock_esm("../src/hash_util", {by_sender_url: () => "#sender"});
mock_esm("../src/people", {
    get_by_user_id(user_id) {
        return {user_id, is_inaccessible_user: true};
    },
    small_avatar_url_for_person: () => "/avatar.png",
});
const message_lists = mock_esm("../src/message_lists");
let popover_reference;
mock_esm("../src/popover_menus", {
    popover_items_handle_keyboard: noop,
    toggle_popover_menu(reference) {
        popover_reference = reference;
    },
});
const user_card_popover = zrequire("user_card_popover");

run_test("secondary sender popover resolves its owning canonical message list", ({override}) => {
    const $interaction_root = $(".app-main");
    const $sender = $.create("secondary-sender-name");
    const $row = $.create("secondary-user-card-row");
    $sender.set_closest_results(".message_row", $row);
    $row.attr("data-message-id", "41");
    $row.set_closest_results(".overlay-message-row", []);
    const owner = {
        get(message_id) {
            assert.equal(message_id, 41);
            return {id: 41, sender_id: 7};
        },
    };
    message_lists.current = {
        get() {
            throw new Error("secondary user popover must not use message_lists.current");
        },
    };
    override(message_lists, "get_rendered_message_interaction_root", () => $interaction_root);
    override(message_lists, "get_message_list_for_row", (candidate) => {
        assert.equal(candidate.get(0), $row.get(0));
        return owner;
    });

    user_card_popover.initialize();
    const handler = $interaction_root.get_on_handler(
        "click",
        ".sender_name, .inline-profile-picture-wrapper",
    );
    handler.call($sender.get(0), {stopPropagation: noop});
    assert.equal(popover_reference, $sender.get(0));
});
