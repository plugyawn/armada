"use strict";

// The interim beta's polls and to-do lists (web/src/armada/beta/widgets.js),
// with a real DOM and a stand-in server. A widget is drawn from its
// message's submessages in place of the words that made it; my vote, my
// option, my task and my tick are whole in the frame of the press, sent as
// a submessage, and confirmed (never counted twice) by the server's
// `submessage` event; someone else's arrives the same way and changes the
// row it is about in place; a refusal is taken back and says why; my own
// /poll shows as a poll at once; Onix's poll is drawn as anyone's. The
// submessage shapes are Zulip's (web/src/poll_data.ts, todo_data.ts).
// Deleted with web/src/armada/beta/.

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
const said = [];
const sounds = [];
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
    // A figure that changes is written into its cell.
    figure(cell, value) {
        cell.textContent = value;
    },
    rest: () => ({top: 0, left: 0}),
    say(_target, text) {
        said.push(text);
    },
    refuse: noop,
});
set_global("S", {
    play(name) {
        sounds.push(name);
    },
});
set_global("host", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
// A face is drawn with no words of its own, as a picture is.
set_global("face", (_spec, cls) => `<i class="av ${cls || ""}"></i>`);

// The stand-in server: each write is kept, and answered from the table a
// test sets (a refusal, or success).
const writes = [];
let refuse = null;
set_global("fetch", async (url, init = {}) => {
    const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) : null;
    if (init.method === "POST") {
        writes.push({url, body});
    }
    const answer =
        refuse && url === "/json/submessage"
            ? {result: "error", msg: refuse, code: "BAD_REQUEST"}
            : url === "/json/messages" && init.method === "POST"
              ? {result: "success", id: 950}
              : {result: "success"};
    return {
        ok: answer.result === "success",
        status: answer.result === "success" ? 200 : 400,
        json: async () => answer,
    };
});

const ME = 3;
const IAGO = 5;
const DESDEMONA = 7;
const AGENT = 9;

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const field = find(".tx");
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
    go: find(".disc"),
    goc: find(".disc .cell"),
    ph: find(".ph"),
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const send = zrequire("armada/beta/send.js");
const hover = zrequire("armada/beta/hover.js");
const widgets = zrequire("armada/beta/widgets.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Shiva";
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Shiva"],
    [IAGO, "Iago"],
    [DESDEMONA, "Desdemona"],
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
    key: routing.tkey(10, "decide"),
    kind: "topic",
    stream_id: 10,
    topic: "decide",
    repo: false,
};
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
beta_state.state.P = cache;
hover.buildOver();
widgets.wireWidgets(find("main"), hover.msgOf);
feed.drawLane();
// A press is told from its own click by the time it went down, which the
// fake clock starts at 0.
clock.tick(1);

// A message with its widget, as the server stores it: the first
// submessage, from the sender, says what the widget is; each later one is
// a change from whoever made it.
let next_id = 300;
let next_sub = 7000;
const sub = (message_id, sender_id, data) => ({
    id: (next_sub += 1),
    message_id,
    sender_id,
    msg_type: "widget",
    content: JSON.stringify(data),
});
function arrive(sender_id, text, widget, changes = []) {
    next_id += 1;
    const id = next_id;
    const m = {
        id,
        type: "stream",
        stream_id: 10,
        subject: "decide",
        sender_id,
        timestamp: Math.floor(Date.now() / 1000),
        content: `<p>${text.replaceAll("\n", "<br>\n")}</p>`,
        reactions: [],
        submessages: [
            sub(id, sender_id, widget),
            ...changes.map(([who, data]) => sub(id, who, data)),
        ],
    };
    routing.addMsg(cache, m);
    feed.appendMessage(lane, m);
    return m;
}
// The server's event for one change.
function event(m, sender_id, data) {
    const s = sub(m.id, sender_id, data);
    widgets.onSubmessage({
        type: "submessage",
        msg_type: "widget",
        message_id: m.id,
        submessage_id: s.id,
        sender_id,
        content: s.content,
    });
}
const blockOf = (m) => lane.querySelector(`[data-part="wdg"][data-mid="${m.id}"]`);
// A row as a person reads it: whether it is mine (or done), its words, its count.
const rows = (m) =>
    [...blockOf(m).querySelectorAll("[data-key]")].map(
        (r) =>
            `${(r.getAttribute("aria-pressed") ?? r.getAttribute("aria-checked")) === "true" ? "x" : " "} ${r.textContent.trim()}`,
    );
const rowOf = (m, words) =>
    [...blockOf(m).querySelectorAll("[data-key]")].find((r) =>
        r.textContent.trim().startsWith(words),
    );
