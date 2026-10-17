"use strict";

// A person's own view of a plugin's widget (web/src/armada/beta/widget_view.js,
// design/r9/plugins/ROUND2.md section G): the team's one document, drawn with
// my figure at rest, only the rows my facets keep, a mark when one of my
// alerts is met, my label, in my nav and my column or not, in my order. The
// manifest and the document are the server's own fixtures. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const GPUS_MANIFEST = require("../../../zerver/tests/fixtures/armada_plugins/gpus/armada-plugin.json");
const GPUS_DOC = require("../../../zerver/tests/fixtures/armada_plugins/gpus/gpus.json");
const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const {byMyOrder, inMyColumn, inMyNav, viewOf} = zrequire("armada/beta/widget_view.js");

const declared = GPUS_MANIFEST.provides.widgets[0];
const rowsOf = (doc, label) => {
    const block = doc.blocks.find((b) => b.label === label);
    return (block.items ?? doc.data[block.bind.slice(5)]).map((row) => row.key);
};

run_test("with no preference, the view is the team's document itself", () => {
    const view = viewOf(declared, GPUS_DOC, null);
    assert.equal(view.doc, GPUS_DOC);
    assert.equal(view.label, "GPUs");
    assert.equal(view.alert, null);
    // An empty preference too; and no document is no view.
    assert.equal(viewOf(declared, GPUS_DOC, {filters: {}, alerts: {}}).doc, GPUS_DOC);
    assert.deepEqual(viewOf(declared, undefined, {label: "Pods"}), {
        label: "Pods",
        doc: undefined,
        alert: null,
    });
});

run_test("figures: one of the declared figures at rest, its sizer with it", () => {
    const view = viewOf(declared, GPUS_DOC, {rest_figure: "jobs"});
    assert.deepEqual(view.doc.rest, {figure: "data.running", news: "data.news", sizer: "88 jobs"});
    // The team's document is never changed.
    assert.equal(GPUS_DOC.rest.figure, "data.free");
    // A figure the manifest no longer declares is the widget's own.
    assert.equal(viewOf(declared, GPUS_DOC, {rest_figure: "hottest"}).doc, GPUS_DOC);
});

run_test(
    "facets: only the rows that have my value stay; a row without the facet is not about it",
    () => {
        const h100 = viewOf(declared, GPUS_DOC, {filters: {gpu: "H100"}}).doc;
        assert.deepEqual(rowsOf(h100, "Machines"), ["nlscale-a", "nlscale-c"]);
        assert.deepEqual(rowsOf(h100, "Jobs"), ["lr-3e-4-b", "data-mix-sweep"]);
        const mine = viewOf(declared, GPUS_DOC, {filters: {user: "iago"}}).doc;
        // Machines carry no user: all of them stay.
        assert.deepEqual(rowsOf(mine, "Machines"), [
            "nlscale-a",
            "nlscale-b",
            "lab-4090",
            "nlscale-c",
        ]);
        assert.deepEqual(rowsOf(mine, "Jobs"), ["lr-3e-4-b"]);
        // Two facets keep the rows that have both.
        const both = viewOf(declared, GPUS_DOC, {filters: {gpu: "A100", user: "iago"}}).doc;
        assert.deepEqual(rowsOf(both, "Machines"), ["nlscale-b"]);
        assert.deepEqual(rowsOf(both, "Jobs"), []);
        // A list written in the block filters the same way; other blocks pass.
        const doc = {
            version: 1,
            blocks: [
                {type: "copy", body: "Pods"},
                {
                    type: "list",
                    label: "Pods",
                    items: [
                        {key: "a", label: "a", facets: {gpu: "H100"}},
                        {key: "b", label: "b", facets: {gpu: "L4"}},
                    ],
                },
                {type: "list", label: "Empty", bind: "data.none"},
            ],
        };
        const view = viewOf(declared, doc, {filters: {gpu: "L4"}}).doc;
        assert.deepEqual(rowsOf(view, "Pods"), ["b"]);
        assert.deepEqual(view.blocks[2].items, []);
        assert.equal(view.blocks[0], doc.blocks[0]);
    },
);

run_test("alerts: my threshold on a declared number marks my nav once it is met", () => {
    // 4 are free: "4 or more" is met, "5 or more" is not.
    assert.deepEqual(viewOf(declared, GPUS_DOC, {alerts: {free_at_least: 4}}).alert, {
        key: "free_at_least",
        title: "When this many are free",
        threshold: 4,
    });
    assert.equal(viewOf(declared, GPUS_DOC, {alerts: {free_at_least: 5}}).alert, null);
    // An alert the other way: at most.
    const atMost = {
        ...declared,
        options: {...declared.options, alerts: [{...declared.options.alerts[0], op: "<="}]},
    };
    assert.equal(viewOf(atMost, GPUS_DOC, {alerts: {free_at_least: 4}}).alert.threshold, 4);
    assert.equal(viewOf(atMost, GPUS_DOC, {alerts: {free_at_least: 3}}).alert, null);
    // An undeclared alert, a value that is not a number, a threshold that is not one: no mark.
    assert.equal(viewOf(declared, GPUS_DOC, {alerts: {busy: 1}}).alert, null);
    assert.equal(
        viewOf(
            declared,
            {...GPUS_DOC, data: {...GPUS_DOC.data, free_count: "4"}},
            {alerts: {free_at_least: 1}},
        ).alert,
        null,
    );
    assert.equal(viewOf(declared, GPUS_DOC, {alerts: {free_at_least: "4"}}).alert, null);
});

run_test("in my nav and my column: my say first, then the team's", () => {
    const shown = {nav: "shown", column: true};
    const offered = {nav: "offered", column: false};
    assert.equal(inMyNav(declared, shown, null), true);
    assert.equal(inMyNav(declared, offered, null), false);
    assert.equal(inMyNav(declared, offered, {shown: true}), true);
    assert.equal(inMyNav(declared, shown, {shown: false}), false);
    // With no default from the server, the manifest's.
    assert.equal(inMyNav({...declared, team_default: "offered"}, undefined, null), false);
    assert.equal(inMyNav(declared, undefined, {shown: null}), true);
    // A widget that is not in the nav is never there.
    assert.equal(inMyNav({...declared, in: ["column"]}, shown, {shown: true}), false);
    assert.equal(inMyNav(undefined, shown, null), false);
    assert.equal(inMyColumn(declared, shown, null), true);
    assert.equal(inMyColumn(declared, offered, null), false);
    assert.equal(inMyColumn(declared, offered, {in_column: true}), true);
    assert.equal(inMyColumn(declared, undefined, {in_column: false}), false);
    assert.equal(inMyColumn({...declared, in: ["nav"]}, shown, {in_column: true}), false);
});

run_test("order: the places I gave first, smallest first, then the rest as they came", () => {
    const entries = ["eval", "gpus", "train", "uptime"].map((id) => ({id}));
    const places = {train: 0, uptime: 5};
    const order = byMyOrder(entries, (x) => (x.id in places ? {position: places[x.id]} : null));
    assert.deepEqual(
        order.map((x) => x.id),
        ["train", "uptime", "eval", "gpus"],
    );
    // No places: the order they came in.
    assert.deepEqual(
        byMyOrder(entries, () => ({position: null})).map((x) => x.id),
        ["eval", "gpus", "train", "uptime"],
    );
});
