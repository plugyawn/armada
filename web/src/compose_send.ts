import assert from "minimalistic-assert";
import * as z from "zod/mini";

import * as drafts from "./drafts.ts";
import * as echo from "./echo.ts";
import type {Message} from "./message_store.ts";
import * as sent_messages from "./sent_messages.ts";
import * as server_events_state from "./server_events_state.ts";
import {current_user} from "./state_data.ts";
import * as transmit from "./transmit.ts";
import * as util from "./util.ts";

const send_message_api_response_schema = z.object({
    id: z.number(),
    automatic_new_visibility_policy: z.optional(z.number()),
});

export type SendMessageAPIResponse = z.output<typeof send_message_api_response_schema>;

export type ResolvedComposeRecipient =
    | {
          type: "stream";
          stream_id: number;
          topic: string;
      }
    | {
          type: "private";
          user_ids: number[];
          recipient_emails: string;
      };

type SendMessageBase = {
    sender_id: number;
    queue_id: string | null;
    to: string;
    content: string;
    draft_id?: string | undefined;
    agent_followup?: string;
    thread_root_message_id?: number | undefined;
};

export type ComposeSendMessageData = SendMessageBase &
    (
        | {
              type: "stream";
              stream_id: number;
              topic: string;
          }
        | {
              type: "private";
              stream_id: undefined;
              topic: "";
              reply_to: string;
              private_message_recipient: string;
              to_user_ids: string;
          }
    );

export type SentComposeMessageData<T extends ComposeSendMessageData = ComposeSendMessageData> =
    T & {
        local_id: string;
        locally_echoed: boolean;
        resend: false;
    };

export function build_message_data(
    args: Parameters<typeof build_message_data_impl>[0] & {draft_id: string},
): ComposeSendMessageData & {draft_id: string};
export function build_message_data(
    args: Parameters<typeof build_message_data_impl>[0],
): ComposeSendMessageData;
export function build_message_data(
    args: Parameters<typeof build_message_data_impl>[0],
): ComposeSendMessageData {
    return build_message_data_impl(args);
}

function build_message_data_impl({
    recipient,
    content,
    draft_id,
    agent_followup,
    thread_root_message_id,
}: {
    recipient: ResolvedComposeRecipient;
    content: string;
    draft_id?: string;
    agent_followup?: boolean;
    thread_root_message_id?: number;
}): ComposeSendMessageData {
    const common = {
        sender_id: current_user.user_id,
        queue_id: server_events_state.queue_id,
        content,
        ...(draft_id === undefined ? {} : {draft_id}),
        ...(agent_followup ? {agent_followup: JSON.stringify(true)} : {}),
        ...(thread_root_message_id === undefined ? {} : {thread_root_message_id}),
    };
    if (recipient.type === "stream") {
        return {
            ...common,
            type: "stream",
            stream_id: recipient.stream_id,
            topic: util.is_topic_name_considered_empty(recipient.topic) ? "" : recipient.topic,
            to: JSON.stringify([recipient.stream_id]),
        };
    }
    return {
        ...common,
        type: "private",
        stream_id: undefined,
        topic: "",
        to: JSON.stringify(recipient.user_ids),
        reply_to: recipient.recipient_emails,
        private_message_recipient: recipient.recipient_emails,
        to_user_ids: util.sorted_ids(recipient.user_ids).join(","),
    };
}

export function send_message<T extends ComposeSendMessageData>({
    message_data,
    local_message,
    on_success,
    on_error,
}: {
    message_data: T;
    local_message?: Message;
    on_success: (sent_message: SentComposeMessageData<T>, data: SendMessageAPIResponse) => void;
    on_error: (
        response: string,
        server_error_code: string,
        sent_message: SentComposeMessageData<T>,
        local_message: Message | undefined,
    ) => void;
}): SentComposeMessageData<T> {
    const locally_echoed = local_message !== undefined;
    let local_id: string;
    if (local_message !== undefined) {
        assert(local_message.local_id !== undefined);
        local_id = local_message.local_id;
    } else {
        local_id = sent_messages.get_new_local_id();
    }
    const sent_message: SentComposeMessageData<T> = {
        ...message_data,
        local_id,
        locally_echoed,
        resend: false,
    };

    transmit.send_message(
        sent_message,
        (raw_data) => {
            on_success(sent_message, send_message_api_response_schema.parse(raw_data));
        },
        (response, server_error_code) => {
            on_error(response, server_error_code, sent_message, local_message);
        },
    );
    server_events_state.assert_get_events_running(
        "Restarting get_events because it was not running during send",
    );

    if (local_message !== undefined) {
        setTimeout(() => {
            echo.display_slow_send_loading_spinner(local_message);
        }, 5000);
    }
    return sent_message;
}

export function finalize_message_success(
    sent_message: {local_id: string; draft_id?: string | undefined},
    data: SendMessageAPIResponse,
): void {
    echo.reify_message_id(sent_message.local_id, data.id);
    if (sent_message.draft_id !== undefined) {
        drafts.draft_model.deleteDrafts([sent_message.draft_id]);
    }
}
