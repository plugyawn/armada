// 8. Going back to a topic that was on screen a moment ago.

/* global window, document, MutationObserver */

import {is_api_request} from "../lib/harness.mjs";
import {max, median} from "../lib/stats.mjs";

export const name = "topic-switch-cost";
export const order = 80;
export const description =
    "switching between two visited topics and back: time until the topic is shown, message rows rendered again, requests";

const ROUND_TRIPS = 5;

// Sets the hash and resolves when the target topic is what the page shows:
// the topic bar names it and message rows are visible. The condition is
// polled from macrotasks, so the time is that of the work, not of the next
// frame.
async function switch_to(ctx, hash) {
    const topic = decodeURIComponent(hash.split("/").at(-1));
    const request_mark = ctx.requests.length;
    const result = await ctx.page.evaluate(
        async (target, topic_name) =>
            await new Promise((resolve) => {
                const ac = window.__ac;
                const row_selector = ac.selectors.messageRow.join(", ");
                let rows_rendered = 0;
                let records = 0;
                const observer = new MutationObserver((list) => {
                    records += list.length;
                    for (const record of list) {
                        for (const node of record.addedNodes) {
                            if (node.nodeType !== 1) {
                                continue;
                            }
                            rows_rendered += node.matches(row_selector)
                                ? 1
                                : node.querySelectorAll(row_selector).length;
                        }
                    }
                });
                observer.observe(document.body, {subtree: true, childList: true});
                const long_tasks = ac.long_tasks.length;
                const start = performance.now();
                const channel = new MessageChannel();
                const shown = () =>
                    window.location.hash.includes(target.split("/").at(-1)) &&
                    (ac.last("topicBar")?.textContent.includes(topic_name) ?? false) &&
                    ac.all("messageRow").length > 0;
                channel.port1.addEventListener("message", () => {
                    const elapsed = performance.now() - start;
                    if (shown() || elapsed > 5000) {
                        const done = shown();
                        // Rows rendered after the topic is shown still count.
                        window.setTimeout(() => {
                            observer.disconnect();
                            resolve({
                                ms: done ? elapsed : null,
                                rows_rendered,
                                records,
                                rows: ac.all("messageRow").length,
                                long_tasks: ac.long_tasks.slice(long_tasks).map((task) => task.dur),
                            });
                        }, 600);
                        return;
                    }
                    channel.port2.postMessage(0);
                });
                channel.port1.start();
                window.location.hash = target;
                channel.port2.postMessage(0);
            }),
        hash,
        topic,
    );
    await ctx.sleep(300);
    result.requests = ctx
        .requests_since(request_mark)
        .filter((request) => is_api_request(request))
        .map((request) => `${request.method} ${request.url.replace(/\?.*$/, "")}`);
    return result;
}

function report(ctx, label, samples) {
    const all = (key) => samples.map((sample) => sample[key]);
    const urls = {};
    for (const sample of samples) {
        for (const url of sample.requests) {
            urls[url] = (urls[url] ?? 0) + 1;
        }
    }
    ctx.assert(`${label}.revisit-ms.median`, median(all("ms")), "<=", 16, {
        unit: "ms",
        note: `per switch: ${all("ms")
            .map((value) => (value === null ? "timeout" : Math.round(value)))
            .join(", ")}; ${samples[0].rows} rows`,
    });
    ctx.assert(`${label}.rows-rendered-again`, max(all("rows_rendered")), "<=", 0, {
        note: `message rows inserted into the DOM on a revisit; per switch: ${all("rows_rendered").join(", ")}`,
    });
    ctx.assert(
        `${label}.requests-per-switch`,
        max(samples.map((sample) => sample.requests.length)),
        "<=",
        2,
        {
            note: Object.entries(urls)
                .map(([url, count]) => `${count}x ${url}`)
                .join("; "),
        },
    );
    ctx.info(`${label}.dom-mutation-records.median`, median(all("records")));
    ctx.info(
        `${label}.long-tasks`,
        samples.filter((sample) => sample.long_tasks.length > 0).length,
        {note: `switches with a long task, of ${samples.length}`},
    );
}

export async function run(ctx) {
    await ctx.open(ctx.topics.long);
    await ctx.navigate(ctx.topics.short);
    await ctx.navigate(ctx.topics.long);
    await ctx.navigate(ctx.topics.short);
    await ctx.park_pointer();

    await ctx.scenario("revisit", async () => {
        const to_long = [];
        const to_short = [];
        for (let index = 0; index < ROUND_TRIPS; index += 1) {
            to_long.push(await switch_to(ctx, ctx.topics.long));
            await ctx.settle(300, 3000);
            to_short.push(await switch_to(ctx, ctx.topics.short));
            await ctx.settle(300, 3000);
        }
        report(ctx, "revisit-long", to_long);
        report(ctx, "revisit-short", to_short);
    });
}
