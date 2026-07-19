"use strict";

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const $document = $("document-stub");
class FakeElement {
    constructor($element) {
        this.$element = $element;
    }

    to_$() {
        return this.$element;
    }
}
set_global("Element", FakeElement);
set_global("document", {
    getSelection: () => null,
    to_$: () => $document,
});
set_global("window", {getSelection: () => null});

mock_esm("../src/util", {is_mobile: () => false});
mock_esm("../src/mouse_drag", {is_drag: () => false});
mock_esm("../src/narrow_state", {filter: () => undefined});
const message_lists = mock_esm("../src/message_lists");
const message_store = mock_esm("../src/message_store");
let focused_message_id;
mock_esm("../src/message_thread_hooks", {
    focus_composer(message_id) {
        focused_message_id = message_id;
        return true;
    },
});
mock_esm("../src/message_edit", {currently_editing_messages: new Map()});
let starred_message;
mock_esm("../src/starred_messages_ui", {
    toggle_starred_and_update_server(message) {
        starred_message = message;
    },
});
let reaction_options;
mock_esm("../src/reactions", {
    process_reaction_click(message_id, local_id) {
        reaction_options = {message_id, local_id};
    },
});
mock_esm("../src/emoji_picker", {hide_emoji_popover: noop});
const click_handlers = zrequire("click_handlers");

run_test(
    "secondary reaction, star, and row click share the canonical interaction root",
    ({override}) => {
        const $interaction_root = $(".app-main");
        const $row = $.create("secondary-click-row");
        $row.attr("data-message-id", "41");
        $row.set_closest_results(".overlay-message-row", []);
        const message = {id: 41};
        let selected_id;
        const owner = {
            view: {manages_primary_viewport: false},
            select_id(message_id) {
                selected_id = message_id;
            },
        };
        message_lists.current = {
            view: {manages_primary_viewport: true},
            select_id() {
                throw new Error("secondary row click must not select message_lists.current");
            },
        };
        override(message_lists, "get_rendered_message_interaction_root", () => $interaction_root);
        override(message_lists, "get_message_list_for_row", (candidate) => {
            assert.equal(candidate.get(0), $row.get(0));
            return owner;
        });
        override(message_store, "get", (message_id) => {
            assert.equal(message_id, 41);
            return message;
        });

        click_handlers.initialize();
        const event = {stopPropagation: noop};

        const $star = $.create("secondary-star");
        $star.set_closest_results(".message_row", $row);
        $interaction_root.get_on_handler("click", ".star_container").call($star.get(0), event);
        assert.equal(starred_message, message);

        const $reaction = $.create("secondary-reaction");
        $reaction.attr("data-reaction-id", "unicode-smile");
        $reaction.set_closest_results(".message_row", $row);
        $interaction_root
            .get_on_handler("click", ".message_reaction")
            .call($reaction.get(0), event);
        assert.deepEqual(reaction_options, {message_id: 41, local_id: "unicode-smile"});

        const $messagebox = $.create("secondary-messagebox");
        $messagebox.set_closest_results(".message_row", $row);
        const $target = $.create("secondary-messagebox-target");
        $target.set_closest_results("a", []);
        for (const selector of [
            "textarea",
            "input",
            "button.message_expander",
            "button.message_condenser",
            ".media-image-element",
            ".message_inline_animated_image_still",
            "video",
            ".message_inline_video",
            "img.twitter-avatar",
            ".media-audio-element",
            "i.edit_message_button",
            ".message_edit_notice",
            ".edit-notifications",
            ".spoiler-header",
            ".pear-artifact-pin, .pear-artifact-action",
            ".copy_codeblock",
            ".todo-widget label.checkbox",
        ]) {
            $target.set_matches(selector, false);
        }
        $target.set_parents_result(".spoiler-header", []);
        $target.set_parents_result(".pear-artifact-pin, .pear-artifact-action", []);
        $target.set_parents_result(".copy_codeblock", []);
        $target.set_parents_result(".message_controls", []);
        $target.set_parents_result(".todo-widget label.checkbox", []);
        $target.set_closest_results(".reveal-hidden-message", []);
        $target.set_closest_results(".rehide-muted-user-message", []);
        const target = new FakeElement($target);
        $interaction_root
            .get_on_handler("click", ".messagebox")
            .call($messagebox.get(0), {...event, target});
        assert.equal(selected_id, 41);
        assert.equal(focused_message_id, 41);
    },
);
