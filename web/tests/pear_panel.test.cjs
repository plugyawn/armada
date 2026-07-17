"use strict";

const assert = require("node:assert/strict");

const render_pear_panel = require("../templates/pear_panel.hbs");
const render_pear_workspace_rail = require("../templates/pear_workspace_rail.hbs");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pear_panel = zrequire("pear_panel");

run_test("render_state_changed_for_testing", () => {
    const first_state = {
        has_topic: true,
        context_label: "devel > general chat",
        status_label: "running",
        active_run: {id: 1, status: "running", summary: "Working"},
    };
    const same_state = {
        has_topic: true,
        context_label: "devel > general chat",
        status_label: "running",
        active_run: {id: 1, status: "running", summary: "Working"},
    };
    const changed_state = {
        ...same_state,
        active_run: {id: 1, status: "done", summary: "Done"},
        status_label: "done",
    };

    assert.ok(pear_panel.render_state_changed_for_testing("", first_state));
    assert.ok(
        !pear_panel.render_state_changed_for_testing(JSON.stringify(first_state), same_state),
    );
    assert.ok(
        pear_panel.render_state_changed_for_testing(JSON.stringify(first_state), changed_state),
    );
});

run_test("rail plan uses Working instead of leaking an older completed plan", () => {
    const steps = pear_panel.pear_rail_plan_steps_for_testing([
        {id: 2, status: "running", plan: null},
        {
            id: 1,
            status: "done",
            plan: [{step: "Old task", status: "completed"}],
        },
    ]);

    assert.deepEqual(steps, [
        {
            step: "translated: Working",
            status_glyph: "",
            status_class: "pear-rail-plan-active",
            glyph_class: "pear-thinking-glyph",
        },
    ]);
});

run_test("rail plan renders only the active run checklist", () => {
    const steps = pear_panel.pear_rail_plan_steps_for_testing([
        {
            id: 2,
            status: "running",
            plan: [
                {step: "Inspect", status: "completed"},
                {step: "Implement", status: "in_progress"},
                {step: "Verify", status: "pending"},
            ],
        },
        {
            id: 1,
            status: "done",
            plan: [{step: "Old task", status: "completed"}],
        },
    ]);

    assert.deepEqual(steps, [
        {
            step: "Inspect",
            status_glyph: "✓",
            status_class: "pear-rail-plan-done",
            glyph_class: "",
        },
        {
            step: "Implement",
            status_glyph: "",
            status_class: "pear-rail-plan-active",
            glyph_class: "pear-thinking-glyph",
        },
        {
            step: "Verify",
            status_glyph: "○",
            status_class: "pear-rail-plan-pending",
            glyph_class: "",
        },
    ]);
});

run_test("rail plan keeps a bounded window around the active step", () => {
    const steps = pear_panel.pear_rail_plan_steps_for_testing([
        {
            id: 2,
            status: "running",
            plan: [
                {step: "One", status: "completed"},
                {step: "Two", status: "completed"},
                {step: "Three", status: "completed"},
                {step: "Four", status: "in_progress"},
                {step: "Five", status: "pending"},
                {step: "Six", status: "pending"},
            ],
        },
    ]);

    assert.deepEqual(
        steps.map((entry) => entry.step),
        ["Two", "Three", "Four", "Five"],
    );
});

run_test("rail plan never leaves terminal work animated", () => {
    const steps = pear_panel.pear_rail_plan_steps_for_testing([
        {
            id: 2,
            status: "done",
            plan: [
                {step: "Inspect", status: "completed"},
                {step: "Report", status: "in_progress"},
            ],
        },
    ]);

    assert.deepEqual(steps, [
        {
            step: "Inspect",
            status_glyph: "✓",
            status_class: "pear-rail-plan-done",
            glyph_class: "",
        },
        {
            step: "Report",
            status_glyph: "○",
            status_class: "pear-rail-plan-pending",
            glyph_class: "",
        },
    ]);
});

