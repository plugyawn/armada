import $ from "jquery";
import * as z from "zod/mini";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import type {MessageList} from "./message_list.ts";
import * as message_lists from "./message_lists.ts";
import * as message_store from "./message_store.ts";
import type {Message} from "./message_store.ts";
import {mount_pear_artifact_lifecycle} from "./pear_artifact_lifecycle.ts";
import {any_widget_data_schema} from "./widget_schema.ts";
import type {WidgetOutboundData} from "./widget_schema.ts";
import * as widgetize from "./widgetize.ts";

export type Submessage = z.infer<typeof message_store.submessage_schema>;
const PEAR_ARTIFACT_SUBMESSAGE_TYPE = "pear_artifact";

function submessages_of_type(message: Message, msg_type: string): Submessage[] {
    return (message.submessages ?? []).filter((submessage) => submessage.msg_type === msg_type);
}

const widget_data_event_schema = z.object({
    sender_id: z.number(),
    data: any_widget_data_schema,
});

const inbound_data_event_schema = z.object({
    sender_id: z.number(),
    data: z.intersection(
        z.object({
            type: z.string(),
        }),
        z.record(z.string(), z.unknown()),
    ),
});

const submessages_event_schema = z.tuple([widget_data_event_schema], inbound_data_event_schema);

type SubmessageEvents = z.infer<typeof submessages_event_schema>;

export function get_message_events(message: Message): SubmessageEvents | undefined {
    if (message.locally_echoed) {
        return undefined;
    }

    const widget_submessages = submessages_of_type(message, "widget");
    if (widget_submessages.length === 0) {
        return undefined;
    }

    const sorted_widget_submessages = widget_submessages.toSorted((m1, m2) => m1.id - m2.id);

    const events: {sender_id: number; data: unknown}[] = [];

    for (const obj of sorted_widget_submessages) {
        let data: unknown;
        try {
            data = JSON.parse(obj.content);
        } catch {
            return undefined;
        }

        events.push({sender_id: obj.sender_id, data});
    }

    const parsed = submessages_event_schema.safeParse(events);
    if (!parsed.success) {
        return undefined;
    }

    return parsed.data;
}

export function is_poll_message(message: Message): boolean {
    if (message.submessages === undefined || message.submessages.length === 0) {
        return false;
    }

    try {
        const first_widget = submessages_of_type(message, "widget").toSorted(
            (m1, m2) => m1.id - m2.id,
        )[0];
        if (first_widget === undefined) {
            return false;
        }
        const first_data: unknown = JSON.parse(first_widget.content);
        return (
            typeof first_data === "object" &&
            first_data !== null &&
            "widget_type" in first_data &&
            first_data.widget_type === "poll"
        );
    } catch {
        return false;
    }
}

export function is_widget_edited(message: Message): boolean {
    if (message.has_widget_edits !== undefined) {
        return message.has_widget_edits;
    }

    if (message.submessages === undefined || message.submessages.length === 0) {
        message.has_widget_edits = false;
        return false;
    }

    if (!is_poll_message(message)) {
        message.has_widget_edits = false;
        return false;
    }

    // Check if any subsequent submessages are edits (question changes
    // or new options added after poll creation). Votes don't count.
    for (let i = 1; i < message.submessages.length; i += 1) {
        try {
            const data: unknown = JSON.parse(message.submessages[i]!.content);
            if (
                typeof data === "object" &&
                data !== null &&
                "type" in data &&
                (data.type === "question" || data.type === "new_option")
            ) {
                message.has_widget_edits = true;
                return true;
            }
        } catch {
            continue;
        }
    }

    message.has_widget_edits = false;
    return false;
}

export function render_widget_rows_in_list(list: MessageList | undefined): void {
    for (const message_id of widgetize.get_message_ids()) {
        const $row = list?.get_row(message_id);
        if ($row && $row.length > 0) {
            render_submessage({message_id, $row});
        }
    }
}

export function process_submessages(message: Message): void {
    // This happens in our rendering path, so we try to limit any
    // damage that may be triggered by one rogue message.
    try {
        do_process_submessages(message);
        return;
    } catch (error) {
        blueslip.error("Failed to do_process_submessages", undefined, error);
        return;
    }
}

