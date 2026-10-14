// Motion, on the showroom's bench: everything moves, nothing jumps,
// nothing queues, and only transform and opacity animate (colour on the
// fades). Hover, press, focus and selection are measured frame by frame.

/* global document, window */

import {sleep} from "../lib/browser.mjs";
import {install_sampler, open_showroom} from "../lib/showroom.mjs";

export const name = "motion";
export const order = 21;
export const description =
    "showroom bench: hover and press in the frame, one ring and one thumb, the opposite input neither jumps nor queues, reduced motion, only transform and opacity animate";

const bench = (id) => `[data-armada="bench-${id}"]`;

async function centre(ctx, selector) {
    return await ctx.page.evaluate((query) => {
        const element = document.querySelector(query);
        element.scrollIntoView({block: "center"});
        const r = element.getBoundingClientRect();
        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
    }, selector);
}

// Every animation the page runs while `act` plays out: what it animates
// and for how long.
async function watching(ctx, act, ms = 700) {
    await ctx.page.evaluate((lasts) => {
        window.__armada_seen = [];
        const started = window.performance.now();
        const seen = new Set();
        const tick = () => {
            for (const animation of document.getAnimations()) {
                if (seen.has(animation)) {
                    continue;
                }
                seen.add(animation);
                const timing = animation.effect.getComputedTiming();
                window.__armada_seen.push({
                    properties: [
                        ...new Set(
                            animation.effect.getKeyframes().flatMap((frame) => Object.keys(frame)),
                        ),
                    ].filter(
                        (key) => !["offset", "easing", "composite", "computedOffset"].includes(key),
                    ),
                    duration: timing.duration,
                    loops: timing.iterations === Infinity,
                    easing:
                        timing.easing +
                        animation.effect
                            .getKeyframes()
                            .map((frame) => frame.easing)
                            .join(" "),
                });
            }
            if (window.performance.now() - started < lasts) {
                window.requestAnimationFrame(tick);
            }
        };
        tick();
    }, ms);
    await act();
    await sleep(ms + 100);
    return await ctx.page.evaluate(() => window.__armada_seen);
}

async function hover_and_press(ctx) {
    const row = `${bench("nav")} .armada-nav-kids .armada-nav-row`;
    const at = await centre(ctx, row);
    await ctx.page.mouse.move(at.x, at.y - 200);
    await sleep(200);
    // Sampling starts at the pointer's arrival, in the page.
    await ctx.page.evaluate((query) => {
        const element = document.querySelector(query);
        const label = element.querySelector(".armada-nav-label");
        window.__armada_hover = {};
        const read = () => ({
            opacity: Number(window.getComputedStyle(element, "::before").opacity),
            label: label.getBoundingClientRect().left + label.getBoundingClientRect().top,
        });
        window.__armada_hover.rest = read();
        element.addEventListener(
            "pointerenter",
            () => {
                window.__armada_hover.enter = window.__armada_sample(read, 260);
            },
            {once: true},
        );
        element.addEventListener(
            "pointerleave",
            () => {
                window.__armada_hover.leave = window.__armada_sample(read, 260);
            },
            {once: true},
        );
    }, row);
    await ctx.page.mouse.move(at.x, at.y);
    await sleep(400);
    await ctx.page.mouse.move(at.x, at.y - 200);
    await sleep(400);
    const hover = await ctx.page.evaluate(async () => ({
        rest: window.__armada_hover.rest,
        enter: await window.__armada_hover.enter,
        leave: await window.__armada_hover.leave,
    }));
    const first = hover.enter?.[1] ?? hover.enter?.[0];
    ctx.assert("hover.fill-in-the-frame-of-entry", first?.v.opacity ?? null, ">=", 0.28, {
        note: `opacity ${first?.v.opacity} at ${Math.round(first?.t ?? 0)} ms after the pointer arrived`,
    });
    const whole = hover.enter?.find((sample) => sample.v.opacity >= 0.99);
    ctx.assert("hover.whole-within-ms", whole?.t ?? null, "<=", 80 * 3, {
        unit: " ms",
        note: "80 ms; a time gates at three times its budget",
    });
    const gone = hover.leave?.find((sample) => sample.v.opacity <= 0.01);
    ctx.assert("hover.gone-after-leaving-ms", gone?.t ?? null, "<=", 60 * 3, {
        unit: " ms",
        note: "the trail, 60 ms; a time gates at three times its budget",
    });
    const moved = Math.max(
        ...(hover.enter ?? []).map((sample) => Math.abs(sample.v.label - hover.rest.label)),
    );
    ctx.assert("hover.label-moved-px", Number.isFinite(moved) ? moved : null, "<=", 0.01, {
        unit: " px",
    });

    // Press: the deepest fill in the frame, arriving at 0.96 of the box.
    await ctx.page.mouse.move(at.x, at.y);
    await sleep(300);
    await ctx.page.evaluate((query) => {
        const element = document.querySelector(query);
        const probe = document.createElement("i");
        probe.style.backgroundColor = "var(--fill-press)";
        document.body.append(probe);
        const pressed = window.getComputedStyle(probe).backgroundColor;
        probe.remove();
        element.addEventListener(
            "pointerdown",
            () => {
                window.__armada_press = window.__armada_sample(() => {
                    const style = window.getComputedStyle(element, "::before");
                    return {
                        fill: style.backgroundColor === pressed,
                        scale: new window.DOMMatrix(style.transform).a,
                        opacity: Number(style.opacity),
                    };
                }, 300);
            },
            {once: true},
        );
    }, row);
    await ctx.page.mouse.down();
    await sleep(400);
    const press = await ctx.page.evaluate(async () => await window.__armada_press);
    await ctx.page.mouse.move(at.x, at.y - 200);
    await ctx.page.mouse.up();
    const frame = press?.[1] ?? press?.[0];
    ctx.assert(
        "press.fill-in-the-frame",
        frame ? Number(frame.v.fill && frame.v.opacity === 1) : null,
        "==",
        1,
    );
    ctx.assert("press.first-frame-scale", press?.[0]?.v.scale ?? null, "<=", 0.975, {
        note: "arrives at 0.96 of the box",
    });
    ctx.assert("press.settles-to-scale", press?.at(-1)?.v.scale ?? null, "==", 1);
}

