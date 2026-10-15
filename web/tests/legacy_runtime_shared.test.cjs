"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const runtime_shared = zrequire("legacy_runtime_shared");

run_test("active plan presentation preserves Codex steps and chooses its current status", () => {
    const presentation = runtime_shared.active_plan_presentation([
        {step: "Inspect", status: "completed"},
        {step: "Implement", status: "in_progress"},
        {step: "Verify", status: "pending"},
    ]);

    assert.deepEqual(presentation, {
        kind: "plan",
        current_key: "1:Implement",
        current_index: 1,
        rows: [
            {
                kind: "step",
                index: 0,
                step: "Inspect",
                status: "completed",
                is_current: false,
            },
            {
                kind: "step",
                index: 1,
                step: "Implement",
                status: "current",
                is_current: true,
            },
            {
                kind: "step",
                index: 2,
                step: "Verify",
                status: "pending",
                is_current: false,
            },
        ],
    });
});

run_test("active plan presentation bounds long plans without rewriting them", () => {
    const presentation = runtime_shared.active_plan_presentation([
        {step: "One", status: "completed"},
        {step: "Two", status: "completed"},
        {step: "Three", status: "completed"},
        {step: "Four", status: "in_progress"},
        {step: "Five", status: "pending"},
        {step: "Six", status: "pending"},
    ]);

    assert.equal(presentation.kind, "plan");
    assert.deepEqual(
        presentation.rows.map((row) => (row.kind === "ellipsis" ? "…" : row.step)),
        ["…", "Three", "Four", "Five", "…"],
    );
});

run_test("empty active plan has one neutral Working presentation", () => {
    assert.deepEqual(runtime_shared.active_plan_presentation([]), {
        kind: "working",
        current_key: "working",
        rows: [],
    });
});
