"use strict";

// What the interim beta asks the server for at load, beyond register
// (web/src/armada/beta/runs.js, scanLive; home.js, fetchMentions), with a
// real DOM and a stand-in server: the live runs are read in one request,
// the server's live state says which wait on a question, only the runs
// whose live state is gone or whose question's words are not known have
// their worktree's last word read, all in one request; and only the
// unread mentions the page does not hold are read, in one request, once.
// Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const RUNS = [41, 42, 43, 44];
const dom = real_dom.make_real_dom(
    `<div id="beta-data" data-live-run-ids="${JSON.stringify(RUNS)}" data-agent-user-ids="[9]"></div>`,
);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global("M", {T: {}});

const ME = 1;
const AGENT = 9;
const STREAM = 7;
const QUESTION =
    "<p>Onix needs your input to continue:</p><p><strong>Scope</strong> — Which files?</p>";
const now = Math.floor(Date.now() / 1000);
const question = {id: 120, sender_id: AGENT, content: QUESTION, timestamp: now - 30};
const chat = {id: 130, sender_id: ME, content: "<p>lunch?</p>", timestamp: now - 20};
const run = (id, topic, live) => ({
    id,
    topic,
    stream: "click",
    status: "running",
    session_kind: "shared",
    trigger_message_id: 100,
    date_created: now - 60,
    live,
});
const working = {status: "running", operation: {kind: "tool", label: "rg"}};
const asking = {status: "running", operation: {kind: "question", label: "waiting on you"}};

// The server: every request the page makes, and its answers.
const requests = [];
const connection = mock_esm("../../src/armada/beta/connection.js", {
    async api(method, path, params = {}) {
        requests.push([method, path, params]);
        if (path === "armada/runs") {
            return {
                armada_runs: [
                    run(41, "working", working),
                    run(42, "gone, asked last", null),
                    run(43, "asking", asking),
                    run(44, "gone, chatted last", null),
                ],
            };
        }
        if (path === "messages") {
            const all = [question, {...question, id: 121}, chat];
            return {messages: all.filter((m) => params.message_ids.includes(m.id))};
        }
        throw new Error(`no stand-in answer for ${path}`);
    },
});
// The nav, which loads each repository's topics once at load anyway (home.js,
// scanAll): each topic's newest message.
const drawn = [];
mock_esm("../../src/armada/beta/nav.js", {
    drawTrail(key) {
        drawn.push(key);
    },
    async loadTopics(sid) {
        topicsOf.set(sid, [
            {name: "working", max_id: 110},
            {name: "gone, asked last", max_id: 120},
            {name: "asking", max_id: 121},
            {name: "gone, chatted last", max_id: 130},
        ]);
    },
});

const {marks, state, streams, topicsOf} = zrequire("armada/beta/state.js");
const runs = zrequire("armada/beta/runs.js");
const home = zrequire("armada/beta/home.js");

state.ME = ME;
streams.set(STREAM, {stream_id: STREAM, name: "click"});
const key = (topic) => `s:${STREAM}:${topic}`;

async function settle() {
    for (let i = 0; i < 20; i += 1) {
        await Promise.resolve();
    }
    clock.tick(0);
}

run_test(
    "live runs: one request for the runs, one for the last words of those the live state cannot say",
    async () => {
        await runs.scanLive();
        await settle();
        assert.deepEqual(
            requests.map(([method, path]) => `${method} ${path}`),
            ["GET armada/runs", "GET messages"],
        );
        assert.deepEqual(requests[0][2], {run_ids: RUNS});
        // The run that works by its live state needs nothing more; the one that
        // waits by its live state needs its question's words.
        assert.deepEqual(requests[1][2].message_ids, [120, 121, 130]);
        assert.equal(marks.get(key("working")), "live");
        assert.equal(marks.get(key("asking")), "needs");
        assert.equal(
            marks.get(key("gone, asked last")),
            "needs",
            "its last word is Onix's question",
        );
        assert.equal(marks.get(key("gone, chatted last")), "live");
        const asked = runs.runsSeen.get(key("asking"))[0];
        assert.equal(asked.question, "Which files?");
        assert.equal(asked.askedAt, question.timestamp);
    },
);

run_test("mentions: only the unread ones the page does not hold, newest first, once", async () => {
    requests.length = 0;
    home.mentions.set(205, {id: 205});
    state.unreadMentions = [201, 205, 203];
    await home.fetchMentions();
    assert.deepEqual(requests, [
        ["GET", "messages", {message_ids: [203, 201], apply_markdown: true, client_gravatar: true}],
    ]);
    await home.fetchMentions();
    assert.equal(requests.length, 1, "events keep them after that");
    state.unreadMentions = [];
    await home.fetchMentions();
    assert.equal(requests.length, 1, "no unread mention, no request");
});

run_test("the stand-in server answers only what the page asks here", async () => {
    await assert.rejects(connection.api("GET", "users/me"), /no stand-in answer for users\/me/);
});
