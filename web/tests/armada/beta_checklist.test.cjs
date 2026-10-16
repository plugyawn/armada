"use strict";

// The interim beta's checklist of a run (web/src/armada/beta/checklist.js),
// as the run's turn, the column and the header show it, with a real DOM.
// Its count is the steps Codex marked completed, so a step's mark is a tick
// only when it was completed: a run that ends with steps unfinished leaves
// the step it was on with the dashed ring (it waits for a next turn) and the
// steps it never reached with the ring. Production's runs on runix.chat
// ended so ("0 of 3" with a tick, "1 of 4" with two). Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

real_dom.make_real_dom(`<section id="turn"></section>`);
set_global("M", {T: {}, reduced: () => true});
set_global("smk", (state) => `<span class="mk sm" data-s="${state}"></span>`);
mock_esm("../../src/armada/beta/frame.js", {side: null});

const checklist = zrequire("armada/beta/checklist.js");

const turn = document.querySelector("#turn");
const step = (words, status) => ({step: words, status});
// Production's run 2 in #ops > file-backup, as it ended: done, with the
// step it was on still in progress and two never reached.
const ended = (status) => ({
    id: 2,
    status,
    date_created: 1000,
    date_finished: 1030,
    plan: [
        step("Inspect the checkout and remote", "completed"),
        step("Obtain the runbook and backup host details", "in_progress"),
        step("Diagnose and recover the missed backups", "pending"),
        step("Verify the snapshots", "pending"),
    ],
});
// The checklist in the run's turn, read back: its caption, and each step's
// state as its mark shows it.
function turnChecklist(run) {
    checklist.drawTurnPlan(turn, run);
    const heading = turn.querySelector('[aria-label="Checklist"]').firstElementChild;
    return {
        cap: heading.firstElementChild.textContent,
        count: heading.lastElementChild.textContent,
        marks: [...turn.querySelectorAll("ol > li")].map((li) => li.firstElementChild.dataset.s),
    };
}

run_test("a run that ended with steps unfinished ticks only the completed ones", () => {
    const run = ended("done");
    assert.deepEqual(turnChecklist(run), {
        cap: "Checklist",
        count: "1 of 4",
        marks: ["done", "queued", "left", "left"],
    });
    // The header and the column draw the same marks, under the same count.
    const shown = checklist.progOf({runs: [run]});
    assert.deepEqual(shown.marks, ["done", "queued", "left", "left"]);
    assert.equal(shown.cap, "1 of 4");
});

run_test("a run that ended on its first step, unfinished, ticks nothing", () => {
    turn.replaceChildren();
    const run = {
        ...ended("done"),
        plan: [
            step("Inspect the runbook", "in_progress"),
            step("Diagnose the missed backups", "pending"),
            step("Verify the recovery", "pending"),
        ],
    };
    assert.deepEqual(turnChecklist(run), {
        cap: "Checklist",
        count: "0 of 3",
        marks: ["queued", "left", "left"],
    });
});

run_test("a live run's step in hand is live; a failed or stopped run marks it so", () => {
    assert.deepEqual(turnChecklist(ended("running")).marks, ["done", "live", "left", "left"]);
    assert.deepEqual(turnChecklist(ended("failed")).marks, ["done", "failed", "left", "left"]);
    assert.deepEqual(turnChecklist(ended("cancelled")).marks, [
        "done",
        "cancelled",
        "cancelled",
        "cancelled",
    ]);
});

run_test("a run that finished every step says when it finished", () => {
    const run = ended("done");
    for (const s of run.plan) {
        s.status = "completed";
    }
    const shown = checklist.progOf({runs: [run]});
    assert.deepEqual(shown.marks, ["done", "done", "done", "done"]);
    assert.match(shown.cap, /^Finished \d{1,2}:\d\d$/);
});
