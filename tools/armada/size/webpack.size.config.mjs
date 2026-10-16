// The production build `measure.mjs` weighs: one entry alone (named by
// ARMADA_SIZE_ENTRY), with the repository's own production configuration,
// written to a directory outside the checkout. Nothing here changes what
// is built; it only turns off what a measurement does not need (the
// persistent cache, source maps, the stats files the server reads,
// compressed copies) and leaves minification to `measure.mjs`, which
// minifies each file with esbuild in a second instead of with terser in
// minutes. Terser's output is a few percent smaller; the ceilings in
// known.json and the budgets in the size check are esbuild's figures.

import base from "../../../web/webpack.config.ts";

const DROPPED_PLUGINS = new Set(["BundleTracker", "CompressionPlugin", "HtmlWebpackPlugin"]);

export default function size_config(env, argv) {
    const output = process.env.ARMADA_SIZE_OUT;
    const entry = process.env.ARMADA_SIZE_ENTRY;
    if (!output || !entry) {
        throw new Error(
            "ARMADA_SIZE_OUT or ARMADA_SIZE_ENTRY is not set; run tools/armada/size/measure.mjs",
        );
    }
    const [frontend] = base(env, argv);
    frontend.entry = {[entry]: frontend.entry[entry]};
    frontend.cache = false;
    frontend.devtool = false;
    frontend.optimization.minimize = false;
    frontend.output.path = output;
    frontend.plugins = frontend.plugins.filter(
        (plugin) => !DROPPED_PLUGINS.has(plugin.constructor.name),
    );
    frontend.stats = {
        all: false,
        chunks: true,
        chunkModules: false,
        chunkOrigins: true,
        chunkRelations: true,
        entrypoints: true,
        errors: true,
        ids: true,
    };
    return frontend;
}
