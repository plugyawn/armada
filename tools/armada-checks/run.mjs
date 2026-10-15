// Armada layout-stability and latency checks.
//
//   limactl shell armada -- arm check [check names...] [--json <path>]
//                                     [--update-known] [--accept-failures] [--list]
//
// Runs each check in a fresh page of one headless Chromium, prints one line
// per assertion, writes a JSON report, and exits non-zero unless the suite is
// green. One run at a time: it takes /tmp/armada-check.lock (lib/lock.mjs).
// See README.md in this directory.

import {spawnSync} from "node:child_process";
import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {APP, BASE, USER, VIEWPORT, launch, login} from "./lib/browser.mjs";
import {classify, rewrite_known, stale_ids} from "./lib/classify.mjs";
import {CheckContext} from "./lib/harness.mjs";
import {changed_files, changes, describe_changed_files, problems, snapshot} from "./lib/health.mjs";
import {REPO, keep_targets} from "./lib/host.mjs";
import {ENV_ERROR_STATUS, run_under_lock} from "./lib/lock.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// The tree the server under test runs from, whose processes and files the
// health checks watch (lib/health.mjs); `arm check` runs from it.
const SERVED_TREE = process.env.ARMADA_SERVED_TREE ?? REPO;
const HEALTH_IO = {fetch, proc: "/proc", statfs: fs.statfsSync};
const KNOWN_FILE = "tools/armada-checks/known.json";
const DEFAULT_REPORT = "var/armada-checks/report.json";

function parse_arguments(argv) {
    const options = {
        names: [],
        json: undefined,
        update_known: false,
        accept_failures: false,
        list: false,
    };
    const rest = [...argv];
    while (rest.length > 0) {
        const argument = rest.shift();
        switch (argument) {
            case "--json": {
                options.json = rest.shift();

                break;
            }
            case "--update-known": {
                options.update_known = true;

                break;
            }
            case "--accept-failures": {
                options.accept_failures = true;

                break;
            }
            case "--list": {
                options.list = true;

                break;
            }
            case "--help":
            case "-h": {
                options.help = true;

                break;
            }
            default:
                if (argument.startsWith("--")) {
                    throw new Error(`unknown option ${argument}`);
                } else {
                    options.names.push(argument);
                }
        }
    }
    return options;
}

async function load_checks() {
    const directory = path.join(HERE, "checks");
    const files = fs
        .readdirSync(directory)
        .filter((file) => file.endsWith(".mjs"))
        .toSorted();
    const checks = [];
    for (const file of files) {
        const module = await import(path.join(directory, file));
        checks.push(module.default ?? module);
    }
    return checks.toSorted((a, b) => (a.order ?? 100) - (b.order ?? 100));
}

function write_json(target, data) {
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, `${JSON.stringify(data, null, 4)}\n`);
}

function read_known() {
    // Prefer the host copy: it is the one people edit.
    for (const target of keep_targets(KNOWN_FILE).toReversed()) {
        if (fs.existsSync(target)) {
            return JSON.parse(fs.readFileSync(target, "utf8"));
        }
    }
    return {};
}

function print_line(status, id, detail) {
    console.log(`${status.padEnd(10)} ${id}  ${detail}`);
}

async function run_check(browser, check, user_id) {
    const context = new CheckContext(browser, check.name, {user_id});
    const started = Date.now();
    let error;
    try {
        await context.init();
        await check.run(context);
    } catch (error_) {
        error = String(error_?.stack ?? error_)
            .split("\n")
            .slice(0, 4)
            .join(" | ");
    } finally {
        await context.close();
    }
    return {
        name: check.name,
        description: check.description,
        duration_ms: Date.now() - started,
        error,
        assertions: context.assertions,
        measurements: context.measurements,
        env_events: context.env_events,
    };
}

// Everything that changed around a check while it ran, from the snapshot
// before it to the one after, and in its pages.
function env_errors_of(result, before, after, files) {
    return [
        ...result.env_events,
        ...problems(after, SERVED_TREE),
        ...changes(before, after),
        ...describe_changed_files(files),
    ];
}

