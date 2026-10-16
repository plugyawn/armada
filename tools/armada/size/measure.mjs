#!/usr/bin/env node
// What the application costs to load, from a production build.
//
//   node tools/armada/size/measure.mjs [--entry <name>]... [--json <path>] [--keep]
//
// Builds one entry of web/webpack.assets.json in production mode into a
// temporary directory (nothing is written to the checkout), minifies every
// emitted script and stylesheet with esbuild, and weighs:
//
//   initial JavaScript   what the page must download and run before it
//                        works: the files of the entrypoint
//   initial CSS          the stylesheets of that entrypoint
//   lazy chunks          everything else, fetched on first use
//
// Each entry is built on its own, so that what one costs does not depend
// on which others share its chunks. Asked for brotli, it also compresses
// each minified file at quality 11, as the server's pre-deploy hook does
// (puppet/armada/files/hooks/60-armada-brotli.hook): that is what a person
// waits for, but it takes about a second a megabyte, so the check asks for
// it only where it has a budget.
//
// The command weighs `app` (the old interface, about thirty seconds) and
// `armada-beta` (the interface served at `/`, a few seconds), or the
// entries `--entry` names, brotli included; `--json` writes the figures
// keyed by entry. `arm check size` runs `measure()` and compares the
// figures with their budgets. Sizes are bytes; "kB" is 1,000 bytes.

import {spawnSync} from "node:child_process";
import * as fs from "node:fs";
import {createRequire} from "node:module";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import zlib from "node:zlib";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");
const require = createRequire(path.join(REPO, "web/"));
const BROTLI = {params: {[zlib.constants.BROTLI_PARAM_QUALITY]: 11}};

function find_esbuild() {
    // esbuild is not a direct dependency; it is in the store because other
    // packages bring it.
    const store = path.join(REPO, "node_modules/.pnpm");
    const candidates = fs
        .readdirSync(store)
        .filter((name) => name.startsWith("esbuild@"))
        .toSorted();
    if (candidates.length === 0) {
        throw new Error("esbuild is not installed under node_modules/.pnpm");
    }
    return require(path.join(store, candidates.at(-1), "node_modules/esbuild"));
}

// The entries the repository declares, by name.
function declared_entries() {
    const assets = JSON.parse(fs.readFileSync(path.join(REPO, "web/webpack.assets.json"), "utf8"));
    return Object.keys(assets);
}

function build(output, entry) {
    const stats_file = path.join(output, "stats.json");
    const result = spawnSync(
        process.execPath,
        [
            path.join(REPO, "node_modules/webpack-cli/bin/cli.js"),
            "--mode=production",
            `--config=${path.join(HERE, "webpack.size.config.mjs")}`,
            `--json=${stats_file}`,
        ],
        {
            cwd: path.join(REPO, "web"),
            env: {
                ...process.env,
                ARMADA_SIZE_ENTRY: entry,
                ARMADA_SIZE_OUT: path.join(output, "out"),
                BROWSERSLIST_IGNORE_OLD_DATA: "1",
                NODE_OPTIONS: "--max-old-space-size=1800",
            },
            encoding: "utf8",
            maxBuffer: 64 * 1024 * 1024,
        },
    );
    if (!fs.existsSync(stats_file)) {
        throw new Error(
            `the production build wrote no stats (exit ${result.status}): ${String(result.stderr ?? "").slice(-600)}`,
        );
    }
    const stats = JSON.parse(fs.readFileSync(stats_file, "utf8"));
    if ((stats.errors ?? []).length > 0) {
        throw new Error(
            `the production build failed: ${stats.errors
                .slice(0, 3)
                .map((error) => String(error.message ?? error).split("\n")[0])
                .join(" | ")}`,
        );
    }
    return stats;
}

// Where a lazy chunk is asked for: the modules that import() it.
function chunk_label(chunk) {
    const origins = [
        ...new Set(
            (chunk.origins ?? [])
                .map((origin) => origin.request ?? origin.moduleName)
                .filter(Boolean)
                .map((request) => String(request).split("!").at(-1)),
        ),
    ];
    return (chunk.names ?? []).join(",") || origins.slice(0, 2).join(", ") || String(chunk.id);
}