export function do_process_submessages(message: Message): void {
    const artifacts = submessages_of_type(message, PEAR_ARTIFACT_SUBMESSAGE_TYPE);
    if (artifacts.some((artifact) => artifact.sender_id !== message.sender_id)) {
        blueslip.warn(`A user tried to attach a Pear artifact to message ${message.id}`);
    }
    const events = get_message_events(message);

    if (!events) {
        return;
    }
    const [widget_event, ...inbound_events] = events;

    if (widget_event.sender_id !== message.sender_id) {
        blueslip.warn(`User ${widget_event.sender_id} tried to hijack message ${message.id}`);
        return;
    }

    // Right now, our only use of submessages is widgets.

    const any_data = widget_event.data;

    widgetize.activate({
        any_data,
        events: inbound_events,
        message,
    });
}

async function load_and_render_pear_artifact(
    message: Message,
    artifact: Submessage,
    $host: JQuery,
): Promise<void> {
    try {
        const pear_artifact = await import("./pear_artifact.ts");
        mount_pear_artifact_lifecycle($host, "chat", "rendering", "rendering artifact…");
        const document_value = pear_artifact.parse_pear_artifact(artifact.content);
        if (document_value === undefined) {
            blueslip.warn(`Invalid Pear artifact on message ${message.id}`);
            mount_pear_artifact_lifecycle(
                $host,
                "chat",
                "failed",
                "artifact could not be rendered",
            );
            return;
        }
        pear_artifact.mount_pear_artifact($host, document_value, {
            density: "chat",
            onAction(action, button) {
                if (message.type !== "stream" || button.disabled) {
                    return;
                }
                const original = button.textContent ?? action.label;
                button.disabled = true;
                button.setAttribute("aria-busy", "true");
                button.textContent = "asking…";
                void (async () => {
                    try {
                        const [pear_artifact_actions, stream_data] = await Promise.all([
                            import("./pear_artifact_actions.ts"),
                            import("./stream_data.ts"),
                        ]);
                        const subscription = stream_data.get_sub_by_id(message.stream_id);
                        if (subscription === undefined) {
                            throw new Error("stream subscription unavailable");
                        }
                        pear_artifact_actions.send_pear_artifact_action(
                            {stream: subscription.name, topic: message.topic},
                            action.prompt,
                            {
                                success() {
                                    button.removeAttribute("aria-busy");
                                    button.textContent = "asked ✓";
                                },
                                error() {
                                    button.disabled = false;
                                    button.removeAttribute("aria-busy");
                                    button.textContent = original;
                                },
                            },
                        );
                    } catch (error) {
                        blueslip.error("Could not load Pear artifact actions", undefined, error);
                        button.disabled = false;
                        button.removeAttribute("aria-busy");
                        button.textContent = original;
                    }
                })();
            },
            onPin(button) {
                if (message.type !== "stream" || button.disabled) {
                    return;
                }
                const original = button.textContent ?? "↗ pin to wall";
                button.disabled = true;
                button.setAttribute("aria-busy", "true");
                button.textContent = "pinning…";
                void (async () => {
                    try {
                        const stream_data = await import("./stream_data.ts");
                        const subscription = stream_data.get_sub_by_id(message.stream_id);
                        if (subscription === undefined) {
                            throw new Error("stream subscription unavailable");
                        }
                        void channel.post({
                            url: "/json/pear/monitors",
                            data: {
                                stream: subscription.name,
                                topic: message.topic,
                                request: document_value.title ?? "pinned artifact",
                                spec: JSON.stringify({
                                    title: document_value.title,
                                    cadence_seconds: 300,
                                    artifact: document_value,
                                    recipe: {parse: {mode: "static"}, bindings: {}},
                                }),
                            },
                            success() {
                                button.removeAttribute("aria-busy");
                                button.textContent = "pinned ✓";
                                void refresh_pear_wall();
                            },
                            error() {
                                button.disabled = false;
                                button.removeAttribute("aria-busy");
                                button.textContent = "pin failed — retry";
                            },
                        });
                    } catch (error) {
                        blueslip.error("Could not load Pear pin action", undefined, error);
                        button.disabled = false;
                        button.removeAttribute("aria-busy");
                        button.textContent = original;
                    }
                })();
            },
        });
    } catch (error) {
        blueslip.error("Could not load the Pear artifact renderer", undefined, error);
        mount_pear_artifact_lifecycle($host, "chat", "failed", "artifact renderer unavailable");
    }
}

