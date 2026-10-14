"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {make_motion_dom, token_source} = require("./motion_lib.cjs");

const springs = zrequire("armada/motion/springs.ts");
const motion = zrequire("armada/motion/index.ts");

const EXPORTED = [
    "release",
    "current",
    "halt",
    "rest",
    "flip",
    "fade",
    "appear",
    "leave",
    "hold",
    "thumb",
    "figure",
    "cells",
    "swap",
    "cross",
    "grow",
    "fly",
    "carry",
    "land",
    "kick",
    "squash",
    "burst",
    "shake",
    "refuse",
    "ring",
    "tip",
    "say",
];

function setup(html = `<div id="a">a</div><div id="b">b</div>`, overrides = {}) {
    const dom = make_motion_dom(html);
    springs.read(token_source(overrides));
    return dom;
}

const a_bit = () => document.createTextNode("x");

const translate = (keyframe) =>
    /translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+), ([-\d.]+)\)/
        .exec(keyframe.transform)
        .slice(1)
        .map(Number);

run_test("the kit has the design's names", () => {
    for (const name of EXPORTED) {
        assert.equal(typeof motion[name], "function", name);
    }
});

run_test("release starts displaced, ends at rest, and adds to what is there", () => {
    const dom = setup();
    const a = dom.find("#a");
    const animation = motion.release(a, {x: 40, sy: -0.5}, "move");
    assert.equal(animation.options.duration, 340);
    assert.equal(animation.options.composite, "add");
    assert.deepEqual(translate(animation.keyframes[0]), [40, 0, 1, 0.5]);
    assert.deepEqual(translate(animation.keyframes.at(-1)), [0, 0, 1, 1]);
    // Nothing to release, and nothing before the tokens say how long.
    assert.equal(motion.release(dom.find("#b"), {x: 0.1}), null);
    assert.deepEqual(motion.current(dom.find("#b")), [{}, {}]);
    motion.halt(dom.find("#b"));
    // On a pseudo-element, and held within a limit.
    const clamped = motion.release(dom.find("#b"), {y: 500}, "touch", {
        clamp: {y: 80},
        pseudo: "::before",
    });
    assert.equal(translate(clamped.keyframes[0])[1], 80);
    assert.equal(clamped.options.pseudoElement, "::before");
});

run_test("a retarget keeps displacement and velocity", () => {
    const dom = setup();
    const a = dom.find("#a");
    const first = motion.release(a, {x: 100}, "move");
    dom.advance(100);
    const [was, speed] = motion.current(a);
    assert.ok(was.x > 0 && was.x < 100 && speed.x < 0);
    const second = motion.release(a, {x: 50}, "move");
    assert.equal(first.cancelled, true);
    // It starts on the frame on screen, where the old one was read; a
    // first release waits for the next frame.
    assert.equal(second.startTime, dom.state.now);
    assert.equal(first.startTime, undefined);
    const [now, speed_now] = motion.current(a);
    assert.ok(Math.abs(now.x - (was.x + 50)) < 1e-6);
    assert.ok(Math.abs(speed_now.x - speed.x) < 1e-6);
    assert.ok(Math.abs(translate(second.keyframes[0])[0] - (was.x + 50)) < 0.006);
    // The sampled spring is continuous across the retarget.
    dom.advance(0.001);
    assert.ok(Math.abs(motion.current(a)[0].x - (was.x + 50)) < 1e-3);
    dom.advance(1000);
    assert.deepEqual(motion.current(a), [{}, {}]);
    // Once the animation has started, its own clock says where it is.
    const playing = motion.release(a, {x: 100}, "move");
    playing.currentTime = 0;
    dom.advance(50);
    assert.equal(motion.current(a)[0].x, 100);
    motion.halt(a);
});

