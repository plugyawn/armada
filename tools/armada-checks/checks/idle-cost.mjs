// 7. What the page does when nobody touches it.

/* global window, document, MutationObserver */

import {is_api_request, metrics_delta} from "../lib/harness.mjs";
import {round} from "../lib/stats.mjs";

export const name = "idle-cost";
export const order = 70;
export const description =
    "layouts, style recalculations, main-thread time and requests per minute on a quiet topic, a live run and a wall of monitors";

const WINDOW_SECONDS = 20;
const HEARTBEAT = /^\/json\/(events|users\/me\/presence)/;

// Per-minute budgets. A quiet topic should do nothing. A live topic may
// tick a clock once a second and poll slowly; anything animated belongs on
// the compositor, where it costs no layout and no style recalculation.
const BUDGETS = {
    quiet: {layouts: 10, styles: 10, task_ms: 500, requests: 0},
    "live-run": {layouts: 120, styles: 240, task_ms: 1000, requests: 6},
    "wall-monitors": {layouts: 120, styles: 240, task_ms: 1000, requests: 6},
};

async function idle(ctx, label, hash) {
    await ctx.open(hash);
    await ctx.park_pointer();
    // Let entry fetches and their renders finish.
    await ctx.sleep(4000);
    await ctx.page.evaluate(() => {
        window.__ac.idle_mutations = 0;
        new MutationObserver((records) => {
            window.__ac.idle_mutations += records.length;
        }).observe(document.body, {
            subtree: true,
            childList: true,
            attributes: true,
            characterData: true,
        });
    });
    const request_mark = ctx.requests.length;
    const before = await ctx.metrics();
    await ctx.sleep(WINDOW_SECONDS * 1000);
    const after = await ctx.metrics();
    const delta = metrics_delta(before, after);
    // Zulip's own heartbeat (event long-poll, presence) is not counted: it
    // lands in some 20-second windows and not in others.
    const requests = ctx
        .requests_since(request_mark)
        .filter((request) => is_api_request(request) && !HEARTBEAT.test(request.url));
    const mutations = await ctx.page.evaluate(() => window.__ac.idle_mutations);
    const animations = await ctx.page.evaluate(
        () =>
            document.getAnimations().filter((animation) => animation.playState === "running")
                .length,
    );
    const per_minute = (value) => round((value * 60) / WINDOW_SECONDS, 1);
    const budget = BUDGETS[label];
    const by_url = {};
    for (const request of requests) {
        const key = `${request.method} ${request.url.replace(/\?.*$/, "")}`;
        by_url[key] = (by_url[key] ?? 0) + 1;
    }

    ctx.assert(`${label}.layouts-per-minute`, per_minute(delta.LayoutCount), "<=", budget.layouts, {
        note: `${delta.LayoutCount} in ${WINDOW_SECONDS} s`,
    });
    ctx.assert(
        `${label}.style-recalcs-per-minute`,
        per_minute(delta.RecalcStyleCount),
        "<=",
        budget.styles,
        {note: `${delta.RecalcStyleCount} in ${WINDOW_SECONDS} s`},
    );
    ctx.assert(
        `${label}.main-thread-ms-per-minute`,
        per_minute(delta.TaskDuration * 1000),
        "<=",
        budget.task_ms,
        {unit: "ms", note: `script ${per_minute(delta.ScriptDuration * 1000)} ms of it`},
    );
    ctx.assert(`${label}.requests-per-minute`, per_minute(requests.length), "<=", budget.requests, {
        note:
            Object.entries(by_url)
                .map(([key, count]) => `${count}x ${key}`)
                .join("; ") || "none",
    });
    ctx.info(`${label}.dom-mutation-records-per-minute`, per_minute(mutations));
    ctx.info(`${label}.running-animations`, animations);
}

export async function run(ctx) {
    await ctx.scenario("quiet", async () => {
        await idle(ctx, "quiet", ctx.topics.other);
    });
    await ctx.scenario("live-run", async () => {
        await idle(ctx, "live-run", ctx.topics.live);
    });
    await ctx.scenario("wall-monitors", async () => {
        await idle(ctx, "wall-monitors", ctx.topics.wall);
    });
}
