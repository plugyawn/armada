"use strict";

// The interim beta's Later and Send when (web/src/armada/beta/later.js),
// with a real DOM and a stand-in server. Later is an item of a message's
// More menu, with its times as a small menu, and the overlay keeps its
// four controls; what is put off is listed last on Home, and a reminder
// that came back stands at the top of Home until it is read. Send when
// is a menu on Send: what the field holds is held until a time or until
// the run here ends, as a quiet pending row of mine, which can be edited
// and cancelled. The conversation is drawn by the beta's own feed; the
// frame around it is a few bare elements, and the kit's window globals
// are stand-ins that draw nothing of their own. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<nav><div class="scroll"></div></nav>
<main>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div class="feed" id="pane"><div class="clip"><div class="lane" id="col"></div></div></div>
  <div class="pane" data-pane="Home" hidden><div class="lane" id="home"></div></div>
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
Object.defineProperty(document, "fonts", {
    value: {addEventListener() {}, ready: Promise.resolve()},
});
set_global(
    "BroadcastChannel",
    class {
        postMessage() {}
    },
);
// What the page tells the person (M.say beside Send) and the sounds it
// plays, kept for the tests to read.
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
    refuse: noop,
    figure: noop,
    rest: () => ({top: 0, left: 0}),
    swap(cell, html) {
        cell.innerHTML = html;
    },
    say(_target, text) {
        said.push(text);
    },
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
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

const ME = 3;
const IAGO = 5;
const AGENT = 9;
const NOTIFICATION_BOT = 11;
const RUN = 77;
const T0 = Date.now();
const at = (seconds_ago) => Math.floor(T0 / 1000) - seconds_ago;

