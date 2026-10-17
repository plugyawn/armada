"use strict";

// The interim beta's New channel (web/src/armada/beta/channels.js), with a
// real DOM and a stand-in server. The nav's "+" opens a small raised
// surface: a name, a description, public or private, and Create. Create
// is Zulip's POST channels/create with me as its one subscriber; a name
// that is taken, or a permission I lack, is said in plain words in the
// surface, which stays open with what I typed. The new channel joins the
// nav when the server's `subscription` event says I am in it, and its
// first place opens then, whichever of the answer and the event comes
// first. A channel I leave leaves the nav. Esc and a press outside close
// the surface and give focus back to where it was; Tab goes round its
// fields; Return creates. Under the form, the surface lists the public
// channels I am not in, asked for (GET streams) only when it opens. Join
// is one press: the row says Joining at once and Open once I am in, and
// keeps its place; the nav takes the channel when the event comes; Open
// opens its first place. New arrives with the channels chunk; a press
// that comes before it opens New when it arrives. A channel's members are asked for once and kept
// by the same event as people join and leave. Deleted with
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
const said = [];
set_global("M", {
    T: {},
    release: noop,
    // Where a row is in its spring: at rest.
    current: () => [{}, {}],
    halt: noop,
    refuse: noop,
    appear: noop,
    // What the page tells the person beside a control, kept for the tests.
    say(_target, text) {
        said.push(text);
    },
    focusKbd(element) {
        element.focus();
    },
    leave(element) {
        element.remove();
    },
});
// The sounds the page plays, kept for the tests to read.
const sounds = [];
set_global("S", {
    play(name) {
        sounds.push(name);
    },
});
set_global("host", noop);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);

const ME = 3;

// The stand-in server: each request is kept, and answered from the table
// a test sets. An answer may be held back until the test lets it go.
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
    const reply = await answer[2]();
    return {ok: (reply.status ?? 200) < 400, status: reply.status ?? 200, json: async () => reply};
});
const success = (data = {}) => ({result: "success", msg: "", ...data});
let create_answer;
let join_answer;
let streams_answer;
function serve() {
    requests.length = 0;
    streams_answer = () => success({streams: []});
    answers = [
        ["POST", /^\/json\/channels\/create$/, () => create_answer()],
        ["GET", /^\/json\/streams$/, () => streams_answer()],
        ["POST", /^\/json\/users\/me\/subscriptions$/, () => join_answer()],
        [
            "GET",
            /^\/json\/users\/me\/\d+\/topics$/,
            () => success({topics: [{name: "channel events", max_id: 900}]}),
        ],
        ["GET", /^\/json\/messages$/, () => success({messages: [], found_oldest: true})],
    ];
}
// An answer the test lets go of when it chooses (Create's, unless it says).
function held(
    reply,
    answer_with = (answer) => {
        create_answer = answer;
    },
) {
    let release;
    const waiting = new Promise((resolve) => {
        release = resolve;
    });
    answer_with(async () => {
        await waiting;
        return reply;
    });
    return release;
}
const created = () =>
    requests.filter((r) => r.method === "POST" && r.url === "/json/channels/create");

