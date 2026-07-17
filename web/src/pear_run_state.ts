type PearRunForActiveMessage = {
    id: number;
    snapshot_sequence?: number | undefined;
    status: string;
    ambient_triggered: boolean;
    result_message_id?: number | null | undefined;
    stream?: string | undefined;
    topic?: string | undefined;
    current_activity?: string | undefined;
    plan?: PearRunPlanStep[] | null | undefined;
    overview?: string | null | undefined;
    tool_count?: number | undefined;
    date_created?: number | undefined;
    date_updated?: number | undefined;
    usage?:
        | {
              total_tokens?: number | undefined;
              duration_seconds?: number | null | undefined;
          }
        | null
        | undefined;
    live?:
        | {
              operation?: {kind: string; label: string} | null | undefined;
              has_visible_assistant_text?: boolean | undefined;
              updated_at: number;
          }
        | null
        | undefined;
};

export type PearRunOperation = {
    kind: string;
    label: string;
};

export type PearRunPlanStep = {
    step: string;
    status: string;
};

export type PearRunLiveView = {
    id: number;
    snapshot_sequence?: number | undefined;
    status: string;
    result_message_id?: number | null | undefined;
    stream?: string | undefined;
    topic?: string | undefined;
    current_activity?: string | undefined;
    plan?: PearRunPlanStep[] | null | undefined;
    overview?: string | null | undefined;
    has_visible_assistant_text?: boolean | undefined;
    live_updated_at?: number | undefined;
    trace_available?: boolean | undefined;
    tool_count?: number | undefined;
    date_created?: number | undefined;
    updated_at?: number | undefined;
    usage?:
        | {
              total_tokens?: number | undefined;
              duration_seconds?: number | null | undefined;
          }
        | null
        | undefined;
};

export type PearRunPushEvent = {
    version: 1;
    sequence: number;
    run_id: number;
    event_type: string;
    status: string;
    result_message_id: number | null;
    stream: string;
    topic: string;
    current_activity: string;
    tool_count: number;
    plan: PearRunPlanStep[] | null;
    overview: string | null;
    has_visible_assistant_text: boolean | null;
    usage: {
        input_tokens?: number | undefined;
        cached_input_tokens?: number | undefined;
        output_tokens?: number | undefined;
        reasoning_output_tokens?: number | undefined;
        total_tokens?: number | undefined;
        cache_percent?: number | undefined;
    } | null;
    duration_seconds: number | null;
    trace_available: boolean;
    run_date_created: number;
    date_created: number;
};

type PearActiveResultMessageChange = {
    removed_message_ids: number[];
    footer_changed_message_ids?: number[];
};

export type PearRunFooter = {
    status: string;
    duration_seconds: number | null;
    total_tokens: number | null;
    tool_count: number;
};

const active_result_message_ids = new Set<number>();
// The topic (if any) with a live run, for steer-aware surfaces like
// the compose placeholder. Lowercased for comparison.
let active_run_stream: string | undefined;
let active_run_topic: string | undefined;
const active_run_topic_listeners = new Set<() => void>();

// Completed runs' stats, keyed by result message, for the run footer.
// Cumulative across topic switches because footers are historical facts.
const settled_run_footers = new Map<number, PearRunFooter>();
const ambient_run_ids_by_result_message = new Map<number, number>();
// Active runs' start times (seconds), for the live elapsed counter.
const active_run_started_at = new Map<number, number>();
// A long engine operation (compaction, …) currently animating in the
// active run's footer, keyed by result message.
const active_run_operations = new Map<number, PearRunOperation>();
const run_views_by_id = new Map<number, PearRunLiveView>();
const run_ids_by_result_message = new Map<number, number>();
const latest_sequence_by_run_id = new Map<number, number>();
const run_state_listeners = new Set<(run: PearRunLiveView) => void>();
const active_result_message_change_listeners = new Set<
    (change: PearActiveResultMessageChange) => void
>();
export const PEAR_LIVENESS_MARKERS_CHANGED_EVENT = "pear:liveness-markers-changed";

export function notify_liveness_markers_changed(): void {
    if (typeof document === "undefined" || typeof document.dispatchEvent !== "function") {
        return;
    }
    document.dispatchEvent(new Event(PEAR_LIVENESS_MARKERS_CHANGED_EVENT));
}
function is_active_status(status: string): boolean {
    return status === "queued" || status === "running";
}