// The figures of one kind of file, initial or fetched on first use.
function totals(files, kind, is_initial) {
    const chosen = files.filter((file) => file.kind === kind && file.initial === is_initial);
    const sum = (key) => chosen.reduce((total, file) => total + file[key], 0);
    return {
        files: chosen.length,
        raw: sum("raw"),
        minified: sum("minified"),
        gzip: sum("gzip"),
        brotli: chosen.some((file) => file.brotli === null) ? null : sum("brotli"),
    };
}

function weigh(files) {
    const lazy_scripts = files
        .filter((file) => file.kind === "js" && !file.initial)
        .toSorted((a, b) => b.minified - a.minified);
    return {
        initial_js: totals(files, "js", true),
        initial_css: totals(files, "css", true),
        lazy_js: {
            files: lazy_scripts.length,
            minified: totals(files, "js", false).minified,
            largest: lazy_scripts.slice(0, 8).map((file) => ({
                name: file.name,
                label: file.label,
                minified: file.minified,
            })),
        },
        files,
    };
}

// What one entry costs. Without `brotli`, every brotli figure is null.
export async function measure({entry = "app", brotli = false, keep = false} = {}) {
    // An entry the repository no longer declares weighs nothing, so that
    // the check reports it as not measurable instead of failing to build.
    if (!declared_entries().includes(entry)) {
        return weigh([]);
    }
    const output = fs.mkdtempSync(path.join(os.tmpdir(), "armada-size-"));
    try {
        const esbuild = find_esbuild();
        const stats = build(output, entry);
        const initial = new Set(
            stats.entrypoints[entry].assets.map((asset) => asset.name ?? asset),
        );
        const label_of = new Map();
        for (const chunk of stats.chunks ?? []) {
            for (const file of chunk.files ?? []) {
                label_of.set(file, chunk_label(chunk));
            }
        }
        const files = [];
        // Every script and stylesheet the build wrote; the stats group
        // their assets, the directory does not.
        const emitted = fs
            .readdirSync(path.join(output, "out"), {recursive: true})
            .map(String)
            .toSorted();
        for (const name of emitted) {
            const asset = {name};
            const extension = path.extname(asset.name);
            if (extension !== ".js" && extension !== ".css") {
                continue;
            }
            const source = fs.readFileSync(path.join(output, "out", asset.name), "utf8");
            const minified = (
                await esbuild.transform(source, {
                    loader: extension === ".js" ? "js" : "css",
                    minify: true,
                    legalComments: "none",
                })
            ).code;
            files.push({
                name: asset.name,
                kind: extension.slice(1),
                initial: initial.has(asset.name),
                label: label_of.get(asset.name) ?? asset.name,
                raw: Buffer.byteLength(source),
                minified: Buffer.byteLength(minified),
                gzip: zlib.gzipSync(minified).length,
                brotli: brotli ? zlib.brotliCompressSync(minified, BROTLI).length : null,
            });
        }
        return weigh(files);
    } finally {
        if (!keep) {
            fs.rmSync(output, {recursive: true, force: true});
        } else {
            console.error(`kept ${output}`);
        }
    }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    const argv = process.argv.slice(2);
    const entries = argv.flatMap((argument, index) =>
        argv[index - 1] === "--entry" ? [argument] : [],
    );
    const keep = argv.includes("--keep");
    const results = {};
    const kb = (bytes) => `${(bytes / 1000).toFixed(0)} kB`;
    const weights = (figures) =>
        `${kb(figures.minified)} minified, ${kb(figures.brotli)} brotli, ${kb(figures.gzip)} gzip, ${kb(figures.raw)} raw (${figures.files} files)`;
    for (const entry of entries.length > 0 ? entries : ["app", "armada-beta"]) {
        const result = await measure({entry, brotli: true, keep});
        results[entry] = result;
        console.log(entry);
        console.log(`  initial JavaScript  ${weights(result.initial_js)}`);
        console.log(`  initial CSS         ${weights(result.initial_css)}`);
        console.log(
            `  lazy JavaScript     ${kb(result.lazy_js.minified)} minified in ${result.lazy_js.files} chunks; largest:`,
        );
        for (const chunk of result.lazy_js.largest) {
            console.log(`      ${kb(chunk.minified).padStart(8)}  ${chunk.label}`);
        }
    }
    const json = argv.indexOf("--json");
    if (json !== -1) {
        fs.writeFileSync(argv[json + 1], `${JSON.stringify(results, null, 4)}\n`);
    }
}
