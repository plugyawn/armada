// 10. A tour of the interface produces no console errors and no uncaught
// exceptions.

/* global window, document */

export const name = "console-clean";
export const order = 100;
export const description =
    "scripted tour (composer buttons, navbar menus, Views menu, topic menu, runtime picker, work details, a thread): no console errors, no uncaught exceptions";

function new_tour() {
    return {driven: [], skipped: [], errors: []};
}

// One step of the tour. Errors that appear during it are attributed to it;
// a step that cannot be driven (its control is gone) is reported, because a
// tour that silently visits nothing proves nothing.
async function step(ctx, tour, label, hash, body) {
    const console_mark = ctx.console_errors.length;
    const page_mark = ctx.page_errors.length;
    try {
        const current = await ctx.page.evaluate(() => decodeURIComponent(window.location.hash));
        if (!current.startsWith(decodeURIComponent(hash))) {
            await ctx.navigate(hash);
        }
        await body();
        tour.driven.push(label);
    } catch (error) {
        await ctx.debug_shot(label);
        tour.skipped.push(
            `${label}: ${String(error?.message ?? error)
                .split("\n")[0]
                .slice(0, 120)}`,
        );
    }
    await ctx.sleep(250);
    await ctx.page.evaluate(() => {
        for (const element of window.__ac.all("devErrorOverlay")) {
            element.remove();
        }
    });
    for (const text of ctx.console_errors.slice(console_mark)) {
        tour.errors.push({label, kind: "console", text});
    }
    for (const text of ctx.page_errors.slice(page_mark)) {
        tour.errors.push({label, kind: "exception", text});
    }
}

async function visible_count(ctx) {
    return await ctx.page.evaluate(() => window.__ac.visible_count());
}

// Click a control, require that something appeared, then dismiss it.
async function opens(ctx, selector, {hover, which = "first"} = {}) {
    if (hover) {
        const point = await ctx.center(hover, which);
        if (point) {
            await ctx.page.mouse.move(point.x, point.y);
            await ctx.sleep(200);
        }
    }
    const before = await visible_count(ctx);
    await ctx.click(selector, {which});
    await ctx.sleep(900);
    const after = await visible_count(ctx);
    if (after - before < 3) {
        throw new Error(
            `clicking "${selector}" showed nothing new (${before} -> ${after} visible elements)`,
        );
    }
    await dismiss(ctx);
}

async function dismiss(ctx) {
    await ctx.page.keyboard.press("Escape");
    await ctx.sleep(400);
    await ctx.park_pointer();
}

function compose_controls() {
    const ac = window.__ac;
    const skipped = new Set(ac.all("composeControlsSkipped"));
    return ac.all("composeControls").filter((element) => !skipped.has(element));
}

async function compose_buttons(ctx, tour, hash) {
    await ctx.open_compose();
    await ctx.page.evaluate(`window.__ac.tour_controls = ${compose_controls.toString()}`);
    const labels = await ctx.page.evaluate(() =>
        window.__ac
            .tour_controls()
            .map(
                (element) =>
                    element.getAttribute("aria-label") ||
                    element.dataset.tippyContent ||
                    element.dataset.tooltipTemplateId ||
                    element.className.toString().split(" ").at(-1),
            ),
    );
    if (labels.length === 0) {
        tour.skipped.push("compose buttons: none found");
        return;
    }
    for (const [index, label] of labels.entries()) {
        await step(ctx, tour, `compose button "${label}"`, hash, async () => {
            await ctx.open_compose();
            const point = await ctx.page.evaluate((position) => {
                const element = window.__ac.tour_controls()[position];
                return element ? window.__ac.reachable_point(element) : null;
            }, index);
            if (!point) {
                throw new Error("button is missing or covered");
            }
            await ctx.page.mouse.click(point.x, point.y);
            await ctx.sleep(350);
            // Close whatever it opened (menu, picker, modal); the composer
            // itself is reopened by the next step if this closed it.
            await ctx.page.keyboard.press("Escape");
            await ctx.sleep(200);
            await ctx.park_pointer();
        });
    }
}

export async function run(ctx) {
    ctx.watch_responses();
    const tour = new_tour();
    const topic = ctx.topics.completed;
    await ctx.open(topic);
    // Errors from loading the application itself belong to the tour too.
    for (const text of ctx.console_errors) {
        tour.errors.push({label: "load", kind: "console", text});
    }
    for (const text of ctx.page_errors) {
        tour.errors.push({label: "load", kind: "exception", text});
    }

    await compose_buttons(ctx, tour, topic);
    await step(ctx, tour, "close compose", topic, async () => {
        await ctx.clear_compose();
        await ctx.page.keyboard.press("Escape");
        await ctx.sleep(300);
        await ctx.page.evaluate(() => {
            document.activeElement?.blur?.();
        });
    });
    for (const menu of ["helpMenu", "gearMenu", "personalMenu", "viewsMenu"]) {
        await step(ctx, tour, `menu ${menu}`, topic, async () => {
            await opens(ctx, menu);
        });
    }
    await step(ctx, tour, "topic menu", topic, async () => {
        await opens(ctx, "topicMenu", {hover: "topicRow"});
    });
    await step(ctx, tour, "runtime picker", topic, async () => {
        await opens(ctx, "runtimePicker");
    });
    await step(ctx, tour, "work details", topic, async () => {
        await opens(ctx, "workDetails");
    });
    await step(ctx, tour, "message actions menu", topic, async () => {
        await opens(ctx, "messageActions", {hover: "messageRow", which: "last"});
    });
    await step(ctx, tour, "thread", ctx.topics.threads, async () => {
        await opens(ctx, "threadLaunch");
    });
    for (const tab of ["tabReadme", "tabFiles", "tabChat"]) {
        await step(ctx, tour, `tab ${tab}`, topic, async () => {
            await ctx.click(tab);
            await ctx.settle(400, 4000);
        });
    }
    for (const view of ["inbox", "recent"]) {
        await step(ctx, tour, `view ${view}`, ctx.topics[view], async () => {
            await ctx.settle(400, 4000);
        });
    }

    const distinct = (kind) => [
        ...new Set(
            tour.errors
                .filter((error) => error.kind === kind)
                .map((error) => `[${error.label}] ${error.text.split("\n")[0].slice(0, 160)}`),
        ),
    ];
    const total = tour.driven.length + tour.skipped.length;
    ctx.assert("tour.steps-not-driven", tour.skipped.length, "<=", 0, {
        note: tour.skipped.length > 0 ? tour.skipped.join(" | ") : `${total} steps driven`,
    });
    ctx.assert(
        "console-errors",
        tour.errors.filter((error) => error.kind === "console").length,
        "<=",
        0,
        {
            note: distinct("console").slice(0, 4).join(" | "),
        },
    );
    ctx.assert(
        "uncaught-exceptions",
        tour.errors.filter((error) => error.kind === "exception").length,
        "<=",
        0,
        {note: distinct("exception").slice(0, 4).join(" | ")},
    );
    ctx.assert("failed-responses", ctx.failed_responses.length, "<=", 0, {
        note: [...new Set(ctx.failed_responses)].slice(0, 4).join(" | "),
    });
}
