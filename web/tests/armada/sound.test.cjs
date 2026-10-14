"use strict";

const assert = require("node:assert/strict");

const {set_global, zrequire} = require("../lib/namespace.cjs");
const {make_real_dom} = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const recipes = zrequire("armada/sound/recipes.ts");
const sound = zrequire("armada/sound/sound.ts");

// An audio context that records what it was asked to play.
function fake_audio() {
    const made = {
        contexts: 0,
        started: [],
        stopped: [],
        faded: [],
        resumed: 0,
        closed: 0,
        buffers: 0,
        state: "running",
    };
    class Context {
        constructor(options) {
            made.contexts += 1;
            made.rate = options.sampleRate;
            this.currentTime = 10;
            this.destination = {};
        }
        get state() {
            return made.state;
        }
        async resume() {
            made.resumed += 1;
        }
        async close() {
            made.closed += 1;
        }
        createBuffer(_channels, length) {
            made.buffers += 1;
            return {length, copyToChannel() {}};
        }
        createBufferSource() {
            const source = {
                connect: (node) => node,
                start(when) {
                    made.started.push({length: source.buffer.length, when});
                },
                stop(when) {
                    made.stopped.push(when);
                },
            };
            return source;
        }
        createGain() {
            return {
                connect: (node) => node,
                gain: {
                    value: 1,
                    setTargetAtTime(value) {
                        made.faded.push(value);
                    },
                },
                made,
            };
        }
    }
    set_global("AudioContext", Context);
    return made;
}

// Idle time, run when the test says so.
function fake_idle() {
    const waiting = [];
    set_global("requestIdleCallback", (callback) => {
        waiting.push(callback);
    });
    return {
        waiting,
        run_one() {
            waiting.shift()();
        },
    };
}

function setup() {
    const clock = {now: 100000};
    set_global("performance", {now: () => clock.now});
    const made = fake_audio();
    fake_idle();
    sound.set_muted(true);
    sound.set_muted(false, {gesture: true});
    sound.set_full(false);
    sound.set_level(0);
    sound.clear();
    return {clock, made};
}

const reasons = () => sound.log().map((line) => `${line.name}: ${line.why || "played"}`);

run_test("every sound sits within 0.1 dB of its tier", () => {
    const table = recipes.table();
    assert.deepEqual(
        table.filter((row) => row.set === "default").map((row) => row.name),
        ["send", "steer", "nope", "ask", "fail", "done", "done-short", "mention", "react"],
    );
    assert.deepEqual(
        table.filter((row) => row.set === "full").map((row) => row.name),
        ["ack", "arrive"],
    );
    for (const row of table) {
        assert.ok(Math.abs(row.rms100 - row.tier) <= 0.1, `${row.name}: ${row.rms100}`);
        assert.ok(row.peak < 0, `${row.name} clips`);
        assert.ok(
            row.milliseconds > 40 && row.milliseconds < 700,
            `${row.name}: ${row.milliseconds}`,
        );
    }
    // What I did is quiet; what needs me is not.
    assert.equal(recipes.recipes.send.tier, -32);
    assert.equal(recipes.recipes.mention.tier, -20);
    assert.equal(recipes.gain_of(-20), 0.1);
});

run_test("a sound is the same samples every time it is rendered", () => {
    const [first, second] = [recipes.render("nope"), recipes.render("nope")];
    assert.equal(first.length, Math.round(0.8 * recipes.SAMPLE_RATE));
    assert.deepEqual(first.slice(0, 2000), second.slice(0, 2000));
    assert.ok(recipes.loudest_100ms(new Float32Array(10000)) === 0);
});

run_test("the engine does not exist while muted, or before a gesture", () => {
    const clock = {now: 0};
    set_global("performance", {now: () => clock.now});
    const made = fake_audio();
    fake_idle();
    sound.set_muted(true);
    sound.clear();
    assert.equal(sound.is_muted(), true);
    assert.equal(sound.state(), "none");
    assert.equal(sound.play("send"), false);
    // Unmuted with no gesture: still nothing.
    sound.set_muted(false);
    assert.equal(made.contexts, 0);
    assert.equal(sound.play("send"), false);
    // The first gesture creates it, and wakes it if it sleeps.
    const dom = make_real_dom();
    sound.arm();
    made.state = "suspended";
    dom.press("a");
    assert.equal(made.contexts, 1);
    assert.equal(made.rate, 44100);
    assert.equal(made.resumed, 1);
    assert.equal(sound.state(), "suspended");
    // A sleeping context is asked to wake, and nothing is played late.
    assert.equal(sound.play("send"), false);
    assert.equal(made.resumed, 2);
    assert.deepEqual(reasons(), ["send: context suspended"]);
    made.state = "running";
    // Muted while armed: the gesture creates nothing.
    sound.set_muted(true);
    assert.equal(made.closed, 1);
    sound.arm(dom.document);
    dom.document.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true}));
    assert.equal(made.contexts, 1);
    sound.set_muted(false, {gesture: true});
    sound.set_muted(false, {gesture: true});
    assert.equal(made.contexts, 2);
});