run_test("workspace rail keeps a plan visible before workspace context arrives", () => {
    const html = render_pear_workspace_rail({
        has_workspace: false,
        has_plan: true,
        plan_steps: [
            {
                step: "Working",
                status_glyph: "",
                status_class: "pear-rail-plan-active",
                glyph_class: "pear-thinking-glyph",
            },
        ],
        has_runs: false,
        has_overview: true,
        overview: "Reading the renderer state",
        show_workspace_line: false,
    });

    assert.ok(html.includes("pear-rail-plan"));
    assert.ok(html.includes("pear-thinking-glyph"));
    assert.ok(html.includes("Working"));
    assert.ok(html.includes("pear-rail-overview"));
    assert.ok(html.includes("Reading the renderer state"));
    assert.ok(!html.includes("pear-workspace-rail-card"));
    assert.ok(!html.includes("pear-rail-plan-title"));
});

run_test("status_marker_for_testing", () => {
    assert.equal(pear_panel.status_marker_for_testing("running"), "·");
    assert.equal(pear_panel.status_marker_for_testing("queued"), "·");
    assert.equal(pear_panel.status_marker_for_testing("paused"), "✧");
    assert.equal(pear_panel.status_marker_for_testing("failed"), "✷");
    assert.equal(pear_panel.status_marker_for_testing("cancelled"), "✷");
    assert.equal(pear_panel.status_marker_for_testing("done"), "·");
    assert.equal(pear_panel.status_marker_for_testing("unknown"), "·");
});

run_test("status_label_for_testing", () => {
    assert.equal(pear_panel.status_label_for_testing("running"), "translated: working");
    assert.equal(pear_panel.status_label_for_testing("queued"), "translated: queued");
    assert.equal(pear_panel.status_label_for_testing("paused"), "translated: paused");
    assert.equal(pear_panel.status_label_for_testing("failed"), "translated: failed");
    assert.equal(pear_panel.status_label_for_testing("cancelled"), "translated: cancelled");
    assert.equal(pear_panel.status_label_for_testing("done"), "translated: done");
});

run_test("panel_runtime_label_for_testing", () => {
    assert.equal(pear_panel.panel_runtime_label_for_testing({}), "");
    assert.equal(
        pear_panel.panel_runtime_label_for_testing({model: "gpt-5.5", reasoning_effort: "high"}),
        "gpt-5.5 / h",
    );
    assert.equal(
        pear_panel.panel_runtime_label_for_testing({model: "gpt-5.5", reasoning_effort: "xhigh"}),
        "gpt-5.5 / xh",
    );
    assert.equal(pear_panel.panel_runtime_label_for_testing({model: "codex-spark"}), "codex-spark");
});

run_test("template renders active status as visible text glyph phrase", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_panel_header: true,
        show_runtime_trigger: false,
        show_workbench: false,
        has_topic: true,
        has_panel_menu: false,
        active_run_show_status: true,
        panel_can_close: false,
        active_run: {
            show_status: true,
            status_class: "active",
            status_label: "working",
            status_marker: "·",
            is_active: true,
        },
        topic_context: undefined,
    });

    assert.match(html, /pear-run-status-row pear-run-status-active pear-liveness-active/);
    // The decorative status glyph is not part of the accessible name; the
    // human label is announced, and the glyph is marked aria-hidden.
    assert.match(html, /aria-label="working"/);
    assert.match(html, /<span class="pear-panel-status-label">working<\/span>/);
    assert.match(
        html,
        /<span class="pear-ui-glyph pear-run-status-glyph pear-thinking-glyph" aria-hidden="true">·<\/span>/,
    );
    assert.doesNotMatch(html, /aria-hidden="true">✦<\/span>/);
    assert.doesNotMatch(html, /pear-run-status-compact/);
});