// The position a spring reaches `dt` seconds on from x moving at v towards
// `target`, by small steps.
function spring_step(x, v, target, spring, dt) {
    const omega = (2 * Math.PI) / spring.response;
    const steps = 200;
    const h = dt / steps;
    for (let i = 0; i < steps; i += 1) {
        v += (-omega * omega * (x - target) - 2 * spring.damping * omega * v) * h;
        x += v * h;
    }
    return x;
}

// How far a sample stands from every place a continuous spring could have
// taken it: from the motion before it (position and velocity, fitted to the
// three samples before), towards the old target or the new one, whichever
// the frame's input reached. A retarget that keeps position and velocity
// reads near 0 however fast the thing travels; a jump reads its size.
function discontinuity(samples, k, axis, targets, spring) {
    const [a, b, c, d] = [samples[k - 3], samples[k - 2], samples[k - 1], samples[k]];
    if (!a || !d) {
        return 0;
    }
    const t = (sample) => sample.v[2] / 1000;
    const x = (sample) => sample.v[axis];
    const v1 = (x(c) - x(b)) / (t(c) - t(b));
    const v0 = (x(b) - x(a)) / (t(b) - t(a));
    const acceleration = (v1 - v0) / ((t(c) - t(a)) / 2);
    const v = v1 + (acceleration * (t(c) - t(b))) / 2;
    const reach = targets.map((target) => spring_step(x(c), v, target[axis], spring, t(d) - t(c)));
    const [low, high] = [Math.min(...reach), Math.max(...reach)];
    return x(d) < low ? low - x(d) : x(d) > high ? x(d) - high : 0;
}