run_test("reduced motion: the state is already there", async () => {
    const dom = setup(undefined, {"move-t": "1ms", "touch-t": "1ms", "land-t": "1ms"});
    const a = dom.find("#a");
    assert.equal(motion.release(a, {x: 100}), null);
    assert.equal(motion.hold(a), null);
    assert.equal(motion.shake(a), null);
    motion.burst(a, a_bit);
    assert.equal(dom.state.animations.length, 0);
    // A refusal shows on the edge for two steps of the agent's clock.
    const refusal = motion.refuse(a);
    assert.equal(refusal.options.duration, 200);
    assert.ok(a.querySelector(".armada-refusal"));
    refusal.finish();
    await dom.settle();
    assert.equal(a.querySelector(".armada-refusal"), null);
    dom.place(a, 0, 0, 100, 40);
    assert.equal(motion.grow(a, motion.rest(dom.find("#b"))), null);
    assert.equal(motion.fly(a, motion.rest(a)), null);
    const next = document.createElement("i");
    assert.equal(motion.cross(a, next), next);
    const cell = dom.find("#b");
    cell.replaceChildren(document.createElement("span"));
    motion.swap(cell, document.createElement("b"));
});

run_test("rest takes the springs in flight out; flip releases the difference", () => {
    const dom = setup(`<div id="p"><div id="a">a</div></div>`);
    const [p, a] = [dom.find("#p"), dom.find("#a")];
    dom.place(a, 110, 220, 50, 20);
    motion.release(p, {x: 10, y: 20}, "move");
    const box = motion.rest(a);
    assert.equal(box.left, 100);
    assert.equal(box.top, 200);
    assert.equal(box.right, 150);
    assert.equal(box.bottom, 220);
    motion.halt(p);
    const moved = motion.flip(a, {left: 100, top: 200, width: 100, height: 40}, "move", {
        resize: true,
    });
    assert.deepEqual(translate(moved.keyframes[0]), [-10, -20, 2, 2]);
    motion.halt(a);
    assert.equal(motion.flip(a, motion.rest(a)), null);
    // A thing in flight above every pane is its own frame of reference.
    a.dataset.armadaFlying = "";
    motion.halt(a);
    motion.release(p, {x: 10}, "move");
    assert.equal(motion.rest(a).left, 110);
});

run_test("fades run from the current opacity", async () => {
    const dom = setup();
    const a = dom.find("#a");
    assert.equal(motion.fade(a, 1), null);
    a.style.opacity = "1";
    const out = motion.fade(a, 0);
    assert.equal(out.options.duration, 60);
    assert.equal(a.style.opacity, "0");
    const back = motion.fade(a, 1);
    assert.equal(out.cancelled, true);
    assert.equal(back.options.duration, 80);
    assert.equal(motion.fade(a, 0.5, 33).options.duration, 33);
    assert.equal(motion.appear(a).options.fill, "none");
    const late = motion.appear(a, 0.3, undefined, {wait: 60, pseudo: "::after"});
    assert.equal(late.options.fill, "backwards");
    assert.equal(late.options.pseudoElement, "::after");
    assert.equal(motion.hold(a, 2).options.duration, 200);

    let told = 0;
    const leaving = motion.leave(a, () => {
        told += 1;
    });
    assert.ok(a.hasAttribute("inert"));
    leaving.finish();
    await dom.settle();
    assert.equal(a.isConnected, false);
    assert.equal(told, 1);
    // Cancelled: whoever cancelled it owns the element.
    const b = dom.find("#b");
    motion.leave(b).cancel();
    await dom.settle();
    assert.equal(b.isConnected, true);
});

run_test("one thumb a list", () => {
    const dom = setup(
        `<div id="list"><i id="thumb" data-off></i><button id="a">a</button><button id="b">b</button></div>`,
    );
    const [thumb, a, b] = [dom.find("#thumb"), dom.find("#a"), dom.find("#b")];
    dom.place(a, 0, 0, 100, 28);
    dom.place(b, 0, 28, 120, 28);
    // The first selection appears; it does not travel from nowhere.
    assert.deepEqual(motion.thumb(thumb, a).keyframes, {opacity: [0, 1]});
    assert.equal(thumb.style.getPropertyValue("--h"), "28px");
    assert.equal("off" in thumb.dataset, false);
    const travel = motion.thumb(thumb, b, "move");
    assert.equal(travel.options.duration, 340);
    assert.equal(translate(travel.keyframes[0])[1], -28);
    assert.equal(motion.thumb(thumb, null), null);
    assert.equal("off" in thumb.dataset, true);
});

