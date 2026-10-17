"use strict";

// Where the interim beta offers Onix (web/src/armada/beta/runs.js,
// agentCanRun; mentions.js; send.js), with a real DOM and a stand-in
// server. By the server's rules (zerver/lib/armada.py handle_message,
// zerver/lib/armada_worker.py start_blocked_reason), a mention of the
// agent starts a run in any channel's topic, the first run in a channel
// with no repository making its repository, and never in a direct
// message; only for a person allowed to start runs, and not in a topic
// its people paused. The `@` picker offers Onix exactly there, and a
// message that names Onix there is sent as one to Onix. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const question = require("../../../zerver/tests/fixtures/armada_question_message.json");
const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data" data-may-start-runs="on"></div>
<nav><div class="fixed"></div><div class="scroll"></div></nav>
<main>
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
set_global(
    "ResizeObserver",
    class {
        observe() {}
    },
);
set_global("BroadcastChannel", class {});
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
    say: noop,
    cross: noop,
    swap(cell, html) {
        cell.innerHTML = html;
    },
    current: () => [{}, {}],
    rest: () => ({top: 0, left: 0}),
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
set_global("fitLists", noop);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global("wk", () => `<span class="mk wk"></span>`);
set_global("mk", (name) => `<svg class="mk ${name}"></svg>`);
set_global("face", (spec, cls) => `<i class="av ${cls}">${spec.split(" ")[0].split(":")[0]}</i>`);

// The stand-in server: every request is kept; a message sent is given the
// next id.
const requests = [];
let next_id = 900;
set_global("fetch", async (url, init) => {
    const method = init.method;
    const body = Object.fromEntries(new URLSearchParams(init.body));
    requests.push({method, url: url.split("?")[0], body});
    const sent = {};
    if (method === "POST" && url === "/json/messages") {
        sent.id = next_id;
        next_id += 1;
    }
    return {
        ok: true,
        status: 200,
        json: async () => ({result: "success", msg: "", messages: [], armada_runs: [], ...sent}),
    };
});

const find = (selector) => document.querySelector(selector);
const field = find(".tx");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: find("#pane"),
    lane: find("#col"),
    nav: find("nav"),
    navFixed: find("nav .fixed"),
    navScroll: find("nav .scroll"),
    side: find("#side"),
    outline: find("#outline"),
    panes: {Chat: find("#pane"), Home: find('[data-pane="Home"]')},
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
});
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const mentions = zrequire("armada/beta/mentions.js");
const send = zrequire("armada/beta/send.js");
const column = zrequire("armada/beta/column.js");
const events = zrequire("armada/beta/events.js");
const feed = zrequire("armada/beta/feed.js");
const runs = zrequire("armada/beta/runs.js");

const ME = 3;
const IAGO = 5;
const AGENT = 9;
const OPS = 4;
const CLICK = 22;
beta_state.state.ME = ME;
beta_state.state.meName = "King Hamlet";
beta_state.state.hd = document.createElement("header");
beta_state.armadaBots.add(AGENT);
for (const [id, name, bot] of [
    [ME, "King Hamlet", false],
    [IAGO, "Iago", false],
    [AGENT, "Onix", true],
]) {
    beta_state.people.set(id, {id, name, email: `${id}@example.com`, bot, active: true});
}
// #ops, a channel with no repository yet, as in a fresh organisation.
beta_state.streams.set(OPS, {stream_id: OPS, name: "ops"});
// #click, a channel with its repository.
beta_state.streams.set(CLICK, {stream_id: CLICK, name: "click"});
beta_state.repoStreams.add(CLICK);
mentions.wireMentions();

// A place opened, its messages read.
function open(p) {
    beta_state.state.place = p;
    const c = routing.cacheOf(p);
    c.loaded = true;
    c.oldest = true;
    beta_state.state.P = c;
    return c;
}
const topic = (name, stream = OPS) => open(routing.placeFromKey(routing.tkey(stream, name)));
const direct = () => open(routing.placeFromKey(routing.dkey([IAGO])));

// Every promise the page waits on resolves through promises alone; its
// timers run on the fake clock.
async function settle() {
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
    clock.tick(20);
}

// "@" typed at the field's start, as the keyboard leaves it: the caret
// after it.
function typeAt() {
    field.textContent = "@";
    dom.window.getSelection().collapse(field.firstChild, 1);
    field.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
}
const offered = () => [...document.querySelectorAll("#mpk .mi .lab")].map((x) => x.textContent);
function close() {
    field.textContent = "";
    field.dispatchEvent(new dom.window.InputEvent("input", {bubbles: true}));
}

