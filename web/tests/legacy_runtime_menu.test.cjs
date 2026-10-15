"use strict";

const assert = require("node:assert/strict");

const render_legacy_runtime_menu_popover = require("../templates/legacy_runtime_menu_popover.hbs");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const legacy_runtime_menu = zrequire("legacy_runtime_menu");

const fallback_efforts = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

function make_context(overrides = {}) {
    return {
        stream: "devel",
        topic: "armada",
        model: "gpt-5.5",
        reasoning_effort: "medium",
        response_policy: "ambient",
        ...overrides,
    };
}

run_test("runtime_menu_selector_for_testing", () => {
    assert.equal(
        legacy_runtime_menu.runtime_menu_selector_for_testing,
        "#legacy-panel .legacy-runtime-menu-trigger, .message_header_stream .legacy-topic-header-runtime-control.legacy-runtime-menu-trigger",
    );
    for (const selector of legacy_runtime_menu.forbidden_header_mount_selectors_for_testing) {
        assert.ok(!legacy_runtime_menu.runtime_menu_selector_for_testing.includes(selector));
    }
    assert.ok(
        legacy_runtime_menu.runtime_menu_selector_for_testing.includes(
            ".message_header_stream .legacy-topic-header-runtime-control",
        ),
    );
});

run_test("forbidden_header_mount_selectors_for_testing", () => {
    // Workspace/repo/runtime chrome must stay out of Zulip's global
    // navbar semantics. Stream/topic headers own topic-level controls.
    assert.deepEqual(legacy_runtime_menu.forbidden_header_mount_selectors_for_testing, [
        "#top_navbar",
        "#navbar-middle",
        "#message_view_header",
        ".top-navbar-container",
        ".message-header-navbar-title",
    ]);
});

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
    assert.deepEqual(legacy_runtime_menu.model_reasoning_efforts(context, "gpt-5.5"), gpt_efforts);

    // Empty or absent per-model levels fall back to the context-wide
    // reasoning_efforts, as does an unknown model.
    assert.deepEqual(
        legacy_runtime_menu.model_reasoning_efforts(context, "haiku"),
        context_efforts,
    );
    assert.deepEqual(legacy_runtime_menu.model_reasoning_efforts(context, "opus"), context_efforts);
    assert.deepEqual(
        legacy_runtime_menu.model_reasoning_efforts(context, "unknown"),
        context_efforts,
    );

    // With no usable context-wide efforts either, the built-in
    // fallback list is used.
    assert.deepEqual(
        legacy_runtime_menu.model_reasoning_efforts(
            make_context({reasoning_efforts: []}),
            "gpt-5.5",
        ),
        fallback_efforts,
    );
    assert.deepEqual(
        legacy_runtime_menu.model_reasoning_efforts(make_context(), "gpt-5.5"),
        fallback_efforts,
    );

    // An undefined context falls back too, without throwing.
    assert.deepEqual(
        legacy_runtime_menu.model_reasoning_efforts(undefined, "gpt-5.5"),
        fallback_efforts,
    );
});

