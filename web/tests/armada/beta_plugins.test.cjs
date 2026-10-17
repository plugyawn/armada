"use strict";

// The interim beta's plugins (web/src/armada/beta/plugins.js and its chunk
// plugin_view.js), with a real DOM and a stand-in server. The installs and
// their widgets' documents come as /register's snapshot and as
// armada_plugin and armada_widget events, in the shapes the server sends
// (zerver/lib/armada_plugin_events.py); the documents are the server's own
// test fixtures (zerver/tests/fixtures/armada_plugins/). A widget at rest is
// a nav row whose one trailing thing follows the nav's priority; pressed,
// it opens as a sheet beside the nav; linked to a place, it adds a list to
// that place's column; an item opened from a row borrows the column; an
// update changes what is drawn in place; a plugin's bot speaks on the
// plane; and a plugin's page draws its settings from its manifest. Deleted
// with web/src/armada/beta/.

const assert = require("node:assert/strict");

const GPUS_MANIFEST = require("../../../zerver/tests/fixtures/armada_plugins/gpus/armada-plugin.json");
const GPUS_DOC = require("../../../zerver/tests/fixtures/armada_plugins/gpus/gpus.json");
const WANDB_MANIFEST = require("../../../zerver/tests/fixtures/armada_plugins/wandb/armada-plugin.json");
const RUN_DOC = require("../../../zerver/tests/fixtures/armada_plugins/wandb/run.json");
const RUNS_DOC = require("../../../zerver/tests/fixtures/armada_plugins/wandb/runs.json");
const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<nav><button class="row me"><span class="lab">Iago</span></button></nav>
<main>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div id="pane"><div id="col"></div></div>
  <div class="pane" data-pane="Plugins"><div class="lane" id="plugins"></div></div>
  <aside class="side" id="side"><div class="out" id="outline"></div></aside>
  <div class="dock"><div class="cz"><div class="sh">
    <span class="txw"><div class="tx" contenteditable="true"></div><span class="ph"></span></span>
    <button class="disc" aria-label="Send"><span class="cell"><span>Send</span></span></button>
  </div></div></div>
</main>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("history", dom.window.history);
set_global("localStorage", dom.window.localStorage);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global("removeEventListener", (...args) => dom.window.removeEventListener(...args));
set_global("getSelection", () => dom.window.getSelection());
set_global("innerWidth", 1440);
set_global("innerHeight", 900);
set_global("requestAnimationFrame", (f) => setTimeout(f, 16));
set_global(
    "ResizeObserver",
    class {
        observe() {}
    },
);
Object.defineProperty(document, "fonts", {
    value: {addEventListener() {}, ready: Promise.resolve()},
});
set_global(
    "BroadcastChannel",
    class {
        postMessage() {}
    },
);
const opened = [];
dom.window.open = (url) => {
    opened.push(url);
};
// What moved, and how: a figure changed in its cell, a mark into another,
// something said beside a control.
const figures = [];
const said = [];
const noop = () => {};
set_global("M", {
    T: {},
    SLOW: 1,
    // Where a row stands at rest: there is no layout here.
    rest: () => ({top: 0, left: 0, width: 0, height: 0}),
    release: noop,
    appear: noop,
    fade: noop,
    halt: noop,
    figure(cell, value) {
        figures.push(String(value));
        cell.textContent = String(value);
    },
    cross(old, html) {
        old.insertAdjacentHTML("afterend", html);
        const next = old.nextElementSibling;
        old.remove();
        return next;
    },
    leave(element, done) {
        element.remove();
        if (done) {
            done();
        }
    },
    say(_target, words) {
        said.push(words);
    },
    focusKbd(element) {
        element.focus();
    },
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global("face", (_spec, cls) => `<i class="av ${cls || ""}"></i>`);
set_global("mk", (name) => `<svg class="mk ${name}"></svg>`);
set_global("fitLists", noop);
// The glyphs the sprite has; a plugin that names another draws the plugin glyph.
set_global("GLYPHS", {gpu: {}, chart: {}, plugin: {}});

// The stand-in server: each request is kept, and answered from `answers`
// (by method and path), else with success.
const requests = [];
const answers = new Map();
set_global("fetch", async (url, init = {}) => {
    const method = init.method || "GET";
    const path = url.split("?")[0];
    const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) : null;
    requests.push({method, path, body});
    const answer = answers.get(`${method} ${path}`) || {result: "success"};
    const data = typeof answer === "function" ? answer(body) : answer;
    return {
        ok: data.result === "success",
        status: data.result === "success" ? 200 : 400,
        json: async () => data,
    };
});

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane,
    nav: find("nav"),
    navFixed: document.createElement("div"),
    band: {
        el: find(".band"),
        run: find("#run"),
        st: find("#run-st"),
        clock: find("#clock"),
        act: find("#act"),
        stop: find("#stop"),
        ty: find("#ty"),
        jump: find("#jump"),
    },
    cz: find(".cz"),
    sh: find(".sh"),
    tx: find(".tx"),
    side: find("#side"),
    outline: find("#outline"),
    go: find(".disc"),
    goc: find(".disc .cell"),
    ph: find(".ph"),
    panes: {},
});
// The header is drawn whole from the place; here it has nowhere to go.
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const column = zrequire("armada/beta/column.js");
const events = zrequire("armada/beta/events.js");
const plugins = zrequire("armada/beta/plugins.js");