run_test(
    "the gesture only creates the context; sounds render when idle or when first played",
    () => {
        set_global("performance", {now: () => 0});
        const made = fake_audio();
        const idle = fake_idle();
        sound.set_muted(true);
        sound.set_muted(false);
        sound.clear();
        const dom = make_real_dom();
        sound.arm();
        dom.press("Enter");
        assert.equal(made.contexts, 1);
        assert.equal(made.buffers, 0);
        // A sound wanted before idle time is rendered then, once.
        assert.equal(sound.play("nope", 0, 0), true);
        assert.equal(made.buffers, 1);
        assert.equal(sound.play("nope", 0, 0), true);
        assert.equal(made.buffers, 1);
        // Idle time renders the rest, one a slice, my own send first.
        assert.equal(idle.waiting.length, 1);
        idle.run_one();
        assert.equal(made.buffers, 2);
        while (idle.waiting.length > 0) {
            idle.run_one();
        }
        assert.equal(made.buffers, recipes.sound_names.length);
        // A context closed while slices wait renders nothing more.
        sound.set_muted(true);
        sound.set_muted(false, {gesture: true});
        assert.equal(made.buffers, recipes.sound_names.length);
        sound.set_muted(true);
        idle.run_one();
        assert.equal(made.buffers, recipes.sound_names.length);
        assert.equal(idle.waiting.length, 0);
        // Without idle callbacks (Safari), a later task.
        set_global("requestIdleCallback", undefined);
        const tasks = [];
        const {setTimeout} = globalThis;
        set_global("setTimeout", (callback) => tasks.push(callback));
        sound.set_muted(false, {gesture: true});
        set_global("setTimeout", setTimeout);
        assert.equal(tasks.length, 1);
        tasks[0]();
        assert.equal(made.buffers, recipes.sound_names.length + 1);
        sound.set_muted(true);
    },
);

run_test("a sound starts with its event or not at all", () => {
    const {clock, made} = setup();
    assert.equal(sound.play("send", 0, clock.now - 3), true);
    assert.equal(made.started.length, 1);
    assert.ok(Math.abs(made.started[0].when - 10.002) < 1e-9);
    assert.equal(sound.play("ask", 0.12, clock.now - 31), false);
    assert.deepEqual(reasons(), ["send: played", "ask: late 31 ms"]);
    // Full adds two sounds; by default they are silent.
    assert.equal(sound.play("ack"), false);
    sound.set_full(true);
    assert.equal(sound.play("ack"), true);
    sound.set_level(12);
    assert.equal(sound.play("arrive"), true);
});

run_test("the limiter drops a fourth sound in 2 s, never my own action", () => {
    const {clock, made} = setup();
    assert.equal(sound.play("done"), true);
    assert.equal(sound.play("ask"), true);
    assert.equal(sound.play("done-short"), true);
    // A newcomer that is the lowest is not played.
    assert.equal(sound.play("done-short"), false);
    // Mine always sounds, and is not counted.
    assert.equal(sound.play("send"), true);
    assert.equal(sound.play("react"), true);
    assert.equal(sound.play("nope"), true);
    // A higher one takes the lowest's place: done-short gives way.
    assert.equal(sound.play("mention"), true);
    assert.deepEqual(made.faded, [0]);
    assert.equal(made.stopped.length, 1);
    assert.deepEqual(reasons().slice(-6), [
        "done-short: limiter: lowest priority",
        "send: played",
        "react: played",
        "nope: played",
        "done-short: gave way to mention",
        "mention: played",
    ]);
    // The one that gave way no longer counts; one that has ended is not
    // faded, only replaced.
    clock.now += 500;
    assert.equal(sound.play("fail"), true);
    assert.equal(made.stopped.length, 1);
    // Two seconds on, the window is clear.
    clock.now += 2001;
    assert.equal(sound.play("done-short"), true);
    for (let n = 0; n < 420; n += 1) {
        sound.play("react");
    }
    assert.equal(sound.log().length, 400);
});

run_test("under a fast exchange the consequent goes quiet; repeats fall", () => {
    const {clock, made} = setup();
    const gains = [];
    const context_gain = () => made.started.length;
    for (const name of ["send", "steer", "send", "send"]) {
        sound.play(name);
        gains.push(context_gain());
        clock.now += 300;
    }
    assert.equal(gains.at(-1), 4);
    // From the third send inside 4 s, done-short is silent.
    assert.equal(sound.play("done-short"), false);
    assert.equal(reasons().at(-1), "done-short: fast exchange");
    // Until there have been 4 s without a send.
    clock.now += 4001;
    assert.equal(sound.play("done-short"), true);
    sound.play("send");
    sound.play("send");
    sound.play("send");
    clock.now += 4001;
    sound.play("send");
    assert.equal(sound.play("done-short"), true);
});
