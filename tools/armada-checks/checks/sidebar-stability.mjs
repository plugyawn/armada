// 3. Rows of the left sidebar stay where they are when something else in
// the sidebar changes.

/* global window, document */

import {label_drift, largest_move} from "../lib/stats.mjs";

export const name = "sidebar-stability";
export const order = 30;
export const description =
    "clicking, expanding, filtering and switching views do not move left-sidebar rows that are not part of the change";

const PX = {unit: "px"};

async function positions(ctx, selector = "channelName") {
    return await ctx.page.evaluate(
        (selector_name) => window.__ac.labelled(selector_name),
        selector,
    );
}

function describe_move(move) {
    if (!move) {
        return "";
    }
    return move.label === null
        ? `${move.compared} rows compared`
        : `"${move.label}" ${move.from} -> ${move.to}${move.after_ms === undefined ? "" : ` after ${move.after_ms} ms`} (${move.compared} rows compared)`;
}

// The visible channel rows from top to bottom.
function ordered(map) {
    return Object.entries(map)
        .map(([label, position]) => ({label, ...position}))
        .toSorted((a, b) => a.y - b.y);
}

async function click_row(ctx, label) {
    const point = await ctx.page.evaluate(
        (text) => window.__ac.point_of("channelName", text),
        label,
    );
    if (!point) {
        throw new Error(`no channel row labelled "${label}"`);
    }
    await ctx.page.mouse.move(point.x, point.y);
    await ctx.sleep(150);
    const hash_before = await ctx.page.evaluate(() => window.location.hash);
    await ctx.page.mouse.click(point.x, point.y);
    await ctx.settle(400, 5000);
    await ctx.sleep(300);
    if ((await ctx.page.evaluate(() => window.location.hash)) === hash_before) {
        throw new Error(`clicking channel "${label}" did not navigate`);
    }
    return point;
}

async function under_pointer(ctx, point) {
    return await ctx.page.evaluate(
        (x, y) => window.__ac.label_at("channelName", x, y),
        point.x,
        point.y,
    );
}

// Late-arriving decoration: the topic list of a repository channel paints,
// then worktree icons arrive from a fetch and push the names sideways.
async function late_icons(ctx) {
    await ctx.open(ctx.topics.inbox);
    await ctx.emulate_latency(150);
    await ctx.park_pointer();
    await ctx.start_recording([], ["topic_names"]);
    await ctx.page.evaluate((target) => {
        window.location.hash = target;
    }, ctx.topics.short);
    await ctx.sleep(2500);
    const frames = await ctx.stop_recording();
    await ctx.emulate_latency(0);
    const moved = label_drift(frames, "topic_names", "x");
    ctx.assert("topic-list-paint.topic-text.shift", moved?.delta ?? null, "<=", 0.5, {
        ...PX,
        note: describe_move(moved),
    });
}

// The accordion: opening channel B closes channel A.
async function click_channels(ctx) {
    await ctx.park_pointer();
    const before = await positions(ctx);
    const topics = ordered(await positions(ctx, "topicName"));
    if (topics.length === 0) {
        throw new Error("the open channel shows no topic rows");
    }
    // Only rows a pointer can reach: inside the viewport.
    const reachable = await ctx.page.evaluate(() => window.innerHeight - 40);
    const rows = ordered(before).filter((row) => row.y < reachable);
    const open_index = rows.findLastIndex((row) => row.y < topics[0].y);
    const open = rows[open_index];
    const below = rows[open_index + 1];
    const above = rows[open_index - 2];
    if (!open || !below || !above) {
        throw new Error("need a channel above and below the open one");
    }

    const point = await click_row(ctx, below.label);
    const after = await positions(ctx);
    ctx.assert(
        "click-channel-below.clicked-row.moved",
        after[below.label] ? Math.abs(after[below.label].y - before[below.label].y) : null,
        "<=",
        0.5,
        {...PX, note: `"${below.label}" y ${before[below.label].y} -> ${after[below.label]?.y}`},
    );
    const hit = await under_pointer(ctx, point);
    ctx.assert("click-channel-below.pointer-still-on-clicked-row", hit === below.label, "==", 1, {
        note: `pointer is now over "${hit}"`,
    });
    const upper = largest_move(before, after, "y", (_label, position) => position.y < open.y);
    ctx.assert("click-channel-below.rows-above.moved", upper?.delta ?? null, "<=", 0.5, {
        ...PX,
        note: describe_move(upper),
    });

    // Now `below` is open; click a channel above it.
    await ctx.park_pointer();
    const before_up = await positions(ctx);
    const point_up = await click_row(ctx, above.label);
    const after_up = await positions(ctx);
    ctx.assert(
        "click-channel-above.clicked-row.moved",
        after_up[above.label] ? Math.abs(after_up[above.label].y - before_up[above.label].y) : null,
        "<=",
        0.5,
        {
            ...PX,
            note: `"${above.label}" y ${before_up[above.label].y} -> ${after_up[above.label]?.y}`,
        },
    );
    const hit_up = await under_pointer(ctx, point_up);
    ctx.assert(
        "click-channel-above.pointer-still-on-clicked-row",
        hit_up === above.label,
        "==",
        1,
        {note: `pointer is now over "${hit_up}"`},
    );
    const upper_up = largest_move(
        before_up,
        after_up,
        "y",
        (_label, position) => position.y < before_up[above.label].y,
    );
    ctx.assert("click-channel-above.rows-above.moved", upper_up?.delta ?? null, "<=", 0.5, {
        ...PX,
        note: describe_move(upper_up),
    });
}

