// 16. What the application costs to download: a production build, weighed.

import {measure} from "../../armada/size/measure.mjs";

export const name = "size";
export const order = 160;
export const description =
    "production build of the app entry, each file minified: initial JavaScript, initial CSS, the largest lazy chunk";

// SPEC-ENGINEERING.md section 8, "Size". The build is far over each of
// them today; known.json holds today's figure as a ceiling (`max`), which
// steps down as stages delete code.
const BUDGET_KB = {
    initial_js: 2000,
    initial_css: 160,
    largest_lazy_chunk: 800,
};

const kb = (bytes) => Math.round(bytes / 1000);

export async function run(ctx) {
    // The build runs beside the page the harness opened, not in it.
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
}
