"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const message_lists = mock_esm("../src/message_lists");
const condense = zrequire("condense");

run_test(
    "secondary show-more uses the canonical interaction root and owning list",
    ({override}) => {
        const $interaction_root = $(".app-main");
        const $button = $.create("secondary-message-expander");
        const $row = $.create("secondary-condensed-row");
        const $content = $.create("secondary-condensed-content");
        const $length_controller = $.create("secondary-message-length-controller");
        $button.set_closest_results(".message_row", $row);
        $row.attr("data-message-id", "41");
        $row.set_closest_results(".overlay-message-row", []);
        $row.set_find_results(".message_content", $content);
        $row.set_find_results(".message_length_controller", $length_controller);
        $content.addClass("condensed could-be-condensed");

        const message = {id: 41, collapsed: false, condensed: true};
        let selected;
        const owner = {
            view: {manages_primary_viewport: false},
            get(message_id) {
                assert.equal(message_id, 41);
                return message;
            },
            select_id(message_id, options) {
                selected = {message_id, options};
            },
        };
        message_lists.current = {
            /* istanbul ignore next */
            get() {
                throw new Error("secondary show-more must not use message_lists.current");
            },
        };
        override(message_lists, "get_rendered_message_interaction_root", () => $interaction_root);
        override(message_lists, "get_message_list_for_row", (candidate) => {
            assert.equal(candidate.get(0), $row.get(0));
            return owner;
        });

        condense.initialize();
        const handler = $interaction_root.get_on_handler("click", ".message_expander");
        handler.call($button.get(0), {preventDefault: noop, stopPropagation: noop});

        assert.equal(message.condensed, false);
        assert.equal($content.hasClass("condensed"), false);
        assert.deepEqual(selected, {message_id: 41, options: {then_scroll: false}});
    },
);