async function main() {
    const options = parse_arguments(process.argv.slice(2));
    const all_checks = await load_checks();
    if (options.help) {
        console.log(
            "usage: arm check [check names...] [--json <path>] [--update-known] [--accept-failures] [--list]",
        );
        return 0;
    }
    if (options.list) {
        for (const check of all_checks) {
            console.log(`${check.name.padEnd(26)} ${check.description}`);
        }
        return 0;
    }
    const unknown = options.names.filter(
        (name) => !all_checks.some((check) => check.name === name),
    );
    if (unknown.length > 0) {
        console.error(
            `no such check: ${unknown.join(", ")} (have: ${all_checks.map((check) => check.name).join(", ")})`,
        );
        return 2;
    }
    const locked = run_under_lock({
        spawn: spawnSync,
        argv: [process.execPath, ...process.execArgv, ...process.argv.slice(1)],
    });
    if (locked !== undefined) {
        return locked;
    }
    const checks =
        options.names.length > 0
            ? all_checks.filter((check) => options.names.includes(check.name))
            : all_checks;

    const known = read_known();
    const started = new Date();
    const results = [];
    const totals = {pass: 0, known_fail: 0, fail: 0, fixed: 0, error: 0, stale: 0};
    const seen = new Set();
    // Problems of the environment, as {check, problem}; a null check is the
    // run's own.
    const env_errors = [];
    let since = Date.now();
    const health_before = await snapshot({base: BASE, tree: SERVED_TREE, io: HEALTH_IO});
    let health_after = health_before;
    for (const problem of problems(health_before, SERVED_TREE)) {
        env_errors.push({check: null, problem});
        print_line("ENV-ERROR", "before the run", problem);
    }
    let chrome;
    if (env_errors.length === 0) {
        const browser = await launch();
        try {
            chrome = await browser.version();
            let user_id;
            try {
                user_id = await login(browser);
            } catch (error) {
                // No checks' user: nothing this run measures would be about the tree.
                const problem = String(error?.message ?? error);
                env_errors.push({check: null, problem});
                print_line("ENV-ERROR", "login", problem);
            }
            for (const check of user_id === undefined ? [] : checks) {
                console.log(`\n== ${check.name}: ${check.description}`);
                const result = await run_check(browser, check, user_id);
                results.push(result);
                const counts = classify(result, known, seen, print_line);
                for (const [key, value] of Object.entries(counts)) {
                    totals[key] += value;
                }
                if (result.error) {
                    totals.error += 1;
                    print_line("ERROR", check.name, result.error);
                }
                const scan_from = since;
                since = Date.now();
                const now = await snapshot({base: BASE, tree: SERVED_TREE, io: HEALTH_IO});
                const files = now.local ? changed_files(SERVED_TREE, scan_from) : [];
                result.env_errors = env_errors_of(result, health_after, now, files);
                health_after = now;
                for (const problem of result.env_errors) {
                    env_errors.push({check: check.name, problem});
                    print_line("ENV-ERROR", check.name, problem);
                }
                console.log(`   (${(result.duration_ms / 1000).toFixed(1)} s)`);
            }
        } finally {
            await browser.close();
        }
    }

    const stale = stale_ids(known, results, seen);
    if (stale.length > 0) {
        console.log("");
    }
    for (const id of stale) {
        totals.stale += 1;
        print_line("STALE", id, "listed in known.json but no such assertion ran");
    }

    const env_error = env_errors.length > 0;
    if (env_error && (options.update_known || options.accept_failures)) {
        console.log("\nknown.json not rewritten: the run says nothing about the tree (ENV-ERROR)");
    } else if (options.update_known || options.accept_failures) {
        const sorted = rewrite_known(
            known,
            results,
            stale,
            options,
            totals,
            started.toISOString().slice(0, 10),
        );
        for (const target of keep_targets(KNOWN_FILE)) {
            write_json(target, sorted);
        }
        console.log(`\nknown.json rewritten (${Object.keys(sorted).length} entries)`);
    }

    const green =
        !env_error &&
        totals.fail === 0 &&
        totals.fixed === 0 &&
        totals.error === 0 &&
        totals.stale === 0;
    let verdict = green ? "GREEN" : "RED";
    if (env_error) {
        verdict = "ENV-ERROR";
    }
    const report = {
        started: started.toISOString(),
        finished: new Date().toISOString(),
        base: BASE,
        app: APP,
        user: USER,
        viewport: VIEWPORT,
        chrome,
        verdict,
        green,
        totals,
        env_errors,
        health: {served_tree: SERVED_TREE, before: health_before, after: health_after},
        checks: results,
    };
    const report_targets = options.json
        ? [path.resolve(options.json)]
        : keep_targets(DEFAULT_REPORT);
    for (const target of report_targets) {
        write_json(target, report);
    }

    console.log(
        `\n${verdict}: ${totals.pass} pass, ${totals.known_fail} known-fail, ${totals.fail} fail, ${totals.fixed} fixed-not-promoted, ${totals.stale} stale, ${totals.error} check errors, ${env_errors.length} environment problems`,
    );
    if (env_error) {
        console.log(
            "These results say nothing about the tree: the ENV-ERROR lines above say what else changed or was wrong. Run the whole suite again once it is not.",
        );
    }
    console.log(`report: ${report_targets.at(-1)}`);
    if (env_error) {
        return ENV_ERROR_STATUS;
    }
    return green ? 0 : 1;
}

process.exitCode = await main();
