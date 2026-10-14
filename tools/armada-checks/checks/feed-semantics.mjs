// 11. What the message feed does with the selection, the scroll position
// and the read flags. These are the behaviours the rebuild must keep; each
// is pinned on today's view before any of the code that implements it is
// touched (PLAN.md, Appendix B).

/* global window */

import {PINS, edit_event, ensure_pins, incoming_factory, set_read} from "../lib/fixtures.mjs";
import {round} from "../lib/stats.mjs";

export const name = "feed-semantics";
export const order = 110;
export const description =
    "selection, scroll and read marking in a 120-message topic: pointer keys, recentring, code scrolls, read rules, anchoring, autoscroll, history fetches, Back and Forward, composer growth, topic groups";

const PX = {unit: "px"};

async function prepare(ctx) {
    await ctx.open(ctx.topics.inbox, {rows: false});
    const ids = await ensure_pins(ctx);
    await set_read(ctx, ids, true);
    return ids;
}

async function unread_among(ctx, ids) {
    return await ctx.page.evaluate((list) => window.__ac.feed.unread(list), ids);
}

async function wait_for_unread(ctx, ids, count) {
    await ctx.page.waitForFunction(
        (list, wanted) => window.__ac.feed.unread(list).length === wanted,
        {timeout: 8000},
        ids,
        count,
    );
}

async function press(ctx, key, wait_ms = 350) {
    await ctx.page.keyboard.press(key);
    await ctx.sleep(wait_ms);
    return await ctx.feed();
}

async function wheel(ctx, delta, wait_ms = 700) {
    await ctx.wheel(delta);
    await ctx.sleep(wait_ms);
    return await ctx.feed();
}

// Answer "mark as read" requests in the browser: a message that exists
// only in the page cannot be marked on the server, and the request itself
// is what the check counts.
async function count_read_requests(ctx) {
    return await ctx.route(
        (request) =>
            request.method === "POST" &&
            request.url.startsWith("/json/messages/flags") &&
            request.body.includes("flag=read") &&
            request.body.includes("op=add"),
        {
            answer: () => ({
                result: "success",
                msg: "",
                messages: [],
                ignored_because_not_subscribed_channels: [],
            }),
        },
    );
}

