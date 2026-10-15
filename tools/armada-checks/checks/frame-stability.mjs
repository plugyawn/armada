// 1. The application frame does not move when the middle column changes
// what it shows.

/* global window, document, Element, Node, NodeFilter, MutationObserver, requestAnimationFrame, getComputedStyle */

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {VIEWPORT} from "../lib/browser.mjs";
import {describe_rect, drift, max} from "../lib/stats.mjs";
import {dispatches, start_trace, stop_trace} from "../lib/trace.mjs";

export const name = "frame-stability";
export const order = 10;
export const description =
    "chrome stays put across Chat -> README -> Files -> Chat; scroll is restored; no pane is left behind; the frame's rectangles are the design's at 1100, 1440 and 1920 on every place";

const CHROME = ["navbar", "leftSidebar", "rightSidebar", "topicBar", "tabStrip", "compose"];
const PX = {unit: "px"};

async function anchor_row(ctx) {
    return await ctx.page.evaluate(() => {
        const rows = window.__ac.all("messageRow");
        const middle = window.innerHeight / 2;
        let best = null;
        for (const row of rows) {
            const box = row.getBoundingClientRect();
            if (best === null || Math.abs(box.y - middle) < Math.abs(best.y - middle)) {
                best = {id: row.dataset.messageId ?? row.id, y: Math.round(box.y * 100) / 100};
            }
        }
        return best;
    });
}

async function row_top(ctx, id) {
    return await ctx.page.evaluate((wanted) => {
        const row = window.__ac
            .all("messageRow")
            .find((candidate) => (candidate.dataset.messageId ?? candidate.id) === wanted);
        return row ? Math.round(row.getBoundingClientRect().y * 100) / 100 : null;
    }, id);
}

async function scroll_feed_to(ctx, {fraction, pixels}) {
    await ctx.page.evaluate(
        (share, exact) => {
            const scroller = window.__ac.scroller();
            const top_limit = scroller.scrollHeight - scroller.clientHeight;
            scroller.scrollTop = exact ?? Math.round(share * top_limit);
        },
        fraction ?? null,
        pixels ?? null,
    );
    await ctx.settle(500, 4000);
}

async function tab_round_trip(ctx, label, hash, scroll_fraction) {
    await ctx.open(hash);
    if (scroll_fraction !== undefined) {
        await scroll_feed_to(ctx, {fraction: scroll_fraction});
    }
    await ctx.park_pointer();
    const before = await ctx.scroll_state();
    const anchor = await anchor_row(ctx);

    await ctx.start_recording(CHROME);
    for (const tab of ["tabReadme", "tabFiles", "tabChat"]) {
        await ctx.click(tab);
        await ctx.park_pointer();
        await ctx.settle(500, 4000);
        await ctx.sleep(300);
    }
    const frames = await ctx.stop_recording();
    const after = await ctx.scroll_state();

    for (const element of CHROME) {
        const moved = drift(frames, element);
        ctx.assert(`${label}.${element}.drift`, moved?.delta ?? null, "<=", 0.5, {
            ...PX,
            note: moved
                ? `${describe_rect(moved.first)}${moved.worst ? ` -> ${describe_rect(moved.worst)}` : ""}`
                : "",
        });
    }
    for (const element of ["topicBar", "tabStrip"]) {
        ctx.assert(
            `${label}.${element}.frames-missing`,
            drift(frames, element)?.vanished ?? null,
            "==",
            0,
            {note: `of ${frames.length} frames`},
        );
    }
    const doc_y = frames.map((frame) => Math.abs(frame.doc_y - frames[0].doc_y));
    ctx.assert(`${label}.document-scroll.drift`, max(doc_y), "<=", 0.5, {
        ...PX,
        note: `document scrollY started at ${frames[0].doc_y}`,
    });
    const doc_height = frames.map((frame) => Math.abs(frame.doc_height - frames[0].doc_height));
    ctx.assert(`${label}.document-height.drift`, max(doc_height), "<=", 0.5, {
        ...PX,
        note: `document height started at ${frames[0].doc_height}`,
    });
    ctx.assert(`${label}.feed-scroll.restored`, Math.abs(after.top - before.top), "<=", 0.5, {
        ...PX,
        note: `scrollTop ${before.top} -> ${after.top} of ${before.max}`,
    });
    const anchor_after = anchor ? await row_top(ctx, anchor.id) : null;
    ctx.assert(
        `${label}.anchor-row.restored`,
        anchor && anchor_after !== null ? Math.abs(anchor_after - anchor.y) : null,
        "<=",
        0.5,
        {...PX, note: anchor ? `row y ${anchor.y} -> ${anchor_after}` : ""},
    );
}

