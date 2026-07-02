"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pear_dom_patch = zrequire("pear_dom_patch");
// pear_panel.render_panel_html renders the real pear_panel.hbs
// template, so these tests exercise actual panel markup.
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
        codex_thread_id: "thread-1",
        usage: {
            input_tokens: 1200,
            cached_input_tokens: 1100,
            output_tokens: 300,
            reasoning_output_tokens: 80,
            cache_percent: 91.7,
            duration_seconds: 12.5,
        },
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
        repo_label: "zulip",
        workdir: "/srv/pear/devel-pear-smoke",
        git: make_git(),
        ...overrides,
    };
}

function make_state(overrides = {}) {
    return {
        runs: [],
        topic_context: make_topic_context(),
        stream: "devel",
        topic: "pear-smoke",
        error: "",
        ...overrides,
    };
}

// The state sequence a real panel session walks through: no topic,
// context loads, runs start and stream (two at once — no single-run
// pick), the delta dirties/diverges, a model error appears and
// the runs finish (leaving a detached, mismatched worktree), then a
// fetch error shows up.
function lifecycle_states() {
    return [
        make_state({
            topic_context: undefined,
            stream: undefined,
            topic: undefined,
        }),
        make_state(),
        make_state({runs: [make_run(7, "queued")]}),
        make_state({
            runs: [make_run(8, "running", {session_kind: "side"}), make_run(7, "running")],
            topic_context: make_topic_context({
                git: make_git({
                    dirty: true,
                    changed_count: 2,
                    ahead: 2,
                    behind: 1,
                }),
            }),
        }),
        make_state({runs: [make_run(7, "running")]}),
        make_state({
            runs: [make_run(7, "done")],
            topic_context: make_topic_context({
                git: make_git({
                    branch: "",
                    detached: true,
                    branch_matches_topic: false,
                }),
            }),
        }),
        make_state({
            runs: [make_run(7, "done")],
            topic_context: make_topic_context({
                git: make_git({
                    branch: "hotfix-thing",
                    branch_matches_topic: false,
                }),
            }),
            error: "Pear status is unavailable.",
        }),
    ];
}

function make_panel_root() {
    const dom = new JSDOM("<!doctype html><html><body><div id='pear-panel'></div></body></html>");
    const document = dom.window.document;
    return {document, root: document.querySelector("#pear-panel")};
}

run_test("patch_vs_render_equivalence", () => {
    const {document, root} = make_panel_root();

    for (const [i, state] of lifecycle_states().entries()) {
        const html = pear_panel.render_panel_html(state);
        pear_dom_patch.patch_html(root, html);

        // After N patched updates, the live DOM must equal a full
        // from-scratch render of the latest state.
        const reference = document.createElement("div");
        reference.id = "pear-panel";
        reference.innerHTML = html;
        assert.ok(
            root.isEqualNode(reference),
            `patched DOM diverged from a full render at state ${i}:\n` +
                `patched:\n${root.innerHTML}\n\nfull render:\n${reference.innerHTML}`,
        );
    }
});

run_test("patch_preserves_focus_identity_and_draft", () => {
    const {document, root} = make_panel_root();
    const states = lifecycle_states();

    pear_dom_patch.patch_html(root, pear_panel.render_panel_html(states[3]));

    const textarea = document.querySelector("#pear-side-request");
    assert.ok(textarea !== null);
    textarea.value = "look into the flaky test";
    textarea.focus();
    assert.equal(document.activeElement, textarea);

    // Live ticks that flip run/worktree state must not recreate, blank,
    // or blur the side-request textarea.
    pear_dom_patch.patch_html(root, pear_panel.render_panel_html(states[4]));
    pear_dom_patch.patch_html(root, pear_panel.render_panel_html(states[5]));

    assert.equal(document.querySelector("#pear-side-request"), textarea);
    assert.equal(textarea.value, "look into the flaky test");
    assert.equal(document.activeElement, textarea);
});
