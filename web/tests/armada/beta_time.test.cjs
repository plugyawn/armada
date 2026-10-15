"use strict";

// The interim beta's day key (web/src/armada/beta/time.js): the feed
// starts a new day and the column writes a run's time by comparing day
// keys, and the feed stamps each turn's key in `data-day`, where whoever
// debugs the page reads it as the date. Times are made in the local time
// zone, as the page makes its keys. The day key reads no state, so the
// page's state module is never loaded here (the tests import lazily).
// Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {dayKey} = zrequire("armada/beta/time.js");

const at = (year, month, day, hour = 12, minute = 0) =>
    new Date(year, month - 1, day, hour, minute).getTime() / 1000;

run_test("a day key reads as the date", () => {
    assert.equal(dayKey(at(2026, 10, 16)), 20261016);
    assert.equal(dayKey(at(2026, 1, 1)), 20260101);
    assert.equal(dayKey(at(2026, 12, 31)), 20261231);
});

run_test("one day has one key, from its first minute to its last", () => {
    assert.equal(dayKey(at(2026, 10, 16, 0, 0)), dayKey(at(2026, 10, 16, 23, 59)));
    assert.notEqual(dayKey(at(2026, 10, 16, 23, 59)), dayKey(at(2026, 10, 17, 0, 0)));
});

run_test("later days have larger keys, across a month and a year", () => {
    assert.ok(dayKey(at(2026, 11, 30)) < dayKey(at(2026, 12, 1)));
    assert.ok(dayKey(at(2026, 12, 31)) < dayKey(at(2027, 1, 1)));
});
