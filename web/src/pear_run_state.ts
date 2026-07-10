type PearRunForActiveMessage = {
    status: string;
    result_message_id?: number | null | undefined;
    tool_count?: number | undefined;
    date_created?: number | undefined;
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
          }
        | null
        | undefined;
};

export type PearRunOperation = {
    kind: string;
    label: string;
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
// Cumulative across topic switches — footers are historical facts, so
// they never expire with the active-set TTL.
const settled_run_footers = new Map<number, PearRunFooter>();
// Active runs' start times (seconds), for the live elapsed counter.
const active_run_started_at = new Map<number, number>();
// A long engine operation (compaction, …) currently animating in the
// active run's footer, keyed by result message.
const active_run_operations = new Map<number, PearRunOperation>();
const active_result_message_change_listeners = new Set<
    (change: PearActiveResultMessageChange) => void
>();
let active_result_message_ids_refreshed_at = 0;
let active_result_message_expiry_timer: ReturnType<typeof setTimeout> | undefined;

const ACTIVE_RESULT_MESSAGE_TTL_MS = 15_000;

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

function clear_active_result_message_expiry_timer(): void {
    if (active_result_message_expiry_timer === undefined) {
        return;
    }
    clearTimeout(active_result_message_expiry_timer);
    active_result_message_expiry_timer = undefined;
}

function schedule_active_result_message_expiry(): void {
    if (active_result_message_ids.size === 0) {
        clear_active_result_message_expiry_timer();
        return;
    }
    if (active_result_message_expiry_timer !== undefined) {
        return;
    }
    // Arm for the time remaining until refreshed_at + TTL, not a full TTL:
    // a timer armed before the latest refresh would otherwise fire early,
    // see fresh data, and re-arm for another full TTL — drifting expiry to
    // anywhere in (TTL, 2*TTL] after the last refresh.
    active_result_message_expiry_timer = setTimeout(() => {
        active_result_message_expiry_timer = undefined;
        if (!expire_stale_active_run_result_messages()) {
            schedule_active_result_message_expiry();
        }
    }, active_result_message_expiry_delay_for_testing(Date.now()));
}

export function active_result_message_expiry_delay_for_testing(now: number): number {
    return Math.max(active_result_message_ids_refreshed_at + ACTIVE_RESULT_MESSAGE_TTL_MS - now, 0);
}

export function set_active_run_result_messages(
    runs: PearRunForActiveMessage[],
    options: {force_reconcile?: boolean} = {},
): void {
    const next_active_result_message_ids = new Set<number>();
    const footer_changed_message_ids: number[] = [];
    for (const run of runs) {
        const result_message_id = run.result_message_id;
        if (
            typeof result_message_id !== "number" ||
            !Number.isInteger(result_message_id) ||
            result_message_id <= 0
        ) {
            continue;
        }
        if (is_active_status(run.status)) {
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
        } else if (is_settled_status(run.status)) {
            active_run_started_at.delete(result_message_id);
            active_run_operations.delete(result_message_id);
            const footer = footer_from_run(run);
            if (!footers_equal(settled_run_footers.get(result_message_id), footer)) {
                settled_run_footers.set(result_message_id, footer);
                footer_changed_message_ids.push(result_message_id);
            }
        }
    }
    active_result_message_ids_refreshed_at = Date.now();
    if (sets_equal(active_result_message_ids, next_active_result_message_ids)) {
        schedule_active_result_message_expiry();
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
    schedule_active_result_message_expiry();
    notify_active_result_message_change_listeners({
        removed_message_ids,
        footer_changed_message_ids,
    });
}

export function expire_stale_active_run_result_messages(): boolean {
    if (active_result_message_ids.size === 0) {
        return false;
    }
    if (Date.now() - active_result_message_ids_refreshed_at < ACTIVE_RESULT_MESSAGE_TTL_MS) {
        return false;
    }
    const removed_message_ids = [...active_result_message_ids];
    active_result_message_ids.clear();
    clear_active_result_message_expiry_timer();
    notify_active_result_message_change_listeners({removed_message_ids});
    return true;
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

export function on_active_run_result_messages_changed(
    listener: (change: PearActiveResultMessageChange) => void,
): () => void {
    active_result_message_change_listeners.add(listener);
    return () => active_result_message_change_listeners.delete(listener);
}

export function clear_for_testing(): void {
    active_result_message_ids.clear();
    settled_run_footers.clear();
    active_run_started_at.clear();
    active_run_stream = undefined;
    active_run_topic = undefined;
    active_result_message_ids_refreshed_at = 0;
    clear_active_result_message_expiry_timer();
    notify_active_result_message_change_listeners({removed_message_ids: []});
}
