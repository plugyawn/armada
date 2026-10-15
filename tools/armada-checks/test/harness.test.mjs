// CheckContext's side of the health checks (lib/health.mjs): what its
// pages see of the server, planted through a stand-in for the browser's
// DevTools session, ends in `env_events`, which makes the run ENV-ERROR.

import assert from "node:assert/strict";
import {test} from "node:test";

import {BASE} from "../lib/browser.mjs";
import {CheckContext} from "../lib/harness.mjs";

const ME = 8;

// A page and its DevTools session; `bodies` answers
// Network.getResponseBody by request id.
function fake_browser(bodies = {}) {
    // The DevTools session's events, as CheckContext listens to them.
    const listeners = new Map();
    const client = {
        on(event, listener) {
            listeners.set(event, [...(listeners.get(event) ?? []), listener]);
        },
        emit(event, data) {
            for (const listener of listeners.get(event) ?? []) {
                listener(data);
            }
        },
        async send(method, params) {
            if (method === "Network.getResponseBody") {
                if (!(params.requestId in bodies)) {
                    throw new Error("No resource with given identifier found");
                }
                return {body: bodies[params.requestId]};
            }
            return {};
        },
    };
    const page = {
        setViewport: async () => undefined,
        createCDPSession: async () => client,
        evaluateOnNewDocument: async () => undefined,
        on: () => undefined,
        close: async () => undefined,
    };
    return {client, browser: {newPage: async () => page}};
}

function respond(client, requestId, status, url) {
    client.emit("Network.responseReceived", {
        requestId,
        response: {status, url: `${BASE}${url}`},
    });
    client.emit("Network.loadingFinished", {requestId});
}

test("a 429, a 5xx or a lost event queue from the server is an environment problem", async () => {
    const {client, browser} = fake_browser();
    const ctx = new CheckContext(browser, "planted", {user_id: ME});
    await ctx.init();
    respond(client, "1", 200, "/json/messages");
    respond(client, "2", 404, "/json/messages/9");
    respond(client, "3", 429, "/json/messages");
    respond(client, "4", 502, "/json/users/me");
    respond(client, "5", 400, "/json/events?queue_id=x");
    // The same problem twice is kept once.
    respond(client, "6", 429, "/json/messages");
    await ctx.close();
    assert.deepEqual(ctx.env_events, [
        "429 (rate limited) for /json/messages: someone else's traffic as this user",
        "502 from the server for /json/users/me",
        "400 for /json/events?queue_id=x: the server lost the page's event queue (Tornado restarted)",
    ]);
});

test("an error status the check answered itself is not", async () => {
    const {client, browser} = fake_browser();
    const ctx = new CheckContext(browser, "planted", {user_id: ME});
    await ctx.init();
    await ctx.fulfil({id: "fetch-1", network_id: "7"}, {result: "error"}, 503);
    respond(client, "7", 503, "/json/messages");
    await ctx.close();
    assert.deepEqual(ctx.env_events, []);
});

test("another person's event reaching the page is an environment problem", async () => {
    const events = (list) => JSON.stringify({result: "success", events: list});
    const {client, browser} = fake_browser({
        mine: events([
            {type: "heartbeat"},
            {type: "message", message: {sender_id: ME, display_recipient: "sandbox", subject: "x"}},
        ]),
        theirs: events([
            {type: "message", message: {sender_id: 9, display_recipient: "click", subject: "demo"}},
        ]),
        typing: events([{type: "typing", sender: {user_id: 10}}]),
        broken: "not json",
    });
    const ctx = new CheckContext(browser, "planted", {user_id: ME});
    await ctx.init();
    respond(client, "mine", 200, "/json/events?queue_id=q");
    respond(client, "theirs", 200, "/json/events?queue_id=q");
    respond(client, "typing", 200, "/json/events?queue_id=q");
    // A body the page took with it, and one that is not an event list.
    respond(client, "gone", 200, "/json/events?queue_id=q");
    respond(client, "broken", 200, "/json/events?queue_id=q");
    // Not an event response: its body is never read.
    respond(client, "other", 200, "/json/messages");
    await ctx.close();
    assert.deepEqual(ctx.env_events, [
        "a message in click > demo from user 9",
        "typing from user 10",
    ]);
});

test("at most ten problems are kept for one check", async () => {
    const {client, browser} = fake_browser();
    const ctx = new CheckContext(browser, "planted", {user_id: ME});
    await ctx.init();
    for (let index = 0; index < 15; index += 1) {
        respond(client, String(index), 500, `/json/messages/${index}`);
    }
    await ctx.close();
    assert.equal(ctx.env_events.length, 10);
});
