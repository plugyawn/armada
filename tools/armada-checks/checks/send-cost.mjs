// 6. What pressing Return costs, and what the feed does afterwards.

/* global window, requestAnimationFrame */

import {max, median} from "../lib/stats.mjs";
import {dispatches, start_trace, stop_trace} from "../lib/trace.mjs";

export const name = "send-cost";
export const order = 60;
export const description =
    "Return to the row on screen: handler time, forced layouts, whether the server echo replaces or moves the row, compose height; plain, @Pear, and the cases that are not echoed today";

const PX = {unit: "px"};
const WATCH_MS = 2500;
const HOLD_MS = 900;

// Records what happens after each Return: one sample per frame of the row
// that carries the probe's token.
function install_send_probe() {
    const ac = window.__ac;
    const identities = new WeakMap();
    let next_identity = 1;
    const identity = (element) => {
        if (!element) {
            return 0;
        }
        if (!identities.has(element)) {
            identities.set(element, next_identity);
            next_identity += 1;
        }
        return identities.get(element);
    };
    ac.send = {runs: [], token: "", watch_ms: 2500};
    window.addEventListener(
        "keydown",
        (event) => {
            if (event.key !== "Enter" || event.shiftKey) {
                return;
            }
            const token = ac.send.token;
            const mine = () =>
                ac.all("messageRow").findLast((row) => row.textContent.includes(token));
            const run = {
                token,
                t0: performance.now(),
                rows_before: ac.all("messageRow").length,
                ack_before: ac.all("armadaAck").length,
                compose_before: ac.rect("compose")?.h ?? null,
                frames: [],
            };
            ac.send.runs.push(run);
            const tick = () => {
                const row = mine();
                const box = row?.getBoundingClientRect();
                run.frames.push({
                    t: Math.round((performance.now() - run.t0) * 10) / 10,
                    row: identity(row),
                    content: identity(
                        row?.querySelector(".message_content, [data-message-content]"),
                    ),
                    top: box ? Math.round(box.top * 100) / 100 : null,
                    height: box ? Math.round(box.height * 100) / 100 : null,
                    rows: ac.all("messageRow").length,
                    ack: ac.all("armadaAck").length,
                    compose: ac.rect("compose")?.h ?? null,
                    scroll: ac.scroll_state().top,
                });
                if (performance.now() - run.t0 < ac.send.watch_ms) {
                    requestAnimationFrame(tick);
                }
            };
            requestAnimationFrame(tick);
        },
        true,
    );
}

// Put text in the composer without typing it key by key (typing would run
// typeahead, which typing-cost measures), then leave the caret after a
// space so no completion menu is open when Return is pressed.
async function fill(ctx, text) {
    await ctx.page.evaluate((value) => {
        const textarea = window.__ac.last("composeTextarea");
        textarea.focus();
        textarea.value = value;
        textarea.setSelectionRange(value.length, value.length);
        textarea.dispatchEvent(new window.Event("input", {bubbles: true}));
    }, text);
    await ctx.sleep(250);
    await ctx.page.keyboard.type(" ");
    await ctx.sleep(200);
}

async function prepare(ctx, hash) {
    await ctx.open(hash, {rows: false});
    await ctx.sleep(500);
    await ctx.open_compose();
    await ctx.page.evaluate(install_send_probe);
}

async function press_return(ctx, text, token) {
    await fill(ctx, text);
    await ctx.page.evaluate((value) => {
        window.__ac.send.token = value;
    }, token);
    await ctx.page.keyboard.press("Enter");
}

async function last_run(ctx) {
    return await ctx.page.evaluate(() => window.__ac.send.runs.at(-1));
}

// Hold every message POST so that nothing reaches the server: whatever the
// page shows meanwhile is, by construction, client-side. The requests are
// failed afterwards, so held sends never post anything (and a message that
// mentions the agent never starts a run).
async function pass_through(ctx, requestId) {
    try {
        await ctx.client.send("Fetch.continueRequest", {requestId});
    } catch {
        // The page went away with the request.
    }
}

async function hold_posts(ctx) {
    const held = [];
    await ctx.client.send("Fetch.enable", {
        patterns: [{urlPattern: "*/json/messages*", requestStage: "Request"}],
    });
    ctx.client.on("Fetch.requestPaused", (event) => {
        if (event.request.method === "POST") {
            held.push(event.requestId);
            return;
        }
        void pass_through(ctx, event.requestId);
    });
    return {
        count: () => held.length,
        async release() {
            for (const requestId of held.splice(0)) {
                try {
                    await ctx.client.send("Fetch.failRequest", {requestId, errorReason: "Aborted"});
                } catch {
                    // Already gone.
                }
            }
        },
    };
}

