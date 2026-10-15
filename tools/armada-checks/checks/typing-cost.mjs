// 5. What one keystroke in the composer costs.

/* global window */

import {metrics_delta} from "../lib/harness.mjs";
import {max, median, quantile, round} from "../lib/stats.mjs";
import {dispatches, start_trace, stop_trace, timers} from "../lib/trace.mjs";

export const name = "typing-cost";
export const order = 50;
export const description =
    "200 characters typed into the open composer on a 77-message topic: handler time, forced layouts, long tasks; plain prose and @ # : tokens";

const PROSE =
    "the quick brown fox jumps over the lazy dog while the build runs and we wait for green checks before merging this change into main so nobody is surprised by a late failure in the release branch okay";
const TOKENS =
    "ask @des about #cli and :smi then @iag for the `code` path and **bold** text plus #den and :thu and @oth okay then we continue typing plain words until the end of this line to see what the menu costs us ok";

// Handler events of one keystroke. keypress is left out: its dispatch
// contains the browser inserting the character, which is not handler work.
const HANDLER_EVENTS = ["keydown", "input", "keyup"];

// What ran in the slices (key handlers, timers) that forced a layout: the
// function Chromium entered first in each, as "function@line in file" of
// the development bundle, most frequent first. A failure then names its
// cause in the report.
function forced_by(events, slices) {
    const counts = new Map();
    for (const slice of slices) {
        const call = events.find(
            (event) =>
                event.name === "FunctionCall" &&
                event.ts >= slice.ts &&
                event.ts + (event.dur ?? 0) <= slice.ts + slice.dur * 1000,
        );
        const data = call?.args?.data;
        const where = data
            ? `${data.functionName || "(anonymous)"}@${data.lineNumber} in ${String(data.url ?? "")
                  .split("/")
                  .pop()
                  .slice(0, 40)}`
            : "(no script)";
        counts.set(where, (counts.get(where) ?? 0) + 1);
    }
    return [...counts]
        .toSorted((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([where, count]) => `${count}x ${where}`)
        .join(" | ");
}

async function type_text(ctx, label, text) {
    await ctx.open(ctx.topics.long);
    await ctx.open_compose();
    // Opening the composer scrolls the conversation (an animation of the
    // application's), and the scroll's own end is a timer 100 ms after its
    // last event (message_scroll.ts, scroll_finished) that reads layout.
    // On a busy machine both were still under way when typing began, and
    // were counted as typing: wait until the feed has stopped moving. The
    // feed scrolls inside the frame, not the document (lib/page.mjs,
    // ac.scroller).
    await ctx.settle(400, 4000);
    const still = await ctx.page.evaluate(async () => {
        let position = window.__ac.scroll_state().top;
        let still_since = performance.now();
        const started = still_since;
        let moves = 0;
        while (performance.now() - still_since < 400 && performance.now() - started < 4000) {
            await new Promise((resolve) => {
                setTimeout(resolve, 50);
            });
            const top = window.__ac.scroll_state().top;
            if (top !== position) {
                position = top;
                still_since = performance.now();
                moves += 1;
            }
        }
        return {
            ms: Math.round(still_since - started),
            moves,
            is_document: window.__ac.scroll_state().is_document,
        };
    });
    const rows = await ctx.page.evaluate(() => window.__ac.all("messageRow").length);
    const long_tasks_before = await ctx.page.evaluate(() => window.__ac.long_tasks.length);
    const before = await ctx.metrics();
    await start_trace(ctx.page);
    await ctx.page.keyboard.type(text, {delay: 20});
    await ctx.sleep(700);
    const events = await stop_trace(ctx.page);
    const after = await ctx.metrics();
    const long_tasks = await ctx.page.evaluate(
        (start) => window.__ac.long_tasks.slice(start).map((task) => task.dur),
        long_tasks_before,
    );
    const typed = await ctx.page.evaluate(() => window.__ac.last("composeTextarea")?.value.length);

    // Group handler dispatches into keystrokes: each keydown starts one.
    const handled = dispatches(events, HANDLER_EVENTS);
    const per_key = [];
    for (const dispatch of handled) {
        if (dispatch.label === "keydown" || per_key.length === 0) {
            per_key.push({ms: 0, layouts: 0});
        }
        per_key.at(-1).ms += dispatch.dur;
        per_key.at(-1).layouts += dispatch.layouts;
    }
    const keys = text.length;
    const delta = metrics_delta(before, after);
    const timer_layouts = timers(events).filter((timer) => timer.layouts > 0);

    ctx.assert(`${label}.characters-typed`, typed ?? null, "==", keys, {
        note: `${per_key.length} keystrokes traced, ${rows} message rows in the feed; the feed (${still.is_document ? "the document" : "its own scroller"}) moved ${still.moves} times and was still from ${still.ms} ms before typing began`,
    });
    ctx.assert(
        `${label}.handler-forced-layouts.max-per-key`,
        max(per_key.map((key) => key.layouts)),
        "<=",
        0,
        {
            note: `${per_key.filter((key) => key.layouts > 0).length} of ${per_key.length} keystrokes forced a layout inside a key handler${
                handled.some((dispatch) => dispatch.layouts > 0)
                    ? `; ${handled
                          .filter((dispatch) => dispatch.layouts > 0)
                          .slice(0, 3)
                          .map(
                              (dispatch) =>
                                  `${dispatch.label} of key ${1 + handled.filter((other) => other.label === "keydown" && other.ts <= dispatch.ts).length} ("${text[handled.filter((other) => other.label === "keydown" && other.ts <= dispatch.ts).length - 1] ?? "?"}")`,
                          )
                          .join(", ")}; forced by: ${forced_by(
                          events,
                          handled.filter((dispatch) => dispatch.layouts > 0),
                      )}`
                    : ""
            }`,
        },
    );
    ctx.assert(`${label}.timers-forcing-layout`, timer_layouts.length, "<=", 0, {
        note: `timer callbacks that forced a synchronous layout while typing${
            timer_layouts.length > 0 ? `; forced by: ${forced_by(events, timer_layouts)}` : ""
        }`,
    });
    ctx.assert(`${label}.layouts-per-key`, delta.LayoutCount / keys, "<=", 1.5, {
        note: `${delta.LayoutCount} layouts for ${keys} keys; the browser itself needs 1`,
    });
    ctx.assert(`${label}.style-recalcs-per-key`, delta.RecalcStyleCount / keys, "<=", 2, {
        note: `${delta.RecalcStyleCount} style recalcs for ${keys} keys`,
    });
    // Milliseconds are noisy in a traced development build on a shared
    // machine: the median is asserted against three times the 1 ms budget
    // as a tripwire, and the p95 the budget is written for is recorded.
    ctx.assert(`${label}.handler-ms.median`, median(per_key.map((key) => key.ms)), "<=", 3, {
        unit: "ms",
        note: "keydown + input + keyup dispatch time per key; tripwire at 3x the 1 ms budget",
    });
    ctx.info(
        `${label}.handler-ms.p95`,
        quantile(
            per_key.map((key) => key.ms),
            0.95,
        ),
        {
            unit: "ms",
            note: "budget: 1 ms in a production build",
        },
    );
    ctx.info(`${label}.handler-ms.max`, max(per_key.map((key) => key.ms)), {unit: "ms"});
    ctx.info(`${label}.long-tasks`, long_tasks.length, {
        note: `durations ms: ${long_tasks.map((duration) => Math.round(duration)).join(", ") || "none"}`,
    });
    ctx.info(`${label}.script-ms-per-key`, round((delta.ScriptDuration * 1000) / keys), {
        unit: "ms",
    });
    ctx.info(
        `${label}.layout-and-style-ms-per-key`,
        round(((delta.LayoutDuration + delta.RecalcStyleDuration) * 1000) / keys),
        {unit: "ms"},
    );
    return {long_tasks};
}

export async function run(ctx) {
    await ctx.scenario("prose", async () => {
        await type_text(ctx, "prose", PROSE.slice(0, 200));
    });
    await ctx.scenario("tokens", async () => {
        const {long_tasks} = await type_text(ctx, "tokens", TOKENS.slice(0, 200));
        ctx.assert("tokens.long-tasks", long_tasks.length, "<=", 0, {
            note: `durations ms: ${long_tasks.map((duration) => Math.round(duration)).join(", ") || "none"}`,
        });
    });
}