// Chat scrolled to within a pixel of the top is still "scrolled": a round
// trip through a pane must not send it to the bottom.
async function near_top_round_trip(ctx) {
    await ctx.open(ctx.topics.long);
    await scroll_feed_to(ctx, {pixels: 1});
    await ctx.park_pointer();
    const before = await ctx.scroll_state();
    await ctx.click("tabReadme");
    await ctx.settle(500, 4000);
    await ctx.click("tabChat");
    await ctx.settle(500, 4000);
    await ctx.sleep(300);
    const after = await ctx.scroll_state();
    ctx.assert("long-near-top.feed-scroll.restored", Math.abs(after.top - before.top), "<=", 0.5, {
        ...PX,
        note: `scrollTop ${before.top} -> ${after.top} of ${before.max}`,
    });
}

// Leaving a topic whose Files tab is open must leave nothing of it behind.
async function no_ghost_pane(ctx) {
    await ctx.open(ctx.topics.short);
    for (const [view, selector] of [
        ["inbox", "inboxView"],
        ["recent", "recentView"],
    ]) {
        await ctx.navigate(ctx.topics[view]);
        const clean = await ctx.rects([selector]);
        const clean_height = (await ctx.scroll_state()).doc_height;

        await ctx.navigate(ctx.topics.short);
        await ctx.click("tabFiles");
        await ctx.settle(500, 4000);
        await ctx.navigate(ctx.topics[view]);
        await ctx.sleep(300);
        // Since F5 the header stands on every place; what must not be left
        // behind is the worktree's own part of it, its views.
        const dirty = await ctx.rects([selector, "workspacePane", "tabStrip"]);
        const dirty_height = (await ctx.scroll_state()).doc_height;

        ctx.assert(
            `files-to-${view}.workspace-pane.visible`,
            dirty.workspacePane !== null,
            "==",
            0,
            {
                note: describe_rect(dirty.workspacePane),
            },
        );
        ctx.assert(`files-to-${view}.topic-bar.visible`, dirty.tabStrip !== null, "==", 0, {
            note: describe_rect(dirty.tabStrip),
        });
        ctx.assert(
            `files-to-${view}.view.offset`,
            clean[selector] && dirty[selector]
                ? Math.max(
                      Math.abs(clean[selector].x - dirty[selector].x),
                      Math.abs(clean[selector].y - dirty[selector].y),
                  )
                : null,
            "<=",
            0.5,
            {
                ...PX,
                note: `${describe_rect(clean[selector])} -> ${describe_rect(dirty[selector])}`,
            },
        );
        ctx.assert(
            `files-to-${view}.document-height.delta`,
            Math.abs(dirty_height - clean_height),
            "<=",
            0.5,
            {...PX, note: `${clean_height} -> ${dirty_height}`},
        );

        // Put the topic back on Chat for the next view.
        await ctx.navigate(ctx.topics.short);
        await ctx.click("tabChat");
        await ctx.settle(500, 4000);
    }
}

// ---- The frame (F5): the design's rectangles, on every place --------------

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SPRITE = path.resolve(HERE, "../../../web/src/armada/kit/icons/sprite.svg");

