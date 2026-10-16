"use strict";

// The interim beta's artifacts (web/src/armada/beta/artifacts.js), with a
// real DOM and a stand-in server. An artifact Onix's run attached to its
// final message (an "artifact" submessage: tools/armada/artifact_contract.json)
// is the design's block after Onix's words, with its title and one
// caption; Open shows the whole document in the column, which it borrows
// and gives back (Close, Esc, or Open again), with focus back on Open; a
// later artifact replaces it in place; an action asks Onix in this place;
// only the message's own sender's artifact, of kinds this page knows, is
// drawn. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<nav></nav>
<main>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div id="pane"><div id="col"></div></div>
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
const sounds = [];
const noop = () => {};
set_global("M", {
    T: {},
    SLOW: 1,
    release: noop,
    appear: noop,
    fade: noop,
    flip: noop,
    halt: noop,
    land: noop,
    rest: () => ({top: 0, left: 0}),
    refuse: noop,
    leave(element, done) {
        element.remove();
        if (done) {
            done();
        }
    },
});
set_global("S", {
    play(name) {
        sounds.push(name);
    },
});
set_global("host", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
// A face is drawn with no words of its own, as a picture is.
set_global("face", (_spec, cls) => `<i class="av ${cls || ""}"></i>`);
set_global("mk", (name) => `<svg class="mk ${name}"></svg>`);
// The working mark Onix's new turn stands with while its run starts.
set_global("wk", () => `<span class="mk wk"></span>`);

// The stand-in server: each write is kept, and answered from the table a
// test sets (a refusal, or success).
const writes = [];
const refuse = null;
set_global("fetch", async (url, init = {}) => {
    const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) : null;
    if (init.method === "POST") {
        writes.push({url, body});
    }
    const answer =
        refuse && url === "/json/submessage"
            ? {result: "error", msg: refuse, code: "BAD_REQUEST"}
            : url === "/json/messages" && init.method === "POST"
              ? {result: "success", id: 950}
              : {result: "success"};
    return {
        ok: answer.result === "success",
        status: answer.result === "success" ? 200 : 400,
        json: async () => answer,
    };
});

const ME = 3;
const IAGO = 5;
const DESDEMONA = 7;
const AGENT = 9;

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const field = find(".tx");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane,
    nav: find("nav"),
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
    tx: field,
    side: find("#side"),
    outline: find("#outline"),
    go: find(".disc"),
    goc: find(".disc .cell"),
    ph: find(".ph"),
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const hover = zrequire("armada/beta/hover.js");
const artifacts = zrequire("armada/beta/artifacts.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Shiva";
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Shiva"],
    [IAGO, "Iago"],
    [DESDEMONA, "Desdemona"],
    [AGENT, "Onix"],
]) {
    beta_state.people.set(id, {
        id,
        name,
        email: `${id}@example.com`,
        bot: id === AGENT,
        active: true,
    });
}
beta_state.streams.set(10, {stream_id: 10, name: "armada"});
beta_state.repoStreams.add(10);
const place = {
    key: routing.tkey(10, "images"),
    kind: "topic",
    stream_id: 10,
    topic: "images",
    // A repository: what is said here can start Onix's runs.
    repo: true,
};
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
beta_state.state.P = cache;
hover.buildOver();
artifacts.wireArtifacts(find("main"), hover.msgOf);
feed.drawLane();
// A press is told from its own click by the time it went down, which the
// fake clock starts at 0.
clock.tick(1);

// Onix's final message with what its run attached, as the server stores it.
let next_id = 400;
let next_sub = 8000;
function answer(words, docs, sender_id = AGENT) {
    next_id += 1;
    const id = next_id;
    const m = {
        id,
        type: "stream",
        stream_id: 10,
        subject: "images",
        sender_id: AGENT,
        timestamp: Math.floor(Date.now() / 1000),
        content: `<p>${words}</p>`,
        reactions: [],
        submessages: docs.map((doc) => ({
            id: (next_sub += 1),
            message_id: id,
            sender_id,
            msg_type: "artifact",
            content: typeof doc === "string" ? doc : JSON.stringify(doc),
        })),
    };
    routing.addMsg(cache, m);
    feed.appendMessage(lane, m);
    return m;
}
const NUMBERS = {
    version: 1,
    title: "Images: before and after",
    data: {cls: 0.0001, shown: [5, 40, 100]},
    blocks: [
        {
            type: "metric",
            label: "Layout shift",
            value: 0.43,
            bind: "data.cls",
            delta: -0.4299,
            state: "success",
        },
        {type: "meter", label: "Screenshots loaded", value: 60, total: 100, unit: "pictures"},
        {type: "series", label: "Loaded over the scroll", values: [], bind: "data.shown"},
        {type: "key_value", label: "Measured", items: [{key: "Worst frame", value: "18 ms"}]},
        {type: "log", label: "arm check", lines: ["parts.layout-shift PASS"]},
        {type: "copy", heading: "What is left", body: "The scene."},
    ],
    actions: [{label: "Run the checks again", prompt: "Run the image checks again."}],
};
const rowOf = (m) => lane.querySelector(`[data-part="art"][data-mid="${m.id}"]`);
const panel = () => find("#side").querySelector('[role="region"]');
const verb = (m) =>
    rowOf(m).querySelector("button").firstElementChild.firstElementChild.textContent;
