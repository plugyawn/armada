// The Pear runs store owns the one `GET /json/pear/runs` polling
// cadence (5s idle / 125ms while a run is live) that surfaces like the
// Pear panel subscribe to. Polling is interest-gated: a visible
// subscriber or a short post-send settle watch keeps it alive.

import * as z from "zod/mini";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as message_helper from "./message_helper.ts";
import * as message_live_update from "./message_live_update.ts";
import {raw_message_schema} from "./message_store.ts";
import * as narrow_state from "./narrow_state.ts";

export type PearRunEvent = {
    id: number;
    event_type: string;
    summary: string;
    payload?: {
        kind?: string;
        status?: string;
        title?: string;
        body?: string;
    };
};

export type PearUsage = {
    input_tokens?: number;
    cached_input_tokens?: number;
    output_tokens?: number;
    reasoning_output_tokens?: number;
    total_tokens?: number;
    cache_percent?: number;
    duration_seconds?: number | null;
    summary?: string;
};

export type PearLiveRun = {
    status: string;
    content: string;
    updated_at: number;
};

export type PearRun = {
    id: number;
    result_message_id?: number | null;
    status: string;
    session_kind: string;
    source_stream: string;
    source_topic: string;
    summary: string;
    current_activity: string;
    runtime_label: string;
    sandbox: string;
    approval_policy: string;
    workdir: string;
    codex_thread_id: string;
    usage?: PearUsage | null;
    live?: PearLiveRun | null;
    events?: PearRunEvent[];
};

export type PearReasoningEffort = {
    effort: string;
    label: string;
    description?: string;
};

export type PearModelCatalogEntry = {
    slug: string;
    display_name: string;
    default_reasoning_level?: string;
    supported_reasoning_levels?: PearReasoningEffort[];
};

// The P1a git-status DELTA: one `git status --porcelain=v2 --branch`
// parse, plus the branch==topic check (R3). The panel renders only
// these fields; names/paths live in the authoritative chat header.
export type PearGitContext = {
    available: boolean;
    workdir?: string;
    repo_name?: string;
    worktree_name?: string;
    branch?: string;
    head?: string;
    head_short?: string;
    upstream?: string;
    ahead?: number;
    behind?: number;
    dirty?: boolean;
    changed_count?: number;
    detached?: boolean;
    branch_matches_topic?: boolean;
};

export type PearTopicContext = {
    stream: string;
    topic: string;
    model: string;
    default_model: string;
    model_choices: string[];
    model_catalog?: PearModelCatalogEntry[];
    reasoning_effort: string;
    default_reasoning_effort: string;
    reasoning_efforts: PearReasoningEffort[];
    github_repo: string;
    pinned_repo?: string;
    repo_label?: string;
    repo_title?: string;
    worktree_label?: string;
    workdir: string;
    git: PearGitContext;
};

export type PearRunsResponse = {
    pear_runs: PearRun[];
    pear_topic_context?: PearTopicContext | null;
    runtime_config?: {
        model?: string | null;
        model_choices?: string[];
        model_catalog?: PearModelCatalogEntry[];
        reasoning_effort?: string | null;
        reasoning_efforts?: PearReasoningEffort[];
    };
};

// "narrow" scopes a subscriber to whatever the current narrow's poll
// returns; an explicit {stream, topic} scope filters runs client-side.
// (A server-side filter param is the P6 seam; until then the store
// keeps a single narrow-scoped poll no matter how many subscribers.)
export type PearRunScope =
    | "narrow"
    | {
          stream: string;
          topic: string;
      };

export type PearSnapshot = {
    runs: PearRun[];
    topic_context: PearTopicContext | null | undefined;
    error: string;
};

export type PearStatusTransition = {
    run_id: number;
    // undefined means the run was seen for the first time.
    from_status: string | undefined;
    to_status: string;
    run: PearRun;
};

type PearSubscriber = {
    scope: PearRunScope;
    callback: (snapshot: PearSnapshot) => void;
    on_status_transition: ((transition: PearStatusTransition) => void) | undefined;
};

export const LIVE_REFRESH_INTERVAL_MS = 125;
export const FULL_REFRESH_INTERVAL_MS = 5000;
export const NARROW_SETTLE_WATCH_MS = 60_000;

