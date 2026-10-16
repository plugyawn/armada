// The kit, on the showroom (/devtools/armada/): the field's one rectangle
// in every state, the cells inside it, hit areas and names, the two idioms
// drawing alike, the sprite, and the showroom against its accepted
// pictures.
//
// The pictures are tools/armada-checks/baseline/kit/<section>-<theme>-<n>x.png.
// They were first accepted beside the design's component sheet
// (var/armada-overhaul/build/F4.md). After a change that is meant to move
// pixels, look at the showroom beside the design again and accept anew:
//
//   ARMADA_KIT_ACCEPT=1 arm check kit

/* global document, window, Image */

import * as fs from "node:fs";
import path from "node:path";

import {sleep} from "../lib/browser.mjs";
import {keep_targets} from "../lib/host.mjs";
import {SECTIONS, open_showroom, percent_differing} from "../lib/showroom.mjs";

export const name = "kit";
export const order = 20;
export const description =
    "showroom: the field's rectangle in every state, hit areas and names, Preact and DOM alike, the sprite, and every section against its accepted picture";

const BASELINE = "tools/armada-checks/baseline/kit";
// Pictures people keep are written to the host checkout too (lib/host.mjs);
// the VM's copy is overwritten from it.
const baseline_dirs = () => keep_targets(BASELINE).toReversed();
const LIGHTER_DIAGONALS = new Set(["clip", "link", "edit", "expand"]);

async function field_geometry(ctx) {
    const fields = await ctx.page.evaluate(() =>
        [...document.querySelectorAll('[data-armada="showroom-composer"] .armada-showroom-dock')]
            .slice(0, 12)
            .map((dock) => {
                const box = (element) => {
                    if (!element) {
                        return null;
                    }
                    const r = element.getBoundingClientRect();
                    return {x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom};
                };
                const shape = dock.querySelector(".armada-field-shape");
                return {
                    title: dock.parentElement.querySelector(".armada-type-strong").textContent,
                    dock: box(dock),
                    shape: box(shape),
                    send: box(shape.querySelector(".armada-send")),
                    agent: box(shape.querySelector(".armada-field-agent")),
                    plus: box(shape.querySelector(".armada-field-plus")),
                    form: shape.querySelector(".armada-send").dataset.form,
                };
            }),
    );
    const tall = fields.find((field) => field.title.startsWith("Four lines"));
    const ten = fields.filter((field) => field !== tall && field.form === "word").slice(0, 10);
    const off = (field) =>
        field.shape.w !== 752 || field.shape.h !== 44 || field.shape.x - field.dock.x !== 0;
    ctx.assert("field.states-measured", ten.length, "==", 10, {
        note: ten.map((f) => f.title).join(" | "),
    });
    ctx.assert("field.states-not-752-by-44", ten.filter((field) => off(field)).length, "==", 0, {
        note: ten
            .filter((field) => off(field))
            .map((f) => `${f.title}: ${f.shape.w} x ${f.shape.h}`)
            .join("; "),
    });
    ctx.assert("field.four-lines.height", tall?.shape.h ?? null, "==", 116, {unit: "px"});
    ctx.assert("field.four-lines.width", tall?.shape.w ?? null, "==", 752, {unit: "px"});
    const wrong = (test) => ten.filter((field) => !test(field)).length;
    ctx.assert(
        "field.send-not-72-by-32",
        wrong((f) => f.send.w === 72 && f.send.h === 32),
        "==",
        0,
        {
            note: `widths ${[...new Set(ten.map((f) => f.send.w))].join(", ")}`,
        },
    );
    ctx.assert(
        "field.send-not-6-from-edge",
        wrong((f) => f.shape.r - f.send.r === 6 && f.shape.b - f.send.b === 6),
        "==",
        0,
    );
    ctx.assert(
        "field.agent-not-32-square-4-before",
        wrong((f) => f.agent.w === 32 && f.agent.h === 32 && f.send.x - f.agent.r === 4),
        "==",
        0,
    );
    ctx.assert(
        "field.plus-not-32-square-6-in",
        wrong((f) => f.plus.w === 32 && f.plus.h === 32 && f.plus.x - f.shape.x === 6),
        "==",
        0,
    );
}

