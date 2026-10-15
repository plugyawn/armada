"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body><div id='host'></div></body></html>");
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    HTMLElement: window.HTMLElement,
    MutationObserver: window.MutationObserver,
})) {
    set_global(name, value);
}
const $ = jQueryFactory(window);
mock_jquery($);

const lifecycle = zrequire("legacy_artifact_lifecycle");

run_test("mounts one truthful shared lifecycle shell without authored content", () => {
    const $host = $(document.querySelector("#host"));
    lifecycle.mount_pear_artifact_lifecycle($host, "chat", "preparing", "preparing artifact…");
    const shell = document.querySelector(".pear-artifact-lifecycle");
    assert.equal(shell.dataset.density, "chat");
    assert.equal(shell.dataset.lifecycle, "preparing");
    assert.equal(shell.getAttribute("role"), "status");
    assert.equal(shell.querySelectorAll(".pear-artifact-lifecycle-cell").length, 3);
    assert.match(shell.textContent, /preparing artifact/);

    lifecycle.mount_pear_artifact_lifecycle($host, "wall", "failed", "update rejected");
    assert.equal(document.querySelectorAll(".pear-artifact-lifecycle").length, 1);
    assert.equal(document.querySelector(".pear-artifact-lifecycle").dataset.density, "wall");
    assert.equal(document.querySelector(".pear-artifact-lifecycle").getAttribute("role"), "alert");
});

run_test("uses one cleanup path for replacement, explicit unmount, and disconnection", async () => {
    const host = document.querySelector("#host");
    const $host = $(host);
    let cleanup_count = 0;

    lifecycle.replace_pear_artifact_host($host, document.createElement("article"), () => {
        cleanup_count += 1;
    });
    lifecycle.mount_pear_artifact_lifecycle($host, "chat", "preparing", "replacing");
    assert.equal(cleanup_count, 1);

    lifecycle.replace_pear_artifact_host(host, document.createElement("article"), () => {
        cleanup_count += 1;
    });
    lifecycle.unmount_pear_artifact($host);
    assert.equal(cleanup_count, 2);
    assert.equal(host.children.length, 0);

    lifecycle.replace_pear_artifact_host(host, document.createElement("article"), () => {
        cleanup_count += 1;
    });
    host.remove();
    await Promise.resolve();
    assert.equal(cleanup_count, 3);
    assert.equal(host.children.length, 0);
    document.body.append(host);
});

run_test("clears a host even when artifact cleanup fails", () => {
    const host = document.querySelector("#host");
    lifecycle.replace_pear_artifact_host(host, document.createElement("article"), () => {
        throw new Error("cleanup failed");
    });
    assert.throws(() => lifecycle.unmount_pear_artifact(host), /cleanup failed/);
    assert.equal(host.children.length, 0);
});
