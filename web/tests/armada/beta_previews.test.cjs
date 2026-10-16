"use strict";

// The interim beta's link previews (web/src/armada/beta/previews.js), with
// a real DOM and a stand-in server: a page's preview is a card under the
// bubble that holds its link, three at most and "+N more" for the rest; a
// link the server marks as being fetched keeps the card's place from the
// first draw (my echo guesses which), and the preview fills that place
// without replacing anything around it; the worker's rendering without a
// preview closes it; a video is a still that opens the video; a link to a
// message is a chip and a quote of the message, read from the page's cache
// or from one request, which opens the message here. jsdom lays nothing
// out, so "moves nothing" is read as the conversation's elements: the same
// ones, in the same order, before and after the preview arrives. The
// measured layout is in var/armada-overhaul/build/dogfood/link-previews.md.
// Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
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
  <div id="pane"><div class="clip"><div id="col"></div></div></div>
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
    current: () => [{}, {}],
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

// The header says where I am; opening a place draws it, which is not what
// these tests read.
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const events = zrequire("armada/beta/events.js");
const send = zrequire("armada/beta/send.js");
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
// This realm previews links and pictures, as the box's dev realm does.
beta_state.state.realm = {
    ...beta_state.state.realm,
    urlPreviews: true,
    imagePreviews: true,
    serverUrlPreviews: true,
    serverImagePreviews: true,
};
beta_state.streams.set(10, {stream_id: 10, name: "devel"});

// The markup the box's server wrote for each kind of link (read from
// #devel > "link previews: samples" on 16 October).
const GITHUB = "https://github.com/pallets/click";
const words_with = (words, href, text = href) => `<p>${words} <a href="${href}">${text}</a></p>`;
// The same, as the server first renders it: the link is marked while its
// preview is fetched.
const pending = (words, href) =>
    `<p>${words} <a data-preview="pending" href="${href}">${href}</a></p>`;
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
const narrow = (topic, id) => `#narrow/channel/10-devel/topic/${topic}/near/${id}`;

// #devel > links: an old message of Iago's that others link to.
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

async function settle() {
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
    // A frame and the task after it: a quote's request waits for both.
    clock.tick(20);
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
}
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
// What the conversation is made of: every turn and every part of a
// message, in order. An element replaced or inserted changes it.
const anatomy = () => [...lane.querySelectorAll(":scope > section, [data-part]")];

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

run_test("a link being fetched keeps its card's place, and the preview fills it", async () => {
    serve();
    deliver(message(2010, IAGO, 5, pending("have a look:", GITHUB)));
    const [held] = cards(2010);
    assert.ok(held.hasAttribute("data-wait"), "the card's place is kept from the first draw");
    assert.equal(held.querySelector(".pvh").textContent, "github.com");
    assert.equal(held.getAttribute("href"), GITHUB);
    deliver(message(2011, IAGO, 4, "<p>and the next message, under it</p>"));
    const first = anatomy();
    const words = bubble(2010).querySelector(".b");

    appeared.length = 0;
    render(2010, words_with("have a look:", GITHUB) + "\n" + GITHUB_EMBED);
    // The same elements, in the same order: the bubble was not drawn again
    // (a selection in it survives), the card took the preview in its own box,
    // and nothing came between it and the next message.
    assert.deepEqual(anatomy(), first);
    assert.equal(bubble(2010).querySelector(".b"), words);
    assert.ok(!held.hasAttribute("data-wait"));
    assert.match(held.querySelector(".pvh").textContent, /^GitHub - pallets\/click/);
    assert.ok(held.querySelector(".tn > img"));
    // Only the arrived words fade in.
    assert.ok(appeared.length > 0 && appeared.every((e) => held.contains(e)));
});

run_test("the worker's rendering without a preview closes the place", async () => {
    serve();
    deliver(message(2020, IAGO, 5, pending("a page without a picture:", "https://example.com/")));
    assert.equal(cards(2020).length, 1);
    render(2020, words_with("a page without a picture:", "https://example.com/"));
    assert.equal(cards(2020).length, 0);
    assert.equal(bubble(2020).querySelector("a").getAttribute("href"), "https://example.com/");
    // A rendering that still carries the mark is not the embed worker's
    // (the thumbnail worker's keeps the words as they were): the place stays.
    const upload =
        '<div class="message_inline_image"><a href="/user_uploads/2/ab/shot.png"><img class="image-loading-placeholder" data-original-dimensions="1440x900" src="/static/images/loading/loader-black.svg"></a></div>';
    const thumbnail =
        '<div class="message_inline_image"><a href="/user_uploads/2/ab/shot.png"><img data-original-dimensions="1440x900" src="/user_uploads/thumbnail/2/ab/shot.png/840x560.webp"></a></div>';
    deliver(message(2021, IAGO, 5, pending("a shot and", GITHUB) + "\n" + upload));
    assert.equal(cards(2021).length, 1);
    render(2021, pending("a shot and", GITHUB) + "\n" + thumbnail);
    assert.ok(cards(2021)[0].hasAttribute("data-wait"));
    render(2021, words_with("a shot and", GITHUB) + "\n" + thumbnail + "\n" + GITHUB_EMBED);
    assert.ok(!cards(2021)[0].hasAttribute("data-wait"));
});

