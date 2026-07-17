"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const workspace_events = zrequire("pear_workspace_events");

run_test("Workspace revision listeners receive events until they unsubscribe", () => {
    const seen = [];
    const unsubscribe = workspace_events.on_pear_workspace_changed((event) => seen.push(event));
    const event = {
        type: "pear_workspace_changed",
        workspace_id: "workspace-a",
        revision: 2,
    };
    workspace_events.apply_pear_workspace_changed_event(event);
    unsubscribe();
    workspace_events.apply_pear_workspace_changed_event({...event, revision: 3});
    assert.deepEqual(seen, [event]);
});