async function pointer_keys(ctx, ids) {
    const index = (id) => ids.indexOf(id);
    await ctx.open(PINS.hash, {fresh: true});
    const start = await ctx.feed();
    ctx.assert("pointer.opens-on-newest", index(start.selected), "==", PINS.count - 1, {
        note: "a topic with nothing unread opens with its newest message selected",
    });

    // Each key is judged by the step it makes from where the selection
    // was, so that a key that does nothing cannot pass by standing still.
    let at = start;
    for (const [key, step] of [
        ["k", -1],
        ["ArrowUp", -1],
        ["j", 1],
        ["ArrowDown", 1],
    ]) {
        const after = await press(ctx, key);
        ctx.assert(
            `pointer.keys.${key}.selected-id`,
            index(after.selected) - index(at.selected),
            "==",
            step,
            {
                note: `messages moved: ${index(at.selected)} -> ${index(after.selected)} of ${PINS.count}`,
            },
        );
        at = after;
    }

    // Page keys scroll about a page; the selection then follows by the
    // keep-in-view rule, which the page helper models from the positions
    // of the rows.
    //
    // Since F5 the feed has no blank tail under its newest message, so at
    // the end a Page Down after a Page Up only returns to where it was and
    // takes nothing out of view. Two pages up first give it a page to go;
    // the second also makes the view render the rest of the topic, which
    // changes every scroll offset, before anything is measured.
    await press(ctx, "PageUp", 600);
    await press(ctx, "PageUp", 600);
    await ctx.settle(800, 8000);
    let paged = 0;
    const amounts = [];
    for (const [label, key, direction] of [
        ["PageUp", "PageUp", -1],
        ["PageDown", "PageDown", 1],
        ["PageUp", "PageUp", -1],
        ["Space", "Space", 1],
    ]) {
        const before = await ctx.feed();
        const after = await press(ctx, key, 600);
        const expected = await ctx.page.evaluate(
            (selected) => window.__ac.feed.keep_in_view_model(selected),
            before.selected,
        );
        const moved = (after.scroll_top - before.scroll_top) * direction;
        const fraction = moved / before.view.height;
        amounts.push(`${label} ${Math.round(moved)}px`);
        if (fraction >= 0.6 && fraction <= 1) {
            paged += 1;
        }
        if (label !== "PageUp" || amounts.length === 1) {
            ctx.assert(
                `pointer.keys.${label}.selected-id`,
                // The press says something only if the rule moves the
                // selection; then the message selected is the value, so
                // that a selection left behind fails with where it is.
                expected === before.selected ? null : index(after.selected),
                "==",
                index(expected),
                {
                    note: `selection ${index(before.selected)} -> ${index(after.selected)}; the keep-in-view rule gives ${index(expected)}${expected === before.selected ? ", which is where it was: the press did not take it out of view" : ""}`,
                },
            );
        }
    }
    ctx.assert("pointer.page-keys.scroll-a-page", paged, "==", 4, {
        note: `presses that scrolled between 0.6 and 1 of the readable height (${start.view.height}px): ${amounts.join(", ")}`,
    });

    // A scroll the user makes takes the selection along once it leaves the
    // band between 10% and 90% of the readable area.
    let kept = 0;
    const moves = [];
    for (const delta of [-1500, 1100]) {
        const before = await ctx.feed();
        const after = await wheel(ctx, delta);
        const expected = await ctx.page.evaluate(
            (selected) => window.__ac.feed.keep_in_view_model(selected),
            before.selected,
        );
        moves.push(
            `wheel ${delta}: ${index(before.selected)} -> ${index(after.selected)} (rule: ${index(expected)}) at ${after.selected_fraction}`,
        );
        if (after.selected === expected && after.selected !== before.selected) {
            kept += 1;
        }
    }
    ctx.assert("pointer.keep-in-view", kept, "==", 2, {note: moves.join("; ")});

    const before_home = await ctx.feed();
    await press(ctx, "Home", 300);
    await ctx.page
        .waitForFunction(
            (first) => window.__ac.feed.list()?.selected_id() === first,
            {timeout: 8000},
            ids[0],
        )
        .catch(() => undefined);
    await ctx.settle();
    ctx.assert(
        "pointer.keys.Home.selected-id",
        index(before_home.selected) > 0 ? index((await ctx.feed()).selected) : null,
        "==",
        0,
        {note: `pressed with message ${index(before_home.selected)} selected`},
    );

    // Going down, a row that is below the readable area is brought to 1/7
    // of it. Stop before the tall fixture message.
    const down = [];
    let before = await ctx.feed();
    for (let step = 0; step < PINS.tall_index - 6; step += 1) {
        const after = await press(ctx, "j", 250);
        if (after.scroll_top !== before.scroll_top && before.scroll_top > 0) {
            down.push(after.selected_fraction);
        }
        before = after;
    }

    // End must have somewhere to go, whatever the keys before it did.
    for (let step = 0; step < 8 && index(before.selected) >= PINS.count - 3; step += 1) {
        before = await press(ctx, "k", 250);
    }
    const before_end = before;
    await press(ctx, "End", 300);
    await ctx.page
        .waitForFunction(
            (last) => window.__ac.feed.list()?.selected_id() === last,
            {timeout: 8000},
            ids.at(-1),
        )
        .catch(() => undefined);
    await ctx.settle();
    ctx.assert(
        "pointer.keys.End.selected-id",
        index(before_end.selected) < PINS.count - 1 ? index((await ctx.feed()).selected) : null,
        "==",
        PINS.count - 1,
        {note: `pressed with message ${index(before_end.selected)} selected`},
    );

    // Going up, a row above the readable area is brought to the middle.
    const up = [];
    before = await ctx.feed();
    for (let step = 0; step < 24; step += 1) {
        const after = await press(ctx, "k", 250);
        if (after.scroll_top !== before.scroll_top) {
            up.push(after.selected_fraction);
        }
        before = after;
    }
    const deviations = [
        ...down.map((fraction) => Math.abs(fraction - 1 / 7)),
        ...up.map((fraction) => Math.abs(fraction - 1 / 2)),
    ];
    ctx.assert(
        "pointer.recenter",
        down.length >= 2 && up.length >= 2 ? Math.max(...deviations) : null,
        "<=",
        0.01,
        {
            note: `fraction of the readable height at which the selected row lands; going down ${down.join(", ") || "never recentred"} (rule 0.143), going up ${up.join(", ") || "never recentred"} (rule 0.5)`,
        },
    );
}