const ME = 11;
const GPUS_BOT = 40;
const WANDB_BOT = 41;
beta_state.state.ME = ME;
beta_state.state.meName = "Iago";
beta_state.people.set(ME, {
    id: ME,
    name: "Iago",
    email: "iago@example.com",
    bot: false,
    active: true,
});
beta_state.streams.set(10, {stream_id: 10, name: "nextlat-scaling"});
// Onix is here: a topic it can run in has its checklist, Changes and Runs in
// the column (runs.js, agentHere), between which a linked plugin's list stands.
beta_state.armadaBots.add(30);
beta_state.repoStreams.add(10);
const place = {
    key: routing.tkey(10, "lr-sweep"),
    kind: "topic",
    stream_id: 10,
    topic: "lr-sweep",
    repo: true,
};
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
cache.runsLoaded = true;
beta_state.state.P = cache;

const seconds = () => Math.floor(Date.now() / 1000);
// The installs as everyone sees them (plugin_view), newest last.
const gpus = () => ({
    id: 7,
    plugin: "gpus",
    name: "GPU tracker",
    glyph: "gpu",
    version: "0.1.0",
    state: "running",
    disabled: false,
    bot_user_id: GPUS_BOT,
    widgets: GPUS_MANIFEST.provides.widgets,
    widget_defaults: {gpus: {nav: "shown", column: true}},
    links: [],
    made_by: null,
    made_in_stream_id: null,
    made_in_topic: null,
    made_in_message_id: null,
});
const wandb = () => ({
    ...gpus(),
    id: 8,
    plugin: "wandb",
    name: "Weights & Biases",
    glyph: "chart",
    bot_user_id: WANDB_BOT,
    widgets: WANDB_MANIFEST.provides.widgets,
    widget_defaults: {runs: {nav: "shown", column: true}},
    links: [{id: 31, kind: "project", value: "runix/nextlat-scaling", stream_id: 10, topic: null}],
});
// A widget's document for a scope, as an armada_widget event carries it.
const widget = (install_id, key, document, extra = {}) => ({
    install_id,
    widget: key,
    link_id: null,
    item: "",
    stream_id: null,
    topic: null,
    document,
    version: 1,
    fed_at: seconds(),
    stale: false,
    ...extra,
});
const copy = (x) => structuredClone(x);
function register() {
    plugins.applyPlugins({
        armada_plugins: [gpus(), wandb()],
        armada_widgets: [
            widget(7, "gpus", copy(GPUS_DOC)),
            widget(8, "runs", copy(RUNS_DOC)),
            widget(8, "runs", copy(RUNS_DOC), {link_id: 31, stream_id: 10}),
            widget(8, "run", copy(RUN_DOC), {link_id: 31, stream_id: 10, item: "lr-3e-4-b"}),
        ],
    });
}
// The plugin chunk arrives when a test first needs it (settle, sheetView):
// until then the column's lists stand in their slots, as on a page whose
// chunk is still on its way (chunks.js, setChunkLoader).
const chunks = zrequire("armada/beta/chunks.js");
let releaseChunk;
const chunkHere = new Promise((resolve) => {
    releaseChunk = resolve;
});
chunks.setChunkLoader("plugins", async () => {
    await chunkHere;
    return zrequire("armada/beta/plugin_view.js");
});
register();
plugins.wirePlugins();
column.drawSide();

const rows = () => [...document.querySelectorAll("nav .row.wg")];
const rowOf = (label) => rows().find((r) => r.querySelector(".lab").textContent === label);
const trail = (label) => rowOf(label).querySelector(".wf > :not(.sz)");
const sheet = () => find(".wsheet");
const borrowed = () => find("#side > .wd");
const section = () => find("#outline section.sec.plug");
async function settle() {
    releaseChunk();
    for (let i = 0; i < 40; i += 1) {
        await Promise.resolve();
    }
}
// A pointer press, and its own click.
async function pressOn(target) {
    target.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    target.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true, button: 0, detail: 1}));
    await settle();
}
// A press from the keyboard: the row has focus, and Return clicks it.
async function pressByKeyboard(target) {
    target.focus();
    target.click();
    await settle();
}
clock.tick(1);

run_test(
    "the column's list stands in its slots from the snapshot, and its rows arrive into them",
    async () => {
        // Before the plugin chunk is here: W&B's section after Changes, its
        // label and its three slots, no rows yet; and no plugin rows in Jump to.
        let sec = section();
        assert.equal(sec.previousElementSibling.id, "sec-changes");
        assert.equal(sec.nextElementSibling.id, "sec-runs");
        assert.equal(sec.style.getPropertyValue("--slots"), "3");
        assert.equal(sec.querySelector(".sh > span").textContent, "Training");
        assert.equal(sec.querySelectorAll("li").length, 0);
        assert.deepEqual(plugins.pluginItems(), []);
        // The chunk arrives, and the column is drawn again by itself: the
        // same section, in the same place and slots, now with its rows.
        await settle();
        sec = section();
        assert.equal(sec.previousElementSibling.id, "sec-changes");
        assert.equal(sec.style.getPropertyValue("--slots"), "3");
        assert.equal(sec.querySelectorAll("li[data-row]").length, 3);
        assert.ok(plugins.pluginItems().some((x) => x.label === "Show GPUs"));
    },
);