run_test("a figure moves only the characters that changed", () => {
    const dom = setup(`<span id="a"></span>`);
    const a = dom.find("#a");
    assert.deepEqual(
        motion.cells("1:0m").map((cell) => cell.dataset.cell),
        [undefined, "mark", undefined, "word"],
    );
    motion.figure(a, 9);
    assert.equal(a.textContent, "9");
    assert.equal(dom.state.animations.length, 0);
    motion.figure(a, 9);
    motion.figure(a, 10);
    assert.equal(a.children.length, 2);
    const before = dom.state.animations.length;
    motion.figure(a, 12);
    assert.equal(a.children[1].firstChild.nodeValue, "2");
    // The ghost of the old digit leaves; the cell rises from below.
    assert.equal(dom.state.animations.length, before + 2);
    assert.equal(translate(dom.last().keyframes[0])[1], 8);
    dom.advance(1000);
    motion.figure(a, 11);
    assert.equal(translate(dom.last().keyframes[0])[1], -8);
    assert.equal(a.children[1].querySelectorAll("i").length, 1);
    const count = dom.state.animations.length;
    motion.figure(a, 13, {cut: true});
    motion.figure(a, 14, {quiet: true});
    motion.figure(a, 15, {direction: -1});
    assert.equal(dom.state.animations.length, count + 4);
    a.children[0].replaceChildren();
    motion.figure(a, 25);
    assert.equal(motion.cell_kind("+"), "word");
});

run_test("a word in a fixed cell: the old one goes, the new one comes", () => {
    const dom = setup(`<span id="cell"><span id="old">Send</span><i data-sizer>Answer</i></span>`);
    const cell = dom.find("#cell");
    const next = document.createElement("span");
    motion.swap(cell, next);
    assert.ok("gone" in dom.find("#old").dataset);
    assert.equal(cell.lastElementChild, next);
    const again = document.createElement("span");
    motion.swap(cell, again, {sequence: true, quiet: true, direction: -1});
    assert.equal(dom.find("#cell").querySelector("#old"), null);
    assert.equal(dom.last().options.delay, 60);
    const empty = document.createElement("span");
    document.body.append(empty);
    motion.swap(empty, document.createElement("b"), {sequence: true});
});

run_test("one mark becomes another in place", async () => {
    const dom = setup(`<p><i id="old"></i></p>`);
    const old = dom.find("#old");
    dom.place(old, 10, 20, 14, 14);
    old.animate({opacity: [0, 1]}, {});
    const next = document.createElement("b");
    assert.equal(motion.cross(old, next), next);
    assert.equal(old.style.position, "fixed");
    assert.equal(old.style.left, "10px");
    assert.equal(old.parentElement, document.body);
    // Never a frame with neither: the new one is in the old one's place.
    assert.equal(document.querySelector("p").firstElementChild, next);
    // A mark that was not on screen is simply replaced.
    const hidden = document.createElement("i");
    next.after(hidden);
    dom.place(hidden, 0, 0, 0, 0);
    const third = document.createElement("u");
    motion.cross(hidden, third);
    assert.equal(hidden.isConnected, false);
});