async function controls(ctx) {
    const found = await ctx.page.evaluate(() => {
        const small = [];
        const unnamed = [];
        let count = 0;
        let probed = 0;
        for (const control of document.querySelectorAll(
            "[data-armada^='showroom-'] :is(button, textarea, [role=switch], [role=checkbox])",
        )) {
            let r = control.getBoundingClientRect();
            if (r.width === 0 || control.closest("[data-off]") || control.matches("[data-off]")) {
                continue;
            }
            count += 1;
            const label = control.getAttribute("aria-label") ?? control.textContent.trim();
            if (label === "") {
                unnamed.push(control.className);
            }
            if (Math.min(r.width, r.height) >= 24) {
                continue;
            }
            // What is drawn under 24 px must still be met by the pointer
            // across 24 in each direction: some 24 px box over it whose
            // corners all reach it (the hit shape may grow to one side).
            control.scrollIntoView({block: "center", inline: "center"});
            r = control.getBoundingClientRect();
            probed += 1;
            const reaches = (px, py) => control.contains(document.elementFromPoint(px, py));
            const span = (start, size) => {
                const wide = Math.max(size, 24);
                // A whole pixel in from each edge: hit testing rounds a
                // point on the edge into the neighbour.
                const starts = [];
                for (let from = start + size - wide; from <= start; from += 1) {
                    starts.push([from + 1, from + wide - 1]);
                }
                return starts;
            };
            const met = span(r.left, r.width).some(([x0, x1]) =>
                span(r.top, r.height).some(
                    ([y0, y1]) =>
                        reaches(x0, y0) && reaches(x1, y0) && reaches(x0, y1) && reaches(x1, y1),
                ),
            );
            if (!met) {
                small.push(`${control.className} ${r.width}x${r.height}`);
            }
        }
        const gaps = [];
        for (const shape of document.querySelectorAll(".armada-field-shape")) {
            const boxes = [...shape.querySelectorAll(":scope > button")]
                .map((b) => b.getBoundingClientRect())
                .filter((r) => r.width > 0)
                .toSorted((a, b) => a.left - b.left);
            for (let i = 1; i < boxes.length; i += 1) {
                if (boxes[i].top < boxes[i - 1].bottom && boxes[i].bottom > boxes[i - 1].top) {
                    gaps.push(boxes[i].left - boxes[i - 1].right);
                }
            }
        }
        window.scrollTo(0, 0);
        return {count, probed, small, unnamed, gaps};
    });
    ctx.assert("controls.measured", found.count, ">=", 300);
    ctx.assert("controls.hit-area-under-24px", found.small.length, "==", 0, {
        note: `${found.probed} drawn under 24 px probed at the corners of a 24 px box over each${
            found.small.length > 0
                ? `; missed: ${[...new Set(found.small)].slice(0, 6).join("; ")}`
                : ""
        }`,
    });
    ctx.assert("controls.unnamed", found.unnamed.length, "==", 0, {
        note: found.unnamed.slice(0, 4).join("; "),
    });
    ctx.assert(
        "field.hit-shapes-closer-than-4px",
        found.gaps.filter((gap) => gap < 4).length,
        "==",
        0,
        {
            note: `gaps ${[...new Set(found.gaps)].join(", ")}`,
        },
    );
}

