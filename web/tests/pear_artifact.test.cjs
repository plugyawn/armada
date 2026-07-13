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
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    TextEncoder,
    requestAnimationFrame: (callback) => callback(0),
    CSS: {escape: (value) => value.replaceAll('"', '\\"')},
})) {
    set_global(name, value);
}
const $ = jQueryFactory(window);
mock_jquery($);

const artifact = zrequire("pear_artifact");

function valid_document() {
    return {
        version: 1,
        title: "Newton's laws",
        blocks: [
            {type: "copy", eyebrow: "mechanics", heading: "Force changes motion", body: "F = ma"},
            {
                type: "scene",
                label: "Force moving from body A to body B",
                aspect: "wide",
                root: {
                    type: "group",
                    layout: "graph",
                    align: "center",
                    children: [
                        {type: "shape", id: "a", shape: "circle", label: "A"},
                        {type: "shape", id: "b", shape: "circle", label: "B"},
                        {
                            type: "connector",
                            from: "a",
                            to: "b",
                            directed: true,
                            label: "force",
                            state: "selected",
                        },
                    ],
                },
            },
        ],
        actions: [{label: "Explain", prompt: "Explain the relationship."}],
    };
}

run_test("parses the generated artifact contract and rejects authored presentation", () => {
    const parsed = artifact.parse_pear_artifact(JSON.stringify(valid_document()));
    assert.equal(parsed.title, "Newton's laws");

    for (const hostile of [
        {style: "color:red"},
        {svg: "<svg><script/></svg>"},
        {url: "https://example.com/pixel"},
        {onclick: "alert(1)"},
    ]) {
        const document = valid_document();
        Object.assign(document.blocks[1].root, hostile);
        assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
    }
});

run_test("rejects unresolved connectors and duplicate ids", () => {
    const missing = valid_document();
    missing.blocks[1].root.children[2].to = "missing";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(missing)), undefined);

    const duplicate = valid_document();
    duplicate.blocks[1].root.children[1].id = "a";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(duplicate)), undefined);
});

run_test("rejects primitive fields that the renderer would ignore", () => {
    for (const ignored of [
        {children: [{type: "text", text: "never rendered"}]},
        {
            points: [
                [0, 0],
                [1, 1],
            ],
        },
        {control: "button"},
    ]) {
        const document = valid_document();
        Object.assign(document.blocks[1].root.children[0], ignored);
        assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
    }
});

run_test("renders native semantic nodes without exposing source JSON or adopting SVG", () => {
    const document = artifact.parse_pear_artifact(JSON.stringify(valid_document()));
    assert.ok(document);
    const host = window.document.querySelector("#host");
    const rendered = artifact.mount_pear_artifact($(host), document, {density: "chat"});
    assert.equal(rendered.dataset.density, "chat");
    assert.equal(rendered.querySelector(".pear-artifact-title").textContent, "Newton's laws");
    assert.equal(rendered.querySelectorAll(".pear-artifact-shape").length, 2);
    assert.equal(rendered.querySelectorAll(".pear-artifact-action").length, 1);
    assert.equal(rendered.textContent.includes('"version"'), false);
    assert.equal(rendered.querySelectorAll("script, iframe, foreignObject").length, 0);
    assert.equal(rendered.querySelectorAll(".pear-artifact-connectors line").length, 1);
    assert.equal(rendered.querySelector('[data-pear-artifact-node="a"]').style.gridColumn, "1");
    assert.equal(rendered.querySelector('[data-pear-artifact-node="b"]').style.gridColumn, "2");
    assert.equal(
        rendered.querySelector(".pear-artifact-connectors line").dataset.state,
        "selected",
    );
    assert.match(
        rendered.querySelector(".pear-artifact-connectors line").getAttribute("marker-end"),
        /^url\(#pear-artifact-arrow-/,
    );
    assert.equal(rendered.querySelector(".pear-artifact-connector-label").textContent, "force");
});

run_test("renders bounded repeat data and keeps interactions local", () => {
    const document = {
        version: 1,
        data: {items: ["alpha", "beta", "gamma"]},
        blocks: [
            {
                type: "scene",
                label: "Three values",
                root: {
                    type: "text",
                    bind: "data.item",
                    repeat: {source: "data.items", limit: 3},
                    interaction: "select",
                    ariaLabel: "Select value",
                },
            },
        ],
    };
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    const values = [...rendered.querySelectorAll(".pear-artifact-text")];
    assert.deepEqual(
        values.map((node) => node.textContent),
        ["alpha", "beta", "gamma"],
    );
    values[0].click();
    assert.equal(values[0].getAttribute("aria-pressed"), "true");

    document.blocks[0].root.id = "unsafe-repeat-id";
    assert.equal(artifact.parse_pear_artifact(JSON.stringify(document)), undefined);
});

run_test("renders every accepted numeric field without leaking invalid live values", () => {
    const document = {
        version: 1,
        data: {bad: "not-a-number"},
        blocks: [
            {type: "metric", label: "accuracy", value: 94, delta: 2, unit: "%"},
            {type: "meter", label: "progress", value: 4, total: 8, unit: "steps"},
            {type: "series", label: "signal", values: [-2, 0, 3], unit: "ms"},
            {
                type: "scene",
                label: "safe derivation",
                root: {
                    type: "group",
                    layout: "column",
                    children: [
                        {
                            type: "text",
                            text: "unavailable",
                            derive: {op: "divide", args: ["data.bad", 0]},
                        },
                        {
                            type: "control",
                            control: "stepper",
                            label: "tick",
                            action: "advance",
                            value: 1,
                            max: 2,
                        },
                        {type: "mark", mark: "point", values: [-1, 0, 2]},
                        {type: "mark", mark: "area", values: [-1, 0, 2]},
                        {type: "mark", mark: "arc", values: [1, 2, 3]},
                    ],
                },
            },
        ],
    };
    const parsed = artifact.parse_pear_artifact(JSON.stringify(document));
    assert.ok(parsed);
    const rendered = artifact.render_pear_artifact(parsed, {density: "chat"});
    assert.equal(rendered.querySelector(".pear-artifact-delta").textContent, "+2");
    assert.equal(rendered.querySelector(".pear-artifact-meter-meta").textContent, "4 / 8 steps");
    assert.equal(rendered.querySelector(".pear-artifact-series-unit").textContent, "ms");
    assert.equal(rendered.textContent.includes("NaN"), false);
    assert.equal(rendered.querySelector(".pear-artifact-text").textContent, "unavailable");
    assert.equal(rendered.querySelectorAll(".pear-artifact-series-point").length, 3);
    assert.equal(rendered.querySelectorAll(".pear-artifact-series-area").length, 1);
    assert.equal(rendered.querySelectorAll(".pear-artifact-series-arc").length, 3);
    const stepper = rendered.querySelector(".pear-artifact-control-button");
    assert.equal(stepper.dataset.action, "advance");
    stepper.click();
    stepper.click();
    assert.equal(stepper.textContent, "tick  2");
});