run_test("Onix is offered in a channel's topic before the channel has a repository", () => {
    topic("file-backup");
    typeAt();
    assert.deepEqual(offered(), ["Iago", "Onix", "topic"]);
    close();
});

run_test("Onix is not offered where a mention of it starts no run", () => {
    // A direct message: the server answers that runs need a channel's topic.
    direct();
    typeAt();
    assert.deepEqual(offered(), ["Iago"]);
    close();

    // A topic its people paused.
    topic("paused").ctx = {is_paused: true};
    typeAt();
    assert.deepEqual(offered(), ["Iago", "topic"]);
    close();

    // Someone the server does not let start runs.
    topic("file-backup");
    beta_state.DATA.mayStartRuns = "off";
    typeAt();
    assert.deepEqual(offered(), ["Iago", "topic"]);
    close();
    beta_state.DATA.mayStartRuns = "on";
});

run_test("a message that names Onix there is sent as one to Onix", async () => {
    const c = topic("file-backup");
    requests.length = 0;
    field.textContent = "@onix why did the backup stop?";
    send.send({at: 0});
    // Onix's turn stands under mine at once, and the server is told the
    // message is for Onix, as from a repository's topic.
    assert.deepEqual(
        c.msgs.map((m) => [m.sender_id, Boolean(m.local)]),
        [
            [ME, true],
            [AGENT, true],
        ],
    );
    await settle();
    const post = requests.find((r) => r.method === "POST" && r.url === "/json/messages");
    assert.equal(post.body.content, "@**Onix** why did the backup stop?");
    assert.equal(post.body.agent_followup, "true");
});

// The column, read as a person reads it: each list's heading and rows.
function column_lists() {
    column.drawSide();
    return [...find("#outline").children].map((x) =>
        [
            x.querySelector(".sh > span"),
            ...x.querySelectorAll("li"),
            ...(x.matches(".note") ? [x] : []),
        ]
            .filter(Boolean)
            .map((y) => y.textContent.trim()),
    );
}
const run = {id: 1, status: "done", trigger_message_id: 18, date_created: 0, plan: null};

run_test("the column holds the checklist, Runs and Onix wherever Onix can run", () => {
    // #ops has no repository: no Changes, whose facts are git's.
    const c = topic("certificate");
    c.runsLoaded = true;
    assert.deepEqual(column_lists(), [
        ["No run here yet."],
        ["Runs", "No runs yet"],
        ["People", "Onix"],
    ]);

    // #click has its repository: Changes stands under the checklist.
    const w = topic("option-groups", CLICK);
    w.runsLoaded = true;
    w.ctx = {git: {available: true, changed_files: []}};
    assert.deepEqual(column_lists(), [
        ["No run here yet."],
        ["Changes", "No uncommitted changes"],
        ["Runs", "No runs yet"],
        ["People", "Onix"],
    ]);
});

run_test("where Onix cannot run, the column keeps its runs if it has run there", () => {
    beta_state.DATA.mayStartRuns = "off";
    const c = topic("queues");
    assert.deepEqual(column_lists(), [[]], "People only, under the heading line");
    c.runs = [run];
    assert.equal(column_lists()[1][0], "Runs");
    beta_state.DATA.mayStartRuns = "on";
});

run_test("opening any channel's topic reads its runs", async () => {
    requests.length = 0;
    routing.openPlace(routing.placeFromKey(routing.tkey(OPS, "disk")));
    await settle();
    assert.ok(requests.some((r) => r.url === "/json/armada/runs"));
});

run_test("a channel's first run makes it a repository in the open page", async () => {
    // #ops has no repository: its column has no Changes.
    const c = topic("database-backup");
    c.runsLoaded = true;
    assert.deepEqual(
        column_lists().map((list) => list[0]),
        ["No run here yet.", "Runs", "People"],
    );
    requests.length = 0;
    // The run's first event, queued: the server made the repository with it.
    events.onRun({
        type: "agent_run",
        run_id: 7,
        event_type: "queued",
        status: "queued",
        stream: "ops",
        topic: "database-backup",
        trigger_message_id: 950,
        result_message_id: null,
        run_date_created: 0,
        date_created: 0,
        plan: null,
    });
    assert.deepEqual(
        column_lists().map((list) => list[0]),
        [
            "The runs here had no plan. A run that plans its work shows its steps here.",
            "Changes",
            "Runs",
            "People",
        ],
    );
    // The topic's git facts are read, and any place in #ops opened from
    // now on is a repository's.
    await settle();
    clock.tick(1000);
    assert.ok(requests.some((r) => r.url === "/json/armada/topic-context"));
    assert.ok(routing.placeFromKey(routing.tkey(OPS, "disk")).repo);
});

