// The design language, counted on what is drawn: on the showroom, where
// every count is the design's limit, and on the running app's screens,
// where the old interface fails today and each surface's package turns its
// lines to fixed (known.json holds today's figures as ceilings).
//
// What is counted here is what computed styles can say: sizes, radii,
// faces, capitals and tracking, filters, shadows, curves and durations.
// The review sheet's colour and information counts (hue families,
// coloured pixels, second-ink share, regions) are the design's review
// tools in var/armada-overhaul/design/principles/ (saas-anatomy/smell.mjs,
// doctrine/extra.mjs, doctrine/pixels.py), run by hand. The package that
// first holds a surface to them brings them into lib/ in this
// repository's style and wires them here, with the figures of
// SPEC-DESIGN.md appendix A as limits.

/* global document, window */

import {sleep} from "../lib/browser.mjs";
import {open_showroom} from "../lib/showroom.mjs";

export const name = "design-lint";
export const order = 22;
export const description =
    "the design language on the showroom (sizes, radii, faces, capitals, filters, shadows, curves, durations), and the same counts on the app's screens";

// Runs in the page. `scope` is a selector for what is the design's to
// answer for.
function audit(scope) {
    const SIZES = new Set([12, 14, 16, 20, 28]);
    const RADII = new Set([0, 4, 6, 10, 14, 16, 18, 22]);
    const FACES = new Set(["Söhne", "Hanken Grotesk", "Geist Mono"]);
    const resolved = (declaration) => {
        const probe = document.createElement("i");
        probe.style.cssText = declaration;
        document.body.append(probe);
        const style = window.getComputedStyle(probe);
        const value = declaration.startsWith("box-shadow")
            ? style.boxShadow
            : style.transitionTimingFunction;
        probe.remove();
        return value;
    };
    const shadows = ["raised", "field", "field-focus"].map((token) =>
        resolved(`box-shadow: var(--shadow-${token})`),
    );
    const springs = new Set(
        ["touch", "move", "settle", "land"].map((token) =>
            resolved(`transition: opacity 1s var(--spring-${token})`),
        ),
    );
    const found = {
        elements: 0,
        text_runs: 0,
        under_12: [],
        capitals_or_tracking: [],
        sizes_off: [],
        radii_off: [],
        faces_off: [],
        filters: [],
        shadows_off: [],
        curves_off: [],
        over_440: [],
    };
    const name_of = (element) =>
        `${element.tagName.toLowerCase()}.${String(element.className?.baseVal ?? element.className).split(" ")[0]}`;
    const seconds = (list) =>
        Math.max(
            ...list
                .split(",")
                .map(
                    (value) => Number.parseFloat(value) * (value.trim().endsWith("ms") ? 0.001 : 1),
                ),
        );
    for (const root of document.querySelectorAll(scope)) {
        for (const element of [root, ...root.querySelectorAll("*")]) {
            if (element.closest("iframe, [hidden], symbol") || element.tagName === "IFRAME") {
                continue;
            }
            const box = element.getBoundingClientRect();
            if (box.width === 0 && box.height === 0) {
                continue;
            }
            found.elements += 1;
            const style = window.getComputedStyle(element);
            const own_text = [...element.childNodes].some(
                (node) => node.nodeType === 3 && node.nodeValue.trim() !== "",
            );
            if (own_text) {
                found.text_runs += 1;
                const size = Number.parseFloat(style.fontSize);
                if (size < 12) {
                    found.under_12.push(`${name_of(element)} ${size}`);
                }
                if (!SIZES.has(Math.round(size * 100) / 100)) {
                    found.sizes_off.push(`${name_of(element)} ${size}`);
                }
                if (
                    style.textTransform === "uppercase" ||
                    style.fontVariantCaps !== "normal" ||
                    !["normal", "0px"].includes(style.letterSpacing) ||
                    !["normal", "0px"].includes(style.wordSpacing)
                ) {
                    found.capitals_or_tracking.push(name_of(element));
                }
                const face = style.fontFamily.split(",")[0].trim().replaceAll(/["']/g, "");
                if (!FACES.has(face)) {
                    found.faces_off.push(`${name_of(element)} ${face}`);
                }
            }
            for (const corner of [
                style.borderTopLeftRadius,
                style.borderTopRightRadius,
                style.borderBottomLeftRadius,
                style.borderBottomRightRadius,
            ]) {
                const first = corner.split(" ")[0];
                const round = first.endsWith("%")
                    ? Number.parseFloat(first) >= 50
                    : Number.parseFloat(first) >= Math.min(box.width, box.height) / 2 - 0.01;
                if (!round && !RADII.has(Number.parseFloat(first))) {
                    found.radii_off.push(`${name_of(element)} ${first}`);
                    break;
                }
            }
            if ((style.backdropFilter ?? "none") !== "none" || style.filter !== "none") {
                found.filters.push(name_of(element));
            }
            if (style.boxShadow !== "none") {
                // The three shadow tokens, and 1 px edges drawn as shadows.
                let rest = style.boxShadow;
                for (const shadow of shadows) {
                    rest = rest.replace(shadow, "");
                }
                rest = rest
                    .replaceAll(
                        /rgba?\([^)]*\) (0px|-?1px) (0px|-?1px) 0px (0px|1px)( inset)?/g,
                        "",
                    )
                    .replaceAll(/[\s,]/g, "");
                if (rest !== "") {
                    found.shadows_off.push(`${name_of(element)} ${style.boxShadow.slice(0, 60)}`);
                }
            }
            const moves = [];
            if (seconds(style.transitionDuration) > 0) {
                moves.push([style.transitionTimingFunction, seconds(style.transitionDuration)]);
            }
            if (style.animationName !== "none") {
                moves.push([
                    style.animationTimingFunction,
                    style.animationIterationCount === "infinite"
                        ? 0
                        : seconds(style.animationDuration),
                ]);
            }
            for (const [curves, lasts] of moves) {
                if (
                    curves
                        .split(/,(?![^(]*\))/)
                        .some((curve) => curve.trim() !== "linear" && !springs.has(curve.trim()))
                ) {
                    found.curves_off.push(`${name_of(element)} ${curves.slice(0, 40)}`);
                }
                if (lasts > 0.4401) {
                    found.over_440.push(`${name_of(element)} ${lasts}s`);
                }
            }
        }
    }
    return found;
}

const LINES = [
    ["text-under-12px", "under_12"],
    ["capitals-or-tracked-runs", "capitals_or_tracking"],
    ["sizes-off-the-scale", "sizes_off"],
    ["radii-off-the-scale", "radii_off"],
    ["faces-not-the-two", "faces_off"],
    ["filters", "filters"],
    ["shadows-not-tokens", "shadows_off"],
    ["curves-not-springs", "curves_off"],
    ["longer-than-440ms", "over_440"],
];

function report(ctx, prefix, found) {
    ctx.assert(`${prefix}.elements-read`, found.elements, ">=", 100, {
        note: `${found.text_runs} runs of text`,
    });
    for (const [id, key] of LINES) {
        const examples = [...new Set(found[key])];
        ctx.assert(`${prefix}.${id}`, found[key].length, "==", 0, {
            note:
                examples.length > 0
                    ? `${examples.length} kinds: ${examples.slice(0, 5).join("; ")}`
                    : "",
        });
    }
}

// The app's screens the review sheet names that exist today.
const SCREENS = [
    ["live-run", "#narrow/channel/22-click/topic/demo-live-run"],
    ["settled", "#narrow/channel/22-click/topic/demo-completed-run"],
    ["home", "#inbox"],
];

export async function run(ctx) {
    for (const theme of ["light", "dark"]) {
        await ctx.scenario(`showroom.${theme}`, async () => {
            await open_showroom(ctx, `as=preact&theme=${theme}`, {theme});
            report(
                ctx,
                `showroom.${theme}`,
                await ctx.page.evaluate(audit, "[data-armada='showroom']"),
            );
        });
    }
    await ctx.page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
    await ctx.page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "light"}]);
    for (const [id, hash] of SCREENS) {
        await ctx.scenario(`app.${id}`, async () => {
            await ctx.open(hash);
            await sleep(1500);
            report(ctx, `app.${id}`, await ctx.page.evaluate(audit, "body"));
        });
    }
}