function is_settled_status(status: string): boolean {
    return status === "done" || status === "failed" || status === "cancelled";
}

function footer_from_run(run: PearRunForActiveMessage): PearRunFooter {
    return {
        status: run.status,
        duration_seconds: run.usage?.duration_seconds ?? null,
        total_tokens: run.usage?.total_tokens ?? null,
        tool_count: run.tool_count ?? 0,
    };
}

function footers_equal(left: PearRunFooter | undefined, right: PearRunFooter): boolean {
    return (
        left?.status === right.status &&
        left.duration_seconds === right.duration_seconds &&
        left.total_tokens === right.total_tokens &&
        left.tool_count === right.tool_count
    );
}

function sets_equal(left: Set<number>, right: Set<number>): boolean {
    if (left.size !== right.size) {
        return false;
    }
    for (const value of left) {
        if (!right.has(value)) {
            return false;
        }
    }
    return true;
}

function removed_active_result_message_ids(next_active_result_message_ids: Set<number>): number[] {
    return [...active_result_message_ids].filter(
        (message_id) => !next_active_result_message_ids.has(message_id),
    );
}

function notify_active_result_message_change_listeners(
    change: PearActiveResultMessageChange,
): void {
    for (const listener of active_result_message_change_listeners) {
        listener(change);
    }
}

export function set_active_run_result_messages(
    runs: PearRunForActiveMessage[],
    options: {force_reconcile?: boolean} = {},
): void {
    const next_active_result_message_ids = new Set<number>();
    const footer_changed_message_ids: number[] = [];
    for (const run of runs) {
        const latest_view = run_views_by_id.get(run.id);
        const result_message_id = latest_view?.result_message_id ?? run.result_message_id;
        const status = latest_view?.status ?? run.status;
        if (
            typeof result_message_id !== "number" ||
            !Number.isInteger(result_message_id) ||
            result_message_id <= 0
        ) {
            continue;
        }
        if (run.ambient_triggered) {
            ambient_run_ids_by_result_message.set(result_message_id, run.id);
        } else {
            ambient_run_ids_by_result_message.delete(result_message_id);
        }
        if (is_active_status(status)) {
            next_active_result_message_ids.add(result_message_id);
            if (typeof run.date_created === "number") {
                active_run_started_at.set(result_message_id, run.date_created);
            }
            const operation = run.live?.operation ?? undefined;
            const previous_operation = active_run_operations.get(result_message_id);
            if (
                operation?.kind !== previous_operation?.kind ||
                operation?.label !== previous_operation?.label
            ) {
                if (operation === undefined) {
                    active_run_operations.delete(result_message_id);
                } else {
                    active_run_operations.set(result_message_id, operation);
                }
                footer_changed_message_ids.push(result_message_id);
            }
        } else if (is_settled_status(status)) {
            active_run_started_at.delete(result_message_id);
            active_run_operations.delete(result_message_id);
            const footer = footer_from_run({...run, ...latest_view, status});
            if (!footers_equal(settled_run_footers.get(result_message_id), footer)) {
                settled_run_footers.set(result_message_id, footer);
                footer_changed_message_ids.push(result_message_id);
            }
        }
    }
    if (sets_equal(active_result_message_ids, next_active_result_message_ids)) {
        if (options.force_reconcile === true || footer_changed_message_ids.length > 0) {
            notify_active_result_message_change_listeners({
                removed_message_ids: [],
                footer_changed_message_ids,
            });
        }
        return;
    }
    const removed_message_ids = removed_active_result_message_ids(next_active_result_message_ids);
    active_result_message_ids.clear();
    for (const message_id of next_active_result_message_ids) {
        active_result_message_ids.add(message_id);
    }
    notify_active_result_message_change_listeners({
        removed_message_ids,
        footer_changed_message_ids,
    });
}