run_test("model_choices", () => {
    assert.deepEqual(legacy_runtime_menu.model_choices(undefined), []);

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
    assert.deepEqual(legacy_runtime_menu.model_choices(context), [
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
    assert.deepEqual(legacy_runtime_menu.model_choices(custom_context), [
        {value: "custom-model", label: "custom-model", selected: true},
        {value: "gpt-5.5", label: "GPT-5.5", selected: false},
        {value: "opus", label: "opus", selected: false},
    ]);
});

run_test("effort_choices", () => {
    assert.deepEqual(legacy_runtime_menu.effort_choices(undefined), []);

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
    assert.deepEqual(legacy_runtime_menu.effort_choices(context), [
        {value: "medium", label: "Medium", selected: false},
        {value: "high", label: "High", selected: true},
    ]);

    // Without a catalog, the built-in fallback efforts are offered,
    // still marking the selected reasoning_effort.
    assert.deepEqual(
        legacy_runtime_menu.effort_choices(make_context({reasoning_effort: "xhigh"})),
        [
            {value: "low", label: "Low", selected: false},
            {value: "medium", label: "Medium", selected: false},
            {value: "high", label: "High", selected: false},
            {value: "xhigh", label: "XHigh", selected: true},
        ],
    );
});

run_test("presence_choices", () => {
    assert.deepEqual(legacy_runtime_menu.presence_choices(undefined), []);
    assert.deepEqual(legacy_runtime_menu.presence_choices(make_context()), [
        {value: "ambient", label: "translated: Join when useful", selected: true},
        {value: "mentions_only", label: "translated: Only when asked", selected: false},
    ]);
    assert.deepEqual(
        legacy_runtime_menu.presence_choices(make_context({response_policy: "mentions_only"})),
        [
            {value: "ambient", label: "translated: Join when useful", selected: false},
            {value: "mentions_only", label: "translated: Only when asked", selected: true},
        ],
    );
});

run_test("runtime_summary", () => {
    assert.deepEqual(legacy_runtime_menu.runtime_summary(undefined), {
        model_label: "translated: Pear",
        effort_label: "",
        compact_label: "translated: Pear",
    });

    assert.deepEqual(
        legacy_runtime_menu.runtime_summary(
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
        legacy_runtime_menu.runtime_summary(
            make_context({model: "custom", reasoning_effort: "low"}),
        ),
        {
            model_label: "custom",
            effort_label: "Low",
            compact_label: "custom · Low",
        },
    );

    assert.deepEqual(
        legacy_runtime_menu.runtime_summary(
            make_context({model: "custom", reasoning_effort: "xhigh"}),
        ),
        {
            model_label: "custom",
            effort_label: "XHigh",
            compact_label: "custom · XHigh",
        },
    );
});

run_test("runtime_status_for_testing", () => {
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing(undefined), {
        glyph: "·",
        label: "translated: idle",
        class_name: "legacy-runtime-status-idle",
        is_active: false,
    });
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing({status: "running"}), {
        glyph: "·",
        label: "translated: working",
        class_name: "legacy-runtime-status-active",
        is_active: true,
    });
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing({status: "queued"}), {
        glyph: "·",
        label: "translated: queued",
        class_name: "legacy-runtime-status-active",
        is_active: true,
    });
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing({status: "loading"}), {
        glyph: "·",
        label: "translated: loading",
        class_name: "legacy-runtime-status-active",
        is_active: true,
    });
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing({status: "paused"}), {
        glyph: "✧",
        label: "translated: paused",
        class_name: "legacy-runtime-status-paused",
        is_active: false,
    });
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing({status: "failed"}), {
        glyph: "✷",
        label: "translated: failed",
        class_name: "legacy-runtime-status-blocked",
        is_active: false,
    });
    assert.deepEqual(legacy_runtime_menu.runtime_status_for_testing({status: "cancelled"}), {
        glyph: "✷",
        label: "translated: cancelled",
        class_name: "legacy-runtime-status-blocked",
        is_active: false,
    });
});

