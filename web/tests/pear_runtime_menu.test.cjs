"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pear_runtime_menu = zrequire("pear_runtime_menu");

const fallback_efforts = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

function make_context(overrides = {}) {
    return {
        stream: "devel",
        topic: "pear",
        model: "gpt-5.5",
        reasoning_effort: "medium",
        ...overrides,
    };
}

run_test("model_reasoning_efforts", () => {
    const gpt_efforts = [
        {effort: "medium", label: "Medium"},
        {effort: "xhigh", label: "XHigh"},
    ];
    const context_efforts = [
        {effort: "low", label: "Low"},
        {effort: "high", label: "High"},
    ];
    const context = make_context({
        model_catalog: [
            {slug: "gpt-5.5", display_name: "GPT-5.5", supported_reasoning_levels: gpt_efforts},
            {slug: "haiku", display_name: "Haiku", supported_reasoning_levels: []},
            {slug: "opus", display_name: "Opus"},
        ],
        reasoning_efforts: context_efforts,
    });

    // A model's own supported_reasoning_levels win.
    assert.deepEqual(pear_runtime_menu.model_reasoning_efforts(context, "gpt-5.5"), gpt_efforts);

    // Empty or absent per-model levels fall back to the context-wide
    // reasoning_efforts, as does an unknown model.
    assert.deepEqual(pear_runtime_menu.model_reasoning_efforts(context, "haiku"), context_efforts);
    assert.deepEqual(pear_runtime_menu.model_reasoning_efforts(context, "opus"), context_efforts);
    assert.deepEqual(
        pear_runtime_menu.model_reasoning_efforts(context, "unknown"),
        context_efforts,
    );

    // With no usable context-wide efforts either, the built-in
    // fallback list is used.
    assert.deepEqual(
        pear_runtime_menu.model_reasoning_efforts(make_context({reasoning_efforts: []}), "gpt-5.5"),
        fallback_efforts,
    );
    assert.deepEqual(
        pear_runtime_menu.model_reasoning_efforts(make_context(), "gpt-5.5"),
        fallback_efforts,
    );

    // An undefined context falls back too, without throwing.
    assert.deepEqual(
        pear_runtime_menu.model_reasoning_efforts(undefined, "gpt-5.5"),
        fallback_efforts,
    );
});

run_test("model_choices", () => {
    assert.deepEqual(pear_runtime_menu.model_choices(undefined), []);

    // Catalog entries are mapped to choices, using display_name when
    // available and falling back to the slug; extra model_choices are
    // appended without duplicating catalog slugs.
    const context = make_context({
        model: "gpt-5.5",
        model_catalog: [
            {slug: "gpt-5.5", display_name: "GPT-5.5"},
            {slug: "haiku", display_name: ""},
        ],
        model_choices: ["haiku", "opus"],
    });
    assert.deepEqual(pear_runtime_menu.model_choices(context), [
        {value: "gpt-5.5", label: "GPT-5.5", selected: true},
        {value: "haiku", label: "haiku", selected: false},
        {value: "opus", label: "opus", selected: false},
    ]);

    // A current model that is in neither the catalog nor
    // model_choices is unshifted to the front, selected.
    const custom_context = make_context({
        model: "custom-model",
        model_catalog: [{slug: "gpt-5.5", display_name: "GPT-5.5"}],
        model_choices: ["opus"],
    });
    assert.deepEqual(pear_runtime_menu.model_choices(custom_context), [
        {value: "custom-model", label: "custom-model", selected: true},
        {value: "gpt-5.5", label: "GPT-5.5", selected: false},
        {value: "opus", label: "opus", selected: false},
    ]);
});

run_test("effort_choices", () => {
    assert.deepEqual(pear_runtime_menu.effort_choices(undefined), []);

    const context = make_context({
        model: "gpt-5.5",
        reasoning_effort: "high",
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
    });
    assert.deepEqual(pear_runtime_menu.effort_choices(context), [
        {value: "medium", label: "Medium", selected: false},
        {value: "high", label: "High", selected: true},
    ]);

    // Without a catalog, the built-in fallback efforts are offered,
    // still marking the selected reasoning_effort.
    assert.deepEqual(pear_runtime_menu.effort_choices(make_context({reasoning_effort: "xhigh"})), [
        {value: "low", label: "Low", selected: false},
        {value: "medium", label: "Medium", selected: false},
        {value: "high", label: "High", selected: false},
        {value: "xhigh", label: "XHigh", selected: true},
    ]);
});

run_test("effort_slider_for_testing", () => {
    assert.deepEqual(pear_runtime_menu.effort_slider_for_testing([]), {
        has_efforts: false,
        selected_label: "",
        value: 0,
        max: 0,
    });

    assert.deepEqual(
        pear_runtime_menu.effort_slider_for_testing([
            {value: "low", label: "Low", selected: false},
            {value: "medium", label: "Medium", selected: true},
            {value: "high", label: "High", selected: false},
        ]),
        {
            has_efforts: true,
            selected_label: "Medium",
            value: 1,
            max: 2,
        },
    );

    assert.deepEqual(
        pear_runtime_menu.effort_slider_for_testing([
            {value: "low", label: "Low", selected: false},
            {value: "medium", label: "Medium", selected: false},
        ]),
        {
            has_efforts: true,
            selected_label: "Low",
            value: 0,
            max: 1,
        },
    );
});

run_test("runtime_summary", () => {
    assert.deepEqual(pear_runtime_menu.runtime_summary(undefined), {
        model_label: "translated: Pear",
        effort_label: "",
        compact_label: "translated: Pear",
    });

    assert.deepEqual(
        pear_runtime_menu.runtime_summary(
            make_context({
                model: "gpt-5.5",
                reasoning_effort: "high",
                model_catalog: [{slug: "gpt-5.5", display_name: "GPT-5.5"}],
                reasoning_efforts: [{effort: "high", label: "High"}],
            }),
        ),
        {
            model_label: "GPT-5.5",
            effort_label: "High",
            compact_label: "GPT-5.5 · High",
        },
    );

    assert.deepEqual(
        pear_runtime_menu.runtime_summary(make_context({model: "custom", reasoning_effort: "low"})),
        {
            model_label: "custom",
            effort_label: "Low",
            compact_label: "custom · Low",
        },
    );
});

run_test("build_runtime_usage_rows", () => {
    assert.deepEqual(pear_runtime_menu.build_runtime_usage_rows(undefined), []);
    assert.deepEqual(
        pear_runtime_menu.build_runtime_usage_rows({
            input_tokens: 1_000,
            output_tokens: 250,
            reasoning_output_tokens: 40,
            total_tokens: 1_290,
            cache_percent: 66.6,
            duration_seconds: 2.5,
        }),
        [
            {label: "translated: cache", value: "66.6%"},
            {label: "translated: tokens", value: "1,290"},
            {label: "translated: time", value: "2.5s"},
            {label: "translated: speed", value: "100 tok/s"},
            {label: "translated: reasoning", value: "40"},
        ],
    );

    assert.deepEqual(
        pear_runtime_menu.build_runtime_usage_rows({
            total_tokens: 9,
            duration_seconds: 2,
        }),
        [
            {label: "translated: tokens", value: "9"},
            {label: "translated: time", value: "2.0s"},
            {label: "translated: speed", value: "4.5 tok/s"},
        ],
    );
});
