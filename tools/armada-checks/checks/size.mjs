// 16. What the application costs to download: production builds, weighed.

import {measure} from "../../armada/size/measure.mjs";

export const name = "size";
export const order = 160;
export const description =
    "production builds of the app and armada-beta entries, each file minified: initial JavaScript, initial CSS, the largest lazy chunk; the beta's also in brotli";

// SPEC-ENGINEERING.md section 8, "Size". The build is far over each of
// them today; known.json holds today's figure as a ceiling (`max`), which
// steps down as stages delete code.
const BUDGET_KB = {
    initial_js: 2000,
    initial_css: 160,
    largest_lazy_chunk: 800,
};

// The interface people load at `/`, the interim beta (web/src/armada/beta/).
// Brotli is what a person waits for, as the server sends it; minified
// JavaScript is what the browser parses. Each budget is about 8% above
// the figure with the perf and features batch of 16 October 2026 (92 kB,
// 302 kB and 19 kB), rounded up to the next 5 kB, so that it passes today
// and growth past it is a deliberate raise.
const BETA_BUDGET_KB = {
    initial_js_brotli: 100,
    initial_js: 330,
    initial_css_brotli: 25,
};

const kb = (bytes) => Math.round(bytes / 1000);

export async function run(ctx) {
    // The builds run beside the page the harness opened, not in it.
    const size = await measure();
    // A build that produced no entrypoint must not read as "0 kB".
    const measurable = size.initial_js.files > 0 && size.initial_js.minified > 0;
    const largest = size.lazy_js.largest[0];

    ctx.assert(
        "initial-js.minified-kb",
        measurable ? kb(size.initial_js.minified) : null,
        "<=",
        BUDGET_KB.initial_js,
        {
            unit: "kB",
            note: measurable
                ? `${size.initial_js.files} files; ${kb(size.initial_js.gzip)} kB gzip, ${kb(size.initial_js.raw)} kB before minification`
                : "not measurable: the build has no app entrypoint",
        },
    );
    ctx.assert(
        "initial-css.minified-kb",
        size.initial_css.files > 0 ? kb(size.initial_css.minified) : null,
        "<=",
        BUDGET_KB.initial_css,
        {
            unit: "kB",
            note:
                size.initial_css.files > 0
                    ? `${size.initial_css.files} files; ${kb(size.initial_css.gzip)} kB gzip`
                    : "not measurable: the entrypoint has no stylesheet",
        },
    );
    ctx.assert(
        "lazy-chunk.largest-minified-kb",
        largest ? kb(largest.minified) : null,
        "<=",
        BUDGET_KB.largest_lazy_chunk,
        {
            unit: "kB",
            note: largest
                ? size.lazy_js.largest
                      .slice(0, 4)
                      .map((chunk) => `${kb(chunk.minified)} kB ${chunk.label}`)
                      .join("; ")
                : "not measurable: the build has no lazy chunk",
        },
    );
    ctx.info("lazy-js.minified-kb", kb(size.lazy_js.minified), {
        unit: "kB",
        note: `${size.lazy_js.files} chunks fetched on first use`,
    });

    const {initial_js: js, initial_css: css} = await measure({entry: "armada-beta", brotli: true});
    const js_measurable = js.files > 0 && js.minified > 0;
    const css_measurable = css.files > 0 && css.minified > 0;
    const in_files = (figures) => (figures.files === 1 ? "1 file" : `${figures.files} files`);
    const no_js = "not measurable: the build has no armada-beta entrypoint";

    ctx.assert(
        "beta-js.brotli-kb",
        js_measurable ? kb(js.brotli) : null,
        "<=",
        BETA_BUDGET_KB.initial_js_brotli,
        {
            unit: "kB",
            note: js_measurable
                ? `${in_files(js)}; ${kb(js.minified)} kB minified, ${kb(js.gzip)} kB gzip`
                : no_js,
        },
    );
    ctx.assert(
        "beta-js.minified-kb",
        js_measurable ? kb(js.minified) : null,
        "<=",
        BETA_BUDGET_KB.initial_js,
        {
            unit: "kB",
            note: js_measurable ? `${in_files(js)}; ${kb(js.raw)} kB before minification` : no_js,
        },
    );
    ctx.assert(
        "beta-css.brotli-kb",
        css_measurable ? kb(css.brotli) : null,
        "<=",
        BETA_BUDGET_KB.initial_css_brotli,
        {
            unit: "kB",
            note: css_measurable
                ? `${in_files(css)}; ${kb(css.minified)} kB minified, ${kb(css.gzip)} kB gzip`
                : "not measurable: the armada-beta entrypoint has no stylesheet",
        },
    );
}