// A message taller than the readable area: selecting it shows where it
// starts, and j pages through it instead of leaving it. Long messages are
// shown shortened, so the fixture's 60-paragraph message is expanded first.
async function tall_message(ctx, ids) {
    const tall = ids[PINS.tall_index];
    const start = ids[PINS.tall_index - 2];
    await ctx.open(`${PINS.hash}/near/${start}`, {fresh: true});
    await ctx.sleep(600);
    await ctx.click("messageExpander");
    await ctx.sleep(500);
    await ctx.park_pointer();
    let state = await ctx.feed();
    for (let step = 0; step < 6 && state.selected !== start; step += 1) {
        state = await press(ctx, ids.indexOf(state.selected) > ids.indexOf(start) ? "k" : "j", 400);
    }
    const steps = [];
    let problems = 0;
    let pages = 0;
    let reached = false;
    for (let step = 0; step < 14; step += 1) {
        const before = state;
        state = await press(ctx, "j", 450);
        if (state.selected === tall && !reached) {
            reached = true;
            // Its first line is inside the readable area.
            if (
                state.selected_top < state.view.top - 0.5 ||
                state.selected_top > state.view.bottom
            ) {
                problems += 1;
            }
            steps.push(
                `selected with its top at ${state.selected_top} (area ${state.view.top} to ${state.view.bottom}), ${state.selected_height}px tall`,
            );
            continue;
        }
        if (!reached) {
            continue;
        }
        if (before.selected_bottom > before.view.bottom) {
            // Its end is below the area: j must page, not leave.
            const moved = state.scroll_top - before.scroll_top;
            if (state.selected !== tall || moved <= 0 || moved > before.view.height) {
                problems += 1;
            }
            pages += 1;
            steps.push(
                state.selected === tall
                    ? `j paged ${Math.round(moved)}px`
                    : `j left it with ${Math.round(before.selected_bottom - before.view.bottom)}px unread below`,
            );
        }
        if (state.selected !== tall) {
            steps.push("j moved on");
            break;
        }
    }
    const height = await ctx.page.evaluate(
        (id) => window.__ac.rect_of(window.__ac.feed.row(id))?.h ?? 0,
        tall,
    );
    ctx.assert(
        "pointer.tall-message-clamp",
        reached && pages >= 1 && height > state.view.height ? problems : null,
        "==",
        0,
        {note: steps.join("; ") || "the tall message was never selected"},
    );
}

async function code_scroll(ctx, ids) {
    // A short message arrives while the newest message is selected and in
    // view: the feed scrolls by itself. That scroll must not count as
    // reading.
    await ctx.open(PINS.hash, {fresh: true});
    const reads = await count_read_requests(ctx);
    const incoming = await incoming_factory(ctx, ids);
    let before = await ctx.feed();
    const short = incoming("code scroll", 3);
    await ctx.inject([short.event]);
    await ctx.sleep(1300);
    let after = await ctx.feed();
    let scrolled = after.scroll_top - before.scroll_top;
    const still_unread = (await unread_among(ctx, [short.id])).length === 1;
    ctx.assert(
        "code-scroll.nothing-marked-read",
        scrolled > 20 ? reads.seen.length + (still_unread ? 0 : 1) : null,
        "==",
        0,
        {
            note: `the feed scrolled ${Math.round(scrolled)}px by itself for a new message; ${reads.seen.length} mark-as-read requests; the message is ${still_unread ? "still unread" : "read"}`,
        },
    );
    // The same position after the smallest scroll a user can make.
    const before_wheel = reads.seen.length;
    await wheel(ctx, 2, 900);
    ctx.assert(
        "code-scroll.user-scroll-marks-read",
        scrolled > 20 ? reads.seen.length - before_wheel : null,
        ">=",
        1,
        {
            note: `mark-as-read requests made after a 2px wheel movement with the newest message in view (${before_wheel} had been made before it)`,
        },
    );
    await reads.stop();

    // A tall message arrives: the feed may scroll only as far as keeps the
    // selected message in view, and the selection stays where it was.
    await ctx.open(PINS.hash, {fresh: true});
    const more_reads = await count_read_requests(ctx);
    // With the selection a fifth of the way down, the message is taller
    // than the distance the feed may move.
    for (let step = 0; step < 3; step += 1) {
        await press(ctx, "k", 200);
    }
    before = await ctx.feed();
    const tall = (await incoming_factory(ctx, ids))("tall arrival", 20);
    await ctx.inject([tall.event]);
    await ctx.sleep(1300);
    after = await ctx.feed();
    scrolled = after.scroll_top - before.scroll_top;
    const arrived_height = await ctx.page.evaluate(
        (id) => window.__ac.rect_of(window.__ac.feed.row(id))?.h ?? 0,
        tall.id,
    );
    ctx.assert(
        "code-scroll.selection-unchanged",
        scrolled > 20 && arrived_height > before.selected_top - before.view.top
            ? Number(after.selected !== before.selected)
            : null,
        "==",
        0,
        {
            note: `a ${Math.round(arrived_height)}px message arrived with the selection ${Math.round(before.selected_top - before.view.top)}px below the top of the readable area; the feed scrolled ${Math.round(scrolled)}px by itself; selected ${ids.indexOf(before.selected)} -> ${ids.includes(after.selected) ? ids.indexOf(after.selected) : "the new message"}, now at ${after.selected_fraction} of the readable area`,
        },
    );
    await more_reads.stop();
}