// A pointer press: the control answers on the press; the click that follows is its own.
function pressOn(target) {
    target.dispatchEvent(new dom.window.MouseEvent("pointerdown", {bubbles: true, button: 0}));
    target.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true, button: 0, detail: 1}));
}
const posted = () =>
    writes.filter((w) => w.url === "/json/submessage").map((w) => JSON.parse(w.body.content));
async function settle() {
    for (let i = 0; i < 20; i += 1) {
        await Promise.resolve();
    }
}
const POLL = {
    widget_type: "poll",
    extra_data: {question: "Which runtime first?", options: ["Claude Code", "Codex"]},
};

// Iago's poll as Desdemona left it: her option, a vote on Codex and on
// hers; Iago's own vote taken back, and one for an option that does not
// exist.
const iagosPoll = () =>
    arrive(IAGO, "/poll Which runtime first?\nClaude Code\nCodex", POLL, [
        [IAGO, {type: "vote", key: "canned,1", vote: 1}],
        [DESDEMONA, {type: "new_option", idx: 1, option: "Both"}],
        [DESDEMONA, {type: "vote", key: "canned,1", vote: 1}],
        [DESDEMONA, {type: "vote", key: "7,1", vote: 1}],
        [IAGO, {type: "vote", key: "canned,1", vote: -1}],
        [IAGO, {type: "vote", key: "9,9", vote: 1}],
    ]);

run_test("a poll is drawn from its submessages, in place of the words that made it", () => {
    const m = iagosPoll();
    const block = blockOf(m);
    assert.doesNotMatch(block.textContent, /\/poll/);
    assert.match(block.textContent, /Which runtime first\?/);
    assert.match(block.textContent, /1 voted/);
    assert.deepEqual(rows(m), ["  Claude Code", "  Codex1", "  Both1"]);
    // Only the author changes the question; Iago's poll is not mine.
    assert.equal(block.querySelector("button[data-w='title']"), null);
});

run_test(
    "my vote is whole in the frame of the press, sent, and counted once when it is confirmed",
    async () => {
        const m = iagosPoll();
        writes.length = 0;
        const row = rowOf(m, "Claude Code");
        pressOn(row);
        // At once: my box is ticked and the count is mine.
        assert.deepEqual(rows(m), ["x Claude Code1", "  Codex1", "  Both1"]);
        await settle();
        assert.deepEqual(posted(), [{type: "vote", key: "canned,0", vote: 1}]);
        assert.equal(writes[0].body.message_id, String(m.id));
        assert.equal(writes[0].body.msg_type, "widget");
        // The server's event for it: one vote, not two, and the same row.
        event(m, ME, {type: "vote", key: "canned,0", vote: 1});
        assert.deepEqual(rows(m), ["x Claude Code1", "  Codex1", "  Both1"]);
        assert.equal(rowOf(m, "Claude Code"), row);
        // Pressed again, from the keyboard: taken back.
        row.click();
        assert.deepEqual(rows(m), ["  Claude Code", "  Codex1", "  Both1"]);
        await settle();
        assert.deepEqual(posted().at(-1), {type: "vote", key: "canned,0", vote: -1});
        event(m, ME, {type: "vote", key: "canned,0", vote: -1});
        assert.deepEqual(rows(m), ["  Claude Code", "  Codex1", "  Both1"]);
    },
);

run_test("someone else's vote and option change the poll in place", () => {
    const m = iagosPoll();
    const codex = rowOf(m, "Codex");
    event(m, IAGO, {type: "vote", key: "canned,1", vote: 1});
    assert.equal(rowOf(m, "Codex"), codex);
    assert.deepEqual(rows(m), ["  Claude Code", "  Codex2", "  Both1"]);
    assert.match(blockOf(m).textContent, /2 voted/);
    assert.match(codex.getAttribute("aria-label"), /2 votes \(Desdemona, Iago\)/);
    event(m, IAGO, {type: "new_option", idx: 1, option: "Neither"});
    assert.deepEqual(rows(m), ["  Claude Code", "  Codex2", "  Both1", "  Neither"]);
    // The same event twice changes nothing.
    const again = cache.byId.get(m.id).submessages.at(-1);
    widgets.onSubmessage({
        type: "submessage",
        msg_type: "widget",
        message_id: m.id,
        submessage_id: again.id,
        sender_id: IAGO,
        content: again.content,
    });
    assert.deepEqual(rows(m), ["  Claude Code", "  Codex2", "  Both1", "  Neither"]);
});