run_test("the nav holds a row for each widget, the newest first, above the account row", () => {
    assert.deepEqual(
        rows().map((r) => r.querySelector(".lab").textContent),
        ["Training", "GPUs"],
    );
    assert.equal(find("nav .wgs").nextElementSibling, find("nav .row.me"));
    // Each trailing figure is the document's `rest` figure, in a cell as
    // wide as its sizer.
    assert.equal(trail("GPUs").textContent, "4 free");
    assert.equal(rowOf("GPUs").querySelector(".sz").textContent, "88 free");
    assert.equal(trail("Training").textContent, "3 running");
    assert.equal(rowOf("GPUs").getAttribute("aria-label"), "GPUs: 4 free");
    // The item widget opened from a row is no nav row.
    assert.equal(rows().length, 2);
});

run_test("rest priority: needs you, then failed, then the paused mark, then the figure", () => {
    const install = gpus();
    const declared = install.widgets[0];
    const at = seconds();
    const fed = (data, extra) =>
        widget(7, "gpus", {...copy(GPUS_DOC), data: {...GPUS_DOC.data, ...data}}, extra);
    assert.deepEqual(plugins.restOf(install, declared, fed({}), at), {
        kind: "figure",
        text: "4 free",
        tip: "",
    });
    assert.equal(plugins.restOf(install, declared, fed({news: "failed"}), at).kind, "failed");
    assert.equal(plugins.restOf(install, declared, fed({news: "needs"}), at).kind, "needs");
    // Unheard for three times its cadence (10 s): paused, even with a figure.
    assert.equal(plugins.restOf(install, declared, fed({}, {fed_at: at - 31}), at).kind, "paused");
    assert.equal(plugins.restOf(install, declared, fed({}, {fed_at: at - 29}), at).kind, "figure");
    // News outranks the paused mark.
    assert.equal(
        plugins.restOf(install, declared, fed({news: "needs"}, {stale: true}), at).kind,
        "needs",
    );
    // An install that failed says so before its document; one waiting for
    // its settings, with none yet, shows the dashed ring.
    assert.equal(
        plugins.restOf({...install, state: "failed"}, declared, fed({}), at).kind,
        "failed",
    );
    assert.equal(plugins.restOf({...install, state: "waiting"}, declared, null, at).kind, "queued");
    assert.equal(plugins.restOf(install, declared, null, at).kind, "none");
    // A figure that is a number is written as one.
    assert.equal(plugins.restOf(install, declared, fed({free: 1234.5}), at).text, "1,234.5");

    // In the nav: the mark takes the figure's place in the same cell.
    plugins.onWidgetEvent({type: "armada_widget", op: "update", ...fed({news: "failed"})});
    assert.ok(trail("GPUs").matches(".mk.failed"));
    assert.equal(rowOf("GPUs").getAttribute("aria-label"), "GPUs: failed");
    plugins.onWidgetEvent({type: "armada_widget", op: "update", ...fed({news: "needs"})});
    assert.ok(trail("GPUs").matches(".mk.needs"));
    plugins.onWidgetEvent({type: "armada_widget", op: "update", ...fed({})});
    assert.equal(trail("GPUs").textContent, "4 free");
});

run_test(
    "staleness: a widget unheard for three times its cadence shows the paused mark, with when it was last heard",
    () => {
        const fed_at = seconds();
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(7, "gpus", copy(GPUS_DOC), {fed_at}),
        });
        assert.equal(trail("GPUs").textContent, "4 free");
        // No event says so: the page's own timer turns it at 3 × 10 s.
        clock.tick(29_000);
        assert.equal(trail("GPUs").textContent, "4 free");
        clock.tick(2_000);
        assert.ok(trail("GPUs").matches(".mk.paused"));
        const tip = rowOf("GPUs").dataset.tip;
        assert.match(tip, /^Last heard \d{1,2}:\d{2}$/);
        assert.equal(rowOf("GPUs").getAttribute("aria-label"), `GPUs: ${tip}`);
        // The server's own word wins as well, and a fresh document clears it.
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(7, "gpus", copy(GPUS_DOC), {stale: true}),
        });
        assert.ok(trail("GPUs").matches(".mk.paused"));
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(7, "gpus", copy(GPUS_DOC)),
        });
        assert.equal(trail("GPUs").textContent, "4 free");
        assert.equal(rowOf("GPUs").dataset.tip, undefined);
    },
);

run_test("the document turned into rows: a list's own items or the data bound to it", async () => {
    await viewLoaded();
    const [machines, jobs] = plugins.listsOf(GPUS_DOC);
    assert.deepEqual(
        plugins.rowsOf(GPUS_DOC, machines).map((r) => r.key),
        ["nlscale-a", "nlscale-b", "lab-4090", "nlscale-c"],
    );
    assert.deepEqual(
        plugins.rowsOf(GPUS_DOC, jobs).map((r) => r.label),
        ["lr-3e-4-b", "data-mix-sweep"],
    );
    // Items written in the block, with what is not a row left out.
    const block = {
        type: "list",
        label: "Here",
        items: [{key: "a", label: "A"}, {label: "no key"}, "x"],
    };
    assert.deepEqual(plugins.rowsOf({blocks: [block]}, block), [{key: "a", label: "A"}]);
    assert.equal(plugins.markOf("waiting"), "queued");
    assert.equal(plugins.markOf("stopped"), "cancelled");
    assert.equal(plugins.markOf("live"), "live");
    assert.equal(plugins.glyphOf("table"), "plugin");

    // The column of a linked place: W&B's list after Changes, its label in
    // Strong and the caption naming its figure, three slots, rows without
    // their second line.
    const sec = section();
    assert.equal(sec.previousElementSibling.id, "sec-changes");
    assert.equal(sec.nextElementSibling.id, "sec-runs");
    assert.equal(sec.querySelector(".sh").textContent, "Trainingloss");
    assert.equal(sec.style.getPropertyValue("--slots"), "3");
    const lis = [...sec.querySelectorAll("li")];
    assert.deepEqual(
        lis.map((li) => li.textContent),
        ["lr-3e-4-b2.31", "lr-1e-3-b2.27", "lr-1e-4-a2.29"],
    );
    assert.ok(lis[0].querySelector(".mk.live"));
    assert.ok(lis[2].querySelector(".mk.done"));
    assert.ok(lis[0].classList.contains("on"));
    assert.ok(!lis[2].classList.contains("on"));
    // A place the plugin is not linked to has no list.
    assert.equal(plugins.pluginSectionsHTML({...place, stream_id: 99}), "");
});

