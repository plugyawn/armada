type PearRunForActiveMessage = {
    status: string;
    result_message_id?: number | null;
};

const active_result_message_ids = new Set<number>();
const active_result_message_change_listeners = new Set<() => void>();

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

function notify_active_result_message_change_listeners(): void {
    for (const listener of active_result_message_change_listeners) {
        listener();
    }
}

export function set_active_run_result_messages(runs: PearRunForActiveMessage[]): void {
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
    if (sets_equal(active_result_message_ids, next_active_result_message_ids)) {
        return;
    }
    active_result_message_ids.clear();
    for (const message_id of next_active_result_message_ids) {
        active_result_message_ids.add(message_id);
    }
    notify_active_result_message_change_listeners();
}

export function is_active_run_result_message(message_id: number | undefined): boolean {
    return message_id !== undefined && active_result_message_ids.has(message_id);
}

export function on_active_run_result_messages_changed(listener: () => void): () => void {
    active_result_message_change_listeners.add(listener);
    return () => active_result_message_change_listeners.delete(listener);
}