run_test("the server's marks say which links keep a place, for a while", async () => {
    serve();
    // A marked link keeps its card's place; an unmarked one, none.
    deliver(
        message(
            2022,
            IAGO,
            5,
            `<p>see <a data-preview="pending" href="${GITHUB}">${GITHUB}</a> and <a href="https://example.org/x">x</a></p>`,
        ),
    );
    assert.deepEqual(
        cards(2022).map((c) => [c.getAttribute("href"), c.hasAttribute("data-wait")]),
        [[GITHUB, true]],
    );
    // A message loaded with the conversation, ten seconds old, keeps it too;
    // a mark on one sent long ago was left behind, and keeps nothing.
    routing.addMsg(cache, message(2023, IAGO, 10, pending("fresh:", "https://example.org/fresh")));
    routing.addMsg(cache, message(2024, IAGO, 600, pending("old:", "https://example.org/old")));
    feed.drawLane();
    assert.equal(cards(2023).length, 1);
    assert.equal(cards(2024).length, 0);
});

run_test("my echo guesses the links the server will fetch", async () => {
    const tpl = document.createElement("template");
    const held = (sender, content) => {
        tpl.innerHTML = content;
        return previews.fetchedLinks(tpl.content, sender);
    };
    assert.deepEqual(held(IAGO, words_with("see", GITHUB)), [GITHUB]);
    // The agent's own posts are previewed; another bot's are not.
    assert.deepEqual(held(AGENT, words_with("see", GITHUB)), [GITHUB]);
    assert.deepEqual(held(NOTICE_BOT, words_with("see", GITHUB)), []);
    // A YouTube link is fetched for its title.
    assert.deepEqual(held(IAGO, words_with("see", YOUTUBE)), [YOUTUBE]);
    // Inside a quote, to this server, relative, a picture by its name,
    // Dropbox: none.
    assert.deepEqual(held(IAGO, `<blockquote>${words_with("see", GITHUB)}</blockquote>`), []);
    assert.deepEqual(held(IAGO, words_with("see", "http://zulip.zulipdev.com/help/")), []);
    assert.deepEqual(held(IAGO, words_with("see", "/user_uploads/2/ab/notes.txt")), []);
    assert.deepEqual(held(IAGO, words_with("see", "https://example.com/cat.png")), []);
    assert.deepEqual(held(IAGO, words_with("see", "https://www.dropbox.com/s/abc/plan")), []);
    // A message with more than 24 links is previewed not at all.
    const many = Array.from(
        {length: 25},
        (_, i) => `<a href="https://example.com/${i}">${i}</a>`,
    ).join(" ");
    assert.deepEqual(held(IAGO, `<p>${many}</p>`), []);
    // With the realm's previews off, nothing is fetched.
    beta_state.state.realm.urlPreviews = false;
    assert.deepEqual(held(IAGO, words_with("see", GITHUB)), []);
    beta_state.state.realm.urlPreviews = true;
});

run_test("+N more opens the rest in place, under the cards that stay", async () => {
    serve();
    const pages = ["a", "b", "c", "d", "e"].map((x) => `https://example.org/w${x}`);
    deliver(
        message(
            2025,
            IAGO,
            5,
            `<p>${pages.map((h) => `<a data-preview="pending" href="${h}">${h}</a>`).join(" ")}</p>`,
        ),
    );
    const shown = cards(2025);
    assert.equal(shown.length, 3);
    const words = bubble(2025).querySelector(".b");
    dom.click(lane.querySelector('.pvm[data-mid="2025"]'));
    assert.equal(cards(2025).length, 5);
    assert.deepEqual(cards(2025).slice(0, 3), shown);
    assert.equal(bubble(2025).querySelector(".b"), words);
});

run_test("my echo keeps my links' places, and the server's copy keeps them", async () => {
    serve();
    field.textContent = `have a look at ${GITHUB}.`;
    field.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
    send.send({at: 0});
    // At once: the address is a link, as the server will make it, and its
    // card's place is there.
    const local = beta_state.state.P.msgs.at(-1);
    assert.ok(local.local);
    const [held] = cards(local.id);
    assert.ok(held.hasAttribute("data-wait"));
    assert.equal(bubble(local.id).querySelector("a").getAttribute("href"), GITHUB);
    await settle();
    // The server's copy, then its rendering with the preview.
    const server = message(
        5000,
        ME,
        0,
        `<p>have a look at <a data-preview="pending" href="${GITHUB}">${GITHUB}</a>.</p>`,
    );
    events.onMessage({
        type: "message",
        message: server,
        flags: ["read"],
        local_message_id: local.id,
    });
    assert.deepEqual(cards(5000), [held]);
    render(5000, `<p>have a look at <a href="${GITHUB}">${GITHUB}</a>.</p>\n${GITHUB_EMBED}`);
    assert.deepEqual(cards(5000), [held]);
    assert.ok(!held.hasAttribute("data-wait"));
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
    const words = bubble(2030).querySelector(".b");
    render(2030, youtube("YouTube - Never Gonna Give You Up"));
    assert.equal(bubble(2030).querySelector(".b"), words);
    assert.equal(bubble(2030).querySelector("a").textContent, YOUTUBE);
    assert.equal(cards(2030)[0], card);
    assert.equal(card.querySelector(".pvh").textContent, "Never Gonna Give You Up");
});