run_test("build_runtime_usage_rows", () => {
    assert.deepEqual(legacy_runtime_menu.build_runtime_usage_rows(undefined), []);
    assert.deepEqual(
        legacy_runtime_menu.build_runtime_usage_rows({
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
            {label: "translated: reasoning", value: "40"},
        ],
    );

    assert.deepEqual(
        legacy_runtime_menu.build_runtime_usage_rows({
            total_tokens: 9,
            duration_seconds: 2,
        }),
        [
            {label: "translated: tokens", value: "9"},
            {label: "translated: time", value: "2.0s"},
        ],
    );
});

run_test("runtime_usage_meter_for_testing", () => {
    assert.deepEqual(legacy_runtime_menu.runtime_usage_meter_for_testing(undefined), {
        has_meter: false,
        label: "",
        value: "",
        percent: 0,
        is_hot: false,
    });
    assert.deepEqual(
        legacy_runtime_menu.runtime_usage_meter_for_testing({
            cache_percent: 66.66,
        }),
        {
            has_meter: true,
            label: "translated: cached context",
            value: "66.7%",
            percent: 66.66,
            is_hot: false,
        },
    );
    assert.deepEqual(
        legacy_runtime_menu.runtime_usage_meter_for_testing({
            cache_percent: 70,
        }),
        {
            has_meter: true,
            label: "translated: cached context",
            value: "70.0%",
            percent: 70,
            is_hot: true,
        },
    );
    assert.deepEqual(
        legacy_runtime_menu.runtime_usage_meter_for_testing({
            cache_percent: 125,
        }),
        {
            has_meter: true,
            label: "translated: cached context",
            value: "100.0%",
            percent: 100,
            is_hot: true,
        },
    );
    assert.deepEqual(
        legacy_runtime_menu.runtime_usage_meter_for_testing({
            cache_percent: -5,
        }),
        {
            has_meter: true,
            label: "translated: cached context",
            value: "0.0%",
            percent: 0,
            is_hot: false,
        },
    );
});

run_test("runtime popover renders armada context meter line", () => {
    const html = render_legacy_runtime_menu_popover({
        has_runtime: true,
        runtime_status: {
            glyph: "·",
            label: "Idle",
            class_name: "legacy-runtime-status-idle",
            is_active: false,
        },
        runtime_model_label: "GPT-5.5",
        runtime_effort_label: "XHigh",
        runtime_usage_meter: {
            has_meter: true,
            label: "cached context",
            value: "72.0%",
            percent: 72,
            is_hot: true,
        },
        has_runtime_usage: false,
        model_choices: [],
        effort_choices: [],
        presence_choices: [],
    });

    assert.match(html, /legacy-runtime-meter-block legacy-runtime-meter-hot/);
    assert.match(html, /legacy-runtime-meter-line/);
    assert.match(html, /legacy-runtime-meter-fill/);
    // The fill width is applied from render_popover_content after the
    // template renders; an inline style attribute would trip template lint.
    assert.doesNotMatch(html, /style=/);
    assert.doesNotMatch(html, /<meter\b/);
});

run_test("runtime popover does not expose workbench actions", () => {
    const html = render_legacy_runtime_menu_popover({
        has_runtime: true,
        runtime_status: {
            glyph: "·",
            label: "Idle",
            class_name: "legacy-runtime-status-idle",
            is_active: false,
        },
        runtime_model_label: "GPT-5.5",
        runtime_effort_label: "High",
        runtime_usage_meter: {
            has_meter: false,
            label: "",
            value: "",
            percent: 0,
            is_hot: false,
        },
        has_runtime_usage: false,
        model_choices: [{value: "gpt-5.5", label: "GPT-5.5", selected: true}],
        effort_choices: [{value: "high", label: "High", selected: true}],
        presence_choices: [
            {value: "ambient", label: "Join when useful", selected: true},
            {value: "mentions_only", label: "Only when asked", selected: false},
        ],
    });

    assert.doesNotMatch(html, /legacy-workbench-menu-link/);
    assert.doesNotMatch(html, />translated: Workbench</);
    assert.doesNotMatch(html, /legacy-runtime-model-select/);
    assert.match(html, /legacy-runtime-model-choice/);
    assert.doesNotMatch(html, /legacy-runtime-effort-range/);
    assert.match(html, /legacy-runtime-effort-choice/);
});

run_test("runtime popover uses compact radio menu rows", () => {
    const html = render_legacy_runtime_menu_popover({
        has_runtime: true,
        runtime_status: {
            glyph: "·",
            label: "Idle",
            class_name: "legacy-runtime-status-idle",
            is_active: false,
        },
        runtime_model_label: "GPT-5.5",
        runtime_effort_label: "High",
        runtime_usage_meter: {
            has_meter: false,
            label: "",
            value: "",
            percent: 0,
            is_hot: false,
        },
        has_runtime_usage: false,
        model_choices: [
            {value: "gpt-5.5", label: "GPT-5.5", selected: true},
            {value: "gpt-5.4-mini", label: "GPT-5.4-Mini", selected: false},
        ],
        effort_choices: [
            {value: "low", label: "Low", selected: false},
            {value: "high", label: "High", selected: true},
        ],
        presence_choices: [
            {value: "ambient", label: "Join when useful", selected: true},
            {value: "mentions_only", label: "Only when asked", selected: false},
        ],
    });

    assert.match(html, /legacy-runtime-menu-section[^>]*>translated: Model<\/div>/);
    assert.match(html, /legacy-runtime-menu-section[^>]*>translated: Thinking<\/div>/);
    assert.match(html, /legacy-runtime-menu-section[^>]*>translated: Presence<\/div>/);
    assert.match(html, /legacy-runtime-model-menu-choice[^>]+data-model="gpt-5.5"/);
    assert.match(html, /legacy-runtime-effort-menu-choice[^>]+data-effort="high"/);
    assert.match(html, /legacy-runtime-presence-menu-choice[^>]+data-policy="ambient"/);
    assert.match(html, />translated: @Pear always works<\/div>/);
    assert.match(
        html,
        /class="legacy-runtime-model-choice" name="legacy-runtime-model" value="gpt-5.5" checked/,
    );
    assert.match(
        html,
        /class="legacy-runtime-effort-choice" name="legacy-runtime-effort" value="high" checked/,
    );
    assert.match(html, /role="menuitemradio" aria-checked="true"/);
    assert.doesNotMatch(html, /legacy-runtime-setting-row/);
    assert.doesNotMatch(html, /legacy-runtime-effort-range/);
    assert.doesNotMatch(html, /<select\b/);
});
