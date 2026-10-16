"use strict";

// The interim beta's message controls (web/src/armada/beta/hover.js), with
// a real DOM and a stand-in server: More and Up offer an edit only while
// the realm allows it, and a save the server refuses puts the message
// back and gives my words back to the field; More offers Delete only
// where the realm's and the channel's rules let me delete, and a delete
// the server refuses leaves the message and says why. The conversation is drawn by
// the beta's own feed; the frame around it is a few bare elements. The
// beta reads the kit's window globals when it draws; the few it needs here
// are stand-ins that draw nothing of their own, except that a swapped
// word is written, so the default button can be read. Deleted with
// web/src/armada/beta/.

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
// jsdom lays nothing out: an element's box is all zeros, and a range has
// none at all. The overlay measures Onix's words as ranges (hover.js).
dom.window.Range.prototype.getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    right: 0,
    bottom: 0,
    width: 0,
    height: 0,
});
Object.defineProperty(document, "fonts", {
    value: {addEventListener() {}, ready: Promise.resolve()},
});
// The tabs of this browser (connection.js) hear nothing here: Node's own
// BroadcastChannel would keep the test process alive after the last test.
set_global(
    "BroadcastChannel",
    class {
        postMessage() {}
    },
);
// What the page tells the person (M.say on the default button), the sounds
// it plays and what it refuses, kept for the tests to read.
const said = [];
const sounds = [];
const refused = [];
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
    swap(cell, html) {
        cell.innerHTML = html;
    },
    say(_target, text) {
        said.push(text);
    },
    refuse(element) {
        refused.push(element);
    },
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
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

const ME = 3;
const IAGO = 5;
const AGENT = 9;
// A bot of mine, which the server lets me delete the messages of.
const MY_BOT = 7;
const LIMIT = 600;
// The fake clock's now, which only the last test moves on. (Setting the
// clock's date instead would outlive this file: the next one's clock
// would start before its own beginning.)
const T0 = Date.now();
const at = (seconds_ago) => Math.floor(T0 / 1000) - seconds_ago;