// SPEC-DESIGN appendix B, in CSS px (left, top, width, height). The column
// is absent under 1408: its host is then the folded heading, 296 wide at
// the lane's right edge, More 4 px before it.
export const DESIGN = {
    1440: {
        height: 900,
        nav: [0, 0, 248, 900],
        header: [248, 0, 1192, 88],
        glyph: 336,
        more: [996, 52, 28, 28],
        column: [1064, 52, 320, 828],
        heading: [1064, 52, 320, 28],
        lane: [320, 704],
        band: [248, 800, 1192, 36],
        track: 836,
    },
    1100: {
        height: 800,
        nav: [0, 0, 248, 800],
        header: [248, 0, 852, 88],
        glyph: 338,
        more: [698, 52, 28, 28],
        column: [730, 52, 296, 28],
        heading: [730, 52, 296, 28],
        lane: [322, 704],
        band: [248, 700, 852, 36],
        track: 736,
    },
    1920: {
        height: 1080,
        nav: [0, 0, 248, 1080],
        header: [248, 0, 1672, 88],
        glyph: 432,
        more: [1228, 52, 28, 28],
        column: [1311, 52, 440, 1008],
        heading: [1311, 52, 440, 28],
        lane: [416, 840],
        band: [248, 980, 1672, 36],
        track: 1016,
    },
};

// The old composer opens by itself in a direct message, with a recipient
// row and a toolbar that the field does not have (B3 replaces it); its
// height there is the composer's, not the frame's. The frame is measured
// with it at rest, as the field rests: closed, one line.
export async function rest_composer(ctx, label) {
    const open = await ctx.exists("composeTextarea");
    if (open) {
        await ctx.click("composeTextarea");
        await ctx.page.keyboard.press("Escape");
        await ctx.settle(300, 3000);
    }
    if (label !== undefined) {
        ctx.info(`${label}.old-composer-was-open`, open ? 1 : 0, {
            note: "closed before measuring (B3)",
        });
    }
}

export function places(ctx) {
    return [
        ["worktree", ctx.topics.live],
        ["channel", ctx.topics.plainChannel],
        ["dm", ctx.topics.dm],
        ["home", ctx.topics.inbox],
    ];
}

// Every rectangle of the frame, and the first glyph of each header line:
// the left of the first text or icon inside it.
export async function frame_rects(ctx) {
    return await ctx.page.evaluate(() => {
        const ac = window.__ac;
        const box = (name) => {
            const rect = ac.rect(name);
            return rect ? [rect.x, rect.y, rect.w, rect.h] : null;
        };
        const glyph = (name) => {
            const line = ac.first(name);
            if (!line) {
                return null;
            }
            const walker = document.createTreeWalker(line, NodeFilter.SHOW_ALL);
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                let rect;
                if (node.nodeType === Node.TEXT_NODE && node.textContent.trim() !== "") {
                    const range = document.createRange();
                    range.selectNodeContents(node);
                    rect = range.getBoundingClientRect();
                } else if (node instanceof Element && /^(I|SVG|IMG)$/i.test(node.tagName)) {
                    rect = node.getBoundingClientRect();
                }
                const visible =
                    rect &&
                    rect.width > 0 &&
                    (node instanceof Element ? node : node.parentElement).checkVisibility({
                        visibilityProperty: true,
                    });
                if (visible) {
                    return [Math.round(rect.x * 100) / 100, Math.round(rect.y * 100) / 100];
                }
            }
            return null;
        };
        const lane = box("lane") ?? box("inboxPane");
        return {
            place: document.querySelector("[data-armada=plane]")?.dataset.place ?? null,
            nav: box("nav"),
            header: box("header"),
            line1: glyph("headerLine1"),
            // A worktree's views stand on line 2 (drawn from line 1's
            // markup until B2); elsewhere line 2's own sentence.
            line2: ac.first("headerViews") ? glyph("headerViews") : glyph("headerLine2"),
            more: box("more"),
            column: box("column"),
            heading: box("columnHeading"),
            lane,
            band: box("band"),
            track: box("composerTrack"),
            document_height: document.scrollingElement.scrollHeight,
            document_scroll: document.scrollingElement.scrollTop,
            viewport_height: window.innerHeight,
        };
    });
}

function off(measured, expected) {
    if (measured === null || measured === undefined) {
        return null;
    }
    return (
        Math.round(
            Math.max(...expected.map((value, index) => Math.abs(measured[index] - value))) * 100,
        ) / 100
    );
}

const show = (value) => (value === null || value === undefined ? "none" : value.join(" "));

