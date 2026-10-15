import assert from "node:assert/strict";
import {test} from "node:test";

import {classify, format_value, rewrite_known, stale_ids} from "../lib/classify.mjs";

function assertion(id, value, pass, {operator = "<=", threshold = 0, unit = "", note = ""} = {}) {
    return {id, value, operator, threshold, unit, pass, note};
}

function run(result, known) {
    const lines = [];
    const seen = new Set();
    const counts = classify(result, known, seen, (...line) => lines.push(line));
    return {counts, lines, seen};
}

test("format_value", () => {
    assert.equal(format_value(null, "px"), "n/a");
    assert.equal(format_value(3, "px"), "3px");
});

test("classify gives every assertion one status and prints one line each", () => {
    const known = {
        "c/fixed": {measured: 4, unit: "px"},
        "c/fixed-no-unit": {measured: 4},
        "c/gone": {measured: 4},
        "c/gone-before": {measured: null},
        "c/worse": {measured: 4, max: 5},
        "c/within": {measured: 4, max: 5},
        "c/known": {measured: 4},
    };
    const result = {
        assertions: [
            assertion("c/pass", 0, true, {unit: "px", note: "fine"}),
            assertion("c/fixed", 0, true, {unit: "px"}),
            assertion("c/fixed-no-unit", 0, true),
            assertion("c/gone", null, false, {note: "not measurable"}),
            assertion("c/gone-before", null, false),
            assertion("c/worse", 6, false),
            assertion("c/within", 5, false),
            assertion("c/known", 9, false),
            assertion("c/new", 1, false),
        ],
        measurements: [
            {id: "c/info", value: 3, unit: "ms", note: "median"},
            {id: "c/info-bare", value: null, unit: "", note: ""},
        ],
    };
    const {counts, lines, seen} = run(result, known);
    assert.deepEqual(counts, {pass: 1, known_fail: 3, fail: 3, fixed: 2});
    assert.deepEqual(
        result.assertions.map((entry) => entry.status),
        [
            "PASS",
            "FIXED",
            "FIXED",
            "FAIL",
            "KNOWN-FAIL",
            "FAIL",
            "KNOWN-FAIL",
            "KNOWN-FAIL",
            "FAIL",
        ],
    );
    assert.equal(seen.size, 9);
    assert.deepEqual(lines[0], ["PASS", "c/pass", "0px <= 0px  (fine)"]);
    assert.match(lines[1][2], /\[was 4px; remove it from known.json/);
    assert.match(lines[2][2], /\[was 4; remove it/);
    assert.match(lines[3][2], /known failure is no longer measurable/);
    assert.match(lines[5][2], /known at most 5; it got worse/);
    assert.deepEqual(lines[8], ["FAIL", "c/new", "1 <= 0"]);
    assert.deepEqual(lines[9], ["INFO", "c/info", "3ms  (median)"]);
    assert.deepEqual(lines[10], ["INFO", "c/info-bare", "n/a"]);
});

test("stale_ids lists entries of checks that ran without error but did not report them", () => {
    const known = {"a/x": {}, "a/y": {}, "b/z": {}, "c/w": {}, _comment: {}};
    const results = [{name: "a"}, {name: "b", error: "crashed"}, {name: "_comment"}];
    assert.deepEqual(stale_ids(known, results, new Set(["a/x"])), ["a/y"]);
});

function totals() {
    return {pass: 0, known_fail: 0, fail: 0, fixed: 0, error: 0, stale: 0};
}

test("rewrite_known with --update-known drops the fixed and the stale, refreshes the known", () => {
    const known = {
        "c/z-known": {measured: 4, max: 9, audit: "why"},
        "c/fixed": {measured: 1},
        "c/stale": {measured: 1},
    };
    const results = [
        {
            assertions: [
                {...assertion("c/z-known", 6, false), status: "KNOWN-FAIL"},
                {...assertion("c/fixed", 0, true), status: "FIXED"},
                {...assertion("c/new", 3, false), status: "FAIL"},
                {...assertion("c/pass", 0, true), status: "PASS"},
            ],
        },
    ];
    const counts = {...totals(), fixed: 1, stale: 1, known_fail: 1, fail: 1};
    const next = rewrite_known(
        known,
        results,
        ["c/stale"],
        {update_known: true},
        counts,
        "2026-10-15",
    );
    assert.deepEqual(next, {"c/z-known": {measured: 6, max: 9, audit: "why"}});
    assert.deepEqual(counts, {...totals(), pass: 1, known_fail: 1, fail: 1});
    assert.equal(results[0].assertions[1].status, "PASS");
    assert.equal(results[0].assertions[2].status, "FAIL");
});

test("classify measures getting worse from the threshold, whichever way it fails", () => {
    const known = {
        "c/floor": {measured: 6, max: 5},
        "c/flag": {measured: 0, max: 0},
        "c/count": {measured: 4, max: 4},
    };
    const result = {
        assertions: [
            assertion("c/floor", 4, false, {operator: ">=", threshold: 10}),
            assertion("c/flag", 0, false, {operator: "==", threshold: 1}),
            assertion("c/count", 3, false, {operator: "==", threshold: 6}),
        ],
        measurements: [],
    };
    run(result, known);
    assert.deepEqual(
        result.assertions.map((entry) => entry.status),
        ["FAIL", "KNOWN-FAIL", "FAIL"],
    );
});

test("rewrite_known with --accept-failures records new failures and keeps what it knew", () => {
    const known = {
        "c/b-grew": {measured: 2, max: 3, owner: "B3", audit: "old audit", since: "2026-10-01"},
        "c/a-flat": {measured: 2, max: 3, audit: "flat"},
        "c/d-within": {measured: 2, max: 3, owner: "C1", audit: "within", since: "2026-10-02"},
    };
    const results = [
        {
            assertions: [
                {...assertion("c/b-grew", 5, false), status: "FAIL"},
                {...assertion("c/a-flat", null, false), status: "FAIL"},
                {...assertion("c/c-new", 7, false, {unit: "px", operator: "=="}), status: "FAIL"},
                {...assertion("c/d-within", 1, false), status: "FAIL"},
                {...assertion("c/e-unmeasured", null, false), status: "FAIL"},
                {...assertion("c/fixed", 0, true), status: "FIXED"},
            ],
        },
    ];
    const counts = {...totals(), fail: 5, fixed: 1, stale: 1};
    const next = rewrite_known(
        known,
        results,
        ["c/stale"],
        {accept_failures: true},
        counts,
        "2026-10-15",
    );
    assert.deepEqual(Object.keys(next), [
        "c/a-flat",
        "c/b-grew",
        "c/c-new",
        "c/d-within",
        "c/e-unmeasured",
    ]);
    assert.deepEqual(next["c/b-grew"], {
        measured: 5,
        unit: "",
        threshold: "<= 0",
        max: 5,
        owner: "B3",
        audit: "old audit",
        since: "2026-10-01",
    });
    assert.deepEqual(next["c/a-flat"], {
        measured: null,
        unit: "",
        threshold: "<= 0",
        max: 3,
        owner: "TODO: the package that turns it to FIXED",
        audit: "flat",
        since: "2026-10-15",
    });
    assert.deepEqual(next["c/c-new"], {
        measured: 7,
        unit: "px",
        threshold: "== 0",
        max: 7,
        owner: "TODO: the package that turns it to FIXED",
        audit: "TODO: say which audit or commit explains this failure",
        since: "2026-10-15",
    });
    assert.equal(next["c/d-within"].max, 3);
    assert.equal("max" in next["c/e-unmeasured"], false);
    assert.deepEqual(counts, {...totals(), known_fail: 5, fixed: 1, stale: 1});
});
