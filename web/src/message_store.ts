import _ from "lodash";
import * as z from "zod/mini";

import * as blueslip from "./blueslip.ts";
import type {RawLocalMessage} from "./echo.ts";
import type {LocalMessage, NewMessage, ProcessedMessage} from "./message_helper.ts";
import type {TimeFormattedReminder} from "./message_reminder.ts";
import * as people from "./people.ts";
import * as stream_data from "./stream_data.ts";
import {topic_link_schema} from "./types.ts";
import type {UserStatusEmojiInfo} from "./user_status.ts";
import * as util from "./util.ts";

const stored_messages = new Map<number, ProcessedMessage>();

const matched_message_schema = z.object({
    match_content: z.optional(z.string()),
    match_subject: z.optional(z.string()),
});

export type MatchedMessage = z.infer<typeof matched_message_schema>;

const message_reaction_type_schema = z.enum(["unicode_emoji", "realm_emoji", "zulip_extra_emoji"]);

export type MessageReactionType = z.infer<typeof message_reaction_type_schema>;

const display_recipient_user_schema = z.object({
    email: z.string(),
    full_name: z.string(),
    id: z.number(),
});

export type DisplayRecipientUser = z.infer<typeof display_recipient_user_schema>;

const display_recipient_schema = z.union([z.string(), z.array(display_recipient_user_schema)]);

export type DisplayRecipient = z.infer<typeof display_recipient_schema>;

export const message_thread_participant_schema = z.object({
    user_id: z.number(),
    full_name: z.string(),
    avatar_url: z.nullish(z.string()),
});

export const message_thread_reply_summary_schema = z.object({
    reply_count: z.number(),
    unread_reply_count: z.number(),
    last_reply_at: z.number(),
    last_sender: z.nullable(message_thread_participant_schema),
    participants: z.array(message_thread_participant_schema),
});

export type MessageThreadReplySummary = z.infer<typeof message_thread_reply_summary_schema>;

// The server-pushed summary update for a thread root whose reply set
// changed. Unlike the fetch-hydrated summary above, it carries no
// per-viewer unread count; the client preserves its local unread accounting
// when applying it. A null summary means the thread's last reply was
// deleted.
export const message_thread_summary_fields_schema = z.object({
    reply_count: z.number(),
    last_reply_at: z.number(),
    last_sender: z.nullable(message_thread_participant_schema),
    participants: z.array(message_thread_participant_schema),
});

export type MessageThreadSummaryFields = z.infer<typeof message_thread_summary_fields_schema>;

export const message_thread_summary_event_schema = z.object({
    id: z.number(),
    type: z.literal("message_thread_summary"),
    root_message_id: z.number(),
    summary: z.nullable(message_thread_summary_fields_schema),
});

export type MessageThreadSummaryEvent = z.infer<typeof message_thread_summary_event_schema>;

const message_edit_history_entry_schema = z.object({
    user_id: z.nullable(z.number()),
    timestamp: z.number(),
    prev_content: z.optional(z.string()),
    prev_rendered_content: z.optional(z.string()),
    prev_stream: z.optional(z.number()),
    prev_topic: z.optional(z.string()),
    stream: z.optional(z.number()),
    topic: z.optional(z.string()),
});

export type MessageEditHistoryEntry = z.infer<typeof message_edit_history_entry_schema>;

const message_reaction_schema = z.object({
    emoji_name: z.string(),
    emoji_code: z.string(),
    reaction_type: message_reaction_type_schema,
    user_id: z.number(),
});

export type MessageReaction = z.infer<typeof message_reaction_schema>;

export const single_message_content_schema = z.object({
    message: z.object({
        content: z.string(),
        content_type: z.enum(["text/html", "text/x-markdown"]),
    }),
});

export const message_render_response_schema = z.object({
    msg: z.string(),
    result: z.string(),
    rendered: z.string(),
});

export const submessage_schema = z.object({
    id: z.number(),
    sender_id: z.number(),
    message_id: z.number(),
    content: z.string(),
    msg_type: z.string(),
});

