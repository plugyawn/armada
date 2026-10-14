"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {make_motion_dom, token_source} = require("./motion_lib.cjs");

const springs = zrequire("armada/motion/springs.ts");

run_test("the tokens are read once, in milliseconds", () => {
    springs.read(token_source());
    assert.deepEqual(
        springs.spring_names.map((name) => springs.springs[name].duration),
        [210, 340, 440, 360],
    );
    assert.equal(springs.springs.land.damping, 0.72);
    assert.equal(springs.times["fade-in"], 80);
    assert.equal(springs.times.quiet, 40000);
    assert.equal(springs.times.announce, 2000);
    assert.equal(springs.amounts.rise, 8);
    assert.equal(springs.amounts.dip, 0.96);
    assert.equal(springs.milliseconds("10s"), 10000);
    assert.equal(springs.milliseconds("210ms"), 210);
    assert.equal(springs.reduced(), false);
});

run_test("reduced motion is the springs collapsed", () => {
    springs.read(token_source({"move-t": "1ms"}));
    assert.equal(springs.reduced(), true);
    springs.read(token_source());
});

run_test("load reads the document's own tokens", () => {
    make_motion_dom();
    springs.load();
    // jsdom computes no custom property: nothing has a time to take, and
    // nothing moves.
    assert.equal(springs.reduced(), true);
    assert.ok(Number.isNaN(springs.springs.move.duration));
    springs.read(token_source());
});

run_test("each spring reproduces its token's linear() string within 0.5%", () => {
    const source = token_source();
    for (const name of springs.spring_names) {
        const stops = source
            .getPropertyValue(`--spring-${name}`)
            .replaceAll(/^linear\(|\)$/g, "")
            .split(",")
            .map((stop) => stop.trim().split(" "));
        let compared = 0;
        for (const [value, at] of stops) {
            if (at === undefined) {
                continue;
            }
            const computed = springs.progress(springs.springs[name], Number.parseFloat(at) / 100);
            assert.ok(
                Math.abs(computed - Number(value)) <= 0.005,
                `${name} at ${at}: ${computed} against ${value}`,
            );
            compared += 1;
        }
        assert.ok(compared >= 13);
        assert.equal(springs.progress(springs.springs[name], 1), 1);
    }
    // Only the spring for textless objects passes its mark.
    const peak = (name) =>
        Math.max(
            ...Array.from({length: 100}, (_, i) =>
                springs.progress(springs.springs[name], i / 100),
            ),
        );
    assert.ok(peak("land") > 1.03 && peak("land") < 1.045);
    assert.ok(peak("move") <= 1);
});

run_test("a spring carries its speed", () => {
    const flight = springs.flight(springs.springs.move, {x: 100}, {x: 0});
    const [early, speed] = springs.sample(flight, 0.05);
    assert.ok(early.x < 100 && early.x > 0);
    assert.ok(speed.x < 0);
    const bouncy = springs.flight(springs.springs.land, {s: 1}, {});
    const [, landing] = springs.sample(bouncy, 0.05);
    assert.ok(landing.s < 0);
});
