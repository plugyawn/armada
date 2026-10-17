"use strict";

// A plugin's Recent in words (web/src/armada/beta/plugin_recent.js,
// design/r9/plugins/DESIGN.md §5): each line the server keeps of what was
// done to an install, said as the design says it. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {recentLine} = zrequire("armada/beta/plugin_recent.js");

const IAGO = 11;
const ONIX = 30;
const BOT = 41;
const names = {[IAGO]: "Iago", [ONIX]: "Onix", [BOT]: "Weights & Biases"};
const ctx = {
    nameOf: (id) => names[id],
    secretTitle: (name) => ({api_key: "API key", ssh_key: "SSH key"})[name] ?? name,
    streamName: (id) => ({10: "nextlat-scaling"})[id] ?? "",
};
const line = (type, detail = {}, extra = {}) =>
    recentLine(
        {id: 1, type, acting_user_id: IAGO, time: 0, stream_id: null, detail, ...extra},
        ctx,
    );

run_test("who did what to the install, in the design's words", () => {
    assert.deepEqual(line("installed", {version: "0.4.0"}), {
        mark: null,
        words: "Iago installed 0.4.0",
    });
    assert.equal(line("installed").words, "Iago installed it");
    assert.equal(line("made", {to_version: "0.1.0", for_user_id: IAGO}).words, "Iago made 0.1.0");
    assert.equal(
        line("made", {to_version: "0.1.0", for_user_id: IAGO}, {acting_user_id: ONIX}).words,
        "Onix made 0.1.0 for Iago",
    );
    assert.equal(line("made").words, "Iago made it");
    assert.equal(
        line("updated", {from_version: "0.3.0", to_version: "0.3.1"}).words,
        "Iago updated it from 0.3.0 to 0.3.1",
    );
    assert.equal(line("updated", {to_version: "0.3.1"}).words, "Iago updated it");
    assert.equal(line("removed").words, "Iago removed it");
    assert.equal(line("restored").words, "Iago put it back");
    // A link: the project, as code, and where.
    assert.equal(
        line("linked", {value: "runix/nextlat-scaling", topic: null}, {stream_id: 10}).words,
        "Iago linked <code>runix/nextlat-scaling</code> to nextlat-scaling",
    );
    assert.equal(
        line("linked", {value: "runix/muon", topic: "muon-ablation"}, {stream_id: 10}).words,
        "Iago linked <code>runix/muon</code> to nextlat-scaling › muon-ablation",
    );
    // A channel the reader cannot name is left out.
    assert.equal(
        line("linked", {value: "runix/muon"}, {stream_id: 99}).words,
        "Iago linked <code>runix/muon</code>",
    );
    assert.equal(
        line("unlinked", {value: "runix/muon"}).words,
        "Iago unlinked <code>runix/muon</code>",
    );
    // A line of a type this does not know yet still says something.
    assert.equal(line("published_index").words, "Iago published index");
});

run_test("tools: who used one, and whether it failed", () => {
    assert.deepEqual(line("tool_called", {tool: "compare_runs"}, {acting_user_id: ONIX}), {
        mark: null,
        words: "Onix used <code>compare_runs</code>",
    });
    assert.deepEqual(line("tool_called", {tool: "link_run", via: "mcp", is_error: true}), {
        mark: "failed",
        words: "Iago used <code>link_run</code>, which failed",
    });
});

run_test("started: the plugin's own line, behind its mark, with no one's name", () => {
    assert.deepEqual(line("started", {version: "0.4.0"}, {acting_user_id: BOT}), {
        mark: "done",
        words: "Started 0.4.0",
    });
    assert.equal(line("started", {}, {acting_user_id: BOT}).words, "Started");
    // A line with no one acting begins with a capital.
    assert.equal(line("removed", {}, {acting_user_id: null}).words, "Removed it");
});

run_test("a settings change: each part it made, by the secrets' titles", () => {
    assert.equal(
        line("settings_changed", {settings: {old: {}, new: {entity: "runix"}}}).words,
        "Iago changed its settings",
    );
    assert.equal(
        line("settings_changed", {settings: {old: {}, new: {}}, secrets: ["api_key"]}).words,
        "Iago changed its settings and set API key",
    );
    assert.equal(
        line("settings_changed", {secrets: ["api_key", "ssh_key", "token"]}).words,
        "Iago set API key, SSH key and token",
    );
    assert.equal(
        line("settings_changed", {disabled: {old: false, new: true}}).words,
        "Iago turned it off",
    );
    assert.equal(
        line("settings_changed", {disabled: {old: true, new: false}}).words,
        "Iago turned it on",
    );
    assert.equal(
        line("settings_changed", {widget_defaults: {old: {}, new: {}}}).words,
        "Iago changed where it shows",
    );
    assert.equal(
        line("settings_changed", {
            disabled_tools: {old: ["compare_runs"], new: ["link_run", "run_logs"]},
        }).words,
        "Iago turned off <code>link_run</code> and <code>run_logs</code> and turned on <code>compare_runs</code>",
    );
    assert.equal(line("settings_changed", {secrets: []}).words, "Iago changed it");
    // What a plugin or a person names is escaped.
    assert.equal(
        line("settings_changed", {
            disabled_tools: {old: [], new: ["<b>x</b>"]},
        }).words,
        "Iago turned off <code>&lt;b&gt;x&lt;/b&gt;</code>",
    );
    // A line that records less than it should still reads as words.
    assert.equal(line("unlinked", {}, {detail: "not an object"}).words, "Iago unlinked a project");
    assert.equal(line("tool_called").words, "Iago used a tool");
});
