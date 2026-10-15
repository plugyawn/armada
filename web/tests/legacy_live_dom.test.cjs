"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>");
set_global("document", dom.window.document);
set_global("HTMLElement", dom.window.HTMLElement);

const live_dom = zrequire("legacy_live_dom");

run_test("progress identity ignores runtime-only glyph animation state", () => {
    const plan = [
        {step: "Inspect", status: "completed"},
        {step: "Verify", status: "in_progress"},
    ];
    const signature = live_dom.pear_live_progress_signature(plan, "Running focused tests");
    const current = document.createElement("section");
    current.dataset.pearProgressSignature = signature;
    const glyph = document.createElement("span");
    glyph.className = "pear-thinking-glyph pear-thinking-glyph-animated";
    current.append(glyph);

    assert.equal(live_dom.pear_live_progress_matches(current, signature), true);
    assert.equal(
        live_dom.pear_live_progress_matches(
            current,
            live_dom.pear_live_progress_signature([...plan], "Reading focused results"),
        ),
        false,
    );
});

run_test("unchanged overview keeps its DOM identity", () => {
    const current = document.createElement("section");
    const overview = document.createElement("div");
    overview.className = "pear-live-overview";
    overview.dataset.pearOverviewKey = '[7,"Verify","Running focused tests"]';
    overview.dataset.animationStarted = "true";
    current.append(overview);

    const next = document.createElement("section");
    const replacement = document.createElement("div");
    replacement.className = "pear-live-overview";
    replacement.dataset.pearOverviewKey = overview.dataset.pearOverviewKey;
    next.append(replacement);

    assert.equal(live_dom.preserve_unchanged_pear_overview(current, next), true);
    assert.equal(next.firstElementChild, overview);
    assert.equal(next.firstElementChild.dataset.animationStarted, "true");
});

run_test("changed overview receives a new DOM node", () => {
    const current = document.createElement("section");
    const overview = document.createElement("div");
    overview.className = "pear-live-overview";
    overview.dataset.pearOverviewKey = '[7,"Verify","Running tests"]';
    current.append(overview);

    const next = document.createElement("section");
    const replacement = document.createElement("div");
    replacement.className = "pear-live-overview";
    replacement.dataset.pearOverviewKey = '[7,"Verify","Reading results"]';
    next.append(replacement);

    assert.equal(live_dom.preserve_unchanged_pear_overview(current, next), false);
    assert.equal(next.firstElementChild, replacement);
});
