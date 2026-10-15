"use strict";

// The interim beta's connection rules (web/src/armada/beta/link.js), on a
// fake clock against a fake server: the incident of 15 October (a dead
// queue and a refused register, asked again at once, forever) and each
// recovery path of resilience.md R1. Deleted with web/src/armada/beta/.

const assert = require("node:assert/strict");

const {zrequire} = require("../lib/namespace.cjs");
const {run_test} = require("../lib/test.cjs");

const beta_link = zrequire("armada/beta/link.js");

const MINUTE = 60000;

// Lets every promise the link is waiting on run. The test runner fakes
// every timer (web/tests/lib/namespace.cjs), so this yields to the
// microtask queue only, and often enough for the longest chain here.
async function settle() {
    for (let i = 0; i < 300; i += 1) {
        await Promise.resolve();
    }
}

function make_clock() {
    let now = 0;
    let next_id = 1;
    const timers = new Map();
    return {
        now: () => now,
        setTimeout(f, ms) {
            next_id += 1;
            timers.set(next_id, {at: now + ms, f});
            return next_id;
        },
        clearTimeout(id) {
            timers.delete(id);
        },
        async advance(ms) {
            const end = now + ms;
            await settle();
            // A link that asks again without waiting fires timers at one
            // instant forever: fail rather than hang.
            for (let fired = 0; ; fired += 1) {
                assert.ok(fired < 5000, `a storm at ${now} ms`);
                const due = [...timers].filter(([, t]) => t.at <= end);
                if (due.length === 0) {
                    break;
                }
                due.sort(([a, x], [b, y]) => x.at - y.at || a - b);
                const [id, timer] = due[0];
                timers.delete(id);
                now = timer.at;
                timer.f();
                await settle();
            }
            now = end;
            await settle();
        },
    };
}

const hang = () => "hang";
const success = (body = {}) => ({body: {result: "success", msg: "", ...body}});
const failure = (status, code, msg = "") => ({status, body: {result: "error", code, msg}});
const registered = (body = {}) =>
    success({
        queue_id: "q2",
        last_event_id: -1,
        user_id: 10,
        zulip_version: "12.0",
        zulip_merge_base: "abc",
        ...body,
    });

// A link whose server answers each request with what `routes` returns for
// it: an answer, "hang" (no answer until the request is aborted) or "net"
// (the network fails).
function make_link(routes, options = {}) {
    const clock = make_clock();
    const requests = [];
    const seen = {events: [], statuses: [], tokens: [], resyncs: 0};
    const env = {hidden: false, online: true};
    const state = {ME: 10, queue: "q1", lastEvent: 5, ...options.state};
    function fetch(url, init) {
        const parsed = new URL(url, "https://armada.test");
        const request = {
            method: init.method,
            path: parsed.pathname.replace("/json/", ""),
            query: Object.fromEntries(parsed.searchParams),
            token: init.headers["X-CSRFToken"],
            keepalive: init.keepalive,
            at: clock.now(),
        };
        requests.push(request);
        const answer = routes(request);
        if (answer === "hang") {
            return new Promise((_resolve, reject) => {
                init.signal.addEventListener("abort", () => {
                    reject(new Error("aborted"));
                });
            });
        }
        if (answer === "net") {
            return Promise.reject(new TypeError("Failed to fetch"));
        }
        return Promise.resolve({
            status: answer.status ?? 200,
            headers: {
                get: (name) => (name === "Retry-After" ? (answer.retry_after ?? null) : null),
            },
            async json() {
                if (answer.body === undefined) {
                    throw new SyntaxError("Unexpected token <");
                }
                return answer.body;
            },
        });
    }
    const link = beta_link.createLink({
        state,
        csrf: "token-1",
        fetch,
        now: clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        random: options.random ?? (() => 0.5),
        hidden: () => env.hidden,
        online: () => env.online,
        registerParams: () => ({event_types: ["message"]}),
        onRegister(r) {
            state.ME = r.user_id;
        },
        onEvent(event) {
            seen.events.push(event.id);
        },
        onResync() {
            seen.resyncs += 1;
        },
        onStatus(status) {
            seen.statuses.push(status);
        },
        onToken(token) {
            seen.tokens.push(token);
        },
    });
    const of = (path) => requests.filter((r) => r.path === path);
    return {link, clock, requests, seen, env, state, of};
}

