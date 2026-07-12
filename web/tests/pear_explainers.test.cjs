"use strict";

const assert = require("node:assert/strict");

const {jQueryFactory} = require("jquery/factory");
const {JSDOM} = require("jsdom");

const {mock_esm, mock_jquery, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://zulip.zulipdev.com/",
});
const {window} = dom;
for (const [name, value] of Object.entries({
    window,
    document: window.document,
    DOMParser: window.DOMParser,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    HTMLButtonElement: window.HTMLButtonElement,
    SVGElement: window.SVGElement,
})) {
    set_global(name, value);
}
const $ = jQueryFactory(window);
mock_jquery($);

const posts = [];
mock_esm("../src/channel", {
    post(request) {
        posts.push(request);
    },
});
mock_esm("../src/i18n", {$t: ({defaultMessage}) => defaultMessage});
mock_esm("../src/message_store", {
    get() {
        return {type: "stream", stream_id: 9, topic: "attention"};
    },
});
const actions = [];
mock_esm("../src/pear_explainer_actions", {
    send_pear_explainer_action(target, prompt, callbacks) {
        actions.push({target, prompt});
        callbacks.success();
    },
});
mock_esm("../src/stream_data", {get_sub_by_id: () => ({name: "devel"})});

const pear_explainers = zrequire("pear_explainers");
pear_explainers.initialize();

const safe_svg = '<svg viewBox="0 0 40 20"><rect width="40" height="20" class="psvg-card"/></svg>';

function render_row(language, source) {
    document.body.innerHTML = `<div class="pear-message message_row" data-message-id="7">
        <div class="message_content">
            <div class="codehilite" data-code-language="${language}" data-pear-source="fingerprint"><pre></pre></div>
        </div>
    </div>`;
    document.querySelector("pre").textContent = source;
    pear_explainers.enhance_pear_chat_explainers($(document.querySelector(".pear-message")));
}

run_test("enhances one explainer fence through the shared SVG and action runtime", () => {
    const spec = {
        title: "Attention",
        block: {
            components: [
                {type: "text", props: {eyebrow: "Tier 4", heading: "Animation", body: "Live"}},
                {type: "svg", props: {markup: safe_svg}},
            ],
            actions: [{label: "Explain", prompt: "Explain the highlighted edge"}],
        },
    };
    render_row("pear-explainer", JSON.stringify(spec));
    const explainer = document.querySelector(".pear-chat-explainer");
    assert.ok(explainer instanceof window.HTMLElement);
    assert.equal(explainer.dataset.pearSource, "fingerprint");
    assert.equal(explainer.querySelector("h2").textContent, "Attention");
    assert.ok(explainer.querySelector("svg.psvg") instanceof window.SVGElement);
    assert.equal(explainer.querySelector(".pear-explainer-action").textContent, "Explain");
    assert.equal(document.querySelector(".codehilite"), null);

    $(explainer.querySelector(".pear-explainer-action")).trigger("click");
    assert.deepEqual(actions.pop(), {
        target: {stream: "devel", topic: "attention"},
        prompt: "Explain the highlighted edge",
    });
    assert.equal(explainer.querySelector(".pear-explainer-action").textContent, "asked ✓");

    $(explainer.querySelector(".pear-explainer-pin")).trigger("click");
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, "/json/pear/monitors");
    assert.equal(posts[0].data.stream, "devel");
    assert.equal(JSON.parse(posts[0].data.spec).title, "Attention");
});

run_test("keeps hostile and malformed explainer fences visible", () => {
    render_row(
        "pear-svg",
        '<svg viewBox="0 0 10 10"><foreignObject><div>unsafe</div></foreignObject></svg>',
    );
    assert.ok(document.querySelector(".codehilite") instanceof window.HTMLElement);
    assert.equal(document.querySelector(".pear-chat-explainer"), null);

    render_row("pear-explainer", '{"block":');
    assert.ok(document.querySelector(".codehilite") instanceof window.HTMLElement);
    assert.equal(document.querySelector(".pear-chat-explainer"), null);
});

run_test("renders the pear-svg shorthand as the same explainer host", () => {
    render_row("pear-svg", safe_svg);
    assert.ok(document.querySelector(".pear-chat-explainer svg.psvg") instanceof window.SVGElement);
    assert.equal(document.querySelector(".pear-chat-explainer h2").textContent, "diagram");
});
