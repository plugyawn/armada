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
<div id="beta-data" data-read-receipts="on" data-sprite-version="1"></div>
<nav><div class="fixed"></div><div class="scroll"></div><button class="row me"></button></nav>
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
// A face hops when its person's message lands (partners.js, hopOnce).
dom.window.Element.prototype.animate = () => ({finished: Promise.resolve(), cancel() {}});
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
    figure(cell, value) {
        cell.textContent = String(value);
    },
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

// The partners' pack: two forms of one line, each with its icon.
const form = (num, name, prevo, evos) => ({
    num,
    name,
    types: ["Fire"],
    line: "4",
    prevo,
    evos,
    first: prevo === null,
    sprite: {regular: `${num}.png`, shiny: null, size: [68, 56], box: [0, 0, 68, 56]},
    sv: {path: `sv/${num}.png`, size: [96, 96], box: [0, 0, 96, 96]},
});
const PACK = {species: {4: form(4, "Charmander", null, ["5"]), 5: form(5, "Charmeleon", "4", [])}};
const partner = (species) => ({species, shiny: false, level: 5, stone: "rose"});
const PARTNERS = {result: "success", on: true, partners: {}, agent: null, me: null};

// The stand-in server answers every request with success and nothing in
// it; a message sent is given the next id.
const requests = [];
let next_id = 9000;
set_global("fetch", async (url, init = {}) => {
    const method = init.method ?? "GET";
    requests.push({method, url: url.split("?")[0]});
    if (url.startsWith("/beta/sprites/species.json")) {
        return {ok: true, status: 200, json: async () => PACK};
    }
    if (url.split("?")[0] === "/json/armada/partners") {
        return {ok: true, status: 200, json: async () => PARTNERS};
    }
    const sent = {};
    if (method === "POST" && url.split("?")[0] === "/json/messages") {
        sent.id = next_id;
        next_id += 1;
    }
    return {
        ok: true,
        status: 200,
        json: async () => ({result: "success", msg: "", messages: [], found_oldest: true, ...sent}),
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
const send = zrequire("armada/beta/send.js");
const receipts = zrequire("armada/beta/receipts.js");
const connection = zrequire("armada/beta/connection.js");
const partners = zrequire("armada/beta/partners.js");
const nav = zrequire("armada/beta/nav.js");
const runs = zrequire("armada/beta/runs.js");

const ME = 3;
const IAGO = 5;
const KING = 7;
const AGENT = 9;
beta_state.state.ME = ME;
beta_state.state.meName = "Shiva";
beta_state.state.hd = find("header.hd");
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Shiva"],
    [IAGO, "Iago"],
    [KING, "King Hamlet"],
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
beta_state.streams.set(20, {stream_id: 20, name: "Denmark"});

// Messages are a minute apart from 8:00 on one day (local time), so that
// each person's message after someone else's is a turn of its own and no
// day line falls between two of them unless a test puts one there.
const T0 = new Date(2026, 9, 14, 8, 0).getTime() / 1000;
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

// The searches of the whole lane a piece of work makes.
function lane_searches(work) {
    const count = {n: 0};
    for (const name of ["querySelector", "querySelectorAll", "getElementsByClassName"]) {
        const own = lane[name];
        lane[name] = (...args) => {
            count.n += 1;
            return own.apply(lane, args);
        };
    }
    try {
        work();
    } finally {
        for (const name of ["querySelector", "querySelectorAll", "getElementsByClassName"]) {
            Reflect.deleteProperty(lane, name);
        }
    }
    return count.n;
}
const turn_of = (id) => lane.querySelector(`[data-mid="${id}"]`).closest(".cl");
const thumbs = (id) => ({
    type: "reaction",
    op: "add",
    message_id: id,
    user_id: IAGO,
    emoji_name: "+1",
    emoji_code: "1f44d",
    reaction_type: "unicode_emoji",
});

run_test("an event finds its message's turn without a search of the lane", () => {
    open_with("rows", forty(300));
    // A search of the lane is counted, so that none counted means none made.
    assert.equal(
        lane_searches(() => lane.querySelector(".cl")),
        1,
    );
    assert.equal(
        lane_searches(() => events.onReaction(thumbs(320))),
        0,
    );
    assert.match(turn_of(320).textContent, /\u{1F44D}/u);
});

run_test("a message I send keeps its turn when the server's copy takes its place", () => {
    find(".tx").textContent = "on its way";
    send.send({at: 0});
    const mine = beta_state.state.P.msgs.at(-1);
    const turn = turn_of(mine.id);
    const id = next_id;
    // The server's own event names the local id.
    events.onMessage({
        message: {...message(id, ME, "rows", 400), content: "<p>on its way</p>"},
        local_message_id: mine.id,
        flags: ["read"],
    });
    assert.equal(mine.id, id);
    assert.equal(turn_of(id), turn);
    events.onReaction(thumbs(id));
    assert.match(turn.textContent, /\u{1F44D}/u);
});

const tick_of = (id) => turn_of(id).querySelector(".rt");

run_test("a read receipt finds its tick without a search of the lane", () => {
    // Every other turn is mine, one message each, and carries its tick.
    open_with("ticks", forty(500));
    const mine = [501, 503, 505];
    for (const id of mine) {
        assert.match(tick_of(id).getAttribute("aria-label"), /^Sent/);
    }
    assert.equal(
        lane_searches(() => receipts.onReadReceipts({message_ids: mine, user_id: IAGO})),
        0,
    );
    for (const id of mine) {
        assert.match(tick_of(id).getAttribute("aria-label"), /^Read/);
    }
});

run_test("who read a message is kept only while a place holds the message", () => {
    // A message of mine in a place this page has not opened: nothing is
    // kept for it (the place asks when it opens) ...
    receipts.onReadReceipts({message_ids: [123456], user_id: IAGO});
    assert.ok(!receipts.rcpt.has(123456));
    // ... and a message deleted takes what was known of it along.
    assert.ok(receipts.rcpt.has(501));
    events.onDelete({message_ids: [501]});
    assert.ok(!receipts.rcpt.has(501));
});

// A lane as a canonical string (each element's tag, classes and other
// attributes in order, and its text): two lanes that read the same give
// the same string, whatever order their classes were set in.
const shape = (node) =>
    [...node.childNodes]
        .map((n) => {
            if (n.nodeType === 3) {
                return JSON.stringify(n.data);
            }
            const attrs = [...n.attributes]
                .filter((a) => a.name !== "class")
                .map((a) => `${a.name}=${a.value}`)
                .toSorted();
            return `<${n.tagName} ${[...n.classList].toSorted().join(".")} ${attrs.join(" ")}>${shape(n)}</>`;
        })
        .join("");

run_test("an older page adds only its own rows, and the lane reads as if drawn whole", () => {
    // The lane's newest forty, and a page of forty before them that ends
    // with Iago a minute before Iago's turn begins the lane.
    const c = open_with(
        "pages",
        Array.from({length: 40}, (_, i) => message(1000 + i, i % 2 ? ME : IAGO, "pages", 100 + i)),
    );
    c.oldest = false;
    feed_js.ends(c);
    const there = [...lane.querySelectorAll(".cl")];
    for (const m of Array.from({length: 40}, (_, i) =>
        message(960 + i, i % 2 ? IAGO : ME, "pages", 60 + i),
    )) {
        routing.addMsg(c, m);
    }
    feed_js.catchUp(c, {keep: true});
    // Every turn that was there is there still, Iago's first one now with
    // the page's last message at its head ...
    assert.ok(there.every((t) => lane.contains(t)));
    assert.equal(turn_of(999), turn_of(1000));
    assert.equal(lane.querySelectorAll(".day").length, 1);
    // ... and the lane reads as the same messages drawn whole would.
    const added = shape(lane);
    feed_js.drawLane();
    assert.equal(added, shape(lane));
});

run_test("a deleted message leaves in place, and the turns either side join as drawn", () => {
    const c = open_with("deletes", [
        message(1990, IAGO, "deletes", -1500),
        message(2000, IAGO, "deletes", 10),
        message(2001, ME, "deletes", 11),
        message(2002, IAGO, "deletes", 12),
        message(2003, ME, "deletes", 13),
        message(2004, IAGO, "deletes", 14),
    ]);
    assert.equal(lane.querySelectorAll(".day").length, 2);
    const first = turn_of(2000);
    const last = turn_of(2004);
    // Mine goes from between Iago's two turns, which become one; the day
    // before loses its only message, and its day line with it.
    events.onDelete({message_ids: [2001, 1990]});
    assert.equal(turn_of(2002), first);
    assert.equal(turn_of(2004), last);
    assert.equal(lane.querySelectorAll(".day").length, 1);
    assert.ok(!c.rows.has(2001));
    const removed = shape(lane);
    feed_js.drawLane();
    assert.equal(removed, shape(lane));
});

// The page's visibility, as the test sets it.
let hidden = false;
Object.defineProperty(document, "hidden", {configurable: true, get: () => hidden});
// Every turn element made from here on is counted as it is made.
let turns_built = 0;
const own_create = document.createElement.bind(document);
document.createElement = (tag, ...args) => {
    if (tag === "section") {
        turns_built += 1;
    }
    return own_create(tag, ...args);
};

run_test("a place left and opened again shows its own rows, and builds none", async () => {
    open_with("kept", forty(3000));
    const there = [...lane.querySelectorAll(".cl")];
    await routing.openPlace(place_of("elsewhere"));
    assert.ok(!there.some((t) => lane.contains(t)));
    // While I am elsewhere, a message arrives there and a reaction lands on
    // an older one: neither is written into a lane no one sees.
    events.onMessage({message: message(3040, IAGO, "kept"), flags: ["read"]});
    events.onReaction(thumbs(3010));
    turns_built = 0;
    await routing.openPlace(place_of("kept"));
    // The same turns are back, and the one turn built is the new message's.
    assert.ok(there.every((t) => lane.contains(t)));
    assert.equal(turns_built, 1);
    assert.ok(turn_of(3040));
    assert.match(turn_of(3010).textContent, /\u{1F44D}/u);
    // The lane reads as the same messages drawn whole.
    const back = shape(lane);
    feed_js.drawLane();
    assert.equal(back, shape(lane));
});

run_test("a hidden page writes no rows, and writes them once when it is seen", () => {
    const c = open_with("hidden", forty(3100));
    const was = lane.innerHTML;
    hidden = true;
    events.onMessage({message: message(3140, IAGO, "hidden"), flags: ["read"]});
    events.onReaction(thumbs(3120));
    events.onDelete({message_ids: [3101]});
    events.onUpdate({
        message_id: 3130,
        rendered_content: "<p>said again</p>",
        edit_timestamp: T0 + 9000,
    });
    // The cache has it all; of the lane, only the deleted message's parts
    // left (that is a removal, and reads no layout).
    assert.ok(c.byId.has(3140));
    assert.notEqual(lane.innerHTML, was);
    assert.ok(!lane.textContent.includes("words of 3140"));
    assert.ok(!lane.textContent.includes("said again"));
    hidden = false;
    feed_js.catchUp(c);
    assert.match(turn_of(3140).textContent, /words of 3140/);
    assert.match(turn_of(3130).textContent, /said again/);
    assert.match(turn_of(3120).textContent, /\u{1F44D}/u);
    const seen = shape(lane);
    feed_js.drawLane();
    assert.equal(seen, shape(lane));
});

const turns = () => lane.querySelectorAll(":scope > .cl").length;
// n turns, one message each, a minute apart from `from`, Iago's and mine.
const many = (from, n, topic) =>
    Array.from({length: n}, (_, i) => message(from + i, i % 2 ? ME : IAGO, topic, i));

run_test("a place left keeps its newest messages, and lets the rest go", async () => {
    const {KEEP} = beta_state;
    const c = open_with("trimmed", many(4000, 300, "trimmed"));
    receipts.onReadReceipts({message_ids: [4001, 4299], user_id: IAGO});
    await routing.openPlace(place_of("trimmed elsewhere"));
    assert.equal(c.msgs.length, KEEP);
    assert.equal(c.msgs[0].id, 4300 - KEEP);
    assert.ok(!beta_state.msgIndex.has(4300 - KEEP - 1));
    assert.ok(beta_state.msgIndex.has(4300 - KEEP));
    assert.ok(!receipts.rcpt.has(4001));
    assert.ok(receipts.rcpt.has(4299));
    assert.equal([...c.park.children].filter((x) => x.matches(".cl")).length, KEEP);
    // Opened again: its newest messages, and the line that offers the rest.
    await routing.openPlace(place_of("trimmed"));
    assert.equal(turns(), KEEP);
    assert.ok(lane.querySelector(":scope > .more-up"));
    const back = shape(lane);
    feed_js.drawLane();
    assert.equal(back, shape(lane));
});

run_test("the cut through a turn leaves the rest of the turn, as drawn", async () => {
    const {KEEP} = beta_state;
    // Iago's first turn is four messages; the place holds two more than it
    // keeps, so the cut falls inside that turn.
    const c = open_with("cut", [
        ...Array.from({length: 4}, (_, i) => message(8000 + i, IAGO, "cut", i)),
        ...Array.from({length: KEEP - 2}, (_, i) =>
            message(8004 + i, i % 2 ? IAGO : ME, "cut", 4 + i),
        ),
    ]);
    const first = turn_of(8000);
    await routing.openPlace(place_of("cut elsewhere"));
    await routing.openPlace(place_of("cut"));
    assert.equal(c.msgs[0].id, 8002);
    assert.equal(turn_of(8002), first);
    const cut = shape(lane);
    feed_js.drawLane();
    assert.equal(cut, shape(lane));
});

run_test("the open place, its end in view, lets its oldest go past the cap", () => {
    const {CAP, KEEP} = beta_state;
    open_with("capped", many(5000, CAP, "capped"));
    beta_state.state.pinned = true;
    assert.equal(turns(), CAP);
    events.onMessage({message: message(5000 + CAP, IAGO, "capped", CAP), flags: ["read"]});
    assert.equal(beta_state.state.P.msgs.length, KEEP);
    assert.equal(turns(), KEEP);
    assert.ok(!beta_state.msgIndex.has(5000));
    // On another view, the rows written wait to be hugged; the ones let go
    // are not held for it.
    beta_state.state.tab = "README";
    for (let i = 1; i <= CAP; i += 1) {
        events.onMessage({
            message: message(5000 + CAP + i, i % 2 ? ME : IAGO, "capped", CAP + i),
            flags: ["read"],
        });
    }
    beta_state.state.tab = "Chat";
    assert.ok(feed_js.toHug.size > 0);
    assert.ok(
        [...feed_js.toHug].every((x) => x.isConnected),
        "a turn let go is held for hugging",
    );
    // While I read further up, nothing is let go.
    beta_state.state.pinned = false;
    const held = beta_state.state.P.msgs.length;
    for (let i = 1; i <= CAP; i += 1) {
        events.onMessage({
            message: message(5000 + 2 * CAP + i, IAGO, "capped", 2 * CAP + i),
            flags: ["read"],
        });
    }
    assert.equal(beta_state.state.P.msgs.length, held + CAP);
});

run_test("ten places are kept at most; one let go hears only its unread count", async () => {
    const {PLACES_KEPT} = beta_state;
    const opened = [];
    for (let i = 0; i <= PLACES_KEPT; i += 1) {
        const p = place_of(`lru ${i}`);
        await routing.openPlace(p);
        opened.push(p.key);
        events.onMessage({message: message(6000 + i * 10, IAGO, `lru ${i}`), flags: ["read"]});
    }
    assert.deepEqual([...beta_state.places.keys()], opened.slice(-PLACES_KEPT));
    assert.ok(!beta_state.msgIndex.has(6000));
    assert.ok(beta_state.msgIndex.has(6010));
    // A message to the place let go is counted unread, and kept nowhere.
    events.onMessage({message: message(6001, IAGO, "lru 0"), flags: []});
    assert.ok(beta_state.unread.get(opened[0]).has(6001));
    assert.ok(!beta_state.msgIndex.has(6001));
    assert.ok(!beta_state.places.has(opened[0]));
    // Opened again, it asks the server for its messages.
    requests.length = 0;
    await routing.openPlace(place_of("lru 0"));
    assert.ok(requests.some((r) => r.method === "GET" && r.url === "/json/messages"));
});

run_test("a lost queue forgets the messages it will ask for again", () => {
    open_with("resync", many(7000, 5, "resync"));
    connection.resync();
    assert.ok(!beta_state.msgIndex.has(7000));
});

run_test("a partner that changes redraws that person's faces, and nothing else", async () => {
    PARTNERS.partners = {[IAGO]: partner("4"), [KING]: partner("4")};
    await partners.loadPartners();
    assert.ok(partners.pack);
    // Iago's and King's faces: in the nav's direct messages, in a lane, and
    // in the rows of a place left.
    beta_state.state.dms = [{user_ids: [IAGO]}, {user_ids: [KING]}];
    open_with("faces left", [message(9500, IAGO, "faces left"), message(9501, KING, "faces left")]);
    await routing.openPlace(place_of("faces"));
    for (const m of [message(9510, IAGO, "faces"), message(9511, KING, "faces")]) {
        events.onMessage({message: m, flags: ["read"]});
    }
    nav.drawNav();
    const nav_rows = [...document.querySelectorAll("nav .row")];
    const kings = [...document.querySelectorAll(`[data-uid="${KING}"]`)];
    const parked = beta_state.places.get(routing.tkey(10, "faces left")).park;
    const iago_left = () => parked.querySelector(`[data-uid="${IAGO}"]`).dataset.fs;
    assert.equal(iago_left(), "sv:4");
    // Iago's partner evolves.
    partners.applyPartners({...PARTNERS, partners: {[IAGO]: partner("5"), [KING]: partner("4")}});
    assert.ok(
        nav_rows.every((r) => r.isConnected),
        "the nav was drawn again",
    );
    assert.ok(
        kings.every((f) => f.isConnected),
        "a face whose partner did not change was drawn again",
    );
    for (const f of document.querySelectorAll(`[data-uid="${IAGO}"][data-fs]`)) {
        assert.equal(f.dataset.fs, "sv:5");
    }
    assert.equal(iago_left(), "sv:5");
});

run_test("a message in a collapsed channel counts on that channel's row, in place", () => {
    assert.ok(!beta_state.expanded.has(20));
    nav.drawNav();
    const rows = [...document.querySelectorAll("nav .row")];
    const denmark = document.querySelector('nav .row[data-k="c:20"]');
    const unseen = (id, topic) => ({
        message: {...message(id, IAGO, topic), stream_id: 20, display_recipient: "Denmark"},
        flags: [],
    });
    events.onMessage(unseen(9600, "ferries"));
    events.onMessage(unseen(9601, "bridges"));
    assert.ok(
        rows.every((r) => r.isConnected),
        "the nav was drawn again",
    );
    assert.equal(denmark.querySelector(".n").textContent, "2");
    assert.ok(denmark.classList.contains("new-in"));
});

run_test("the page's own measurements keep their newest entries", () => {
    const {sends} = beta_state.stats;
    sends.length = 0;
    for (let i = 0; i < 700; i += 1) {
        sends.push({task: i});
    }
    assert.equal(sends.length, 600);
    assert.equal(sends[0].task, 100);
    assert.equal(sends.at(-1).task, 699);
});

run_test("a place keeps its newest runs, and any older one still live", () => {
    const where = {stream: "devel", topic: "runs kept"};
    const run = (run_id, status) => ({
        type: "agent_run",
        ...where,
        run_id,
        status,
        event_type: status,
        run_date_created: T0 + run_id,
        date_created: T0 + run_id,
    });
    open_with("runs kept", []);
    events.onRun(run(1, "running"));
    for (let id = 2; id <= 60; id += 1) {
        events.onRun(run(id, "completed"));
    }
    const seen = runs.runsSeen.get(routing.tkey(10, "runs kept"));
    assert.equal(seen.length, runs.RUNS_KEPT);
    assert.equal(seen[0].id, 60);
    assert.ok(
        seen.some((r) => r.id === 1),
        "a live run was let go",
    );
});
