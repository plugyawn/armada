"use strict";

// A bot's words in the interim beta (web/src/armada/beta/parts.js, feed.js),
// with a real DOM. People talk in bubbles and machines do not (statement 42;
// the plugin design's decision 9, one rule for every machine voice): the Ops
// bot's alert, a plugin's line and the notification bot's notice stand on
// the plane as Onix's words do, each post under its face, its name and its
// time. People keep their bubbles, and Onix is drawn as before. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<main>
  <div class="band"><div class="lane"><button id="jump" class="off">Jump to latest</button></div></div>
  <div class="feed" id="pane"><div class="clip"><div class="lane" id="col"></div></div></div>
</main>`);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("localStorage", dom.window.localStorage);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global(
    "ResizeObserver",
    class {
        observe() {}
    },
);
Object.defineProperty(document, "fonts", {value: {addEventListener() {}}});
// jsdom lays nothing out: every box, a word's included, is empty.
dom.window.Range.prototype.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0});
const noop = () => {};
set_global("M", {
    T: {},
    release: noop,
    appear: noop,
    fade: noop,
    flip: noop,
    halt: noop,
});
set_global("host", noop);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane,
    band: {el: find(".band"), jump: find("#jump")},
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const parts = zrequire("armada/beta/parts.js");
const feed = zrequire("armada/beta/feed.js");
const hover = zrequire("armada/beta/hover.js");
const {hhmm} = zrequire("armada/beta/time.js");

const ME = 8;
const IAGO = 5;
const AGENT = 9;
const OPS = 10;
const NOTIFICATIONS = 6;
beta_state.state.ME = ME;
beta_state.armadaBots.add(AGENT);
for (const [id, name, bot] of [
    [ME, "Progyan", false],
    [IAGO, "Iago", false],
    [AGENT, "Onix", true],
    [OPS, "Ops", true],
    [NOTIFICATIONS, "Notification Bot", true],
]) {
    beta_state.people.set(id, {id, name, email: `${id}@example.com`, bot, active: true});
}
beta_state.streams.set(4, {stream_id: 4, name: "ops"});

// Production's alert in #ops > file-backup, as the server rendered it.
const ALERT =
    '<p><strong>Critical</strong>: no file snapshot in 2 hours; no nightly backup in 26 hours.<br>\nRunbook: <code>docs/armada/operations.md</code>, section "file-backup".</p>';
const T0 = new Date(2026, 9, 16, 21, 0).getTime() / 1000;
const message = (id, sender_id, minute, content = ALERT) => ({
    id,
    type: "stream",
    stream_id: 4,
    subject: "file-backup",
    sender_id,
    timestamp: T0 + minute * 60,
    content,
    reactions: [],
    flags: ["read"],
});

run_test("a bot's words stand on the plane; a person's are a bubble", () => {
    const kinds = (sender) => parts.partsOf(message(1, sender, 0)).map((part) => part.t);
    assert.deepEqual(kinds(OPS), ["ans"]);
    assert.deepEqual(kinds(NOTIFICATIONS), ["ans"]);
    assert.deepEqual(kinds(AGENT), ["ans"]);
    assert.deepEqual(kinds(IAGO), ["b"]);
});

run_test("each bot post stands under its face, its name and its time", () => {
    const p = routing.placeFromKey(routing.tkey(4, "file-backup"));
    beta_state.state.place = p;
    const c = routing.cacheOf(p);
    beta_state.state.P = c;
    // Two alerts a minute apart, a person, then the notification bot.
    for (const m of [
        message(16, OPS, 0),
        message(17, OPS, 1),
        message(18, IAGO, 2, "<p>looking</p>"),
        message(19, NOTIFICATIONS, 3, "<p>Progyan created a new channel #ops.</p>"),
    ]) {
        routing.addMsg(c, m);
    }
    c.loaded = true;
    c.oldest = true;
    feed.drawLane();
    // Each turn's label line, read piece by piece: a person's is their
    // name (their face stands in the gutter); a bot's is its face (here its
    // initials), its name and its time.
    const labels = [...lane.querySelectorAll(":scope > section")].map((turn) =>
        [...turn.querySelector(":scope > div").childNodes].map((x) => x.textContent),
    );
    assert.deepEqual(labels, [
        ["Op", "Ops", hhmm(T0)],
        ["Op", "Ops", hhmm(T0 + 60)],
        ["Iago"],
        ["NB", "Notification Bot", hhmm(T0 + 180)],
    ]);
    // The words follow their label, whole.
    assert.match(lane.querySelector(":scope > section").textContent, /Runbook: docs\/armada/);
});

run_test("a bot's post has the message's controls, from the keyboard too", () => {
    hover.buildOver();
    // The latest message is the one in the Tab order; standing on it brings
    // its controls up inside it.
    dom.press("Tab");
    const post = lane.querySelector("[data-door]");
    assert.equal(post.dataset.mid, "19");
    post.focus();
    assert.deepEqual(
        [...post.querySelectorAll("button")].map((b) => b.getAttribute("aria-label")),
        ["React", "Reply in thread", "Copy text", "More"],
    );
});
