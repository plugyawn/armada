"use strict";

// The workspace menu of the interim beta (web/src/armada/beta/lab.js), with a
// real DOM: the workspace's name opens it and Escape or a press elsewhere
// closes it; it holds who I am, Sign out, the theme, sound and motion; and
// beside the name a word appears only while the connection is in trouble.
// Nothing says "Beta". Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<nav><div class="top"><button class="wk-name" aria-label="Workspace">Armada</button><button class="c sq new" aria-label="New"></button></div></nav>
<main><p id="elsewhere">The conversation</p></main>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("localStorage", dom.window.localStorage);
set_global("M", {T: {}, stretch() {}});
let sound = false;
set_global("S", {
    on: () => sound,
    set(on) {
        sound = on;
    },
});

let signed_out = 0;
mock_esm("../../src/armada/beta/composer.js", {ready() {}});
mock_esm("../../src/armada/beta/connection.js", {
    signOut() {
        signed_out += 1;
    },
});

const beta_state = zrequire("armada/beta/state.js");
const lab = zrequire("armada/beta/lab.js");

beta_state.state.meName = "Shiva";
lab.buildLab();

const name = document.querySelector(".wk-name");
const menu = () => document.querySelector("#lab .pn");
const is_open = () => name.getAttribute("aria-expanded") === "true";
const button = (words) =>
    [...menu().querySelectorAll("button")].find((b) => b.textContent.trim() === words);
const press = (element) =>
    element.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true}));
const trouble = () => document.querySelector("#lab-t");

run_test("the workspace's name opens the menu, and Escape or a press elsewhere closes it", () => {
    assert.equal(is_open(), false);
    press(name);
    assert.equal(is_open(), true);
    assert.match(menu().textContent, /Signed in as Shiva\./);
    assert.ok(button("Sign out"));
    assert.ok(button("Dark"));
    menu().dispatchEvent(new dom.window.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    assert.equal(is_open(), false);
    assert.equal(document.activeElement, name);

    press(name);
    assert.equal(is_open(), true);
    document
        .querySelector("#elsewhere")
        .dispatchEvent(new dom.window.MouseEvent("mousedown", {bubbles: true}));
    assert.equal(is_open(), false);
    // The name opens and closes it too.
    press(name);
    press(name);
    assert.equal(is_open(), false);
});

run_test("its controls set the theme, sound and motion, and sign out", () => {
    press(name);
    press(button("Dark"));
    assert.equal(document.documentElement.dataset.theme, "dark");
    press(button("Auto"));
    assert.equal(document.documentElement.dataset.theme, undefined);
    press(button("Sound is off"));
    assert.ok(button("Sound is on"));
    press(button("Reduce motion"));
    assert.equal(document.documentElement.dataset.motion, "reduced");
    press(button("Motion is reduced"));
    assert.equal(document.documentElement.dataset.motion, undefined);
    press(button("Sign out"));
    assert.equal(signed_out, 1);
    press(name);
});

run_test("nothing says Beta; trouble with the connection is a word beside the name", () => {
    assert.doesNotMatch(document.body.textContent, /beta/i);
    assert.equal(trouble().hidden, true);
    lab.showTrouble("reconnecting");
    assert.equal(trouble().hidden, false);
    assert.equal(trouble().textContent, "Reconnecting…");
    lab.showTrouble("offline");
    assert.equal(trouble().textContent, "Offline");
    lab.showTrouble("connected");
    assert.equal(trouble().hidden, true);
    assert.equal(trouble().textContent, "");
});