run_test("backoff has equal jitter and a cap that depends on visibility", () => {
    const low = () => 0;
    const high = () => 1;
    assert.equal(beta_link.backoffMs(0, false, low), 500);
    assert.equal(beta_link.backoffMs(0, false, high), 1000);
    assert.equal(beta_link.backoffMs(3, false, high), 8000);
    assert.equal(beta_link.backoffMs(10, false, low), beta_link.CAP_VISIBLE_MS / 2);
    assert.equal(beta_link.backoffMs(10, false, high), beta_link.CAP_VISIBLE_MS);
    assert.equal(beta_link.backoffMs(10, true, high), beta_link.CAP_HIDDEN_MS);
});

run_test("the incident: a dead queue and a refused register", async () => {
    // The page's CSRF token is stale and the session cannot mend it; the
    // server refuses every register for ten minutes. The page used to
    // ask at once, forever, until the rate limit locked every tab out.
    for (const random of [() => 0, () => 0.5, () => 0.999]) {
        const {link, clock, of, seen} = make_link(
            (r) => {
                if (r.path === "events") {
                    return failure(400, "BAD_EVENT_QUEUE_ID", "Bad event queue ID: q1");
                }
                if (r.path === "register") {
                    return failure(403, "CSRF_FAILED", "CSRF error: CSRF token incorrect");
                }
                assert.equal(r.path, "armada/session");
                return success({user_id: 10, csrf_token: "token-1"});
            },
            {random},
        );
        void link.run();
        await clock.advance(10 * MINUTE);
        const at = of("register").map((r) => r.at);
        assert.ok(at.filter((t) => t < 3 * MINUTE).length <= 10, `registers in 3 min: ${at}`);
        for (let i = 1; i < at.length; i += 1) {
            assert.ok(at[i] - at[i - 1] >= beta_link.REGISTER_GAP_MS);
            if (at[i - 1] >= 3 * MINUTE) {
                assert.ok(at[i] - at[i - 1] >= beta_link.REGISTER_SLOW_GAP_MS);
            }
        }
        // Each register asked the session once, and repeated nothing:
        // its token was not new.
        assert.equal(of("armada/session").length, at.length);
        assert.equal(of("users/me").length, 0);
        assert.equal(of("events").length, 1);
        assert.deepEqual(seen.statuses, ["reconnecting", "offline"]);
        assert.equal(seen.resyncs, 0);
    }
});

run_test("a lost queue is registered again once, then the page resyncs", async () => {
    const {link, clock, of, seen, state} = make_link((r) => {
        if (r.path === "register") {
            return registered();
        }
        if (r.query.queue_id === "q1") {
            return failure(400, "BAD_REQUEST", "An event newer than 5 has already been pruned!");
        }
        return r.query.last_event_id === "-1" ? success({events: [{id: 0}, {id: 1}]}) : "hang";
    });
    void link.run();
    await clock.advance(10000);
    assert.equal(of("register").length, 1);
    assert.equal(seen.resyncs, 1);
    assert.deepEqual(seen.events, [0, 1]);
    assert.equal(state.lastEvent, 1);
    assert.equal(state.queue, "q2");
    assert.equal(link.stats.queuesLost, 1);
    assert.deepEqual(seen.statuses, []);
});

run_test("register: one in flight, and only for the same person", async () => {
    let user_id = 10;
    const {link, clock, of, seen} = make_link(() => registered({user_id}));
    const [a, b] = [link.register(), link.register()];
    assert.equal(await a, await b);
    assert.equal(of("register").length, 1);
    assert.equal(link.isNewBuild({zulip_version: "12.0", zulip_merge_base: "abc"}), false);
    assert.equal(link.isNewBuild({zulip_version: "12.1", zulip_merge_base: "abd"}), true);

    user_id = 11;
    await assert.rejects(link.register(), {stopped: true});
    assert.equal(link.stopped, "other_user");
    assert.equal(link.otherUserId, 11);
    // A stopped link asks nothing more.
    await assert.rejects(link.api("GET", "messages"), {message: "Signed in as someone else"});
    assert.equal(of("messages").length, 0);
    await clock.advance(MINUTE);
    assert.deepEqual(seen.statuses, ["other_user"]);
});