run_test("grow keeps the corners", async () => {
    const dom = setup(`<p id="a"><span class="armada-nine"></span>hi</p>`);
    const a = dom.find("#a");
    Object.assign(a.style, {
        borderTopLeftRadius: "18px",
        borderBottomRightRadius: "18px",
        background: "rgb(1, 2, 3)",
    });
    dom.place(a, 0, 0, 200, 60);
    assert.equal(motion.grow(a, {left: 0, top: 0, width: 200, height: 60.5}), null);
    const last = motion.grow(a, {left: 0, top: 0, width: 44, height: 28});
    const nine = a.querySelector(".armada-nine");
    assert.equal(a.querySelectorAll(".armada-nine").length, 1);
    assert.equal(nine.children.length, 9);
    assert.equal(a.style.background, "transparent");
    assert.equal(nine.children[0].style.borderTopLeftRadius, "18px");
    assert.equal(nine.children[8].style.borderBottomRightRadius, "18px");
    assert.equal(nine.children[4].style.borderTopLeftRadius, "");
    // A second arrival before the first has settled.
    const again = motion.grow(a, {left: 0, top: 0, width: 100, height: 28});
    assert.equal(last.cancelled, false);
    assert.equal(a.querySelectorAll(".armada-nine").length, 1);
    for (const animation of dom.state.animations) {
        animation.finish();
    }
    await dom.settle();
    assert.ok(again);
    assert.equal(a.querySelector(".armada-nine"), null);
    assert.notEqual(a.style.background, "transparent");
    // Too small to have a middle: only the corners are drawn.
    dom.place(a, 0, 0, 30, 30);
    motion.grow(a, {left: 0, top: 0, width: 10, height: 10});
    assert.equal(a.querySelector(".armada-nine").children.length, 4);
});

run_test("fly: from a rectangle to its place, in the top layer", async () => {
    const dom = setup(`<div id="lane"><p id="a"><b id="inner">x</b></p></div>`);
    const a = dom.find("#a");
    a.style.color = "red";
    dom.place(a, 300, 400, 120, 36);
    const flight = motion.fly(a, {left: 700, top: 800, width: 72, height: 32});
    assert.deepEqual(translate(flight.keyframes[0]).slice(0, 2), [400, 400]);
    assert.equal(a.getAttribute("popover"), "manual");
    assert.equal(a.style.left, "300px");
    // Its room is kept by its own picture, unseen and unnamed.
    const spacer = a.nextElementSibling;
    assert.equal(spacer.style.visibility, "hidden");
    assert.equal(spacer.id, "");
    assert.equal(spacer.querySelector("[id]"), null);
    assert.equal(motion.flying.size, 1);
    // The pane under it moves: it is retargeted, not restarted.
    motion.carry(44);
    assert.equal(a.style.top, "356px");
    assert.equal(flight.cancelled, true);
    await dom.settle();
    assert.equal(motion.flying.size, 1);
    dom.last().finish();
    await dom.settle();
    assert.equal(motion.flying.size, 0);
    assert.equal(a.hasAttribute("popover"), false);
    assert.equal(a.style.cssText, "color: red;");
    assert.equal(spacer.isConnected, false);

    // Carried with nothing left to travel, it lands at once.
    motion.fly(a, {left: 700, top: 800, width: 72, height: 32}, "move", {in_place: true});
    assert.equal(a.nextElementSibling, null);
    dom.advance(5000);
    motion.carry(0);
    assert.equal(motion.flying.size, 0);
    // Already in its place; or no top layer to fly in.
    assert.equal(motion.fly(a, motion.rest(a)), null);
    a.dataset.noPopover = "";
    assert.equal(motion.fly(a, {left: 0, top: 0, width: 1, height: 1}), null);
    assert.equal(a.hasAttribute("popover"), false);
    const text = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    text.showPopover = () => {};
    document.body.append(text);
    dom.place(text, 0, 0, 10, 10);
    Object.defineProperty(text, "cloneNode", {value: () => document.createTextNode("")});
    assert.equal(motion.fly(text, {left: 5, top: 5, width: 1, height: 1}), null);
});

