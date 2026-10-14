// 4. The column's host changes what it holds, or opens as a sheet, and
// nothing else in the frame moves (replaces sidebar-toggle-stability, F5).

/* global window */

import {DESIGN, places, rest_composer} from "./frame-stability.mjs";

export const name = "column-stability";
export const order = 40;
export const description =
    "with each content the column's host can hold, and as a sheet, the header, views, conversation, band, composer and nav rows stay put, at 1920, 1440 and 1100, on every place";

const WATCHED = [
    "header",
    "headerViews",
    "lane",
    "inboxPane",
    "band",
    "composerTrack",
    "nav",
    "more",
];
const PX = {unit: "px"};

async function snapshot(ctx) {
    return await ctx.page.evaluate((names) => {
        const ac = window.__ac;
        return {
            rects: ac.rects(names),
            rows: {...ac.labelled("channelName"), ...ac.labelled("topicName")},
            sections: ac.all("columnSection").length,
            heading: ac.rect("columnHeading"),
            sheet: ac.first("sheet")?.dataset.state ?? null,
        };
    }, WATCHED);
}

function worst_move(before, after) {
    let worst = {delta: 0, what: "nothing moved"};
    for (const element of WATCHED) {
        const a = before.rects[element];
        const b = after.rects[element];
        if (a === null && b === null) {
            continue;
        }
        if (a === null || b === null) {
            return {delta: null, what: `${element} ${a === null ? "appeared" : "vanished"}`};
        }
        const delta = Math.max(
            Math.abs(a.x - b.x),
            Math.abs(a.y - b.y),
            Math.abs(a.w - b.w),
            Math.abs(a.h - b.h),
        );
        if (delta > worst.delta) {
            worst = {delta: Math.round(delta * 100) / 100, what: element};
        }
    }
    for (const [label, a] of Object.entries(before.rows)) {
        const b = after.rows[label];
        if (b === undefined) {
            continue;
        }
        const delta = Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));
        if (delta > worst.delta) {
            worst = {delta: Math.round(delta * 100) / 100, what: `nav row ${label}`};
        }
    }
    return worst;
}

async function hold(ctx, label, before, act) {
    await act();
    await ctx.settle(300, 3000);
    await ctx.sleep(300);
    const after = await snapshot(ctx);
    const moved = worst_move(before, after);
    ctx.assert(`${label}.moved`, moved.delta, "<=", 0.5, {...PX, note: moved.what});
    return after;
}

export async function run(ctx) {
    for (const size of [1920, 1440, 1100]) {
        await ctx.scenario(`w${size}`, async () => {
            await ctx.page.setViewport({
                width: size,
                height: DESIGN[size].height,
                deviceScaleFactor: 1,
            });
            let first = true;
            for (const [place, hash] of places(ctx)) {
                if (first) {
                    await ctx.open(hash);
                    first = false;
                } else {
                    await ctx.navigate(hash);
                }
                // A press outside the old composer closes it, which moves
                // the band (composer.open.*, B3); start from it closed.
                await rest_composer(ctx);
                await ctx.park_pointer();
                await ctx.sleep(300);
                const label = `w${size}.${place}`;
                const rest = await snapshot(ctx);
                // At rest the host holds the place's own sections (stub
                // contents until B4 and B8): one standing, or folded into
                // its heading under the threshold.
                ctx.assert(`${label}.sections-at-rest`, rest.heading === null ? 0 : 1, "==", 1, {
                    note: `${rest.sections} sections under the heading`,
                });
                // The host as a sheet: More opens it, Esc closes it.
                const open = await hold(ctx, `${label}.sheet-open`, rest, async () => {
                    await ctx.click("more");
                });
                ctx.assert(`${label}.sheet-opened`, open.sheet === "open" ? 1 : 0, "==", 1, {
                    note: `sheet ${open.sheet}`,
                });
                const closed = await hold(ctx, `${label}.sheet-closed`, rest, async () => {
                    await ctx.page.keyboard.press("Escape");
                });
                ctx.assert(`${label}.sheet-closed-by-esc`, closed.sheet === null ? 1 : 0, "==", 1, {
                    note: `sheet ${closed.sheet}`,
                });
                // The heading opens the same sheet, folded or standing.
                await hold(ctx, `${label}.heading-pressed`, rest, async () => {
                    await ctx.click("columnHeading");
                });
                await ctx.page.keyboard.press("Escape");
                await ctx.settle(200, 2000);
            }
        });
    }
    await ctx.page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
}