function pressOn(target) {
    target.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    target.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true, button: 0, detail: 1}));
}
async function settle() {
    for (let i = 0; i < 20; i += 1) {
        await Promise.resolve();
    }
}

run_test("an artifact is a block after Onix's words, and opens in the column it borrows", () => {
    const m = answer("Here are the numbers.", [NUMBERS]);
    const row = rowOf(m);
    assert.match(row.textContent, /Images: before and after/);
    assert.match(row.textContent, /6 parts/);
    assert.equal(verb(m), "Open");
    // The parts after the words: the row stands last, after the answer.
    assert.equal(row.previousElementSibling.textContent.trim(), "Here are the numbers.");

    pressOn(row.querySelector("button"));
    assert.ok(panel());
    assert.equal(find("#side").dataset.borrowed, "artifact");
    assert.equal(verb(m), "Close");
    assert.equal(row.querySelector("button").getAttribute("aria-expanded"), "true");
    const text = panel().textContent;
    // The figure from the data it is bound to, its change, the meter, the
    // series' range, the table, the log, the words.
    assert.match(text, /Layout shift\s*0\.0001\s*−0\.4299/);
    assert.match(text, /60 of 100 pictures/);
    assert.match(text, /5–100/);
    assert.match(text, /Worst frame\s*18 ms/);
    assert.match(text, /parts\.layout-shift PASS/);
    assert.match(text, /What is left\s*The scene\./);
    assert.equal(panel().querySelector('[role="meter"]').getAttribute("aria-valuenow"), "60");

    // Open again closes it, and the column has its lists back.
    pressOn(row.querySelector("button"));
    assert.equal(panel(), null);
    assert.equal(find("#side").dataset.borrowed, undefined);
    assert.equal(verb(m), "Open");
});

run_test("from the keyboard: Open puts focus in the panel, Esc gives it back to Open", () => {
    const m = answer("Again.", [NUMBERS]);
    const open = rowOf(m).querySelector("button");
    open.focus();
    open.click();
    assert.ok(panel());
    assert.equal(document.activeElement.getAttribute("aria-label"), "Close");
    dom.press("Escape");
    assert.equal(panel(), null);
    assert.equal(document.activeElement, open);
    // Close in the panel does the same.
    open.click();
    dom.click(panel().querySelector('[aria-label="Close"]'));
    assert.equal(panel(), null);
    assert.equal(document.activeElement, open);
});

run_test("an artifact that arrives later replaces the one shown, in place", async () => {
    const m = answer("A first take.", [
        {version: 1, title: "First", blocks: [{type: "copy", body: "one"}]},
    ]);
    pressOn(rowOf(m).querySelector("button"));
    assert.match(panel().textContent, /First\s*one/);
    artifacts.onArtifactEvent({
        type: "submessage",
        msg_type: "artifact",
        message_id: m.id,
        submessage_id: (next_sub += 1),
        sender_id: AGENT,
        content: JSON.stringify({
            version: 1,
            title: "Second",
            blocks: [{type: "log", label: "out", lines: ["a", "b"]}],
        }),
    });
    await settle();
    assert.match(rowOf(m).textContent, /Second\s*2 lines/);
    assert.match(panel().textContent, /Second/);
    assert.match(panel().textContent, /out/);
    assert.doesNotMatch(panel().textContent, /one/);
    artifacts.closeArtifact(true);
});

run_test("an action asks Onix, here, in my name", async () => {
    const m = answer("Numbers.", [NUMBERS]);
    pressOn(rowOf(m).querySelector("button"));
    writes.length = 0;
    pressOn(
        [...panel().querySelectorAll("button")].find(
            (b) => b.textContent === "Run the checks again",
        ),
    );
    await settle();
    const sent = writes.find((w) => w.url === "/json/messages");
    assert.ok(sent);
    assert.equal(sent.body.content, "@**Onix** Run the image checks again.");
    assert.equal(sent.body.agent_followup, "true");
    assert.equal(sent.body.topic, "images");
    artifacts.closeArtifact(true);
});

run_test("only the sender's own artifact, of kinds this page knows, is drawn", () => {
    const other = answer("Not mine to attach.", [NUMBERS], IAGO);
    assert.equal(rowOf(other), null);
    const broken = answer("Broken.", [
        "{not json",
        {version: 2, blocks: [{type: "copy", body: "x"}]},
    ]);
    assert.equal(rowOf(broken), null);
    const unknown = answer("Mixed.", [
        {version: 1, blocks: [{type: "hologram"}, {type: "copy", body: "kept"}]},
    ]);
    assert.match(rowOf(unknown).textContent, /Artifact\s*Text/);
    // The latest of several is the one shown.
    const two = answer("Twice.", [
        {version: 1, title: "Older", blocks: [{type: "copy", body: "a"}]},
        {version: 1, title: "Newer", blocks: [{type: "copy", body: "b"}]},
    ]);
    assert.match(rowOf(two).textContent, /Newer/);
    assert.equal(artifacts.artifactOf({sender_id: AGENT, submessages: []}), null);
});