run_test("a question that waits is answered by its options, as a typed reply", async () => {
    // Onix asks in #general > greetings, a channel the page was loaded
    // without a repository for, as on runix.chat; the run waits.
    beta_state.streams.set(3, {stream_id: 3, name: "general"});
    const c = open(routing.placeFromKey(routing.tkey(3, "greetings")));
    const now = Math.floor(Date.now() / 1000);
    const asked = {id: 54, type: "stream", stream_id: 3, subject: "greetings", sender_id: AGENT};
    routing.addMsg(c, {...asked, timestamp: now, content: question.rendered, reactions: []});
    c.runs = [{id: 11, status: "running", trigger_message_id: 52, date_created: now - 2}];
    runs.inferAsk(c.runs[0], c.msgs);
    feed.drawLane();
    runs.drawAsks();
    // The first option, as a press or the key 1 in an empty field reaches
    // it, is sent as I would write the answer.
    requests.length = 0;
    assert.ok(runs.answerWith(runs.askOption(1), 0));
    await settle();
    const sent = requests.filter((r) => r.method === "POST" && r.url === "/json/messages");
    assert.deepEqual(
        sent.map((r) => r.body.content),
        ["@**Onix** Red"],
    );
    assert.equal(runs.askOption(3), null, "there is no third option");

    // Answered: the question no longer waits, and its options are spent.
    runs.drawAsks();
    assert.equal(runs.askOption(2), null);
    assert.equal(runs.answerWith(find('[data-answer="Blue"]'), 0), false);
});

run_test("a question asked before the place's runs are read is live at once", () => {
    // As on runix.chat: a run's events were seen for #general > greetings
    // before the place was held (runs 9 and 10), and the place's own runs
    // are not read yet when run 11 starts and asks.
    const key = routing.tkey(3, "greetings again");
    runs.runsSeen.set(key, [{id: 10, status: "done", trigger_message_id: 48}]);
    const c = open(routing.placeFromKey(key));
    c.msgs.length = 0;
    const now = Math.floor(Date.now() / 1000);
    events.onRun({
        type: "agent_run",
        run_id: 11,
        event_type: "running",
        status: "running",
        stream: "general",
        topic: "greetings again",
        trigger_message_id: 52,
        result_message_id: 53,
        run_date_created: now,
        date_created: now,
        plan: null,
    });
    events.onMessage({
        message: {
            id: 54,
            type: "stream",
            stream_id: 3,
            subject: "greetings again",
            sender_id: AGENT,
            timestamp: now,
            content: question.rendered,
            reactions: [],
        },
        flags: ["read"],
    });
    // The run waits on its question from the moment it is asked, not from
    // the server's first reminder that it waits, 15 s later.
    assert.equal(runs.waitingRun(c).id, 11);
});

run_test("a run asked with a mention and no words is named by the words just before it", () => {
    const c = topic("titles");
    c.runsLoaded = true;
    const t0 = Math.floor(Date.now() / 1000) - 3600;
    const mention = '<p><span class="user-mention" data-user-id="9">@Onix</span>';
    const say = (id, sender_id, seconds, content) =>
        routing.addMsg(c, {id, sender_id, timestamp: t0 + seconds, content, reactions: []});
    say(300, ME, 0, "<p>New channel?</p>");
    say(301, ME, 5, `${mention}</p>`);
    say(302, IAGO, 10, "<p>hi</p>");
    // Mine from long before is not what this mention asks.
    say(303, ME, 400, `${mention}</p>`);
    say(304, ME, 410, `${mention} check the disk</p>`);
    // Right after a mention with words: nothing new was said.
    say(305, ME, 420, `${mention}</p>`);
    c.runs = [305, 304, 303, 301].map((trigger_message_id, i) => ({
        id: 40 - i,
        status: "done",
        trigger_message_id,
        date_created: t0 + 500,
        plan: null,
    }));
    column.drawSide();
    assert.deepEqual(
        [...find("#sec-runs").querySelectorAll("li > span:first-of-type")].map(
            (t) => t.textContent,
        ),
        [
            "Look at the latest messages",
            "check the disk",
            "Look at the latest messages",
            "New channel?",
        ],
    );
});
