"use strict";

// The interim beta's caret at rest (web/src/armada/beta/caret.js), with a
// real DOM: after the hold, a drawn caret stands where the browser draws
// its own and the browser's own is hidden under it; a key, a press, the
// words changing under it, the field's width changing or focus leaving hand
// the caret back at once; a spot that cannot be read is left to the
// browser's own blink; with motion reduced nothing rests. jsdom lays
// nothing out, so where the browser would draw the caret is given here as
// the rectangles a layout would report. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div class="cz"><div class="sh"><div class="txw">
  <span class="ph cell"><span>Message devel</span></span>
  <div class="tx m-caret" contenteditable="plaintext-only"></div>
</div></div></div>
<button id="other">Other</button>`);
// The field's own box as the kit sets it: 3 px of room for an empty
// field's caret, the indent that puts it there, and the line.
Object.assign(dom.find(".tx").style, {
    padding: "4px 0 4px 3px",
    textIndent: "-3px",
    lineHeight: "24px",
});
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global("getSelection", () => dom.window.getSelection());
set_global("requestAnimationFrame", (f) => setTimeout(f, 16));
set_global("cancelAnimationFrame", (t) => clearTimeout(t));
let reduced = false;
set_global("M", {T: {"caret-hold": 500}, reduced: () => reduced});

// A layout: the field's box, its frame's box, the glyphs of the line the
// selection is on, and the placeholder's glyphs.
const BOXES = new Map();
const rect = (left, top, width, height) => ({
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
});
dom.window.HTMLElement.prototype.getBoundingClientRect = function () {
    return BOXES.get(this) ?? rect(0, 0, 0, 0);
};
// Where the browser's own caret is: the collapsed selection's rectangle,
// none when it sits between elements.
let caret_at = null;
const ph_glyphs = rect(336, 847, 120, 22);
dom.window.Range.prototype.getClientRects = function () {
    if (this.startContainer.parentElement?.closest(".ph")) {
        return [ph_glyphs];
    }
    return caret_at ? [caret_at] : [];
};
let resize_observed = null;
set_global(
    "ResizeObserver",
    class {
        constructor(callback) {
            resize_observed = callback;
        }
        observe() {}
        unobserve() {}
    },
);

const field = dom.find(".tx");
const frame = dom.find(".txw");
BOXES.set(field, rect(333, 842, 602, 32));
BOXES.set(frame, rect(336, 842, 599, 32));
dom.document.hasFocus = () => true;

const {wireCaret} = zrequire("armada/beta/caret.js");
wireCaret();

const drawn = () => frame.querySelector(".m-cur");
const shown = () => drawn() !== null && !drawn().hidden;
const place = () => [drawn().style.left, drawn().style.top, drawn().style.height];
// The person types a letter: the words change, the selection moves.
function letter(text, at) {
    field.textContent += text;
    caret_at = at;
    const selection = dom.window.getSelection();
    selection.collapse(field.firstChild, field.textContent.length);
    field.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
    dom.document.dispatchEvent(new dom.window.Event("selectionchange"));
    clock.tick(0);
}

run_test("an empty field rests after the hold, its caret drawn where the browser draws it", () => {
    field.focus();
    dom.window.getSelection().collapse(field, 0);
    clock.tick(499 + 16);
    assert.ok(!shown(), "not before the hold");
    assert.equal(field.dataset.rest, undefined);
    clock.tick(1 + 16);
    assert.ok(shown());
    // The start of the line, its indent included, as tall as the
    // placeholder's glyphs and centred on the line as the browser centres them.
    assert.deepEqual(place(), ["-3px", "5px", "22px"]);
    assert.equal(field.dataset.rest, "drawn");
});

run_test(
    "a key hands the caret back at once; it rests again after the hold, where it now is",
    () => {
        letter("H", rect(345.73, 847, 0, 22));
        assert.ok(!shown());
        assert.equal(field.dataset.rest, undefined);
        // Keys a little apart keep it awake.
        for (const at of [355.2, 364.9, 371.01]) {
            clock.tick(300);
            letter("i", rect(at, 847, 0, 22));
            assert.ok(!shown());
        }
        clock.tick(500 + 16);
        assert.ok(shown());
        // Snapped down to the pixel, as Chromium snaps its own.
        assert.deepEqual(place(), ["35px", "5px", "22px"]);
    },
);

// A mutation observer's records arrive as a microtask.
async function settle() {
    for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
    }
}

run_test(
    "the words changing under it, the field's width, or focus leaving hand it back",
    async () => {
        field.textContent = "";
        caret_at = null;
        await settle();
        assert.ok(!shown(), "the words changed under it (a send cleared them)");
        clock.tick(500 + 16);
        assert.ok(shown());

        resize_observed();
        assert.ok(shown(), "the observer's first word, the same size, changes nothing");
        BOXES.set(field, rect(333, 842, 402, 32));
        resize_observed();
        assert.ok(!shown(), "a narrower field rewraps its words");
        BOXES.set(field, rect(333, 842, 602, 32));
        clock.tick(500 + 16);
        assert.ok(shown());

        dom.find("#other").focus();
        clock.tick(0);
        assert.ok(!shown());
        clock.tick(2000);
        assert.ok(!shown(), "nothing rests in a field that does not hold focus");
    },
);

run_test("where its spot cannot be read, the browser's own blink stands in", () => {
    field.focus();
    field.innerHTML = `<span class="pill">@Onix</span>`;
    caret_at = null;
    dom.window.getSelection().collapse(field, 1);
    dom.document.dispatchEvent(new dom.window.Event("selectionchange"));
    clock.tick(500 + 16);
    assert.ok(!shown());
    assert.equal(field.dataset.rest, "native");
    letter(" ", rect(390.5, 847, 0, 22));
    assert.equal(field.dataset.rest, undefined);
    clock.tick(500 + 16);
    assert.equal(field.dataset.rest, "drawn");
    assert.deepEqual(place(), ["54px", "5px", "22px"]);
});

run_test("with motion reduced the caret never rests: the browser's own holds still", () => {
    reduced = true;
    letter("x", rect(399, 847, 0, 22));
    clock.tick(5000);
    assert.ok(!shown());
    assert.equal(field.dataset.rest, undefined);
    reduced = false;
});
