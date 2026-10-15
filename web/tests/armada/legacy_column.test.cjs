"use strict";

// The old rail's sheet (bridge/legacy_column.ts), used the way a person
// uses it: More opens it, Esc or a press outside closes it, and focus goes
// in with it and comes back to what opened it.

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("../lib/namespace.cjs");
const {make_real_dom} = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = make_real_dom(`
    <div data-armada="more"><button id="more" type="button">More</button></div>
    <p id="conversation" tabindex="-1">A row of the conversation</p>
    <div data-armada="sheet">
        <button type="button" disabled>A disabled control</button>
        <input id="filter" type="text" />
        <button type="button">People</button>
    </div>
`);

// The bridge's stylesheet is the browser's business.
mock_esm("../../src/armada/bridge/legacy_column.css");

const legacy_column = zrequire("armada/bridge/legacy_column.ts");

legacy_column.initialize();

const more = dom.find("#more");
const filter = dom.find("#filter");

run_test("More opens the sheet with focus on its first control", () => {
    more.focus();
    legacy_column.toggle();
    assert.ok(legacy_column.is_open());
    assert.equal(dom.document.activeElement, filter);
});

run_test("Esc closes the sheet, does nothing else, and gives focus back", () => {
    const allowed = dom.press("Escape");
    assert.equal(allowed, false);
    assert.ok(!legacy_column.is_open());
    assert.equal(dom.document.activeElement, more);
    // A second Esc finds no sheet and is left to the page.
    assert.equal(dom.press("Escape"), true);
});

run_test("More again closes it, as it opened it", () => {
    more.focus();
    legacy_column.toggle();
    assert.equal(dom.document.activeElement, filter);
    legacy_column.toggle();
    assert.ok(!legacy_column.is_open());
    assert.equal(dom.document.activeElement, more);
});

run_test("a press outside closes it and leaves focus to the press", () => {
    more.focus();
    legacy_column.open();
    const conversation = dom.find("#conversation");
    conversation.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true}));
    assert.ok(!legacy_column.is_open());
    assert.notEqual(dom.document.activeElement, more);
});

run_test("a press inside it, or on More, leaves it open", () => {
    more.focus();
    legacy_column.open();
    filter.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true}));
    more.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true}));
    assert.ok(legacy_column.is_open());
    legacy_column.close();
});