// A disclosure moves what is below it and nothing above it, and closing it
// again puts every row back.
async function toggle_section(ctx, label, selector, which) {
    await ctx.park_pointer();
    const before = await positions(ctx);
    const header_rect = async () =>
        await ctx.page.evaluate(
            (selector_name, pick) => {
                const matches = window.__ac.all(selector_name);
                return window.__ac.rect_of(pick === "second" ? matches[1] : matches[0]);
            },
            selector,
            which,
        );
    const header = await header_rect();
    if (!header) {
        throw new Error(`no visible "${selector}"`);
    }
    const point = {x: header.x + 60, y: header.y + header.h / 2};
    await ctx.page.mouse.click(point.x, point.y);
    await ctx.settle(300, 3000);
    const toggled = await positions(ctx);
    const header_toggled = await header_rect();
    const changed = largest_move(before, toggled, "y");
    const row_count_changed = Object.keys(before).length !== Object.keys(toggled).length;
    // Without this the two assertions below pass when the click did nothing.
    ctx.assert(
        `${label}.toggle-had-effect`,
        Number(row_count_changed || (changed?.delta ?? 0) > 0),
        "==",
        1,
        {note: "1 when the click expanded or collapsed something"},
    );
    // Nothing above the disclosure moves, and neither does its own header
    // (there may be no channel row above it: the header is then the only
    // thing measured).
    const upper = largest_move(before, toggled, "y", (_label, position) => position.y < header.y);
    const header_moved = header_toggled
        ? Math.max(Math.abs(header_toggled.x - header.x), Math.abs(header_toggled.y - header.y))
        : null;
    ctx.assert(
        `${label}.rows-above.moved`,
        header_moved === null ? null : Math.max(upper?.delta ?? 0, header_moved),
        "<=",
        0.5,
        {
            ...PX,
            note: `header y ${header.y} -> ${header_toggled?.y}; ${upper ? describe_move(upper) : "no channel rows above it"}`,
        },
    );
    await ctx.page.mouse.click(point.x, point.y);
    await ctx.settle(300, 3000);
    const after = await positions(ctx);
    const round_trip = largest_move(before, after, "y");
    ctx.assert(
        `${label}.round-trip.moved`,
        Object.keys(after).length === Object.keys(before).length
            ? (round_trip?.delta ?? null)
            : null,
        "<=",
        0.5,
        {...PX, note: describe_move(round_trip)},
    );
}

// Going to Inbox or Recent and back.
async function home_views(ctx) {
    await ctx.navigate(ctx.topics.short);
    await ctx.park_pointer();
    const before = await positions(ctx);
    const topics = ordered(await positions(ctx, "topicName"));
    const first_topic_y = topics[0]?.y ?? Number.POSITIVE_INFINITY;
    await ctx.start_recording([], ["channel_names"]);
    for (const target of [ctx.topics.inbox, ctx.topics.recent, ctx.topics.short]) {
        await ctx.page.evaluate((hash) => {
            window.location.hash = hash;
        }, target);
        await ctx.sleep(1200);
    }
    await ctx.settle(400, 4000);
    const frames = await ctx.stop_recording();
    const after = await positions(ctx);
    // Rows above the open channel are not affected by its topics closing.
    const moved = label_drift(frames, "channel_names", "y", {
        only: (_label, position) => position.y < first_topic_y,
    });
    ctx.assert("home-view-switch.rows-above-open-channel.drift", moved?.delta ?? null, "<=", 0.5, {
        ...PX,
        note: describe_move(moved),
    });
    const round_trip = largest_move(before, after, "y");
    ctx.assert("home-view-switch.round-trip.moved", round_trip?.delta ?? null, "<=", 0.5, {
        ...PX,
        note: describe_move(round_trip),
    });
}

// Using the filter and clearing it leaves the sidebar as it was.
async function filter(ctx, label, text) {
    await ctx.park_pointer();
    const before = await positions(ctx);
    await ctx.click("sidebarFilter");
    await ctx.page.keyboard.type(text, {delay: 80});
    await ctx.settle(300, 3000);
    await ctx.page.keyboard.down("Control");
    await ctx.page.keyboard.press("a");
    await ctx.page.keyboard.up("Control");
    await ctx.page.keyboard.press("Backspace");
    await ctx.sleep(200);
    await ctx.page.evaluate(() => {
        document.activeElement?.blur?.();
    });
    await ctx.park_pointer();
    await ctx.settle(400, 3000);
    const after = await positions(ctx);
    const moved = largest_move(before, after, "y");
    ctx.assert(
        `${label}.cleared.moved`,
        Object.keys(after).length === Object.keys(before).length ? (moved?.delta ?? null) : null,
        "<=",
        0.5,
        {...PX, note: describe_move(moved)},
    );
}

export async function run(ctx) {
    // Order matters: the filter scenarios can leave the sidebar in another
    // state (that is what they test), so they run last.
    await ctx.scenario("topic-list-paint", async () => {
        await late_icons(ctx);
    });
    await ctx.scenario("click-channel", async () => {
        await click_channels(ctx);
    });
    await ctx.scenario("home-view-switch", async () => {
        await home_views(ctx);
    });
    await ctx.scenario("toggle-channel-section", async () => {
        await toggle_section(ctx, "toggle-channel-section", "sectionHeader", "second");
    });
    await ctx.scenario("toggle-direct-messages", async () => {
        await toggle_section(ctx, "toggle-direct-messages", "dmHeader", "first");
    });
    await ctx.scenario("filter-text", async () => {
        await filter(ctx, "filter-text", "ver");
    });
    await ctx.scenario("filter-space", async () => {
        await filter(ctx, "filter-space", " ");
    });
}