run_test("template renders idle topic shell as compact runtime entry point", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_panel_header: false,
        show_topic_control: true,
        show_runtime_trigger: true,
        show_workbench: false,
        has_topic: true,
        has_panel_menu: false,
        panel_can_close: false,
        active_run: undefined,
        context_label: "devel > general chat",
        topic_context: {
            panel_repo_label: "zulip/zulip",
            panel_worktree_label: "devel-general-chat",
            panel_git_status_label: "clean",
            panel_git_status_class: "clean",
            panel_git_ref_label: "main / a3f92c1",
            panel_runtime_label: "gpt-5.5 / xh",
        },
    });

    assert.match(html, /pear-panel-inner/);
    assert.doesNotMatch(html, /pear-panel-header-idle/);
    assert.match(html, /pear-topic-control/);
    assert.match(html, /pear-topic-control-heading/);
    assert.match(html, /pear-topic-control-title-text">translated: Context<\/span>/);
    assert.match(html, /pear-runtime-menu-trigger/);
    assert.match(html, /pear-topic-runtime-name/);
    assert.match(html, /<span class="pear-topic-runtime-name">translated: Model<\/span>/);
    assert.match(html, /<span class="pear-runtime-menu-label">gpt-5.5 \/ xh<\/span>/);
    assert.doesNotMatch(html, /<span class="pear-runtime-menu-label">translated: Pear<\/span>/);
    assert.match(html, /pear-topic-context-label">translated: Repo<\/span>/);
    assert.match(html, /pear-topic-context-main" title="zulip\/zulip">zulip\/zulip<\/span>/);
    assert.match(html, /pear-topic-context-status-clean">clean<\/span>/);
    assert.match(
        html,
        /pear-topic-context-main" title="devel-general-chat">devel-general-chat<\/span>/,
    );
    assert.match(html, /pear-topic-context-ref" title="main \/ a3f92c1">main \/ a3f92c1<\/span>/);
    assert.doesNotMatch(
        html,
        /<span class="pear-runtime-menu-label">translated: Pear runtime<\/span>/,
    );
    assert.doesNotMatch(html, /pear-run-actions/);
    assert.doesNotMatch(html, /class="pear-workbench(?:\s|")/);
});

run_test("template renders topic shell while runtime context is loading", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_panel_header: false,
        show_topic_control: true,
        show_runtime_trigger: true,
        show_workbench: false,
        has_topic: true,
        has_panel_menu: false,
        panel_can_close: false,
        active_run: undefined,
        context_label: "devel > general chat",
        topic_context: undefined,
    });

    assert.match(html, /pear-topic-control/);
    assert.match(html, /pear-topic-control-heading/);
    assert.match(html, /pear-runtime-menu-trigger/);
    assert.match(html, /pear-topic-control-title-text">translated: Context<\/span>/);
    assert.match(html, /<span class="pear-topic-runtime-name">translated: Model<\/span>/);
    assert.match(html, /<span class="pear-runtime-menu-label"><\/span>/);
});

run_test("template renders channel shell when no topic is selected", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_panel_header: false,
        show_topic_control: true,
        show_runtime_trigger: false,
        show_workbench: false,
        has_topic: false,
        has_panel_menu: false,
        panel_can_close: false,
        active_run: undefined,
        context_label: "devel",
        topic_context: undefined,
    });

    assert.match(html, /pear-topic-control/);
    assert.match(html, /pear-topic-control-title-text">translated: Context<\/span>/);
    assert.doesNotMatch(html, /pear-runtime-menu-trigger/);
    assert.match(html, /pear-topic-context-label">translated: Channel<\/span>/);
    assert.match(html, /pear-topic-context-main" title="devel">devel<\/span>/);
    assert.match(html, /pear-topic-context-label">translated: Worktree<\/span>/);
    assert.match(html, /pear-topic-context-main">translated: Select a topic<\/span>/);
});

run_test("template renders workbench without active run", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        show_topic_control: true,
        show_runtime_trigger: true,
        panel_can_close: true,
        active_run: undefined,
        topic_context: {
            panel_repo_label: "zulip/zulip",
            panel_worktree_label: "routing-fix",
            panel_git_status_label: "2 files",
            panel_git_status_class: "dirty",
            panel_git_ref_label: "main / a3f92c1",
            panel_runtime_label: "gpt-5.5 / h",
            panel_reasoning_effort_label: "High",
            panel_reasoning_effort_meter_class: "high",
            workbench_scope_note: "Changes and commits are scoped to this topic worktree.",
            workbench_title: "routing-fix",
            workbench_meta: "devel / routing-fix / a3f92c1",
            workbench_diff_selected: false,
            workbench_history_selected: true,
            workbench_side_selected: false,
            workbench_diff_disabled: false,
            workbench_history_disabled: false,
            diff_tab_label: "diff",
            history_tab_label: "history",
            side_tab_label: "whisper",
            workbench_view: "history",
            has_recent_commits: false,
            workbench_diff_summary_label: "2 files +16 -4",
            workbench_diff_file_label: "2 files",
            workbench_diff_insertions_label: "+16",
            workbench_diff_deletions_label: "-4",
        },
    });

    assert.match(html, /pear-workbench/);
    assert.doesNotMatch(html, /pear-topic-control/);
    assert.match(html, /pear-workbench-context-strip/);
    assert.doesNotMatch(html, /pear-workbench-model-select/);
    assert.doesNotMatch(html, /pear-workbench-effort-range/);
    assert.doesNotMatch(html, /pear-topic-open-action/);
    assert.match(html, /pear-workbench-title-row/);
    assert.match(html, /pear-workbench-scope-note/);
    assert.match(html, /Changes and commits are scoped to this topic worktree\./);
    assert.match(html, /routing-fix/);
    assert.match(html, /devel \/ routing-fix \/ a3f92c1/);
    assert.match(html, /Repo/);
    assert.match(html, /Worktree/);
    assert.match(html, /2 files/);
    assert.match(html, /main \/ a3f92c1/);
    assert.match(html, /pear-workbench-context-row/);
    assert.match(html, /pear-workbench-tabs/);
    assert.match(html, /pear-workbench-close/);
    assert.match(html, /diff/);
    assert.match(html, /history/);
    assert.match(html, /whisper/);
    assert.doesNotMatch(html, /pear-workbench-head-row/);
    assert.doesNotMatch(html, /pear-workbench-diffstat/);
    assert.doesNotMatch(html, /pear-workbench-context-meter/);
});

