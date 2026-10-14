// Chromium tracing: counts forced synchronous layouts, i.e. Layout or style
// recalculation events that run *inside* a script-driven slice (an event
// handler, a timer) instead of in the rendering step of the frame.

const WORK = new Set([
    "Layout",
    "UpdateLayoutTree",
    "RecalculateStyles",
    "TimerFire",
    "FireAnimationFrame",
    "FunctionCall",
    "EventDispatch",
    "RunTask",
]);

export async function start_trace(page) {
    await page.tracing.start({
        categories: ["devtools.timeline", "disabled-by-default-devtools.timeline"],
    });
}

export async function stop_trace(page) {
    const buffer = await page.tracing.stop();
    const parsed = JSON.parse(Buffer.from(buffer).toString("utf8"));
    const events = (parsed.traceEvents ?? parsed).filter(
        (event) => event.ph === "X" && WORK.has(event.name),
    );
    // Keep the renderer main thread: the thread that dispatched DOM events,
    // or failing that the one with the most layout work.
    const score = new Map();
    for (const event of events) {
        const key = `${event.pid}:${event.tid}`;
        const weight = event.name === "EventDispatch" ? 1000 : event.name === "Layout" ? 1 : 0;
        score.set(key, (score.get(key) ?? 0) + weight);
    }
    let main;
    let best = -1;
    for (const [key, value] of score) {
        if (value > best) {
            best = value;
            main = key;
        }
    }
    return events
        .filter((event) => `${event.pid}:${event.tid}` === main)
        .toSorted((a, b) => a.ts - b.ts);
}

function contained(parent, child) {
    return child.ts >= parent.ts && child.ts + (child.dur ?? 0) <= parent.ts + (parent.dur ?? 0);
}

// For every slice accepted by `is_parent`, the layouts and style recalcs
// nested inside it. `label` names the slice in the result.
export function nested_work(events, is_parent, label = (event) => event.name) {
    const layouts = events.filter((event) => event.name === "Layout");
    const styles = events.filter(
        (event) => event.name === "UpdateLayoutTree" || event.name === "RecalculateStyles",
    );
    const result = [];
    for (const parent of events) {
        if (!is_parent(parent)) {
            continue;
        }
        result.push({
            label: label(parent),
            ts: parent.ts,
            dur: (parent.dur ?? 0) / 1000,
            layouts: layouts.filter((event) => contained(parent, event)).length,
            styles: styles.filter((event) => contained(parent, event)).length,
        });
    }
    return result;
}

// DOM event dispatches of the given types (keydown, input, keyup, click...).
export function dispatches(events, types) {
    return nested_work(
        events,
        (event) => event.name === "EventDispatch" && types.includes(event.args?.data?.type),
        (event) => event.args.data.type,
    );
}

export function timers(events) {
    return nested_work(events, (event) => event.name === "TimerFire");
}

export function count(events, name) {
    return events.filter((event) => event.name === name).length;
}