run_test(
    "the sheet: every block, from the row's foot; Esc closes it and the row has focus again",
    async () => {
        await viewLoaded();
        const row = rowOf("GPUs");
        await pressByKeyboard(row);
        const s = sheet();
        assert.ok(s);
        assert.equal(s.getAttribute("role"), "dialog");
        assert.equal(row.getAttribute("aria-expanded"), "true");
        // Its foot stands level with its row's: one row (Training) above GPUs,
        // none below it.
        assert.equal(s.style.getPropertyValue("--below"), "0");
        assert.equal(document.activeElement.textContent, "Open");
        // Machines and Jobs, each row with its second line and figure; a
        // machine's load is a sparkline.
        const lists = [...s.querySelectorAll("section.sec")];
        assert.deepEqual(
            lists.map((x) => x.querySelector(".sh span").textContent),
            ["Machines", "Jobs"],
        );
        const first = lists[0].querySelector("li");
        assert.equal(first.querySelector(".sub").textContent, "8 × H100 · Prime Intellect");
        assert.equal(first.querySelector(".v").textContent, "0 free");
        assert.ok(first.classList.contains("two"));
        assert.ok(first.querySelector("svg.spk path"));
        // A pod still being made: the dashed ring, Starting.
        const pod = lists[0].querySelectorAll("li")[3];
        assert.ok(pod.querySelector(".mk.queued"));
        assert.equal(pod.querySelector(".v").textContent, "Starting");
        // Its action, a tool.
        assert.equal(s.querySelector("[data-wact]").textContent, "Tell me when 8 are free");

        dom.press("Escape");
        assert.equal(sheet(), null);
        assert.equal(document.activeElement, row);
        assert.equal(row.getAttribute("aria-expanded"), "false");
        // The rows are nav rows: Up and Down move between them, Home and End
        // go to the ends, and a key with a modifier is the page's.
        dom.press("ArrowUp");
        assert.equal(document.activeElement, rowOf("Training"));
        dom.press("ArrowUp");
        assert.equal(document.activeElement, rowOf("Training"));
        dom.press("End");
        assert.equal(document.activeElement, row);
        dom.press("Home");
        assert.equal(document.activeElement, rowOf("Training"));
        dom.press("ArrowDown");
        assert.equal(document.activeElement, row);
        assert.equal(dom.press("ArrowDown", {metaKey: true}), true);
        assert.equal(document.activeElement, row);

        // By the pointer: the sheet opens, the field keeps the caret; a press
        // elsewhere closes it; the other row opens its own.
        await pressOn(row);
        assert.ok(sheet());
        await pressOn(rowOf("Training"));
        assert.equal(sheet().getAttribute("aria-label"), "Training");
        assert.equal(sheet().style.getPropertyValue("--below"), "1");
        lane.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
        assert.equal(sheet(), null);
        // Jump to has one command a widget.
        const show = plugins.pluginItems().find((x) => x.label === "Show GPUs");
        assert.equal(show.cap, "GPU tracker");
        show.cmd();
        await settle();
        assert.equal(sheet().getAttribute("aria-label"), "GPUs");
        show.cmd();
        assert.equal(sheet(), null);
    },
);

run_test(
    "a tool action asks first when it cannot be undone, and runs for me on the plugin's server",
    async () => {
        answers.set("POST /json/armada/plugins/7/actions", {
            result: "success",
            text: "I'll tell you.",
            is_error: false,
        });
        await pressOn(rowOf("GPUs"));
        await pressOn(sheet().querySelector("[data-wact]"));
        const action = requests.findLast((r) => r.path === "/json/armada/plugins/7/actions");
        assert.deepEqual(action.body, {
            widget: "gpus",
            link_id: "null",
            item: "",
            index: "0",
            confirmed: "false",
        });
        assert.equal(said.at(-1), "I'll tell you.");
        // A row's action that declares confirm: the dialog stands on Cancel.
        const pod = sheet().querySelector('li[data-row="nlscale-c"]');
        pod.click();
        await settle();
        const dialog = find(".dlg");
        assert.match(dialog.textContent, /End nlscale-c\? It has not started yet\./);
        assert.equal(document.activeElement.textContent, "Cancel");
        dom.press("Escape");
        await settle();
        assert.equal(find(".dlg"), null);
        assert.equal(requests.filter((r) => r.path.endsWith("/actions")).length, 1);
        pod.click();
        await settle();
        await pressOn(find(".dlg [data-yes]"));
        assert.deepEqual(requests.at(-1).body, {
            widget: "gpus",
            link_id: "null",
            item: "",
            row: "nlscale-c",
            confirmed: "true",
        });
        answers.delete("POST /json/armada/plugins/7/actions");
        dom.press("Escape");
    },
);