// Act, act again the other way 100 ms later, and watch one element: it
// must not jump in a frame, and nothing may still be running 500 ms on.
async function opposite(ctx, id, control, watched, which, token) {
    const controls = await ctx.page.evaluate(
        (query) =>
            [...document.querySelectorAll(query)].map((element) => {
                element.scrollIntoView({block: "center"});
                const r = element.getBoundingClientRect();
                return {x: r.left + r.width / 2, y: r.top + r.height / 2};
            }),
        control,
    );
    const [first, second] = [controls[which[0]], controls[which[1]]];
    if (!first || !second) {
        ctx.assert(`opposite.${id}.max-frame-jump-px`, null, "<=", 7.99);
        return;
    }
    const at_rest = async () =>
        await ctx.page.evaluate((query) => {
            const r = document.querySelector(query).getBoundingClientRect();
            return [r.left, r.top];
        }, watched);
    const spring = await ctx.page.evaluate((name) => {
        const style = window.getComputedStyle(document.documentElement);
        return {
            response: Number.parseFloat(style.getPropertyValue(`--${name}-r`)),
            damping: Number.parseFloat(style.getPropertyValue(`--${name}-z`)),
        };
    }, token);
    // Where each of the two inputs sends it, each measured at rest.
    await ctx.page.mouse.click(second.x, second.y);
    await sleep(700);
    await ctx.page.mouse.click(first.x, first.y);
    await sleep(700);
    const after_first = await at_rest();
    await ctx.page.mouse.click(second.x, second.y);
    await sleep(700);
    const after_second = await at_rest();
    await ctx.page.mouse.click(first.x, first.y);
    await sleep(700);
    await ctx.page.evaluate(() => {
        window.__armada_inputs = [];
        window.addEventListener(
            "pointerdown",
            () => window.__armada_inputs.push(window.performance.now()),
            {capture: true},
        );
    });
    const sampling = ctx.page.evaluate(
        (query) =>
            window.__armada_sample(() => {
                const r = document.querySelector(query).getBoundingClientRect();
                return [r.left, r.top, window.performance.now()];
            }, 800),
        watched,
    );
    await sleep(40);
    await ctx.page.mouse.click(second.x, second.y);
    await sleep(100);
    await ctx.page.mouse.click(first.x, first.y);
    const samples = await sampling;
    const inputs = await ctx.page.evaluate(() => window.__armada_inputs);
    // In the frame of the opposite input, and the next, the thing goes on
    // from where it was at the speed it had.
    let jump = 0;
    let travelled = 0;
    for (const at of inputs.slice(-1)) {
        const index = samples.findIndex((sample) => sample.v[2] >= at);
        for (const k of [index, index + 1]) {
            if (k < 3 || k >= samples.length) {
                continue;
            }
            for (const axis of [0, 1]) {
                jump = Math.max(
                    jump,
                    discontinuity(samples, k, axis, [after_second, after_first], spring),
                );
            }
        }
    }
    for (const sample of samples) {
        travelled = Math.max(
            travelled,
            Math.abs(sample.v[0] - samples[0].v[0]),
            Math.abs(sample.v[1] - samples[0].v[1]),
        );
    }
    ctx.assert(`opposite.${id}.max-frame-jump-px`, inputs.length >= 2 ? jump : null, "<=", 7.99, {
        unit: " px",
        note: `how far it stood, in the frame of the opposite input and the next, from where a "${token}" spring keeping its position and speed could have taken it; it travelled up to ${travelled.toFixed(1)} px over ${samples.length} frames; ${inputs.length} inputs`,
    });
    await sleep(500);
    const running = await ctx.page.evaluate(
        (query) =>
            document.getAnimations().filter((animation) => {
                const target = animation.effect.target;
                return (
                    target &&
                    target.closest(query) !== null &&
                    animation.effect.getComputedTiming().iterations !== Infinity
                );
            }).length,
        bench(id),
    );
    ctx.assert(`opposite.${id}.running-500ms-later`, running, "==", 0);
}

async function ring(ctx) {
    const near = `${bench("ring")} .armada-showroom-row .armada-text-control`;
    const at = await centre(ctx, near);
    await ctx.page.mouse.click(at.x, at.y);
    await sleep(300);
    const by_pointer = await ctx.page.evaluate(() =>
        Number(window.getComputedStyle(document.querySelector(".armada-ring")).opacity),
    );
    ctx.assert("ring.shown-for-a-pointer", by_pointer, "==", 0);
    const tab = async () => {
        const sampling = ctx.page.evaluate(() =>
            window.__armada_sample(() => {
                const element = document.querySelector(".armada-ring");
                const r = element.getBoundingClientRect();
                const focus = document.activeElement.getBoundingClientRect();
                return {
                    x: r.left,
                    y: r.top,
                    w: r.width,
                    opacity: Number(window.getComputedStyle(element).opacity),
                    fx: focus.left,
                    fy: focus.top,
                    fw: focus.width,
                };
            }, 420),
        );
        await sleep(30);
        await ctx.page.keyboard.press("Tab");
        return await sampling;
    };
    const neighbour = await tab();
    const settled = neighbour.at(-1).v;
    ctx.assert(
        "ring.count",
        await ctx.page.evaluate(() => document.querySelectorAll(".armada-ring").length),
        "==",
        1,
    );
    ctx.assert("ring.shown-for-the-keyboard", settled.opacity, "==", 1);
    ctx.assert("ring.offset-from-control-px", settled.fx - settled.x, "==", 2, {unit: " px"});
    const next = await tab();
    // Between neighbours it travels: early frames are between the two.
    const start = neighbour.at(-1).v.x;
    const end = next.at(-1).v.x;
    const between = next.filter(
        (sample) =>
            sample.v.x > Math.min(start, end) + 1 &&
            sample.v.x < Math.max(start, end) - 1 &&
            sample.v.opacity > 0.5,
    ).length;
    ctx.assert("ring.frames-travelling-between-neighbours", between, ">=", 3, {
        note: `from x ${start} to x ${end}`,
    });
    const far = await tab();
    const from = next.at(-1).v;
    const distance = Math.hypot(far.at(-1).v.x - from.x, far.at(-1).v.y - from.y);
    const crossing = far.filter(
        (sample) =>
            Math.hypot(sample.v.x - far.at(-1).v.x, sample.v.y - far.at(-1).v.y) > 8 &&
            sample.t > 34,
    ).length;
    ctx.assert("ring.far.frames-crossing-the-window", distance > 160 ? crossing : null, "==", 0, {
        note: `${Math.round(distance)} px away: it fades in there`,
    });
}

