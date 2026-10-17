"use strict";

// The interim beta's cards in the nav (web/src/armada/beta/channel_view.js,
// a chunk), with a real DOM and a stand-in server. The pointer resting on a
// channel's row brings up a card beside the nav: what the channel is for,
// how many are in it and its repository, with "+" for a new topic. On a
// topic's row: who has spoken there, the latest word and its run. A card
// waits as a tooltip does, travels at once between rows while it is up, and
// lets the pointer cross to it; a press on the row takes it away. From the
// keyboard a row's focus brings it up, Tab goes into it and on to the next
// row, and Esc gives the row its focus back. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<nav>
  <div class="top"><button class="wk-name">Armada</button><button class="c sq new" aria-label="New"></button></div>
  <div class="fixed"></div>
  <div class="scroll"></div>
  <button class="row me">Iago</button>
</nav>
<main>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div class="feed" id="pane"><div class="clip"><div class="lane" id="col"></div></div></div>
  <div class="pane" data-pane="Home" hidden><div class="lane" id="home"></div></div>
  <div class="dock"><div class="cz"><div class="sh">
    <button class="k to"></button>
    <span class="txw"><div class="tx" contenteditable="true"></div><span class="ph"></span></span>
    <button class="k mode"></button>
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
set_global("innerHeight", 900);
set_global("requestAnimationFrame", (f) => setTimeout(f, 16));
set_global(
    "ResizeObserver",
    class {
        observe() {}
    },
);
set_global(
    "BroadcastChannel",
    class {
        postMessage() {}
    },
);
const noop = () => {};
const sounds = [];
set_global("M", {
    // The tooltip's delay and linger, as the kit's tokens give them.
    T: {"tip-delay": 500, "tip-linger": 300, "pane-rise": 4},
    release: noop,
    flip: noop,
    fade: noop,
    appear: noop,
    current: () => [{}, {}],
    halt: noop,
    refuse: noop,
    say: noop,
    focusKbd(element) {
        element.focus();
    },
    leave(element) {
        element.inert = true;
        element.remove();
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
set_global("mk", (name) => `<svg class="mk ${name}"></svg>`);
set_global("face", (_spec, cls) => `<i class="av ${cls || ""}"></i>`);

const ME = 11;
const CORDELIA = 8;
const HAMLET = 12;
const OTHELLO = 13;
const MIHIR = 15;
const ONIX = 25;
const NLS = 10;
const OPS = 33;

// The stand-in server: each request is kept, and answered from the table.
const requests = [];
let answers = [];
set_global("fetch", async (url, init = {}) => {
    const method = init.method ?? "GET";
    const [path, query] = url.split("?");
    requests.push({
        method,
        url: path,
        body: Object.fromEntries(init.body ?? new URLSearchParams(query)),
    });
    const answer = answers.find(([m, pattern]) => m === method && pattern.test(path));
    assert.ok(answer, `no stand-in answer for ${method} ${path}`);
    const reply = await answer[2](path, init);
    return {ok: (reply.status ?? 200) < 400, status: reply.status ?? 200, json: async () => reply};
});
const success = (data = {}) => ({result: "success", msg: "", ...data});
const MEMBERS = {[NLS]: [ME, CORDELIA, HAMLET, OTHELLO, MIHIR, ONIX], [OPS]: [ME, HAMLET, OTHELLO]};
const at = (seconds_ago) => Math.floor(Date.now() / 1000) - seconds_ago;
function serve() {
    requests.length = 0;
    answers = [
        [
            "GET",
            /^\/json\/streams\/\d+\/members$/,
            (path) => success({subscribers: MEMBERS[Number(path.split("/")[3])]}),
        ],
        [
            "GET",
            /^\/json\/messages$/,
            () =>
                success({
                    messages: [
                        {id: 1, sender_id: CORDELIA, timestamp: at(900)},
                        {id: 2, sender_id: ONIX, timestamp: at(800)},
                        {id: 3, sender_id: HAMLET, timestamp: at(720)},
                    ],
                }),
        ],
        [
            "GET",
            /^\/json\/armada\/topic-context$/,
            () =>
                success({
                    armada_topic_context: {
                        repository_url: "https://github.com/runix/nextlat-scaling",
                    },
                }),
        ],
        [
            "GET",
            /^\/json\/users\/me\/\d+\/topics$/,
            () =>
                success({
                    topics: [
                        {name: "lr-sweep", max_id: 9},
                        {name: "eval-harness", max_id: 8},
                    ],
                }),
        ],
        [
            "GET",
            /^\/json\/armada\/workspace-topics$/,
            () => success({armada_workspace_topics: [{topic: "lr-sweep"}]}),
        ],
        ["GET", /^\/json\/armada\/runs$/, () => success({armada_runs: []})],
        ["POST", /^\/json\/messages\/flags$/, () => success()],
    ];
}

const find = (selector) => document.querySelector(selector);
const field = find(".tx");
const nav_scroll = find("nav .scroll");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane: find("#col"),
    nav: find("nav"),
    navFixed: find("nav .fixed"),
    navScroll: nav_scroll,
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
    toBtn: find(".k.to"),
    modeBtn: find(".k.mode"),
    panes: {Chat: find("#pane"), Home: find('[data-pane="Home"]')},
});
// The header and the column draw what a place says of itself, which these
// tests do not read; a repository's owner/name is the address's last two
// parts.
mock_esm("../../src/armada/beta/header.js", {
    drawHeader: noop,
    repoPath: (url, fallback) => (url ? url.split("/").slice(-2).join("/") : fallback),
});
mock_esm("../../src/armada/beta/column.js", {drawSide: noop});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const nav = zrequire("armada/beta/nav.js");
const channels = zrequire("armada/beta/channels.js");
const chunks = zrequire("armada/beta/chunks.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Iago";
for (const [user_id, name, bot] of [
    [ME, "Iago"],
    [CORDELIA, "Cordelia"],
    [HAMLET, "King Hamlet"],
    [OTHELLO, "Othello"],
    [MIHIR, "Mihir Kumar"],
    [ONIX, "Onix", true],
]) {
    beta_state.people.set(user_id, {id: user_id, name, bot: Boolean(bot), active: true});
}
beta_state.armadaBots.add(ONIX);
beta_state.streams.set(NLS, {
    stream_id: NLS,
    name: "nextlat-scaling",
    description: "Scaling laws for next-latent prediction.",
});
beta_state.streams.set(OPS, {
    stream_id: OPS,
    name: "ops",
    description: "Deploys, alerts and who is on call.",
    invite_only: true,
});
beta_state.repoStreams.add(NLS);
beta_state.expanded.add(NLS);
beta_state.topicsOf.set(NLS, [
    {name: "lr-sweep", max_id: 9},
    {name: "eval-harness", max_id: 8},
]);
beta_state.worktrees.set(NLS, new Set(["lr-sweep"]));
// The place in view, as the page always has one: nextlat-scaling > lr-sweep.
const place = routing.placeFromKey(routing.tkey(NLS, "lr-sweep"));
beta_state.state.place = place;
beta_state.state.P = routing.cacheOf(place);
nav.drawNav();
clock.tick(1);

// The chunk arrives as the page's idle prefetch brings it.
chunks.setChunkLoader("channels", async () => zrequire("armada/beta/channel_view.js"));

async function settle() {
    for (let i = 0; i < 200; i += 1) {
        await Promise.resolve();
    }
}
const row = (key) => [...nav_scroll.querySelectorAll(".row")].find((r) => r.dataset.k === key);
const card = () => find('[role="dialog"].ncard:not([inert])');
const fact = (name) => {
    const names = [...card().querySelectorAll("dt")].map((dt) => dt.textContent);
    const i = names.indexOf(name);
    return i === -1 ? null : card().querySelectorAll("dd")[i].textContent;
};
function rest_on(target) {
    target.dispatchEvent(new dom.window.MouseEvent("pointerover", {bubbles: true}));
}
function leave(target, to = null) {
    target.dispatchEvent(new dom.window.MouseEvent("pointerleave", {relatedTarget: to}));
}
function away() {
    leave(nav_scroll);
    clock.tick(400);
    if (card()) {
        leave(card());
        clock.tick(400);
    }
}
const asked = (url) => requests.filter((r) => r.url === url).length;

run_test("resting on a channel's row brings its card up after the tooltip's delay", async () => {
    serve();
    await chunks.chunk("channels");

    rest_on(row(`c:${NLS}`).querySelector(".lab"));
    clock.tick(400);
    assert.equal(card(), null);
    // Moving within the row does not start the wait again.
    rest_on(row(`c:${NLS}`));
    clock.tick(100);
    assert.equal(card().getAttribute("aria-label"), "nextlat-scaling");
    assert.equal(
        card().querySelector(".say").textContent,
        "Scaling laws for next-latent prediction.",
    );
    assert.ok(card().querySelector('[aria-label="New topic in nextlat-scaling"]'));

    // Its members were asked for while the pointer rested; its repository
    // from one of its worktrees, without git.
    await settle();
    assert.equal(fact("Members"), "6 members");
    assert.equal(fact("Repository"), "runix/nextlat-scaling");
    const context = requests.find((r) => r.url === "/json/armada/topic-context");
    assert.deepEqual(context.body, {
        stream: "nextlat-scaling",
        topic: "lr-sweep",
        include_git: "false",
    });

    // Someone joining is counted where the count stands.
    channels.onSubscription({
        type: "subscription",
        op: "peer_add",
        stream_ids: [NLS],
        user_ids: [99],
    });
    assert.equal(fact("Members"), "6 members");
    beta_state.people.set(99, {id: 99, name: "Ana", bot: false, active: true});
    channels.onSubscription({
        type: "subscription",
        op: "peer_add",
        stream_ids: [NLS],
        user_ids: [99],
    });
    assert.equal(fact("Members"), "7 members");
    away();
    assert.equal(card(), null);
});

run_test("while a card is up it travels at once; the pointer may cross to it", async () => {
    serve();
    rest_on(row(`c:${NLS}`));
    clock.tick(500);
    assert.equal(card().getAttribute("aria-label"), "nextlat-scaling");

    rest_on(row(`c:${OPS}`));
    assert.equal(card().getAttribute("aria-label"), "ops");
    await settle();
    assert.equal(fact("Members"), "3 members");
    // A channel with no repository says none.
    assert.equal(fact("Repository"), null);

    // Leaving the nav for the card keeps it; leaving the card takes it.
    leave(nav_scroll, card());
    clock.tick(100);
    card().dispatchEvent(new dom.window.MouseEvent("pointerenter"));
    clock.tick(1000);
    assert.equal(card().getAttribute("aria-label"), "ops");
    leave(card());
    clock.tick(300);
    assert.equal(card(), null);

    // Just after one goes, the next comes at once, as a tooltip's does.
    rest_on(row(`c:${NLS}`));
    assert.equal(card().getAttribute("aria-label"), "nextlat-scaling");
    away();
});

run_test("a press on a row takes its card away until the pointer leaves the row", () => {
    serve();
    rest_on(row(`c:${NLS}`));
    clock.tick(500);
    assert.ok(card());
    row(`c:${NLS}`).dispatchEvent(
        new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}),
    );
    assert.equal(card(), null);
    rest_on(row(`c:${NLS}`));
    clock.tick(1000);
    assert.equal(card(), null);

    row(`c:${NLS}`).dispatchEvent(
        new dom.window.MouseEvent("pointerout", {bubbles: true, relatedTarget: row(`c:${OPS}`)}),
    );
    rest_on(row(`c:${OPS}`));
    clock.tick(500);
    assert.equal(card().getAttribute("aria-label"), "ops");
    away();
});