run_test("template keeps volatile run metadata out of workbench chrome", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        show_runtime_trigger: false,
        panel_can_close: true,
        active_run_is_active: true,
        active_run: {
            show_status: false,
            is_active: true,
            usage: {cache_percent: 72},
        },
        topic_context: {
            workbench_title: "routing-fix",
            workbench_meta: "devel / routing-fix / a3f92c1",
            panel_repo_label: "devel",
            panel_worktree_label: "routing-fix",
            panel_git_status_label: "clean",
            panel_git_status_class: "clean",
            panel_git_ref_label: "main / a3f92c1",
            panel_runtime_label: "gpt-5.5 / xh",
            panel_reasoning_effort_label: "XHigh",
            panel_reasoning_effort_meter_class: "xhigh",
            workbench_scope_note: "Changes and commits are scoped to this topic worktree.",
            workbench_diff_selected: true,
            workbench_history_selected: false,
            workbench_side_selected: false,
            workbench_diff_disabled: false,
            workbench_history_disabled: false,
            diff_tab_label: "diff",
            history_tab_label: "history",
            side_tab_label: "whisper",
            workbench_view: "diff",
            has_workbench_diff: false,
        },
    });

    assert.match(html, /pear-workbench-tabs/);
    assert.match(html, /pear-workbench-title-row/);
    assert.match(html, /pear-workbench-context-strip/);
    assert.doesNotMatch(html, /pear-workbench-model-select/);
    assert.doesNotMatch(html, /pear-workbench-effort-range/);
    assert.doesNotMatch(html, /pear-workbench-context-meter/);
    assert.doesNotMatch(html, /pear-workbench-head-row/);
    assert.doesNotMatch(html, /pear-thinking-glyph/);
    assert.doesNotMatch(html, /cache_percent/);
});

run_test("template keeps runtime controls out of workbench chrome", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        show_runtime_trigger: false,
        panel_can_close: true,
        active_run: {
            show_status: true,
            usage: {cache_percent: 91},
        },
        topic_context: {
            workbench_title: "routing-fix",
            workbench_meta: "devel / routing-fix / a3f92c1",
            panel_repo_label: "devel",
            panel_worktree_label: "routing-fix",
            panel_git_status_label: "clean",
            panel_git_status_class: "clean",
            panel_git_ref_label: "main / a3f92c1",
            panel_runtime_label: "gpt-5.5 / h",
            panel_reasoning_effort_label: "High",
            panel_reasoning_effort_meter_class: "high",
            workbench_scope_note: "Changes and commits are scoped to this topic worktree.",
            workbench_diff_selected: true,
            workbench_history_selected: false,
            workbench_side_selected: false,
            workbench_diff_disabled: false,
            workbench_history_disabled: false,
            diff_tab_label: "diff",
            history_tab_label: "history",
            side_tab_label: "whisper",
            workbench_view: "diff",
            has_workbench_diff: false,
        },
    });

    assert.doesNotMatch(html, /pear-workbench-context-meter/);
    assert.doesNotMatch(html, /cache_percent/);
    assert.doesNotMatch(html, /pear-workbench-model-select/);
    assert.doesNotMatch(html, /pear-workbench-effort-range/);
    assert.match(html, /pear-workbench-context-strip/);
    assert.doesNotMatch(html, /pear-workbench-runtime-trigger/);
    assert.doesNotMatch(html, /No committed work yet/);
});