// The stand-in server: each request is kept, and answered from the table
// a test sets.
const requests = [];
let answers = [];
set_global("fetch", async (url, init = {}) => {
    const method = init.method ?? "GET";
    const [path, query] = url.split("?");
    const body =
        init.body instanceof URLSearchParams
            ? Object.fromEntries(init.body)
            : query
              ? Object.fromEntries(new URLSearchParams(query))
              : null;
    requests.push({method, url: path, body});
    const answer = answers.find(([m, pattern]) => m === method && pattern.test(path));
    assert.ok(answer, `no stand-in answer for ${method} ${path}`);
    const reply = await answer[2]({url, body});
    return {ok: (reply.status ?? 200) < 400, status: reply.status ?? 200, json: async () => reply};
});
const success = (data = {}) => ({result: "success", msg: "", ...data});
const messagesById = new Map();
function serve(extra = []) {
    requests.length = 0;
    answers = [
        ...extra,
        ["POST", /^\/json\/reminders$/, () => success({reminder_id: 501})],
        ["DELETE", /^\/json\/reminders\/\d+$/, () => success()],
        ["POST", /^\/json\/messages\/flags$/, () => success()],
        ["POST", /^\/json\/scheduled_messages$/, () => success({scheduled_message_id: 601})],
        ["PATCH", /^\/json\/scheduled_messages\/\d+$/, () => success()],
        ["DELETE", /^\/json\/scheduled_messages\/\d+$/, () => success()],
        ["POST", /^\/json\/messages$/, () => success({id: 140})],
        [
            "GET",
            /^\/json\/messages$/,
            ({body}) =>
                success({
                    messages: JSON.parse(body.message_ids)
                        .map((id) => messagesById.get(id))
                        .filter(Boolean),
                }),
        ],
    ];
}
const asked = (method, url) => requests.filter((r) => r.method === method && r.url === url);

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const field = find(".tx");
const go = find(".disc");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane,
    nav: find("nav"),
    navScroll: find("nav .scroll"),
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
    panes: {Chat: find("#pane"), Home: find('[data-pane="Home"]')},
});
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop});
mock_esm("../../src/armada/beta/column.js", {drawSide: noop});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const send = zrequire("armada/beta/send.js");
const hover = zrequire("armada/beta/hover.js");
const home = zrequire("armada/beta/home.js");
const later = zrequire("armada/beta/later.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Cordelia";
// The header, which Home draws again with its rows (header.js stands in).
beta_state.state.hd = document.createElement("header");
beta_state.armadaBots.add(AGENT);
for (const [id, name, bot, email] of [
    [ME, "Cordelia", false, "cordelia@example.com"],
    [IAGO, "Iago", false, "iago@example.com"],
    [AGENT, "Onix", true, "onix-bot@example.com"],
    [NOTIFICATION_BOT, "Notification Bot", true, "notification-bot@zulip.com"],
]) {
    beta_state.people.set(id, {id, name, email, bot, active: true, owner: null});
}
beta_state.state.realm = {
    allowEdit: true,
    editLimit: null,
    deleteLimit: null,
    deleteAny: null,
    deleteOwn: null,
    guest: false,
};
beta_state.streams.set(10, {stream_id: 10, name: "devel"});
beta_state.repoStreams.add(10);

// #devel > later, a worktree where a run is live: Iago's question and
// my answer.
const place = {
    key: routing.tkey(10, "later"),
    kind: "topic",
    stream_id: 10,
    stream: "devel",
    topic: "later",
    repo: true,
};
const message = (id, sender_id, seconds_ago, words) => ({
    id,
    type: "stream",
    stream_id: 10,
    subject: "later",
    sender_id,
    timestamp: at(seconds_ago),
    content: `<p>${words}</p>`,
    reactions: [],
});
const live_run = {id: RUN, status: "running", trigger_message_id: 124, date_created: at(60)};
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
cache.runs = [live_run];
beta_state.state.P = cache;
for (const m of [
    message(124, ME, 60, "run the tests please"),
    message(125, IAGO, 30, "which flag name did we pick?"),
]) {
    routing.addMsg(cache, m);
    messagesById.set(m.id, m);
}
hover.buildOver();
later.wireLater();
feed.drawLane();
// host.js hands a key to an open More menu before the rest of the page.
dom.window.addEventListener("keydown", (e) => {
    if (hover.menu) {
        hover.menu._key(e);
    }
});

// Every promise the page waits on resolves through promises alone; the
// timers it sets (Home is drawn again 50 ms after a change) run on the
// fake clock.
async function settle() {
    for (let round = 0; round < 3; round += 1) {
        for (let i = 0; i < 50; i += 1) {
            await Promise.resolve();
        }
        clock.tick(60);
    }
}
const write = (text) => {
    field.textContent = text;
    field.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
};
const verb = () => go.getAttribute("aria-label");
const labels = (menu) => [...menu.querySelectorAll("button")].map((b) => b.firstChild.textContent);
const choose = (menu, label) =>
    dom.click([...menu.querySelectorAll("button")].find((b) => b.textContent.startsWith(label)));
// A menu of choices, found by its name.
const choices = (name) => find(`[role="menu"][aria-label="${name}"]`);

// The message I stand on, reached as the keyboard reaches it: the one in
// the Tab order takes focus, and Up and Down move between messages; its
// overlay comes up inside it.
async function standOn(id) {
    await settle();
    // Tab: the page chooses the message in the Tab order as the key goes
    // down, and the browser moves focus to it.
    dom.press("Tab");
    lane.querySelector("[data-door]").focus();
    const order = cache.msgs.map((m) => String(m.id));
    const here = () => document.activeElement.closest("[data-mid]").dataset.mid;
    while (here() !== String(id)) {
        dom.press(order.indexOf(here()) > order.indexOf(String(id)) ? "ArrowUp" : "ArrowDown");
    }
    return document.activeElement;
}
async function openMore(id) {
    const door = await standOn(id);
    dom.click(door.parentElement.querySelector('[aria-label="More"]'));
    return find('[role="menu"]');
}
async function openLaterOf(id) {
    choose(await openMore(id), "Later");
    return choices("Later");
}
const tomorrowAt9 = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return Math.floor(d.getTime() / 1000);
};
const homeRows = () =>
    [...find("#home").querySelectorAll(".hrow")].map((row) => ({
        text: row.querySelector("p").textContent,
        where: row.querySelector(".wh").textContent,
        when: row.querySelector("time").textContent,
    }));