run_test("a topic's card says who has spoken there, the latest word and its run", async () => {
    serve();
    const key = routing.tkey(NLS, "eval-harness");
    rest_on(row(key));
    clock.tick(500);
    assert.equal(card().getAttribute("aria-label"), "eval-harness");
    // What is not known yet keeps its place.
    assert.equal(fact("People"), "…");
    assert.equal(fact("Latest"), "…");
    await settle();
    // People latest first, Onix after them; a name is its first word.
    assert.equal(fact("People"), "King, Cordelia and 1 other");
    assert.equal(fact("Latest"), "King, 12m ago");
    assert.equal(fact("Run"), null);
    const narrow = JSON.parse(requests.find((r) => r.url === "/json/messages").body.narrow);
    assert.deepEqual(narrow, [
        {operator: "channel", operand: NLS},
        {operator: "topic", operand: "eval-harness"},
    ]);

    // Its run, from the nav's mark; asked again only after a minute.
    beta_state.marks.set(key, "needs");
    rest_on(row(`c:${NLS}`));
    rest_on(row(key));
    assert.equal(fact("Run"), "Onix asks you");
    assert.equal(fact("Latest"), "King, 12m ago");
    assert.equal(asked("/json/messages"), 1);
    beta_state.marks.delete(key);
    away();
});