const find = (selector) => document.querySelector(selector);
const field = find(".tx");
const plus = find("nav .new");
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
// tests do not read.
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop, repoPath: noop});
mock_esm("../../src/armada/beta/column.js", {drawSide: noop});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const channels = zrequire("armada/beta/channels.js");
const chunks = zrequire("armada/beta/chunks.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Cordelia";
beta_state.streams.set(10, {stream_id: 10, name: "general"});
// The place in view, as the page always has one: #general > chat.
const place = {
    key: routing.tkey(10, "chat"),
    kind: "topic",
    stream_id: 10,
    stream: "general",
    topic: "chat",
    repo: false,
};
beta_state.state.place = place;
beta_state.state.P = routing.cacheOf(place);
beta_state.state.P.loaded = true;
zrequire("armada/beta/nav.js").drawNav();
channels.applyChannels({repository_stream_ids: [30]});
channels.wireChannels();
// New arrives with the channels chunk (channel_new.js), when the test lets
// it.
let chunk_arrives;
const chunk_here = new Promise((resolve) => {
    chunk_arrives = resolve;
});
chunks.setChunkLoader("channels", async () => {
    await chunk_here;
    return zrequire("armada/beta/channel_view.js");
});
// A press is told from its own click by the time it went down, which the
// fake clock starts at 0.
clock.tick(1);

// Every promise the page waits on resolves through promises alone.
async function settle() {
    for (let i = 0; i < 200; i += 1) {
        await Promise.resolve();
    }
}
// A pointer press: the control answers on the press; the click that
// follows is its own.
function press_on(target) {
    target.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    dom.click(target);
}
const surface = () => find('[role="dialog"][aria-label="New channel"]');
const name_field = () => surface().querySelector('input[aria-label="Name"]');
const description_field = () => surface().querySelector('input[aria-label="Description"]');
const choice = (label) =>
    [...surface().querySelectorAll('[role="radio"]')].find((b) => b.textContent === label);
const chosen = () => surface().querySelector('[role="radio"][aria-checked="true"]').textContent;
const create_button = () =>
    [...surface().querySelectorAll("button")].find((b) => b.textContent.startsWith("Creat"));
const caption = () => surface().querySelector("[aria-live]").textContent;
const nav_rows = () => [...nav_scroll.querySelectorAll(".row")].map((r) => r.textContent.trim());
function open_new() {
    field.focus();
    press_on(plus);
    return surface();
}
function close_new() {
    dom.press("Escape");
}
const PUBLIC = "Anyone here can find it and join.";
const PRIVATE = "Only the people in it can see it.";

run_test("a press on the + before New has arrived opens it when it does", async () => {
    serve();
    field.focus();
    press_on(plus);
    assert.equal(surface(), null);
    chunk_arrives();
    await settle();
    assert.equal(document.activeElement, name_field());
    close_new();
    assert.equal(surface(), null);
});

run_test("the + opens New channel with its name field ready, and sends nothing", () => {
    serve();
    open_new();

    assert.equal(document.activeElement, name_field());
    assert.equal(plus.getAttribute("aria-expanded"), "true");
    assert.equal(chosen(), "Public");
    assert.equal(caption(), PUBLIC);
    assert.deepEqual(created(), []);
    close_new();
});

run_test("Return with no name says what is missing, and sends nothing", () => {
    serve();
    open_new();
    dom.type(name_field(), "   ");

    dom.press("Enter");
    assert.equal(caption(), "Give the channel a name.");
    assert.deepEqual(sounds.splice(0), ["nope"]);
    assert.deepEqual(created(), []);

    // Typing again gives the caption back to what it says of the choice.
    dom.type(name_field(), "o");
    assert.equal(caption(), PUBLIC);
    close_new();
});

run_test("Tab goes round the fields, the arrows choose who can see it", () => {
    serve();
    open_new();

    dom.press("Tab");
    assert.equal(document.activeElement, description_field());
    dom.press("Tab");
    assert.equal(document.activeElement, choice("Public"));
    dom.press("ArrowRight");
    assert.equal(document.activeElement, choice("Private"));
    assert.equal(chosen(), "Private");
    assert.equal(caption(), PRIVATE);
    dom.press("Tab");
    assert.equal(document.activeElement, create_button());
    dom.press("Tab");
    assert.equal(document.activeElement, name_field());
    dom.press("Tab", {shiftKey: true});
    assert.equal(document.activeElement, create_button());
    dom.press("Tab", {shiftKey: true});
    assert.equal(document.activeElement, choice("Private"));

    // A press chooses too.
    press_on(choice("Public"));
    assert.equal(chosen(), "Public");
    assert.equal(caption(), PUBLIC);
    assert.deepEqual(created(), []);
    close_new();
});

run_test("Create makes the channel; it joins the nav and its first place opens", async () => {
    serve();
    const release = held(success({id: 20}));
    open_new();
    dom.type(name_field(), "ops");
    dom.type(description_field(), "Deploys and alerts");
    press_on(choice("Private"));

    dom.press("Enter");
    assert.deepEqual(
        created().map((r) => r.body),
        [
            {
                name: "ops",
                description: "Deploys and alerts",
                invite_only: "true",
                subscribers: JSON.stringify([ME]),
            },
        ],
    );
    // While the server works the verb says so, and a second Return sends
    // nothing more.
    assert.equal(create_button().firstElementChild.textContent, "Creating");
    dom.press("Enter");
    assert.equal(created().length, 1);

    release();
    await settle();
    assert.equal(surface(), null);
    assert.equal(document.activeElement, field);
    assert.equal(plus.getAttribute("aria-expanded"), "false");
    // The channel is mine once the server says so.
    assert.deepEqual(nav_rows(), ["general"]);
    assert.equal(beta_state.state.place, place);

    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 20, name: "ops", invite_only: true}],
    });
    await settle();
    assert.equal(beta_state.state.place.key, routing.tkey(20, "channel events"));
    assert.equal(dom.window.location.hash, "#c/20/channel%20events");
    assert.deepEqual(nav_rows(), ["general", "ops", "channel events"]);
});

