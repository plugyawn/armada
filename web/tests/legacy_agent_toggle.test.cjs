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

const legacy_agent_toggle = zrequire("legacy_agent_toggle");

run_test("leaving Pear mode restores the canonical compose placeholder", () => {
    placeholder_refreshes = 0;
    legacy_agent_toggle.clear();
    assert.equal(placeholder_refreshes, 1);
});
