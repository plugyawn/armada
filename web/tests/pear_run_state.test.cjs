"use strict";

const assert = require("node:assert/strict");

const {clock, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pear_run_state = zrequire("pear_run_state");

function test(label, f) {
    run_test(label, () => {
        pear_run_state.clear_for_testing();
        clock.reset();
        try {
            f();
        } finally {
            pear_run_state.clear_for_testing();
        }
    });
}

test("set_active_run_result_messages tracks only active result messages", () => {
    pear_run_state.set_active_run_result_messages([
        {status: "queued", result_message_id: 1},
        {status: "running", result_message_id: 2},
        {status: "done", result_message_id: 3},
        {status: "running", result_message_id: null},
    ]);

    assert.ok(pear_run_state.is_active_run_result_message(1));
    assert.ok(pear_run_state.is_active_run_result_message(2));
    assert.ok(!pear_run_state.is_active_run_result_message(3));
});

test("ambient run ids are retained by result message", () => {
    pear_run_state.set_active_run_result_messages([
        {id: 41, status: "done", ambient_triggered: true, result_message_id: 301},
        {id: 42, status: "done", ambient_triggered: false, result_message_id: 302},
    ]);

    assert.equal(pear_run_state.get_ambient_run_id(301), 41);
    assert.equal(pear_run_state.get_ambient_run_id(302), undefined);

    pear_run_state.set_active_run_result_messages([
        {id: 41, status: "done", ambient_triggered: false, result_message_id: 301},
    ]);
    assert.equal(pear_run_state.get_ambient_run_id(301), undefined);
});

test("agent snapshots hydrate once and unrelated push events preserve them", () => {
    const agents = [
        {
            thread_id: "child-a",
            nickname: "Scout",
            status: "active",
        },
    ];
    pear_run_state.remember_runs([
        {
            id: 44,
            status: "running",
            ambient_triggered: false,
            result_message_id: 304,
            agents,
        },
    ]);
    assert.deepEqual(pear_run_state.get_run_view_by_id(44).agents, agents);

    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 1,
        run_id: 44,
        event_type: "transcript",
        status: "running",
        result_message_id: 304,
        stream: "devel",
        topic: "agents",
        current_activity: "Working",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 10,
        date_created: 11,
        agent_thread_id: null,
    });
    assert.deepEqual(pear_run_state.get_run_view_by_id(44).agents, agents);
});

test("native agent activity merges into an existing descriptor", () => {
    pear_run_state.remember_runs([
        {
            id: 46,
            status: "running",
            ambient_triggered: false,
            result_message_id: 306,
            agents: [
                {
                    thread_id: "child-a",
                    nickname: "Scout",
                    status: "active",
                    current_activity: "Starting",
                },
            ],
        },
    ]);

    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 1,
        run_id: 46,
        event_type: "agent",
        status: "running",
        result_message_id: 306,
        stream: "devel",
        topic: "agents",
        current_activity: "Delegating",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 10,
        date_created: 12,
        agent_thread_id: "child-a",
        agent: {
            thread_id: "child-a",
            current_activity: "Inspect files",
        },
    });

    assert.deepEqual(pear_run_state.get_run_view_by_id(46).agents, [
        {
            thread_id: "child-a",
            nickname: "Scout",
            status: "active",
            current_activity: "Inspect files",
        },
    ]);
});

test("native agent activity never invents an unknown child descriptor", () => {
    const agents = [
        {
            thread_id: "child-a",
            nickname: "Scout",
            status: "active",
        },
    ];
    pear_run_state.remember_runs([
        {
            id: 47,
            status: "running",
            ambient_triggered: false,
            result_message_id: 307,
            agents,
        },
    ]);

    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 1,
        run_id: 47,
        event_type: "agent",
        status: "running",
        result_message_id: 307,
        stream: "devel",
        topic: "agents",
        current_activity: "Delegating",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 10,
        date_created: 12,
        agent_thread_id: "child-missing",
        agent: {
            thread_id: "child-missing",
            status: "active",
        },
    });

    assert.deepEqual(pear_run_state.get_run_view_by_id(47).agents, agents);
});

test("native agent event identity reaches listeners with the refreshed snapshot", () => {
    const agents = [
        {
            thread_id: "child-a",
            nickname: "Scout",
            status: "active",
        },
    ];
    let observed;
    const unsubscribe = pear_run_state.on_run_state_changed((run, event) => {
        if (event !== undefined) {
            observed = {run, event};
        }
    });
    try {
        pear_run_state.apply_pear_run_event({
            version: 1,
            sequence: 1,
            run_id: 45,
            event_type: "agent",
            status: "running",
            result_message_id: 305,
            stream: "devel",
            topic: "agents",
            current_activity: "Delegating",
            tool_count: 1,
            plan: null,
            overview: null,
            has_visible_assistant_text: true,
            usage: null,
            duration_seconds: null,
            trace_available: true,
            run_date_created: 10,
            date_created: 11,
            agent_thread_id: "child-a",
            agents,
        });
        assert.equal(observed.event.agent_thread_id, "child-a");
        assert.deepEqual(observed.run.agents, agents);
    } finally {
        unsubscribe();
    }
});