run_test("the event may come before the answer: the place opens once both are in", async () => {
    serve();
    const release = held(success({id: 21}));
    open_new();
    dom.type(name_field(), "design");
    dom.press("Enter");

    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 21, name: "design"}],
    });
    await settle();
    assert.ok(nav_rows().includes("design"));
    assert.equal(beta_state.state.place.key, routing.tkey(20, "channel events"));

    release();
    await settle();
    assert.equal(beta_state.state.place.key, routing.tkey(21, "channel events"));
});

run_test("a name that is taken is said in plain words, and what I typed stays", async () => {
    serve();
    create_answer = () => ({
        status: 409,
        result: "error",
        code: "CHANNEL_ALREADY_EXISTS",
        msg: "Channel 'general' already exists",
    });
    open_new();
    dom.type(name_field(), "general");
    dom.type(description_field(), "Everything else");

    dom.press("Enter");
    await settle();
    assert.equal(caption(), "A channel named “general” already exists.");
    assert.deepEqual(sounds.splice(0), ["nope"]);
    assert.equal(name_field().value, "general");
    assert.equal(description_field().value, "Everything else");
    assert.equal(document.activeElement, name_field());
    assert.equal(create_button().firstElementChild.textContent, "Create");
    close_new();
});

run_test("a permission I lack, or any other refusal, is said in plain words", async () => {
    serve();
    create_answer = () => ({status: 400, result: "error", msg: "Insufficient permission"});
    open_new();
    dom.type(name_field(), "announcements");
    dom.press("Enter");
    await settle();
    assert.equal(caption(), "You are not allowed to create public channels.");

    press_on(choice("Private"));
    assert.equal(caption(), PRIVATE);
    dom.press("Enter");
    await settle();
    assert.equal(caption(), "You are not allowed to create private channels.");

    create_answer = () => ({status: 400, result: "error", msg: "Not allowed for guest users"});
    press_on(create_button());
    await settle();
    assert.equal(caption(), "Guests cannot create channels.");

    create_answer = () => ({
        status: 400,
        result: "error",
        msg: "Invalid character in channel name, at position 4.",
    });
    dom.press("Enter");
    await settle();
    assert.equal(caption(), "Not created: Invalid character in channel name, at position 4.");
    assert.ok(surface());
    sounds.length = 0;
    close_new();
});

run_test("Esc and a press outside close it, and focus goes back to where it was", () => {
    serve();
    open_new();
    dom.type(name_field(), "half a name");
    dom.press("Escape");
    assert.equal(surface(), null);
    assert.equal(document.activeElement, field);

    // Opened from the keyboard, focus goes back to the "+".
    plus.focus();
    dom.click(plus);
    assert.ok(surface());
    assert.equal(name_field().value, "");
    dom.press("Escape");
    assert.equal(surface(), null);
    assert.equal(document.activeElement, plus);

    // A press outside closes it; a press on the "+" closes it too.
    open_new();
    find("#col").dispatchEvent(
        new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}),
    );
    assert.equal(surface(), null);
    assert.equal(document.activeElement, field);
    open_new();
    press_on(plus);
    assert.equal(surface(), null);
    assert.equal(document.activeElement, field);

    // Focus that goes elsewhere (Jump to, opened with its key) takes the
    // surface with it.
    open_new();
    find(".disc").focus();
    assert.equal(surface(), null);
    assert.deepEqual(created(), []);
});

run_test("the nav follows the channels I join and leave", () => {
    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 30, name: "armada"}],
    });
    assert.ok(nav_rows().includes("armada"));
    // A repository is a repository from the moment I am in it.
    assert.equal(routing.placeFromKey(routing.tkey(30, "main")).repo, true);
    assert.equal(routing.placeFromKey(routing.tkey(21, "main")).repo, false);

    channels.onSubscription({type: "subscription", op: "remove", subscriptions: [{stream_id: 21}]});
    assert.ok(!nav_rows().includes("design"));
    channels.onSubscription({type: "subscription", op: "remove", subscriptions: [{stream_id: 30}]});
    assert.ok(!nav_rows().includes("armada"));
    assert.equal(routing.placeFromKey(routing.tkey(30, "main")), null);

    // What else the event says (someone else joining) changes nothing here.
    const before = nav_rows();
    channels.onSubscription({
        type: "subscription",
        op: "peer_add",
        stream_ids: [10],
        user_ids: [5],
    });
    assert.deepEqual(nav_rows(), before);
});