export function remember_runs(runs: PearRunForActiveMessage[]): void {
    for (const run of runs) {
        const latest_event_sequence = latest_sequence_by_run_id.get(run.id) ?? 0;
        const snapshot_is_current =
            latest_event_sequence === 0 ||
            (run.snapshot_sequence !== undefined && run.snapshot_sequence >= latest_event_sequence);
        const hydrated_view: PearRunLiveView = {
            id: run.id,
            snapshot_sequence: run.snapshot_sequence,
            status: run.status,
            result_message_id: run.result_message_id,
            stream: run.stream,
            topic: run.topic,
            current_activity: run.current_activity,
            plan: run.plan,
            overview: run.overview,
            has_visible_assistant_text: run.live?.has_visible_assistant_text,
            live_updated_at: run.live?.updated_at,
            trace_available: true,
            tool_count: run.tool_count,
            date_created: run.date_created,
            updated_at: run.date_updated,
            usage: run.usage,
        };
        const previous = run_views_by_id.get(run.id);
        if (!snapshot_is_current && previous !== undefined) {
            // The response began before a push event already applied by the
            // synchronous event reducer. Keep the event-owned state; the
            // snapshot's immutable metadata is not represented in this view.
            continue;
        }
        if (run.snapshot_sequence !== undefined && run.snapshot_sequence > latest_event_sequence) {
            latest_sequence_by_run_id.set(run.id, run.snapshot_sequence);
        }
        const base_view =
            previous?.updated_at !== undefined &&
            previous.updated_at > (hydrated_view.updated_at ?? Number.NEGATIVE_INFINITY)
                ? previous
                : hydrated_view;
        const hydrated_live_is_newer =
            hydrated_view.live_updated_at !== undefined &&
            hydrated_view.live_updated_at >=
                (previous?.live_updated_at ?? Number.NEGATIVE_INFINITY);
        // Volatile worker state and durable run events have independent
        // clocks.  A newer push must not prevent the next authoritative live
        // snapshot from revealing the first real assistant text.
        const view: PearRunLiveView = {
            ...base_view,
            has_visible_assistant_text: hydrated_live_is_newer
                ? hydrated_view.has_visible_assistant_text
                : previous?.has_visible_assistant_text,
            live_updated_at: hydrated_live_is_newer
                ? hydrated_view.live_updated_at
                : previous?.live_updated_at,
        };
        run_views_by_id.set(run.id, view);
        if (
            typeof previous?.result_message_id === "number" &&
            previous.result_message_id !== view.result_message_id &&
            run_ids_by_result_message.get(previous.result_message_id) === run.id
        ) {
            run_ids_by_result_message.delete(previous.result_message_id);
        }
        if (typeof view.result_message_id === "number") {
            run_ids_by_result_message.set(view.result_message_id, run.id);
        }
        if (is_active_status(run.status)) {
            for (const listener of run_state_listeners) {
                listener(view);
            }
        }
    }
}

export function snapshot_is_current(
    run_id: number,
    snapshot_sequence: number | undefined,
): boolean {
    const latest_event_sequence = latest_sequence_by_run_id.get(run_id) ?? 0;
    return (
        latest_event_sequence === 0 ||
        (snapshot_sequence !== undefined && snapshot_sequence >= latest_event_sequence)
    );
}

export function event_is_latest(run_id: number, sequence: number): boolean {
    return sequence === (latest_sequence_by_run_id.get(run_id) ?? 0);
}