export const raw_message_schema = z.intersection(
    z.intersection(
        z.object({
            avatar_url: z.nullable(z.string()),
            client: z.string(),
            content: z.string(),
            content_type: z.enum(["text/html", "text/x-markdown"]),
            display_recipient: display_recipient_schema,
            edit_history: z.optional(z.array(message_edit_history_entry_schema)),
            id: z.number(),
            is_me_message: z.boolean(),
            is_agent_message: z.optional(z.boolean()),
            reply_summary: z.optional(message_thread_reply_summary_schema),
            thread_root_message_id: z.optional(z.number()),
            last_edit_timestamp: z.optional(z.number()),
            last_moved_timestamp: z.optional(z.number()),
            reactions: z.array(message_reaction_schema),
            sender_email: z.string(),
            sender_full_name: z.string(),
            sender_id: z.number(),
            // The web app doesn't use sender_realm_str; ignore.
            // sender_realm_str: z.string(),
            submessages: z.array(submessage_schema),
            timestamp: z.number(),
            flags: z.array(z.string()),
        }),
        z.discriminatedUnion("type", [
            z.object({
                type: z.literal("private"),
                topic_links: z.optional(z.array(z.never())),
            }),
            z.object({
                type: z.literal("stream"),
                stream_id: z.number(),
                // Messages that come from the server use `subject`.
                // Messages that come from `send_message` use `topic`.
                subject: z.optional(z.string()),
                topic: z.optional(z.string()),
                topic_links: z.array(topic_link_schema),
            }),
        ]),
    ),
    matched_message_schema,
);

export type RawMessage = z.infer<typeof raw_message_schema>;

// We add these boolean properties to Raw message in
// `message_store.convert_raw_message_to_message_with_booleans` method.
type Booleans = {
    unread: boolean;
    historical: boolean;
    starred: boolean;
    mentioned: boolean;
    mentioned_me_directly: boolean;
    stream_wildcard_mentioned: boolean;
    topic_wildcard_mentioned: boolean;
    is_agent_message: boolean;
    collapsed: boolean;
    condensed?: boolean;
    alerted: boolean;
};

type RawMessageWithBooleans = (
    | Omit<RawMessage & {type: "private"}, "flags">
    | Omit<RawMessage & {type: "stream"}, "flags">
) &
    Booleans;

type LocalMessageWithBooleans = (
    | Omit<RawLocalMessage & {type: "private"}, "flags">
    | Omit<RawLocalMessage & {type: "stream"}, "flags">
) &
    Booleans;

export type MessageWithBooleans = RawMessageWithBooleans | LocalMessageWithBooleans;

export type MessageCleanReaction = {
    class: string;
    count: number;
    emoji_alt_code: boolean;
    emoji_code: string;
    emoji_name: string;
    is_realm_emoji: boolean;
    label: string;
    local_id: string;
    reaction_type: "zulip_extra_emoji" | "realm_emoji" | "unicode_emoji";
    user_ids: number[];
    vote_text: string;
};

export type Message = (
    | Omit<RawMessageWithBooleans & {type: "private"}, "reactions">
    | Omit<RawMessageWithBooleans & {type: "stream"}, "reactions" | "subject">
) & {
    clean_reactions: Map<string, MessageCleanReaction>;

    // Local echo state cluster of fields.
    locally_echoed?: boolean;
    failed_request?: boolean;
    show_slow_send_spinner?: boolean;
    resend?: boolean;
    local_id?: string;

    // The original markup for the message, which we'll have if we
    // sent it or if we fetched it (usually, because the current user
    // tried to edit the message).
    raw_content?: string | undefined;

    // Added in `message_helper.process_new_message`.
    sent_by_me: boolean;
    reply_to: string;

    // These properties are set and used in `message_list_view.ts`.
    // TODO: It would be nice if we could not store these on the message
    // object and only reference them within `message_list_view`.
    message_reactions?: MessageCleanReaction[];
    url?: string;

    // Used in `markdown.js`, `server_events.js`, and
    // `convert_raw_message_to_message_with_booleans`
    flags?: string[];

    // Used in `message_avatar.hbs` to render sender avatar in
    // message list.
    small_avatar_url?: string | null;

    // Used in `message_body.hbs` to show sender status emoji alongside
    // their name in message list.
    status_emoji_info?: UserStatusEmojiInfo | undefined;

    // Used for edited messages to show their last edit time.
    local_edit_timestamp?: number;

    // Used in message_notifications to track if a notification has already
    // been sent for this message.
    notification_sent?: boolean;

    // Human replies are hydrated as a batched summary with ordinary messages;
    // reply bodies remain lazy and live in the shared message-thread shell.
    reply_summary?: MessageThreadReplySummary | undefined;

    // Added during message rendering in message_list_view.ts. Should
    // never be accessed outside rendering, as the value may be stale.
    reminders?: TimeFormattedReminder[] | undefined;

    // Cache for whether the message has widget edits (e.g. poll question changes).
    has_widget_edits?: boolean;
} & (
        | {
              type: "private";
              is_private: true;
              is_stream: false;
              pm_with_url: string;
              to_user_ids: string;
              display_reply_to: string;
          }
        | {
              type: "stream";
              is_private: false;
              is_stream: true;
              stream: string;
              topic: string;
              display_reply_to: undefined;
          }
    );

