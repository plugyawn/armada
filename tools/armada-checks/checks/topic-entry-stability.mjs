// 2. Entering a topic paints its chrome once, in its final place.

/* global window */

import {describe_rect, drift, probe_drift, sum} from "../lib/stats.mjs";

export const name = "topic-entry-stability";
export const order = 20;
export const description =
    "first visit and revisit of a worktree topic: nothing in the chrome changes size or position after first paint";

const FRAME = ["navbar", "leftSidebar", "rightSidebar"];
// Home has no band and no field (F5), so coming from Inbox the band and the
// field appear with the topic; from then on they must not move.
const LATE = ["topicBar", "tabStrip", "compose"];
// The header's values (branch, distance, commit), which replace the old
// rail card. Only whether they are there with the header is asserted until
// B2 draws them (known.json); their drift once there is B2's to add.
const VALUES = ["headerValues"];
const PX = {unit: "px"};

async function enter(ctx, label, hash, {from_first_frame = false} = {}) {
    await ctx.park_pointer();
    const shifts_before = await ctx.page.evaluate(() => window.__ac.shifts.length);
    await ctx.start_recording([...FRAME, ...LATE, ...VALUES], ["first_row_y", "hash"]);
    await ctx.page.evaluate((target) => {
        window.location.hash = target;
    }, hash);
    await ctx.sleep(2500);
    const frames = await ctx.stop_recording();
    const shifts = await ctx.page.evaluate(
        (start) => window.__ac.shifts.slice(start).map((shift) => shift.value),
        shifts_before,
    );

    for (const element of FRAME) {
        // Rows inside the nav are covered by sidebar-stability; the nav and
        // the column themselves must not move at all.
        const moved = drift(frames, element);
        ctx.assert(`${label}.${element}.drift`, moved?.delta ?? null, "<=", 0.5, {
            ...PX,
            note: moved?.worst
                ? `${describe_rect(moved.first)} -> ${describe_rect(moved.worst)}`
                : "",
        });
    }
    // Elements that belong to the topic. Coming from a view without them,
    // they must not move after they first appear; coming from another topic
    // they must not move at all.
    const arrival = frames.findIndex((frame) =>
        frame.probes.hash?.includes(hash.split("/").at(-1)),
    );
    for (const element of LATE) {
        const moved = drift(frames, element, {from: from_first_frame ? 0 : Math.max(arrival, 0)});
        ctx.assert(`${label}.${element}.drift`, moved?.delta ?? null, "<=", 0.5, {
            ...PX,
            note: moved
                ? `${describe_rect(moved.first)}${moved.worst ? ` -> ${describe_rect(moved.worst)} ${Math.round(moved.worst_t - moved.first_t)} ms later` : ""}`
                : "",
        });
    }
    // The header is complete when the place is first shown: its views and
    // its values (branch, commit) are there in the same frame, not one fetch
    // later. Since F5 the topic's bar is the header, which is always shown,
    // so the count starts at the frame in which the hash names the topic.
    const bar_frames = frames.slice(Math.max(arrival, 0)).filter((frame) => frame.rects.topicBar);
    for (const element of ["tabStrip", "headerValues"]) {
        ctx.assert(
            `${label}.${element}.frames-late`,
            bar_frames.length === 0
                ? null
                : bar_frames.filter((frame) => !frame.rects[element]).length,
            "==",
            0,
            {note: `frames in which the topic bar is shown without it, of ${bar_frames.length}`},
        );
    }
    const row = probe_drift(frames, "first_row_y", {from: Math.max(arrival, 0)});
    ctx.assert(`${label}.first-message.drift`, row?.delta ?? null, "<=", 0.5, {
        ...PX,
        note: row?.worst === null || !row ? "" : `y ${row.first} -> ${row.worst}`,
    });
    ctx.info(`${label}.layout-shift-score`, sum(shifts), {
        note: `${shifts.length} layout-shift entries (includes the feed being replaced)`,
    });
}

export async function run(ctx) {
    await ctx.open(ctx.topics.inbox);
    // The late pieces arrive with a fetch. A little latency makes "late"
    // deterministic on a fast local server; correct code passes at any latency.
    await ctx.emulate_latency(100);

    await ctx.scenario("first-visit", async () => {
        await enter(ctx, "first-visit", ctx.topics.short);
    });
    await ctx.scenario("revisit", async () => {
        await ctx.navigate(ctx.topics.inbox);
        await enter(ctx, "revisit", ctx.topics.short);
    });
    await ctx.scenario("switch-first-visit", async () => {
        await enter(ctx, "switch-first-visit", ctx.topics.other, {from_first_frame: true});
    });
    await ctx.scenario("switch-revisit", async () => {
        await enter(ctx, "switch-revisit", ctx.topics.short, {from_first_frame: true});
    });
}