async function read_rules(ctx, ids) {
    const lower = ids.slice(60);
    // Scrolling part of the way down marks only what has been on screen.
    await ctx.open(ctx.topics.inbox, {rows: false});
    await set_read(ctx, ids, true);
    await set_read(ctx, lower, false);
    await ctx.sleep(700);
    await ctx.open(PINS.hash, {fresh: true});
    await ctx.sleep(800);
    const at_open = await unread_among(ctx, ids);
    const seen = new Set(await ctx.page.evaluate(() => window.__ac.feed.fully_visible()));
    for (let step = 0; step < 4; step += 1) {
        await wheel(ctx, 250, 600);
        for (const id of await ctx.page.evaluate(() => window.__ac.feed.fully_visible())) {
            seen.add(id);
        }
    }
    const left = new Set(await unread_among(ctx, ids));
    const marked = at_open.filter((id) => !left.has(id));
    ctx.assert(
        "read.fully-visible-only",
        marked.length > 0 ? marked.filter((id) => !seen.has(id)).length : null,
        "==",
        0,
        {
            note: `${at_open.length} unread when opened at the first of them; a 1000px scroll marked ${marked.length} read (messages ${marked.map((id) => ids.indexOf(id)).join(", ")}); counted: those that were never fully in the readable area`,
        },
    );

    // An unfocused window marks nothing; it does when focus returns.
    const tail = ids.slice(-8);
    await ctx.open(ctx.topics.inbox, {rows: false});
    await set_read(ctx, ids, true);
    await ctx.sleep(500);
    await ctx.open(PINS.hash, {fresh: true});
    // Not on the newest message: a scroll re-selects the selected row,
    // and that alone marks the newest message read.
    await press(ctx, "k", 200);
    await press(ctx, "k", 200);
    await ctx.page.evaluate(() => {
        window.dispatchEvent(new window.Event("blur"));
    });
    await set_read(ctx, tail, false);
    await wait_for_unread(ctx, tail, tail.length);
    // A scroll too small to move the selection: moving the selection
    // down marks what it passes, focused or not.
    await wheel(ctx, -3, 500);
    await wheel(ctx, 3, 900);
    const while_unfocused = tail.length - (await unread_among(ctx, tail)).length;
    await ctx.page.evaluate(() => {
        window.dispatchEvent(new window.Event("focus"));
    });
    await ctx.sleep(900);
    const after_focus = tail.length - (await unread_among(ctx, tail)).length;
    ctx.assert(
        "read.not-when-unfocused",
        after_focus === tail.length ? while_unfocused : null,
        "==",
        0,
        {
            note: `${tail.length} unread messages in view at the bottom; marked read while the window was unfocused and scrolled: ${while_unfocused}; after focus returned: ${after_focus}`,
        },
    );

    // Nothing is marked under an overlay; it is once the overlay closes.
    await set_read(ctx, tail, false);
    await wait_for_unread(ctx, tail, tail.length);
    await ctx.page.keyboard.press("?");
    await ctx.page.waitForFunction(() => window.__ac.all("overlay").length > 0, {timeout: 5000});
    const refocus = async () => {
        await ctx.page.evaluate(() => {
            window.dispatchEvent(new window.Event("blur"));
            window.dispatchEvent(new window.Event("focus"));
        });
        await ctx.sleep(900);
    };
    await refocus();
    const under_overlay = tail.length - (await unread_among(ctx, tail)).length;
    await ctx.page.keyboard.press("Escape");
    await ctx.page.waitForFunction(() => window.__ac.all("overlay").length === 0, {timeout: 5000});
    await refocus();
    const after_overlay = tail.length - (await unread_among(ctx, tail)).length;
    ctx.assert(
        "read.not-under-overlay",
        after_overlay === tail.length ? under_overlay : null,
        "==",
        0,
        {
            note: `${tail.length} unread messages in view; marked read when the window regained focus with the shortcuts overlay open: ${under_overlay}; with it closed: ${after_overlay}`,
        },
    );

    // A /near/ link into the middle of unread history does not start
    // marking until the reader has scrolled back to the first unread.
    await ctx.open(ctx.topics.inbox, {rows: false});
    await set_read(ctx, ids, true);
    await set_read(ctx, lower, false);
    await ctx.sleep(700);
    await ctx.open(`${PINS.hash}/near/${ids[100]}`, {fresh: true});
    await ctx.sleep(1200);
    for (let step = 0; step < 3; step += 1) {
        await press(ctx, "j", 300);
    }
    await ctx.sleep(500);
    const before_scroll = lower.length - (await unread_among(ctx, lower)).length;
    for (let step = 0; step < 14; step += 1) {
        const first_unread_shown = await ctx.page.evaluate((id) => {
            const row = window.__ac.feed.row(id);
            return (
                row !== null && row.getBoundingClientRect().top >= window.__ac.feed.viewport().top
            );
        }, lower[0]);
        if (first_unread_shown) {
            break;
        }
        await wheel(ctx, -700, 450);
    }
    await ctx.sleep(600);
    for (let step = 0; step < 12; step += 1) {
        await press(ctx, "j", 200);
    }
    await ctx.sleep(600);
    const after_scroll = lower.length - (await unread_among(ctx, lower)).length;
    ctx.assert(
        "read.near-resumes-after-user-scroll",
        before_scroll === 0 && after_scroll > 0,
        "==",
        1,
        {
            note: `opened at message 100 with 60 to 119 unread: moving the selection down three messages marked ${before_scroll}; after scrolling back to the first unread and moving down, ${after_scroll} were marked`,
        },
    );
    await set_read(ctx, ids, true);
}

