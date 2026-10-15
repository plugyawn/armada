"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {mock_esm, mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM(
    "<!doctype html><html><body><div id='host'></div><span id='status'></span></body></html>",
);
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    HTMLElement: window.HTMLElement,
})) {
    set_global(name, value);
}
mock_jquery(jQueryFactory(window));

const requests = [];
let rendered_markdown_updates = 0;
mock_esm("../src/channel", {
    get(options) {
        const request = {
            aborted: false,
            abort() {
                this.aborted = true;
            },
            options,
        };
        requests.push(request);
        return request;
    },
});
mock_esm("../src/rendered_markdown", {
    update_elements() {
        rendered_markdown_updates += 1;
    },
});
mock_esm("../src/ui_util", {
    parse_html(html) {
        const template = document.createElement("template");
        template.innerHTML = html;
        return template.content;
    },
});

const legacy_run_trace = zrequire("legacy_run_trace");

function reset() {
    requests.length = 0;
    rendered_markdown_updates = 0;
    document.querySelector("#host").replaceChildren();
    document.querySelector("#status").textContent = "";
}

run_test("loads one shared root/child trace contract and coalesces in-flight events", () => {
    reset();
    const host = document.querySelector("#host");
    const status = document.querySelector("#status");
    let after_render_count = 0;
    const controller = legacy_run_trace.create_controller({
        run_id: 7,
        thread_id: "child-2",
        host,
        status,
        after_render() {
            after_render_count += 1;
        },
    });

    controller.load();
    controller.mark_dirty(true);
    assert.equal(requests.length, 1, "an event during a request is single-flight");
    assert.deepEqual(requests[0].options.data, {limit: 100, thread_id: "child-2"});

    requests[0].options.success({rendered: "<p>First</p>", trace: []});
    assert.equal(requests.length, 2, "the dirty event schedules one follow-up request");
    assert.notEqual(host.textContent, "First", "a known-stale trace is never painted");
    assert.equal(after_render_count, 0);
    assert.equal(rendered_markdown_updates, 0);
    requests[1].options.success({rendered: "<p>Latest</p>", trace: []});
    assert.equal(host.textContent, "Latest");
    assert.equal(after_render_count, 1);
    assert.equal(rendered_markdown_updates, 1);
    assert.equal(controller.loaded, true);
    assert.equal(controller.request_state, "ready");
});

run_test("destroy aborts the active request and rejects its stale success", () => {
    reset();
    const host = document.querySelector("#host");
    const controller = legacy_run_trace.create_controller({run_id: 9, host});
    controller.load();
    const request = requests[0];
    controller.destroy();
    assert.equal(request.aborted, true);
    request.options.success({rendered: "<p>Stale</p>", trace: []});
    assert.notEqual(host.textContent, "Stale");
});

run_test("pure trace selection helpers retain the root disclosure behavior", () => {
    assert.equal(legacy_run_trace.should_load_on_open_for_testing(true, false, false), false);
    assert.equal(legacy_run_trace.should_load_on_open_for_testing(true, true, false), true);
    assert.equal(legacy_run_trace.event_should_load_for_testing(false), false);
    assert.equal(
        legacy_run_trace.duplicate_tail_count_for_testing(["P:old", "P:answer"], ["P:answer"]),
        1,
    );
});
