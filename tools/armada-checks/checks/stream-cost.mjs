// 14. What a streaming answer costs, on today's pipeline: the recorded
// runs in fixtures/ are fed through the application's own event code
// (`zulip_test.inject_events`) at the times the server would have sent
// them. Nothing is posted and no agent runs.

/* global window, document, requestAnimationFrame, MutationObserver, DOMParser */

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {edit_event} from "../lib/fixtures.mjs";
import {metrics_delta} from "../lib/harness.mjs";
import {max, median, sum} from "../lib/stats.mjs";
import {start_trace, stop_trace, timers} from "../lib/trace.mjs";

export const name = "stream-cost";
export const order = 140;
export const description =
    "a recorded answer replayed into the live run's message at 400 and 1,200 words a second: forced layouts, layouts and writes a frame, long tasks, how long a word waits, whether the newest words are in view; and the acknowledgement of a message to the agent";

const FIXTURES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../fixtures");
const FRAME_MS = 1000 / 60;
const LONG_TASK_MS = 50;

// Installed in the page: one sample a frame of the streaming row.
function install_stream_probe(message_id) {
    const ac = window.__ac;
    const CHROME =
        ".legacy-live-progress, .legacy-run-trace-reveal, .legacy-run-footer, [data-run-chrome]";
    const text_of = (root) => {
        const copy = root.cloneNode(true);
        for (const element of copy.querySelectorAll(CHROME)) {
            element.remove();
        }
        return copy.textContent.replaceAll(/\s+/g, " ").trim();
    };
    const count = (text) => (text === "" ? 0 : text.split(" ").length);
    const content_of = () =>
        ac.feed.row(message_id)?.querySelector(".message_content, [data-message-content]") ?? null;
    const probe = {on: true, frames: [], deliveries: 0, injected: [], handled: [], t0: null};
    ac.stream = probe;
    probe.text_of_html = (html) =>
        text_of(new DOMParser().parseFromString(`<div>${html}</div>`, "text/html").body.firstChild);
    probe.words_of_html = (html) => count(probe.text_of_html(html));
    probe.text = () => {
        const content = content_of();
        return content ? text_of(content) : "";
    };
    // One delivery is one burst of DOM changes made without yielding. The
    // run's own clock and progress line tick by themselves and are not
    // writes of the answer.
    const in_chrome = (node) =>
        (node.nodeType === 1 ? node : node.parentElement)?.closest(CHROME) !== null &&
        (node.nodeType === 1 ? node : node.parentElement) !== null;
    // Only changes to the streaming message count: its row being put in
    // the list, or anything changing inside it.
    const touches_answer = (record) => {
        const row = ac.feed.row(message_id);
        if (row === null) {
            return false;
        }
        if ([...record.addedNodes].some((node) => node === row || node.contains?.(row))) {
            return true;
        }
        return row.contains(record.target) && !in_chrome(record.target);
    };
    const observer = new MutationObserver((records) => {
        if (records.some((record) => touches_answer(record))) {
            probe.deliveries += 1;
        }
    });
    observer.observe(ac.last("feedList") ?? document.body, {
        subtree: true,
        childList: true,
        characterData: true,
    });
    const tick = () => {
        if (!probe.on) {
            observer.disconnect();
            return;
        }
        const content = content_of();
        const view = ac.feed.viewport();
        let newest_bottom = null;
        if (content) {
            const blocks = [...content.children].filter((child) => !child.matches(CHROME));
            newest_bottom = blocks.at(-1)?.getBoundingClientRect().bottom ?? null;
        }
        probe.frames.push({
            t: performance.now(),
            words: content ? count(text_of(content)) : 0,
            newest_bottom,
            view_bottom: view.bottom,
            shortened: content?.classList.contains("condensed") ?? false,
            deliveries: probe.deliveries,
        });
        probe.deliveries = 0;
        requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}

function schedule_events(events) {
    const probe = window.__ac.stream;
    probe.t0 = performance.now();
    for (const [index, entry] of events.entries()) {
        setTimeout(() => {
            probe.injected[index] = performance.now();
            window.zulip_test.inject_events([entry.event]);
            probe.handled[index] = performance.now() - probe.injected[index];
        }, entry.at_ms);
    }
    return probe.t0;
}

async function replay(ctx, label, run) {
    await ctx.open(ctx.topics.live, {fresh: true});
    await ctx.page.keyboard.press("End");
    await ctx.sleep(800);
    const target = await ctx.page.evaluate(() => {
        const row = window.__ac.last("agentLiveRow");
        if (!row) {
            return null;
        }
        const id = Number(row.dataset.messageId);
        return {id, rendered: window.zulip_test.get_message(id).content};
    });
    if (target === null) {
        throw new Error("no live agent message in the fixture topic");
    }
    const raw = await ctx.api("GET", `/json/messages/${target.id}`, {apply_markdown: false});
    const base_content = raw.data?.message?.content ?? "";

    await ctx.page.evaluate(install_stream_probe, target.id);
    await ctx.sleep(200);
    const start_words = await ctx.page.evaluate(() => window.__ac.stream.frames.at(-1).words);
    // How many words of the answer each event shows, by the same count.
    const shown_by = await ctx.page.evaluate(
        (edits) => edits.map((html) => window.__ac.stream.words_of_html(html)),
        run.edits.map((edit) => edit.rendered_content),
    );
    const events = run.edits.map((edit) => ({
        at_ms: edit.at_ms,
        event: {
            id: -1,
            ...edit_event(target.id, `${target.rendered}\n${edit.rendered_content}`, {
                content: `${base_content}\n\n${edit.content}`,
            }),
            stream_id: 22,
        },
    }));

    const long_before = await ctx.page.evaluate(() => window.__ac.long_tasks.length);
    const before = await ctx.metrics();
    await start_trace(ctx.page);
    await ctx.page.evaluate(schedule_events, events);
    await ctx.sleep(run.duration_ms + 1200);
    const trace = await stop_trace(ctx.page);
    const after = await ctx.metrics();
    const result = await ctx.page.evaluate((final_html) => {
        const probe = window.__ac.stream;
        probe.on = false;
        return {
            t0: probe.t0,
            injected: probe.injected,
            handled: probe.handled,
            frames: probe.frames,
            // Block boundaries carry white space in the server's HTML and
            // none in the page; the words are compared without it.
            final_text: probe.text().replaceAll(" ", ""),
            expected_text: probe.text_of_html(final_html).replaceAll(" ", ""),
        };
    }, run.edits.at(-1).rendered_content);
    const long_tasks = await ctx.page.evaluate(
        (from) => window.__ac.long_tasks.slice(from).map((task) => Math.round(task.dur)),
        long_before,
    );

    const frames = result.frames.filter((frame) => frame.t >= result.t0);
    const delta = metrics_delta(before, after);
    const final_words = shown_by.at(-1);
    const shown = (frame) => Math.max(0, frame.words - start_words);

    // When each word of the answer came into being: evenly over the run.
    const made_at = (word) => result.t0 + ((word + 1) / final_words) * run.duration_ms;
    let worst_wait = 0;
    for (const frame of frames) {
        const next = shown(frame);
        if (next < final_words && made_at(next) < frame.t) {
            worst_wait = Math.max(worst_wait, (frame.t - made_at(next)) / FRAME_MS);
        }
    }
    const first_frame = frames.findIndex((frame) => shown(frame) > 0);
    const first_event = result.injected[0];
    const streaming = first_frame === -1 ? [] : frames.slice(first_frame);
    const hidden = streaming.filter(
        (frame) =>
            frame.shortened ||
            frame.newest_bottom === null ||
            frame.newest_bottom > frame.view_bottom + 0.5,
    ).length;
    const forced = timers(trace);

    ctx.assert(
        `${label}.events-replayed`,
        result.injected.filter(Boolean).length,
        "==",
        run.edits.length,
        {
            note: `update_message events dispatched through the application's own event code; ${frames.length} frames watched, ${start_words} words in the message before`,
        },
    );
    ctx.assert(`${label}.forced-layouts`, sum(forced.map((timer) => timer.layouts)), "<=", 0, {
        note: `layouts forced inside the timers that delivered the events and inside the application's own; ${forced.filter((timer) => timer.layouts > 0).length} timers forced one`,
    });
    ctx.assert(`${label}.layouts-per-frame`, delta.LayoutCount / frames.length, "<=", 1.1, {
        note: `${delta.LayoutCount} layouts and ${delta.RecalcStyleCount} style recalculations in ${frames.length} frames`,
    });
    // With ten events in two hundred frames the budget above leaves room
    // for a hundred more layouts. Per event applied it does not: one
    // layout for the write, and one of slack for the run's own clock.
    ctx.assert(`${label}.layouts-per-event`, delta.LayoutCount / run.edits.length, "<=", 2, {
        note: `${delta.LayoutCount} layouts during the replay for ${run.edits.length} events`,
    });
    // The budget is one write a frame. On today's pipeline the count per
    // frame is 1 or 2 depending on where the frames fall, so it is
    // recorded, and the gate is the count that does not move: writes to
    // the answer per event received.
    const bursts = sum(frames.map((frame) => frame.deliveries));
    ctx.assert(`${label}.writes-per-event`, bursts / run.edits.length, "<=", 1, {
        note: `${bursts} bursts of DOM changes to the answer for ${run.edits.length} events, in ${frames.filter((frame) => frame.deliveries > 0).length} frames`,
    });
    ctx.info(`${label}.writes-per-frame.max`, max(frames.map((frame) => frame.deliveries)), {
        note: `budget 1; ${frames.filter((frame) => frame.deliveries > 1).length} frames had more than one`,
    });
    // A long task is 50 ms of the main thread. The ones that count here
    // are those the answer causes: events whose handling took that long.
    // Any other long task on the page in that time is recorded.
    //
    // Today one event takes 15 to 40 ms to apply and, in some runs on a
    // shared machine, one of the ten takes 51 ms or more; so the gate is
    // at three times the line, as for every other time (SPEC-ENGINEERING
    // section 8), and the count at the line itself is recorded.
    const slow = result.handled.filter((ms) => ms > LONG_TASK_MS);
    const very_slow = result.handled.filter((ms) => ms > 3 * LONG_TASK_MS);
    ctx.assert(`${label}.long-tasks`, very_slow.length, "<=", 0, {
        note: `events whose handling took more than ${3 * LONG_TASK_MS} ms; ${slow.length} took more than ${LONG_TASK_MS} ms; slowest ${Math.round(max(result.handled) ?? 0)} ms, median ${Math.round(median(result.handled) ?? 0)} ms`,
    });
    ctx.info(`${label}.long-tasks.over-${LONG_TASK_MS}ms`, slow.length, {
        note: "events whose handling was a long task; budget 0, not gated: it is 0 or 1 from run to run today",
    });
    ctx.info(`${label}.long-tasks.page`, long_tasks.length, {
        note: `every long task on the page during the replay, whatever caused it; durations ms: ${long_tasks.join(", ") || "none"}`,
    });
    ctx.assert(
        `${label}.words-waiting.max-frames`,
        first_frame === -1 ? null : worst_wait,
        "<=",
        2,
        {
            note: `longest time a word existed without being on screen, in frames, if the ${final_words} words arrive evenly over ${run.duration_ms} ms; today they are sent every ${run.flush_ms} ms`,
        },
    );
    ctx.assert(`${label}.frames-word-hidden`, first_frame === -1 ? null : hidden, "<=", 0, {
        note: `of ${streaming.length} frames after the first words: frames in which the newest text was below the readable area or cut by "Show more" (${streaming.filter((frame) => frame.shortened).length} shortened)`,
    });
    ctx.assert(
        `${label}.first-words.frames-to-paint`,
        // Words that never came are as late as the replay was long.
        first_event === undefined
            ? null
            : frames.filter(
                  (frame) =>
                      frame.t > first_event &&
                      (first_frame === -1 || frame.t <= frames[first_frame].t),
              ).length,
        "<=",
        1,
        {
            note:
                first_frame === -1
                    ? "no frame showed words of the answer: every frame after the first event is counted"
                    : `frames from the first event being handed to the page to the first frame that shows its words, that frame included (${Math.round(frames[first_frame].t - first_event)} ms)`,
        },
    );
    // A frame is counted when the page draws one, so a main thread that
    // is busy for 80 ms still reads as one frame. The time is recorded;
    // it is not gated because applying one event takes 15 to 50 ms today.
    ctx.info(
        `${label}.first-words.ms-to-paint`,
        first_frame === -1 || first_event === undefined
            ? null
            : frames[first_frame].t - first_event,
        {unit: "ms", note: "budget one frame, 16.7 ms"},
    );
    ctx.assert(
        `${label}.settle.text-equal`,
        result.expected_text.length > 0
            ? Number(result.final_text.endsWith(result.expected_text))
            : null,
        "==",
        1,
        {
            note: `the message ends with the ${result.expected_text.length} characters of the last event's text: ${result.final_text.length} characters shown`,
        },
    );
    ctx.assert(
        `${label}.script-ms-per-frame`,
        (delta.ScriptDuration * 1000) / frames.length,
        "<=",
        6,
        {
            unit: "ms",
            note: "mean over the replay; tripwire at three times the 2 ms budget, development build, traced",
        },
    );
    ctx.info(
        `${label}.event-handler-ms.median`,
        median(forced.filter((timer) => timer.dur > 0.5).map((timer) => timer.dur)),
        {
            unit: "ms",
            note: "timers longer than 0.5 ms during the replay",
        },
    );
    ctx.info(
        `${label}.layout-and-style-ms-per-frame`,
        ((delta.LayoutDuration + delta.RecalcStyleDuration) * 1000) / frames.length,
        {unit: "ms"},
    );
}

// A message for the agent must be acknowledged in the page before the
// server has said anything, by the element that will hold the answer.
async function acknowledgement(ctx) {
    await ctx.open(ctx.topics.short, {rows: false, fresh: true});
    await ctx.sleep(500);
    await ctx.open_compose();
    const sends = await ctx.hold_sends();
    try {
        const token = `ac-ack-${Date.now().toString(36)}`;
        await ctx.fill_compose(`@**Pear** armada-checks ${token}`);
        await ctx.page.evaluate(() => {
            const ac = window.__ac;
            const before = new Set(ac.all("agentAck"));
            ac.ack = {frames: [], on: true};
            window.addEventListener(
                "keydown",
                (event) => {
                    if (event.key !== "Enter" || ac.ack.t0 !== undefined) {
                        return;
                    }
                    ac.ack.t0 = performance.now();
                    const tick = () => {
                        if (!ac.ack.on) {
                            return;
                        }
                        ac.ack.frames.push(
                            ac.all("agentAck").filter((element) => !before.has(element)).length,
                        );
                        requestAnimationFrame(tick);
                    };
                    requestAnimationFrame(tick);
                },
                true,
            );
        });
        await ctx.page.keyboard.press("Enter");
        await ctx.sleep(900);
        const frames = await ctx.page.evaluate(() => {
            window.__ac.ack.on = false;
            return window.__ac.ack.frames;
        });
        const acknowledged = frames.some((count) => count > 0);
        ctx.assert(
            "ack.empty-frames",
            frames.length > 0 ? frames.filter((count) => count === 0).length : null,
            "<=",
            0,
            {
                note: `frames after Return, of ${frames.length} watched while the request was held (${sends.seen.length} held, nothing posted, no run started), that show neither an acknowledgement nor words from the agent`,
            },
        );
        // The second half, that the acknowledging element is the one the
        // answer is written into, needs the stream writer (C2, which owns
        // this file). Until it is written this reads 0 whether or not an
        // acknowledgement appears: a known failure with a number, not one
        // that stops being measurable the day the mark is added.
        ctx.assert("ack.same-element", frames.length > 0 ? 0 : null, "==", 1, {
            note: acknowledged
                ? "an acknowledgement appeared; following its element to the first words of the answer is not written yet"
                : "no acknowledging element appeared, so none can become the answer",
        });
    } finally {
        await sends.fail();
        await sends.stop();
    }
}

export async function run(ctx) {
    for (const rate of [400, 1200]) {
        await ctx.scenario(`${rate}wps`, async () => {
            const recorded = JSON.parse(
                fs.readFileSync(path.join(FIXTURES, `stream-${rate}.json`), "utf8"),
            );
            await replay(ctx, `${rate}wps`, recorded);
        });
    }
    await ctx.scenario("ack", async () => {
        await acknowledgement(ctx);
    });
}