// A place opened as routing.js opens it: the pending rows are drawn for it.
function showHome() {
    beta_state.state.place = home.HOME;
    later.drawPending();
    home.drawHome();
}
function showTopic() {
    beta_state.state.place = place;
    beta_state.state.P = cache;
    later.drawPending();
}
const pendingRows = () =>
    [...find("#pane").querySelectorAll(".pend > .cl")].map((row) => ({
        words: row.querySelector(".b").textContent,
        waits: row.querySelector(".tl .sn").textContent,
        controls: [...row.querySelectorAll(".tl button")].map((b) => b.textContent),
    }));

run_test("Later is an item of More; the overlay keeps its four controls", async () => {
    serve();
    for (const id of [125, 124]) {
        const menu = await openMore(id);
        assert.deepEqual(
            [...find(".over").querySelectorAll(".c")].map((c) => c.getAttribute("aria-label")),
            ["React", "Reply in thread", "Copy text", "More"],
        );
        assert.deepEqual(labels(menu).slice(0, 2), ["Reply", "Later"]);
        // Its key is L, as each item's key is its letter.
        dom.press("l");
        assert.equal(hover.menu, null);
        assert.ok(choices("Later"));
        dom.press("Escape");
        assert.equal(choices("Later"), null);
    }
    assert.deepEqual(requests, []);
});

run_test(
    "Later's times, and When this run ends only while a run is live in its topic",
    async () => {
        serve();
        let menu = await openLaterOf(125);
        assert.deepEqual(labels(menu), [
            "In 1 hour",
            "Tomorrow",
            "When this run ends",
            "Just keep it",
        ]);
        choose(menu, "When this run ends");
        await settle();
        assert.equal(choices("Later"), null);
        assert.deepEqual(asked("POST", "/json/reminders"), [
            {method: "POST", url: "/json/reminders", body: {message_id: "125", agent_run_id: "77"}},
        ]);
        assert.deepEqual(said.slice(-1), ["Back when the run ends"]);

        // From the keyboard: Down to Tomorrow, Return chooses it.
        serve();
        menu = await openLaterOf(125);
        dom.press("ArrowDown");
        dom.press("Enter");
        await settle();
        assert.deepEqual(asked("POST", "/json/reminders"), [
            {
                method: "POST",
                url: "/json/reminders",
                body: {message_id: "125", scheduled_delivery_timestamp: String(tomorrowAt9())},
            },
        ]);

        // Esc gives the menu up and asks the server nothing.
        serve();
        await openLaterOf(125);
        dom.press("Escape");
        assert.equal(choices("Later"), null);
        assert.deepEqual(requests, []);

        // With the run ended, no run is offered to wait for.
        cache.runs = [{...live_run, status: "done"}];
        menu = await openLaterOf(125);
        assert.deepEqual(labels(menu), ["In 1 hour", "Tomorrow", "Just keep it"]);
        choose(menu, "In 1 hour");
        await settle();
        const [{body}] = asked("POST", "/json/reminders");
        assert.ok(
            Math.abs(Number(body.scheduled_delivery_timestamp) - (Date.now() / 1000 + 3600)) < 5,
        );
        cache.runs = [live_run];
    },
);

run_test("the Later list on Home: first line, where, when it comes back; Done", async () => {
    serve();
    later.applyLater({
        reminders: [
            {
                reminder_id: 502,
                reminder_target_message_id: 125,
                scheduled_delivery_timestamp: tomorrowAt9(),
                failed: false,
            },
            {
                reminder_id: 503,
                reminder_target_message_id: 124,
                scheduled_delivery_timestamp: 253402214400,
                agent_run_id: RUN,
                failed: false,
            },
        ],
        starred_messages: [],
    });
    await later.loadLater();
    showHome();
    assert.deepEqual(homeRows(), [
        {
            text: "which flag name did we pick?",
            where: "devel / later",
            when: "Tomorrow 9:00",
        },
        {text: "run the tests please", where: "devel / later", when: "When the run ends"},
    ]);

    // "Just keep it": a star, which the list shows as Kept.
    showTopic();
    serve();
    choose(await openLaterOf(125), "Just keep it");
    await settle();
    assert.deepEqual(asked("POST", "/json/messages/flags"), [
        {
            method: "POST",
            url: "/json/messages/flags",
            body: {messages: "[125]", op: "add", flag: "starred"},
        },
    ]);
    later.onStarred({type: "update_message_flags", op: "add", flag: "starred", messages: [125]});

    // Done on Iago's message: its reminder is deleted and its star taken off.
    showHome();
    serve();
    dom.click(find('#home [data-later="125"] [data-later-done]'));
    await settle();
    assert.deepEqual(
        requests.map((r) => [r.method, r.url]),
        [
            ["DELETE", "/json/reminders/502"],
            ["POST", "/json/messages/flags"],
        ],
    );
    // Done does not open the message's place.
    assert.equal(beta_state.state.place, home.HOME);
    later.onReminders({type: "reminders", op: "remove", reminder_id: 502});
    later.onStarred({
        type: "update_message_flags",
        op: "remove",
        flag: "starred",
        messages: [125],
    });
    await settle();
    // A reminder I deleted myself did not come back: nothing is asked.
    assert.deepEqual(asked("GET", "/json/messages"), []);
    assert.deepEqual(homeRows(), [
        {text: "run the tests please", where: "devel / later", when: "When the run ends"},
    ]);
    later.onReminders({type: "reminders", op: "remove", reminder_id: 503});
});

