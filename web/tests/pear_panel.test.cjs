"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const render_pear_panel = require("../templates/pear_panel.hbs");

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

run_test("status_marker_for_testing", () => {
    assert.equal(pear_panel.status_marker_for_testing("running"), "✦");
    assert.equal(pear_panel.status_marker_for_testing("queued"), "✦");
    assert.equal(pear_panel.status_marker_for_testing("paused"), "✧");
    assert.equal(pear_panel.status_marker_for_testing("failed"), "✷");
    assert.equal(pear_panel.status_marker_for_testing("cancelled"), "✷");
    assert.equal(pear_panel.status_marker_for_testing("done"), "·");
    assert.equal(pear_panel.status_marker_for_testing("unknown"), "·");
});

run_test("runtime_chip_labels_for_testing", () => {
    const topic_context = {
        model: "gpt-5.5",
        model_catalog: [
            {
                slug: "gpt-5.5",
                display_name: "GPT-5.5",
            },
        ],
        reasoning_effort: "medium",
        reasoning_efforts: [
            {
                effort: "medium",
                label: "Medium",
            },
        ],
    };

    assert.deepEqual(pear_panel.runtime_chip_labels_for_testing(topic_context), {
        label: "GPT-5.5 · m",
        title: "GPT-5.5 · Medium",
    });
    assert.deepEqual(
        pear_panel.runtime_chip_labels_for_testing({
            model: "custom-model",
            reasoning_effort: "xhigh",
            reasoning_efforts: [],
        }),
        {
            label: "custom-model · xh",
            title: "custom-model · xhigh",
        },
    );
});

run_test("context_meter_for_testing", () => {
    assert.deepEqual(pear_panel.context_meter_for_testing(undefined), {
        has_meter: false,
        percent: 0,
        style: "--pear-context-meter-percent: 0%;",
        label: "",
        value_label: "",
        is_hot: false,
    });

    assert.deepEqual(pear_panel.context_meter_for_testing({cache_percent: 38.4}), {
        has_meter: true,
        percent: 38.4,
        style: "--pear-context-meter-percent: 38.4%;",
        label: "translated: Cached context 38%",
        value_label: "38%",
        is_hot: false,
    });

    assert.deepEqual(pear_panel.context_meter_for_testing({cache_percent: 91.8}), {
        has_meter: true,
        percent: 91.8,
        style: "--pear-context-meter-percent: 91.8%;",
        label: "translated: Cached context 92%",
        value_label: "92%",
        is_hot: true,
    });

    assert.deepEqual(pear_panel.context_meter_for_testing({cache_percent: 150}), {
        has_meter: true,
        percent: 100,
        style: "--pear-context-meter-percent: 100%;",
        label: "translated: Cached context 100%",
        value_label: "100%",
        is_hot: true,
    });
});

run_test("template renders workbench without active run", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        panel_can_close: true,
        active_run: undefined,
        topic_context: {
            workbench_title: "general chat",
            workbench_title_attr: "/srv/pear-workspaces/devel-general-chat",
            workbench_meta_label: "",
            workbench_branch_meta_label: "devel / general-chat / abc1234",
            workbench_head_label: "HEAD+1",
            workbench_head_title: "HEAD+1 local edits abc1234",
            workbench_head_summary: "local edits",
            workbench_head_short: "abc1234",
            workbench_diff_selected: false,
            workbench_history_selected: true,
            workbench_side_selected: false,
            workbench_diff_disabled: false,
            workbench_history_disabled: false,
            diff_tab_label: "diff",
            history_tab_label: "history",
            side_tab_label: "whisper",
            runtime_chip_title: "GPT-5.5 · XHigh",
            runtime_chip_label: "GPT-5.5 · xh",
            workbench_view: "history",
            git_tree_loading: false,
            git_tree_error: "",
            has_git_tree_graph: false,
            has_recent_commits: false,
            has_workbench_diffstat: true,
            workbench_diff_summary_label: "2 files +16 -4",
            workbench_diff_file_label: "2 files",
            workbench_diff_insertions_label: "+16",
            workbench_diff_deletions_label: "-4",
        },
    });

    assert.match(html, /pear-workbench/);
    assert.match(html, /pear-workbench-context-row/);
    assert.match(html, /pear-workbench-head-row/);
    assert.match(html, /HEAD\+1/);
    assert.match(html, /local edits/);
    assert.match(html, /abc1234/);
    assert.match(html, /pear-workbench-diffstat/);
    assert.match(html, /\+16/);
    assert.match(html, /-4/);
    assert.doesNotMatch(html, /pear-workbench-context-meter/);
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
        null,
    );
});

run_test("panel_header_state_for_testing", () => {
    assert.deepEqual(pear_panel.panel_header_state_for_testing(undefined), {
        show_panel_header: false,
        has_panel_menu: false,
    });
    assert.deepEqual(
        pear_panel.panel_header_state_for_testing({
            show_status: false,
            has_actions: false,
        }),
        {
            show_panel_header: false,
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
    assert.equal(pear_panel.workbench_view_for_artifact_label("Repository"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Branch"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Codex session"), "history");
    assert.equal(pear_panel.workbench_view_for_artifact_label("Verification"), undefined);
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
        true,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "queued",
            has_error: false,
        }),
        true,
    );
    assert.equal(
        pear_panel.work_panel_open_for_testing({
            context_key_value: "devel\u0000pear",
            manually_open: false,
            active_run_status: "paused",
            has_error: false,
        }),
        true,
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
        }),
        true,
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
        }),
        true,
    );
});
