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

run_test("git_display", () => {
    assert.equal(pear_runtime_menu.git_display(undefined), undefined);
    assert.equal(pear_runtime_menu.git_display(make_context()), undefined);
    assert.equal(pear_runtime_menu.git_display(make_context({git: {available: false}})), undefined);

    // Clean repo, with the repo · branch label from github_repo and a
    // trimmed head.
    assert.deepEqual(
        pear_runtime_menu.git_display(
            make_context({
                github_repo: "zulip/zulip",
                repo_label: "ignored",
                git: {available: true, branch: " main ", head: " abc1234 ", dirty: false},
            }),
        ),
        {
            has_repo_branch: true,
            repo_branch_label: "zulip/zulip · main",
            head: "abc1234",
            status_class: "clean",
            status_marker: "✓",
            status_label: "translated: clean",
        },
    );

    // repo_label is the fallback when github_repo is missing or blank.
    assert.deepEqual(
        pear_runtime_menu.git_display(
            make_context({
                github_repo: "  ",
                repo_label: "pear",
                git: {available: true, branch: "main", dirty: false},
            }),
        ),
        {
            has_repo_branch: true,
            repo_branch_label: "pear · main",
            head: "",
            status_class: "clean",
            status_marker: "✓",
            status_label: "translated: clean",
        },
    );

    // Dirty repo with a changed_count.
    assert.deepEqual(
        pear_runtime_menu.git_display(
            make_context({
                git: {available: true, dirty: true, changed_count: 3},
            }),
        ),
        {
            has_repo_branch: false,
            repo_branch_label: "",
            head: "",
            status_class: "dirty",
            status_marker: "✶",
            status_label: "translated: 3 changed",
        },
    );

    // Dirty repo without a changed_count uses diff_shortstat.
    assert.equal(
        pear_runtime_menu.git_display(
            make_context({
                git: {
                    available: true,
                    dirty: true,
                    changed_count: 0,
                    diff_shortstat: " 2 files changed ",
                },
            }),
        ).status_label,
        "2 files changed",
    );

    // Dirty repo with neither gets the generic label.
    assert.equal(
        pear_runtime_menu.git_display(
            make_context({
                git: {available: true, dirty: true},
            }),
        ).status_label,
        "translated: changed",
    );
});
