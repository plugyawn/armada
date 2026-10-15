"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>");
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    HTMLElement: window.HTMLElement,
    AbortController: window.AbortController,
    MutationObserver: window.MutationObserver,
})) {
    set_global(name, value);
}
const $ = jQueryFactory(window);
mock_jquery($);

const pear_wall = zrequire("legacy_wall");

let monitor_version = 1;
const monitor = () => ({
    id: 7,
    title: "Build status",
    ask: "Summarize the build",
    status: "live",
    version: monitor_version,
    cadence_seconds: 60,
    artifact_spec: {version: 1, blocks: [{type: "copy", body: "Build is healthy."}]},
    data: {},
    last_error: "",
    position: 0,
});

run_test("unchanged monitor versions skip artifact serialization, validation, and remount", () => {
    document.body.innerHTML = `
        <div id="wall">
            <section class="pear-wall-block" data-monitor-id="7">
                <div data-pear-artifact-host></div>
            </section>
        </div>`;
    const $wall = $(document.querySelector("#wall"));
    let parses = 0;
    let mounts = 0;
    const module = {
        parse_pear_artifact(raw) {
            parses += 1;
            return JSON.parse(raw);
        },
        mount_pear_artifact($host, document_value) {
            mounts += 1;
            const artifact = document.createElement("article");
            artifact.className = "pear-artifact";
            artifact.dataset.lifecycle = "ready";
            artifact.textContent = document_value.blocks[0].body;
            $host.empty();
            $host[0].append(artifact);
            return artifact;
        },
    };
    const native_stringify = JSON.stringify;
    let artifact_serializations = 0;
    JSON.stringify = (value, ...args) => {
        if (value?.version === 1 && Array.isArray(value.blocks)) {
            artifact_serializations += 1;
        }
        return native_stringify(value, ...args);
    };
    try {
        pear_wall.render_artifacts($wall, [monitor()], module);
        assert.equal(parses, 1);
        assert.equal(artifact_serializations, 1);
        assert.equal(mounts, 1);
        assert.equal($wall.find(".pear-artifact").text(), "Build is healthy.");

        artifact_serializations = 0;
        pear_wall.render_artifacts($wall, [monitor()], module);
        assert.equal(parses, 1);
        assert.equal(artifact_serializations, 0);
        assert.equal(mounts, 1);

        monitor_version += 1;
        pear_wall.render_artifacts($wall, [monitor()], module);
        assert.equal(parses, 2);
        assert.equal(artifact_serializations, 1);
        assert.equal(mounts, 2);
    } finally {
        JSON.stringify = native_stringify;
    }
});