run_test("a reminder that came back stands at the top of Home until it is read", async () => {
    serve();
    const dm = {
        id: 900,
        type: "private",
        sender_id: NOTIFICATION_BOT,
        timestamp: at(5),
        content:
            '<p>You requested a reminder for the following message.</p><p><span class="user-mention silent">Iago</span> <a href="http://zulip.zulipdev.com/#narrow/channel/10-devel/topic/later/near/125">said</a> in <a href="#">#devel &gt; later</a>:</p><blockquote><p>which flag name did we pick?</p></blockquote>',
        display_recipient: [{id: NOTIFICATION_BOT}, {id: ME}],
    };
    messagesById.set(900, dm);
    const key = routing.dkey([NOTIFICATION_BOT]);
    beta_state.unread.set(key, new Set([900]));
    // The server says the reminder was delivered: it is gone from the list, and has come back.
    later.onReminders({type: "reminders", op: "remove", reminder_id: 504});
    await settle();
    showHome();
    const [top] = homeRows();
    assert.deepEqual(top, {
        text: "which flag name did we pick?",
        where: "devel / later",
        when: `Back ${new Date(dm.timestamp * 1000).getHours()}:${String(new Date(dm.timestamp * 1000).getMinutes()).padStart(2, "0")}`,
    });
    assert.equal(find("#home .hrow").dataset.k, place.key);

    // Done reads it: the row goes, and the server is told.
    serve();
    dom.click(find("#home [data-later-seen]"));
    await settle();
    assert.deepEqual(asked("POST", "/json/messages/flags"), [
        {
            method: "POST",
            url: "/json/messages/flags",
            body: {messages: "[900]", op: "add", flag: "read"},
        },
    ]);
    assert.equal(find("#home [data-back]"), null);
    assert.equal(beta_state.unread.has(key), false);
});

run_test("Send when holds what the field holds until the run here ends", async () => {
    serve();
    showTopic();
    write("after this lands, open the PR");
    // A right press on Send opens the menu; the run here is offered.
    go.dispatchEvent(new dom.window.MouseEvent("contextmenu", {bubbles: true, cancelable: true}));
    const menu = choices("Send when");
    assert.deepEqual(labels(menu), ["In 1 hour", "Tomorrow 9:00", "When this run ends"]);
    choose(menu, "When this run ends");

    // At once: the field is empty and my words stand as a pending row
    // that says what they wait for.
    assert.equal(field.textContent, "");
    assert.deepEqual(pendingRows(), [
        {words: "after this lands, open the PR", waits: "When the run ends", controls: []},
    ]);
    await settle();
    assert.deepEqual(asked("POST", "/json/scheduled_messages"), [
        {
            method: "POST",
            url: "/json/scheduled_messages",
            body: {
                type: "stream",
                to: "10",
                topic: "later",
                content: "after this lands, open the PR",
                read_by_sender: "true",
                agent_run_id: "77",
            },
        },
    ]);
    // Nothing was sent now.
    assert.deepEqual(asked("POST", "/json/messages"), []);
    // The server's copy: the row can be edited and cancelled.
    later.onScheduled({
        type: "scheduled_messages",
        op: "add",
        scheduled_messages: [
            {
                scheduled_message_id: 601,
                type: "stream",
                to: 10,
                topic: "later",
                content: "after this lands, open the PR",
                rendered_content: "<p>after this lands, open the PR</p>",
                scheduled_delivery_timestamp: 253402214400,
                agent_run_id: RUN,
                failed: false,
            },
        ],
    });
    assert.deepEqual(pendingRows(), [
        {
            words: "after this lands, open the PR",
            waits: "When the run ends",
            controls: ["Edit", "Cancel"],
        },
    ]);

    // Return alone still sends at once.
    serve();
    write("and a word now");
    send.send({at: 0});
    await settle();
    assert.equal(asked("POST", "/json/messages").length, 1);
    assert.deepEqual(asked("POST", "/json/scheduled_messages"), []);
});

