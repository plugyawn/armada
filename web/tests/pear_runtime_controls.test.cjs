"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

// Pure helpers extracted from pear_panel into the topbar runtime chip.
const pear_runtime_controls = zrequire("pear_runtime_controls");

run_test("model_reasoning_efforts resolves per model, then falls back", () => {
    const catalog = [
        {
            slug: "gpt-5.5",
            display_name: "GPT-5.5",
            supported_reasoning_levels: [
                {effort: "medium", label: "Medium"},
                {effort: "high", label: "High"},
            ],
        },
        {slug: "haiku", display_name: "Haiku", supported_reasoning_levels: []},
    ];
    const ctx = {
        model: "gpt-5.5",
        model_catalog: catalog,
        reasoning_efforts: [{effort: "low", label: "Low"}],
    };

    // A model with its own supported levels wins.
    assert.deepEqual(
        pear_runtime_controls.model_reasoning_efforts(ctx, "gpt-5.5").map((e) => e.effort),
        ["medium", "high"],
    );
    // A model with an empty level list falls back to topic reasoning_efforts.
    assert.deepEqual(
        pear_runtime_controls.model_reasoning_efforts(ctx, "haiku").map((e) => e.effort),
        ["low"],
    );
    // No catalog match and no topic efforts -> the built-in fallback list.
    assert.deepEqual(
        pear_runtime_controls
            .model_reasoning_efforts({model: "x", model_catalog: [], reasoning_efforts: []}, "x")
            .map((e) => e.effort),
        ["low", "medium", "high", "xhigh"],
    );
    // Nullish context -> fallback list, no throw.
    assert.deepEqual(
        pear_runtime_controls
            .model_reasoning_efforts(undefined, undefined)
            .map((e) => e.effort),
        ["low", "medium", "high", "xhigh"],
    );
});

run_test("effort_label and chip_label format the runtime chip", () => {
    const ctx = {
        model: "gpt-5.5",
        model_catalog: [
            {
                slug: "gpt-5.5",
                display_name: "GPT-5.5",
                supported_reasoning_levels: [
                    {effort: "medium", label: "Medium"},
                    {effort: "high", label: "High"},
                ],
            },
        ],
        reasoning_effort: "high",
        reasoning_efforts: [],
    };

    assert.equal(pear_runtime_controls.effort_label(ctx), "High");
    assert.equal(pear_runtime_controls.chip_label(ctx), "◇ GPT-5.5 · High");

    // An effort absent from the resolved list shows its raw slug.
    assert.equal(pear_runtime_controls.effort_label({...ctx, reasoning_effort: "weird"}), "weird");

    // A model absent from the catalog shows its raw slug; "high" resolves
    // to the fallback list's "High" label.
    assert.equal(
        pear_runtime_controls.chip_label({...ctx, model: "custom-model", model_catalog: []}),
        "◇ custom-model · High",
    );
});