async function reduced(ctx) {
    const toggle = await centre(ctx, '[data-armada="bench-reduce"]');
    await ctx.page.mouse.click(toggle.x, toggle.y);
    await sleep(200);
    const tokens = await ctx.page.evaluate(() => {
        const style = window.getComputedStyle(document.documentElement);
        const read = (name) => style.getPropertyValue(name).trim();
        return {
            springs: ["--touch-t", "--move-t", "--settle-t", "--land-t"].map((token) =>
                read(token),
            ),
            fade_in: read("--fade-in"),
            fade_out: read("--fade-out"),
            loops: [
                ...document.querySelectorAll(
                    ".armada-mark[data-working='live'] > use, .armada-dots > use",
                ),
            ].filter((element) => window.getComputedStyle(element).animationName !== "none").length,
        };
    });
    ctx.assert(
        "reduced.spring-durations-not-1ms",
        tokens.springs.filter((value) => value !== "1ms").length,
        "==",
        0,
        {note: tokens.springs.join(", ")},
    );
    ctx.assert(
        "reduced.fades-kept",
        Number(tokens.fade_in === "80ms" && tokens.fade_out === "60ms"),
        "==",
        1,
    );
    ctx.assert("reduced.loops-running", tokens.loops, "==", 0);
    const refuse = `${bench("refuse")} > .armada-text-control`;
    const at = await centre(ctx, refuse);
    const seen = await watching(
        ctx,
        async () => {
            await ctx.page.mouse.click(at.x, at.y);
        },
        400,
    );
    const refusal = seen.find(
        (animation) => animation.duration === 200 && animation.properties.join(",") === "opacity",
    );
    ctx.assert("reduced.refusal-on-the-edge-ms", refusal?.duration ?? null, "==", 200, {
        unit: " ms",
    });
    ctx.assert(
        "reduced.transforms-animated",
        seen.filter(
            (animation) => animation.properties.includes("transform") && animation.duration > 1,
        ).length,
        "==",
        0,
    );
    await ctx.page.mouse.click(toggle.x, toggle.y);
    await sleep(200);
}