async function refresh_pear_wall(): Promise<void> {
    try {
        const pear_wall = await import("./pear_wall.ts");
        pear_wall.refresh();
    } catch (error) {
        blueslip.error("Could not refresh the Pear wall", undefined, error);
    }
}

export function render_submessage(in_opts: {$row: JQuery; message_id: number}): void {
    const message_id = in_opts.message_id;
    const message = message_store.get(message_id);
    if (!message) {
        return;
    }

    const $row = in_opts.$row;

    const artifacts = submessages_of_type(message, PEAR_ARTIFACT_SUBMESSAGE_TYPE)
        .filter((artifact) => artifact.sender_id === message.sender_id)
        .toSorted((m1, m2) => m1.id - m2.id);
    const latest_artifact = artifacts.at(-1);
    if (latest_artifact !== undefined) {
        const $content = $row.find(".message_content").first();
        let $host = $content.children(".pear-artifact-message-host");
        if ($host.length === 0) {
            const host = document.createElement("div");
            host.className = "pear-artifact-message-host";
            $content[0]?.append(host);
            $host = $(host);
        }
        const artifact_id = String(latest_artifact.id);
        if (
            $host.attr("data-pear-artifact-id") === artifact_id &&
            $host.children('.pear-artifact[data-lifecycle="ready"]').length > 0
        ) {
            return;
        }
        $host.attr("data-pear-artifact-id", artifact_id);
        mount_pear_artifact_lifecycle($host, "chat", "preparing", "preparing artifact…");
        void load_and_render_pear_artifact(message, latest_artifact, $host);
        return;
    }

    const post_to_server = make_server_callback(message_id);

    widgetize.render({
        $row,
        message,
        post_to_server,
    });
}

export function update_message(message: Message, submsg: Submessage): void {
    const existing = message.submessages.find((sm) => sm.id === submsg.id);

    if (existing !== undefined) {
        blueslip.warn("Got submessage multiple times: " + submsg.id);
        return;
    }

    message.submessages.push(submsg);
}

export function handle_event(submsg: Submessage): void {
    // Update message.submessages in case we haven't actually
    // activated the widget yet, so that when the message does
    // come in view, the data will be complete.
    const message = message_store.get(submsg.message_id);

    if (message === undefined) {
        // This is generally not a problem--the server
        // can send us events without us having received
        // the original message, since the server doesn't
        // track that.
        return;
    }

    update_message(message, submsg);

    const msg_type = submsg.msg_type;

    if (msg_type === PEAR_ARTIFACT_SUBMESSAGE_TYPE) {
        const $row = message_lists.current?.get_row(submsg.message_id);
        if ($row && $row.length > 0) {
            render_submessage({message_id: submsg.message_id, $row});
        }
        return;
    }
    if (msg_type !== "widget") {
        blueslip.warn("unknown msg_type: " + msg_type);
        return;
    }

    let data: unknown;

    try {
        data = JSON.parse(submsg.content);
    } catch {
        blueslip.warn("server sent us invalid json in handle_event: " + submsg.content);
        return;
    }

    const post_to_server = make_server_callback(submsg.message_id);

    widgetize.handle_event({
        sender_id: submsg.sender_id,
        message,
        post_to_server,
        data,
    });

    // If a poll question was changed or a new option was added,
    // update the widget-edited cache and rerender so the EDITED
    // marker appears immediately.
    if (
        typeof data === "object" &&
        data !== null &&
        "type" in data &&
        (data.type === "question" || data.type === "new_option") &&
        !message.has_widget_edits
    ) {
        message.has_widget_edits = true;
        if (message_lists.current !== undefined) {
            message_lists.current.view.rerender_messages([message]);
        }
    }
}

export function make_server_callback(
    message_id: number,
): (opts: {msg_type: string; data: WidgetOutboundData}) => void {
    return function (opts: {msg_type: string; data: WidgetOutboundData}) {
        const url = "/json/submessage";

        void channel.post({
            url,
            data: {
                message_id,
                msg_type: opts.msg_type,
                content: JSON.stringify(opts.data),
            },
        });
    };
}