const thread_reply_ids_by_root_id = new Map<number, number[]>();
let thread_reply_event_generation = 0;
const latest_thread_reply_event_generation_by_root_id = new Map<number, number>();

export function capture_thread_summary_fetch_generation(): number {
    return thread_reply_event_generation;
}

export function mark_thread_reply_lifecycle(root_message_ids: Iterable<number>): void {
    const ids = [...new Set(root_message_ids)];
    if (ids.length === 0) {
        return;
    }
    thread_reply_event_generation += 1;
    for (const root_message_id of ids) {
        latest_thread_reply_event_generation_by_root_id.set(
            root_message_id,
            thread_reply_event_generation,
        );
    }
}

export function can_apply_thread_summary_from_fetch(
    root_message_id: number,
    fetch_generation: number,
): boolean {
    return (
        (latest_thread_reply_event_generation_by_root_id.get(root_message_id) ?? 0) <=
        fetch_generation
    );
}

function remove_thread_reply_from_index(message: Message): void {
    const root_id = message.thread_root_message_id;
    if (root_id === undefined) {
        return;
    }
    const ids = thread_reply_ids_by_root_id.get(root_id);
    if (ids === undefined) {
        return;
    }
    const index = ids.indexOf(message.id);
    if (index !== -1) {
        ids.splice(index, 1);
    }
    if (ids.length === 0) {
        thread_reply_ids_by_root_id.delete(root_id);
    }
}

function add_thread_reply_to_index(message: Message): void {
    const root_id = message.thread_root_message_id;
    if (root_id === undefined) {
        return;
    }
    const ids = thread_reply_ids_by_root_id.get(root_id) ?? [];
    let low = 0;
    let high = ids.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (ids[middle]! < message.id) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    if (ids[low] !== message.id) {
        ids.splice(low, 0, message.id);
    }
    thread_reply_ids_by_root_id.set(root_id, ids);
}

export function update_message_cache(message_data: ProcessedMessage): void {
    // You should only call this from message_helper (or in tests).
    // Submessages are append-only and arrive on a separate event stream.
    // A message fetch that started before a submessage event can finish after
    // it; replacing the singleton with that older payload would make the
    // widget or artifact disappear until another full fetch. Preserve every
    // locally known submessage while installing the fresh message payload.
    const existing_message = stored_messages.get(message_data.message.id)?.message;
    const existing_submessages = existing_message?.submessages ?? [];
    const incoming_submessages = message_data.message.submessages ?? [];
    if (existing_submessages.length > 0) {
        message_data.message.submessages = incoming_submessages;
        const incoming_ids = new Set(incoming_submessages.map((item) => item.id));
        for (const submessage of existing_submessages) {
            if (!incoming_ids.has(submessage.id)) {
                incoming_submessages.push(submessage);
            }
        }
        incoming_submessages.sort((left, right) => left.id - right.id);
    }
    if (existing_message !== undefined) {
        remove_thread_reply_from_index(existing_message);
    }
    stored_messages.set(message_data.message.id, message_data);
    add_thread_reply_to_index(message_data.message);
}

export function get_cached_message(message_id: number): ProcessedMessage | undefined {
    // You should only call this from message_helper.
    // Use the get() wrapper below for most other use cases.
    return stored_messages.get(message_id);
}

export function clear_for_testing(): void {
    stored_messages.clear();
    thread_reply_ids_by_root_id.clear();
    thread_reply_event_generation = 0;
    latest_thread_reply_event_generation_by_root_id.clear();
}

// This can return a LocalMessage, but unless anything needs that,
// it's easier to type it as just returning a Message.
// TODO: If we finish converting to typescript and find that
// nothing needs LocalMessage, explicitly remove its extra fields
// here before returning the Message.
export function get(message_id: number): Message | undefined {
    return stored_messages.get(message_id)?.message;
}

