"use strict";

// The interim beta's link previews (web/src/armada/beta/previews.js), with
// a real DOM and a stand-in server: a page's preview is a card under the
// bubble that holds its link, and a video is a still that opens the video.
// Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<nav><div class="fixed"></div><div class="scroll"></div></nav>
<aside class="side" id="side"><div class="out" id="outline"></div></aside>
<main>
  <div class="pane" data-pane="Home" hidden><div id="home"></div></div>
  <div class="pane" data-pane="Partner" hidden><div id="picker"></div></div>
  <div class="pane" data-pane="Page" hidden><div id="page"></div></div>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div id="pane"><div id="col"></div></div>
  <div class="dock"><div class="cz"><div class="sh">
    <span class="txw"><div class="tx" contenteditable="true"></div><span class="ph"></span></span>
    <button class="k to"></button><button class="k mode"></button>
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
// The kit's motion and sound draw nothing here; what appears is kept, so a
// test can tell a card that faded in from one that was drawn again.
const appeared = [];
const noop = () => {};
set_global("M", {
    T: {},
    SLOW: 1,
    release: noop,
    appear(element) {
        appeared.push(element);
        return {finished: Promise.resolve()};
    },
    fade: noop,
    flip: noop,
    halt: noop,
    land: noop,
    figure: noop,
    reduced: () => false,
    current: () => [{}],
    rest: () => ({top: 0, left: 0}),
    say: noop,
    refuse: noop,
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
// The column's lists, fitted to its height by the kit: nothing to fit here.
set_global("fitLists", noop);
// The field, drawn by the kit when a place opens: empty here.
set_global("figure", noop);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

const ME = 3;
const IAGO = 5;
const AGENT = 9;
// A bot that is not the agent: the server previews none of its links.
const NOTICE_BOT = 7;
const T0 = Date.now();
const at = (seconds_ago) => Math.floor(T0 / 1000) - seconds_ago;

// The stand-in server: each request is kept with its query, and answered
// from the table a test sets.
const requests = [];
let answers = [];
set_global("fetch", async (url, init = {}) => {
    const method = init.method ?? "GET";
    const [path, search = ""] = url.split("?");
    const query = Object.fromEntries(new URLSearchParams(search));
    requests.push({method, path, query});
    const answer = answers.find(([m, pattern]) => m === method && pattern.test(path));
    assert.ok(answer, `no stand-in answer for ${method} ${path}`);
    const reply = await answer[2]({url, path, query});
    return {ok: (reply.status ?? 200) < 400, status: reply.status ?? 200, json: async () => reply};
});
const success = (data = {}) => ({result: "success", msg: "", ...data});
function serve(extra = []) {
    requests.length = 0;
    answers = [...extra, ["POST", /^\/json\/messages$/, () => success({id: 5000})]];
}

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const field = find(".tx");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane,
    nav: find("nav"),
    navFixed: find("nav .fixed"),
    navScroll: find("nav .scroll"),
    side: find("#side"),
    outline: find("#outline"),
    panes: {
        Chat: find("#pane"),
        Home: find('[data-pane="Home"]'),
        Partner: find('[data-pane="Partner"]'),
        Page: find('[data-pane="Page"]'),
    },
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
    go: find(".disc"),
    toBtn: find(".k.to"),
    modeBtn: find(".k.mode"),
    goc: find(".disc .cell"),
    ph: find(".ph"),
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const events = zrequire("armada/beta/events.js");
const previews = zrequire("armada/beta/previews.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Polonius";
beta_state.armadaBots.add(AGENT);
for (const [id, name, bot] of [
    [ME, "Polonius", false],
    [IAGO, "Iago", false],
    [AGENT, "Onix", true],
    [NOTICE_BOT, "Notice Bot", true],
]) {
    beta_state.people.set(id, {
        id,
        name,
        email: `${id}@example.com`,
        bot,
        active: true,
        owner: null,
    });
}
beta_state.streams.set(10, {stream_id: 10, name: "devel"});

// The markup the box's server wrote for each kind of link (read from
// #devel > "link previews: samples" on 16 October).
const GITHUB = "https://github.com/pallets/click";
const words_with = (words, href, text = href) => `<p>${words} <a href="${href}">${text}</a></p>`;
const embed = (href, image, title, description) =>
    `<div class="message_embed"><a class="message_embed_image" href="${href}" style="background-image: url(&quot;${image}&quot;)"></a>` +
    `<div class="data-container"><div class="message_embed_title"><a href="${href}" title="${title}">${title}</a></div>` +
    `<div class="message_embed_description">${description}</div></div></div>`;
const GITHUB_EMBED = embed(
    GITHUB,
    "https://repository-images.githubusercontent.com/19103692/f4a9",
    "GitHub - pallets/click: Python composable command line interface toolkit",
    "Python composable command line interface toolkit.",
);
const YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
const youtube = (text) =>
    `<p>YouTube <a href="${YOUTUBE}">${text}</a></p>\n<div class="youtube-video message_inline_image"><a data-id="dQw4w9WgXcQ" href="${YOUTUBE}"><img src="https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"></a></div>`;

// #devel > links, with an old message of Iago's.
const place = {
    key: routing.tkey(10, "links"),
    kind: "topic",
    stream_id: 10,
    topic: "links",
    repo: false,
};
const message = (id, sender_id, seconds_ago, content, topic = "links") => ({
    id,
    type: "stream",
    stream_id: 10,
    display_recipient: "devel",
    subject: topic,
    sender_id,
    timestamp: at(seconds_ago),
    content,
    reactions: [],
    flags: ["read"],
});
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
beta_state.state.P = cache;
routing.addMsg(cache, message(1002, IAGO, 3600, "<p>the old decision: cards are 368 wide</p>"));
feed.drawLane();
previews.wirePreviews();

// A message arriving live, and the embed worker's later rendering of it, as
// the server sends them to everyone.
const deliver = (m) => events.onMessage({type: "message", message: m, flags: ["read"]});
const render = (id, rendered_content) =>
    events.onUpdate({
        type: "update_message",
        message_id: id,
        message_ids: [id],
        user_id: null,
        rendering_only: true,
        rendered_content,
    });
const cards = (id) => [...lane.querySelectorAll(`.pv[data-mid="${id}"]`)];
const bubble = (id) => lane.querySelector(`.bl[data-mid="${id}"]`);

run_test("a page's preview is a card under the bubble that holds its link", async () => {
    serve();
    deliver(message(2001, IAGO, 50, words_with("a page:", GITHUB) + "\n" + GITHUB_EMBED));
    const [card] = cards(2001);
    assert.equal(card.previousElementSibling, bubble(2001));
    assert.equal(card.getAttribute("href"), GITHUB);
    assert.equal(card.target, "_blank");
    assert.equal(
        card.querySelector(".pvh").textContent,
        "GitHub - pallets/click: Python composable command line interface toolkit",
    );
    assert.equal(
        card.querySelector(".pvc").textContent,
        "github.com · Python composable command line interface toolkit.",
    );
    // The picture loads lazily, inside its cell, over the link glyph.
    const picture = card.querySelector(".tn > img");
    assert.equal(picture.getAttribute("loading"), "lazy");
    assert.equal(
        picture.getAttribute("src"),
        "https://repository-images.githubusercontent.com/19103692/f4a9",
    );
    assert.ok(card.querySelector(".tn > .ic.link"));
    // The turn's time stays on the bubble, above its card.
    assert.ok(bubble(2001).querySelector("time"));
    // Nothing of the server's own preview markup is left in the words.
    assert.equal(lane.querySelector(".message_embed, .message_embed_image"), null);
    assert.deepEqual(requests, []);
});

run_test("a bubble shows three cards, and +N more opens the rest under them", async () => {
    serve();
    const pages = ["a", "b", "c", "d", "e"].map((x) => `https://example.org/${x}`);
    const content =
        `<p>${pages.map((h) => `<a href="${h}">${h}</a>`).join(" ")}</p>\n` +
        pages.map((h) => embed(h, `${h}.png`, `Page ${h.slice(-1)}`, "words")).join("");
    deliver(message(2060, IAGO, 50, content));
    assert.deepEqual(
        cards(2060).map((c) => c.getAttribute("href")),
        pages.slice(0, 3),
    );
    const more = lane.querySelector('.pvm[data-mid="2060"]');
    assert.equal(more.textContent, "+2 more");
    assert.equal(more.previousElementSibling, cards(2060)[2]);
    // The time stays on the bubble.
    assert.ok(bubble(2060).querySelector("time"));
    // The rest opens under the line and the view keeps its place, even at
    // the conversation's end: nothing above the line moves.
    assert.ok(beta_state.state.pinned);
    const pane = find("#pane");
    const scrolled = [];
    Object.defineProperty(pane, "scrollTop", {
        configurable: true,
        get: () => 0,
        set(y) {
            scrolled.push(y);
        },
    });
    // The spy sees a write, so that none seen means none made.
    pane.scrollTop = 1;
    assert.deepEqual(scrolled, [1]);
    scrolled.length = 0;
    dom.click(more, {detail: 1});
    delete pane.scrollTop;
    assert.deepEqual(scrolled, []);
    assert.deepEqual(
        cards(2060).map((c) => c.getAttribute("href")),
        pages,
    );
    assert.equal(lane.querySelector('.pvm[data-mid="2060"]'), null);
    // A pointer's press leaves focus alone.
    assert.notEqual(document.activeElement, cards(2060)[3]);

    // Enter on the line opens them too (the browser's click, with no
    // pointer), and focus goes on to the first card that opened.
    deliver(message(2061, IAGO, 40, content));
    const line = lane.querySelector('.pvm[data-mid="2061"]');
    line.focus();
    line.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true, detail: 0}));
    assert.equal(cards(2061).length, 5);
    assert.equal(document.activeElement, cards(2061)[3]);
});

run_test("a video is a still that opens the video, under the link as it was typed", async () => {
    serve();
    // The server's first rendering: the link as typed, the still inlined.
    deliver(message(2030, ME, 5, youtube(YOUTUBE)));
    const [card] = cards(2030);
    assert.equal(card.dataset.kind, "video");
    assert.equal(card.getAttribute("href"), YOUTUBE);
    assert.equal(card.target, "_blank");
    assert.ok(card.querySelector(".still > img") && card.querySelector(".still > .play"));
    assert.equal(lane.querySelector("iframe, .youtube-video"), null);
    // Its title arrives as the link's new words; the bubble keeps the link as
    // typed and the title goes to the card.
    render(2030, youtube("YouTube - Never Gonna Give You Up"));
    assert.equal(bubble(2030).querySelector("a").textContent, YOUTUBE);
    assert.equal(cards(2030)[0].querySelector(".pvh").textContent, "Never Gonna Give You Up");
});