// The stand-in server: each request is kept, and answered from the table
// a test sets. An answer may be a promise the test settles later.
const requests = [];
let answers = [];
set_global("fetch", async (url, init = {}) => {
    const method = init.method ?? "GET";
    const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) : null;
    requests.push({method, url: url.split("?")[0], body});
    const answer = answers.find(([m, pattern]) => m === method && pattern.test(url.split("?")[0]));
    assert.ok(answer, `no stand-in answer for ${method} ${url}`);
    const reply = await answer[2]({url, body});
    if (reply === "unreachable") {
        throw new TypeError("Failed to fetch");
    }
    return {ok: (reply.status ?? 200) < 400, status: reply.status ?? 200, json: async () => reply};
});
const success = (data = {}) => ({result: "success", msg: "", ...data});
const RAW = new Map([
    [120, "an old thought"],
    [124, "first words"],
    [126, "another of mine"],
]);
function serve(extra = []) {
    requests.length = 0;
    answers = [
        ...extra,
        [
            "GET",
            /^\/json\/messages\/\d+$/,
            ({url}) =>
                success({message: {content: RAW.get(Number(url.split("?")[0].split("/").pop()))}}),
        ],
        ["PATCH", /^\/json\/messages\/\d+$/, () => success()],
        ["GET", /^\/json\/users\/\d+$/, () => success({user: {timezone: ""}})],
        ["GET", /^\/json\/messages$/, () => success({messages: []})],
    ];
}
const refusal = (msg) => ({result: "error", code: "BAD_REQUEST", msg, status: 400});
const edits = () => requests.filter((r) => r.method === "PATCH");

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const field = find(".tx");
const go = find(".disc");
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
    go,
    goc: find(".disc .cell"),
    ph: find(".ph"),
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const events = zrequire("armada/beta/events.js");
const send = zrequire("armada/beta/send.js");
const hover = zrequire("armada/beta/hover.js");
const mentions = zrequire("armada/beta/mentions.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Shiva";
beta_state.armadaBots.add(AGENT);
for (const [id, name, owner] of [
    [ME, "Shiva", null],
    [IAGO, "Iago", null],
    [AGENT, "Onix", ME],
    [MY_BOT, "Notice Bot", ME],
]) {
    beta_state.people.set(id, {
        id,
        name,
        email: `${id}@example.com`,
        bot: owner !== null,
        active: true,
        owner,
    });
}
// The realm's system groups, nested as the server makes them: everyone
// holds the members, who hold the full members, who hold the moderators,
// who hold the administrators, who hold the owners. Iago administers; I
// am a full member.
const [NOBODY, OWNERS, ADMINS, MODERATORS, FULL_MEMBERS, MEMBERS, EVERYONE] = [
    9, 10, 11, 12, 13, 14, 15,
];
for (const [id, members, subgroups] of [
    [NOBODY, [], []],
    [OWNERS, [], []],
    [ADMINS, [IAGO], [OWNERS]],
    [MODERATORS, [], [ADMINS]],
    [FULL_MEMBERS, [ME], [MODERATORS]],
    [MEMBERS, [], [FULL_MEMBERS]],
    [EVERYONE, [], [MEMBERS]],
]) {
    beta_state.groups.set(id, {members: new Set(members), subgroups: new Set(subgroups)});
}
// Zulip's defaults, with this realm's ten minutes for editing and for
// deleting: administrators delete anything, everyone else their own.
const REALM = {
    allowEdit: true,
    editLimit: LIMIT,
    deleteLimit: LIMIT,
    deleteAny: ADMINS,
    deleteOwn: EVERYONE,
    guest: false,
};
beta_state.state.realm = {...REALM};
const DEVEL = {
    stream_id: 10,
    name: "devel",
    can_delete_any_message_group: NOBODY,
    can_delete_own_message_group: NOBODY,
};
beta_state.streams.set(10, {...DEVEL});

// #devel > dogfood findings: an old message of mine, a recent one, and
// Iago's answer, the latest.
const place = {
    key: routing.tkey(10, "dogfood findings"),
    kind: "topic",
    stream_id: 10,
    topic: "dogfood findings",
    repo: false,
};
const message = (id, sender_id, seconds_ago, words) => ({
    id,
    type: "stream",
    stream_id: 10,
    subject: "dogfood findings",
    sender_id,
    timestamp: at(seconds_ago),
    content: `<p>${words}</p>`,
    reactions: [],
});
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
beta_state.state.P = cache;
for (const m of [
    message(120, ME, 3600, "an old thought"),
    message(124, ME, 60, "first words"),
    message(125, IAGO, 30, "that looks saved"),
]) {
    routing.addMsg(cache, m);
}
// The controls are built at boot, before the conversation is drawn.
hover.buildOver();
feed.drawLane();

// Every promise the page waits on resolves through promises alone; the
// microtasks the page queues itself run on the fake clock.
async function settle() {
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
    clock.tick(0);
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
}
// What a message shows in the conversation: its words and its marks.
const shown = (id) => lane.querySelector(`[data-mid="${id}"]`).closest("section").textContent;
const verb = () => go.getAttribute("aria-label");
const write = (text) => {
    field.textContent = text;
    field.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
};

// The message I stand on in the conversation, reached as the keyboard
// reaches it: the one in the Tab order takes focus, and Up and Down move
// between messages.
async function standOn(id) {
    await settle();
    lane.querySelector("[data-door]").focus();
    const order = beta_state.state.P.msgs.map((m) => String(m.id));
    const here = () => document.activeElement.closest("[data-mid]").dataset.mid;
    let presses = 0;
    while (here() !== String(id)) {
        assert.ok(presses < order.length, `message ${id} could not be reached`);
        presses += 1;
        dom.press(order.indexOf(here()) > order.indexOf(String(id)) ? "ArrowUp" : "ArrowDown");
    }
    return document.activeElement;
}
// More, from the controls that come up on the message I stand on.
async function openMore(id) {
    const door = await standOn(id);
    dom.click(door.parentElement.querySelector('[aria-label="More"]'));
    return find('[role="menu"]');
}
// host.js hands a key to an open menu before the rest of the page sees it.
dom.window.addEventListener("keydown", (e) => {
    if (hover.menu) {
        hover.menu._key(e);
    }
});
// An item of the menu, pressed.
function choose(menu, label) {
    dom.click([...menu.querySelectorAll("button")].find((b) => b.textContent.startsWith(label)));
}
function closeMore() {
    hover.closeMenu();
}

run_test("More offers Edit on my message only while the realm lets me edit it", async () => {
    serve();
    let menu = await openMore(124);
    assert.match(menu.textContent, /Edit/);
    closeMore();

    // An hour old, past the realm's ten minutes: Edit goes.
    menu = await openMore(120);
    assert.doesNotMatch(menu.textContent, /Edit/);
    closeMore();

    // Someone else's message is never mine to edit.
    menu = await openMore(125);
    assert.doesNotMatch(menu.textContent, /Edit/);
    closeMore();

    // An administrator lifts the limit: the realm's own event says so.
    events.onRealm({
        type: "realm",
        op: "update_dict",
        property: "default",
        data: {message_content_edit_limit_seconds: null},
    });
    menu = await openMore(120);
    assert.match(menu.textContent, /Edit/);
    closeMore();

    // ... and then turns editing off altogether.
    events.onRealm({
        type: "realm",
        op: "update_dict",
        property: "default",
        data: {allow_message_editing: false},
    });
    menu = await openMore(124);
    assert.doesNotMatch(menu.textContent, /Edit/);
    closeMore();
    beta_state.state.realm = {...REALM};
    // Nothing was asked of the server for an edit that was not offered.
    assert.deepEqual(requests, []);
});

run_test("a save the server refuses puts the message back and my words in the field", async () => {
    serve([
        [
            "PATCH",
            /^\/json\/messages\/124$/,
            () => refusal("The time limit for editing this message has passed"),
        ],
    ]);
    said.length = 0;
    sounds.length = 0;
    refused.length = 0;
    hover.editLast();
    await settle();
    write("second words");
    send.send({at: 0});
    // At once, as a send is: the new words, marked Edited, and an empty field.
    assert.match(shown(124), /second words/);
    assert.match(shown(124), /Edited/);
    assert.equal(field.textContent, "");

    await settle();
    assert.deepEqual(edits(), [
        {method: "PATCH", url: "/json/messages/124", body: {content: "second words"}},
    ]);
    // The message says what it last saved, with no Edited mark ...
    assert.match(shown(124), /first words/);
    assert.doesNotMatch(shown(124), /second words|Edited/);
    // ... the page says why, as it says every refusal ...
    assert.deepEqual(said, ["Not saved: The time limit for editing this message has passed"]);
    assert.deepEqual(sounds.slice(-1), ["nope"]);
    assert.equal(refused.length, 1);
    // ... and my words are back in the field, as the same edit, to save
    // again or to copy.
    assert.equal(field.textContent, "second words");
    assert.equal(verb(), "Save");
    assert.equal(hover.editing.m.id, 124);
    assert.equal(document.activeElement, field);

    // Esc gives the edit up; the message keeps what it saved.
    hover.endEdit();
    assert.equal(field.textContent, "");
    assert.equal(verb(), "Send");
    assert.match(shown(124), /first words/);
});

run_test("a save that cannot reach the server says so and keeps my words", async () => {
    serve([["PATCH", /^\/json\/messages\/124$/, () => "unreachable"]]);
    said.length = 0;
    hover.editLast();
    await settle();
    write("words on a train");
    send.send({at: 0});
    await settle();
    assert.match(shown(124), /first words/);
    assert.doesNotMatch(shown(124), /Edited/);
    assert.deepEqual(said, ["Not saved: The server cannot be reached"]);
    assert.equal(field.textContent, "words on a train");

    // Tried again once the server is back: saved, and marked Edited.
    serve();
    send.send({at: 0});
    await settle();
    assert.match(shown(124), /words on a train/);
    assert.match(shown(124), /Edited/);
    assert.equal(field.textContent, "");
    assert.equal(verb(), "Send");
});

run_test("an edit gives back the draft the field held, saved or refused", async () => {
    serve();
    write("half a thought");
    let menu = await openMore(124);
    choose(menu, "Edit");
    await settle();
    assert.equal(field.textContent, "first words");
    write("third words");
    send.send({at: 0});
    await settle();
    assert.match(shown(124), /third words/);
    // The draft I had before the edit is mine again.
    assert.equal(field.textContent, "half a thought");

    // Refused while I am writing something new: my words come back as the
    // edit, and what I was writing waits as its draft.
    let refuse;
    serve([
        ["PATCH", /^\/json\/messages\/124$/, () => new Promise((resolve) => (refuse = resolve))],
    ]);
    write("");
    menu = await openMore(124);
    choose(menu, "Edit");
    await settle();
    write("fourth words");
    send.send({at: 0});
    write("a new message");
    refuse(refusal("The time limit for editing this message has passed"));
    await settle();
    assert.equal(field.textContent, "fourth words");
    assert.equal(verb(), "Save");
    hover.endEdit();
    assert.equal(field.textContent, "a new message");

    // Refused while another edit holds the field: my words wait in that
    // edit's draft, and come back when it ends.
    write("");
    RAW.set(124, "third words");
    routing.addMsg(beta_state.state.P, message(126, ME, 20, "another of mine"));
    feed.drawLane();
    serve([
        ["PATCH", /^\/json\/messages\/124$/, () => new Promise((resolve) => (refuse = resolve))],
    ]);
    menu = await openMore(124);
    choose(menu, "Edit");
    await settle();
    write("fifth words");
    send.send({at: 0});
    assert.equal(hover.editLast(), true);
    await settle();
    assert.equal(field.textContent, "another of mine");
    refuse(refusal("The time limit for editing this message has passed"));
    await settle();
    assert.match(shown(124), /third words/);
    assert.equal(field.textContent, "another of mine");
    hover.endEdit();
    assert.equal(field.textContent, "fifth words");
});

run_test("a card opened from More gives focus back to More when Esc closes it", async () => {
    serve();
    // Iago's message, reached from the keyboard; More, then Card, its last item.
    await openMore(125);
    const more = document.activeElement;
    assert.equal(more.getAttribute("aria-label"), "More");
    dom.press("ArrowDown");
    dom.press("ArrowDown");
    dom.press("ArrowDown");
    dom.press("Enter");
    await settle();
    const open = find('[role="dialog"]');
    assert.equal(open.getAttribute("aria-label"), "Iago");
    assert.equal(document.activeElement, open);

    dom.press("Escape");
    assert.equal(find('[role="dialog"]'), null);
    // Focus is on More again, and its controls are back up around it.
    assert.equal(document.activeElement, more);
    for (let node = more; node; node = node.parentElement) {
        assert.ok(!node.inert);
    }
    closeMore();
});

run_test("More's arrows go round: Up from the first item, Down from the last", async () => {
    serve();
    // Iago's message: Reply, Copy text, Copy link, Card. Up from Reply is Card.
    await openMore(125);
    const more = document.activeElement;
    dom.press("ArrowUp");
    dom.press("Enter");
    await settle();
    assert.equal(find('[role="dialog"]').getAttribute("aria-label"), "Iago");
    dom.press("Escape");
    assert.equal(document.activeElement, more);

    // Down from Card is Reply: the field quotes Iago's words.
    dom.click(more);
    dom.press("ArrowUp");
    dom.press("ArrowDown");
    dom.press("Enter");
    assert.equal(find('[role="dialog"]'), null);
    assert.match(find(".cz").textContent, /Iago.*that looks saved/);
    mentions.takeReply();
});

// Whether More offers Delete on a message, read from the menu itself.
async function offersDelete(id) {
    const menu = await openMore(id);
    const offered = [...menu.querySelectorAll("button")].some((b) =>
        b.textContent.startsWith("Delete"),
    );
    closeMore();
    return offered;
}
const realmChanges = (data) =>
    events.onRealm({type: "realm", op: "update_dict", property: "default", data});
const channelChanges = (property, value) =>
    events.onStream({type: "stream", op: "update", stream_id: 10, name: "devel", property, value});

run_test("More offers Delete only where the realm's rules let me delete", async () => {
    serve();
    // Mine, a minute old: everyone may delete their own inside the
    // realm's ten minutes, and everyone holds me three groups down.
    assert.equal(await offersDelete(124), true);
    // An hour old, past the limit.
    assert.equal(await offersDelete(120), false);
    // Iago's: only those who may delete anything may delete it.
    assert.equal(await offersDelete(125), false);

    // A message of a bot I own is mine to delete. Onix's words are the
    // run's record, offered to nobody, though its bot is mine too.
    routing.addMsg(cache, message(127, MY_BOT, 10, "build passed"));
    routing.addMsg(cache, message(128, AGENT, 5, "I read the logs"));
    feed.drawLane();
    assert.equal(await offersDelete(127), true);
    assert.equal(await offersDelete(128), false);
    events.onDelete({type: "delete_message", message_ids: [127, 128]});

    // Only administrators may delete their own now: not I ...
    realmChanges({can_delete_own_message_group: ADMINS});
    assert.equal(await offersDelete(124), false);
    // ... unless the channel lets its members delete theirs, by a group
    // of its own: in a channel, its rule is enough. The limit still holds.
    channelChanges("can_delete_own_message_group", {
        direct_members: [],
        direct_subgroups: [MEMBERS],
    });
    assert.equal(await offersDelete(124), true);
    assert.equal(await offersDelete(120), false);
    // The realm's limit lifted: my old message too.
    events.onRealm({
        type: "realm",
        op: "update",
        property: "message_content_delete_limit_seconds",
        value: null,
    });
    assert.equal(await offersDelete(120), true);

    // Moderators may delete anything; I become one: Iago's message too.
    realmChanges({can_delete_any_message_group: MODERATORS});
    assert.equal(await offersDelete(125), false);
    events.onUserGroup({
        type: "user_group",
        op: "add_members",
        group_id: MODERATORS,
        user_ids: [ME],
    });
    assert.equal(await offersDelete(125), true);
    // As a guest, the realm's rule never lets me delete others' messages,
    // whatever group holds me ...
    events.onRealmUser({type: "realm_user", op: "update", person: {user_id: ME, role: 600}});
    assert.equal(await offersDelete(125), false);
    // ... but a channel's rule may.
    channelChanges("can_delete_any_message_group", {direct_members: [ME], direct_subgroups: []});
    assert.equal(await offersDelete(125), true);

    events.onRealmUser({type: "realm_user", op: "update", person: {user_id: ME, role: 400}});
    events.onUserGroup({
        type: "user_group",
        op: "remove_members",
        group_id: MODERATORS,
        user_ids: [ME],
    });
    beta_state.state.realm = {...REALM};
    beta_state.streams.set(10, {...DEVEL});
    assert.equal(await offersDelete(125), false);
    // Nothing was asked of the server: the rules are the page's to read.
    assert.deepEqual(requests, []);
});

run_test("a delete the server refuses leaves the message and says why", async () => {
    serve([
        [
            "DELETE",
            /^\/json\/messages\/124$/,
            () => refusal("The time limit for deleting this message has passed"),
        ],
    ]);
    said.length = 0;
    sounds.length = 0;
    refused.length = 0;
    const before = shown(124);
    // More, Delete; the dialog stands on Cancel, and Tab goes to Delete.
    choose(await openMore(124), "Delete");
    assert.ok(find('[role="dialog"]'));
    dom.press("Tab");
    assert.equal(document.activeElement.textContent, "Delete");
    dom.press("Enter");
    await settle();
    assert.deepEqual(
        requests.map((r) => [r.method, r.url]),
        [["DELETE", "/json/messages/124"]],
    );
    // The message is as it was, in the conversation and in the page's
    // cache ...
    assert.equal(find('[role="dialog"]'), null);
    assert.equal(shown(124), before);
    assert.ok(beta_state.state.P.byId.has(124));
    // ... and the page says why, as it says a refused edit.
    assert.deepEqual(said, ["Not deleted: The time limit for deleting this message has passed"]);
    assert.deepEqual(sounds.slice(-1), ["nope"]);
    assert.equal(refused.length, 1);
    assert.equal(document.activeElement, field);

    // A delete that cannot reach the server says that.
    serve([["DELETE", /^\/json\/messages\/124$/, () => "unreachable"]]);
    said.length = 0;
    choose(await openMore(124), "Delete");
    dom.press("Tab");
    dom.press("Enter");
    await settle();
    assert.equal(shown(124), before);
    assert.deepEqual(said, ["Not deleted: The server cannot be reached"]);
});

run_test("an edit saved with no words asks to delete only when I may", async () => {
    serve();
    write("");
    said.length = 0;
    // Mine to delete: emptied and saved, the edit asks to delete it.
    choose(await openMore(124), "Edit");
    await settle();
    write("");
    send.send({at: 0});
    assert.ok(find('[role="dialog"]'));
    dom.press("Escape");
    assert.equal(find('[role="dialog"]'), null);

    // Not mine to delete: no dialog; the edit stays open and the page
    // says why.
    realmChanges({can_delete_own_message_group: NOBODY});
    choose(await openMore(124), "Edit");
    await settle();
    write("");
    send.send({at: 0});
    assert.equal(find('[role="dialog"]'), null);
    assert.deepEqual(said, ["Not deleted: You don't have permission to delete this message"]);
    assert.deepEqual(sounds.slice(-1), ["nope"]);
    assert.equal(hover.editing.m.id, 124);
    assert.equal(verb(), "Save");
    hover.endEdit();
    beta_state.state.realm = {...REALM};
    assert.deepEqual(
        requests.filter((r) => r.method !== "GET"),
        [],
    );
});

run_test("Up edits my latest message only inside the limit", async () => {
    serve();
    write("");
    // Up asks for my latest message's words and puts them in the field.
    assert.equal(hover.editLast(), true);
    await settle();
    assert.equal(field.textContent, "another of mine");
    assert.equal(verb(), "Save");
    hover.endEdit();
    assert.equal(field.textContent, "");
    assert.equal(verb(), "Send");

    // It was sent 20 seconds ago. Up still opens it 4 seconds past the
    // realm's ten minutes, the grace Zulip's own client gives Up ...
    clock.tick((LIMIT - 20 + 4) * 1000);
    assert.equal(hover.editLast(), true);
    await settle();
    hover.endEdit();
    // ... and not after it.
    clock.tick(2000);
    requests.length = 0;
    assert.equal(hover.editLast(), false);
    // A driver that asks for the edit anyway is refused the same way.
    hover.startEdit(beta_state.state.P.byId.get(126));
    await settle();
    assert.equal(field.textContent, "");
    assert.equal(verb(), "Send");
    assert.deepEqual(requests, []);
});
