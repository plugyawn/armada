// The showroom, for the checks that read it: how to open it, and how to
// compare two pictures of it without an image library (the browser's own
// canvas decodes and counts).

/* global document, window, Image */

import {sleep} from "./browser.mjs";

export const SECTIONS = [
    "type",
    "colour",
    "space",
    "header",
    "controls",
    "composer",
    "band",
    "column",
    "nav",
    "conversation",
    "blocks",
    "raised",
    "marks",
    "glyphs",
];
export const SHEET_WIDTH = 1712;

export async function open_showroom(ctx, query = "as=preact", {scale = 1, theme = "light"} = {}) {
    await ctx.page.setViewport({width: SHEET_WIDTH, height: 1000, deviceScaleFactor: scale});
    await ctx.page.emulateMediaFeatures([{name: "prefers-color-scheme", value: theme}]);
    const response = await ctx.page.goto(`${ctx.base}/devtools/armada/?${query}`, {
        waitUntil: "networkidle2",
    });
    await ctx.page.evaluate(async () => {
        await document.fonts.ready;
    });
    await sleep(600);
    return response.status();
}

// The share of pixels that differ between two PNGs (base64), in percent,
// with the rectangles in `masks` left out of both: of the whole picture,
// and of each rectangle in `crops` (device pixels; a component's own
// crop). A size mismatch is 100 everywhere.
export async function percent_differing(page, a, b, masks = [], crops = []) {
    return await page.evaluate(
        async (first, second, skip, parts) => {
            const load = async (data) => {
                const image = new Image();
                image.src = `data:image/png;base64,${data}`;
                await image.decode();
                const canvas = document.createElement("canvas");
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                const context = canvas.getContext("2d", {willReadFrequently: true});
                context.drawImage(image, 0, 0);
                for (const mask of skip) {
                    context.fillRect(mask.x, mask.y, mask.width, mask.height);
                }
                return context.getImageData(0, 0, canvas.width, canvas.height);
            };
            const [one, two] = [await load(first), await load(second)];
            if (one.width !== two.width || one.height !== two.height) {
                return {whole: 100, crops: parts.map(() => 100)};
            }
            const differs = (index) =>
                Math.abs(one.data[index] - two.data[index]) > 6 ||
                Math.abs(one.data[index + 1] - two.data[index + 1]) > 6 ||
                Math.abs(one.data[index + 2] - two.data[index + 2]) > 6;
            const share = (x0, y0, x1, y1) => {
                let differing = 0;
                for (let y = y0; y < y1; y += 1) {
                    for (let x = x0; x < x1; x += 1) {
                        if (differs((y * one.width + x) * 4)) {
                            differing += 1;
                        }
                    }
                }
                return (100 * differing) / Math.max(1, (x1 - x0) * (y1 - y0));
            };
            const clamp = (value, most) => Math.max(0, Math.min(most, Math.round(value)));
            return {
                whole: share(0, 0, one.width, one.height),
                crops: parts.map((crop) =>
                    share(
                        clamp(crop.x, one.width),
                        clamp(crop.y, one.height),
                        clamp(crop.x + crop.width, one.width),
                        clamp(crop.y + crop.height, one.height),
                    ),
                ),
            };
        },
        a,
        b,
        masks,
        crops,
    );
}

// Sample something every frame for a while, in the page: read in the task
// after the frame's rendering, so what is read is what was painted (an
// after-layout phase may still write in a frame after its rAF callbacks).
export async function install_sampler(page) {
    await page.evaluate(() => {
        window.__armada_sample = (read, ms) =>
            new Promise((resolve) => {
                const samples = [];
                const started = window.performance.now();
                const channel = new window.MessageChannel();
                const tick = () => {
                    const now = window.performance.now();
                    samples.push({t: now - started, v: read()});
                    if (now - started < ms) {
                        window.requestAnimationFrame(() => channel.port2.postMessage(0));
                    } else {
                        channel.port1.close();
                        resolve(samples);
                    }
                };
                channel.port1.addEventListener("message", tick);
                channel.port1.start();
                tick();
            });
    });
}