run_test("arrivals and knocks", async () => {
    const dom = setup();
    const a = dom.find("#a");
    dom.place(a, 100, 100, 20, 20);
    const landing = motion.land(a);
    assert.equal(landing.options.duration, 360);
    assert.ok(Math.abs(translate(landing.keyframes[0])[2] - 0.7) < 1e-9);
    const kicked = motion.kick(a, {x: -180});
    assert.deepEqual(translate(kicked.keyframes[0]), [0, 0, 1, 1]);
    assert.ok(translate(kicked.keyframes[2])[0] < 0);
    assert.equal(translate(motion.squash(a).keyframes[0])[2], 0.97);
    // Three springs on one element, each its own.
    assert.equal(landing.cancelled, false);

    motion.burst(a, a_bit);
    const bits = document.querySelectorAll(".armada-bit");
    assert.equal(bits.length, 3);
    assert.equal(dom.last().options.duration, 280);
    assert.equal(dom.last().options.delay, 40);
    for (const animation of dom.state.animations.slice(-3)) {
        animation.finish();
    }
    await dom.settle();
    assert.equal(document.querySelectorAll(".armada-bit").length, 0);
    assert.equal(motion.shake(a).options.duration, 200);
    assert.equal(motion.refuse(a).options.composite, "add");
});

run_test("the ring: keyboard only, travels between neighbours, follows its control", () => {
    const dom = setup(
        `<button id="a">a</button><button id="b">b</button><button id="far">far</button><textarea id="field"></textarea><div id="plain" tabindex="0" data-noring></div>`,
    );
    const [a, b, far] = [dom.find("#a"), dom.find("#b"), dom.find("#far")];
    dom.place(a, 100, 100, 60, 24);
    dom.place(b, 180, 100, 60, 24);
    b.style.borderTopLeftRadius = "6px";
    dom.place(far, 900, 600, 60, 24);
    const unwire = motion.wire();
    // jsdom computes no custom property; the tokens are the stylesheet's.
    springs.read(token_source());
    // A press focuses without a ring.
    a.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true}));
    dom.click(a);
    const ring = document.querySelector(".armada-ring");
    assert.equal(ring.style.opacity, "0");
    assert.equal(motion.by_keyboard(), false);
    // Tab: the ring arrives from 3 px out, 2 px off its control.
    dom.press("Tab");
    assert.equal(motion.by_keyboard(), true);
    b.focus();
    assert.equal(motion.ring_target(), b);
    // It goes there in the next frame, not in the input's task.
    assert.equal(ring.style.opacity, "0");
    dom.frame();
    assert.equal(ring.style.opacity, "1");
    assert.equal(ring.style.width, "64px");
    assert.equal(ring.style.translate, "178px 98px");
    assert.equal(ring.style.borderRadius, "8px");
    assert.deepEqual(translate(dom.last().keyframes[0]).slice(0, 2), [-3, -3]);
    // A neighbour: it travels.
    dom.advance(1000);
    a.focus();
    dom.frame();
    assert.deepEqual(translate(dom.last().keyframes[0]).slice(0, 2), [80, 0]);
    // Beyond 160 px it does not cross the window.
    dom.advance(1000);
    far.focus();
    dom.frame();
    assert.deepEqual(translate(dom.last().keyframes[0]).slice(0, 2), [-3, -3]);
    // It follows its control when layout moves it, and not otherwise.
    dom.frame();
    assert.equal(ring.style.translate, "898px 598px");
    dom.place(far, 900, 500, 60, 24);
    dom.frame();
    assert.equal(ring.style.translate, "898px 498px");
    // Once its place has held for two frames it stops reading; a resize of
    // its control, of the window, or a scroll wakes it again.
    dom.frame();
    dom.frame();
    assert.equal(dom.state.frames.length, 0);
    dom.place(far, 900, 520, 60, 24);
    dom.resize(far);
    dom.frame();
    assert.equal(ring.style.translate, "898px 518px");
    dom.frame();
    dom.frame();
    assert.equal(dom.state.frames.length, 0);
    dom.place(far, 900, 540, 60, 24);
    dom.window.dispatchEvent(new dom.window.Event("scroll"));
    dom.window.dispatchEvent(new dom.window.Event("resize"));
    assert.equal(dom.state.frames.length, 1);
    dom.frame();
    assert.equal(ring.style.translate, "898px 538px");
    // A field shows its caret, not the ring; nor does what opts out.
    dom.find("#field").focus();
    assert.equal(motion.ring_target(), null);
    dom.find("#plain").focus();
    assert.equal(motion.ring_target(), null);
    dom.frame();
    // Arrow keys count as the keyboard; focus leaving the window clears it.
    dom.press("ArrowDown");
    dom.press("x");
    far.focus();
    assert.equal(motion.ring_target(), far);
    far.blur();
    assert.equal(motion.ring_target(), null);
    far.focus();
    far.remove();
    dom.frame();
    assert.equal(motion.ring_target(), null);
    motion.focus_by_keyboard(a);
    assert.equal(motion.ring_target(), a);
    assert.equal(motion.by_keyboard(false), false);
    dom.window.dispatchEvent(new dom.window.Event("blur"));
    assert.equal(motion.ring_target(), null);
    dom.frame();
    assert.equal(dom.resize_observers(), 0);
    unwire();
    b.focus();
    assert.equal(motion.ring_target(), null);
});