export function apply_pear_run_event(event: PearRunPushEvent): void {
    if (
        event.version !== 1 ||
        event.sequence <= (latest_sequence_by_run_id.get(event.run_id) ?? 0)
    ) {
        return;
    }
    latest_sequence_by_run_id.set(event.run_id, event.sequence);
    const previous = run_views_by_id.get(event.run_id);
    const view: PearRunLiveView = {
        ...previous,
        id: event.run_id,
        status: event.status,
        result_message_id: event.result_message_id,
        stream: event.stream,
        topic: event.topic,
        current_activity: event.current_activity,
        plan: event.plan ?? previous?.plan,
        overview: event.overview ?? (event.plan === null ? previous?.overview : null),
        has_visible_assistant_text:
            event.has_visible_assistant_text ?? previous?.has_visible_assistant_text,
        live_updated_at:
            event.has_visible_assistant_text === null
                ? previous?.live_updated_at
                : event.date_created,
        trace_available: event.trace_available || previous?.trace_available,
        tool_count: event.tool_count,
        date_created: event.run_date_created,
        updated_at: event.date_created,
        usage: {
            ...previous?.usage,
            ...event.usage,
            duration_seconds: event.duration_seconds ?? previous?.usage?.duration_seconds,
        },
    };
    run_views_by_id.set(event.run_id, view);
    const message_id = event.result_message_id;
    if (
        typeof previous?.result_message_id === "number" &&
        previous.result_message_id !== message_id &&
        run_ids_by_result_message.get(previous.result_message_id) === event.run_id
    ) {
        run_ids_by_result_message.delete(previous.result_message_id);
    }
    if (message_id !== null) {
        run_ids_by_result_message.set(message_id, event.run_id);
        if (is_active_status(event.status)) {
            active_result_message_ids.add(message_id);
            active_run_started_at.set(message_id, event.run_date_created);
        } else {
            active_result_message_ids.delete(message_id);
            active_run_started_at.delete(message_id);
            active_run_operations.delete(message_id);
            settled_run_footers.set(message_id, {
                status: event.status,
                duration_seconds: view.usage?.duration_seconds ?? null,
                total_tokens: view.usage?.total_tokens ?? null,
                tool_count: event.tool_count,
            });
        }
        notify_active_result_message_change_listeners({
            removed_message_ids: is_active_status(event.status) ? [] : [message_id],
            footer_changed_message_ids: [message_id],
        });
    }
    for (const listener of run_state_listeners) {
        listener(view);
    }
}

export function get_run_view(message_id: number | undefined): PearRunLiveView | undefined {
    if (message_id === undefined) {
        return undefined;
    }
    const run_id = run_ids_by_result_message.get(message_id);
    return run_id === undefined ? undefined : run_views_by_id.get(run_id);
}

export function get_run_view_by_id(run_id: number): PearRunLiveView | undefined {
    return run_views_by_id.get(run_id);
}

export function on_run_state_changed(listener: (run: PearRunLiveView) => void): () => void {
    run_state_listeners.add(listener);
    return () => run_state_listeners.delete(listener);
}

export function is_active_run_result_message(message_id: number | undefined): boolean {
    return message_id !== undefined && active_result_message_ids.has(message_id);
}

export function set_active_run_topic(stream: string | undefined, topic: string | undefined): void {
    const next_stream = stream?.toLowerCase();
    const next_topic = topic?.toLowerCase();
    if (next_stream === active_run_stream && next_topic === active_run_topic) {
        return;
    }
    active_run_stream = next_stream;
    active_run_topic = next_topic;
    for (const listener of active_run_topic_listeners) {
        listener();
    }
}

export function topic_has_active_run(
    stream: string | undefined,
    topic: string | undefined,
): boolean {
    return (
        stream !== undefined &&
        topic !== undefined &&
        active_run_stream === stream.toLowerCase() &&
        active_run_topic === topic.toLowerCase()
    );
}

export function on_active_run_topic_changed(listener: () => void): () => void {
    active_run_topic_listeners.add(listener);
    return () => active_run_topic_listeners.delete(listener);
}

export function get_active_run_operation(
    message_id: number | undefined,
): PearRunOperation | undefined {
    return message_id === undefined ? undefined : active_run_operations.get(message_id);
}

export function get_active_run_started_at(message_id: number | undefined): number | undefined {
    if (message_id === undefined) {
        return undefined;
    }
    return active_run_started_at.get(message_id);
}

export function get_run_footer(message_id: number | undefined): PearRunFooter | undefined {
    if (message_id === undefined) {
        return undefined;
    }
    return settled_run_footers.get(message_id);
}

export function get_ambient_run_id(message_id: number | undefined): number | undefined {
    if (message_id === undefined) {
        return undefined;
    }
    return ambient_run_ids_by_result_message.get(message_id);
}

export function on_active_run_result_messages_changed(
    listener: (change: PearActiveResultMessageChange) => void,
): () => void {
    active_result_message_change_listeners.add(listener);
    return () => active_result_message_change_listeners.delete(listener);
}

export function clear_for_testing(): void {
    active_result_message_ids.clear();
    settled_run_footers.clear();
    ambient_run_ids_by_result_message.clear();
    active_run_started_at.clear();
    active_run_operations.clear();
    run_views_by_id.clear();
    run_ids_by_result_message.clear();
    latest_sequence_by_run_id.clear();
    active_run_stream = undefined;
    active_run_topic = undefined;
    notify_active_result_message_change_listeners({removed_message_ids: []});
}