export function assert_design(ctx, label, size, place, rects) {
    const design = DESIGN[size];
    const at = `${label}.${size}.${place}`;
    const check = (name, measured, expected) => {
        ctx.assert(`${at}.${name}.off-design`, off(measured, expected), "<=", 0.5, {
            ...PX,
            note: `${show(measured)} (design ${expected.join(" ")})`,
        });
    };
    check("nav", rects.nav, design.nav);
    check("header", rects.header, design.header);
    check("line1-glyph-x", rects.line1 && [rects.line1[0]], [design.glyph]);
    check("line2-glyph-x", rects.line2 && [rects.line2[0]], [design.glyph]);
    check("more", rects.more, design.more);
    check("column", rects.column, design.column);
    check("column-heading", rects.heading, design.heading);
    check("conversation", rects.lane && [rects.lane[0], rects.lane[2]], design.lane);
    if (place === "home") {
        // Home is not a conversation: no band, no field.
        ctx.assert(`${at}.band.shown`, rects.band === null ? 0 : 1, "==", 0, {
            note: show(rects.band),
        });
        ctx.assert(
            `${at}.composer-track.height`,
            rects.track === null ? 0 : rects.track[3],
            "<=",
            0.5,
            {
                ...PX,
                note: show(rects.track),
            },
        );
    } else {
        check("band", rects.band, design.band);
        check("composer-track-y", rects.track && [rects.track[1]], [design.track]);
    }
    ctx.assert(
        `${at}.document-scroll-height`,
        Math.abs(rects.document_height - rects.viewport_height),
        "<=",
        0,
        {...PX, note: `${rects.document_height} against a viewport of ${rects.viewport_height}`},
    );
}

async function composition(ctx) {
    for (const size of [1440, 1100, 1920]) {
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
            await rest_composer(ctx, `composition.${size}.${place}`);
            await ctx.park_pointer();
            await ctx.sleep(300);
            const rects = await frame_rects(ctx);
            ctx.assert(
                `composition.${size}.${place}.place`,
                rects.place === place ? 1 : 0,
                "==",
                1,
                {
                    note: `data-place ${rects.place}`,
                },
            );
            assert_design(ctx, "composition", size, place, rects);
        }
        // Files takes the composition and folds the column into its heading,
        // which stays where it was; More does not move.
        await ctx.navigate(ctx.topics.live);
        const chat = await frame_rects(ctx);
        await ctx.click("tabFiles");
        await ctx.settle(500, 4000);
        const files = await frame_rects(ctx);
        ctx.assert(
            `composition.${size}.files.column-heading.moved`,
            chat.heading && files.heading ? off(files.heading, chat.heading) : null,
            "<=",
            0.5,
            {...PX, note: `${show(chat.heading)} -> ${show(files.heading)}`},
        );
        ctx.assert(
            `composition.${size}.files.column.folded-height`,
            files.column?.[3] ?? null,
            "<=",
            28,
            {
                ...PX,
                note: show(files.column),
            },
        );
        ctx.assert(
            `composition.${size}.files.document-scroll-height`,
            Math.abs(files.document_height - files.viewport_height),
            "<=",
            0,
            {...PX},
        );
        await ctx.click("tabChat");
        await ctx.settle(500, 4000);
    }
}

// Dragging the window from 1100 to 1920: the composition follows in CSS.
// No script may write a width or one of the five properties; the old
// resize handlers that still run are counted, not gated.
async function resize_drag(ctx) {
    await ctx.page.setViewport({width: 1100, height: 900, deviceScaleFactor: 1});
    await ctx.open(ctx.topics.live);
    await ctx.page.evaluate(() => {
        window.__ac.style_writes = [];
        const names = /--(col|side-w|side-gap|comp|lx)\b|width/;
        new MutationObserver((records) => {
            for (const record of records) {
                const style = record.target.getAttribute("style") ?? "";
                if (names.test(style) && record.target.closest("[data-armada=frame]")) {
                    window.__ac.style_writes.push(
                        `${record.target.tagName}#${record.target.id}: ${style}`,
                    );
                }
            }
        }).observe(document.body, {attributes: true, attributeFilter: ["style"], subtree: true});
    });
    await start_trace(ctx.page);
    for (let width = 1100; width <= 1920; width += 40) {
        await ctx.page.setViewport({width, height: 900, deviceScaleFactor: 1});
    }
    await ctx.page.setViewport({width: 1920, height: 1080, deviceScaleFactor: 1});
    const lane = await ctx.page.evaluate(
        () =>
            new Promise((resolve) => {
                requestAnimationFrame(() => {
                    const rect = window.__ac.rect("lane");
                    resolve(rect ? [rect.x, rect.w] : null);
                });
            }),
    );
    const events = await stop_trace(ctx.page);
    const writes = await ctx.page.evaluate(() => window.__ac.style_writes);
    ctx.assert("resize.composition-written-by-script", writes.length, "==", 0, {
        note: writes.slice(0, 3).join("; "),
    });
    ctx.assert(
        "resize.conversation-in-the-first-frame.off-design",
        off(lane, DESIGN[1920].lane),
        "<=",
        0.5,
        {
            ...PX,
            note: `${show(lane)} in the frame after the last resize`,
        },
    );
    const handlers = dispatches(events, ["resize"]);
    ctx.info("resize.old-handlers-dispatched", handlers.length, {
        note: `resize events with script listeners (the old app's); ${handlers.reduce((sum, item) => sum + item.layouts, 0)} layouts forced inside them`,
    });
}