run_test("a register with no person in it is signed out", async () => {
    const {link} = make_link(() => registered({user_id: null}), {state: {ME: 0}});
    assert.equal(link.isNewBuild({zulip_version: "12.0", zulip_merge_base: "abc"}), false);
    await assert.rejects(link.connect(), {message: "Signed out"});
    assert.equal(link.stopped, "signed_out");
});

run_test("at load, register waits out a passing failure and throws a lasting one", async () => {
    let answers = ["net", {status: 502}, registered()];
    let page = make_link(() => answers.shift(), {state: {ME: 0, queue: null}});
    const connected = page.link.connect();
    await page.clock.advance(20000);
    assert.equal((await connected).queue_id, "q2");
    assert.equal(page.state.queue, "q2");
    assert.equal(page.of("register").length, 3);

    answers = [failure(400, "BAD_REQUEST", "Invalid event type")];
    page = make_link(() => answers.shift(), {state: {ME: 0, queue: null}});
    await assert.rejects(page.link.connect(), {message: "Invalid event type"});
});

run_test("a token refused for CSRF is mended from the session once", async () => {
    const {link, of, seen} = make_link((r) => {
        if (r.path === "armada/session") {
            return success({user_id: 10, csrf_token: "token-2"});
        }
        return r.method === "GET" || r.token === "token-2"
            ? success({id: 7})
            : failure(403, "CSRF_FAILED", "CSRF error");
    });
    assert.deepEqual(await link.api("POST", "messages", {content: "hi"}), {
        result: "success",
        msg: "",
        id: 7,
    });
    assert.deepEqual(
        of("messages").map((r) => r.token),
        ["token-1", "token-2"],
    );
    assert.equal(link.stats.csrfRefreshes, 1);
    assert.deepEqual(seen.tokens, ["token-2"]);
    // A GET carries no token.
    await link.api("GET", "messages", {anchor: "newest", narrow: []});
    assert.equal(of("messages").at(-1).token, undefined);
    assert.deepEqual(of("messages").at(-1).query, {anchor: "newest", narrow: "[]"});
});

run_test("a session that is someone else's stops the page", async () => {
    const {link, of} = make_link((r) =>
        r.path === "armada/session"
            ? success({user_id: 11, csrf_token: "token-2"})
            : failure(403, "CSRF_FAILED", "CSRF error"),
    );
    await assert.rejects(link.api("POST", "messages"), {stopped: true});
    assert.equal(link.stopped, "other_user");
    assert.equal(of("messages").length, 1);
});

run_test("a session that cannot be read leaves the refusal as it was", async () => {
    const {link} = make_link((r) =>
        r.path === "armada/session" ? "net" : failure(403, "CSRF_FAILED", "CSRF error"),
    );
    await assert.rejects(link.api("POST", "messages"), {code: "CSRF_FAILED", status: 403});
});

run_test("a 429 holds every request until Retry-After", async () => {
    const answers = [
        {...failure(429, "RATE_LIMIT_HIT"), retry_after: "5"},
        {status: 429, body: {result: "error", code: "RATE_LIMIT_HIT", msg: "", "retry-after": 2}},
        success({messages: []}),
    ];
    const {link, clock, of} = make_link(
        (r) => (r.path === "messages" ? answers.shift() : success()),
        {random: () => 0},
    );
    const first = link.api("GET", "messages");
    await clock.advance(1000);
    const second = link.api("GET", "users");
    await clock.advance(30000);
    assert.deepEqual(await first, {result: "success", msg: "", messages: []});
    await second;
    // 5 s from the header; then 2 s from the body, which also held the
    // request that was waiting for the first hold to end.
    assert.deepEqual(
        of("messages").map((r) => r.at),
        [0, 5000, 7000],
    );
    assert.deepEqual(
        of("users").map((r) => r.at),
        [7000],
    );
    assert.equal(link.stats.rateLimited, 2);
});

run_test("a 429 is repeated twice at most, and never for the loop's own requests", async () => {
    const {link, clock, of} = make_link(() => ({status: 429}), {random: () => 0});
    await assert.rejects(link.api("GET", "events", {}, {once: true}), {
        status: 429,
        message: "Too many requests",
    });
    assert.equal(of("events").length, 1);
    const repeated = assert.rejects(link.api("GET", "messages"), {status: 429});
    await clock.advance(10000);
    await repeated;
    assert.deepEqual(
        of("messages").map((r) => r.at),
        [1000, 2000, 3000],
    );
});

