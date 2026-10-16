"use strict";

// The web app's copy of the plugin contract
// (web/src/armada/api/plugin_contract_generated.ts, written by
// tools/armada/artifact_contract.py): what a plugin may name is what the
// kit draws, and the widget vocabulary is the artifact's, less pictures,
// plus the list, rest and tool and link actions.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const contract = zrequire("armada/api/plugin_contract_generated.ts");
const artifact = zrequire("armada_artifact_contract_generated.ts");
const names = zrequire("armada/kit/icons/names.ts");

const widget = contract.ARMADA_WIDGET_SCHEMA;
const manifest = contract.ARMADA_PLUGIN_MANIFEST_SCHEMA;

run_test("a plugin names only the glyphs and marks the sprite has", () => {
    const glyphs = names.glyph_groups.flatMap((group) => group.glyphs.map((glyph) => glyph.name));
    assert.deepEqual(widget.$defs.glyph.enum, glyphs);
    assert.deepEqual(manifest.$defs.glyph.enum, glyphs);
    assert.deepEqual(widget.$defs.runState.enum, [...names.mark_names]);
});

run_test("a widget's blocks are the artifact's, with list for media", () => {
    const refs = (schema) => schema.$defs.block.oneOf.map((variant) => variant.$ref);
    const expected = [
        ...refs(artifact.ARMADA_ARTIFACT_SCHEMA).filter((ref) => ref !== "#/$defs/mediaBlock"),
        "#/$defs/listBlock",
    ];
    assert.deepEqual(refs(widget), expected);
    assert.equal(widget.$defs.mediaBlock, undefined);
    assert.equal(widget.properties.blocks.maxItems, 6);
    assert.equal(widget.properties.actions.maxItems, 4);
});

run_test("a list has at most 32 rows, and 3 in the column", () => {
    const list = widget.$defs.listBlock.properties;
    assert.equal(list.type.const, "list");
    assert.equal(list.items.maxItems, 32);
    assert.equal(list.slots.maximum, 3);
    assert.equal(manifest.$defs.widget.properties.column_slots.maximum, 3);
    const bound = widget.properties.data.additionalProperties.anyOf[0];
    assert.equal(bound.maxItems, 32);
    assert.equal(bound.items.$ref, "#/$defs/listItem");
});

run_test("a widget declares its team default and the options people may choose", () => {
    const declared = manifest.$defs.widget.properties;
    assert.deepEqual(declared.team_default.enum, ["shown", "offered"]);
    assert.deepEqual(Object.keys(manifest.$defs.options.properties), [
        "figures",
        "facets",
        "alerts",
    ]);
    assert.equal(manifest.$defs.options.properties.facets.maxItems, 4);
    assert.equal(widget.$defs.listItem.properties.facets.maxProperties, 4);
});

run_test("rest is one figure and one piece of news, in a cell its sizer sets", () => {
    assert.deepEqual(widget.properties.rest, {$ref: "#/$defs/rest"});
    assert.deepEqual(widget.$defs.rest.required, ["figure", "sizer"]);
    assert.equal(widget.$defs.rest.properties.sizer.maxLength, 12);
});

run_test("an action asks the agent, calls a tool or opens an http(s) page", () => {
    assert.deepEqual(
        widget.$defs.widgetAction.oneOf.map((variant) => variant.$ref),
        ["#/$defs/action", "#/$defs/toolAction", "#/$defs/linkAction"],
    );
    assert.deepEqual(widget.properties.actions.items, {$ref: "#/$defs/widgetAction"});
    const url = new RegExp(widget.$defs.httpUrl.pattern);
    assert.ok(url.test("https://wandb.ai/runix/nextlat-scaling"));
    // Spelled in two pieces, as eslint's no-script-url refuses the literal.
    assert.ok(!url.test(["javascript", "alert(1)"].join(":")));
    assert.ok(!url.test('https://wandb.ai/"><script>'));
});

run_test("the plugin API's version", () => {
    assert.equal(contract.ARMADA_PLUGIN_API_VERSION, 1);
    assert.equal(manifest.$id, "urn:armada:contract:plugin-manifest-v1");
    assert.equal(widget.$id, "urn:armada:contract:widget-v1");
});