export function get_thread_replies(thread_root_message_id: number): Message[] {
    return (thread_reply_ids_by_root_id.get(thread_root_message_id) ?? []).flatMap((message_id) => {
        const message = stored_messages.get(message_id)?.message;
        return message === undefined ? [] : [message];
    });
}

export function get_thread_roots(): Message[] {
    return [...stored_messages.values()].flatMap(({message}) =>
        message.thread_root_message_id === undefined && message.reply_summary !== undefined
            ? [message]
            : [],
    );
}

export function apply_thread_summary(
    root_message_id: number,
    summary: MessageThreadSummaryFields | null,
): boolean {
    // Applies a server-pushed message_thread_summary event to the cached
    // root. Returns false when the root is not cached; a later fetch
    // hydrates the fresh summary anyway.
    const message = get(root_message_id);
    if (message === undefined) {
        return false;
    }
    // Fence fetches that began before this push from overwriting it (and the
    // local unread adjustments made since) with a stale hydrated summary.
    mark_thread_reply_lifecycle([root_message_id]);
    if (summary === null) {
        message.reply_summary = undefined;
        return true;
    }
    // The push carries no per-viewer unread count; preserve the local one.
    // With no prior summary, derive it from the cached replies, matching the
    // fallback in unread_reply_count readers.
    const preserved_unread_count =
        message.reply_summary?.unread_reply_count ??
        get_thread_replies(root_message_id).filter((reply) => reply.unread).length;
    message.reply_summary = {
        ...summary,
        unread_reply_count: Math.max(0, Math.min(preserved_unread_count, summary.reply_count)),
    };
    return true;
}

export function set_messages_for_tests(messages: ProcessedMessage[]): void {
    clear_for_testing();
    for (const message of messages) {
        update_message_cache(message);
    }
}

export function get_pm_emails(
    message: Message | MessageWithBooleans | LocalMessageWithBooleans,
): string {
    const user_ids = people.pm_with_user_ids(message) ?? [];
    const emails = user_ids.map((user_id) => {
        const person = people.maybe_get_user_by_id(user_id);
        if (!person) {
            blueslip.error("Unknown user id", {user_id});
            return "?";
        }
        return person.email;
    });
    emails.sort();

    return emails.join(", ");
}

export function get_pm_full_names(user_ids: number[]): string {
    user_ids = people.sorted_other_user_ids(user_ids);
    const sorted_names = people.get_display_full_names(user_ids);
    sorted_names.sort(util.make_strcmp());

    return sorted_names.join(", ");
}

export function convert_raw_message_to_message_with_booleans(opts: NewMessage):
    | {
          type: "server_message";
          message: RawMessageWithBooleans;
      }
    | {
          type: "local_message";
          message: LocalMessageWithBooleans;
      } {
    const flags = opts.raw_message.flags ?? [];

    function convert_flag(flag_name: string): boolean {
        return flags.includes(flag_name);
    }

    const converted_flags = {
        unread: !convert_flag("read"),
        historical: convert_flag("historical"),
        starred: convert_flag("starred"),
        mentioned:
            convert_flag("mentioned") ||
            convert_flag("stream_wildcard_mentioned") ||
            convert_flag("topic_wildcard_mentioned"),
        mentioned_me_directly: convert_flag("mentioned"),
        stream_wildcard_mentioned: convert_flag("stream_wildcard_mentioned"),
        topic_wildcard_mentioned: convert_flag("topic_wildcard_mentioned"),
        is_agent_message:
            opts.type === "server_message" && opts.raw_message.is_agent_message === true,
        collapsed: convert_flag("collapsed"),
        alerted: convert_flag("has_alert_word"),
    };

    // Once we have set boolean flags here, the `flags` attribute is
    // just a distraction, so we delete it.  (All the downstream code
    // uses booleans.)

    // We have to return these separately because of how the `MessageWithBooleans`
    // type is set up.
    if (opts.type === "local_message") {
        if (opts.raw_message.type === "private") {
            return {
                type: "local_message",
                message: {
                    ..._.omit(opts.raw_message, "flags"),
                    ...converted_flags,
                },
            };
        }
        return {
            type: "local_message",
            message: {
                ..._.omit(opts.raw_message, "flags"),
                ...converted_flags,
            },
        };
    }
    if (opts.raw_message.type === "private") {
        return {
            type: "server_message",
            message: {
                ..._.omit(opts.raw_message, "flags"),
                ...converted_flags,
            },
        };
    }
    return {
        type: "server_message",
        message: {
            ..._.omit(opts.raw_message, "flags"),
            ...converted_flags,
        },
    };
}