run_test("a link to a message is a chip and a quote, from the cache or one request", async () => {
    serve([
        [
            "GET",
            /^\/json\/messages$/,
            ({query}) =>
                query.message_ids
                    ? success({
                          messages: [
                              message(77, IAGO, 86400, "<p>@Iago said it first</p>", "older"),
                          ],
                      })
                    : success({messages: []}),
        ],
    ]);
    const origin = dom.window.location.origin;
    deliver(
        message(
            2040,
            IAGO,
            5,
            `<p>as <a href="${narrow("links", 1002)}">${origin}/${narrow("links", 1002)}</a> says, and ` +
                `<a class="message-link" href="/${narrow("older", 77)}">#devel &gt; older @ 💬</a> and ` +
                `<a href="${narrow("older", 78)}">this one</a></p>`,
        ),
    );
    // In the words: the place, which opens the message here.
    const chips = [...bubble(2040).querySelectorAll("a[data-msg]")];
    assert.deepEqual(
        chips.map((a) => [a.className, a.textContent, a.dataset.msg, a.target]),
        [
            ["placelink", "devel›links", "1002", ""],
            ["placelink", "devel›older", "77", ""],
            ["msglink", "this one", "78", ""],
        ],
    );
    // Under them, a quote of each: the one in the cache at once ...
    const [cached, asked, unseen] = cards(2040);
    assert.equal(cached.querySelector(".pvh b").textContent, "Iago");
    assert.equal(cached.querySelector(".pvc").textContent, "the old decision: cards are 368 wide");
    assert.ok(cached.querySelector(".pvh time"));
    assert.ok(asked.hasAttribute("data-wait") && unseen.hasAttribute("data-wait"));
    // ... the others from one request for both.
    await settle();
    assert.deepEqual(
        requests.map((r) => [r.method, r.path, r.query.message_ids]),
        [["GET", "/json/messages", "[77,78]"]],
    );
    assert.equal(asked.querySelector(".pvc").textContent, "Iago said it first");
    assert.ok(!asked.hasAttribute("data-wait"));
    assert.equal(unseen.querySelector(".pvh").textContent, "Not available");
    // Drawn again, nothing is asked again.
    feed.drawLane();
    await settle();
    assert.equal(requests.length, 1);
});

run_test("quotes count among a bubble's three cards", async () => {
    serve();
    const ids = [1002, 77, 78, 2001];
    deliver(
        message(
            2045,
            IAGO,
            5,
            `<p>${ids.map((id) => `<a href="${narrow("links", id)}">this ${id}</a>`).join(" ")}</p>`,
        ),
    );
    assert.equal(cards(2045).length, 3);
    assert.equal(lane.querySelector('.pvm[data-mid="2045"]').textContent, "+1 more");
});

run_test("pressing a quote opens the message in its place, not a new tab", async () => {
    serve([
        [
            "GET",
            /^\/json\/messages$/,
            ({query}) =>
                success({
                    messages: query.narrow
                        ? [message(77, IAGO, 86400, "<p>@Iago said it first</p>", "older")]
                        : [],
                    found_oldest: true,
                    found_newest: true,
                }),
        ],
        ["GET", /^\/json\/users\/me\/\d+\/topics$/, () => success({topics: []})],
    ]);
    const quote = cards(2040)[1];
    // A press with a modifier is the browser's (a new tab): the page stays.
    assert.equal(dom.click(quote, {metaKey: true}), true);
    await settle();
    assert.equal(beta_state.state.place.key, place.key);
    // A press opens the message here, in its own place.
    assert.equal(dom.click(quote), false, "the browser does not follow the link");
    await settle();
    assert.equal(beta_state.state.place.key, routing.tkey(10, "older"));
    assert.ok(lane.querySelector('[data-part][data-mid="77"]'));
    // A link that names no place (#narrow/near/<id>) opens where the message is.
    await routing.openPlace(place);
    deliver(message(2041, IAGO, 5, `<p>here: <a href="#narrow/near/77">that</a></p>`));
    assert.equal(dom.click(bubble(2041).querySelector("a[data-msg]")), false);
    await settle();
    assert.equal(beta_state.state.place.key, routing.tkey(10, "older"));
});

run_test("a place the server does not answer in time closes", async () => {
    serve();
    // Back to #devel > links, where a message waits on its preview.
    await routing.openPlace(place);
    deliver(message(2050, IAGO, 1, pending("slow page:", "https://slow.example.org/")));
    assert.equal(cards(2050).length, 1);
    clock.tick(previews.WAIT_LIMIT + 1);
    assert.equal(cards(2050).length, 0);
    assert.ok(bubble(2050));
});