function first_row_frame(run) {
    return run.frames.findIndex((frame) => frame.row !== 0);
}

function compose_changes(run, until_ms = Number.POSITIVE_INFINITY) {
    // No compose box to measure is "not measurable", not "did not change".
    if (run.compose_before === null) {
        return null;
    }
    let changes = 0;
    let previous = run.compose_before;
    for (const frame of run.frames) {
        if (frame.t > until_ms) {
            break;
        }
        if (frame.compose !== previous) {
            changes += 1;
            previous = frame.compose;
        }
    }
    return changes;
}

async function plain_sends(ctx) {
    await prepare(ctx, ctx.topics.probe);
    const stamp = Date.now().toString(36);
    const samples = [];
    for (let index = 0; index < 3; index += 1) {
        const token = `ac-${stamp}-${index}`;
        const request_mark = ctx.requests.length;
        await fill(ctx, `armada-checks send probe ${token}`);
        await ctx.page.evaluate((value) => {
            window.__ac.send.token = value;
        }, token);
        await start_trace(ctx.page);
        await ctx.page.keyboard.press("Enter");
        await ctx.sleep(WATCH_MS + 300);
        const events = await stop_trace(ctx.page);
        const run = await last_run(ctx);
        const enter = dispatches(events, ["keydown"]).toSorted((a, b) => b.dur - a.dur)[0];
        const shown = first_row_frame(run);
        const after = run.frames.slice(Math.max(shown, 0));
        let replaced = 0;
        let content_replaced = 0;
        for (let frame = 1; frame < after.length; frame += 1) {
            if (after[frame].row !== 0 && after[frame].row !== after[frame - 1].row) {
                replaced += 1;
            }
            if (after[frame].content !== 0 && after[frame].content !== after[frame - 1].content) {
                content_replaced += 1;
            }
        }
        // Movement of the row relative to the feed's own scrolling: a row
        // that scrolls with the feed has not been moved by the echo.
        const anchored = after.map((frame) => frame.top + frame.scroll);
        samples.push({
            shown,
            shown_ms: shown === -1 ? null : run.frames[shown].t,
            handler_ms: enter?.dur ?? null,
            forced_layouts: enter?.layouts ?? null,
            forced_styles: enter?.styles ?? null,
            replaced,
            content_replaced,
            moved:
                after.length > 0
                    ? max(anchored.map((value) => Math.abs(value - anchored[0])))
                    : null,
            height_changed:
                after.length > 0
                    ? max(after.map((frame) => Math.abs(frame.height - after[0].height)))
                    : null,
            compose_changes: compose_changes(run),
            posts: ctx
                .requests_since(request_mark)
                .filter(
                    (request) =>
                        request.method === "POST" && request.url.startsWith("/json/messages"),
                ).length,
        });
    }
    const all = (key) => samples.map((sample) => sample[key]);
    const complete = samples.every((sample) => sample.shown !== -1);

    ctx.assert(
        "plain.messages-sent",
        samples.filter((sample) => sample.posts === 1).length,
        "==",
        3,
        {
            note: "sends that produced exactly one POST /json/messages",
        },
    );
    ctx.assert("plain.row-in-first-frame", complete ? max(all("shown")) : null, "<=", 0, {
        note: "index of the first animation frame after Return that contains the row (0 = the very next frame)",
    });
    ctx.assert("plain.enter-forced-layouts", median(all("forced_layouts")), "<=", 1, {
        note: `synchronous layouts inside the Return keydown handler; per send: ${all("forced_layouts").join(", ")}; style recalcs: ${all("forced_styles").join(", ")}`,
    });
    ctx.assert("plain.enter-handler-ms.median", median(all("handler_ms")), "<=", 8, {
        unit: "ms",
        note: `per send: ${all("handler_ms")
            .map((value) => Math.round(value))
            .join(", ")}; development build, traced`,
    });
    ctx.assert(
        "plain.row-replaced-by-server-echo",
        complete ? max(all("replaced")) : null,
        "<=",
        0,
        {
            note: "times the row element was swapped for another after it was first shown",
        },
    );
    ctx.assert(
        "plain.content-replaced-by-server-echo",
        complete ? max(all("content_replaced")) : null,
        "<=",
        0,
        {note: "times the message body element was swapped after it was first shown"},
    );
    ctx.assert("plain.row-moved-after-paint", complete ? max(all("moved")) : null, "<=", 0.5, {
        ...PX,
        note: "relative to the feed's scroll position",
    });
    ctx.assert(
        "plain.row-height-changed-after-paint",
        complete ? max(all("height_changed")) : null,
        "<=",
        0.5,
        PX,
    );
    const compose_measured = samples.every((sample) => sample.compose_changes !== null);
    ctx.assert(
        "plain.compose-height-changes",
        compose_measured ? max(all("compose_changes")) : null,
        "<=",
        0,
        {
            note: "frames in which the compose box changed height after Return",
        },
    );
    ctx.info("plain.enter-to-row-frame-ms.median", median(all("shown_ms")), {
        unit: "ms",
        note: "includes waiting for the next frame",
    });
}