run_test("template renders diff rows without duplicate summary chips", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        show_runtime_trigger: false,
        panel_can_close: true,
        active_run: undefined,
        topic_context: {
            workbench_title: "routing-fix",
            workbench_meta: "devel / routing-fix / a3f92c1",
            panel_repo_label: "devel",
            panel_worktree_label: "routing-fix",
            panel_git_status_label: "2 files",
            panel_git_status_class: "dirty",
            panel_git_ref_label: "main / a3f92c1",
            panel_runtime_label: "gpt-5.5 / h",
            panel_reasoning_effort_label: "High",
            panel_reasoning_effort_meter_class: "high",
            workbench_scope_note: "Changes and commits are scoped to this topic worktree.",
            workbench_diff_selected: true,
            workbench_history_selected: false,
            workbench_side_selected: false,
            workbench_diff_disabled: false,
            workbench_history_disabled: false,
            diff_tab_label: "diff 2",
            history_tab_label: "history",
            side_tab_label: "whisper",
            workbench_view: "diff",
            has_workbench_diff: true,
            has_changed_files: true,
            has_workbench_diff_summary: true,
            workbench_diff_summary_label: "2 files +16 -4",
            workbench_diff_file_label: "2 files",
            workbench_diff_insertions_label: "+16",
            workbench_diff_deletions_label: "-4",
            git: {
                changed_files: [
                    {
                        status: "M",
                        kind: "modified",
                        path: "zerver/lib/pear_gate.py",
                    },
                    {
                        status: "A",
                        kind: "added",
                        path: "zerver/tests/test_pear_gate.py",
                    },
                ],
            },
        },
    });

    assert.doesNotMatch(html, /pear-workbench-diffstat/);
    assert.match(html, /pear-file-list/);
    assert.match(html, /pear-file-row pear-file-modified/);
    assert.match(html, /zerver\/lib\/pear_gate\.py/);
    assert.match(html, /pear-file-row pear-file-added/);
    assert.match(html, /zerver\/tests\/test_pear_gate\.py/);
    assert.doesNotMatch(html, /pear-workbench-summary/);
});

run_test("should_refresh_context_for_testing", () => {
    const base_options = {
        context_key_value: "devel\u0000general chat",
        force_context: false,
        live: false,
        now: 100_000,
        last_refresh_key: "devel\u0000general chat",
        last_refresh_at: 90_000,
        has_context: true,
    };

    assert.equal(pear_panel.should_refresh_context_for_testing(base_options), false);
    assert.equal(
        pear_panel.should_refresh_context_for_testing({...base_options, live: true}),
        false,
    );
    assert.equal(
        pear_panel.should_refresh_context_for_testing({...base_options, context_key_value: ""}),
        false,
    );
    assert.equal(
        pear_panel.should_refresh_context_for_testing({...base_options, force_context: true}),
        true,
    );
    assert.equal(
        pear_panel.should_refresh_context_for_testing({...base_options, has_context: false}),
        true,
    );
    assert.equal(
        pear_panel.should_refresh_context_for_testing({
            ...base_options,
            last_refresh_key: "devel\u0000old topic",
        }),
        true,
    );
    assert.equal(
        pear_panel.should_refresh_context_for_testing({
            ...base_options,
            last_refresh_at: 39_999,
        }),
        true,
    );
});