run_test(
    "the sparkline domain: an idle machine on 0 to 100 is a flat line at the foot",
    async () => {
        await pressOn(rowOf("GPUs"));
        const view = await sheetView();
        const idle = view.sparkPoints([0, 0, 1, 0], [0, 100]).map(([, y]) => y);
        // Within a fifth of a pixel of the foot (20 − 3).
        assert.ok(idle.every((y) => y > 16.8 && y <= 17));
        // The same values on their own range are drawn the whole height.
        const own = view.sparkPoints([0, 0, 1, 0]).map(([, y]) => y);
        assert.deepEqual([Math.min(...own), Math.max(...own)], [3, 17]);
        // A value past the domain is held at its edge, and the line is the
        // cell's width; fewer than two points draw nothing.
        const busy = view.sparkPoints([50, 140], [0, 100]);
        assert.deepEqual(busy, [
            [0, 10],
            [62, 3],
        ]);
        assert.deepEqual(view.sparkPoints([5]), []);
        dom.press("Escape");
    },
);
// The chunk's view, as plugins.js made it, through the sheet it opened.
// The plugin chunk, and the column drawn again with it: the rows of a linked
// list and Jump to's rows are the chunk's.
async function viewLoaded() {
    await sheetView();
    column.drawSide();
}
async function sheetView() {
    releaseChunk();
    const bind = await chunks.chunk("plugins");
    assert.equal(typeof bind, "function");
    return plugins.pluginView();
}

run_test(
    "a live update changes what is drawn in place: the nav row, the column's list, the open sheet",
    async () => {
        await viewLoaded();
        await pressOn(rowOf("GPUs"));
        const navRow = rowOf("GPUs");
        const li = section().querySelector('li[data-row="lr-3e-4-b"]');
        const sheetRow = sheet().querySelector('li[data-row="nlscale-b"]');
        const path = sheetRow.querySelector(".spk path").getAttribute("d");
        figures.length = 0;

        const doc = copy(GPUS_DOC);
        doc.data.free = "6 free";
        doc.data.machines[1].figure = "4 free";
        doc.data.machines[1].spark.values = [50, 50, 20, 10, 0, 0];
        plugins.onWidgetEvent({type: "armada_widget", op: "update", ...widget(7, "gpus", doc)});
        const runs = copy(RUNS_DOC);
        runs.data.runs[0].figure = 2.3;
        runs.data.runs[0].state = "done";
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(8, "runs", runs, {link_id: 31, stream_id: 10}),
        });

        // The same elements, their figures rolled in their cells.
        assert.equal(rowOf("GPUs"), navRow);
        assert.equal(trail("GPUs").textContent, "6 free");
        assert.equal(section().querySelector('li[data-row="lr-3e-4-b"]'), li);
        assert.equal(li.querySelector(".d").textContent, "2.3");
        assert.ok(li.querySelector(".mk.done"));
        assert.ok(!li.classList.contains("on"));
        assert.equal(sheet().querySelector('li[data-row="nlscale-b"]'), sheetRow);
        assert.equal(sheetRow.querySelector(".v").textContent, "4 free");
        assert.notEqual(sheetRow.querySelector(".spk path").getAttribute("d"), path);
        // Each changed figure rolled in its cell (the motion kit leaves a figure that did not change as it is).
        for (const value of ["6 free", "4 free", "2.3"]) {
            assert.ok(figures.includes(value));
        }

        // Other rows: the list is drawn again, in the same slots.
        const fewer = copy(RUNS_DOC);
        fewer.data.runs.pop();
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(8, "runs", fewer, {link_id: 31, stream_id: 10}),
        });
        assert.equal(section().querySelectorAll("li").length, 2);
        assert.equal(section().style.getPropertyValue("--slots"), "3");
        dom.press("Escape");
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(8, "runs", copy(RUNS_DOC), {link_id: 31, stream_id: 10}),
        });
    },
);

run_test("an item opened from the column borrows it, and gives it back on Close", async () => {
    await viewLoaded();
    const li = section().querySelector('li[data-row="lr-3e-4-b"]');
    await pressOn(li);
    const item = borrowed();
    assert.ok(item);
    assert.equal(find("#side").dataset.borrowed, "plugin");
    assert.ok(find("#side").classList.contains("folded"));
    assert.equal(li.getAttribute("aria-current"), "true");
    assert.equal(item.querySelector(".ch").textContent, "lr-3e-4-b");
    // Its document: the words, the step, its metrics with their lines, its
    // last lines, its verbs.
    const metrics = [...item.querySelectorAll(".wl.met > li")];
    assert.deepEqual(
        metrics.map((x) => x.querySelector(".t").textContent),
        ["loss", "validation loss", "learning rate", "gradient norm"],
    );
    assert.ok(metrics.every((x) => x.querySelector(".spk")));
    assert.match(item.textContent, /On nlscale-a, GPUs 0 to 3, for 2h 14m\./);
    assert.match(item.textContent, /step 42200 loss 2\.3098/);
    assert.deepEqual(
        [...item.querySelectorAll("[data-wact]")].map((b) => b.textContent),
        ["Open in W&B", "Ask Onix"],
    );
    // A page opens in a tab of its own, its host said first.
    const open = item.querySelector("[data-wact]");
    assert.equal(open.dataset.tip, "wandb.ai");
    await pressOn(open);
    assert.equal(opened.at(-1), "https://wandb.ai/runix/nextlat-scaling/runs/lr-3e-4-b");

    // A new document for the item, in place.
    const run = copy(RUN_DOC);
    run.data.metrics[0].figure = 2.29;
    plugins.onWidgetEvent({
        type: "armada_widget",
        op: "update",
        ...widget(8, "run", run, {link_id: 31, stream_id: 10, item: "lr-3e-4-b"}),
    });
    assert.match(borrowed().textContent, /loss.*2\.29/);

    // Close gives the column back, with focus on the row it came from.
    item.querySelector("[data-wclose]").click();
    await settle();
    assert.equal(borrowed(), null);
    assert.equal(find("#side").dataset.borrowed, undefined);
    assert.ok(!find("#side").classList.contains("folded"));
    assert.equal(document.activeElement, section().querySelector('li[data-row="lr-3e-4-b"]'));
    // Esc closes it from inside; leaving the place gives the column back too.
    await pressByKeyboard(section().querySelector('li[data-row="lr-1e-3-b"]'));
    assert.equal(document.activeElement.getAttribute("aria-label"), "Close");
    assert.match(borrowed().textContent, /Nothing more from the plugin about lr-1e-3-b yet\./);
    dom.press("Escape");
    assert.equal(borrowed(), null);
    await pressOn(section().querySelector('li[data-row="lr-1e-4-a"]'));
    plugins.closeWidgetViews();
    assert.equal(borrowed(), null);
});