async function anchoring_and_autoscroll(ctx, ids) {
    await ctx.open(PINS.hash, {fresh: true});
    const reads = await count_read_requests(ctx);
    const incoming = await incoming_factory(ctx, ids);

    // At the bottom: a message too tall for the free space below the last
    // one is brought into view.
    let before = await ctx.feed();
    const arrival = incoming("arrives at the bottom", 9);
    await ctx.inject([arrival.event]);
    await ctx.sleep(1300);
    let after = await ctx.feed();
    const box = await ctx.page.evaluate(
        (id) => window.__ac.rect_of(window.__ac.feed.row(id)),
        arrival.id,
    );
    const free_before = before.view.bottom - before.selected_bottom;
    ctx.assert(
        "autoscroll.at-bottom.follows",
        box !== null && box.h > free_before ? Math.max(0, box.y + box.h - after.view.bottom) : null,
        "<=",
        0.5,
        {
            ...PX,
            note: `a ${box?.h}px message arrived with ${Math.round(free_before)}px free below the last one; the feed scrolled ${Math.round(after.scroll_top - before.scroll_top)}px; counted: how far its end is below the readable area`,
        },
    );

    // Scrolled up: nothing moves.
    await wheel(ctx, -2500, 900);
    before = await ctx.feed();
    await ctx.inject([incoming("arrives while scrolled up", 9).event]);
    await ctx.sleep(1300);
    after = await ctx.feed();
    ctx.assert(
        "autoscroll.scrolled-up.stays",
        before.at_bottom ? null : Math.abs(after.scroll_top - before.scroll_top),
        "<=",
        0.5,
        {...PX, note: `scroll position ${before.scroll_top} -> ${after.scroll_top}`},
    );
    ctx.assert(
        "anchor.append.offset",
        after.selected === before.selected
            ? Math.abs(after.selected_top - before.selected_top)
            : null,
        "<=",
        0.5,
        {...PX, note: `selected row at y ${before.selected_top} -> ${after.selected_top}`},
    );

    // A message three rows above the selected one grows by eight lines.
    const target = before.model_ids[before.model_ids.indexOf(before.selected) - 3];
    const paragraphs = [];
    for (let line = 0; line < 9; line += 1) {
        paragraphs.push(`<p>edited above line ${line + 1}</p>`);
    }
    before = await ctx.feed();
    const height_before = await ctx.page.evaluate(
        (id) => window.__ac.rect_of(window.__ac.feed.row(id))?.h,
        target,
    );
    await ctx.inject([edit_event(target, paragraphs.join(""))]);
    await ctx.sleep(1000);
    after = await ctx.feed();
    const height_after = await ctx.page.evaluate(
        (id) => window.__ac.rect_of(window.__ac.feed.row(id))?.h,
        target,
    );
    ctx.assert(
        "anchor.edit-above.offset",
        after.selected === before.selected && height_after - height_before > 50
            ? Math.abs(after.selected_top - before.selected_top)
            : null,
        "<=",
        0.5,
        {
            ...PX,
            note: `a message above grew ${height_before} -> ${height_after}px; selected row at y ${before.selected_top} -> ${after.selected_top}`,
        },
    );
    await reads.stop();

    // My own send is brought into view whole, even where a message from
    // someone else of the same height would only be met part of the way.
    const send_held = async (lines, arrange = async () => undefined) => {
        await ctx.open_compose();
        await arrange();
        const posts = await ctx.route(
            (request) => request.method === "POST" && /^\/json\/messages(\?|$)/.test(request.url),
        );
        const token = `own-send-${Date.now().toString(36)}`;
        for (let line = 0; line < lines; line += 1) {
            await ctx.page.keyboard.type(
                line === 0 ? `armada-checks ${token}` : `line ${line + 1}`,
            );
            if (line < lines - 1) {
                await ctx.page.keyboard.down("Shift");
                await ctx.page.keyboard.press("Enter");
                await ctx.page.keyboard.up("Shift");
            }
        }
        await ctx.sleep(300);
        const sent_from = await ctx.feed();
        await ctx.page.keyboard.press("Enter");
        await ctx.sleep(1500);
        const echoed = await ctx.page.evaluate((text) => {
            const row = window.__ac
                .all("messageRow")
                .findLast((candidate) => candidate.textContent.includes(text));
            const view = window.__ac.feed.viewport();
            const box = row?.getBoundingClientRect();
            return box
                ? {
                      top: Math.round(box.top),
                      height: Math.round(box.height),
                      shown: box.top >= view.top - 0.5 && box.bottom <= view.bottom + 0.5,
                  }
                : null;
        }, token);
        const landed = await ctx.feed();
        // Nothing was posted: the request never left the browser.
        await posts.fail();
        await posts.stop();
        await ctx.sleep(400);
        return {echoed, sent_from, landed};
    };

    // Leave 25px between the newest message and the composer, so that
    // three lines of mine cannot fit without the feed moving.
    await ctx.open(PINS.hash, {fresh: true});
    let free_after = null;
    let sent = await send_held(3, async () => {
        const gap = async () =>
            await ctx.page.evaluate(
                () => window.__ac.feed.viewport().bottom - window.__ac.feed.rows().at(-1).bottom,
            );
        await wheel(ctx, -Math.round((await gap()) - 25), 800);
        free_after = await gap();
        await ctx.click("composeTextarea");
    });
    const newest_in_view =
        sent.sent_from.view.bottom >= sent.sent_from.view.top &&
        free_after !== null &&
        free_after >= 0;
    ctx.assert(
        "autoscroll.own-send.jumps",
        sent.echoed !== null && newest_in_view && sent.echoed.height > free_after
            ? sent.echoed.shown
            : null,
        "==",
        1,
        {
            note: `sent ${sent.echoed?.height}px of text with ${Math.round(free_after ?? 0)}px free between the newest message and the composer (request held, nothing posted); my message is at y ${sent.echoed?.top}, the readable area is ${sent.landed.view.top} to ${sent.landed.view.bottom}; the feed scrolled ${Math.round(sent.landed.scroll_top - sent.sent_from.scroll_top)}px`,
        },
    );

    await ctx.open(PINS.hash, {fresh: true});
    await wheel(ctx, -2500, 900);
    sent = await send_held(1);
    ctx.assert(
        "autoscroll.own-send.jumps-from-anywhere",
        sent.echoed === null ? null : sent.echoed.shown,
        "==",
        1,
        {
            note: `sent while scrolled ${Math.round(sent.sent_from.scroll_max - sent.sent_from.scroll_top)}px above the end (request held, nothing posted); my message is at y ${sent.echoed?.top}, the readable area is ${sent.landed.view.top} to ${sent.landed.view.bottom}`,
        },
    );
}