run_test("topic_context_after_refresh_for_testing", () => {
    const cached_context = {stream: "devel", topic: "pear", model: "gpt-5.5"};
    const fresh_context = {stream: "devel", topic: "pear", model: "gpt-5.5-mini"};

    assert.equal(
        pear_panel.topic_context_after_refresh_for_testing({
            include_context: false,
            live: false,
            response_topic_context: null,
            last_topic_context: cached_context,
        }),
        cached_context,
    );
    assert.equal(
        pear_panel.topic_context_after_refresh_for_testing({
            include_context: false,
            live: true,
            response_topic_context: undefined,
            last_topic_context: cached_context,
        }),
        cached_context,
    );
    assert.equal(
        pear_panel.topic_context_after_refresh_for_testing({
            include_context: true,
            live: false,
            response_topic_context: fresh_context,
            last_topic_context: cached_context,
        }),
        fresh_context,
    );
    assert.equal(
        pear_panel.topic_context_after_refresh_for_testing({
            include_context: true,
            live: false,
            response_topic_context: null,
            last_topic_context: cached_context,
        }),
        cached_context,
    );
    assert.equal(
        pear_panel.topic_context_after_refresh_for_testing({
            include_context: true,
            live: false,
            response_topic_context: null,
            last_topic_context: null,
            cached_topic_context: cached_context,
        }),
        cached_context,
    );
    assert.equal(
        pear_panel.topic_context_after_refresh_for_testing({
            include_context: true,
            live: false,
            response_topic_context: null,
            last_topic_context: null,
        }),
        null,
    );
});

run_test("topic_context_for_display_for_testing", () => {
    const current_context = {stream: "devel", topic: "pear", model: "gpt-5.5"};
    const cached_context = {stream: "devel", topic: "pear", model: "gpt-5.5-mini"};
    const other_context = {stream: "devel", topic: "other", model: "gpt-5.5"};

    assert.equal(
        pear_panel.topic_context_for_display_for_testing({
            stream: "devel",
            topic: "pear",
            render_topic_context: current_context,
            cached_topic_context: cached_context,
        }),
        current_context,
    );
    assert.equal(
        pear_panel.topic_context_for_display_for_testing({
            stream: "devel",
            topic: "pear",
            render_topic_context: other_context,
            cached_topic_context: cached_context,
        }),
        cached_context,
    );
    assert.equal(
        pear_panel.topic_context_for_display_for_testing({
            stream: "devel",
            topic: "pear",
            render_topic_context: null,
            cached_topic_context: undefined,
        }),
        undefined,
    );
});

run_test("optimistic_topic_context_for_testing", () => {
    const topic_context = {
        stream: "devel",
        topic: "pear",
        model: "gpt-5.5",
        reasoning_effort: "xhigh",
        model_catalog: [
            {
                slug: "gpt-5.3-codex-spark",
                display_name: "GPT-5.3 Codex Spark",
                default_reasoning_level: "medium",
                supported_reasoning_levels: [
                    {effort: "low", label: "Low"},
                    {effort: "medium", label: "Medium"},
                ],
            },
        ],
        reasoning_efforts: [
            {effort: "low", label: "Low"},
            {effort: "medium", label: "Medium"},
            {effort: "high", label: "High"},
            {effort: "xhigh", label: "XHigh"},
        ],
    };

    assert.deepEqual(
        pear_panel.optimistic_topic_context_for_testing(topic_context, {
            model: "gpt-5.3-codex-spark",
        }),
        {
            ...topic_context,
            model: "gpt-5.3-codex-spark",
            reasoning_effort: "medium",
        },
    );
    assert.deepEqual(
        pear_panel.optimistic_topic_context_for_testing(topic_context, {
            reasoning_effort: "low",
        }),
        {
            ...topic_context,
            reasoning_effort: "low",
        },
    );
});

run_test("panel_header_state_for_testing", () => {
    assert.deepEqual(pear_panel.panel_header_state_for_testing(undefined), {
        show_panel_header: false,
        show_runtime_trigger: false,
        has_panel_menu: false,
    });
    assert.deepEqual(
        pear_panel.panel_header_state_for_testing({
            show_status: false,
            has_actions: false,
        }),
        {
            show_panel_header: false,
            show_runtime_trigger: false,
            has_panel_menu: false,
        },
    );
    assert.deepEqual(pear_panel.panel_header_state_for_testing(undefined, {has_topic: true}), {
        show_panel_header: false,
        show_runtime_trigger: true,
        has_panel_menu: false,
    });
    assert.deepEqual(
        pear_panel.panel_header_state_for_testing(undefined, {
            has_topic: true,
            show_workbench: true,
        }),
        {
            show_panel_header: false,
            show_runtime_trigger: true,
            has_panel_menu: false,
        },
    );
    assert.deepEqual(
        pear_panel.panel_header_state_for_testing({
            show_status: true,
            has_actions: false,
        }),
        {
            show_panel_header: true,
            show_runtime_trigger: false,
            has_panel_menu: false,
        },
    );
    assert.deepEqual(
        pear_panel.panel_header_state_for_testing({
            show_status: true,
            has_actions: true,
        }),
        {
            show_panel_header: true,
            show_runtime_trigger: false,
            has_panel_menu: true,
        },
    );
});

