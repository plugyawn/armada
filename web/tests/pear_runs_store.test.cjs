"use strict";

const assert = require("node:assert/strict");

const {clock, mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const channel = mock_esm("../src/channel");
mock_esm("../src/narrow_state", {
    stream_name: () => "devel",
    topic: () => "pear-smoke",
});

const pear_runs_store = zrequire("pear_runs_store");

function make_run(id, status, overrides = {}) {
    return {
        id,
        status,
        session_kind: "shared",
        source_stream: "devel",
        source_topic: "pear-smoke",
        summary: "Ship the thing",
        current_activity: "Running tests",
        runtime_label: "model=gpt-5.2-codex",
        sandbox: "workspace-write",
        approval_policy: "never",
        workdir: "/srv/pear/devel-pear-smoke",
        codex_thread_id: "thread-1",
        ...overrides,
    };
}

run_test("open_gated_polling_and_transitions", () => {
    const get_calls = [];
    channel.get = (opts) => {
        get_calls.push(opts);
    };

    const snapshots = [];
    const transitions = [];
    const unsubscribe = pear_runs_store.subscribe(
        "narrow",
        (snapshot) => {
            snapshots.push(snapshot);
        },
        {
            on_status_transition(transition) {
                transitions.push(transition);
            },
        },
    );

    // Subscribing delivers the cached snapshot synchronously…
    assert.equal(snapshots.length, 1);
    assert.deepEqual(snapshots[0].runs, []);
    // …and starts the poll immediately, scoped to the current narrow.
    assert.equal(pear_runs_store.is_polling(), true);
    assert.equal(get_calls.length, 1);
    assert.equal(get_calls[0].url, "/json/pear/runs");
    assert.equal(get_calls[0].data.stream, "devel");
    assert.equal(get_calls[0].data.topic, "pear-smoke");
    assert.equal(get_calls[0].data.include_context, true);

    get_calls[0].success({
        pear_runs: [make_run(7, "running")],
        pear_topic_context: null,
    });
    get_calls[0].complete();

    assert.equal(snapshots.length, 2);
    assert.equal(snapshots[1].runs[0].status, "running");
    // First sighting reports a transition from undefined.
    assert.equal(transitions.length, 1);
    assert.equal(transitions[0].run_id, 7);
    assert.equal(transitions[0].from_status, undefined);
    assert.equal(transitions[0].to_status, "running");

    // A live run schedules the 125ms live cadence, without context.
    clock.tick(pear_runs_store.LIVE_REFRESH_INTERVAL_MS);
    assert.equal(get_calls.length, 2);
    assert.equal(get_calls[1].data.include_context, false);

    get_calls[1].success({pear_runs: [make_run(7, "done")]});
    get_calls[1].complete();

    // The running→done status transition is emitted per run id.
    assert.equal(transitions.length, 2);
    assert.equal(transitions[1].run_id, 7);
    assert.equal(transitions[1].from_status, "running");
    assert.equal(transitions[1].to_status, "done");

    // With no active run, the live cadence stops.
    clock.tick(1000);
    assert.equal(get_calls.length, 2);

    // The 5s idle cadence still fires.
    clock.tick(pear_runs_store.FULL_REFRESH_INTERVAL_MS);
    assert.equal(get_calls.length, 3);
    get_calls[2].success({pear_runs: [], pear_topic_context: null});
    get_calls[2].complete();

    // Closing the last subscriber stops polling entirely: zero
    // /json/pear/runs requests while the right area is closed.
    unsubscribe();
    assert.equal(pear_runs_store.is_polling(), false);
    const calls_after_close = get_calls.length;
    pear_runs_store.refresh();
    clock.tick(60 * 1000);
    assert.equal(get_calls.length, calls_after_close);
});

run_test("scoped_subscription_filters_runs", () => {
    const get_calls = [];
    channel.get = (opts) => {
        get_calls.push(opts);
    };

    const narrow_snapshots = [];
    const scoped_snapshots = [];
    const unsubscribe_narrow = pear_runs_store.subscribe("narrow", (snapshot) => {
        narrow_snapshots.push(snapshot);
    });
    const unsubscribe_scoped = pear_runs_store.subscribe(
        {stream: "devel", topic: "other-topic"},
        (snapshot) => {
            scoped_snapshots.push(snapshot);
        },
    );

    // Only one poll no matter how many subscribers.
    assert.equal(get_calls.length, 1);
    get_calls[0].success({
        pear_runs: [make_run(1, "running"), make_run(2, "done", {source_topic: "other-topic"})],
        pear_topic_context: null,
    });
    get_calls[0].complete();

    const narrow_latest = narrow_snapshots.at(-1);
    assert.deepEqual(
        narrow_latest.runs.map((run) => run.id),
        [1, 2],
    );
    const scoped_latest = scoped_snapshots.at(-1);
    assert.deepEqual(
        scoped_latest.runs.map((run) => run.id),
        [2],
    );

    unsubscribe_narrow();
    assert.equal(pear_runs_store.is_polling(), true);
    unsubscribe_scoped();
    assert.equal(pear_runs_store.is_polling(), false);

    // Live cadence pending from the running run must not fire after
    // the last unsubscribe.
    const calls_after_close = get_calls.length;
    clock.tick(60 * 1000);
    assert.equal(get_calls.length, calls_after_close);
});

run_test("post_run_action_refreshes", () => {
    const get_calls = [];
    const post_calls = [];
    channel.get = (opts) => {
        get_calls.push(opts);
    };
    channel.post = (opts) => {
        post_calls.push(opts);
    };

    const unsubscribe = pear_runs_store.subscribe("narrow", () => {});
    assert.equal(get_calls.length, 1);

    pear_runs_store.post_run_action(7, "pause");
    assert.equal(post_calls.length, 1);
    assert.equal(post_calls[0].url, "/json/pear/runs/7/pause");
    post_calls[0].success();
    assert.equal(get_calls.length, 2);

    unsubscribe();
});