// Start with only part of the topic loaded, go towards the missing end,
// and watch the selected row while the rest arrives.
async function history_fetch(ctx, ids) {
    await ctx.open(PINS.hash, {fresh: true});
    // Home loads the oldest hundred and End the newest hundred, so after
    // both the first twenty are not in the page.
    await press(ctx, "Home", 1500);
    await press(ctx, "End", 1500);
    await ctx.settle();
    let state = await ctx.feed();
    const partial = state.model_ids.length;
    await ctx.emulate_latency(150);
    const mark = ctx.requests.length;
    await ctx.start_recording([], ["selected_id", "selected_top", "model_length"]);
    for (let step = 0; step < 14; step += 1) {
        const asked = ctx
            .requests_since(mark)
            .some(
                (request) =>
                    request.url.startsWith("/json/messages?") &&
                    request.url.includes("num_after=0"),
            );
        if (asked) {
            break;
        }
        await wheel(ctx, -1000, 450);
    }
    await ctx.page
        .waitForFunction(
            (length) => window.__ac.feed.list().all_messages().length > length,
            {timeout: 8000},
            partial,
        )
        .catch(() => undefined);
    await ctx.sleep(500);
    const frames = await ctx.stop_recording();
    await ctx.emulate_latency(0);
    state = await ctx.feed();
    ctx.assert(
        "fetch.older-near-top",
        partial < PINS.count &&
            state.model_ids.length === PINS.count &&
            state.model_ids[0] === ids[0],
        "==",
        1,
        {
            note: `${partial} of ${PINS.count} messages in the page; after scrolling towards the top: ${state.model_ids.length}, oldest is message ${ids.indexOf(state.model_ids[0])}`,
        },
    );
    const arrival = frames.findIndex(
        (frame, at) => at > 0 && frame.probes.model_length > frames[at - 1].probes.model_length,
    );
    const last = frames[arrival - 1]?.probes;
    const first = frames[arrival]?.probes;
    ctx.assert(
        "anchor.prepend.offset",
        arrival > 0 &&
            last.selected_id === first.selected_id &&
            last.selected_top !== null &&
            first.selected_top !== null
            ? Math.abs(first.selected_top - last.selected_top)
            : null,
        "<=",
        0.5,
        {
            ...PX,
            note:
                arrival > 0
                    ? `${first.model_length - last.model_length} older messages were inserted above; selected row at y ${last.selected_top} in the frame before and ${first.selected_top} in the frame after`
                    : "no frame in which older messages arrived",
        },
    );

    // The other direction, in a new page: Home loads the oldest hundred
    // and leaves the newest twenty out.
    await ctx.open(PINS.hash, {fresh: true});
    await press(ctx, "Home", 1500);
    await ctx.settle();
    const oldest_only = (await ctx.feed()).model_ids.length;
    for (let step = 0; step < 16; step += 1) {
        state = await wheel(ctx, 1000, 450);
        if (state.model_ids.length > oldest_only) {
            break;
        }
    }
    await ctx.sleep(600);
    state = await ctx.feed();
    ctx.assert(
        "fetch.newer-near-bottom",
        oldest_only < PINS.count &&
            state.model_ids.length === PINS.count &&
            state.model_ids.at(-1) === ids.at(-1),
        "==",
        1,
        {
            note: `${oldest_only} of ${PINS.count} messages in the page; after scrolling towards the end: ${state.model_ids.length}, newest is message ${ids.indexOf(state.model_ids.at(-1))}`,
        },
    );
}

