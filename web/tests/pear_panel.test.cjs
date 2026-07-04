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

run_test("template renders workbench without active run", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        panel_can_close: true,
        active_run: undefined,
        topic_context: {
            workbench_diff_selected: false,
            workbench_history_selected: true,
            workbench_side_selected: false,
            workbench_diff_disabled: false,
            workbench_history_disabled: false,
            diff_tab_label: "diff",
            history_tab_label: "history",
            side_tab_label: "whisper",
            workbench_view: "history",
            git_tree_loading: false,
            git_tree_error: "",
            has_git_tree_graph: false,
            has_recent_commits: false,
            workbench_diff_summary_label: "2 files +16 -4",
            workbench_diff_file_label: "2 files",
            workbench_diff_insertions_label: "+16",
            workbench_diff_deletions_label: "-4",
        },
    });

    assert.match(html, /pear-workbench/);
    assert.match(html, /pear-workbench-context-row/);
    assert.match(html, /pear-workbench-tabs/);
    assert.match(html, /pear-workbench-close/);
    assert.match(html, /diff/);
    assert.match(html, /history/);
    assert.match(html, /whisper/);
    assert.doesNotMatch(html, /pear-workbench-title/);
    assert.doesNotMatch(html, /pear-workbench-runtime/);
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
        panel_can_close: true,
        active_run_is_active: true,
        active_run: {
            show_status: false,
            is_active: true,
            usage: {cache_percent: 72},
        },
        topic_context: {
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
    assert.doesNotMatch(html, /pear-workbench-context-meter/);
    assert.doesNotMatch(html, /pear-workbench-head-row/);
    assert.doesNotMatch(html, /pear-thinking-glyph/);
    assert.doesNotMatch(html, /cache_percent/);
});

run_test("template keeps runtime metadata out of workbench content rail", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        panel_can_close: true,
        active_run: {
            show_status: true,
            usage: {cache_percent: 91},
        },
        topic_context: {
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
    assert.doesNotMatch(html, /GPT-5\.5/);
    assert.doesNotMatch(html, /pear-workbench-runtime/);
    assert.doesNotMatch(html, /No committed work yet/);
});

run_test("template renders diff rows without duplicate summary chips", () => {
    const html = render_pear_panel({
        panel_open: true,
        show_workbench: true,
        has_topic: true,
        show_panel_header: false,
        panel_can_close: true,
        active_run: undefined,
        topic_context: {
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
