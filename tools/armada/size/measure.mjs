#!/usr/bin/env node
// What the application costs to load, from a production build.
//
//   node tools/armada/size/measure.mjs [--json <path>] [--keep]
//
// Builds the `app` entry in production mode into a temporary directory
// (about a minute; nothing is written to the checkout), minifies every
// emitted script and stylesheet with esbuild, and prints:
//
//   initial JavaScript   what the page must download and run before it
//                        works: the files of the `app` entrypoint
//   initial CSS          the stylesheets of that entrypoint
//   lazy chunks          everything else, fetched on first use
//
// `arm check size` runs this and compares the figures with their budgets.
// Sizes are bytes of minified text; "kB" is 1,000 bytes.

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

function build(output) {
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

export async function measure({keep = false} = {}) {
    const output = fs.mkdtempSync(path.join(os.tmpdir(), "armada-size-"));
    try {
        const esbuild = find_esbuild();
        const stats = build(output);
        const initial = new Set(stats.entrypoints.app.assets.map((asset) => asset.name ?? asset));
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
            });
        }
        const total = (kind, is_initial, key) =>
            files
                .filter((file) => file.kind === kind && file.initial === is_initial)
                .reduce((sum, file) => sum + file[key], 0);
        const lazy_scripts = files
            .filter((file) => file.kind === "js" && !file.initial)
            .toSorted((a, b) => b.minified - a.minified);
        return {
            initial_js: {
                files: files.filter((file) => file.kind === "js" && file.initial).length,
                raw: total("js", true, "raw"),
                minified: total("js", true, "minified"),
                gzip: total("js", true, "gzip"),
            },
            initial_css: {
                files: files.filter((file) => file.kind === "css" && file.initial).length,
                raw: total("css", true, "raw"),
                minified: total("css", true, "minified"),
                gzip: total("css", true, "gzip"),
            },
            lazy_js: {
                files: lazy_scripts.length,
                minified: total("js", false, "minified"),
                largest: lazy_scripts.slice(0, 8).map((file) => ({
                    name: file.name,
                    label: file.label,
                    minified: file.minified,
                })),
            },
            files,
        };
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
    const result = await measure({keep: argv.includes("--keep")});
    const json = argv.indexOf("--json");
    if (json !== -1) {
        fs.writeFileSync(argv[json + 1], `${JSON.stringify(result, null, 4)}\n`);
    }
    const kb = (bytes) => `${(bytes / 1000).toFixed(0)} kB`;
    console.log(
        `initial JavaScript  ${kb(result.initial_js.minified)} minified, ${kb(result.initial_js.gzip)} gzip, ${kb(result.initial_js.raw)} raw (${result.initial_js.files} files)`,
    );
    console.log(
        `initial CSS         ${kb(result.initial_css.minified)} minified, ${kb(result.initial_css.gzip)} gzip, ${kb(result.initial_css.raw)} raw (${result.initial_css.files} files)`,
    );
    console.log(
        `lazy JavaScript     ${kb(result.lazy_js.minified)} minified in ${result.lazy_js.files} chunks; largest:`,
    );
    for (const chunk of result.lazy_js.largest) {
        console.log(`    ${kb(chunk.minified).padStart(8)}  ${chunk.label}`);
    }
}