// A scenario that changes the window gives it back, even when it fails, so
// the next one is measured at the size it compares against.
async function with_viewport_restored(ctx, body) {
    try {
        await body();
    } finally {
        await ctx.page.setViewport(VIEWPORT);
    }
}

// The scroll edge: one line on the header's lower edge while content lies
// above the pane, from one attribute written only when it changes.
async function scroll_edge(ctx) {
    await ctx.open(ctx.topics.long);
    const result = await ctx.page.evaluate(async () => {
        const header = document.querySelector("[data-armada=header]");
        const scroller = window.__ac.scroller();
        const frame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
        let redundant = 0;
        new MutationObserver((records) => {
            // Each record's new state is the next one's old state.
            const states = [
                ...records.map((record) => record.oldValue !== null),
                header.hasAttribute("data-edge"),
            ];
            for (let index = 0; index < records.length; index += 1) {
                if (states[index] === states[index + 1]) {
                    redundant += 1;
                }
            }
        }).observe(header, {
            attributes: true,
            attributeFilter: ["data-edge"],
            attributeOldValue: true,
        });
        const states = [];
        for (const top of [0, 40, 80, 120, 0, 0, 200]) {
            scroller.scrollTop = top;
            await frame();
            await frame();
            states.push(header.hasAttribute("data-edge"));
        }
        const mask = getComputedStyle(scroller).maskImage;
        return {states, redundant, mask};
    });
    const expected = [false, true, true, true, false, false, true];
    ctx.assert(
        "scroll-edge.line-shown-with-content-above",
        result.states.filter((state, index) => state !== expected[index]).length,
        "==",
        0,
        {note: result.states.join(" ")},
    );
    ctx.assert("scroll-edge.writes-that-change-nothing", result.redundant, "==", 0, {
        note: "the attribute is written only when it changes",
    });
    ctx.assert("scroll-edge.no-mask", result.mask === "none" ? 0 : 1, "==", 0, {note: result.mask});
}