run_test("Edit puts a held message's words in the field; Return saves them into it", async () => {
    serve();
    showTopic();
    dom.click(find("#pane [data-pend-edit]"));
    assert.equal(field.textContent, "after this lands, open the PR");
    assert.equal(verb(), "Save");
    write("after this lands, open the PR and ping Iago");
    send.send({at: 0});
    assert.equal(field.textContent, "");
    assert.equal(pendingRows()[0].words, "after this lands, open the PR and ping Iago");
    await settle();
    assert.deepEqual(asked("PATCH", "/json/scheduled_messages/601"), [
        {
            method: "PATCH",
            url: "/json/scheduled_messages/601",
            body: {content: "after this lands, open the PR and ping Iago"},
        },
    ]);
    assert.equal(verb(), "Send");
});

run_test("Cancel gives the words back to the field, and the message is not sent", async () => {
    serve();
    showTopic();
    dom.click(find("#pane [data-pend-cancel]"));
    assert.deepEqual(pendingRows(), []);
    assert.equal(field.textContent, "after this lands, open the PR and ping Iago");
    await settle();
    assert.deepEqual(asked("DELETE", "/json/scheduled_messages/601"), [
        {method: "DELETE", url: "/json/scheduled_messages/601", body: null},
    ]);
    write("");
});

run_test("a held message that is sent leaves its row; a time is said as a time", async () => {
    serve();
    showTopic();
    const in_an_hour = Math.floor(Date.now() / 1000) + 3600;
    later.onScheduled({
        type: "scheduled_messages",
        op: "add",
        scheduled_messages: [
            {
                scheduled_message_id: 602,
                type: "stream",
                to: 10,
                topic: "later",
                content: "standup notes",
                rendered_content: "<p>standup notes</p>",
                scheduled_delivery_timestamp: in_an_hour,
                failed: false,
            },
        ],
    });
    const d = new Date(in_an_hour * 1000);
    const hhmm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
    assert.equal(
        pendingRows()[0].waits,
        d.getDate() === new Date().getDate() ? hhmm : `Tomorrow ${hhmm}`,
    );
    // A held message is mine and of this place only: Home has none.
    showHome();
    assert.equal(find("#pane .pend").children.length, 0);
    showTopic();
    later.onScheduled({type: "scheduled_messages", op: "remove", scheduled_message_id: 602});
    assert.deepEqual(pendingRows(), []);
});

run_test(
    "Send when offers no run where none is live, and holds nothing from an empty field",
    async () => {
        serve();
        showTopic();
        cache.runs = [];
        write("tomorrow's plan");
        assert.equal(later.openSendWhen(), true);
        assert.deepEqual(labels(choices("Send when")), ["In 1 hour", "Tomorrow 9:00"]);
        choose(choices("Send when"), "Tomorrow 9:00");
        await settle();
        assert.equal(
            asked("POST", "/json/scheduled_messages")[0].body.scheduled_delivery_timestamp,
            String(tomorrowAt9()),
        );
        later.onScheduled({type: "scheduled_messages", op: "remove", scheduled_message_id: 601});

        // An empty field holds nothing.
        serve();
        write("");
        later.openSendWhen();
        choose(choices("Send when"), "In 1 hour");
        await settle();
        assert.deepEqual(requests, []);
        cache.runs = [live_run];
    },
);
