"use strict";

// The interim beta's conversation lane (web/src/armada/beta/feed.js and the
// modules that write into it), with a real DOM and a stand-in server: what
// moves when the conversation grows, and what is kept, built again or let
// go as places are opened, left and filled. jsdom lays nothing out, so the
// lane is given a layout of its own: every row 50 px high, one under the
// other, the conversation's end at the foot of a 500 px pane (as a pinned
// feed is). The beta reads the kit's window globals when it draws; the ones
// it needs here are stand-ins that draw nothing of their own. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data" data-read-receipts="on"></div>
<nav><div class="fixed"></div><div class="scroll"></div></nav>
<main>
  <header class="hd"></header>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div class="feed" id="pane"><div class="clip"><div class="lane" id="col"></div></div></div>
  <div class="dock"><div class="cz"><div class="sh">
    <span class="txw"><div class="tx" contenteditable="true"></div><span class="ph"></span></span>
    <button class="k to"></button><button class="k mode"></button>
    <button class="disc" aria-label="Send"><span class="cell"><span>Send</span></span></button>
  </div></div></div>
  <aside class="side" id="side"><div class="out" id="outline"></div></aside>
  <div class="pane" data-pane="Home" hidden><div class="lane" id="home"></div></div>
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
set_global("HTMLImageElement", dom.window.HTMLImageElement);
set_global(
    "ResizeObserver",
    class {
        observe() {}
        unobserve() {}
    },
);
set_global(
    "BroadcastChannel",
    class {
        postMessage() {}
    },
);
Object.defineProperty(document, "fonts", {
    value: {addEventListener() {}, ready: Promise.resolve()},
});

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const feed = find("#pane");

// The lane's layout: its rows are 50 px high, one under the other, and its
// end stands at the foot of the 500 px pane.
const ROW = 50;
const VIEW = 500;
const lane_top = () => VIEW - lane.children.length * ROW;
const in_lane = (element) => {
    let x = element;
    while (x && x.parentElement !== lane) {
        x = x.parentElement;
    }
    return x;
};
const index_in_lane = (row) => [...lane.children].indexOf(row);
for (const [name, value] of [
    ["offsetTop", (e) => (e.parentElement === lane ? index_in_lane(e) * ROW : 0)],
    ["offsetHeight", (e) => (e.parentElement === lane ? ROW : 0)],
]) {
    Object.defineProperty(dom.window.HTMLElement.prototype, name, {
        configurable: true,
        get() {
            return value(this);
        },
    });
}
lane.getBoundingClientRect = () => ({top: lane_top(), bottom: VIEW, left: 0, right: 600});
feed.getBoundingClientRect = () => ({top: 0, bottom: VIEW, left: 0, right: 600, height: VIEW});

// The motion kit: what is released is kept, and a spring stays where it
// was released to (no time passes here) until it is halted.
const springs = new Map();
const releases = [];
const spring = (element, id) => springs.get(element)?.get(id ?? "m");
const noop = () => {};
set_global("M", {
    T: {},
    SLOW: 1,
    release(element, add, token, o = {}) {
        const id = o.id ?? "m";
        const [x, v] = this.current(element, id);
        const y = (x.y ?? 0) + (add.y ?? 0);
        releases.push({element, y, add: add.y ?? 0, token, id});
        if (!springs.has(element)) {
            springs.set(element, new Map());
        }
        springs.get(element).set(id, {y, v: (v.y ?? 0) + (o.kick?.y ?? 0)});
        return null;
    },
    current(element, id) {
        const s = spring(element, id);
        return s ? [{y: s.y}, {y: s.v}] : [{}, {}];
    },
    halt(element, id) {
        springs.get(element)?.delete(id ?? "m");
    },
    rest(element) {
        const row = in_lane(element);
        const top = row ? lane_top() + index_in_lane(row) * ROW : 0;
        return {top, left: 0, bottom: top + ROW, right: 0, width: 0, height: ROW};
    },
    reduced: () => false,
    appear: noop,
    fade: noop,
    flip: noop,
    land: noop,
    figure: noop,
    cross: noop,
    say: noop,
    refuse: noop,
    burst: noop,
    squash: noop,
    kick: noop,
    focusKbd: noop,
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
set_global("fitLists", noop);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

// The stand-in server answers every request with success and nothing in it.
const requests = [];
set_global("fetch", async (url, init = {}) => {
    requests.push({method: init.method ?? "GET", url: url.split("?")[0]});
    return {
        ok: true,
        status: 200,
        json: async () => ({result: "success", msg: "", messages: [], found_oldest: true}),
    };
});

mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed,
    lane,
    nav: find("nav"),
    navFixed: find("nav > .fixed"),
    navScroll: find("nav > .scroll"),
    side: find("#side"),
    outline: find("#outline"),
    panes: {Chat: feed, Home: find('[data-pane="Home"]')},
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
    go: find(".disc"),
    goc: find(".disc .cell"),
    ph: find(".ph"),
    toBtn: find(".k.to"),
    modeBtn: find(".k.mode"),
});
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop});
mock_esm("../../src/armada/beta/column.js", {drawSide: noop});
mock_esm("../../src/armada/beta/band.js", {drawRunState: noop, stop: noop, tick: noop});
mock_esm("../../src/armada/beta/lab.js", {buildLab: noop, drawLab: noop, lab: null});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed_js = zrequire("armada/beta/feed.js");
const events = zrequire("armada/beta/events.js");

