"use strict";

// A global time in a message, as the interim beta draws it
// (web/src/armada/beta/parts.js). Zulip renders `<time:...>` as a <time>
// element holding the moment in UTC, and every reader's page writes it in
// their own time zone. When the Codex account reached its usage limit on
// runix.chat, Onix's message said "until Fri 23 Oct, 2:16 pm", Codex's
// UTC read as anyone's local time; it now writes the reset as a global
// time. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

real_dom.make_real_dom("");
set_global("M", {T: {}});
const AGENT = 9;
mock_esm("../../src/armada/beta/faces.js", {isOnix: (id) => id === AGENT});

const parts = zrequire("armada/beta/parts.js");

// The run's message as the server renders it.
const notice = {
    id: 77,
    sender_id: AGENT,
    content:
        "<p>Onix can't work right now: the Codex account has reached its usage limit, " +
        'until <time datetime="2026-10-23T14:16:00Z">2026-10-23T14:16:00Z</time>.</p>',
};
// Onix's words as they stand on the page, read in time zone `zone` on
// 17 October 2026.
function wordsIn(zone) {
    const was = process.env.TZ;
    const then = Date.now();
    process.env.TZ = zone;
    clock.setSystemTime(new Date("2026-10-17T06:00:00Z"));
    try {
        const [words] = parts.partsOf(notice);
        return parts.partEl(words, notice).textContent;
    } finally {
        // The zone (tools/test-js-with-node sets UTC) and the clock as they
        // were, for the tests after these.
        process.env.TZ = was;
        clock.setSystemTime(then);
    }
}

run_test("a reset time is drawn in each reader's own time zone", () => {
    assert.equal(
        wordsIn("Asia/Kolkata"),
        "Onix can't work right now: the Codex account has reached its usage limit, until 23 Oct 19:46.",
    );
    assert.equal(
        wordsIn("UTC"),
        "Onix can't work right now: the Codex account has reached its usage limit, until 23 Oct 14:16.",
    );
});
