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
    DOMParser: window.DOMParser,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    SVGElement: window.SVGElement,
})) {
    set_global(name, value);
}
const $ = jQueryFactory(window);
mock_jquery($);

const explainer_spec = zrequire("pear_explainer_spec");
const components = zrequire("pear_wall_components");
const render_pear_chat_explainer = require("../templates/pear_chat_explainer.hbs");

const safe_svg = '<svg viewBox="0 0 40 20"><rect width="40" height="20" class="psvg-card"/></svg>';

run_test("parses raw pear-svg as a static shared explainer", () => {
    assert.deepEqual(explainer_spec.parse_pear_explainer_spec(safe_svg, "pear-svg"), {
        title: "diagram",
        block: {components: [{type: "svg", props: {markup: safe_svg}}]},
        recipe: {parse: {mode: "static"}},
    });
});

run_test("renders explainer hierarchy and action controls through the shared partial", () => {
    const html = render_pear_chat_explainer({
        title: "Attention",
        has_actions: true,
        pin_label: "pin to wall",
        pin_tooltip: "Keep this live on the wall",
        components: [
            {
                is_text: true,
                eyebrow: "Tier 4",
                heading: "Animation",
                body: "Tokens move through the graph.",
            },
        ],
        actions: [{action_index: 0, label: "Explain this"}],
    });
    assert.match(html, /pear-chat-explainer-head/);
    assert.match(html, /Tier 4/);
    assert.match(html, /pear-explainer-action/);
    assert.match(html, /data-pear-explainer-action="0"/);
});

run_test("accepts copy, diagram, actions, and an asynchronous recipe in one spec", () => {
    const parsed = explainer_spec.parse_pear_explainer_spec(
        JSON.stringify({
            title: "pipeline",
            cadence_seconds: 20,
            block: {
                components: [
                    {type: "text", props: {eyebrow: "live", heading: "Pipeline", body: "Healthy"}},
                    {type: "svg", props: {label: "topology", markup: safe_svg}},
                ],
                actions: [{label: "Explain", prompt: "Explain the current bottleneck"}],
            },
            recipe: {
                command: "python3 render.py",
                parse: {mode: "text"},
                bindings: {"1.markup": "text"},
            },
        }),
        "pear-explainer",
    );
    assert.equal(parsed.title, "pipeline");
    assert.equal(parsed.block.components[1].type, "svg");
    assert.equal(parsed.block.actions[0].label, "Explain");
    assert.equal(parsed.recipe.bindings["1.markup"], "text");
});

run_test("rejects unknown components and unbounded agent actions", () => {
    const make = (block) =>
        explainer_spec.parse_pear_explainer_spec(JSON.stringify({block}), "pear-explainer");
    assert.equal(
        make({components: [{type: "iframe", props: {src: "https://example.com"}}]}),
        undefined,
    );
    assert.equal(
        make({
            components: [{type: "text", props: {body: "x"}}],
            actions: [{label: "Ask", prompt: "x".repeat(1001)}],
        }),
        undefined,
    );
    assert.equal(
        make({components: Array.from({length: 5}, () => ({type: "text", props: {body: "x"}}))}),
        undefined,
    );
});

run_test("mounts rebuilt SVG nodes without adopting hostile source nodes", () => {
    document.body.innerHTML = '<div id="host" data-pear-svg-component="0"></div>';
    const spec = [{type: "svg", props: {markup: safe_svg}}];
    const valid = components.mount_svg_components($(document.body), spec, {}, new Map());
    assert.equal(valid, true);
    const svg = document.querySelector("#host > svg.psvg");
    assert.ok(svg instanceof window.SVGElement);
    assert.equal(svg.ownerDocument, document);
    assert.equal(document.querySelector("script"), null);
});

run_test("keeps the last valid Wall drawing when a later tick is hostile", () => {
    const cache = new Map();
    const spec = [{type: "svg", props: {markup: safe_svg}}];
    document.body.innerHTML = '<div id="host" data-pear-svg-component="0"></div>';
    assert.equal(components.mount_svg_components($(document.body), spec, {}, cache), true);

    document.body.innerHTML = '<div id="host" data-pear-svg-component="0"></div>';
    const valid = components.mount_svg_components(
        $(document.body),
        spec,
        {"0.markup": '<svg viewBox="0 0 10 10"><script>alert(1)</script></svg>'},
        cache,
    );
    assert.equal(valid, false);
    assert.ok(document.querySelector("#host > svg.psvg") instanceof window.SVGElement);
    assert.equal(document.querySelector("script"), null);
    assert.equal(
        document.querySelector("#host").classList.contains("pear-explainer-svg-stale"),
        true,
    );
});
