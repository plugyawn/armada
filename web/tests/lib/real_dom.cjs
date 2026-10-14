"use strict";

// A real DOM (jsdom) for tests of components that build and listen to
// elements themselves. Tests written with it exercise behaviour the way
// a person does (a press, a key, typed text, an input method) and read
// the result back from the document; they do not match markup or CSS
// text, so they survive a rename.
//
//     const {make_real_dom} = require("../lib/real_dom.cjs");
//
//     run_test("Return sends", () => {
//         const dom = make_real_dom(`<textarea id="field"></textarea>`);
//         const field = dom.find("#field");
//         mount_composer(field);
//         dom.type(field, "hello");
//         dom.press("Enter", {target: field});
//         assert.deepEqual(sent, ["hello"]);
//     });
//
// The globals it installs go through `set_global`, so they are removed
// again when the test file finishes.

const {JSDOM} = require("jsdom");

const {set_global} = require("./namespace.cjs");

// What a component may reach for without going through `window`.
const GLOBAL_NAMES = [
    "document",
    "Node",
    "Text",
    "Element",
    "HTMLElement",
    "DocumentFragment",
    "Event",
    "CustomEvent",
    "KeyboardEvent",
    "MouseEvent",
    "InputEvent",
    "CompositionEvent",
    "FocusEvent",
    "MutationObserver",
    "NodeFilter",
    "getComputedStyle",
];

// Keys whose `keyCode` handlers still read.
const KEY_CODES = new Map([
    ["Enter", 13],
    ["Escape", 27],
    ["Tab", 9],
    ["Backspace", 8],
    [" ", 32],
    ["ArrowLeft", 37],
    ["ArrowUp", 38],
    ["ArrowRight", 39],
    ["ArrowDown", 40],
]);

exports.make_real_dom = (body_html = "") => {
    const jsdom = new JSDOM(`<!doctype html><html><body>${body_html}</body></html>`, {
        url: "http://zulip.zulipdev.com/",
        pretendToBeVisual: true,
    });
    const {window} = jsdom;
    const {document} = window;
    for (const name of GLOBAL_NAMES) {
        set_global(name, window[name]);
    }

    function find(selector) {
        const element = document.querySelector(selector);
        if (element === null) {
            throw new Error(`real_dom: nothing matches ${selector}`);
        }
        return element;
    }

    function dispatch(target, event) {
        // True when no handler called preventDefault.
        return target.dispatchEvent(event);
    }

    function key_event(type, key, init) {
        return new window.KeyboardEvent(type, {
            key,
            keyCode:
                KEY_CODES.get(key) ?? (key.length === 1 ? key.toUpperCase().codePointAt(0) : 0),
            bubbles: true,
            cancelable: true,
            ...init,
        });
    }

    // One key going down and up on the focused element (or `target`).
    // Returns false when a keydown handler prevented the default action.
    function press(key, {target = document.activeElement ?? document.body, ...init} = {}) {
        const allowed = dispatch(target, key_event("keydown", key, init));
        dispatch(target, key_event("keyup", key, init));
        return allowed;
    }

    // A pointer press and release on an element, then the click the
    // browser derives from them. Focus moves the way it does in a
    // browser: on the press, unless a handler prevented it.
    function click(target, init = {}) {
        const options = {bubbles: true, cancelable: true, button: 0, ...init};
        if (dispatch(target, new window.MouseEvent("mousedown", options))) {
            target.focus();
        }
        dispatch(target, new window.MouseEvent("mouseup", options));
        return dispatch(target, new window.MouseEvent("click", options));
    }

    // Text typed into a field one character at a time, with the events
    // a keyboard produces. A character whose keydown is prevented is
    // not inserted.
    function type(field, text) {
        field.focus();
        for (const character of text) {
            if (!dispatch(field, key_event("keydown", character))) {
                dispatch(field, key_event("keyup", character));
                continue;
            }
            field.value += character;
            dispatch(
                field,
                new window.InputEvent("input", {
                    data: character,
                    inputType: "insertText",
                    bubbles: true,
                }),
            );
            dispatch(field, key_event("keyup", character));
        }
    }

    // Text entered through an input method: the candidate is composed,
    // then committed with Return. Browsers report that Return as a
    // keydown with `isComposing` set and keyCode 229; a handler that
    // treats it as an ordinary Return sends a half-written message.
    // Returns false when that keydown was prevented.
    function compose_with_input_method(field, text) {
        field.focus();
        dispatch(field, new window.CompositionEvent("compositionstart", {bubbles: true}));
        field.value += text;
        dispatch(
            field,
            new window.CompositionEvent("compositionupdate", {data: text, bubbles: true}),
        );
        dispatch(
            field,
            new window.InputEvent("input", {
                data: text,
                inputType: "insertCompositionText",
                isComposing: true,
                bubbles: true,
            }),
        );
        const allowed = dispatch(
            field,
            key_event("keydown", "Enter", {isComposing: true, keyCode: 229}),
        );
        dispatch(field, new window.CompositionEvent("compositionend", {data: text, bubbles: true}));
        dispatch(field, key_event("keyup", "Enter"));
        return allowed;
    }

    return {
        window,
        document,
        body: document.body,
        find,
        press,
        click,
        type,
        compose_with_input_method,
    };
};
