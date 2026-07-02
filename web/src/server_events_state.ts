import type {StateData} from "./state_data";

export let queue_id: string | null;
export let assert_get_events_running: (error_message: string) => void;
export let restart_get_events: () => void;

// Mirrors zerver.lib.pear.PEAR_EMBEDDED_BOT_SERVICE. The authoritative
// initial Pear bot identity still comes from the server via pear_bot_user_ids.
const PEAR_EMBEDDED_BOT_SERVICE = "pear";

let pear_bot_user_ids = new Set<number>();

// True once /json/events has successfully returned at least once
// since this page's queue was created. Until then, the people store
// may legitimately lack users that exist server-side but were created
// after the /register snapshot's last_event_id; report_late_add uses
// this to demote "Added user late" from blueslip.error to a log.
let first_events_response_received = false;

export function has_received_first_events_response(): boolean {
    return first_events_response_received;
}

export function mark_first_events_response_received(): void {
    first_events_response_received = true;
}

function services_are_definitive(services: unknown): boolean {
    return (
        Array.isArray(services) &&
        services.some(
            (service) =>
                typeof service === "object" &&
                service !== null &&
                Object.hasOwn(service, "service_name"),
        )
    );
}

function services_include_pear(services: unknown): boolean {
    return (
        Array.isArray(services) &&
        services.some(
            (service) =>
                typeof service === "object" &&
                service !== null &&
                "service_name" in service &&
                service.service_name === PEAR_EMBEDDED_BOT_SERVICE,
        )
    );
}

export function is_pear_bot_user_id(user_id: number): boolean {
    return pear_bot_user_ids.has(user_id);
}

export function add_pear_bot_user_id(user_id: number): void {
    pear_bot_user_ids.add(user_id);
}

export function remove_pear_bot_user_id(user_id: number): void {
    pear_bot_user_ids.delete(user_id);
}

export function maybe_update_pear_bot_user_id_from_services(
    user_id: number,
    services: unknown,
): void {
    if (!services_are_definitive(services)) {
        return;
    }

    if (services_include_pear(services)) {
        add_pear_bot_user_id(user_id);
    } else {
        remove_pear_bot_user_id(user_id);
    }
}

export function initialize(
    params: StateData["server_events_state"] & {
        assert_get_events_running: (error_message: string) => void;
        restart_get_events: () => void;
    },
): void {
    queue_id = params.queue_id;
    assert_get_events_running = params.assert_get_events_running;
    restart_get_events = params.restart_get_events;
    pear_bot_user_ids = new Set(params.pear_bot_user_ids ?? []);
}
