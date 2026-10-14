// 16. Summoning something must move nothing. A walk through the things a
// reader summons in a topic: for each, the frame of the application and a
// message in the middle of the conversation are sampled on every frame.
// Events from other people (a reaction, typing) are handed to the page as
// the server would send them; nothing is stored.

/* global window, document */

import {drift, probe_drift} from "../lib/stats.mjs";

export const name = "interaction-stability";
export const order = 160;
export const description =
    "hovering, selecting, menus, the runtime picker, work details, a thread, reactions from me and from others, someone typing, the composer: nothing in the frame and no message in the middle of the conversation moves";

const PX = {unit: "px"};
// The composer is part of the frame for every step except the two that
// open and close it.
const FRAME = ["navbar", "leftSidebar", "rightSidebar", "topicBar", "tabStrip", "compose"];
const AROUND_COMPOSER = FRAME.filter((element) => element !== "compose");
const MARKED = ["marked_top", "marked_left", "marked_height", "marked_width"];
// Reported for a part of the frame that disappeared without anything
// else having moved.
const GONE = 9999;

// Mark the message nearest the middle of the readable area; return the id
// of the one above it, which is where reactions are put.
async function mark_middle(ctx) {
    return await ctx.page.evaluate(() => {
        const view = window.__ac.feed.viewport();
        const middle = view.top + view.height / 2;
        const rows = window.__ac.all("messageRow");
        let best = rows[0];
        for (const row of rows) {
            const box = row.getBoundingClientRect();
            if (Math.abs(box.top - middle) < Math.abs(best.getBoundingClientRect().top - middle)) {
                best = row;
            }
        }
        window.__ac.marked = best;
        const above = rows[rows.indexOf(best) - 1];
        return {
            marked: Number(best.dataset.messageId),
            above: above ? Number(above.dataset.messageId) : null,
        };
    });
}

// Record every frame while `action` runs. `effect` says whether the thing
// the interaction is about can be seen afterwards; an interaction that did
// not happen proves nothing about stability.
async function watch(
    ctx,
    label,
    action,
    {frame = FRAME, wait_ms = 600, effect = async () => true} = {},
) {
    await ctx.park_pointer();
    await ctx.sleep(200);
    await ctx.start_recording(frame, MARKED);
    let driven = true;
    try {
        await action();
    } catch {
        driven = false;
    }
    await ctx.sleep(wait_ms);
    const frames = await ctx.stop_recording();
    const happened = driven && (await effect());
    const drifts = frame.map((element) => ({element, result: drift(frames, element)}));
    // A part of the frame that was never on screen was not measured, and
    // one that was gone for a frame did not stay where it was: `drift`
    // skips the frames in which an element is missing.
    const absent = drifts.filter((entry) => entry.result === null).map((entry) => entry.element);
    const vanished = drifts
        .filter((entry) => (entry.result?.vanished ?? 0) > 0)
        .map((entry) => `${entry.element} for ${entry.result.vanished} frames`);
    const by_size = drifts
        .filter((entry) => entry.result !== null)
        .toSorted((a, b) => b.result.delta - a.result.delta);
    const moved = by_size[0];
    const all_moved = by_size
        .filter((entry) => entry.result.delta > 0.5)
        .map((entry) => `${entry.element} ${entry.result.delta}px`);
    const why_not = driven ? "the interaction had no visible effect" : "could not be driven";
    const measurable = happened && moved !== undefined && absent.length === 0;
    ctx.assert(
        `${label}.frame.drift`,
        measurable
            ? vanished.length > 0 && moved.result.delta <= 0.5
                ? GONE
                : moved.result.delta
            : null,
        "<=",
        0.5,
        {
            ...PX,
            note: !happened
                ? why_not
                : absent.length > 0
                  ? `not on screen, so not measured: ${absent.join(", ")}`
                  : `largest change of ${frame.join(", ")} over ${frames.length} frames: ${moved?.element}${all_moved.length > 1 ? `; moved: ${all_moved.join(", ")}` : ""}${vanished.length > 0 ? `; gone from the screen: ${vanished.join(", ")} (${GONE} when nothing else moved)` : ""}`,
        },
    );
    // The marked message must have been on screen from the first frame.
    const visible_at_start = (frames[0]?.probes.marked_width ?? 0) > 0;
    const conversation = MARKED.map((probe) => ({probe, result: probe_drift(frames, probe)}))
        .filter((entry) => entry.result !== null)
        .toSorted((a, b) => b.result.delta - a.result.delta)[0];
    ctx.assert(
        `${label}.conversation.drift`,
        happened && visible_at_start && conversation ? conversation.result.delta : null,
        "<=",
        0.5,
        {
            ...PX,
            note: !happened
                ? why_not
                : visible_at_start && conversation
                  ? `a message in the middle of the conversation: ${conversation.probe.replace("marked_", "")} ${conversation.result.first} -> ${conversation.result.worst ?? conversation.result.first}`
                  : "the marked message was not on screen",
        },
    );
}

