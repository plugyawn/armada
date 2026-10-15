import assert from "node:assert/strict";
import {test} from "node:test";

import {
    describe_rect,
    drift,
    label_drift,
    largest_move,
    max,
    median,
    probe_drift,
    quantile,
    rect_delta,
    round,
    sum,
} from "../lib/stats.mjs";

const box = (x, y, w = 10, h = 10) => ({x, y, w, h});

test("round keeps finite numbers and turns anything else into null", () => {
    assert.equal(round(1.23456), 1.23);
    assert.equal(round(1.23456, 3), 1.235);
    assert.equal(round(null), null);
    assert.equal(round(undefined), null);
    assert.equal(round(Number.NaN), null);
    assert.equal(round(Number.POSITIVE_INFINITY), null);
});

test("quantile and median ignore what is not a number", () => {
    assert.equal(quantile([], 0.5), null);
    assert.equal(quantile([Number.NaN, null], 0.5), null);
    assert.equal(quantile([3, 1, 2, Number.NaN], 0.5), 2);
    assert.equal(quantile([1, 2, 3, 4], 1), 4);
    assert.equal(quantile([5, 1, 9], 0), 1);
    assert.equal(median([4, 1, 3]), 3);
});

test("sum and max", () => {
    assert.equal(sum([]), 0);
    assert.equal(sum([1, 2, 3.5]), 6.5);
    assert.equal(max([]), null);
    assert.equal(max([Number.NaN]), null);
    assert.equal(max([1, Number.NaN, 7, 3]), 7);
});

test("rect_delta is the largest change of an edge or a dimension", () => {
    assert.equal(rect_delta(null, box(0, 0)), null);
    assert.equal(rect_delta(box(0, 0), undefined), null);
    assert.equal(rect_delta(box(0, 0), box(0, 3)), 3);
    assert.equal(rect_delta(box(0, 0, 10, 10), box(1, 0, 10, 40)), 30);
    assert.equal(rect_delta(box(0, 0, 10, 10), box(1, 0, 10, 40), {ignore_height: true}), 1);
});

test("drift measures from the first frame an element appears in", () => {
    assert.equal(drift([{t: 0, rects: {}}], "a"), null);
    const frames = [
        {t: 0, rects: {}},
        {t: 16, rects: {a: box(0, 0)}},
        {t: 32, rects: {}},
        {t: 48, rects: {a: box(0, 5)}},
        {t: 64, rects: {a: box(0, 2, 10, 50)}},
    ];
    assert.deepEqual(drift(frames, "a"), {
        delta: 40,
        vanished: 1,
        first: box(0, 0),
        worst: box(0, 2, 10, 50),
        first_t: 16,
        worst_t: 64,
    });
    assert.equal(drift(frames, "a", {ignore_height: true}).delta, 5);
    assert.equal(drift(frames, "a", {from: 3}).delta, 40);
    assert.equal(drift(frames, "a", {from: 3}).first_t, 48);
    assert.deepEqual(drift(frames.slice(0, 2), "a"), {
        delta: 0,
        vanished: 0,
        first: box(0, 0),
        worst: null,
        first_t: 16,
        worst_t: null,
    });
});

test("describe_rect", () => {
    assert.equal(describe_rect(box(1, 2, 3, 4)), "1,2 3x4");
    assert.equal(describe_rect(null), "absent");
});

test("probe_drift is the largest deviation from the first value", () => {
    assert.equal(probe_drift([{probes: {}}], "p"), null);
    const frames = [
        {probes: {p: null}},
        {probes: {p: 10}},
        {probes: {}},
        {probes: {p: 7}},
        {probes: {p: 12}},
    ];
    assert.deepEqual(probe_drift(frames, "p"), {delta: 3, first: 10, worst: 7});
    assert.deepEqual(probe_drift(frames, "p", {from: 4}), {delta: 0, first: 12, worst: null});
});

test("largest_move compares rows present in both snapshots", () => {
    assert.equal(largest_move(undefined, {}, "y"), null);
    assert.equal(largest_move({a: {y: 1}}, undefined, "y"), null);
    const before = {a: {y: 0}, b: {y: 10}, c: {y: 20}};
    const after = {a: {y: 0}, b: {y: 14.5}, c: {y: 50}};
    assert.deepEqual(largest_move(before, after, "y"), {
        delta: 30,
        label: "c",
        from: 20,
        to: 50,
        compared: 3,
    });
    assert.deepEqual(
        largest_move(before, after, "y", (label) => label !== "c"),
        {delta: 4.5, label: "b", from: 10, to: 14.5, compared: 2},
    );
    assert.deepEqual(largest_move({a: {y: 1}}, {a: {y: 1}}, "y"), {
        delta: 0,
        label: null,
        compared: 1,
    });
});

test("label_drift follows each row from where it first appeared", () => {
    assert.equal(label_drift([{t: 0, probes: {}}], "rows", "y"), null);
    const frames = [
        {t: 0, probes: {rows: {a: {y: 0}, skip: {y: 0}}}},
        {t: 10, probes: {}},
        {t: 20, probes: {rows: {a: {y: 3}, b: {y: 100}, skip: {y: 90}}}},
        {t: 30.4, probes: {rows: {a: {y: 1}, b: {y: 101}}}},
    ];
    const only = (label) => label !== "skip";
    assert.deepEqual(label_drift(frames, "rows", "y", {only}), {
        delta: 3,
        label: "a",
        from: 0,
        to: 3,
        after_ms: 20,
        compared: 2,
    });
    assert.deepEqual(label_drift(frames, "rows", "y", {from: 3}), {
        delta: 0,
        label: null,
        compared: 2,
    });
});