run_test("a channel's members are asked for once, and kept as people join and leave", async () => {
    serve();
    let release_members;
    answers.push([
        "GET",
        /^\/json\/streams\/10\/members$/,
        () =>
            new Promise((resolve) => {
                release_members = () => resolve(success({subscribers: [ME, 5]}));
            }),
    ]);
    const members_of = (sid) => {
        const known = beta_state.members.get(sid);
        return known instanceof Set ? [...known].toSorted((a, b) => a - b) : known;
    };
    const asked = () => requests.filter((r) => r.url === "/json/streams/10/members").length;
    beta_state.members.delete(10);

    const first = channels.loadMembers(10);
    const second = channels.loadMembers(10);
    assert.equal(asked(), 1);
    // Someone joins and someone leaves while the server is asked: the
    // answer may be older than either, so both are applied to it.
    channels.onSubscription({
        type: "subscription",
        op: "peer_add",
        stream_ids: [10],
        user_ids: [9],
    });
    channels.onSubscription({
        type: "subscription",
        op: "peer_remove",
        stream_ids: [10],
        user_ids: [5],
    });
    release_members();
    await settle();
    assert.equal(await first, beta_state.members.get(10));
    assert.equal(await second, beta_state.members.get(10));
    assert.deepEqual(members_of(10), [ME, 9]);

    // Once known, the event keeps them; nothing is asked again.
    channels.onSubscription({
        type: "subscription",
        op: "peer_add",
        stream_ids: [10, 77],
        user_ids: [11, 12],
    });
    channels.onSubscription({
        type: "subscription",
        op: "peer_remove",
        stream_ids: [10],
        user_ids: [9],
    });
    assert.deepEqual(members_of(10), [ME, 11, 12]);
    await channels.loadMembers(10);
    assert.equal(asked(), 1);
    // A channel whose members were never asked for is not guessed at.
    assert.equal(beta_state.members.get(77), undefined);

    // A channel I join comes with its members; one I leave forgets them.
    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 31, name: "ops", subscribers: [ME, 4]}],
    });
    assert.deepEqual(members_of(31), [ME, 4]);
    assert.equal(beta_state.streams.get(31).subscribers, undefined);
    channels.onSubscription({type: "subscription", op: "remove", subscriptions: [{stream_id: 31}]});
    assert.equal(beta_state.members.get(31), undefined);
});

// The public channels the server knows: #general and #ops are mine; it
// would never send a private one I cannot see or an archived one, and if
// it did they would not be offered.
const PUBLIC_CHANNELS = [
    {stream_id: 10, name: "general", invite_only: false},
    {stream_id: 20, name: "ops", invite_only: false},
    {stream_id: 42, name: "releases", description: "What ships, and when", invite_only: false},
    {stream_id: 41, name: "deploys", description: "", invite_only: false},
    {stream_id: 43, name: "support", invite_only: false},
    {stream_id: 44, name: "secret", invite_only: true},
    {stream_id: 45, name: "old", invite_only: false, is_archived: true},
];
const the_list = () => surface().querySelector('[aria-label="Channels to join"]');
const listed = () =>
    [...the_list().querySelectorAll("button")].map((b) => b.getAttribute("aria-label"));
const join_button = (name) =>
    [...the_list().querySelectorAll("button")].find((b) =>
        b.getAttribute("aria-label").includes(name),
    );
const verb_of = (button) => button.firstElementChild.textContent;
const asked_streams = () => requests.filter((r) => r.method === "GET" && r.url === "/json/streams");
const joins = () =>
    requests
        .filter((r) => r.method === "POST" && r.url === "/json/users/me/subscriptions")
        .map((r) => r.body);

run_test("it lists the public channels I am not in, asked for when it opens", async () => {
    serve();
    streams_answer = () => success({streams: PUBLIC_CHANNELS});
    assert.deepEqual(asked_streams(), []);
    open_new();
    assert.equal(the_list().textContent, "Loading");

    await settle();
    assert.deepEqual(listed(), ["Join deploys", "Join releases", "Join support"]);
    assert.deepEqual(
        asked_streams().map((r) => r.body),
        [{include_public: "true", include_subscribed: "false", exclude_archived: "true"}],
    );
    // The form keeps the caret.
    assert.equal(document.activeElement, name_field());

    // Asked again each time it opens.
    close_new();
    open_new();
    await settle();
    assert.equal(asked_streams().length, 2);
    close_new();
});

