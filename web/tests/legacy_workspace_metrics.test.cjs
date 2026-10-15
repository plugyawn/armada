"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const metrics = zrequire("legacy_workspace_metrics");

run_test("workspace metrics expose durations and error counters without document data", () => {
    metrics.reset_pear_workspace_metrics_for_testing();
    metrics.begin_pear_workspace_metric("workspace_file_interactive");
    metrics.finish_pear_workspace_metric("workspace_file_interactive");
    metrics.record_pear_workspace_error(429);

    const snapshot = metrics.pear_workspace_metrics_snapshot();
    assert.deepEqual(snapshot.counts, {
        workspace_file_interactive: 1,
        workspace_error: 1,
        workspace_429: 1,
    });
    assert.ok(snapshot.lastDurationsMs.workspace_file_interactive >= 0);
    assert.equal(JSON.stringify(snapshot).includes("README"), false);
});