run_test(
    "an option of mine is added from the keyboard; a refused vote is taken back and says why",
    async () => {
        const m = iagosPoll();
        const add = [...blockOf(m).querySelectorAll("button")].find(
            (b) => b.textContent === "Add an option",
        );
        add.click();
        const input = document.activeElement;
        assert.equal(input.tagName, "INPUT");
        input.value = "Ask the user";
        dom.press("Enter", {target: input});
        assert.deepEqual(rows(m).at(-1), "  Ask the user");
        assert.equal(document.activeElement, add);
        await settle();
        assert.deepEqual(posted().at(-1), {type: "new_option", idx: 1, option: "Ask the user"});
        // Esc gives an option up.
        add.click();
        document.activeElement.value = "never mind";
        dom.press("Escape", {target: document.activeElement});
        assert.equal(rows(m).length, 4);

        refuse = "You cannot vote on this poll";
        pressOn(rowOf(m, "Both"));
        assert.equal(rows(m)[2], "x Both2");
        await settle();
        assert.equal(rows(m)[2], "  Both1");
        assert.deepEqual(said.slice(-1), ["Not saved: You cannot vote on this poll"]);
        assert.deepEqual(sounds.slice(-1), ["nope"]);
        refuse = null;
    },
);

run_test("a to-do list: a tick from anyone, and mine, and a task added", async () => {
    const m = arrive(
        ME,
        "/todo Ship it\nPictures: boxes\nLightbox",
        {
            widget_type: "todo",
            extra_data: {
                task_list_title: "Ship it",
                tasks: [
                    {task: "Pictures", desc: "boxes"},
                    {task: "Lightbox", desc: ""},
                ],
            },
        },
        [[ME, {type: "strike", key: "0,canned"}]],
    );
    assert.deepEqual(rows(m), ["x Picturesboxes", "  Lightbox"]);
    assert.match(blockOf(m).textContent, /1 of 2 done/);
    // Iago ticks one; then takes it back.
    const lightbox = rowOf(m, "Lightbox");
    event(m, IAGO, {type: "strike", key: "1,canned"});
    assert.deepEqual(rows(m), ["x Picturesboxes", "x Lightbox"]);
    assert.equal(rowOf(m, "Lightbox"), lightbox);
    assert.match(blockOf(m).textContent, /2 of 2 done/);
    event(m, IAGO, {type: "strike", key: "1,canned"});
    assert.deepEqual(rows(m), ["x Picturesboxes", "  Lightbox"]);
    // Mine, from the keyboard, at once.
    writes.length = 0;
    lightbox.click();
    assert.deepEqual(rows(m), ["x Picturesboxes", "x Lightbox"]);
    await settle();
    assert.deepEqual(posted(), [{type: "strike", key: "1,canned"}]);
    // A task and what it means, added.
    [...blockOf(m).querySelectorAll("button")].find((b) => b.textContent === "Add a task").click();
    document.activeElement.value = "Artifacts: the scene";
    dom.press("Enter", {target: document.activeElement});
    assert.deepEqual(rows(m).at(-1), "  Artifactsthe scene");
    await settle();
    assert.deepEqual(posted().at(-1), {
        type: "new_task",
        key: 1,
        task: "Artifacts",
        desc: "the scene",
        completed: false,
    });
    // My list's title is mine to change.
    blockOf(m).querySelector("button[data-w='title']").click();
    document.activeElement.value = "Ship images and widgets";
    dom.press("Enter", {target: document.activeElement});
    assert.match(blockOf(m).textContent, /Ship images and widgets/);
    await settle();
    assert.deepEqual(posted().at(-1), {
        type: "new_task_list_title",
        title: "Ship images and widgets",
    });
});

run_test("my /poll shows as a poll at once, and Onix's poll is drawn as anyone's", async () => {
    field.textContent = "/poll Lunch?\nDosa\nThali";
    send.send({at: 0});
    const echo = cache.msgs.find((m) => m.raw === "/poll Lunch?\nDosa\nThali");
    assert.ok(blockOf(echo));
    assert.deepEqual(rows(echo), ["  Dosa", "  Thali"]);
    assert.match(blockOf(echo).textContent, /Lunch\?/);
    await settle();
    // Onix's poll, posted by its tool as /poll content.
    const m = arrive(AGENT, "/poll Which fix first?\nThe edit\nThe focus", {
        widget_type: "poll",
        extra_data: {question: "Which fix first?", options: ["The edit", "The focus"]},
    });
    assert.deepEqual(rows(m), ["  The edit", "  The focus"]);
    event(m, IAGO, {type: "vote", key: "canned,0", vote: 1});
    assert.deepEqual(rows(m), ["  The edit1", "  The focus"]);
    assert.doesNotMatch(lane.textContent, /does not draw yet/);
    assert.deepEqual(widgets.widgetFromText("/todo\n- a: b\n* c"), {
        widget_type: "todo",
        extra_data: {
            task_list_title: "",
            tasks: [
                {task: "a", desc: "b"},
                {task: "c", desc: ""},
            ],
        },
    });
    assert.equal(widgets.widgetFromText("/polling"), null);
});