test("done run clears active result message immediately", () => {
    const changes = [];
    const unsubscribe = pear_run_state.on_active_run_result_messages_changed((change) =>
        changes.push(change),
    );
    try {
        pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 4}]);
        assert.ok(pear_run_state.is_active_run_result_message(4));

        pear_run_state.set_active_run_result_messages([{status: "done", result_message_id: 4}]);
        assert.ok(!pear_run_state.is_active_run_result_message(4));
        assert.deepEqual(
            changes.map((change) => change.removed_message_ids),
            [[], [4]],
        );
    } finally {
        unsubscribe();
    }
});

test("topic reconciliation preserves active messages in other rendered topics", () => {
    pear_run_state.set_active_run_result_messages(
        [
            {
                id: 51,
                status: "running",
                ambient_triggered: false,
                result_message_id: 501,
                stream: "devel",
                topic: "alpha",
            },
        ],
        {stream: "devel", topic: "alpha"},
    );
    pear_run_state.set_active_run_result_messages(
        [
            {
                id: 52,
                status: "running",
                ambient_triggered: false,
                result_message_id: 502,
                stream: "devel",
                topic: "beta",
            },
        ],
        {stream: "devel", topic: "beta"},
    );

    pear_run_state.set_active_run_result_messages([], {
        stream: "devel",
        topic: "alpha",
        force_reconcile: true,
    });

    assert.ok(!pear_run_state.is_active_run_result_message(501));
    assert.ok(pear_run_state.is_active_run_result_message(502));
});

test("quiet active result message remains active until a terminal event", () => {
    pear_run_state.set_active_run_result_messages([{status: "running", result_message_id: 6}]);
    clock.tick(60_000);
    assert.ok(pear_run_state.is_active_run_result_message(6));
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 10,
        run_id: 2,
        event_type: "done",
        status: "done",
        result_message_id: 6,
        stream: "devel",
        topic: "speed",
        current_activity: "Completed",
        tool_count: 3,
        plan: null,
        overview: null,
        has_visible_assistant_text: null,
        usage: {total_tokens: 1200},
        duration_seconds: 12.5,
        trace_available: true,
        run_date_created: 100,
        date_created: 110,
    });
    assert.ok(!pear_run_state.is_active_run_result_message(6));
    assert.deepEqual(pear_run_state.get_run_footer(6), {
        status: "done",
        duration_seconds: 12.5,
        total_tokens: 1200,
        tool_count: 3,
        overview: null,
    });
});

test("settled footer retains the last non-empty step-bound overview", () => {
    pear_run_state.remember_runs([
        {
            id: 3,
            status: "running",
            ambient_triggered: false,
            result_message_id: 7,
            overview: "Comparing two edits",
            date_updated: 100,
        },
    ]);
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 11,
        run_id: 3,
        event_type: "plan",
        status: "running",
        result_message_id: 7,
        stream: "devel",
        topic: "speed",
        current_activity: "Next step",
        tool_count: 2,
        plan: [{step: "Summarize", status: "in_progress"}],
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 90,
        date_created: 105,
    });
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 12,
        run_id: 3,
        event_type: "done",
        status: "done",
        result_message_id: 7,
        stream: "devel",
        topic: "speed",
        current_activity: "Completed",
        tool_count: 2,
        plan: null,
        overview: null,
        has_visible_assistant_text: null,
        usage: {total_tokens: 500},
        duration_seconds: 5,
        trace_available: true,
        run_date_created: 90,
        date_created: 110,
    });

    assert.deepEqual(pear_run_state.get_run_footer(7), {
        status: "done",
        duration_seconds: 5,
        total_tokens: 500,
        tool_count: 2,
        overview: "Comparing two edits",
    });
});

test("stale hydration cannot overwrite a newer push event", () => {
    pear_run_state.remember_runs([
        {
            id: 9,
            snapshot_sequence: 10,
            status: "running",
            ambient_triggered: false,
            result_message_id: 90,
            stream: "devel",
            topic: "speed",
            date_created: 90,
            date_updated: 100,
        },
    ]);
    pear_run_state.set_active_run_result_messages([
        {
            id: 9,
            status: "running",
            ambient_triggered: false,
            result_message_id: 90,
            date_updated: 100,
        },
    ]);
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 20,
        run_id: 9,
        event_type: "done",
        status: "done",
        result_message_id: 90,
        stream: "devel",
        topic: "speed",
        current_activity: "Completed",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: null,
        usage: {total_tokens: 100},
        duration_seconds: 2,
        trace_available: true,
        run_date_created: 90,
        date_created: 110,
    });

    const stale_run = {
        id: 9,
        snapshot_sequence: 19,
        status: "running",
        ambient_triggered: false,
        result_message_id: 90,
        stream: "devel",
        topic: "speed",
        date_created: 90,
        date_updated: 105,
    };
    pear_run_state.remember_runs([stale_run]);
    pear_run_state.set_active_run_result_messages([stale_run]);

    assert.equal(pear_run_state.get_run_view(90).status, "done");
    assert.ok(!pear_run_state.is_active_run_result_message(90));
});

