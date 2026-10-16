"use strict";

// The interim beta's sound engine (web/src/armada/beta/kit/sound.js): it
// runs only while sound is on. Turning sound off suspends the audio
// context, turning it on again resumes the same one, and the page going
// away closes it. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom("");
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));

// An audio context that says what was asked of it.
const contexts = [];
set_global(
    "AudioContext",
    class {
        state = "running";
        currentTime = 0;
        destination = {};
        calls = [];
        constructor() {
            contexts.push(this);
        }
        suspend() {
            this.calls.push("suspend");
            this.state = "suspended";
        }
        resume() {
            this.calls.push("resume");
            this.state = "running";
        }
        close() {
            this.calls.push("close");
            this.state = "closed";
        }
        createBuffer(_channels, length) {
            return {length, copyToChannel() {}};
        }
        createBufferSource() {
            return {connect: (node) => node, start() {}, stop() {}};
        }
        createGain() {
            return {connect: (node) => node, gain: {value: 1}};
        }
    },
);

zrequire("armada/beta/kit/sound.js");
const {S} = dom.window;

run_test("no engine until sound is turned on", () => {
    assert.equal(contexts.length, 0);
    assert.equal(S.play("mention"), false);
});

run_test("off suspends the engine, on resumes the same one", () => {
    S.set(true);
    assert.equal(contexts.length, 1);
    assert.equal(S.state(), "running");
    S.set(false);
    assert.equal(S.state(), "suspended");
    assert.equal(S.play("mention"), false, "nothing plays while off");
    S.set(true);
    assert.equal(contexts.length, 1);
    assert.equal(S.state(), "running");
    assert.deepEqual(contexts[0].calls, ["suspend", "resume"]);
});

run_test("the page going away closes the engine; into the back-forward cache it sleeps", () => {
    dom.window.dispatchEvent(Object.assign(new dom.window.Event("pagehide"), {persisted: true}));
    assert.equal(S.state(), "suspended");
    dom.window.dispatchEvent(Object.assign(new dom.window.Event("pageshow"), {persisted: true}));
    assert.equal(S.state(), "running");
    dom.window.dispatchEvent(Object.assign(new dom.window.Event("pagehide"), {persisted: false}));
    assert.deepEqual(contexts[0].calls, ["suspend", "resume", "suspend", "resume", "close"]);
    assert.equal(S.ctx(), null);
});