run_test(
    "install and remove: a widget arrives at the group's top, and leaves with everything it drew",
    async () => {
        await viewLoaded();
        const before = rows();
        plugins.onPluginEvent({
            type: "armada_plugin",
            op: "add",
            plugin: {
                ...gpus(),
                id: 9,
                plugin: "eval-queue",
                name: "Eval queue",
                glyph: "table",
                bot_user_id: 42,
                widgets: [{key: "queue", label: "Eval queue", in: ["nav"], cadence: 30}],
                widget_defaults: {queue: {nav: "shown", column: true}},
            },
        });
        assert.deepEqual(
            rows().map((r) => r.querySelector(".lab").textContent),
            ["Eval queue", "Training", "GPUs"],
        );
        // A glyph the sprite lacks is the plugin glyph; nothing heard yet, no figure.
        assert.ok(rowOf("Eval queue").querySelector(".ic.plugin"));
        assert.equal(trail("Eval queue"), null);
        assert.ok(before.every((r) => !r.isConnected));
        // Offered to the team: in "+" (later), not the nav.
        plugins.onPluginEvent({
            type: "armada_plugin",
            op: "update",
            plugin: {
                ...plugins.installs.get(9),
                widget_defaults: {queue: {nav: "offered", column: true}},
            },
        });
        assert.equal(rowOf("Eval queue"), undefined);

        // Removing W&B takes its row, its column list and its open sheet.
        await pressOn(rowOf("Training"));
        assert.ok(sheet());
        plugins.onPluginEvent({type: "armada_plugin", op: "remove", install_id: 8});
        assert.equal(rowOf("Training"), undefined);
        assert.equal(sheet(), null);
        assert.equal(section(), null);
        assert.equal(beta_state.pluginBots.has(WANDB_BOT), false);
        assert.equal([...plugins.widgets.values()].filter((s) => s.install_id === 8).length, 0);
        // A widget's own removal (its link taken away) takes its list.
        plugins.onPluginEvent({type: "armada_plugin", op: "add", plugin: wandb()});
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "update",
            ...widget(8, "runs", copy(RUNS_DOC), {link_id: 31, stream_id: 10}),
        });
        assert.equal(section().querySelectorAll("li[data-row]").length, 3);
        plugins.onWidgetEvent({
            type: "armada_widget",
            op: "remove",
            install_id: 8,
            widget: "runs",
            link_id: 31,
            item: "",
        });
        assert.match(section().textContent, /Waiting for Weights & Biases/);
        // A link the team takes away: the list leaves the column.
        plugins.onPluginEvent({
            type: "armada_plugin",
            op: "update",
            plugin: {...wandb(), links: []},
        });
        assert.equal(section(), null);
        // /register again: the snapshot is the whole truth.
        register();
        column.drawSide();
        assert.deepEqual(
            rows().map((r) => r.querySelector(".lab").textContent),
            ["Training", "GPUs"],
        );
        assert.equal(section().querySelectorAll("li[data-row]").length, 3);
    },
);

run_test("a plugin's words are on the plane: no bubble, its glyph before its name", () => {
    const m = {
        id: 900,
        type: "stream",
        stream_id: 10,
        subject: "lr-sweep",
        sender_id: WANDB_BOT,
        timestamp: seconds(),
        content: "<p><code>lr-1e-4-a</code> finished at step 60,000.</p>",
        reactions: [],
        submessages: [],
    };
    routing.addMsg(cache, m);
    const {cl} = feed.appendMessage(lane, m);
    assert.ok(cl.classList.contains("plug"));
    assert.equal(cl.querySelector(".b"), null);
    assert.ok(cl.querySelector(".ans"));
    // A bot's label (feed.js): its name and its time, the plugin's glyph
    // where a face would be.
    const who = cl.querySelector(".who");
    assert.equal(who.querySelector("span").textContent, "Weights & Biases");
    assert.ok(who.firstElementChild.matches(".ic.chart"));
    assert.ok(who.querySelector("time"));

    // As it arrives, it makes no sound: a plugin is someone else acting.
    // A person's message does.
    const played = [];
    set_global("S", {play: (name) => played.push(name)});
    const tab = beta_state.state.tab;
    beta_state.state.tab = "Chat";
    // The lane holds the place, as openPlace leaves it.
    feed.drawLane();
    for (const [id, sender_id] of [
        [901, WANDB_BOT],
        [902, 8],
    ]) {
        events.onMessage({type: "message", message: {...m, id, sender_id}, flags: []});
    }
    assert.deepEqual(played, ["arrive"]);
    beta_state.state.tab = tab;
    set_global("S", {play: noop});
});