run_test("the ring wakes when the document changes, not when it writes itself", async () => {
    const dom = setup(
        `<button id="a">a</button><p id="above"></p><div popover><button id="in">in</button></div>`,
    );
    const a = dom.find("#a");
    dom.place(a, 100, 100, 60, 24);
    const unwire = motion.wire();
    springs.read(token_source());
    motion.focus_by_keyboard(a);
    dom.frame();
    dom.frame();
    dom.frame();
    assert.equal(dom.state.frames.length, 0);
    // Its own writes (a fade, a place) wake nothing.
    motion.ring(a);
    dom.frame();
    dom.frame();
    dom.frame();
    assert.equal(dom.state.frames.length, 0);
    await dom.settle();
    assert.equal(dom.state.frames.length, 0);
    // Something written above it may have moved it.
    dom.place(a, 100, 140, 60, 24);
    dom.find("#above").textContent = "a line";
    await dom.settle();
    assert.equal(dom.state.frames.length, 1);
    // It moved, so it reads again in the next frame, then rests.
    dom.frame();
    assert.equal(dom.state.frames.length, 1);
    dom.frame();
    dom.frame();
    assert.equal(dom.state.frames.length, 0);
    // Over a control in the top layer it is shown again, so it stands over it.
    const inside = dom.find("#in");
    dom.place(inside, 300, 300, 60, 24);
    motion.focus_by_keyboard(inside);
    assert.equal(motion.ring_target(), inside);
    motion.ring(null);
    dom.frame();
    // The development guard sees the same writes, and reads them only
    // when something measures.
    const unwatch = motion.watch();
    dom.find("#above").textContent = "another line";
    await dom.settle();
    unwatch();
    unwire();
});