run_test("workbench_view_for_testing", () => {
    assert.equal(
        pear_panel.workbench_view_for_testing({
            requested: undefined,
            has_diff: true,
            has_history: true,
        }),
        "diff",
    );
    assert.equal(
        pear_panel.workbench_view_for_testing({
            requested: "history",
            has_diff: true,
            has_history: true,
        }),
        "history",
    );
    assert.equal(
        pear_panel.workbench_view_for_testing({
            requested: "history",
            has_diff: true,
            has_history: false,
        }),
        "history",
    );
    assert.equal(
        pear_panel.workbench_view_for_testing({
            requested: "diff",
            has_diff: false,
            has_history: true,
        }),
        "diff",
    );
    assert.equal(
        pear_panel.workbench_view_for_testing({
            requested: undefined,
            has_diff: false,
            has_history: false,
        }),
        "history",
    );
    assert.equal(
        pear_panel.workbench_view_for_testing({
            requested: "side",
            has_diff: true,
            has_history: true,
        }),
        "side",
    );
});

run_test("workbench_view_for_artifact_label", () => {
    assert.equal(pear_panel.workbench_view_for_artifact_label("Diff"), "diff");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Doc"), undefined);
    assert.equal(pear_panel.workbench_view_for_artifact_label("Repository"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Branch"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Commit"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("SHA"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Codex session"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Verification"), undefined);
});

run_test("workbench_viewport_allows_popover_for_testing", () => {
    assert.equal(pear_panel.workbench_viewport_allows_popover_for_testing(390), true);
    assert.equal(pear_panel.workbench_viewport_allows_popover_for_testing(900), true);
    assert.equal(pear_panel.workbench_viewport_allows_popover_for_testing(1199), true);
    assert.equal(pear_panel.workbench_viewport_allows_popover_for_testing(1200), true);
    assert.equal(pear_panel.workbench_viewport_allows_popover_for_testing(1440), true);
});

run_test("work_panel_open_for_testing", () => {
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "",
            manually_open: true,
            active_run_status: "running",
            has_error: true,
        }),
        false,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "done",
            has_error: false,
        }),
        false,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: true,
            active_run_status: "done",
            has_error: false,
        }),
        true,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "running",
            has_error: false,
        }),
        false,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "queued",
            has_error: false,
        }),
        false,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "paused",
            has_error: false,
        }),
        false,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "failed",
            has_error: false,
        }),
        false,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: undefined,
            has_error: true,
            error_dismissed: false,
        }),
        true,
    );
    // A dismissed error must not force the panel open (the "undismissable on
    // error" regression: the 5s poll's still-present error kept re-opening it).
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel pear",
            manually_open: false,
            active_run_status: undefined,
            has_error: true,
            error_dismissed: true,
        }),
        false,
    );
});

run_test("workbench_open_for_testing", () => {
    assert.equal(
        pear_panel.workbench_open_for_testing({
            context_key_value: "",
            manually_open: true,
            has_error: true,
        }),
        false,
    );
    assert.equal(
        pear_panel.workbench_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            has_error: false,
        }),
        false,
    );
    assert.equal(
        pear_panel.workbench_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: true,
            has_error: false,
        }),
        true,
    );
    assert.equal(
        pear_panel.workbench_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            has_error: true,
            error_dismissed: false,
        }),
        true,
    );
    assert.equal(
        pear_panel.workbench_open_for_testing({
            context_key_value: "devel pear",
            manually_open: false,
            has_error: true,
            error_dismissed: true,
        }),
        false,
    );
});

