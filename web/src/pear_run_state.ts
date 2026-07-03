type PearRunForActiveMessage = {
    status: string;
    result_message_id?: number | null;
};

type PearActiveResultMessageChange = {
    removed_message_ids: number[];
};

const active_result_message_ids = new Set<number>();
const active_result_message_change_listeners = new Set<
    (change: PearActiveResultMessageChange) => void
>();
let active_result_message_ids_refreshed_at = 0;
let active_result_message_expiry_timer: ReturnType<typeof setTimeout> | undefined;

const ACTIVE_RESULT_MESSAGE_TTL_MS = 15_000;

function is_active_status(status: string): boolean {
    return status === "queued" || status === "running";
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
    active_result_message_expiry_timer = setTimeout(() => {
        active_result_message_expiry_timer = undefined;
        if (!expire_stale_active_run_result_messages()) {
            schedule_active_result_message_expiry();
        }
    }, ACTIVE_RESULT_MESSAGE_TTL_MS);
}

export function set_active_run_result_messages(
    runs: PearRunForActiveMessage[],
    options: {force_reconcile?: boolean} = {},
): void {
    const next_active_result_message_ids = new Set<number>();
    for (const run of runs) {
        const result_message_id = run.result_message_id;
        if (
            is_active_status(run.status) &&
            typeof result_message_id === "number" &&
            Number.isInteger(result_message_id) &&
            result_message_id > 0
        ) {
            next_active_result_message_ids.add(result_message_id);
        }
    }
    active_result_message_ids_refreshed_at = Date.now();
    if (sets_equal(active_result_message_ids, next_active_result_message_ids)) {
        schedule_active_result_message_expiry();
        if (options.force_reconcile === true) {
            notify_active_result_message_change_listeners({removed_message_ids: []});
        }
        return;
    }
    const removed_message_ids = removed_active_result_message_ids(next_active_result_message_ids);
    active_result_message_ids.clear();
    for (const message_id of next_active_result_message_ids) {
        active_result_message_ids.add(message_id);
    }
    schedule_active_result_message_expiry();
    notify_active_result_message_change_listeners({removed_message_ids});
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

export function on_active_run_result_messages_changed(
    listener: (change: PearActiveResultMessageChange) => void,
): () => void {
    active_result_message_change_listeners.add(listener);
    return () => active_result_message_change_listeners.delete(listener);
}

export function clear_for_testing(): void {
    active_result_message_ids.clear();
    active_result_message_ids_refreshed_at = 0;
    clear_active_result_message_expiry_timer();
    notify_active_result_message_change_listeners({removed_message_ids: []});
}
