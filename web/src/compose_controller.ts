import assert from "minimalistic-assert";

import * as compose_send from "./compose_send.ts";
import * as drafts from "./drafts.ts";
import * as echo from "./echo.ts";
import * as message_events from "./message_events.ts";
import * as message_viewport from "./message_viewport.ts";
import * as reload from "./reload.ts";
import * as zcommand from "./zcommand.ts";

export type ComposeDestination = {
    recipient: compose_send.ResolvedComposeRecipient;
    pear_followup?: boolean;
    thread_root_message_id?: number;
};

export type ControllerSentMessage = compose_send.SentComposeMessageData<
    compose_send.ComposeSendMessageData & {draft_id: string}
>;

export type ComposeControllerAdapter = {
    get_content: () => string;
    get_destination: () => ComposeDestination | undefined;
    validate: (scheduling_message: boolean) => boolean;
    save_draft: (is_sending_saving: boolean) => string | undefined;
    is_busy: () => boolean;
    set_busy: (busy: boolean) => void;
    clear: () => void;
    show_send_error: (response: string, server_error_code: string) => void;
    stop_typing: () => void;
    before_finish?: () => void;
    before_send?: () => void;
    dispatch_send?: () => void;
    schedule?: () => void;
    after_success?: (
        sent_message: ControllerSentMessage,
        data: compose_send.SendMessageAPIResponse,
    ) => void;
    update_typing?: () => void;
    process_command?: (content: string) => boolean;
    draft_save_delay_ms?: number;
};

export type ComposeController = {
    finish: (scheduling_message?: boolean) => boolean | undefined;
    send: () => boolean;
    handle_success: (
        sent_message: ControllerSentMessage,
        data: compose_send.SendMessageAPIResponse,
    ) => void;
    notify_content_changed: () => void;
    flush_draft: () => void;
    destroy: () => void;
};

export function run_post_send_tasks(stop_typing: () => void): void {
    message_viewport.bottom_of_feed.reset();
    stop_typing();
    reload.maybe_reset_pending_reload_timeout("compose_end");
}

export function create(adapter: ComposeControllerAdapter): ComposeController {
    let destroyed = false;
    let draft_timer: ReturnType<typeof setTimeout> | undefined;

    function flush_draft(): void {
        if (draft_timer !== undefined) {
            clearTimeout(draft_timer);
            draft_timer = undefined;
        }
        if (!destroyed) {
            adapter.save_draft(false);
        }
    }

    function notify_content_changed(): void {
        adapter.update_typing?.();
        if (adapter.draft_save_delay_ms === undefined) {
            return;
        }
        if (draft_timer !== undefined) {
            clearTimeout(draft_timer);
        }
        draft_timer = setTimeout(() => {
            draft_timer = undefined;
            if (!destroyed) {
                adapter.save_draft(false);
            }
        }, adapter.draft_save_delay_ms);
    }

    function handle_success(
        sent_message: ControllerSentMessage,
        data: compose_send.SendMessageAPIResponse,
    ): void {
        compose_send.finalize_message_success(sent_message, data);
        if (destroyed) {
            return;
        }
        if (!sent_message.locally_echoed) {
            adapter.clear();
        }
        adapter.set_busy(false);
        adapter.after_success?.(sent_message, data);
    }

    function reset_failed_draft(draft_id: string): void {
        const draft = drafts.draft_model.getDraft(draft_id);
        if (draft !== false) {
            draft.is_sending_saving = false;
            drafts.draft_model.editDraft(draft_id, draft);
        }
    }

    function send(): boolean {
        if (destroyed) {
            return false;
        }
        adapter.before_send?.();
        const destination = adapter.get_destination();
        if (destination === undefined) {
            adapter.set_busy(false);
            return false;
        }
        const draft_id = adapter.save_draft(true);
        assert(draft_id !== undefined);
        const message_data = compose_send.build_message_data({
            recipient: destination.recipient,
            content: adapter.get_content(),
            draft_id,
            ...(destination.pear_followup === undefined
                ? {}
                : {pear_followup: destination.pear_followup}),
            ...(destination.thread_root_message_id === undefined
                ? {}
                : {thread_root_message_id: destination.thread_root_message_id}),
        });
        const local_message = echo.try_deliver_locally(
            message_data,
            message_events.insert_new_messages,
        );
        compose_send.send_message({
            message_data,
            ...(local_message === undefined ? {} : {local_message}),
            on_success: handle_success,
            on_error(response, server_error_code, _sent_message, failed_local_message) {
                if (failed_local_message !== undefined) {
                    echo.message_send_error(failed_local_message.id, response);
                    drafts.sync_count();
                    reset_failed_draft(draft_id);
                    return;
                }
                reset_failed_draft(draft_id);
                if (destroyed) {
                    return;
                }
                adapter.set_busy(false);
                adapter.show_send_error(response, server_error_code);
            },
        });
        if (local_message !== undefined) {
            adapter.clear();
            adapter.set_busy(false);
        }
        return true;
    }

    function finish(scheduling_message = false): boolean | undefined {
        if (destroyed || adapter.is_busy()) {
            return undefined;
        }
        adapter.before_finish?.();
        const process_command = adapter.process_command ?? zcommand.process;
        if (process_command(adapter.get_content())) {
            run_post_send_tasks(adapter.stop_typing);
            adapter.clear();
            return undefined;
        }
        adapter.set_busy(true);
        if (!adapter.validate(scheduling_message)) {
            adapter.set_busy(false);
            return false;
        }
        if (scheduling_message) {
            assert(adapter.schedule !== undefined);
            adapter.schedule();
        } else if (adapter.dispatch_send !== undefined) {
            adapter.dispatch_send();
        } else {
            send();
        }
        run_post_send_tasks(adapter.stop_typing);
        return true;
    }

    return {
        finish,
        send,
        handle_success,
        notify_content_changed,
        flush_draft,
        destroy() {
            if (adapter.is_busy()) {
                if (draft_timer !== undefined) {
                    clearTimeout(draft_timer);
                    draft_timer = undefined;
                }
            } else {
                flush_draft();
            }
            destroyed = true;
            adapter.stop_typing();
        },
    };
}