run_test("+ on a channel's card names a new topic, and Return opens it", async () => {
    serve();
    rest_on(row(`c:${NLS}`));
    clock.tick(500);
    field.focus();
    const plus = card().querySelector('[aria-label="New topic in nextlat-scaling"]');
    plus.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    const name = card().querySelector('input[aria-label="New topic in nextlat-scaling"]');
    assert.equal(document.activeElement, name);
    // The pointer leaving does not take a card that holds the caret.
    away();
    assert.ok(card());

    // No name is refused, and nothing opens.
    dom.press("Enter");
    assert.deepEqual(sounds.splice(0), ["nope"]);
    assert.equal(beta_state.state.place.topic, "lr-sweep");

    dom.type(name, "  Muon   ablation ");
    dom.press("Enter");
    assert.equal(card(), null);
    assert.equal(beta_state.state.place.key, routing.tkey(NLS, "muon ablation"));
    // The topic keeps the case it was written in.
    assert.equal(beta_state.state.place.topic, "Muon ablation");
    await settle();

    // A name that is taken opens that topic, as it is written there.
    rest_on(row(`c:${NLS}`));
    clock.tick(500);
    card()
        .querySelector("[data-card-new]")
        .dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    dom.type(card().querySelector("input"), "LR-SWEEP");
    dom.press("Enter");
    assert.equal(beta_state.state.place.topic, "lr-sweep");
    await settle();
});

run_test("from the keyboard: a row's focus brings its card, Tab goes into it and on", () => {
    serve();
    rest_on(row(`c:${NLS}`));
    row(`c:${NLS}`).focus();
    clock.tick(500);
    assert.equal(card().getAttribute("aria-label"), "nextlat-scaling");

    dom.press("Tab");
    const plus = card().querySelector("[data-card-new]");
    assert.equal(document.activeElement, plus);
    // Shift-Tab goes back to the row, Tab into the card again.
    dom.press("Tab", {shiftKey: true});
    assert.equal(document.activeElement, row(`c:${NLS}`));
    dom.press("Tab");
    assert.equal(document.activeElement, plus);
    // Past its last control: the next row of the nav.
    dom.press("Tab");
    assert.equal(document.activeElement, row(routing.tkey(NLS, "lr-sweep")));
    clock.tick(500);
    assert.equal(card().getAttribute("aria-label"), "lr-sweep");

    // Esc in the card gives the row its focus back.
    row(`c:${NLS}`).focus();
    clock.tick(500);
    dom.press("Tab");
    dom.press("Escape");
    assert.equal(card(), null);
    assert.equal(document.activeElement, row(`c:${NLS}`));
    field.focus();
    away();
});
