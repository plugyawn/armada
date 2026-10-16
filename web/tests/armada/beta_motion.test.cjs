"use strict";

// The interim beta's motion kit (web/src/armada/beta/kit/motion.js), with a
// real DOM: the keyboard's focus ring is placed when something can have
// moved its control (a scroll, the page changing, the control's size), one
// frame each, and asks for no frame at all while nothing moves. jsdom lays
// nothing out and animates nothing, so a layout's rectangles and the Web
// Animations calls are stand-ins here. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="pane"><button id="a">One</button><button id="b">Two</button></div>
<div id="field" contenteditable="true"></div>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
// Frames asked for, counted: the ring must ask for none while nothing moves.
let frames = 0;
set_global("requestAnimationFrame", (f) => {
    frames += 1;
    return setTimeout(f, 16);
});
set_global("cancelAnimationFrame", (t) => clearTimeout(t));
let resized = null;
set_global(
    "ResizeObserver",
    class {
        constructor(callback) {
            resized = callback;
        }
        observe() {}
        unobserve() {}
    },
);
// The spring tokens motion.js reads from the root (tokens.css).
for (const [name, value] of Object.entries({
    "--touch-r": "0.15",
    "--touch-z": "1",
    "--touch-t": "210ms",
    "--fade-in": "120ms",
    "--fade-out": "60ms",
    "--ring-near": "48px",
})) {
    dom.document.documentElement.style.setProperty(name, value);
}
// Web Animations, as far as motion.js uses them.
const animations = [];
dom.window.Element.prototype.animate = function (_keyframes, options) {
    let done;
    const animation = {
        target: this,
        options,
        finished: new Promise((resolve) => {
            done = resolve;
        }),
        cancel() {},
        finish() {
            done();
        },
    };
    animations.push(animation);
    return animation;
};
// A layout: each control's box, which a test moves.
const BOXES = new Map();
dom.window.HTMLElement.prototype.getBoundingClientRect = function () {
    const [left, top, width, height] = BOXES.get(this) ?? [0, 0, 0, 0];
    return {left, top, width, height, right: left + width, bottom: top + height};
};
const a = dom.find("#a");
const b = dom.find("#b");
BOXES.set(a, [100, 200, 80, 32]);
BOXES.set(b, [190, 200, 80, 32]);

zrequire("armada/beta/kit/motion.js");
const {M} = dom.window;
M.wire();

const ring = () => dom.document.querySelector("#m-ring");
const placed = () => ring().style.translate;
function tab_to(target) {
    dom.press("Tab", {target: dom.document.activeElement ?? dom.body});
    target.focus();
}
// A mutation observer's records arrive as a microtask.
async function settle() {
    for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
    }
    clock.tick(16);
}

run_test(
    "the ring stands on the control the keyboard reached, and asks for no frame at rest",
    async () => {
        tab_to(a);
        assert.equal(placed(), "98px 198px");
        assert.equal(ring().style.width, "84px");
        await settle();
        frames = 0;
        clock.tick(10_000);
        assert.equal(frames, 0, "a ring at rest asks for no frame");
    },
);

run_test(
    "a scroll, the page changing or the control resizing re-place it, one frame each",
    async () => {
        BOXES.set(a, [100, 150, 80, 32]);
        dom.find("#pane").dispatchEvent(new dom.window.Event("scroll"));
        dom.find("#pane").dispatchEvent(new dom.window.Event("scroll"));
        clock.tick(16);
        assert.equal(frames, 1, "two scrolls in one frame are one placement");
        assert.equal(placed(), "98px 148px");

        BOXES.set(a, [100, 120, 80, 32]);
        dom.find("#pane").prepend(dom.document.createElement("p"));
        await settle();
        assert.equal(placed(), "98px 118px");

        BOXES.set(a, [100, 120, 120, 32]);
        resized();
        clock.tick(16);
        assert.equal(ring().style.width, "124px");

        await settle();
        frames = 0;
        clock.tick(10_000);
        assert.equal(frames, 0);
    },
);

run_test("a press takes the ring away, and nothing it watched asks for a frame after", async () => {
    tab_to(b);
    assert.equal(placed(), "188px 198px");
    dom.find("#pane").dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true}));
    await settle();
    frames = 0;
    dom.find("#pane").dispatchEvent(new dom.window.Event("scroll"));
    dom.find("#pane").append(dom.document.createElement("p"));
    await settle();
    clock.tick(1000);
    assert.equal(frames, 0);
});