export async function run(ctx) {
    await ctx.scenario("bench", async () => {
        await open_showroom(ctx, "as=preact");
        await install_sampler(ctx.page);
        ctx.assert(
            "bench.items",
            await ctx.page.evaluate(
                () => document.querySelectorAll("[data-armada^='bench-']").length,
            ),
            ">=",
            15,
        );
        await hover_and_press(ctx);
    });
    await ctx.scenario("selection", async () => {
        await opposite(
            ctx,
            "views",
            `${bench("views")} .armada-view`,
            `${bench("views")} .armada-thumb`,
            [3, 0],
            "move",
        );
        const thumbs = await ctx.page.evaluate(
            (query) => document.querySelectorAll(`${query} .armada-thumb`).length,
            bench("list"),
        );
        ctx.assert("selection.thumbs-in-a-list", thumbs, "==", 1);
        const row = await centre(ctx, `${bench("list")} .armada-nav-row`);
        await ctx.page.mouse.click(row.x, row.y);
        await ctx.page.keyboard.press("ArrowDown");
        await sleep(400);
        const selected = await ctx.page.evaluate((query) => {
            const list = document.querySelector(query);
            const thumb = list.querySelector(".armada-thumb").getBoundingClientRect();
            const current = list.querySelector("[aria-current]").getBoundingClientRect();
            return {
                count: list.querySelectorAll("[aria-current]").length,
                off: Math.abs(thumb.top - current.top) + Math.abs(thumb.height - current.height),
            };
        }, bench("list"));
        ctx.assert(
            "selection.thumb-on-the-selected-row-px",
            selected.count === 1 ? selected.off : null,
            "<=",
            0.5,
            {unit: " px"},
        );
    });
    await ctx.scenario("opposite", async () => {
        await opposite(
            ctx,
            "popover",
            `${bench("popover")} > .armada-text-control`,
            `${bench("popover")} .armada-picker`,
            [0, 0],
            "move",
        );
        await opposite(
            ctx,
            "nav",
            `${bench("nav")} [aria-expanded]`,
            '[data-armada="bench-nav-block"]',
            [0, 0],
            "move",
        );
        await opposite(
            ctx,
            "column",
            `${bench("column")} > .armada-text-control`,
            '[data-armada="bench-column-pane"]',
            [0, 0],
            "touch",
        );
    });
    await ctx.scenario("surfaces", async () => {
        // A surface that holds words settles 4 px and does not scale.
        const control = await centre(ctx, `${bench("popover")} > .armada-text-control`);
        const hidden = await ctx.page.evaluate(
            (query) => {
                const popover = document.querySelector(query);
                return popover.hidden || popover.inert;
            },
            `${bench("popover")} .armada-picker`,
        );
        if (!hidden) {
            await ctx.page.mouse.click(control.x, control.y);
            await sleep(500);
        }
        const sampling = ctx.page.evaluate(
            (query) => {
                const popover = document.querySelector(query);
                const rest = () => {
                    const matrix = new window.DOMMatrix(
                        popover.getAnimations().length > 0
                            ? window.getComputedStyle(popover).transform
                            : "none",
                    );
                    return {scale: matrix.a, y: matrix.f, shown: !popover.hidden};
                };
                return window.__armada_sample(rest, 500);
            },
            `${bench("popover")} .armada-picker`,
        );
        await sleep(30);
        await ctx.page.mouse.click(control.x, control.y);
        const samples = (await sampling).filter((sample) => sample.v.shown);
        ctx.assert(
            "surface.popover.frames-scaled",
            samples.length > 3
                ? samples.filter((sample) => Math.abs(sample.v.scale - 1) > 0.001).length
                : null,
            "==",
            0,
        );
        ctx.assert(
            "surface.popover.rise-px",
            samples.length > 3 ? Math.max(...samples.map((sample) => Math.abs(sample.v.y))) : null,
            "<=",
            4,
            {unit: " px"},
        );
    });
    await ctx.scenario("ring", async () => {
        await ring(ctx);
    });
    await ctx.scenario("animated", async () => {
        // A tour of the bench's verbs: what animates, how, for how long.
        const seen = [];
        for (const id of [
            "figure",
            "verb",
            "cross",
            "react",
            "grow",
            "send",
            "refuse",
            "leave",
            "say",
        ]) {
            const points = await ctx.page.evaluate(
                (query) =>
                    [
                        ...document.querySelectorAll(
                            `${query} :is(.armada-text-control, .armada-send, .armada-control)`,
                        ),
                    ].map((element) => {
                        element.scrollIntoView({block: "center"});
                        const r = element.getBoundingClientRect();
                        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
                    }),
                bench(id),
            );
            for (const point of points.slice(0, 3)) {
                seen.push(
                    ...(await watching(
                        ctx,
                        async () => {
                            await ctx.page.mouse.click(point.x, point.y);
                        },
                        500,
                    )),
                );
            }
        }
        const allowed = new Set(["transform", "opacity", "color", "backgroundColor"]);
        const once = seen.filter((animation) => !animation.loops);
        ctx.assert("animated.animations-seen", once.length, ">=", 20);
        const other = once.flatMap((animation) =>
            animation.properties.filter((property) => !allowed.has(property)),
        );
        ctx.assert("animated.properties-other-than-transform-and-opacity", other.length, "==", 0, {
            note: [...new Set(other)].join(", "),
        });
        ctx.assert(
            "animated.longer-than-440ms",
            once.filter((animation) => animation.duration > 440).length,
            "==",
            0,
            {
                note: `longest ${Math.max(...once.map((animation) => animation.duration))} ms`,
            },
        );
        ctx.assert(
            "animated.beziers",
            once.filter((animation) => /cubic-bezier|ease/.test(animation.easing)).length,
            "==",
            0,
        );
        const loops = await ctx.page.evaluate(
            () =>
                new Set(
                    document
                        .getAnimations()
                        .filter(
                            (animation) =>
                                animation.effect.getComputedTiming().iterations === Infinity,
                        )
                        .map((animation) => animation.animationName),
                ).size,
        );
        ctx.assert("animated.kinds-of-loop", loops, "<=", 2, {
            note: "the working mark, and a typing person's dots",
        });
    });
    await ctx.scenario("reduced", async () => {
        await reduced(ctx);
    });
}
