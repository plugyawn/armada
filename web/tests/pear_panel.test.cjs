"use strict";

const assert = require("node:assert/strict");

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

run_test("work_cursor_state_for_testing", () => {
    assert.deepEqual(pear_panel.work_cursor_state_for_testing(undefined), {
        visible: false,
        status_class: "",
        status_marker: "",
        repo_label: "",
        branch_label: "",
        head_label: "",
        status_label: "",
        title: "",
    });

    assert.deepEqual(
        pear_panel.work_cursor_state_for_testing({
            stream: "devel",
            topic: "pear",
            model: "gpt-5.5",
            git: {
                available: true,
                branch: "main",
                head: "abc1234",
                dirty: false,
            },
            github_repo: "zulip/zulip",
        }),
        {
            visible: true,
            status_class: "clean",
            status_marker: "✓",
            repo_label: "zulip/zulip",
            branch_label: "main",
            head_label: "abc1234",
            status_label: "translated: clean",
            title: "zulip/zulip / main / abc1234 / translated: clean",
        },
    );

    assert.equal(
        pear_panel.work_cursor_state_for_testing({
            stream: "devel",
            topic: "pear",
            model: "gpt-5.5",
            git: {
                available: true,
                dirty: true,
                changed_count: 4,
            },
            repo_label: "Megatron-LM",
            github_repo: "",
        }).status_label,
        "translated: +4",
    );
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
            active_run_status: undefined,
            has_error: true,
        }),
        true,
    );
});
