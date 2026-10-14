// A topic the semantics checks own: nobody else reads it, so its unread
// flags and its length are whatever the checks left there.
//
// It is created on first use and never rewritten. 120 messages are more
// than one fetch returns (50 before and 50 after the anchor), so the feed
// has older and newer history to ask the server for.

/* global window */

export const PINS = {
    stream_id: 2,
    stream: "sandbox",
    topic: "armada-pins",
    hash: "#narrow/channel/2-sandbox/topic/armada-pins",
    count: 120,
    // One message taller than the feed; the rest are one to four lines.
    tall_index: 30,
};

const NARROW = [
    {operator: "channel", operand: PINS.stream_id},
    {operator: "topic", operand: PINS.topic},
];

const FILLER = [
    "the bow line is made fast and the stern is still swinging",
    "soundings every cable until the bar is astern",
    "signal the flagship that we hold station to leeward",
    "the watch below turns out at eight bells",
];

export function pin_content(index) {
    const label = `pin ${String(index).padStart(3, "0")}`;
    if (index === PINS.tall_index) {
        const lines = [];
        for (let line = 0; line < 60; line += 1) {
            lines.push(`${label} line ${line + 1}: ${FILLER[line % FILLER.length]}`);
        }
        return lines.join("\n\n");
    }
    const lines = [label];
    for (let line = 0; line < index % 4; line += 1) {
        lines.push(FILLER[(index + line) % FILLER.length]);
    }
    return lines.join("\n\n");
}

async function fetch_ids(ctx) {
    const response = await ctx.api("GET", "/json/messages", {
        anchor: "oldest",
        num_before: 0,
        num_after: 1000,
        narrow: NARROW,
        apply_markdown: false,
    });
    if (response.status !== 200) {
        throw new Error(`cannot list ${PINS.topic}: HTTP ${response.status}`);
    }
    const stray = response.data.messages.filter((message) => !/^pin \d{3}/.test(message.content));
    if (stray.length > 0) {
        throw new Error(
            `${PINS.topic} holds ${stray.length} messages that are not fixtures (first id ${stray[0].id}); move them to another topic`,
        );
    }
    return response.data.messages.map((message) => message.id);
}

// Ids of the 120 fixture messages, oldest first, creating the missing
// ones. The page must already be showing the application.
export async function ensure_pins(ctx) {
    let ids = await fetch_ids(ctx);
    for (let index = ids.length; index < PINS.count; index += 1) {
        let response;
        for (let attempt = 0; attempt < 5; attempt += 1) {
            response = await ctx.api("POST", "/json/messages", {
                type: "stream",
                to: PINS.stream_id,
                topic: PINS.topic,
                content: pin_content(index),
            });
            if (response.status !== 429) {
                break;
            }
            // The server limits how fast one user may post.
            await ctx.sleep((Number(response.data?.["retry-after"]) || 10) * 1000 + 500);
        }
        if (response.status !== 200) {
            throw new Error(`cannot seed ${PINS.topic}: HTTP ${response.status}`);
        }
    }
    if (ids.length < PINS.count) {
        ids = await fetch_ids(ctx);
    }
    if (ids.length !== PINS.count) {
        throw new Error(
            `${PINS.topic} holds ${ids.length} fixture messages, expected ${PINS.count}; move the extra ones to another topic`,
        );
    }
    return ids;
}

// Set the read flag of fixture messages on the server and wait until the
// page has heard about it.
export async function set_read(ctx, ids, read) {
    if (ids.length === 0) {
        return;
    }
    const response = await ctx.api("POST", "/json/messages/flags", {
        messages: ids,
        op: read ? "add" : "remove",
        flag: "read",
    });
    if (response.status !== 200) {
        throw new Error(`cannot change read flags: HTTP ${response.status}`);
    }
}

export async function server_unread(ctx, ids) {
    const response = await ctx.api("GET", "/json/messages", {
        anchor: "oldest",
        num_before: 0,
        num_after: 1000,
        narrow: NARROW,
        apply_markdown: false,
    });
    const wanted = new Set(ids);
    return response.data.messages
        .filter((message) => wanted.has(message.id) && !message.flags.includes("read"))
        .map((message) => message.id);
}

// Ids no real message has; unique for the whole run, because a page keeps
// every message it has seen.
let next_id = 900000000;

// The pieces of a `message` event for a message that does not exist on the
// server: a real message of the topic with another sender, a new id and
// new content. The page treats it as any message from someone else;
// nothing is stored.
export async function incoming_factory(ctx, ids) {
    const response = await ctx.api("GET", "/json/messages", {
        anchor: ids.at(-1),
        num_before: 0,
        num_after: 0,
        narrow: NARROW,
        apply_markdown: true,
    });
    const {flags: _flags, ...template} = response.data.messages[0];
    const sender = await ctx.page.evaluate(() => {
        const id = window.zulip_test.get_user_id_from_name("King Hamlet");
        const person = window.zulip_test.get_person_by_user_id(id);
        return {id: person.user_id, name: person.full_name, email: person.email};
    });
    return (label, lines) => {
        next_id += 1;
        const paragraphs = [];
        for (let line = 0; line < lines; line += 1) {
            paragraphs.push(`<p>${label} line ${line + 1}</p>`);
        }
        return {
            id: next_id,
            event: {
                type: "message",
                flags: [],
                message: {
                    ...template,
                    id: next_id,
                    sender_id: sender.id,
                    sender_full_name: sender.name,
                    sender_email: sender.email,
                    avatar_url: null,
                    timestamp: Math.floor(Date.now() / 1000),
                    content: paragraphs.join(""),
                },
            },
        };
    };
}

// An `update_message` event as the server sends it for an edit of a
// message's content.
export function edit_event(
    message_id,
    rendered_content,
    {content = "edited", user_id = null} = {},
) {
    return {
        type: "update_message",
        user_id,
        rendering_only: user_id === null,
        message_id,
        message_ids: [message_id],
        flags: ["read"],
        edit_timestamp: Math.floor(Date.now() / 1000),
        stream_id: PINS.stream_id,
        orig_content: "",
        orig_rendered_content: "",
        content,
        rendered_content,
        is_me_message: false,
    };
}