run_test(
    "Plugins and a plugin's page: the settings drawn from the schema, a secret written and never read back",
    async () => {
        const full = {
            ...wandb(),
            manifest: WANDB_MANIFEST,
            settings: {entity: "runix", posts: "both"},
            secrets: [
                {
                    name: "api_key",
                    title: "API key",
                    required: true,
                    set: true,
                    hint: "7f3a",
                    set_by: ME,
                    // Noon (UTC) on 14 October 2026.
                    date_set: 1_791_979_200,
                },
            ],
            detail: "",
            installed_by: ME,
            disabled_tools: ["compare_runs"],
            date_installed: seconds(),
            date_removed: null,
            sha: "9b1f0c4a",
        };
        answers.set("GET /json/armada/plugins", {result: "success", plugins: [full]});
        answers.set("GET /json/armada/plugins/8/tools", {
            result: "success",
            tools: [
                {name: "compare_runs", description: "two runs, metric by metric"},
                {name: "link_run", description: "link a run to a worktree"},
            ],
        });
        answers.set("PATCH /json/armada/plugins/8", (body) => ({
            result: "success",
            plugin: {
                ...full,
                ...(body.settings ? {settings: JSON.parse(body.settings)} : {}),
                ...(body.widget_defaults
                    ? {widget_defaults: JSON.parse(body.widget_defaults)}
                    : {}),
            },
        }));
        const store = plugins.pluginPlaceFromKey("plugins");
        beta_state.state.place = store;
        plugins.openPlugins(store);
        await settle();
        const box = find("#plugins");
        assert.match(
            box.textContent,
            /Weights & Biases\s*plugyawn · nav, column, tools for Onix, posts/,
        );
        assert.match(plugins.pluginsSideHTML(), /Install from an address/);
        assert.equal(plugins.pluginsHead().checklist.cap, "1");

        const page = plugins.pluginPlaceFromKey("plugin:8");
        beta_state.state.place = page;
        plugins.openPlugins(page);
        await settle();
        // The secret: who set it, four dots and its last four, never its value.
        assert.match(box.textContent, /API keySet by Iago on 14 Oct\./);
        assert.equal(box.querySelector(".key code").textContent, "7f3a");
        // Entity is words; Lines in the topic, a choice of three, stands under
        // Where it shows.
        assert.equal(box.querySelector('input[data-set="entity"]').value, "runix");
        const shows = [...box.querySelectorAll("h2")].find(
            (h) => h.textContent === "Where it shows",
        ).nextElementSibling;
        assert.ok(
            shows.querySelector('[data-set="posts"][aria-checked="true"]').textContent === "Both",
        );
        // The tools Onix is given, compare_runs turned off.
        assert.equal(
            box.querySelector('[data-tool="compare_runs"]').getAttribute("aria-checked"),
            "false",
        );

        // A setting saved whole; a secret written, then shown as set.
        const entity = box.querySelector('input[data-set="entity"]');
        entity.value = "runix-ml";
        entity.dispatchEvent(new dom.window.Event("change", {bubbles: true}));
        await settle();
        assert.deepEqual(JSON.parse(requests.at(-1).body.settings), {
            entity: "runix-ml",
            posts: "both",
        });
        await pressOn(box.querySelector("[data-replace]"));
        const field = box.querySelector('input[data-secret="api_key"]');
        assert.equal(field.type, "password");
        field.value = "sk-new-key";
        dom.press("Enter", {target: field});
        await settle();
        assert.deepEqual(JSON.parse(requests.at(-1).body.secrets), {api_key: "sk-new-key"});
        assert.doesNotMatch(box.innerHTML, /sk-new-key/);
        // Where it shows: the nav switch writes the team's defaults whole.
        await pressOn(box.querySelector('[data-shows="runs"][data-where="nav"]'));
        assert.deepEqual(JSON.parse(requests.at(-1).body.widget_defaults), {runs: {nav: "off"}});
        // An entry of the store's index (PL6 reads it): its page, and Install
        // with what was set up.
        const store_again = plugins.pluginPlaceFromKey("plugins");
        beta_state.state.place = store_again;
        plugins.openPlugins(store_again);
        await settle();
        plugins.pluginView().setIndex([
            {
                id: "wandb",
                repository: "plugyawn/armada-wandb",
                commit: "3c51a9e",
                manifest: WANDB_MANIFEST,
            },
        ]);
        await pressOn(box.querySelector("[data-index-open]"));
        assert.match(box.textContent, /What it adds.*A line in the nav, Training/);
        assert.match(box.textContent, /Reach api\.wandb\.ai/);
        // It stands in the store's place: its name in the header, its source in the column.
        assert.equal(plugins.pluginsHead().title, "Weights &amp; Biases");
        assert.match(plugins.pluginsSideHTML(), /plugyawn\/armada-wandb/);
        box.querySelector('input[data-install-secret="api_key"]').value = "sk-wandb";
        box.querySelector('input[data-install-set="entity"]').value = "runix";
        const crashes = [...box.querySelectorAll('[data-install-set="posts"]')].find(
            (b) => b.textContent === "Crashes",
        );
        await pressOn(crashes);
        assert.equal(crashes.getAttribute("aria-checked"), "true");
        answers.set("POST /json/armada/plugins", {
            result: "error",
            msg: "Weights & Biases is installed here already.",
            code: "BAD_REQUEST",
        });
        await pressOn(box.querySelector("[data-install]"));
        const install = requests.at(-1).body;
        assert.equal(JSON.parse(install.manifest).id, "wandb");
        assert.deepEqual(
            [
                install.source,
                install.sha,
                JSON.parse(install.settings),
                JSON.parse(install.secrets),
            ],
            [
                "plugyawn/armada-wandb",
                "3c51a9e",
                {entity: "runix", posts: "crashes"},
                {api_key: "sk-wandb"},
            ],
        );
        assert.equal(
            box.querySelector(".go-row .cap").textContent,
            "Weights & Biases is installed here already.",
        );
        answers.delete("POST /json/armada/plugins");
        for (const key of [
            "GET /json/armada/plugins",
            "GET /json/armada/plugins/8/tools",
            "PATCH /json/armada/plugins/8",
        ]) {
            answers.delete(key);
        }
        beta_state.state.place = place;
    },
);

