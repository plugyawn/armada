"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");

const channel = mock_esm("../src/channel");

// The tippy wiring is inert here: initialize() guards on the missing
// DOM, so the tippyjs/ui_util imports are never exercised and need no
// mocks.
const pear_git_hover = zrequire("pear_git_hover");

function make_tree(overrides = {}) {
    return {
        available: true,
        workdir: "/srv/pear/devel-pear-smoke",
        graph: "* abc1234 (HEAD -> pear-smoke) Ship the thing",
        status_short: " M web/src/pear_panel.ts",
        ...overrides,
    };
}

run_test("fetch_git_tree_fetches_once_and_caches", async () => {
    pear_git_hover.clear_cache();
    const get_calls = [];
    channel.get = (opts) => {
        get_calls.push(opts);
        opts.success({git_tree: make_tree()});
    };

    const tree = await pear_git_hover.fetch_git_tree("devel", "pear-smoke");
    assert.equal(get_calls.length, 1);
    assert.equal(get_calls[0].url, "/json/pear/git-tree");
    assert.deepEqual(get_calls[0].data, {stream: "devel", topic: "pear-smoke"});
    assert.equal(tree.graph, "* abc1234 (HEAD -> pear-smoke) Ship the thing");

    // A second hover on the same topic is served from the cache — the
    // endpoint is only ever hit on demand, never polled or refetched
    // within the TTL.
    const cached_tree = await pear_git_hover.fetch_git_tree("devel", "pear-smoke");
    assert.equal(get_calls.length, 1);
    assert.deepEqual(cached_tree, tree);

    // A different topic is a different cache entry.
    await pear_git_hover.fetch_git_tree("devel", "pear-other");
    assert.equal(get_calls.length, 2);
});

run_test("fetch_git_tree_dedupes_inflight_requests", async () => {
    pear_git_hover.clear_cache();
    const pending = [];
    channel.get = (opts) => {
        pending.push(opts);
    };

    const first = pear_git_hover.fetch_git_tree("devel", "pear-smoke");
    const second = pear_git_hover.fetch_git_tree("devel", "pear-smoke");
    assert.equal(pending.length, 1);

    pending[0].success({git_tree: make_tree()});
    const [first_tree, second_tree] = await Promise.all([first, second]);
    assert.deepEqual(first_tree, second_tree);
});

run_test("fetch_git_tree_error_does_not_poison_cache", async () => {
    pear_git_hover.clear_cache();
    const get_calls = [];
    let should_fail = true;
    channel.get = (opts) => {
        get_calls.push(opts);
        if (should_fail) {
            opts.error({readyState: 4, responseText: "nope"});
        } else {
            opts.success({git_tree: make_tree()});
        }
    };

    blueslip.expect("warn", "Failed to fetch Pear git tree: nope");
    await assert.rejects(pear_git_hover.fetch_git_tree("devel", "pear-smoke"));
    assert.equal(get_calls.length, 1);

    // The failure is not cached; the next hover retries.
    should_fail = false;
    const tree = await pear_git_hover.fetch_git_tree("devel", "pear-smoke");
    assert.equal(get_calls.length, 2);
    assert.equal(tree.available, true);
});

run_test("popover_template_states", () => {
    // The popover template renders the loading, graph, and
    // unavailable states (graph text is escaped, not markup).
    const render = require("../templates/pear_git_tree_popover.hbs");

    const loading_html = render({loading: true});
    assert.ok(loading_html.includes("translated: Loading commit tree…"));

    const graph_html = render({
        available: true,
        graph: "* abc1234 (HEAD -> pear-smoke) Merge <script>alert(1)</script>",
        status_short: " M web/src/pear_panel.ts",
    });
    assert.ok(graph_html.includes("pear-git-tree-graph"));
    assert.ok(graph_html.includes("pear-git-tree-status"));
    assert.ok(!graph_html.includes("<script>"));

    const unavailable_html = render({available: false});
    assert.ok(unavailable_html.includes("translated: Commit tree unavailable."));
});
