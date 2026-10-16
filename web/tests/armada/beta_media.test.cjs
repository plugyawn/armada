"use strict";

// The interim beta's pictures (web/src/armada/beta/media.js, parts.js),
// with a real DOM and a stand-in server: a picture's box is reserved from
// the size the server writes on it, before a byte of it loads, and stays
// that size when the thumbnail replaces the placeholder; a picture loads
// only when it comes near the conversation's visible part; the link that
// only names a picture is its label, not words; my own picture shows at
// once, with its size, and the server's copy takes its place without a
// gap; a picture that cannot load says so in its own box; the field's
// chip counts what has gone up. jsdom lays nothing out and loads no image:
// the observer and the image loader are stand-ins the tests drive, and the
// boxes are read as the page writes them. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
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
// The fake clock holds queueMicrotask; the page's microtasks run as
// promises do.
set_global("queueMicrotask", async (f) => {
    await Promise.resolve();
    f();
});
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
const said = [];
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
    figure: noop,
    rest: () => ({top: 0, left: 0}),
    say(_target, text) {
        said.push(text);
    },
    refuse: noop,
    leave(element, done) {
        element.remove();
        if (done) {
            done();
        }
    },
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global("face", (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0]}</i>`);

// The observer the page watches its boxes with: the tests say which box
// has come near.
const observers = [];
set_global(
    "IntersectionObserver",
    class {
        constructor(callback, options) {
            this.callback = callback;
            this.options = options;
            this.targets = new Set();
            observers.push(this);
        }
        observe(target) {
            this.targets.add(target);
        }
        unobserve(target) {
            this.targets.delete(target);
        }
    },
);
function comeNear(box) {
    for (const io of observers) {
        if (io.targets.has(box)) {
            io.callback([{target: box, isIntersecting: true}], io);
        }
    }
}
const watched = (box) => observers.some((io) => io.targets.has(box));
// The decoder the page fetches a newer source with, beside the picture on
// screen: the tests say when it has decoded.
const decoding = [];
set_global(
    "Image",
    class {
        decode() {
            return new Promise((resolve) => decoding.push({src: this.src, resolve}));
        }
    },
);

// The stand-in server, for the upload; an upload's progress is the
// XMLHttpRequest's, which the tests drive.
const uploads = [];
set_global(
    "XMLHttpRequest",
    class {
        constructor() {
            this.upload = {};
            uploads.push(this);
        }
        open(method, url) {
            this.method = method;
            this.url = url;
        }
        setRequestHeader() {}
        send(body) {
            this.body = body;
        }
    },
);
// A message I send is the server's message 900.
set_global("fetch", async (url, init = {}) => ({
    ok: true,
    status: 200,
    json: async () =>
        init.method === "POST" && url === "/json/messages"
            ? {result: "success", id: 900}
            : {result: "success"},
}));

const ME = 3;
const IAGO = 5;
const AGENT = 9;

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const feed_el = find("#pane");
const field = find(".tx");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: feed_el,
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
    go: find(".disc"),
    goc: find(".disc .cell"),
    ph: find(".ph"),
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const events = zrequire("armada/beta/events.js");
const composer = zrequire("armada/beta/composer.js");
const send = zrequire("armada/beta/send.js");
const media = zrequire("armada/beta/media.js");
const hover = zrequire("armada/beta/hover.js");

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

const place = {
    key: routing.tkey(10, "screenshots"),
    kind: "topic",
    stream_id: 10,
    topic: "screenshots",
    repo: false,
};
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
beta_state.state.P = cache;

// The server's markup for an upload, as zerver/lib/markdown and
// zerver/lib/thumbnail write it: the name as a link in the words, then the
// preview; before the thumbnail exists the preview is a placeholder that
// already carries the picture's size.
const upload_url = (name) => `/user_uploads/2/ab/x${name.length}/${name}`;
function preview(name, w, h, {placeholder = false} = {}) {
    const url = upload_url(name);
    const img = placeholder
        ? `<img class="image-loading-placeholder" data-original-content-type="image/png" data-original-dimensions="${w}x${h}" src="/static/images/loading/loader-black.svg">`
        : `<img data-original-content-type="image/png" data-original-dimensions="${w}x${h}" src="/user_uploads/thumbnail/2/ab/x${name.length}/${name}/840x560.webp">`;
    return `<div class="message_inline_image"><a href="${url}" title="${name}">${img}</a></div>`;
}
const named = (...names) =>
    `<p>${names.map((n) => `<a href="${upload_url(n)}">${n}</a>`).join("<br>\n")}</p>`;
let next_id = 200;
function arrive(sender_id, content) {
    const m = {
        id: next_id,
        type: "stream",
        stream_id: 10,
        subject: "screenshots",
        sender_id,
        timestamp: Math.floor(Date.now() / 1000),
        content,
        reactions: [],
        flags: ["read"],
    };
    next_id += 1;
    routing.addMsg(cache, m);
    feed.appendMessage(lane, m);
    return m;
}
const turnOf = (id) => lane.querySelector(`[data-mid="${id}"]`).closest("section");
// What one message shows: its parts, in a turn that may hold others.
const wordsOf = (id) =>
    [...lane.querySelectorAll(`[data-mid="${id}"]`)].map((x) => x.textContent).join(" ");
const boxesOf = (id) =>
    [...turnOf(id).querySelectorAll(`[data-mid="${id}"] a[href]`)].filter((a) =>
        a.querySelector("img"),
    );
const shapeOf = (box) => box.style.getPropertyValue("--ar");
async function settle() {
    for (let i = 0; i < 20; i += 1) {
        await Promise.resolve();
    }
}
hover.buildOver();
feed.drawLane();

run_test(
    "a picture's box is the server's size before it loads, and it loads only when near",
    async () => {
        const m = arrive(IAGO, named("login.png") + preview("login.png", 1440, 900));
        await settle();
        const [box] = boxesOf(m.id);
        // Its shape is the picture's own, and it is never shown wider than it is.
        assert.equal(shapeOf(box), "1.6");
        assert.equal(box.style.getPropertyValue("--w"), "1440px");
        // Nothing has loaded: the box waits to come near.
        assert.ok(watched(box));
        assert.equal(box.querySelector("img").getAttribute("src"), null);
        comeNear(box);
        assert.equal(
            box.querySelector("img").getAttribute("src"),
            "/user_uploads/thumbnail/2/ab/x9/login.png/840x560.webp",
        );
        // It is pressed to open the original.
        assert.equal(box.getAttribute("href"), upload_url("login.png"));
        // The file's name is the picture's label, not words of the message.
        assert.equal(box.getAttribute("aria-label"), "login.png");
        assert.doesNotMatch(wordsOf(m.id), /login\.png/);
    },
);

run_test(
    "a full-page screenshot shows its top; a picture of unknown size keeps a box of one size",
    async () => {
        const m = arrive(AGENT, "<p>The whole page:</p>" + preview("page.png", 1280, 6000));
        const n = arrive(
            IAGO,
            '<p>from the web</p><div class="message_inline_image"><a href="https://example.com/cat.png" title="cat"><img src="https://example.com/cat.png"></a></div>',
        );
        await settle();
        const [tall] = boxesOf(m.id);
        assert.equal(shapeOf(tall), String(Math.round((1 / 3) * 1e4) / 1e4));
        assert.equal(tall.dataset.crop, "tall");
        const [web] = boxesOf(n.id);
        assert.equal(shapeOf(web), "");
        assert.equal(web.dataset.size, "unknown");
        // A link that names a picture in other words stays in the words.
        assert.match(wordsOf(n.id), /from the web/);
    },
);

run_test(
    "pictures sent together are one group, and a Markdown image leaves the words",
    async () => {
        const m = arrive(
            IAGO,
            `<p>before and after<br>\n<a href="${upload_url("a.png")}">a.png</a><br>\n<a href="${upload_url("b.png")}">b.png</a></p>` +
                preview("a.png", 800, 600) +
                preview("b.png", 800, 600),
        );
        const n = arrive(
            IAGO,
            `<p>see <img alt="the chart" class="inline-image" data-original-dimensions="1200x800" data-original-src="${upload_url("chart.png")}" src="/user_uploads/thumbnail/2/ab/x9/chart.png/840x560.webp"> here</p>`,
        );
        await settle();
        const groups = turnOf(m.id).querySelectorAll(`[data-mid="${m.id}"][data-part="pic"]`);
        assert.equal(groups.length, 1);
        assert.equal(boxesOf(m.id).length, 2);
        // The words stay in their bubble, without the names.
        assert.match(wordsOf(m.id), /before and after/);
        assert.doesNotMatch(wordsOf(m.id), /a\.png|b\.png/);
        const bubble = turnOf(n.id).querySelector(`[data-mid="${n.id}"] .b`);
        assert.equal(bubble.querySelector("img"), null);
        assert.match(bubble.textContent, /see\s+here/);
        const [chart] = boxesOf(n.id);
        assert.equal(chart.getAttribute("aria-label"), "the chart");
        assert.equal(chart.getAttribute("href"), upload_url("chart.png"));
    },
);

run_test(
    "the thumbnail replacing its placeholder keeps the box, and never shows the spinner",
    async () => {
        const name = "late.png";
        const m = arrive(IAGO, named(name) + preview(name, 1000, 500, {placeholder: true}));
        await settle();
        let [box] = boxesOf(m.id);
        assert.equal(shapeOf(box), "2");
        comeNear(box);
        assert.equal(box.querySelector("img").getAttribute("src"), null);
        // The server's thumbnail is ready: its rendering-only update.
        events.onUpdate({
            type: "update_message",
            message_id: m.id,
            rendering_only: true,
            rendered_content: named(name) + preview(name, 1000, 500),
        });
        await settle();
        [box] = boxesOf(m.id);
        assert.equal(shapeOf(box), "2");
        comeNear(box);
        assert.match(box.querySelector("img").getAttribute("src"), /840x560\.webp$/);
    },
);

run_test("a picture that cannot load says so in its own box", async () => {
    const m = arrive(IAGO, named("gone.png") + preview("gone.png", 640, 480));
    await settle();
    const [box] = boxesOf(m.id);
    comeNear(box);
    box.querySelector("img").dispatchEvent(new dom.window.Event("error"));
    assert.equal(shapeOf(box), String(Math.round((640 / 480) * 1e4) / 1e4));
    assert.match(box.textContent, /Image not available/);
    assert.match(box.getAttribute("aria-label"), /not available/);
});

run_test(
    "my picture shows at once at its size, and the server's copy takes its place without a gap",
    async () => {
        // The picture I attached, as this browser read it, and its address.
        const url = upload_url("mine.png");
        media.rememberLocal(url, {w: 1200, h: 900, preview: "blob:mine"});
        beta_state.state.atts = [{id: 1, name: "mine.png", url, failed: false}];
        field.textContent = "the new header";
        send.send({at: 0});
        await settle();
        const echo = cache.msgs.at(-1);
        let [box] = boxesOf(echo.id);
        assert.equal(shapeOf(box), "1.3333");
        comeNear(box);
        assert.equal(box.querySelector("img").getAttribute("src"), "blob:mine");
        assert.match(wordsOf(echo.id), /the new header/);
        assert.doesNotMatch(wordsOf(echo.id), /mine\.png/);

        // The server's copy: my row takes its id, and the picture is drawn
        // again from the thumbnail, starting from what is on screen.
        const server = {
            id: 900,
            type: "stream",
            stream_id: 10,
            subject: "screenshots",
            sender_id: ME,
            timestamp: echo.timestamp,
            content:
                `<p>the new header<br>\n<a href="${url}">mine.png</a></p>` +
                preview("mine.png", 1200, 900),
            reactions: [],
        };
        events.onMessage({
            type: "message",
            message: server,
            local_message_id: echo.id,
            flags: ["read"],
        });
        await settle();
        [box] = boxesOf(900);
        assert.equal(shapeOf(box), "1.3333");
        comeNear(box);
        const img = box.querySelector("img");
        assert.equal(img.getAttribute("src"), "blob:mine");
        // The thumbnail has decoded beside it: it takes the picture's place.
        const thumb = decoding.find((d) => d.src.endsWith("/mine.png/840x560.webp"));
        assert.ok(thumb);
        thumb.resolve();
        await settle();
        assert.equal(
            img.getAttribute("src"),
            "/user_uploads/thumbnail/2/ab/x8/mine.png/840x560.webp",
        );
    },
);

run_test("the field's chip counts what has gone up, then names the file", async () => {
    beta_state.state.atts = [];
    composer.upload(new File(["png"], "shot.png", {type: "image/png"}));
    await settle();
    const chip = () => find(".cz .atts").textContent;
    assert.match(chip(), /shot\.png/);
    assert.match(chip(), /0%/);
    const xhr = uploads.at(-1);
    assert.equal(xhr.url, "/json/user_uploads");
    xhr.upload.onprogress({lengthComputable: true, loaded: 42, total: 100});
    assert.match(chip(), /42%/);
    xhr.status = 200;
    xhr.responseText = JSON.stringify({
        result: "success",
        url: upload_url("shot.png"),
        filename: "shot.png",
    });
    xhr.onload();
    await settle();
    assert.doesNotMatch(chip(), /%/);
    assert.equal(beta_state.state.atts[0].url, upload_url("shot.png"));

    // A refused upload says so on its chip.
    composer.upload(new File(["png"], "big.png", {type: "image/png"}));
    await settle();
    const refused = uploads.at(-1);
    refused.status = 400;
    refused.responseText = JSON.stringify({result: "error", msg: "File too large"});
    refused.onload();
    await settle();
    assert.match(chip(), /big\.png \(failed\)/);
    assert.deepEqual(said.slice(-1), ["File too large"]);
    beta_state.state.atts = [];
    composer.drawAtts();
});

run_test("a message that is only a picture can be stood on, and carries its controls", async () => {
    const m = arrive(IAGO, named("alone.png") + preview("alone.png", 800, 500));
    await settle();
    // No bubble: the picture is the message's stop in the Tab order ...
    assert.equal(turnOf(m.id).querySelector(`[data-mid="${m.id}"] .b`), null);
    // Tab chooses the message in the Tab order as the key goes down: the
    // newest, this one.
    dom.press("Tab");
    lane.querySelector("[data-door]").focus();
    const door = document.activeElement;
    assert.equal(door.dataset.mid, String(m.id));
    assert.ok(door.querySelector("a[href]"));
    // ... and standing on it brings up React, Reply in thread, Copy text and More.
    const controls = [...door.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"));
    assert.deepEqual(controls, ["React", "Reply in thread", "Copy text", "More"]);
});