run_test(
    "my own view: hidden, labelled, placed, its figure, alerts and rows, whole at once",
    async () => {
        await viewLoaded();
        const mine = (changes) => ({
            type: "armada_widget_preference",
            op: "update",
            preference: {
                install_id: 7,
                widget: "gpus",
                shown: null,
                position: null,
                label: "",
                rest_figure: "",
                filters: {},
                alerts: {},
                in_column: null,
                ...changes,
            },
        });
        const labels = () => rows().map((r) => r.querySelector(".lab").textContent);
        plugins.onPreferenceEvent(mine({shown: false}));
        assert.deepEqual(labels(), ["Training"]);
        // Mine, first in my group, under my label, with another of its figures.
        plugins.onPreferenceEvent(
            mine({shown: true, position: 0, label: "Pods", rest_figure: "jobs"}),
        );
        assert.deepEqual(labels(), ["Pods", "Training"]);
        assert.equal(trail("Pods").textContent, "2 jobs");
        assert.equal(rowOf("Pods").querySelector(".sz").textContent, "88 jobs");
        // One of my alerts is met: the needs-you mark, saying which.
        plugins.onPreferenceEvent(mine({label: "Pods", alerts: {free_at_least: 4}}));
        assert.ok(trail("Pods").matches(".mk.needs"));
        assert.equal(rowOf("Pods").dataset.tip, "When this many are free: 4");
        // The sheet shows only the rows my facets keep.
        plugins.onPreferenceEvent(mine({label: "Pods", filters: {gpu: "H100"}}));
        await pressOn(rowOf("Pods"));
        assert.deepEqual(
            [...sheet().querySelectorAll(".wl > li")].map((li) => li.dataset.row),
            ["nlscale-a", "nlscale-c", "lr-3e-4-b", "data-mix-sweep"],
        );
        assert.equal(sheet().getAttribute("aria-label"), "Pods");
        // And as the filter changes, the open sheet follows.
        plugins.onPreferenceEvent(mine({label: "Pods", filters: {gpu: "A100"}}));
        assert.deepEqual(
            [...sheet().querySelectorAll(".wl > li")].map((li) => li.dataset.row),
            ["nlscale-b"],
        );
        dom.press("Escape");
        // W&B's list leaves my column, and only mine.
        plugins.onPreferenceEvent({
            ...mine({in_column: false}),
            preference: {...mine({in_column: false}).preference, install_id: 8, widget: "runs"},
        });
        assert.equal(section(), null);
        plugins.onPreferenceEvent({
            type: "armada_widget_preference",
            op: "remove",
            install_id: 8,
            widget: "runs",
        });
        assert.ok(section());
        plugins.onPreferenceEvent({
            type: "armada_widget_preference",
            op: "remove",
            install_id: 7,
            widget: "gpus",
        });
        assert.deepEqual(labels(), ["Training", "GPUs"]);

        // A change of mine is whole in the frame of the press, then sent; a
        // refusal puts back what was there.
        const done = plugins.setPreference(7, "gpus", {shown: false, position: null});
        assert.deepEqual(labels(), ["Training"]);
        await done;
        assert.deepEqual(requests.at(-1), {
            method: "PATCH",
            path: "/json/armada/widgets/7/gpus/preference",
            body: {shown: "false", reset: '["position"]'},
        });
        answers.set("PATCH /json/armada/widgets/7/gpus/preference", {
            result: "error",
            msg: "GPUs is removed.",
            code: "BAD_REQUEST",
        });
        const refused = plugins.setPreference(7, "gpus", {shown: true, label: "Pods"});
        assert.deepEqual(labels(), ["Training", "Pods"]);
        await refused;
        assert.deepEqual(labels(), ["Training"]);
        assert.equal(said.at(-1), "GPUs is removed.");
        answers.delete("PATCH /json/armada/widgets/7/gpus/preference");
        plugins.onPreferenceEvent({
            type: "armada_widget_preference",
            op: "remove",
            install_id: 7,
            widget: "gpus",
        });
        assert.deepEqual(labels(), ["Training", "GPUs"]);
    },
);
