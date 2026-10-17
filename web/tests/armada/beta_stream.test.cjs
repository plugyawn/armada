"use strict";

// Onix's words as they stream into the interim beta
// (web/src/armada/beta/agent_turn.js), with a real DOM: the agent_stream
// events append, one animation frame writes what has arrived, a block that
// has ended is not written again, the saved copies the server sends while
// the words stream do not take them back, and when the run ends the
// server's rendering is the answer, in the same row. Nothing reads layout
// on the way. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(`
<div id="beta-data"></div>
<nav><div class="fixed"></div><div class="scroll"></div></nav>
<main>
  <header class="hd"></header>
  <div class="band"><div class="lane">
    <div id="run" class="off"><span id="run-st"></span><span id="clock"></span><span id="act"></span><button id="stop">Stop</button></div>
    <div id="ty" class="off"></div><button id="jump" class="off">Jump to latest</button>
  </div></div>
  <div id="pane"><div id="col"></div></div>
  <aside id="side"><div id="outline"></div></aside>
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
set_global("innerWidth", 1440);
set_global("innerHeight", 900);
set_global("requestAnimationFrame", (f) => setTimeout(f, 16));
// The rows a live answer's observer watches, kept so a test can tell it
// that a row grew, as the browser does after it lays the row out.
const watched = new Set();
let grow = null;
set_global(
    "ResizeObserver",
    class {
        constructor(callback) {
            this.callback = callback;
        }
        observe(element) {
            if (element.matches?.("section")) {
                grow = this.callback;
                watched.add(element);
            }
        }
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
const noop = () => {};
const releases = [];
set_global("M", {
    T: {},
    SLOW: 1,
    reduced: () => false,
    release(_element, by) {
        releases.push(by);
    },
    appear: () => ({finished: Promise.resolve()}),
    fade: noop,
    flip: noop,
    halt: noop,
    land: noop,
    figure: noop,
    current: () => [{}, {}],
    rest: () => ({top: 0, left: 0}),
    say: noop,
    refuse: noop,
    leave: noop,
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fitLists", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global("wk", () => `<svg class="mk wk"></svg>`);
set_global("mk", (name) => `<svg class="mk ${name}"></svg>`);
set_global("smk", (state) => `<span class="mk sm" data-s="${state}"></span>`);
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);
// The stand-in server: the live runs it holds answer GET armada/runs.
const runs_asked = [];
let live_runs = [];
set_global("fetch", async (url) => {
    if (url.startsWith("/json/armada/runs")) {
        runs_asked.push(url);
    }
    return {
        ok: true,
        status: 200,
        json: async () => ({result: "success", msg: "", messages: [], armada_runs: live_runs}),
    };
});

const ME = 3;
const AGENT = 9;
const RUN = 40;
const T0 = Math.floor(Date.now() / 1000);

const find = (selector) => document.querySelector(selector);
const lane = find("#col");
const pane = find("#pane");
// The feed's layout, counted: nothing that writes the stream may read it.
let layout_reads = 0;
const read_layout = () => {
    layout_reads += 1;
    return 0;
};
for (const name of ["scrollHeight", "clientHeight", "offsetHeight", "scrollTop"]) {
    Object.defineProperty(pane, name, {configurable: true, get: read_layout, set() {}});
}
// KaTeX's sheet, which the page fetches the first time it shows a formula.
mock_esm("katex/dist/katex.min.css");
mock_esm("../../src/armada/beta/frame.js", {
    main: find("main"),
    feed: pane,
    lane,
    nav: find("nav"),
    navFixed: find("nav .fixed"),
    navScroll: find("nav .scroll"),
    side: find("#side"),
    outline: find("#outline"),
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
});

const beta_state = zrequire("armada/beta/state.js");
const routing = zrequire("armada/beta/routing.js");
const feed = zrequire("armada/beta/feed.js");
const events = zrequire("armada/beta/events.js");
const agent_turn = zrequire("armada/beta/agent_turn.js");
const hover = zrequire("armada/beta/hover.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Shiva";
beta_state.state.tab = "Chat";
beta_state.state.hd = find("main > header.hd");
beta_state.state.realm = {allowEdit: true, editLimit: null, deleteLimit: null};
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Shiva"],
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
// #click has its repository, as any channel with runs does.
beta_state.streams.set(10, {stream_id: 10, name: "click"});
beta_state.repoStreams.add(10);
const place = {
    key: routing.tkey(10, "option groups"),
    kind: "topic",
    stream_id: 10,
    topic: "option groups",
    repo: true,
};
const message = (id, sender_id, html) => ({
    id,
    type: "stream",
    stream_id: 10,
    display_recipient: "click",
    subject: "option groups",
    sender_id,
    timestamp: T0,
    content: html,
    reactions: [],
});
beta_state.state.place = place;
const cache = routing.cacheOf(place);
cache.loaded = true;
cache.oldest = true;
beta_state.state.P = cache;
hover.buildOver();
feed.drawLane();

const frame = () => clock.tick(16);
const run_event = (status, answer_id) =>
    events.onRun({
        type: "agent_run",
        run_id: RUN,
        event_type: status,
        status,
        trigger_message_id: answer_id - 1,
        result_message_id: answer_id,
        stream: "click",
        topic: "option groups",
        current_activity: "",
        tool_count: 0,
        run_date_created: T0,
        date_created: T0,
    });
// Onix's row, as a run starts: my request, its placeholder, the run.
function start_run(answer_id) {
    events.onMessage({
        message: message(answer_id - 1, ME, "<p>@Onix explain</p>"),
        flags: ["read"],
    });
    events.onMessage({message: message(answer_id, AGENT, "<p>· Working</p>"), flags: ["read"]});
    run_event("running", answer_id);
    frame();
    return lane.querySelector(`[data-mid="${answer_id}"]`).closest("section");
}
function streamer(answer_id, item = 0) {
    let offset = 0;
    return (text) => {
        agent_turn.onAgentStream(
            {type: "agent_stream", run_id: RUN, message_id: answer_id, item, offset, text},
            0,
        );
        offset += text.length;
    };
}
const words = (row) => row.querySelector(".ans").textContent.replaceAll(/\s+/g, " ").trim();
const saved = (answer_id, html) =>
    events.onUpdate({message_id: answer_id, rendered_content: html, rendering_only: true}, 0);

run_test("words arrive as they stream, one write a frame, in the row the run started", async () => {
    const row = start_run(101);
    const say = streamer(101);
    const writes = [];
    new dom.window.MutationObserver((records) => writes.push(records.length)).observe(row, {
        childList: true,
        subtree: true,
        characterData: true,
    });
    // A mutation observer hears of each task's writes once, after it.
    const heard = async () => {
        await Promise.resolve();
        await Promise.resolve();
    };
    say("The parser reads");
    say(" each group");
    say(" in order.");
    await heard();
    // Nothing is written by an event ...
    assert.deepEqual(writes, []);
    assert.doesNotMatch(row.textContent, /parser/);
    frame();
    await heard();
    // ... and one frame writes all three.
    assert.equal(writes.length, 1);
    assert.equal(words(row), "The parser reads each group in order.");
    say(" It keeps");
    say(" the first value.");
    frame();
    await heard();
    assert.equal(writes.length, 2);
    assert.equal(words(row), "The parser reads each group in order. It keeps the first value.");
    assert.equal(lane.querySelector('[data-mid="101"]').closest("section"), row);
});

run_test(
    "a paragraph that has ended is not written again, and the mark rides the new words",
    () => {
        const row = start_run(111);
        const say = streamer(111);
        say("**What changed**\n\nThe parser keeps the first value.\n\nIt reads");
        frame();
        const [heading, first, second] = row.querySelectorAll(".ans > *");
        assert.equal(heading.textContent, "What changed");
        assert.equal(first.textContent, "The parser keeps the first value.");
        const mark = row.querySelector(".head");
        assert.ok(second.contains(mark));
        say(" each group");
        frame();
        say(" in order.");
        frame();
        // The same elements: the ended paragraph untouched, the open one grown,
        // and the working mark the same element, still at the end of the words.
        assert.deepEqual([...row.querySelectorAll(".ans > *")], [heading, first, second]);
        assert.equal(first.textContent, "The parser keeps the first value.");
        assert.equal(second.textContent, "It reads each group in order.");
        assert.equal(row.querySelector(".head"), mark);
        assert.equal(second.lastChild, mark);
    },
);

run_test("code streams into one block, written as text while it is open", () => {
    const row = start_run(121);
    const say = streamer(121);
    say("Run this:\n\n```bash\npython -m pytest");
    frame();
    const block = row.querySelector("pre");
    assert.match(block.textContent, /python -m pytest/);
    say(" tests/\nruff check");
    frame();
    say("\n```\n\nBoth pass.");
    frame();
    assert.equal(row.querySelector("pre"), block);
    assert.match(block.textContent, /python -m pytest tests\/\nruff check/);
    assert.doesNotMatch(block.textContent, /```/);
    assert.equal(
        [...row.querySelectorAll(".ans")].map((x) => x.textContent.trim()).join(" | "),
        "Run this: | Both pass.",
    );
});

run_test("a heading and a table stream as what they are", () => {
    const row = start_run(126);
    const say = streamer(126);
    say("## Options\n\n| Flag | Default |\n|---|---:|\n| --verbose | off");
    frame();
    assert.equal(row.querySelector("h3").textContent, "Options");
    const cells = () => [...row.querySelectorAll("td")].map((x) => x.textContent);
    // The row being written is a row already.
    assert.deepEqual(cells(), ["--verbose", "off"]);
    say(" |\n| --quiet | on |\n\nThat is all.");
    frame();
    assert.deepEqual(cells(), ["--verbose", "off", "--quiet", "on"]);
    assert.equal(row.querySelectorAll("table").length, 1);
    assert.match(row.textContent, /That is all\./);
});

run_test("a picture among words is drawn once, however often its block is written", () => {
    const row = start_run(127);
    const say = streamer(127);
    say("The page now:\n[shot.png](/user_uploads/2/ab/shot.png");
    frame();
    say(")\nIt loads");
    frame();
    say(" in one go.");
    frame();
    assert.equal(row.querySelectorAll("img").length, 1);
    // The picture links its file; it gets its source as it comes near (media.js).
    assert.equal(
        row.querySelectorAll("img")[0].closest("a").getAttribute("href"),
        "/user_uploads/2/ab/shot.png",
    );
    assert.match(row.textContent, /The page now:.*It loads in one go\./s);
    say("\n\nDone.");
    frame();
    assert.equal(row.querySelectorAll("img").length, 1);
    assert.equal((row.textContent.match(/The page now:/g) ?? []).length, 1);
    assert.match(row.textContent, /Done\./);
});

run_test("the saved message does not take back newer words; the end is the server's", () => {
    const row = start_run(131);
    const say = streamer(131);
    say("The parser keeps the first value");
    frame();
    // The server saves what it had a moment ago, while more streams.
    saved(131, "<p>The parser keeps</p>");
    say(", so --verbose won.");
    frame();
    assert.equal(words(row), "The parser keeps the first value, so --verbose won.");
    // The run ends: its last save, then the run's end. The server's
    // rendering is the answer, and the row is the one the run started.
    saved(131, "<p>The parser keeps the first value, so <code>--verbose</code> won.</p>");
    run_event("done", 131);
    frame();
    assert.equal(lane.querySelector('[data-mid="131"]').closest("section"), row);
    assert.equal(row.querySelector(".ans code").textContent, "--verbose");
    assert.equal(words(row), "The parser keeps the first value, so --verbose won.");
    assert.equal(row.querySelector(".head:not(.gone)"), null);
});

run_test("words that were missed leave the answer to the saved message", () => {
    const row = start_run(141);
    const say = streamer(141);
    say("First words");
    frame();
    // An event never came: the next one starts past what is here.
    agent_turn.onAgentStream(
        {type: "agent_stream", run_id: RUN, message_id: 141, item: 0, offset: 40, text: "later"},
        0,
    );
    frame();
    assert.equal(words(row), "First words");
    saved(141, "<p>First words, then the rest that was missed, and later</p>");
    frame();
    assert.equal(words(row), "First words, then the rest that was missed, and later");
    // A new item starts the stream again.
    const again = streamer(141, 1);
    again("Second item.");
    frame();
    assert.equal(words(row), "Second item.");
});

run_test(
    "a page that opens mid-answer joins the words it gets to the run's saved text",
    async () => {
        const row = start_run(146);
        runs_asked.length = 0;
        // The page opened after the item began: its first event is past the start.
        const later = (offset, text) =>
            agent_turn.onAgentStream(
                {type: "agent_stream", run_id: RUN, message_id: 146, item: 0, offset, text},
                0,
            );
        live_runs = [
            {
                id: RUN,
                live: {stream: {message_id: 146, item: 0, text: "The parser keeps the first"}},
            },
        ];
        later(17, "the first value");
        later(32, ", so --verbose won.");
        for (let i = 0; i < 20; i += 1) {
            await Promise.resolve();
        }
        frame();
        assert.equal(runs_asked.length, 1);
        assert.equal(words(row), "The parser keeps the first value, so --verbose won.");
        // The rest streams on from there.
        later(51, " Fixed.");
        frame();
        assert.equal(words(row), "The parser keeps the first value, so --verbose won. Fixed.");

        // The server's copy can run ahead of its events (words it held back
        // to pace the stream): an event that overlaps it adds only the rest.
        const late = start_run(148);
        const more = (offset, text) =>
            agent_turn.onAgentStream(
                {type: "agent_stream", run_id: RUN, message_id: 148, item: 0, offset, text},
                0,
            );
        live_runs = [{id: RUN, live: {stream: {message_id: 148, item: 0, text: "One two three"}}}];
        more(4, "two");
        for (let i = 0; i < 20; i += 1) {
            await Promise.resolve();
        }
        frame();
        assert.equal(words(late), "One two three");
        more(7, " three four");
        frame();
        assert.equal(words(late), "One two three four");
        live_runs = [];
    },
);

run_test("words for a place that is not open are written when its row is drawn", () => {
    const say = streamer(151);
    events.onMessage({message: message(150, ME, "<p>@Onix explain</p>"), flags: ["read"]});
    events.onMessage({message: message(151, AGENT, "<p>· Working</p>"), flags: ["read"]});
    run_event("running", 151);
    // The person goes elsewhere; the words go on arriving.
    const elsewhere = {
        key: routing.tkey(10, "elsewhere"),
        kind: "topic",
        stream_id: 10,
        topic: "elsewhere",
    };
    beta_state.state.place = elsewhere;
    beta_state.state.P = routing.cacheOf(elsewhere);
    say("Written while ");
    say("nobody looked.");
    frame();
    // Back: the row is drawn again, with every word.
    beta_state.state.place = place;
    beta_state.state.P = cache;
    feed.drawLane();
    const row = lane.querySelector('[data-mid="151"]').closest("section");
    assert.equal(words(row), "Written while nobody looked.");
});

run_test("nothing on the way reads layout; a grown row is followed from its observer", () => {
    const row = start_run(161);
    const say = streamer(161);
    layout_reads = 0;
    for (let i = 0; i < 20; i += 1) {
        say(`word${i} `);
        if (i % 4 === 3) {
            frame();
        }
    }
    saved(161, "<p>word0 word1</p>");
    frame();
    assert.equal(layout_reads, 0);
    assert.ok(watched.has(row));
    // The browser lays the row out taller: the conversation follows it.
    releases.length = 0;
    grow([{target: row, borderBoxSize: [{blockSize: 40}]}]);
    grow([{target: row, borderBoxSize: [{blockSize: 64}]}]);
    assert.deepEqual(releases.at(-1), {y: 24});
});

run_test("the band says what the model is thinking about until its plan moves on", () => {
    start_run(171);
    const act = document.querySelector("#act");
    const step = (words, status) => ({step: words, status});
    const reading = [step("Read the parser", "in_progress"), step("Change it", "pending")];
    const changing = [step("Read the parser", "completed"), step("Change it", "in_progress")];
    const run_says = (fields) => {
        events.onRun({
            type: "agent_run",
            run_id: RUN,
            event_type: "transcript",
            status: "running",
            trigger_message_id: 170,
            result_message_id: 171,
            stream: "click",
            topic: "option groups",
            current_activity: "",
            tool_count: 0,
            plan: null,
            overview: null,
            run_date_created: T0,
            date_created: T0,
            ...fields,
        });
        frame();
        return act.textContent;
    };
    assert.equal(run_says({plan: reading, current_activity: "rg parse_groups"}), "rg parse_groups");
    // The model's reasoning title, as the server pushes it.
    const title = "Exploring dynamic programming options";
    assert.equal(run_says({overview: title, current_activity: title}), title);
    // A command, and the same step again, leave it.
    assert.equal(run_says({current_activity: "pytest tests/"}), title);
    assert.equal(run_says({plan: reading, current_activity: "Plan"}), title);
    // The next step makes it stale, as the server's card says after a reload.
    assert.equal(run_says({plan: changing, current_activity: "git diff"}), "git diff");
});

run_test(
    "display maths: in a person's bubble, on the page for Onix, after KaTeX's sheet",
    async () => {
        const root = document.documentElement;
        // Nothing so far had maths: the sheet was never fetched.
        assert.equal(root.dataset.katex, undefined);
        const display_maths =
            '<p><span class="katex-display"><span class="katex">' +
            '<span class="katex-mathml"><math display="block"><semantics><mrow><mover>' +
            '<mi>h</mi><mo>^</mo></mover></mrow><annotation encoding="application/x-tex">' +
            "\\hat{h}</annotation></semantics></math></span>" +
            '<span class="katex-html" aria-hidden="true">h^</span></span></span></p>';
        const part_of = (id) =>
            lane.querySelector(`[data-mid="${id}"] .katex-display`).closest("[data-part]").dataset
                .part;
        events.onMessage({
            message: message(191, ME, "<p>The update:</p>" + display_maths),
            flags: ["read"],
        });
        events.onMessage({
            message: message(192, AGENT, "<p>It tries to learn:</p>" + display_maths),
            flags: ["read"],
        });
        frame();
        // A person's formula stays in their bubble; Onix's stands on the page,
        // a part of its own with no block behind it.
        assert.equal(part_of(191), "b");
        assert.equal(part_of(192), "math");
        for (let i = 0; i < 10; i += 1) {
            await Promise.resolve();
        }
        // The sheet is in: formulas show (beta.css hides them until then).
        assert.equal(root.dataset.katex, "");
    },
);

run_test("a live turn's footer says what the model is thinking about", () => {
    const run_id = RUN + 7;
    events.onMessage({message: message(210, ME, "<p>@Onix check the branch</p>"), flags: ["read"]});
    events.onMessage({message: message(211, AGENT, "<p>· Working</p>"), flags: ["read"]});
    const run_says = (fields) =>
        events.onRun({
            type: "agent_run",
            run_id,
            event_type: "transcript",
            status: "running",
            trigger_message_id: 210,
            result_message_id: 211,
            stream: "click",
            topic: "option groups",
            current_activity: "",
            tool_count: 0,
            plan: null,
            overview: null,
            run_date_created: T0,
            date_created: T0,
            ...fields,
        });
    run_says({});
    frame();
    const row = lane.querySelector('[data-mid="211"]').closest("section");
    const foot = () => row.querySelector(".line.foot").textContent.trim();
    assert.equal(foot(), "Working");
    // The model's own title, as the server pushes it, written as text.
    run_says({overview: "Checking branch <ancestry>", tool_count: 2});
    frame();
    assert.equal(foot(), "Checking branch <ancestry> · 2 tools");
    assert.equal(row.querySelector(".line.foot ancestry"), null);
    run_says({status: "done", event_type: "done", tool_count: 2});
    frame();
    assert.match(foot(), /^Ran /);
});

run_test("a thread's line says what Onix is thinking about in its live reply", () => {
    const run_id = RUN + 8;
    events.onMessage({
        message: message(220, ME, "<p>Can you check the build?</p>"),
        flags: ["read"],
    });
    events.onMessage({
        message: {...message(221, AGENT, "<p>· Working</p>"), thread_root_message_id: 220},
        flags: ["read"],
    });
    const run_says = (fields) =>
        events.onRun({
            type: "agent_run",
            run_id,
            event_type: "transcript",
            status: "running",
            trigger_message_id: 220,
            result_message_id: 221,
            stream: "click",
            topic: "option groups",
            current_activity: "",
            tool_count: 0,
            plan: null,
            overview: null,
            run_date_created: T0,
            date_created: T0,
            ...fields,
        });
    run_says({});
    frame();
    const line = () =>
        lane.querySelector('[data-mid="220"]').closest("section").querySelector(".tl .sn")
            .textContent;
    assert.equal(line(), "Onix: working");
    run_says({overview: "Reading the plugin contract"});
    frame();
    assert.equal(line(), "Onix: Reading the plugin contract");
});