const ME = 3;
const IAGO = 5;
const AGENT = 9;
beta_state.state.ME = ME;
beta_state.state.meName = "Shiva";
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Shiva"],
    [IAGO, "Iago"],
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
beta_state.streams.set(10, {stream_id: 10, name: "devel"});

// Every message is on one day, a minute apart, so that each person's
// message after someone else's is a turn of its own.
const T0 = Math.floor(Date.now() / 1000) - 7200;
const message = (id, sender_id, topic = "lane", minute = id) => ({
    id,
    type: "stream",
    stream_id: 10,
    subject: topic,
    display_recipient: "devel",
    sender_id,
    timestamp: T0 + minute * 60,
    content: `<p>words of ${id}</p>`,
    reactions: [],
    flags: ["read"],
});
const place_of = (topic) => routing.placeFromKey(routing.tkey(10, topic));
// A place opened and drawn with these messages, as openPlace leaves one
// once the server has answered.
function open_with(topic, msgs) {
    const p = place_of(topic);
    beta_state.state.place = p;
    const c = routing.cacheOf(p);
    beta_state.state.P = c;
    for (const m of msgs) {
        routing.addMsg(c, m);
    }
    c.loaded = true;
    c.oldest = true;
    feed_js.drawLane();
    return c;
}
// Forty turns, alternating between Iago and me.
const forty = (from) => Array.from({length: 40}, (_, i) => message(from + i, i % 2 ? ME : IAGO));
const lane_moves = () => releases.filter((r) => r.id === "lane" || r.element === lane);
const rows_in_view = (shift) =>
    [...lane.children].filter((_row, i) => {
        const top = lane_top() + i * ROW;
        return top + ROW >= -Math.max(0, shift) && top <= VIEW - Math.min(0, shift);
    });

run_test("a message that lands moves the rows in view, never the conversation", () => {
    open_with("lane", forty(100));
    releases.length = 0;
    springs.clear();
    events.onMessage({message: message(140, IAGO), flags: ["read"]});
    const moved = lane_moves();
    // The new turn pushed the conversation up one row; what is in view at
    // some moment of that move (the 500 px pane and the 50 px under it the
    // move uncovers) is released, and nothing else: not the lane, not the
    // thirty rows above.
    assert.ok(!moved.some((r) => r.element === lane), "the lane itself was released");
    assert.deepEqual(
        moved.map((r) => r.element),
        rows_in_view(ROW),
    );
    assert.equal(moved.length, 12);
    assert.ok(moved.every((r) => r.y === ROW && r.token === "settle"));
});

run_test("a row that joins a move under way takes the move's place in it", () => {
    // The last message's move has not ended (no time passes here): another
    // lands while the rows in view are still 50 px from rest.
    releases.length = 0;
    events.onMessage({message: message(141, ME), flags: ["read"]});
    const moved = lane_moves();
    assert.ok(!moved.some((r) => r.element === lane), "the lane itself was released");
    assert.deepEqual(
        moved.map((r) => r.element),
        rows_in_view(2 * ROW),
    );
    // Every row in view is now 100 px from rest: the ones that were moving
    // went on from where they were, the ones that were not (the new row and
    // the one the larger move brings into view) start where those are, so
    // no two rows overlap.
    assert.ok(moved.every((r) => r.y === 2 * ROW));
    assert.ok(moved.some((r) => r.add === ROW));
    assert.ok(moved.some((r) => r.add === 2 * ROW));
});
