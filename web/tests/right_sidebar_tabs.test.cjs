"use strict";

const assert = require("node:assert/strict");

const {JSDOM} = require("jsdom");

const {set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const dom = new JSDOM(
    `<!doctype html><html><body>
        <div class="right-sidebar-tabs" role="tablist">
            <button type="button" class="right-sidebar-tab" data-tab="agents" aria-selected="false"></button>
            <button type="button" class="right-sidebar-tab right-sidebar-tab-selected" data-tab="people" aria-selected="true"></button>
        </div>
    </body></html>`,
);
set_global("document", dom.window.document);

const right_sidebar_tabs = zrequire("right_sidebar_tabs");

run_test("set_tab flips selection and body class; no-op repeats", () => {
    const [agents, people] = document.querySelectorAll(".right-sidebar-tab");
    const seen = [];
    right_sidebar_tabs.on_tab_changed((tab) => seen.push(tab));

    assert.equal(right_sidebar_tabs.current(), "people");
    right_sidebar_tabs.set_tab("agents");
    assert.equal(right_sidebar_tabs.current(), "agents");
    assert.deepEqual(seen, ["agents"]);
    assert.equal(document.body.classList.contains("right-sidebar-tab-agents"), true);
    assert.equal(agents.classList.contains("right-sidebar-tab-selected"), true);
    assert.equal(agents.getAttribute("aria-selected"), "true");
    assert.equal(people.classList.contains("right-sidebar-tab-selected"), false);
    assert.equal(people.getAttribute("aria-selected"), "false");

    right_sidebar_tabs.set_tab("agents");
    assert.deepEqual(seen, ["agents"]);

    right_sidebar_tabs.set_tab("people");
    assert.equal(document.body.classList.contains("right-sidebar-tab-agents"), false);
    assert.deepEqual(seen, ["agents", "people"]);

    right_sidebar_tabs.clear_for_testing();
    assert.equal(right_sidebar_tabs.current(), "people");
    assert.equal(document.body.classList.contains("right-sidebar-tab-agents"), false);
});