test("authoritative snapshot advances the event watermark", () => {
    pear_run_state.remember_runs([
        {
            id: 10,
            snapshot_sequence: 30,
            status: "done",
            ambient_triggered: false,
            result_message_id: 100,
            stream: "devel",
            topic: "speed",
            current_activity: "Completed",
            date_updated: 300,
        },
    ]);

    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 29,
        run_id: 10,
        event_type: "running",
        status: "running",
        result_message_id: 100,
        stream: "devel",
        topic: "speed",
        current_activity: "Stale progress",
        tool_count: 0,
        plan: null,
        overview: null,
        has_visible_assistant_text: null,
        usage: null,
        duration_seconds: null,
        trace_available: false,
        run_date_created: 100,
        date_created: 290,
    });

    assert.equal(pear_run_state.get_run_view(100).status, "done");
    assert.ok(pear_run_state.event_is_latest(10, 30));
    assert.ok(!pear_run_state.event_is_latest(10, 29));
});

test("authoritative snapshot hydrates a newer agent graph despite an older heartbeat", () => {
    const first_agent = {
        thread_id: "child-a",
        nickname: "Scout",
        status: "active",
    };
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 40,
        run_id: 11,
        event_type: "agent",
        status: "running",
        result_message_id: 110,
        stream: "devel",
        topic: "agents",
        current_activity: "Delegating",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 100,
        date_created: 400,
        agent_thread_id: "child-a",
        agents: [first_agent],
    });

    const authoritative_agents = [
        first_agent,
        {
            thread_id: "child-b",
            nickname: "Verifier",
            status: "idle",
        },
    ];
    pear_run_state.remember_runs([
        {
            id: 11,
            snapshot_sequence: 40,
            status: "running",
            ambient_triggered: false,
            result_message_id: 110,
            stream: "devel",
            topic: "agents",
            current_activity: "Delegating",
            agents: authoritative_agents,
            date_created: 100,
            date_updated: 200,
        },
    ]);

    assert.deepEqual(pear_run_state.get_run_view_by_id(11).agents, authoritative_agents);
});

test("typed assistant-visible push retires the placeholder immediately", () => {
    pear_run_state.remember_runs([
        {
            id: 12,
            status: "running",
            ambient_triggered: false,
            result_message_id: 120,
            date_updated: 100,
            live: {has_visible_assistant_text: false, updated_at: 1},
        },
    ]);
    assert.equal(pear_run_state.get_run_view(120).has_visible_assistant_text, false);

    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 20,
        run_id: 12,
        event_type: "assistant_visible",
        status: "running",
        result_message_id: 120,
        stream: "devel",
        topic: "speed",
        current_activity: "Assistant response started",
        tool_count: 1,
        plan: null,
        overview: null,
        has_visible_assistant_text: true,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 90,
        date_created: 200,
    });
    assert.equal(pear_run_state.get_run_view(120).has_visible_assistant_text, true);

    pear_run_state.remember_runs([
        {
            id: 12,
            status: "running",
            ambient_triggered: false,
            result_message_id: 120,
            date_updated: 150,
            live: {has_visible_assistant_text: false, updated_at: 199},
        },
    ]);
    assert.equal(pear_run_state.get_run_view(120).has_visible_assistant_text, true);
    assert.equal(pear_run_state.get_run_view(120).updated_at, 200);
});

test("plan updates clear overview copy from the previous step", () => {
    pear_run_state.remember_runs([
        {
            id: 13,
            status: "running",
            ambient_triggered: false,
            result_message_id: 130,
            plan: [{step: "Inspect", status: "in_progress"}],
            overview: "Reading four files",
            date_updated: 100,
        },
    ]);
    pear_run_state.apply_pear_run_event({
        version: 1,
        sequence: 21,
        run_id: 13,
        event_type: "transcript",
        status: "running",
        result_message_id: 130,
        stream: "devel",
        topic: "speed",
        current_activity: "Plan updated",
        tool_count: 1,
        plan: [{step: "Summarize", status: "in_progress"}],
        overview: null,
        has_visible_assistant_text: null,
        usage: null,
        duration_seconds: null,
        trace_available: true,
        run_date_created: 90,
        date_created: 201,
    });

    assert.equal(pear_run_state.get_run_view(130).overview, null);
});