// Electron's drag regions, the inset for its window buttons, the inlined
// sprite, and a first paint that is the frame (scripts off).
async function first_paint(ctx) {
    await ctx.open(ctx.topics.inbox);
    const drag = await ctx.page.evaluate(() => {
        const region = (name) => {
            const element = window.__ac.first(name);
            return element
                ? getComputedStyle(element).getPropertyValue("-webkit-app-region")
                : null;
        };
        const root = document.documentElement;
        const inset = () => getComputedStyle(root).getPropertyValue("--frame-inset-start").trim();
        const browser = inset();
        root.toggleAttribute("data-electron", true);
        const electron = inset();
        root.toggleAttribute("data-electron", false);
        return {top: region("navTop"), line1: region("headerLine1"), browser, electron};
    });
    ctx.assert(
        "electron.drag-regions",
        [drag.top, drag.line1].filter((value) => value !== "drag").length,
        "==",
        0,
        {
            note: `nav top ${drag.top}, header line 1 ${drag.line1}`,
        },
    );
    ctx.assert("electron.inset-in-a-browser", drag.browser === "0px" ? 0 : 1, "==", 0, {
        note: drag.browser,
    });
    ctx.assert("electron.inset-in-electron", drag.electron === "72px" ? 0 : 1, "==", 0, {
        note: drag.electron,
    });

    const file = fs.readFileSync(SPRITE, "utf8").trim();
    // The page serialises what it parsed as HTML; the file is XML. Both are
    // brought to one spelling (no self-closing tags, no namespace, one space)
    // and compared symbol by symbol.
    const inlined = await ctx.page.evaluate(
        () => document.querySelector("body > svg")?.innerHTML ?? "",
    );
    const symbols_of = (text) =>
        (
            text
                .replaceAll(/<(\w+)([^<>]*?)\s*\/>/g, "<$1$2></$1>")
                .replaceAll(/ xmlns="[^"]*"/g, "")
                .replaceAll(/\s+/g, " ")
                .replaceAll("> <", "><")
                .match(/<symbol[\s\S]*?<\/symbol>/g) ?? []
        ).map((symbol) => symbol.trim());
    const page_symbols = symbols_of(inlined);
    const file_symbols = symbols_of(file);
    const sprite = {
        page: page_symbols.length,
        generated: file_symbols.length,
        differing: file_symbols.filter((symbol, index) => symbol !== page_symbols[index]).length,
        example: file_symbols
            .find((symbol, index) => symbol !== page_symbols[index])
            ?.slice(0, 120),
    };
    ctx.assert("first-paint.sprite-symbols", sprite.page, "==", sprite.generated, {
        note: "symbols inlined in the page against web/src/armada/kit/icons/sprite.svg",
    });
    ctx.assert("first-paint.sprite-symbols-differing", sprite.differing, "==", 0, {
        note: `templates/zerver/app/armada_sprite.svg must be a copy of the generated sprite${sprite.example ? `; first differing: ${sprite.example}` : ""}`,
    });

    // The document as the server sends it, before any script: the frame in
    // its final geometry, and no splash.
    await ctx.page.setJavaScriptEnabled(false);
    try {
        await ctx.page.goto(ctx.app, {waitUntil: "load"});
        const raw = await ctx.page.evaluate(() => {
            const box = (selector) => {
                const element = document.querySelector(selector);
                if (!element) {
                    return null;
                }
                const rect = element.getBoundingClientRect();
                return [rect.x, rect.y, rect.width, rect.height];
            };
            const splash = document.querySelector("#app-loading");
            return {
                header: box("[data-armada=header]"),
                nav: box("[data-armada=nav]"),
                band: box("[data-armada=band]"),
                splash: splash ? splash.getClientRects().length : 0,
                document_height: document.scrollingElement.scrollHeight,
                viewport_height: window.innerHeight,
            };
        });
        ctx.assert(
            "first-paint.header.off-design",
            off(raw.header, DESIGN[1440].header),
            "<=",
            0.5,
            {
                ...PX,
                note: show(raw.header),
            },
        );
        ctx.assert("first-paint.nav.off-design", off(raw.nav, DESIGN[1440].nav), "<=", 0.5, {
            ...PX,
            note: show(raw.nav),
        });
        ctx.assert("first-paint.splash-shown", raw.splash, "==", 0);
        ctx.assert(
            "first-paint.document-scroll-height",
            Math.abs(raw.document_height - raw.viewport_height),
            "<=",
            0,
            {...PX},
        );
    } finally {
        await ctx.page.setJavaScriptEnabled(true);
    }
}

export async function run(ctx) {
    await ctx.scenario("short", async () => {
        await tab_round_trip(ctx, "short", ctx.topics.short);
    });
    await ctx.scenario("long", async () => {
        await tab_round_trip(ctx, "long", ctx.topics.long, 0.5);
    });
    await ctx.scenario("long-near-top", async () => {
        await near_top_round_trip(ctx);
    });
    await ctx.scenario("ghost-pane", async () => {
        await no_ghost_pane(ctx);
    });
    await ctx.scenario("composition", async () => {
        await with_viewport_restored(ctx, async () => composition(ctx));
    });
    await ctx.scenario("resize", async () => {
        await with_viewport_restored(ctx, async () => resize_drag(ctx));
    });
    await ctx.scenario("scroll-edge", async () => {
        await scroll_edge(ctx);
    });
    await ctx.scenario("first-paint", async () => {
        await first_paint(ctx);
    });
}