async function history_and_groups(ctx, ids) {
    await ctx.open(PINS.hash, {fresh: true});
    for (let step = 0; step < 9; step += 1) {
        await press(ctx, "k", 150);
    }
    await ctx.sleep(700);
    const left_at = await ctx.feed();
    await ctx.navigate(ctx.topics.pinsChannel);
    await ctx.sleep(900);
    // The newest messages of the channel are whatever the checks posted
    // last, which in time is one topic only. Two messages in topics of
    // their own arrive (in this page only), so that the view always ends
    // with three runs of messages.
    const incoming = await incoming_factory(ctx, ids);
    for (const topic of ["armada-pins-group-a", "armada-pins-group-b"]) {
        const arrival = incoming(`a message in ${topic}`, 1);
        arrival.event.message.subject = topic;
        await ctx.inject([arrival.event]);
    }
    await ctx.sleep(900);
    // Away from where the channel view opens, so that Forward has
    // something to restore.
    const channel_opened_at = (await ctx.feed()).selected;
    for (let step = 0; step < 4; step += 1) {
        await press(ctx, "k", 150);
    }
    await ctx.sleep(700);
    const channel = await ctx.feed();

    // A channel view shows one group per run of messages of one topic.
    const groups = await ctx.page.evaluate(() => {
        let runs = 0;
        let previous;
        const rendered = new Set(
            window.__ac.all("messageRow").map((row) => Number(row.dataset.messageId)),
        );
        for (const message of window.__ac.feed.list().all_messages()) {
            if (!rendered.has(message.id)) {
                continue;
            }
            if (message.topic !== previous) {
                runs += 1;
                previous = message.topic;
            }
        }
        return {runs, headers: window.__ac.all("topicGroup").length};
    });
    ctx.assert(
        "multi-topic.group-per-topic",
        groups.runs >= 3 ? groups.headers : null,
        "==",
        groups.runs,
        {
            note: `${groups.runs} runs of one topic among the rendered messages of the channel, ${groups.headers} group headers`,
        },
    );

    await ctx.page.goBack();
    await ctx.page.waitForFunction(
        (hash) => window.location.hash === hash,
        {timeout: 8000},
        left_at.hash,
    );
    await ctx.sleep(1200);
    // Positions are read to 0.01px, and the difference has been seen at
    // exactly the limit (148.91 against 148.41): it is compared at the
    // resolution it was read at, not with the noise of the subtraction.
    const back = await ctx.feed();
    const moved_from_default = ids.indexOf(left_at.selected) !== PINS.count - 1;
    ctx.assert(
        "history.back.selection-and-offset",
        moved_from_default
            ? back.selected === left_at.selected
                ? round(Math.abs(back.selected_top - left_at.selected_top))
                : 9999
            : null,
        "<=",
        0.5,
        {
            ...PX,
            note: `left with message ${ids.indexOf(left_at.selected)} selected at y ${left_at.selected_top}; Back shows ${ids.indexOf(back.selected)} at y ${back.selected_top} (9999 when it is another message)`,
        },
    );
    await ctx.page.goForward();
    await ctx.page.waitForFunction(
        (hash) => window.location.hash === hash,
        {timeout: 8000},
        channel.hash,
    );
    await ctx.sleep(1200);
    const forward = await ctx.feed();
    ctx.assert(
        "history.forward.selection-and-offset",
        channel.selected === channel_opened_at
            ? null
            : forward.selected === channel.selected
              ? round(Math.abs(forward.selected_top - channel.selected_top))
              : 9999,
        "<=",
        0.5,
        {
            ...PX,
            note: `the channel view had message id ${channel.selected} selected at y ${channel.selected_top}; Forward shows ${forward.selected} at y ${forward.selected_top}`,
        },
    );
}