const subscribers = new Set<PearSubscriber>();
let full_refresh_timer: number | undefined;
let live_refresh_timer: number | undefined;
let live_refresh_in_flight = false;
let last_runs: PearRun[] = [];
let last_topic_context: PearTopicContext | null | undefined;
let last_error = "";
let status_by_run_id = new Map<number, string>();
let narrow_settle_watch_until = 0;
const refreshing_result_message_ids = new Set<number>();
const refreshed_result_message_ids = new Set<number>();

function current_context(): {stream: string | undefined; topic: string | undefined} {
    return {
        stream: narrow_state.stream_name(),
        topic: narrow_state.topic(),
    };
}

function run_matches_scope(run: PearRun, scope: PearRunScope): boolean {
    if (scope === "narrow") {
        return true;
    }
    return run.source_stream === scope.stream && run.source_topic === scope.topic;
}

export function get_snapshot(scope: PearRunScope = "narrow"): PearSnapshot {
    return {
        runs: last_runs.filter((run) => run_matches_scope(run, scope)),
        topic_context: last_topic_context,
        error: last_error,
    };
}

function notify_subscribers(): void {
    for (const subscriber of subscribers) {
        subscriber.callback(get_snapshot(subscriber.scope));
    }
}

function emit_status_transitions(runs: PearRun[]): void {
    const next_status_by_run_id = new Map<number, string>();
    const transitions: PearStatusTransition[] = [];
    for (const run of runs) {
        next_status_by_run_id.set(run.id, run.status);
        const from_status = status_by_run_id.get(run.id);
        if (from_status !== run.status) {
            transitions.push({
                run_id: run.id,
                from_status,
                to_status: run.status,
                run,
            });
        }
    }
    status_by_run_id = next_status_by_run_id;
    for (const transition of transitions) {
        for (const subscriber of subscribers) {
            if (
                subscriber.on_status_transition !== undefined &&
                run_matches_scope(transition.run, subscriber.scope)
            ) {
                subscriber.on_status_transition(transition);
            }
        }
    }
}

function run_is_active(run: {status: string}): boolean {
    return run.status === "running" || run.status === "queued";
}

export function any_active_run(runs: PearRun[]): boolean {
    return runs.some((run) => run_is_active(run));
}

function run_is_terminal(run: {status: string}): boolean {
    return run.status === "done" || run.status === "failed" || run.status === "cancelled";
}

