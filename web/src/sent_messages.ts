import * as blueslip from "./blueslip.ts";

export let next_local_id = 0;
export const messages = new Map<string, MessageState>();

export function get_new_local_id(): string {
    next_local_id += 1;
    const local_id = next_local_id;
    return "loc-" + local_id.toString();
}

export class MessageState {
    local_id: string;

    server_acked = false;
    saw_event = false;

    constructor(local_id: string) {
        this.local_id = local_id;
    }

    report_server_ack(): void {
        this.server_acked = true;
        this.maybe_finish();
    }

    report_event_received(): void {
        this.saw_event = true;
        this.maybe_finish();
    }

    maybe_finish(): void {
        if (!this.saw_event || !this.server_acked) {
            return;
        }
        messages.delete(this.local_id);
    }
}

export function start_tracking_message(local_id: string): void {
    if (!local_id) {
        blueslip.error("You must supply a local_id");
        return;
    }

    if (messages.has(local_id)) {
        blueslip.error("We are reusing a local_id");
        return;
    }

    const state = new MessageState(local_id);

    messages.set(local_id, state);
}

export function get_message_state(local_id: string): MessageState | undefined {
    const state = messages.get(local_id);

    if (!state) {
        blueslip.warn("Unknown local_id: " + local_id);
    }

    return state;
}

export function report_event_received(local_id: string): void {
    if (local_id === undefined) {
        return;
    }
    const state = get_message_state(local_id);
    if (!state) {
        return;
    }

    state.report_event_received();
}