run_test("refresh_paused_for_testing", () => {
    // Hidden tabs skip event/reconnect reconciliation...
    assert.equal(pear_panel.refresh_paused_for_testing({hidden: true, force_context: false}), true);
    // ...but an explicit forced refresh still runs even while hidden.
    assert.equal(pear_panel.refresh_paused_for_testing({hidden: true, force_context: true}), false);
    // Visible tab never pauses.
    assert.equal(
        pear_panel.refresh_paused_for_testing({hidden: false, force_context: false}),
        false,
    );
});

run_test("workbench_popover_top_for_testing", () => {
    const base = {viewport_height: 900, margin: 12, min_body_height: 260};
    // Normal anchor: top is used as-is.
    assert.equal(pear_panel.workbench_popover_top_for_testing({...base, desired_top: 200}), 200);
    // Anchor scrolled above the viewport: negative desired top is clamped to
    // the margin instead of sliding off the top edge.
    assert.equal(pear_panel.workbench_popover_top_for_testing({...base, desired_top: -400}), 12);
    // Anchor near the bottom: top is pushed up so the min body height still
    // fits above the bottom margin (900 - 260 - 12 = 628).
    assert.equal(pear_panel.workbench_popover_top_for_testing({...base, desired_top: 880}), 628);
});

run_test("topic header controls template matches the retired jQuery skeleton", () => {
    const render_pear_topic_header_controls = require("../templates/pear_topic_header_controls.hbs");
    const html = render_pear_topic_header_controls({control_key: '{"stream_id":7,"topic":"t"}'});

    // Structure: display-contents cluster > runtime group (merged
    // model·effort pill with in-pill status glyph) + the commit line
    // (branch chip shown only for surprising branches; the commit
    // sentence — icon, sha, subject, meta — opens work details). No
    // status dot, no bare "·" separator, no "no repo" furniture.
    assert.match(
        html,
        // Whitespace-tolerant: prettier splits the attributes over lines.
        /^<span\s+class="pear-topic-header-status no-select"\s+data-pear-header-key="\{&quot;stream_id&quot;:7,&quot;topic&quot;:&quot;t&quot;\}"\s*>/,
    );
    assert.ok(!html.includes("pear-topic-header-status-dot"));
    assert.ok(!html.includes("pear-topic-header-meta-separator"));
    assert.ok(!html.includes("pear-topic-header-work-label"));
    for (const marker of [
        'class="pear-topic-header-commitline"',
        "pear-topic-header-branch-control tippy-zulip-delayed-tooltip",
        "zulip-icon zulip-icon-git-branch pear-topic-header-git-icon",
        'class="pear-topic-header-branch-label"',
        "pear-topic-header-git-control pear-panel-open-action",
        "zulip-icon zulip-icon-git-commit pear-topic-header-git-icon",
        'class="pear-commitline-sha"',
        'class="pear-commitline-subject"',
        'class="pear-commitline-meta"',
        "pear-topic-header-runtime-control pear-runtime-menu-trigger",
        'class="pear-runtime-menu-model-label"',
        'class="pear-runtime-menu-effort-label"',
        "zulip-icon zulip-icon-chevron-down pear-topic-header-control-chevron",
    ]) {
        assert.ok(html.includes(marker), `missing: ${marker}`);
    }
    // The glyph and separator spans wrap across lines under prettier.
    assert.match(html, /class="pear-ui-glyph pear-runtime-menu-glyph"\s+aria-hidden="true"\s*>·</);
    assert.match(html, /class="pear-topic-header-runtime-separator"\s+aria-hidden="true"\s*>·</);
    // The runtime button and the branch/work controls carry the header key.
    assert.equal(html.split("data-pear-header-key").length - 1, 4);
    // Two header controls plus Chat, README, and Files.
    assert.equal((html.match(/<button\s+type="button"/g) ?? []).length, 5);
    for (const marker of [
        'class="pear-workspace-tabs pear-workspace-tabs-hidden"',
        'data-pear-tab="chat"',
        'data-pear-tab="readme"',
        'data-pear-tab="files"',
    ]) {
        assert.ok(html.includes(marker), `missing: ${marker}`);
    }
    // Exactly one runtime trigger remains: the merged model·effort pill.
    assert.equal(html.split("pear-runtime-menu-trigger").length - 1, 1);
});