run_test("the tooltip waits before the first, never between neighbours", () => {
    const dom = setup(
        `<button id="a" data-tip="Stop the run" data-key="⌘."><i id="in">x</i></button><button id="b" data-tip="Checklist">b</button><button id="c" data-tip="Far">c</button><p id="none">n</p><div popover><button id="d" data-tip="In a menu">d</button></div>`,
    );
    const timers = [];
    const {set_global} = require("../lib/namespace.cjs");
    set_global("setTimeout", (callback, ms) => {
        timers.push({callback, ms});
        return timers.length;
    });
    set_global("clearTimeout", (id) => {
        if (timers[id - 1]) {
            timers[id - 1].cleared = true;
        }
    });
    const [a, b, c] = [dom.find("#a"), dom.find("#b"), dom.find("#c")];
    dom.place(a, 100, 100, 40, 24);
    dom.place(b, 150, 100, 40, 24);
    dom.place(c, 900, 100, 40, 24);
    const unwire = motion.wire();
    // jsdom computes no custom property; the tokens are the stylesheet's.
    springs.read(token_source());
    const over = (target) =>
        target.dispatchEvent(new dom.window.MouseEvent("pointerover", {bubbles: true}));
    over(dom.find("#in"));
    assert.equal(document.querySelector(".armada-tip"), null);
    assert.equal(timers[0].ms, 500);
    timers[0].callback();
    const tip = document.querySelector(".armada-tip");
    assert.equal(tip.textContent, "Stop the run⌘.");
    assert.equal(tip.style.opacity, "1");
    assert.deepEqual(translate(dom.last().keyframes[0]).slice(0, 2), [0, 4]);
    over(a);
    // A neighbour: at once, and it travels.
    over(b);
    assert.equal(tip.textContent, "Checklist");
    assert.equal(tip.querySelector("kbd"), null);
    assert.equal(translate(dom.last().keyframes[0])[0], -50);
    over(c);
    assert.deepEqual(translate(dom.last().keyframes[0]).slice(0, 2), [0, 4]);
    // It is a manual popover, shown again over a control in the top layer.
    assert.equal(tip.getAttribute("popover"), "manual");
    dom.place(dom.find("#d"), 300, 300, 40, 24);
    over(dom.find("#d"));
    assert.equal(tip.textContent, "In a menu");
    assert.ok("shown" in tip.dataset);
    // Leaving, and coming back within the linger: no wait.
    over(dom.find("#none"));
    assert.equal(tip.style.opacity, "0");
    over(dom.find("#none"));
    dom.advance(100);
    over(a);
    assert.equal(tip.style.opacity, "1");
    over(document);
    dom.advance(1000);
    motion.tip(b, {now: true});
    assert.equal(tip.textContent, "Checklist");

    // Once, as a short confirmation; the control's own tip is kept.
    motion.say(a, "Copied");
    assert.equal(tip.textContent, "Copied");
    assert.equal(a.dataset.tip, "Stop the run");
    assert.equal(a.dataset.key, "⌘.");
    timers.at(-1).callback();
    assert.equal(tip.style.opacity, "0");
    const bare = dom.find("#none");
    dom.place(bare, 0, 0, 10, 10);
    motion.say(bare, "Copied", 10);
    assert.equal("tip" in bare.dataset, false);
    dom.click(a);
    unwire();
});

run_test("warm pays for the first animation at idle", () => {
    const dom = setup();
    motion.warm();
    assert.equal(document.querySelector("i[popover]"), null);
    dom.window.HTMLElement.prototype.showPopover = () => {
        throw new Error("none");
    };
    motion.warm();
});

run_test("a read after a write in an input task throws, in development", () => {
    const dom = setup();
    const a = dom.find("#a");
    dom.place(a, 0, 0, 10, 10);
    const records = [];
    class Observer {
        observe() {}
        disconnect() {}
        takeRecords() {
            return records.splice(0);
        }
    }
    const {set_global} = require("../lib/namespace.cjs");
    set_global("MutationObserver", Observer);
    new Observer().observe();
    // Not watching: nothing is checked.
    motion.rest(a);
    const unwatch = motion.watch();
    records.push("stale");
    dom.window.dispatchEvent(new dom.window.Event("pointerdown"));
    // A read before the first write is the rule's first clause.
    motion.rest(a);
    records.push("a write");
    assert.throws(() => motion.rest(a), /motion\.rest read layout after a write in an input task/);
    assert.throws(() => motion.thumb(a, a), /motion\.thumb/);
    // The after-layout phase reads freely, and may nest.
    motion.after_layout(() => {
        motion.after_layout(() => {});
        motion.rest(a);
    });
    assert.throws(() => motion.burst(a, a_bit), /motion\.burst/);
    // The next frame ends the input task.
    dom.frame();
    records.push("a write outside an input task");
    motion.rest(a);
    unwatch();
    dom.frame();
    dom.window.dispatchEvent(new dom.window.Event("keydown"));
    records.push("unwatched");
    motion.rest(a);
});