// With the newest message in view, a composer that grows must not cover it.
async function compose_growth(ctx) {
    await ctx.open(PINS.hash, {fresh: true});
    await ctx.open_compose();
    const measure = async () =>
        await ctx.page.evaluate(() => {
            const last = window.__ac.feed.rows().at(-1);
            return {last_bottom: last.bottom, compose_top: window.__ac.feed.viewport().bottom};
        });
    const before = await measure();
    for (let line = 0; line < 16; line += 1) {
        await ctx.page.keyboard.type(`line ${line + 1}`);
        await ctx.page.keyboard.down("Shift");
        await ctx.page.keyboard.press("Enter");
        await ctx.page.keyboard.up("Shift");
    }
    await ctx.sleep(700);
    const after = await measure();
    const grew = before.compose_top - after.compose_top;
    // Only a growth that reaches the old position of the last message
    // says anything.
    const would_cover = before.last_bottom > after.compose_top;
    ctx.assert(
        "compose-growth.stays-pinned",
        would_cover ? Math.max(0, after.last_bottom - after.compose_top) : null,
        "<=",
        0.5,
        {
            ...PX,
            note: `the composer grew ${Math.round(grew)}px (its top ${before.compose_top} -> ${after.compose_top}); the newest message ended at y ${before.last_bottom} and now ends at ${after.last_bottom}; counted: how much of it the composer covers`,
        },
    );
    // What does hold today: the reader can always scroll the newest
    // message clear of the composer, however tall it has grown.
    await wheel(ctx, 3000, 800);
    const scrolled = await measure();
    ctx.assert(
        "compose-growth.newest-can-be-scrolled-clear",
        would_cover ? Math.max(0, scrolled.last_bottom - scrolled.compose_top) : null,
        "<=",
        0.5,
        {
            ...PX,
            note: `after scrolling to the end with the grown composer the newest message ends at y ${scrolled.last_bottom}, the composer starts at ${scrolled.compose_top}`,
        },
    );
    await ctx.click("composeTextarea");
    await ctx.clear_compose();
    await ctx.page.keyboard.press("Escape");
    await ctx.sleep(300);
}

export async function run(ctx) {
    let ids;
    await ctx.scenario("fixture", async () => {
        ids = await prepare(ctx);
    });
    if (ids === undefined) {
        ids = await ensure_pins(ctx).catch(() => undefined);
    }
    if (ids === undefined) {
        return;
    }
    await ctx.scenario("pointer", async () => {
        await pointer_keys(ctx, ids);
        await tall_message(ctx, ids);
    });
    await ctx.scenario("code-scroll", async () => {
        await code_scroll(ctx, ids);
    });
    await ctx.scenario("read", async () => {
        await read_rules(ctx, ids);
    });
    await ctx.scenario("autoscroll", async () => {
        await anchoring_and_autoscroll(ctx, ids);
    });
    await ctx.scenario("fetch", async () => {
        await history_fetch(ctx, ids);
    });
    await ctx.scenario("history", async () => {
        await history_and_groups(ctx, ids);
    });
    await ctx.scenario("compose-growth", async () => {
        await compose_growth(ctx);
    });
}