function settle_terminal_result_messages(runs: PearRun[]): void {
    const result_message_ids = [
        ...new Set(
            runs
                .filter((run) => run_is_terminal(run))
                .map((run) => run.result_message_id)
                .filter((message_id): message_id is number => typeof message_id === "number"),
        ),
    ];
    if (result_message_ids.length === 0) {
        return;
    }
    message_live_update.rerender_messages_view_by_message_ids(result_message_ids);
    const message_ids_to_fetch = result_message_ids.filter(
        (message_id) =>
            !refreshed_result_message_ids.has(message_id) &&
            !refreshing_result_message_ids.has(message_id),
    );
    if (message_ids_to_fetch.length === 0) {
        return;
    }
    for (const message_id of message_ids_to_fetch) {
        refreshing_result_message_ids.add(message_id);
    }
    void channel.get({
        url: "/json/messages",
        data: {
            message_ids: JSON.stringify(message_ids_to_fetch),
            allow_empty_topic_name: true,
        },
        success(raw_data) {
            const parsed_data = z
                .object({
                    messages: z.array(raw_message_schema),
                })
                .parse(raw_data);
            for (const raw_message of parsed_data.messages) {
                message_helper.process_new_server_message(raw_message);
                refreshed_result_message_ids.add(raw_message.id);
            }
            message_live_update.rerender_messages_view_by_message_ids(message_ids_to_fetch);
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to refresh Pear result messages: ${xhr.responseText}`);
            }
        },
        complete() {
            for (const message_id of message_ids_to_fetch) {
                refreshing_result_message_ids.delete(message_id);
            }
        },
    });
}

function narrow_settle_watch_is_active(): boolean {
    return Date.now() < narrow_settle_watch_until;
}

function has_polling_interest(): boolean {
    return subscribers.size > 0 || narrow_settle_watch_is_active();
}

function clear_live_refresh(): void {
    if (live_refresh_timer !== undefined) {
        window.clearTimeout(live_refresh_timer);
        live_refresh_timer = undefined;
    }
}

function schedule_live_refresh(): void {
    if (!has_polling_interest() || !any_active_run(last_runs)) {
        clear_live_refresh();
        return;
    }
    if (live_refresh_timer !== undefined || live_refresh_in_flight) {
        return;
    }
    live_refresh_timer = window.setTimeout(() => {
        live_refresh_timer = undefined;
        refresh({live: true});
    }, LIVE_REFRESH_INTERVAL_MS);
}

export function refresh(options: {live?: boolean} = {}): void {
    if (!has_polling_interest()) {
        // Polling is interest-gated: no subscriber surface and no
        // post-send settle watch means zero /json/pear/runs traffic.
        return;
    }
    const live = options.live === true;
    if (live && live_refresh_in_flight) {
        return;
    }
    const {stream, topic} = current_context();
    const data: Record<string, string | boolean> = {
        include_events: false,
        include_context: !live,
    };
    if (stream !== undefined && topic !== undefined) {
        data["stream"] = stream;
        data["topic"] = topic;
    }
    if (live) {
        live_refresh_in_flight = true;
    }
    void channel.get({
        url: "/json/pear/runs",
        data,
        success(raw_data) {
            const response = raw_data as PearRunsResponse;
            last_error = "";
            last_topic_context = live
                ? (response.pear_topic_context ?? last_topic_context)
                : response.pear_topic_context === undefined
                  ? last_topic_context
                  : response.pear_topic_context;
            last_runs = response.pear_runs;
            settle_terminal_result_messages(last_runs);
            emit_status_transitions(last_runs);
            notify_subscribers();
        },
        error(xhr) {
            if (xhr.readyState === 0) {
                return;
            }
            last_error = $t({defaultMessage: "Pear status is unavailable."});
            blueslip.warn(`Failed to fetch Pear runs: ${xhr.responseText}`);
            notify_subscribers();
        },
        complete() {
            if (live) {
                live_refresh_in_flight = false;
            }
            if (!has_polling_interest()) {
                stop_polling();
                return;
            }
            schedule_live_refresh();
        },
    });
}

function start_polling(): void {
    if (full_refresh_timer !== undefined) {
        return;
    }
    refresh();
    full_refresh_timer = window.setInterval(() => {
        if (!has_polling_interest()) {
            stop_polling();
            return;
        }
        refresh();
    }, FULL_REFRESH_INTERVAL_MS);
}

function stop_polling(): void {
    if (full_refresh_timer !== undefined) {
        window.clearInterval(full_refresh_timer);
        full_refresh_timer = undefined;
    }
    clear_live_refresh();
    live_refresh_in_flight = false;
}

export function is_polling(): boolean {
    return full_refresh_timer !== undefined;
}

export function watch_current_narrow_until_idle(): void {
    narrow_settle_watch_until = Math.max(
        narrow_settle_watch_until,
        Date.now() + NARROW_SETTLE_WATCH_MS,
    );
    start_polling();
}

export function subscribe(
    scope: PearRunScope,
    callback: (snapshot: PearSnapshot) => void,
    options: {
        on_status_transition?: (transition: PearStatusTransition) => void;
    } = {},
): () => void {
    const subscriber: PearSubscriber = {
        scope,
        callback,
        on_status_transition: options.on_status_transition,
    };
    subscribers.add(subscriber);
    // Deliver the cached snapshot synchronously so the surface can
    // paint before the first poll returns.
    callback(get_snapshot(scope));
    if (subscribers.size === 1) {
        start_polling();
    }
    return () => {
        if (!subscribers.delete(subscriber)) {
            return;
        }
        if (subscribers.size === 0 && !narrow_settle_watch_is_active()) {
            stop_polling();
        }
    };
}

export function post_run_action(run_id: number | string, action: string): void {
    void channel.post({
        url: `/json/pear/runs/${run_id}/${action}`,
        success() {
            refresh();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to ${action} Pear run: ${xhr.responseText}`);
            }
        },
    });
}
