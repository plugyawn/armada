// 9. A cold load into a topic.

/* global window, document */

import {max, median, round, sum} from "../lib/stats.mjs";

export const name = "startup";
export const order = 90;
export const description =
    "cold load into a topic: time to splash gone and to first row, longest task, layout shift, late fonts, bytes fetched before the first row";

const LOADS = 3;
// A font first requested this long after the first paint was discovered by
// laying out text that needs it, so that text is painted twice.
const LATE_FONT_MS = 250;

async function load(ctx, hash) {
    const requests = new Map();
    const on_request = (event) => {
        requests.set(event.requestId, {
            url: event.request.url.replace(ctx.base, ""),
            type: event.type,
            wall: event.wallTime * 1000,
            wire: 0,
            decoded: 0,
        });
    };
    const on_data = (event) => {
        const request = requests.get(event.requestId);
        if (request) {
            request.decoded += event.dataLength;
        }
    };
    const on_finished = (event) => {
        const request = requests.get(event.requestId);
        if (request) {
            request.wire = event.encodedDataLength;
        }
    };
    await ctx.page.goto("about:blank");
    ctx.client.on("Network.requestWillBeSent", on_request);
    ctx.client.on("Network.dataReceived", on_data);
    ctx.client.on("Network.loadingFinished", on_finished);
    await ctx.page.goto(`${ctx.base}/${hash}`, {waitUntil: "load"});
    await ctx.wait_for_rows();
    await ctx.sleep(3500);
    ctx.client.off("Network.requestWillBeSent", on_request);
    ctx.client.off("Network.dataReceived", on_data);
    ctx.client.off("Network.loadingFinished", on_finished);

    const page = await ctx.page.evaluate(() => {
        const paints = Object.fromEntries(
            performance.getEntriesByType("paint").map((entry) => [entry.name, entry.startTime]),
        );
        return {
            origin: performance.timeOrigin,
            marks: window.__ac.marks,
            first_paint: paints["first-paint"] ?? null,
            long_tasks: window.__ac.long_tasks.map((task) => task.dur),
            shifts: window.__ac.shifts.filter((shift) => !shift.input),
            preloaded_fonts: [...document.querySelectorAll('link[rel="preload"][as="font"]')].map(
                (link) => link.href,
            ),
        };
    });
    const timed = [...requests.values()].map((request) => ({
        ...request,
        t: request.wall - page.origin,
    }));
    const before_row = timed.filter((request) => request.t <= page.marks.first_row);
    // Requests issued before the first paint (today: the loading splash).
    const before_paint = timed.filter((request) => request.t <= page.first_paint);
    const bytes = (list, type) =>
        sum(list.filter((request) => request.type === type).map((request) => request.decoded));
    const fonts = timed.filter((request) => request.type === "Font");
    const preloaded = fonts.filter((font) =>
        page.preloaded_fonts.some((href) => href.endsWith(font.url)),
    );
    const short_name = (font) => font.url.replace(/\?.*$/, "").split("/").at(-1);
    return {
        splash_gone: page.marks.splash_gone ?? null,
        first_row: page.marks.first_row ?? null,
        first_paint: page.first_paint,
        longest_task: max(page.long_tasks) ?? 0,
        long_task_total: sum(page.long_tasks),
        layout_shift: sum(page.shifts.map((shift) => shift.value)),
        late_fonts: fonts.filter((font) => font.t > page.first_paint + LATE_FONT_MS).length,
        fonts: fonts.length,
        preloaded_fonts: page.preloaded_fonts.length,
        // A preload that no request answers would be a tag for a file
        // the page never uses, or one that failed.
        preloaded_fetched: preloaded.length,
        preloaded_bytes: sum(preloaded.map((font) => font.decoded)),
        preloaded_names: preloaded.map((font) => short_name(font)),
        fonts_after_paint: fonts
            .filter((font) => font.t > page.first_paint)
            .map((font) => short_name(font)),
        requests_before_row: before_row.length,
        api_before_row: before_row.filter((request) => /^\/(json|api)\//.test(request.url)).length,
        script_bytes: bytes(before_row, "Script"),
        style_bytes: bytes(before_row, "Stylesheet"),
        image_bytes: bytes(before_row, "Image"),
        font_bytes: bytes(timed, "Font"),
        scripts: before_row.filter((request) => request.type === "Script").length,
        paint_script_bytes: bytes(before_paint, "Script"),
        paint_style_bytes: bytes(before_paint, "Stylesheet"),
        paint_image_bytes: bytes(before_paint, "Image"),
        requests_before_paint: before_paint.length,
        largest_image: timed
            .filter((request) => request.type === "Image")
            .toSorted((a, b) => b.decoded - a.decoded)[0],
    };
}

export async function run(ctx) {
    // Cold: nothing comes from the HTTP cache.
    await ctx.client.send("Network.setCacheDisabled", {cacheDisabled: true});
    const loads = [];
    for (let index = 0; index < LOADS; index += 1) {
        loads.push(await load(ctx, ctx.topics.long));
    }
    const all = (key) => loads.map((sample) => sample[key]);
    const rounded = (key) =>
        all(key)
            .map((value) => Math.round(value))
            .join(", ");
    const kilobytes = (value) => round(value / 1024, 0);

    // The 800 ms budget is for a production build; this development build
    // lands on either side of it from run to run. Tripwire at three times
    // the budget; the count-based assertions below carry the weight.
    ctx.assert("first-row-ms.median", median(all("first_row")), "<=", 2400, {
        unit: "ms",
        note: `usable topic; per load: ${rounded("first_row")}; tripwire at 3x the 800 ms budget`,
    });
    ctx.assert("longest-task-ms.median", median(all("longest_task")), "<=", 100, {
        unit: "ms",
        note: `per load: ${rounded("longest_task")}; all long tasks: ${rounded("long_task_total")} ms`,
    });
    ctx.assert("cumulative-layout-shift.median", median(all("layout_shift")), "<=", 0.01, {
        note: `per load: ${all("layout_shift")
            .map((value) => round(value, 3))
            .join(", ")}`,
    });
    ctx.assert("late-font-requests", max(all("late_fonts")), "<=", 0, {
        note: `font files first requested more than ${LATE_FONT_MS} ms after first paint, of ${loads[0].fonts}; ${loads[0].preloaded_fonts} preloaded`,
    });
    // Every face a first paint uses is preloaded: at most five files and
    // 260 kB, and no face is first asked for once something is on screen.
    const preloaded_fetched = loads[0].preloaded_fetched;
    if (loads[0].preloaded_fonts === 0 || preloaded_fetched !== loads[0].preloaded_fonts) {
        const why = `${loads[0].preloaded_fonts} preload tags, ${preloaded_fetched} of them fetched as fonts`;
        ctx.assert("fonts.preloaded-files", null, "<=", 5, {note: `not measurable: ${why}`});
        ctx.assert("fonts.preloaded-kilobytes", null, "<=", 260, {
            unit: "kB",
            note: `not measurable: ${why}`,
        });
    } else {
        ctx.assert("fonts.preloaded-files", max(all("preloaded_fonts")), "<=", 5, {
            note: loads[0].preloaded_names.join(", "),
        });
        ctx.assert("fonts.preloaded-kilobytes", kilobytes(max(all("preloaded_bytes"))), "<=", 260, {
            unit: "kB",
        });
    }
    ctx.assert(
        "fonts.requests-after-first-paint",
        max(loads.map((sample) => sample.fonts_after_paint.length)),
        "<=",
        0,
        {
            note: `of ${loads[0].fonts} font files; after first paint: ${[...new Set(loads.flatMap((sample) => sample.fonts_after_paint))].join(", ") || "none"}`,
        },
    );
    ctx.assert(
        "image-kilobytes-before-first-row",
        kilobytes(median(all("image_bytes"))),
        "<=",
        500,
        {
            unit: "kB",
            note: loads[0].largest_image
                ? `largest: ${loads[0].largest_image.url.replace(/\?.*$/, "").slice(-50)} ${kilobytes(loads[0].largest_image.decoded)} kB`
                : "",
        },
    );
    ctx.info("splash-gone-ms.median", median(all("splash_gone")), {
        unit: "ms",
        note: `per load: ${rounded("splash_gone")}`,
    });
    ctx.info("first-paint-ms.median", median(all("first_paint")), {unit: "ms"});
    ctx.info("requests-before-first-row", median(all("requests_before_row")), {
        note: `${median(all("api_before_row"))} of them to /json; ${median(all("scripts"))} scripts`,
    });
    ctx.info("script-kilobytes-before-first-row", kilobytes(median(all("script_bytes"))), {
        unit: "kB",
        note: "decoded size of the unminified development build",
    });
    ctx.info("style-kilobytes-before-first-row", kilobytes(median(all("style_bytes"))), {
        unit: "kB",
    });
    ctx.info("font-kilobytes", kilobytes(median(all("font_bytes"))), {unit: "kB"});
    ctx.info("requests-before-first-paint", median(all("requests_before_paint")), {
        note: "first paint is the loading splash today",
    });
    ctx.info("script-kilobytes-before-first-paint", kilobytes(median(all("paint_script_bytes"))), {
        unit: "kB",
    });
    ctx.info("style-kilobytes-before-first-paint", kilobytes(median(all("paint_style_bytes"))), {
        unit: "kB",
    });
    ctx.info("image-kilobytes-before-first-paint", kilobytes(median(all("paint_image_bytes"))), {
        unit: "kB",
    });
}