export function update_booleans(message: Message, flags: string[]): void {
    // When we get server flags for local echo or message edits,
    // we are vulnerable to race conditions, so only update flags
    // that are driven by message content.
    function convert_flag(flag_name: string): boolean {
        return flags.includes(flag_name);
    }

    message.mentioned =
        convert_flag("mentioned") ||
        convert_flag("stream_wildcard_mentioned") ||
        convert_flag("topic_wildcard_mentioned");
    message.mentioned_me_directly = convert_flag("mentioned");
    message.stream_wildcard_mentioned = convert_flag("stream_wildcard_mentioned");
    message.topic_wildcard_mentioned = convert_flag("topic_wildcard_mentioned");
    message.alerted = convert_flag("has_alert_word");
}

export function update_sender_full_name(user_id: number, new_name: string): void {
    for (const message_data of stored_messages.values()) {
        const message = message_data.message;
        if (message.sender_id && message.sender_id === user_id) {
            message.sender_full_name = new_name;
        }
    }
}

export function update_small_avatar_url(user_id: number, new_url: string | null): void {
    for (const message_data of stored_messages.values()) {
        const message = message_data.message;
        if (message.sender_id && message.sender_id === user_id) {
            message.small_avatar_url = new_url;
        }
    }
}

export function update_stream_name(stream_id: number, new_name: string): void {
    for (const message_data of stored_messages.values()) {
        const message = message_data.message;
        if (message.type === "stream" && message.stream_id === stream_id) {
            message.display_recipient = new_name;
        }
    }
}

export function update_status_emoji_info(
    user_id: number,
    new_info: UserStatusEmojiInfo | undefined,
): void {
    for (const message_data of stored_messages.values()) {
        const message = message_data.message;
        if (message.sender_id && message.sender_id === user_id) {
            message.status_emoji_info = new_info;
        }
    }
}

export function reify_message_id({old_id, new_id}: {old_id: number; new_id: number}): void {
    const message_data = stored_messages.get(old_id);
    if (message_data !== undefined) {
        const server_message: Message & Partial<LocalMessage> = message_data.message;
        remove_thread_reply_from_index(server_message);
        if (message_data.type === "local_message") {
            // Important: Messages are managed as singletons, so
            // MessageListData objects may already have pointers to
            // the LocalMessage object for this message. So we must
            // convert the LocalMessage into a Message by dropping the
            // extra local echo/drafts fields, not by constructing a
            // new object with the new type.

            delete server_message.queue_id;
            delete server_message.draft_id;
            delete server_message.to;
            if (server_message.type === "private") {
                delete server_message.topic;
            }
        }
        server_message.id = new_id;
        server_message.locally_echoed = false;
        stored_messages.set(new_id, {type: "server_message", message: server_message});
        stored_messages.delete(old_id);
        add_thread_reply_to_index(server_message);
    }
}

export function update_message_content(message: Message, new_content: string): void {
    message.content = new_content;
}

export function remove(message_ids: number[]): void {
    for (const message_id of message_ids) {
        const message = stored_messages.get(message_id)?.message;
        if (message !== undefined) {
            remove_thread_reply_from_index(message);
        }
        stored_messages.delete(message_id);
    }
}

export function get_message_ids_in_stream(stream_id: number): number[] {
    return [...stored_messages.values()]
        .filter(
            (message_data) =>
                message_data.message.type === "stream" &&
                message_data.message.stream_id === stream_id,
        )
        .map((message_data) => message_data.message.id);
}

export function maybe_update_raw_content(id: number, raw_content: string | undefined): void {
    const message = get(id);
    // In case the message was deleted from the cache after receiving a delete
    // event.
    if (message === undefined) {
        return;
    }
    // We shouldn't cache raw_content for messages we won't be receiving update events
    // for, which in this case are messages from channels the current user isn't
    // subscribed to.
    if (message.type === "stream" && !stream_data.is_subscribed(message.stream_id)) {
        // Clear any existing cached raw_content for this type of message.
        // Not doing so poses the risk of us using a stale version of the
        // raw_content after we manually fetch it.
        message.raw_content = undefined;
        return;
    }
    message.raw_content = raw_content;
}