run_test("Join is one press; the row keeps its place; Open opens the channel", async () => {
    serve();
    streams_answer = () => success({streams: PUBLIC_CHANNELS});
    const release = held(success({subscribed: {[ME]: ["releases"]}}), (answer) => {
        join_answer = answer;
    });
    open_new();
    await settle();
    const rows = [...the_list().children];
    const releases = join_button("releases");

    press_on(releases);
    assert.equal(verb_of(releases), "Joining");
    assert.deepEqual(joins(), [{subscriptions: JSON.stringify([{name: "releases"}])}]);
    // A second press while it goes sends nothing more.
    press_on(releases);
    assert.equal(joins().length, 1);

    release();
    await settle();
    assert.equal(verb_of(releases), "Open");
    assert.equal(releases.getAttribute("aria-label"), "Open releases, joined");
    assert.deepEqual([...the_list().children], rows);
    // The nav takes it when the server says I am in it.
    assert.ok(!nav_rows().includes("releases"));
    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 42, name: "releases"}],
    });
    assert.ok(nav_rows().includes("releases"));
    assert.deepEqual([...the_list().children], rows);

    press_on(releases);
    await settle();
    assert.equal(surface(), null);
    assert.equal(beta_state.state.place.key, routing.tkey(42, "channel events"));
});

run_test("a refused join is said beside Join, which comes back", async () => {
    serve();
    streams_answer = () => success({streams: PUBLIC_CHANNELS});
    join_answer = () => ({
        status: 400,
        result: "error",
        msg: "Unable to access channel (deploys).",
    });
    open_new();
    await settle();
    const deploys = join_button("deploys");

    press_on(deploys);
    await settle();
    assert.deepEqual(said.splice(0), ["Not joined: Unable to access channel (deploys)."]);
    assert.deepEqual(sounds.splice(0), ["nope"]);
    assert.equal(verb_of(deploys), "Join");
    assert.ok(surface());
    close_new();
});

run_test("Tab reaches the list, Up and Down move along it, Return joins", async () => {
    serve();
    streams_answer = () => success({streams: PUBLIC_CHANNELS});
    join_answer = () => success({subscribed: {[ME]: ["support"]}});
    open_new();
    await settle();

    for (let i = 0; i < 4; i += 1) {
        dom.press("Tab");
    }
    assert.equal(document.activeElement, join_button("deploys"));
    dom.press("ArrowUp");
    assert.equal(document.activeElement, join_button("deploys"));
    dom.press("ArrowDown");
    dom.press("ArrowDown");
    dom.press("ArrowDown");
    assert.equal(document.activeElement, join_button("support"));
    // Round to the form and back: the list keeps the row I left.
    dom.press("Tab");
    assert.equal(document.activeElement, name_field());
    dom.press("Tab", {shiftKey: true});
    assert.equal(document.activeElement, join_button("support"));

    dom.press("Enter");
    await settle();
    assert.deepEqual(joins(), [{subscriptions: JSON.stringify([{name: "support"}])}]);
    assert.deepEqual(created(), []);
    assert.equal(verb_of(join_button("support")), "Open");

    // Open before the server says I am in it: the place opens when it does.
    dom.press("Enter");
    assert.equal(surface(), null);
    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 43, name: "support"}],
    });
    await settle();
    assert.equal(beta_state.state.place.key, routing.tkey(43, "channel events"));
});

run_test("a channel joined elsewhere says Open; none left, or none loaded, is said", async () => {
    serve();
    streams_answer = () => success({streams: PUBLIC_CHANNELS});
    open_new();
    await settle();
    channels.onSubscription({
        type: "subscription",
        op: "add",
        subscriptions: [{stream_id: 41, name: "deploys"}],
    });
    assert.equal(verb_of(join_button("deploys")), "Open");
    close_new();

    streams_answer = () => success({streams: PUBLIC_CHANNELS.slice(0, 2)});
    open_new();
    await settle();
    assert.equal(the_list().textContent, "No other public channels.");
    close_new();

    streams_answer = () => ({status: 400, result: "error", msg: "Something went wrong"});
    open_new();
    await settle();
    assert.equal(the_list().textContent, "Not loaded: Something went wrong");
    close_new();
});