run_test("a long poll unanswered for 65 s is asked again without blocking", async () => {
    const {link, clock, of, seen} = make_link((r) =>
        r.query.dont_block === "true" ? success({events: [{id: 6}]}) : "hang",
    );
    void link.run();
    await clock.advance(beta_link.LONG_POLL_DEADLINE_MS - 1);
    assert.equal(of("events").length, 1);
    await clock.advance(2000);
    assert.deepEqual(
        of("events").map((r) => r.query.dont_block),
        [undefined, "true", undefined],
    );
    assert.equal(
        of("events")[1].at - of("events")[0].at <= beta_link.LONG_POLL_DEADLINE_MS + 1000,
        true,
    );
    assert.equal(link.stats.deadlines, 1);
    assert.deepEqual(seen.events, [6]);
    assert.deepEqual(seen.statuses, []);
});

run_test("nothing shows for 5 s; then reconnecting, then offline, then nothing", async () => {
    let up = false;
    let served = 0;
    const {link, clock, seen} = make_link(() => {
        if (!up) {
            return {status: 502};
        }
        served += 1;
        return served === 1 ? success({events: []}) : "hang";
    });
    void link.run();
    await clock.advance(beta_link.GRACE_MS - 1);
    assert.deepEqual(seen.statuses, []);
    await clock.advance(2);
    assert.deepEqual(seen.statuses, ["reconnecting"]);
    await clock.advance(beta_link.OFFLINE_AFTER_MS);
    assert.deepEqual(seen.statuses, ["reconnecting", "offline"]);
    up = true;
    await clock.advance(beta_link.CAP_VISIBLE_MS);
    assert.deepEqual(seen.statuses, ["reconnecting", "offline", "live"]);
    assert.equal(served, 2);
});

run_test("with the network known gone, offline shows when the grace ends", async () => {
    const {link, clock, seen, env} = make_link(() => "net");
    env.online = false;
    env.hidden = true;
    void link.run();
    await clock.advance(beta_link.GRACE_MS);
    assert.deepEqual(seen.statuses, ["offline"]);
    // The network back: the next attempt is not held by the hidden cap.
    env.online = true;
    link.wake("online");
    await clock.advance(1);
    assert.deepEqual(seen.statuses, ["offline", "reconnecting"]);
});

run_test("a long poll open for 3 s is a server that answers", async () => {
    let calls = 0;
    const {link, clock, seen} = make_link(() => {
        calls += 1;
        return calls <= 3 ? "net" : "hang";
    });
    void link.run();
    await clock.advance(beta_link.GRACE_MS + 1000);
    assert.deepEqual(seen.statuses, ["reconnecting"]);
    await clock.advance(3000);
    assert.deepEqual(seen.statuses, ["reconnecting", "live"]);
});

run_test("signs of the person end a wait, at most once in 30 s each", async () => {
    const {link, clock, of} = make_link(() => "net", {random: () => 0.999});
    void link.run();
    await clock.advance(10000);
    const before = of("events").length;
    link.wake("focus");
    await clock.advance(1);
    assert.equal(of("events").length, before + 1);
    link.wake("focus");
    await clock.advance(1);
    assert.equal(of("events").length, before + 1);
    link.wake("visible");
    await clock.advance(1);
    assert.equal(of("events").length, before + 2);
    assert.equal(link.stats.wakes, 2);
});

run_test("the network coming back asks a long poll that may be dead again", async () => {
    const {link, clock, of, seen} = make_link((r) =>
        r.query.dont_block === "true" ? success({events: [{id: 6}]}) : "hang",
    );
    void link.run();
    // Too young to be thought dead, and focus never aborts a long poll.
    await clock.advance(1000);
    link.wake("online");
    link.wake("focus");
    await clock.advance(10000);
    assert.equal(of("events").length, 1);
    await clock.advance(beta_link.WAKE_EVERY_MS);
    link.wake("online");
    await clock.advance(1);
    assert.deepEqual(
        of("events").map((r) => r.query.dont_block),
        [undefined, "true", undefined],
    );
    assert.deepEqual(seen.events, [6]);
    assert.deepEqual(seen.statuses, []);
});

