"use strict";

const assert = require("node:assert/strict");

const {make_real_dom} = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

// A field that behaves like a message composer: Return sends, Shift-Return
// does not, and a Return that commits an input-method candidate does not.
function mount_field(field, sent) {
    field.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
            return;
        }
        event.preventDefault();
        sent.push(field.value);
        field.value = "";
    });
}

run_test("globals are those of the document under test", () => {
    const dom = make_real_dom(`<p id="greeting">hello</p>`);
    assert.equal(document, dom.document);
    assert.equal(dom.body, document.body);
    const greeting = dom.find("#greeting");
    assert.ok(greeting instanceof globalThis.HTMLElement);
    assert.ok(greeting instanceof globalThis.Element);
    assert.equal(greeting.textContent, "hello");
    assert.equal(globalThis.getComputedStyle(greeting).display, "block");
    assert.throws(() => dom.find("#absent"), /nothing matches #absent/);
});

run_test("typed text and Return reach a field's handlers", () => {
    const dom = make_real_dom(`<textarea id="field"></textarea>`);
    const field = dom.find("#field");
    const sent = [];
    mount_field(field, sent);

    dom.type(field, "hi");
    assert.equal(document.activeElement, field);
    assert.equal(field.value, "hi");

    // Shift-Return is left to the browser; nothing is sent.
    assert.equal(dom.press("Enter", {shiftKey: true}), true);
    assert.deepEqual(sent, []);

    // Return on the focused field sends and is consumed.
    assert.equal(dom.press("Enter"), false);
    assert.deepEqual(sent, ["hi"]);
    assert.equal(field.value, "");
});

run_test("a character whose keydown is prevented is not inserted", () => {
    const dom = make_real_dom(`<input id="digits" />`);
    const field = dom.find("#digits");
    const inputs = [];
    field.addEventListener("keydown", (event) => {
        if (!/^\d$/.test(event.key)) {
            event.preventDefault();
        }
    });
    field.addEventListener("input", (event) => {
        inputs.push(event.data);
    });
    dom.type(field, "4a2");
    assert.equal(field.value, "42");
    assert.deepEqual(inputs, ["4", "2"]);
});

run_test("Return that commits an input-method candidate is not an ordinary Return", () => {
    const dom = make_real_dom(`<textarea id="field"></textarea>`);
    const field = dom.find("#field");
    const sent = [];
    const composition = [];
    mount_field(field, sent);
    for (const type of ["compositionstart", "compositionupdate", "compositionend"]) {
        field.addEventListener(type, (event) => {
            composition.push(event.type);
        });
    }

    assert.equal(dom.compose_with_input_method(field, "日本"), true);
    assert.deepEqual(sent, []);
    assert.equal(field.value, "日本");
    assert.deepEqual(composition, ["compositionstart", "compositionupdate", "compositionend"]);

    // The next Return is an ordinary one.
    dom.press("Enter");
    assert.deepEqual(sent, ["日本"]);
});

run_test("keys go to the given target and carry key codes", () => {
    const dom = make_real_dom(`<div id="list" tabindex="0"><button id="item">Open</button></div>`);
    const seen = [];
    dom.find("#list").addEventListener("keydown", (event) => {
        // Handlers written against keyCode exist; the helper fills it in.
        // eslint-disable-next-line unicorn/prefer-keyboard-event-key
        seen.push([event.key, event.keyCode, event.target.id]);
    });
    dom.press("Escape", {target: dom.find("#item")});
    dom.press("j", {target: dom.find("#item")});
    dom.press("F6", {target: dom.find("#item")});
    document.activeElement.blur();
    dom.press("ArrowDown");
    assert.deepEqual(seen, [
        ["Escape", 27, "item"],
        ["j", 74, "item"],
        ["F6", 0, "item"],
    ]);
});

run_test("a click presses, focuses, releases and clicks", () => {
    const dom = make_real_dom(
        `<button id="plain">Plain</button><button id="keeps-focus-away">Menu</button>`,
    );
    const plain = dom.find("#plain");
    const menu = dom.find("#keeps-focus-away");
    const events = [];
    for (const type of ["mousedown", "mouseup", "click"]) {
        document.body.addEventListener(type, (event) => {
            events.push(`${event.type}:${event.target.id}`);
        });
    }
    // A control that must not take focus from the field prevents the press.
    menu.addEventListener("mousedown", (event) => {
        event.preventDefault();
    });
    menu.addEventListener("click", (event) => {
        event.preventDefault();
    });

    assert.equal(dom.click(plain), true);
    assert.equal(document.activeElement, plain);

    assert.equal(dom.click(menu), false);
    assert.equal(document.activeElement, plain);
    assert.deepEqual(events, [
        "mousedown:plain",
        "mouseup:plain",
        "click:plain",
        "mousedown:keeps-focus-away",
        "mouseup:keeps-focus-away",
        "click:keeps-focus-away",
    ]);
});