// A held send: is the row there, is there an acknowledgement, does the box
// hold still, all before any server data can exist.
async function held_send(ctx, label, hash, text, {expect_ack = false} = {}) {
    await prepare(ctx, hash);
    const hold = await hold_posts(ctx);
    const token = `ac-${label}-${Date.now().toString(36)}`;
    await press_return(ctx, text.replace("TOKEN", token), token);
    await ctx.sleep(HOLD_MS);
    const run = await last_run(ctx);
    const posts_held = hold.count();
    await hold.release();
    await ctx.client.send("Fetch.disable");

    const shown = first_row_frame(run);
    ctx.assert(`${label}.row-before-server`, shown !== -1, "==", 1, {
        note:
            shown === -1
                ? `no row for ${HOLD_MS} ms while the POST was held (${posts_held} held)`
                : `row in frame ${shown}, ${run.frames[shown].t} ms after Return (${posts_held} POST held)`,
    });
    ctx.assert(`${label}.compose-height-changes`, compose_changes(run, HOLD_MS - 100), "<=", 0, {
        note: `compose heights seen: ${[...new Set([run.compose_before, ...run.frames.map((frame) => frame.compose)])].join(" -> ")}`,
    });
    if (expect_ack) {
        const ack_frame = run.frames.findIndex((frame) => frame.ack > run.ack_before);
        ctx.assert(`${label}.client-ack-before-server`, ack_frame !== -1, "==", 1, {
            note:
                ack_frame === -1
                    ? `no agent acknowledgement in the page for ${HOLD_MS} ms while the POST was held`
                    : `acknowledged in frame ${ack_frame}`,
        });
    }
}

// Today the sixth unacknowledged message is not echoed.
async function rapid_sends(ctx) {
    await prepare(ctx, ctx.topics.probe);
    const hold = await hold_posts(ctx);
    const stamp = Date.now().toString(36);
    const before = await ctx.page.evaluate(() => window.__ac.all("messageRow").length);
    for (let index = 0; index < 6; index += 1) {
        await press_return(
            ctx,
            `armada-checks rapid ac-rapid-${stamp}-${index}`,
            `ac-rapid-${stamp}-${index}`,
        );
        await ctx.sleep(250);
    }
    await ctx.sleep(400);
    const shown = await ctx.page.evaluate(
        (text) =>
            window.__ac.all("messageRow").filter((row) => row.textContent.includes(text)).length,
        `ac-rapid-${stamp}-`,
    );
    await hold.release();
    await ctx.client.send("Fetch.disable");
    ctx.assert("rapid.six-unacknowledged.rows-shown", shown, "==", 6, {
        note: `six sends with every POST held; the feed had ${before} rows before`,
    });
}

const NO_ECHO = [
    ["image-link", "armada-checks TOKEN see https://example.com/shot.png"],
    ["video-link", "armada-checks TOKEN https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    ["slash-command", "/poll armada-checks TOKEN"],
    ["topic-mention", "armada-checks TOKEN @**topic**"],
];

export async function run(ctx) {
    await ctx.scenario("plain", async () => {
        await plain_sends(ctx);
    });
    await ctx.scenario("agent", async () => {
        await held_send(ctx, "agent", ctx.topics.short, "@**Pear** armada-checks TOKEN", {
            expect_ack: true,
        });
    });
    for (const [label, text] of NO_ECHO) {
        await ctx.scenario(label, async () => {
            await held_send(ctx, label, ctx.topics.probe, text);
        });
    }
    await ctx.scenario("rapid", async () => {
        await rapid_sends(ctx);
    });
}