// The keyboard, the way a person uses it: a group is one Tab stop and
// the arrows move within it; a menu, the picker and a dialog open in the
// top layer, and Esc closes them and gives focus back to what opened them.
async function keys(ctx) {
    const bench = '[data-armada="bench-keys"]';
    const state = async () =>
        await ctx.page.evaluate((query) => {
            const active = document.activeElement;
            const open = document.querySelector(
                `${query} .armada-raised:popover-open, ${query} dialog:modal`,
            );
            return {
                active: active?.textContent.trim().split(/\s+/).slice(0, 2).join(" ") ?? "",
                open: open?.getAttribute("aria-label") ?? "",
                stops: open ? open.querySelectorAll("[tabindex='0']").length : 0,
                ring: document.querySelector(".armada-ring")?.matches(":popover-open") ?? false,
                scrolled: window.scrollX,
            };
        }, bench);
    const open_from = async (label) => {
        await ctx.page.evaluate(
            (query, name) => {
                const opener = [...document.querySelectorAll(`${query} .armada-text-control`)].find(
                    (control) => control.textContent.trim() === name,
                );
                opener.scrollIntoView({block: "center"});
                opener.focus();
            },
            bench,
            label,
        );
        await ctx.page.keyboard.press("Enter");
        await sleep(150);
        return await state();
    };
    const press = async (key) => {
        await ctx.page.keyboard.press(key);
        await sleep(80);
        return await state();
    };

    const menu = await open_from("Open a menu");
    ctx.assert("keys.menu.opens-in-the-top-layer", Number(menu.open === "Message"), "==", 1, {
        note: menu.open,
    });
    ctx.assert("keys.menu.focus-on-its-first-row", Number(menu.active === "Reply in"), "==", 1, {
        note: menu.active,
    });
    ctx.assert("keys.menu.tab-stops", menu.stops, "==", 1);
    const walk = [];
    for (const key of ["ArrowDown", "ArrowDown", "End", "Home", "ArrowUp"]) {
        walk.push((await press(key)).active);
    }
    ctx.assert(
        "keys.menu.arrows-home-end-wrong",
        ["Copy text", "Copy link", "Delete", "Reply in", "Delete"].filter(
            (expected, index) => walk[index] !== expected,
        ).length,
        "==",
        0,
        {note: walk.join(" > ")},
    );
    ctx.assert("keys.menu.ring-over-the-top-layer", Number(menu.ring), "==", 1);
    const closed = await press("Escape");
    ctx.assert("keys.menu.esc-closes", Number(closed.open === ""), "==", 1);
    ctx.assert("keys.menu.focus-back-to-opener", Number(closed.active === "Open a"), "==", 1, {
        note: closed.active,
    });

    const picker = await open_from("Open the picker");
    ctx.assert(
        "keys.picker.focus-on-the-current-model",
        Number(picker.active.startsWith("Sol Fast")),
        "==",
        1,
        {
            note: picker.active,
        },
    );
    ctx.assert("keys.picker.tab-stops", picker.stops, "==", 2, {
        note: "the models, and the efforts",
    });
    const next = await press("ArrowDown");
    ctx.assert(
        "keys.picker.arrow-down-to-the-next-model",
        Number(next.active.startsWith("Mini")),
        "==",
        1,
        {
            note: next.active,
        },
    );
    const picker_closed = await press("Escape");
    ctx.assert(
        "keys.picker.esc-closes-and-gives-focus-back",
        Number(picker_closed.open === "" && picker_closed.active === "Open the"),
        "==",
        1,
    );

    const dialog = await open_from("Open a dialog");
    ctx.assert(
        "keys.dialog.modal",
        Number(dialog.open.startsWith("Discard the changes")),
        "==",
        1,
        {
            note: dialog.open,
        },
    );
    ctx.assert(
        "keys.dialog.focus-on-its-first-button",
        Number(dialog.active === "Cancel"),
        "==",
        1,
        {
            note: dialog.active,
        },
    );
    const dialog_closed = await press("Escape");
    ctx.assert(
        "keys.dialog.esc-closes-and-gives-focus-back",
        Number(dialog_closed.open === "" && dialog_closed.active === "Open a"),
        "==",
        1,
    );

    // A group of segments: the arrows move across it, and not the page.
    await ctx.page.evaluate(() => {
        document
            .querySelector('[data-armada="showroom-toolbar"] .armada-segment[tabindex="0"]')
            .focus();
    });
    const before = await state();
    const across = await press("ArrowRight");
    ctx.assert(
        "keys.segments.arrow-moves-focus",
        Number(across.active !== before.active),
        "==",
        1,
        {
            note: `${before.active} > ${across.active}`,
        },
    );
    ctx.assert("keys.segments.page-scrolled-px", across.scrolled, "==", 0);
}

