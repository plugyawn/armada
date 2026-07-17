"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let placeholder_refreshes = 0;

mock_esm("../src/compose_recipient", {
    update_compose_area_placeholder_text() {
        placeholder_refreshes += 1;
    },
});

const compose_pear = zrequire("compose_pear");

run_test("leaving Pear mode restores the canonical compose placeholder", () => {
    placeholder_refreshes = 0;
    compose_pear.clear();
    assert.equal(placeholder_refreshes, 1);
});