function reaction_event(op, message_id, user_id) {
    return {
        type: "reaction",
        op,
        message_id,
        user_id,
        emoji_name: "+1",
        emoji_code: "1f44d",
        reaction_type: "unicode_emoji",
    };
}

async function walk(ctx) {
    let ids;
    // The same scene before every interaction: the long topic, away from
    // both ends, a message in the middle marked. An interaction that
    // leaves the topic or replaces the feed is put right here, so that it
    // cannot empty the measurements that follow.
    const scene = async () => {
        const intact = await ctx.page.evaluate(
            (hash) =>
                window.location.hash === hash &&
                window.__ac.marked?.isConnected === true &&
                window.__ac.marked.getClientRects().length > 0 &&
                window.__ac.all("menu").length === 0,
            ctx.topics.long,
        );
        if (!intact) {
            await ctx.open(ctx.topics.long, {fresh: true});
            await ctx.wheel(-900);
            await ctx.sleep(900);
            ids = await mark_middle(ctx);
        }
    };
    await scene();
    const people = await ctx.page.evaluate(() => ({
        me: window.zulip_test.current_user.user_id,
        other: window.zulip_test.get_user_id_from_name("King Hamlet"),
        other_email: window.zulip_test.get_person_by_user_id(
            window.zulip_test.get_user_id_from_name("King Hamlet"),
        ).email,
        stream_id: window.__ac.feed.list().all_messages()[0].stream_id,
        topic: window.__ac.feed.list().all_messages()[0].topic,
    }));
    const shows = async (selector_name) =>
        await ctx.page.evaluate((key) => window.__ac.all(key).length > 0, selector_name);
    const reactions = async () =>
        await ctx.page.evaluate(
            (id) =>
                [
                    ...(window.__ac.feed
                        .row(id)
                        ?.querySelectorAll(".message_reaction, [data-reaction]") ?? []),
                ]
                    .map((element) => element.textContent.replaceAll(/\s+/g, ""))
                    .join(","),
            ids.above,
        );
    const step = async (label, action, effect, options = {}) => {
        await scene();
        await watch(ctx, label, action, {effect, ...options});
    };

    await step(
        "hover-message",
        async () => {
            const point = await ctx.page.evaluate(() => {
                const box = window.__ac.marked.getBoundingClientRect();
                return {x: box.left + box.width / 2, y: box.top + Math.min(box.height / 2, 20)};
            });
            await ctx.page.mouse.move(point.x, point.y);
        },
        // The pointer is over the marked message, as the page sees it.
        async () => await ctx.page.evaluate(() => window.__ac.marked.matches(":hover")),
    );
    const selected = (await ctx.feed()).selected;
    await step(
        "select-next-message",
        async () => {
            // Within the view: the selection moves, the feed does not.
            await ctx.page.keyboard.press("k");
        },
        async () => (await ctx.feed()).selected !== selected,
    );
    await step(
        "message-menu.open",
        async () => ctx.page.keyboard.press("i"),
        async () => shows("menu"),
    );
    await watch(ctx, "message-menu.close", async () => ctx.page.keyboard.press("Escape"), {
        effect: async () => !(await shows("menu")),
    });
    await step(
        "runtime-picker.open",
        async () => ctx.click("runtimePicker"),
        async () => shows("runtimeMenu"),
    );
    await watch(ctx, "runtime-picker.close", async () => ctx.page.keyboard.press("Escape"), {
        effect: async () => !(await shows("runtimeMenu")),
    });
    await step(
        "work-details.open",
        async () => ctx.click("workDetails"),
        async () => shows("workDetailsPanel"),
    );
    await watch(ctx, "work-details.close", async () => ctx.page.keyboard.press("Escape"), {
        effect: async () => !(await shows("workDetailsPanel")),
    });

    let before = "";
    await scene();
    before = await reactions();
    await step(
        "reaction.mine",
        async () => ctx.inject([reaction_event("add", ids.above, people.me)]),
        async () => (await reactions()) !== before,
    );
    before = await reactions();
    await watch(
        ctx,
        "reaction.count",
        async () => ctx.inject([reaction_event("add", ids.above, people.other)]),
        {
            effect: async () => (await reactions()) !== before,
        },
    );
    before = await reactions();
    await watch(
        ctx,
        "reaction.removed",
        async () =>
            ctx.inject([
                reaction_event("remove", ids.above, people.other),
                reaction_event("remove", ids.above, people.me),
            ]),
        {effect: async () => (await reactions()) !== before},
    );
    before = await reactions();
    await step(
        "reaction.another-person",
        async () => ctx.inject([reaction_event("add", ids.above, people.other)]),
        async () => (await reactions()) !== before,
    );
    await ctx.inject([reaction_event("remove", ids.above, people.other)]);

    const typing = (op) => ({
        type: "typing",
        op,
        message_type: "stream",
        sender: {user_id: people.other, email: people.other_email},
        stream_id: people.stream_id,
        topic: people.topic,
    });
    await step(
        "typing.starts",
        async () => ctx.inject([typing("start")]),
        async () => shows("typingNotice"),
    );
    await watch(ctx, "typing.stops", async () => ctx.inject([typing("stop")]), {
        effect: async () => !(await shows("typingNotice")),
    });

    // The composer changes its own height when it opens and closes today;
    // the frame around it and the conversation must still hold.
    await step(
        "composer.open",
        async () => {
            await ctx.page.evaluate(() => {
                document.activeElement?.blur?.();
            });
            await ctx.page.keyboard.press("r");
        },
        async () => ctx.exists("composeTextarea"),
        {frame: AROUND_COMPOSER},
    );
    await watch(
        ctx,
        "composer.typing",
        async () => ctx.page.keyboard.type("armada-checks interaction walk, not sent", {delay: 10}),
        {effect: async () => ((await ctx.compose_value()) ?? "").includes("interaction walk")},
    );
    await watch(
        ctx,
        "composer.close",
        async () => {
            await ctx.clear_compose();
            await ctx.page.keyboard.press("Escape");
        },
        {
            frame: AROUND_COMPOSER,
            effect: async () => !(await ctx.exists("composeTextarea")),
        },
    );
}

async function thread(ctx) {
    await ctx.open(ctx.topics.threads, {fresh: true});
    await mark_middle(ctx);
    // The thread opens in the right-hand column, which is its to change.
    const frame = FRAME.filter((element) => element !== "rightSidebar");
    await watch(
        ctx,
        "thread.open",
        async () => {
            // The last launch: in the frame's pane the first one can lie
            // under the recipient bar that sticks to the pane's top.
            await ctx.click("threadLaunch");
        },
        {
            frame,
            wait_ms: 900,
            effect: async () =>
                await ctx.page.evaluate(() => window.__ac.all("threadPane").length > 0),
        },
    );
    await mark_middle(ctx);
    await watch(
        ctx,
        "thread.close",
        async () => {
            await ctx.page.keyboard.press("Escape");
        },
        {
            frame,
            wait_ms: 900,
            effect: async () =>
                await ctx.page.evaluate(() => window.__ac.all("threadPane").length === 0),
        },
    );
}

export async function run(ctx) {
    await ctx.scenario("walk", async () => {
        await walk(ctx);
    });
    await ctx.scenario("thread", async () => {
        await thread(ctx);
    });
}
