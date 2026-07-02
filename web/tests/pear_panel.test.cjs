"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

// pear_panel.render_panel_html renders the real pear_panel.hbs
// template, so these tests assert on actual panel markup.
const pear_panel = zrequire("pear_panel");

function make_run(id, status, overrides = {}) {
    return {
        id,
        status,
        session_kind: "shared",
        source_stream: "devel",
        source_topic: "pear-smoke",
        summary: "Ship the thing",
        current_activity: "Running tests",
        runtime_label: "model=gpt-5.2-codex",
        sandbox: "workspace-write",
        approval_policy: "never",
        workdir: "/srv/pear/devel-pear-smoke",
        codex_thread_id: `thread-${id}`,
        ...overrides,
    };
}

function make_git(overrides = {}) {
    return {
        available: true,
        workdir: "/srv/pear/devel-pear-smoke",
        repo_name: "devel-pear-smoke",
        worktree_name: "devel-pear-smoke",
        branch: "pear-smoke",
        head: "abc1234",
        head_short: "abc1234",
        upstream: "origin/pear-smoke",
        ahead: 0,
        behind: 0,
        dirty: false,
        changed_count: 0,
        detached: false,
        branch_matches_topic: true,
        ...overrides,
    };
}

function make_topic_context(overrides = {}) {
    return {
        stream: "devel",
        topic: "pear-smoke",
        model: "gpt-5.2-codex",
        default_model: "gpt-5.2-codex",
        model_choices: ["gpt-5.2-codex", "gpt-5.2"],
        reasoning_effort: "medium",
        default_reasoning_effort: "medium",
        reasoning_efforts: [
            {effort: "low", label: "Low"},
            {effort: "medium", label: "Medium"},
            {effort: "high", label: "High"},
        ],
        github_repo: "zulip/zulip",
        workdir: "/srv/pear/devel-pear-smoke",
        git: make_git(),
        ...overrides,
    };
}

function render_to_dom(state_overrides = {}) {
    const html = pear_panel.render_panel_html({
        runs: [],
        topic_context: make_topic_context(),
        stream: "devel",
        topic: "pear-smoke",
        error: "",
        model_error: "",
        ...state_overrides,
    });
    const dom = new JSDOM(`<!doctype html><html><body>${html}</body></html>`);
    return dom.window.document;
}

run_test("multi_run_rows_no_single_run_pick", () => {
    // Two live runs plus a finished one: every ACTIVE run gets its own
    // status+verbs row; nothing is silently hidden behind a single pick.
    const document = render_to_dom({
        runs: [
            make_run(9, "running", {session_kind: "side"}),
            make_run(8, "paused"),
            make_run(7, "done"),
        ],
    });

    const rows = [...document.querySelectorAll(".pear-run-row")];
    assert.deepEqual(
        rows.map((row) => row.getAttribute("data-run-id")),
        ["9", "8"],
    );

    // Each row carries its own verbs, targeted at that run.
    const run_9_actions = [
        ...document.querySelectorAll('.pear-run-row[data-run-id="9"] .pear-run-action'),
    ];
    assert.deepEqual(
        run_9_actions.map((button) => button.getAttribute("data-action")),
        ["pause", "cancel"],
    );
    const run_8_actions = [
        ...document.querySelectorAll('.pear-run-row[data-run-id="8"] .pear-run-action'),
    ];
    assert.deepEqual(
        run_8_actions.map((button) => button.getAttribute("data-action")),
        ["resume", "cancel"],
    );

    // The side session is labeled as such.
    assert.ok(
        document.querySelector('.pear-run-row[data-run-id="9"] .pear-run-session-kind') !== null,
    );
    assert.ok(
        document.querySelector('.pear-run-row[data-run-id="8"] .pear-run-session-kind') === null,
    );
});

run_test("terminal_run_still_visible_when_nothing_active", () => {
    const document = render_to_dom({
        runs: [make_run(7, "done"), make_run(6, "failed")],
    });
    const rows = [...document.querySelectorAll(".pear-run-row")];
    assert.deepEqual(
        rows.map((row) => row.getAttribute("data-run-id")),
        ["7"],
    );
    // Terminal runs have no verbs.
    assert.equal(document.querySelectorAll(".pear-run-action").length, 0);
});

run_test("git_delta_row_clean", () => {
    const document = render_to_dom({});
    const row = document.querySelector(".pear-git-delta-row");
    assert.ok(row !== null);
    assert.ok(row.classList.contains("pear-git-clean"));
    assert.equal(
        document.querySelector(".pear-git-delta-status").textContent.trim(),
        "translated: Clean",
    );
    // No badges on a clean, in-sync, topic-pinned worktree.
    assert.equal(document.querySelectorAll(".pear-git-delta-chip").length, 0);

    // The pill is the commit-tree hover affordance, keyed for the
    // git-tree fetch cache.
    const target = document.querySelector(".pear-git-tree-target");
    assert.equal(target.getAttribute("data-stream"), "devel");
    assert.equal(target.getAttribute("data-topic"), "pear-smoke");
    assert.equal(document.querySelector(".pear-git-delta-head").textContent.trim(), "abc1234");
});

run_test("git_delta_row_dirty_diverged_mismatch", () => {
    const document = render_to_dom({
        topic_context: make_topic_context({
            git: make_git({
                dirty: true,
                changed_count: 3,
                ahead: 2,
                behind: 1,
                branch: "hotfix-thing",
                branch_matches_topic: false,
            }),
        }),
    });
    const row = document.querySelector(".pear-git-delta-row");
    assert.ok(row.classList.contains("pear-git-dirty"));
    assert.ok(document.querySelector(".pear-git-delta-status").textContent.includes("3"));
    assert.equal(document.querySelector(".pear-git-chip-ahead-behind").textContent.trim(), "↑2 ↓1");
    assert.ok(document.querySelector(".pear-git-chip-mismatch") !== null);
    assert.ok(document.querySelector(".pear-git-chip-detached") === null);
});

run_test("git_delta_row_detached", () => {
    const document = render_to_dom({
        topic_context: make_topic_context({
            git: make_git({
                branch: "",
                detached: true,
                branch_matches_topic: false,
            }),
        }),
    });
    assert.ok(document.querySelector(".pear-git-chip-detached") !== null);
    // Detached already implies no matching branch; the mismatch badge
    // stays quiet rather than doubling up.
    assert.ok(document.querySelector(".pear-git-chip-mismatch") === null);
});

run_test("no_workspace_restatement", () => {
    // R3: the chat header is authoritative for stream > topic ==
    // repo > branch; the panel must not restate the workspace name.
    const document = render_to_dom({});
    assert.equal(document.querySelectorAll('[class*="pear-workspace"]').length, 0);
    assert.equal(document.querySelectorAll(".pear-worktree-details").length, 0);
    assert.ok(!document.body.textContent.includes("devel-pear-smoke"));
});

run_test("git_unavailable", () => {
    const document = render_to_dom({
        topic_context: make_topic_context({
            git: {available: false, workdir: "/srv/pear/devel-pear-smoke"},
        }),
    });
    assert.equal(document.querySelectorAll(".pear-git-delta-row").length, 0);
    assert.ok(document.querySelector(".pear-git-section .pear-empty-state") !== null);
});