async function sprite(ctx) {
    const found = await ctx.page.evaluate(async () => {
        const host = document.querySelector("svg > symbol").parentElement;
        const ids = [...host.querySelectorAll("symbol")].map((symbol) => symbol.id);
        // Each glyph alone, at 1x, one black pixel of weight: a straight
        // stroke that does not sit on whole pixels shows as two columns (or
        // rows) half covered side by side along its length. A curve or a
        // diagonal shades a pixel or two at a time.
        const soft = [];
        for (const symbol of host.querySelectorAll('symbol[id^="armada-glyph-"]')) {
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="black" stroke-width="1" stroke-linecap="butt" stroke-linejoin="round" color="black">${symbol.innerHTML}</svg>`;
            const image = new Image();
            image.src = `data:image/svg+xml;base64,${window.btoa(svg)}`;
            await image.decode();
            const canvas = document.createElement("canvas");
            canvas.width = 16;
            canvas.height = 16;
            const context = canvas.getContext("2d");
            context.drawImage(image, 0, 0);
            const {data} = context.getImageData(0, 0, 16, 16);
            const half = (x, y) => {
                const alpha = data[(y * 16 + x) * 4 + 3];
                return alpha > 90 && alpha < 165;
            };
            // A straight stroke off the pixel grid straddles two columns (or
            // rows): both are half covered, side by side, for its length.
            let longest = 0;
            for (let a = 0; a < 15; a += 1) {
                let down = 0;
                let across = 0;
                for (let b = 0; b < 16; b += 1) {
                    down = half(a, b) && half(a + 1, b) ? down + 1 : 0;
                    across = half(b, a) && half(b, a + 1) ? across + 1 : 0;
                    longest = Math.max(longest, down, across);
                }
            }
            if (longest >= 3) {
                soft.push(symbol.id.replace("armada-glyph-", ""));
            }
        }
        return {
            bytes: new window.TextEncoder().encode(host.outerHTML).length,
            glyphs: ids.filter((id) => id.startsWith("armada-glyph-")).length,
            marks: ids.filter((id) => /^armada-mark-(?!working)/.test(id)).length,
            working: ids.includes("armada-mark-working"),
            sigils: ids.filter((id) => id.startsWith("armada-sigil-")).length,
            dot: ids.includes("armada-dot"),
            drawn: [
                ...document.querySelectorAll(
                    "[data-armada='showroom-glyphs'] .armada-showroom-icon svg",
                ),
            ].filter(
                (svg) =>
                    svg.getBoundingClientRect().width === 16 &&
                    svg.firstElementChild.getBBox().width > 0,
            ).length,
            weight: window.getComputedStyle(document.querySelector(".armada-glyph")).strokeWidth,
            soft,
        };
    });
    ctx.assert("sprite.bytes", found.bytes, "<=", 16384, {unit: " B"});
    ctx.assert("sprite.glyphs", found.glyphs, "==", 51);
    ctx.assert("sprite.marks", found.marks, "==", 7);
    ctx.assert("sprite.sigils", found.sigils, "==", 4);
    ctx.assert("sprite.working-mark-and-dot", found.working && found.dot, "==", 1);
    ctx.assert("sprite.glyphs-drawn-at-16px", found.drawn, "==", 51, {
        note: `stroke ${found.weight} at 1x`,
    });
    const unexpected = found.soft.filter((glyph) => !LIGHTER_DIAGONALS.has(glyph));
    ctx.assert("sprite.glyphs-with-half-covered-strokes", unexpected.length, "==", 0, {
        note: `half-covered runs in: ${found.soft.join(", ") || "none"}; the design lists clip, link, edit and expand as lighter`,
    });
}

async function idioms(ctx) {
    await open_showroom(ctx, "as=both");
    const found = await ctx.page.evaluate(() => {
        let pairs = 0;
        const differing = [];
        for (const pair of document.querySelectorAll(".armada-showroom-pair")) {
            const [preact, built] = [
                pair.children[0].firstElementChild,
                pair.children[1].firstElementChild.firstElementChild,
            ];
            pairs += 1;
            const [a, b] = [preact.getBoundingClientRect(), built.getBoundingClientRect()];
            if (
                preact.outerHTML !== built.outerHTML ||
                Math.abs(a.width - b.width) > 0.01 ||
                Math.abs(a.height - b.height) > 0.01
            ) {
                differing.push(pair.closest("section").dataset.armada);
            }
        }
        return {pairs, differing};
    });
    ctx.assert("idioms.pairs-compared", found.pairs, ">=", 80);
    ctx.assert("idioms.preact-and-dom-differing", found.differing.length, "==", 0, {
        note: [...new Set(found.differing)].join(", "),
    });
}

async function pictures(ctx) {
    const accept = process.env.ARMADA_KIT_ACCEPT === "1";
    const dirs = baseline_dirs();
    for (const theme of ["light", "dark"]) {
        for (const scale of [1, 2]) {
            await open_showroom(ctx, `as=preact&theme=${theme}`, {scale, theme});
            const regions = await ctx.page.evaluate((ids) => {
                // A still: loops hold their first frame while pictures are taken.
                document.documentElement.dataset.motion = "reduced";
                return ids.map((id) => {
                    const section = document.querySelector(`[data-armada="showroom-${id}"]`);
                    const r = section.getBoundingClientRect();
                    const clip = {
                        x: Math.floor(r.left + window.scrollX),
                        y: Math.floor(r.top + window.scrollY),
                        width: Math.ceil(r.width),
                        height: Math.ceil(r.height),
                    };
                    // Emoji are the platform's own face: masked.
                    // Each component's own crop: a specimen of the sheet.
                    const crops = [...section.querySelectorAll(".armada-showroom-stack")].map(
                        (stack) => {
                            const c = stack.getBoundingClientRect();
                            return {
                                x: c.left - r.left,
                                y: c.top - r.top,
                                width: c.width,
                                height: c.height,
                            };
                        },
                    );
                    const masks = [
                        ...section.querySelectorAll(
                            '.armada-reaction-emoji, .armada-bubble[data-kind="jumbo"], .armada-bit',
                        ),
                    ].map((e) => {
                        const m = e.getBoundingClientRect();
                        return {
                            x: m.left - r.left - 1,
                            y: m.top - r.top - 1,
                            width: m.width + 2,
                            height: m.height + 2,
                        };
                    });
                    return {id, clip, masks, crops};
                });
            }, SECTIONS);
            let worst = 0;
            let worst_id = "";
            let missing = 0;
            let compared = 0;
            for (const {id, clip, masks, crops} of regions) {
                // A section far down the page is painted only once it has
                // been in view; take it there.
                await ctx.page.evaluate((section) => {
                    document
                        .querySelector(`[data-armada="showroom-${section}"]`)
                        .scrollIntoView({block: "start"});
                }, id);
                await sleep(250);
                const shot = await ctx.page.screenshot({clip, encoding: "base64"});
                const file = `${id}-${theme}-${scale}x.png`;
                const accepted = path.join(dirs[0], file);
                if (accept) {
                    for (const dir of dirs) {
                        fs.mkdirSync(dir, {recursive: true});
                        fs.writeFileSync(path.join(dir, file), Buffer.from(shot, "base64"));
                    }
                    continue;
                }
                if (!fs.existsSync(accepted)) {
                    missing += 1;
                    continue;
                }
                const device = (box) => ({
                    x: box.x * scale,
                    y: box.y * scale,
                    width: box.width * scale,
                    height: box.height * scale,
                });
                const percent = await percent_differing(
                    ctx.page,
                    fs.readFileSync(accepted).toString("base64"),
                    shot,
                    masks.map((mask) => device(mask)),
                    crops.map((crop) => device(crop)),
                );
                compared += crops.length;
                let [section_worst, index] = [percent.whole, -1];
                for (const [at, value] of percent.crops.entries()) {
                    if (value > section_worst) {
                        [section_worst, index] = [value, at];
                    }
                }
                if (section_worst > worst) {
                    worst = section_worst;
                    worst_id = index === -1 ? id : `${id} specimen ${index + 1}`;
                }
                if (section_worst > 0.1) {
                    const [out] = keep_targets("var/armada-checks/kit").toReversed();
                    fs.mkdirSync(out, {recursive: true});
                    fs.writeFileSync(path.join(out, file), Buffer.from(shot, "base64"));
                }
            }
            if (accept) {
                ctx.info(`pictures.${theme}-${scale}x.accepted`, regions.length, {
                    note: `written to ${BASELINE}`,
                });
            } else {
                ctx.assert(
                    `pictures.${theme}-${scale}x.sections-without-a-picture`,
                    missing,
                    "==",
                    0,
                );
                ctx.assert(`pictures.${theme}-${scale}x.components-compared`, compared, ">=", 80);
                ctx.assert(
                    `pictures.${theme}-${scale}x.worst-component-percent-differing`,
                    missing === regions.length ? null : worst,
                    "<=",
                    0.1,
                    {
                        unit: "%",
                        note: worst_id
                            ? `of each component's crop, and of the section; worst: ${worst_id}; what differs is written to var/armada-checks/kit/`
                            : "of each component's crop, and of the section",
                    },
                );
            }
        }
    }
}

export async function run(ctx) {
    await ctx.scenario("showroom", async () => {
        const status = await open_showroom(ctx);
        ctx.assert("showroom.status", status, "==", 200);
        const sections = await ctx.page.evaluate(
            () => document.querySelectorAll("[data-armada^='showroom-']").length,
        );
        ctx.assert("showroom.sections", sections, ">=", SECTIONS.length + 3);
        ctx.assert(
            "showroom.console-errors",
            ctx.console_errors.length + ctx.page_errors.length,
            "==",
            0,
            {
                note: [...ctx.console_errors, ...ctx.page_errors].slice(0, 2).join(" | "),
            },
        );
        await field_geometry(ctx);
        await controls(ctx);
        await sprite(ctx);
    });
    await ctx.scenario("keys", async () => {
        await open_showroom(ctx, "as=preact");
        await keys(ctx);
    });
    await ctx.scenario("idioms", async () => {
        await idioms(ctx);
    });
    await ctx.scenario("pictures", async () => {
        await pictures(ctx);
    });
}