run_test("signed out stops everything, and another tab's sign-in resumes", async () => {
    let session = false;
    const {link, clock, of, seen} = make_link((r) =>
        session
            ? r.query.last_event_id === "5"
                ? success({events: [{id: 6}]})
                : "hang"
            : {status: 401},
    );
    void link.run();
    await clock.advance(10 * MINUTE);
    assert.equal(of("events").length, 1);
    assert.equal(link.stopped, "signed_out");
    assert.deepEqual(seen.statuses, ["signed_out"]);
    link.wake("online");
    link.leave();
    assert.equal(of("events").length, 1);

    // Another tab of this browser loaded the page signed in as me again.
    link.leaving = false;
    session = true;
    link.heard({type: "session", user_id: 10, csrf_token: "token-3"});
    await clock.advance(1000);
    assert.equal(link.stopped, null);
    assert.equal(link.csrf, "token-3");
    assert.deepEqual(seen.events, [6]);
    assert.deepEqual(seen.statuses, ["signed_out", "live"]);
});

run_test("what other tabs say", async () => {
    const {link, clock, seen, of} = make_link(hang);
    void link.run();
    link.heard({type: "other"});
    link.heard({type: "session", user_id: 10});
    assert.equal(link.stopped, null);
    assert.equal(link.csrf, "token-1");
    link.heard({type: "session", user_id: 12, csrf_token: "token-4"});
    assert.equal(link.stopped, "other_user");
    assert.equal(link.otherUserId, 12);
    link.heard({type: "signed_out"});
    assert.equal(link.stopped, "other_user");
    // Before the page knows who it is, a session says nothing.
    const fresh = make_link(hang, {state: {ME: 0}});
    fresh.link.heard({type: "session", user_id: 12});
    fresh.link.heard({type: "signed_out"});
    assert.equal(fresh.link.stopped, "signed_out");
    await clock.advance(1);
    assert.deepEqual(seen.statuses, ["other_user"]);
    assert.equal(of("events").length, 1);
});

run_test("signing out ends every wait", async () => {
    const {link, clock, of} = make_link((r) => {
        if (r.path === "events") {
            return failure(400, "BAD_EVENT_QUEUE_ID", "Bad event queue ID: q1");
        }
        if (r.path === "register") {
            return "net";
        }
        return {...failure(429, "RATE_LIMIT_HIT"), retry_after: "60"};
    });
    void link.run();
    await clock.advance(100);
    const held = assert.rejects(link.api("GET", "users"), {stopped: true});
    await clock.advance(1000);
    link.heard({type: "signed_out"});
    await clock.advance(MINUTE);
    await held;
    assert.equal(of("register").length, 1);
    assert.equal(of("users").length, 1);
});

run_test("leaving deletes the queue, once the page is going", async () => {
    const {link, clock, requests} = make_link((r) => (r.method === "DELETE" ? "net" : "hang"));
    void link.run();
    await clock.advance(1000);
    link.leave();
    await clock.advance(MINUTE);
    const deleted = requests.filter((r) => r.method === "DELETE");
    assert.equal(deleted.length, 1);
    assert.deepEqual(deleted[0].query, {queue_id: "q1"});
    assert.equal(deleted[0].keepalive, true);
    assert.equal(deleted[0].token, "token-1");
    assert.equal(requests.filter((r) => r.method === "GET").length, 1);

    // With no queue there is nothing to delete.
    const empty = make_link(hang, {state: {queue: null}});
    empty.link.leave();
    assert.equal(empty.requests.length, 0);
});

run_test("a write refused for another reason is that write's failure only", async () => {
    const {link} = make_link(() => failure(400, "BAD_REQUEST", "Invalid stream"));
    await assert.rejects(link.api("POST", "messages", {content: "hi"}, {body: "raw"}), {
        message: "Invalid stream",
        code: "BAD_REQUEST",
        status: 400,
        net: false,
    });
    assert.equal(link.stopped, null);
    const quiet = make_link(() => ({status: 500, body: {result: "error"}}));
    await assert.rejects(quiet.link.api("DELETE", "messages/1", {}), {
        message: "The request failed",
        net: true,
    });
});
