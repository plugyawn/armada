// Whether a run's results say anything about the tree. A result must be a
// function of the tree and the recipe only (design/r6/harness.md, 2): when
// anything else changed while the checks ran, the run reports ENV-ERROR and
// is rerun, never counted red or green.
//
// Looked at before the run and after every check:
// - the server answers /health (database, RabbitMQ, Redis, memcached);
// - when the server runs on this machine, from the tree it serves: Django,
//   Tornado and the queue processor are running, none is a zombie, and none
//   restarted (a save makes Django and the queue processor reload); the
//   machine did not reboot; disk and memory are not about to run out; no
//   source file of that tree changed;
// - in the pages (lib/harness.mjs): no 429 and no 5xx the check did not
//   answer itself, no lost event queue (Tornado restarts in the same
//   process, so only its clients see it), and no event caused by another
//   person (a message, an edit, a reaction, typing) reached the checks'
//   user.

import * as fs from "node:fs";
import path from "node:path";

export const MIN_FREE_DISK = 2 * 1024 ** 3;
export const MIN_AVAILABLE_MEMORY = 512 * 1024 ** 2;

// The server's processes, by what their command line runs.
export const ROLES = {
    django: /manage\.py (?:rundjangoserver|runserver)\b/,
    tornado: /manage\.py runtornado\b/,
    queue: /manage\.py process_queue\b/,
};

// The tree's source: what Django, Tornado and webpack reload from. Entries
// with these names are skipped wherever they are; the kit's accepted
// pictures are written by a run that accepts new ones.
export const WATCHED = [
    "analytics",
    "confirmation",
    "corporate",
    "templates",
    "tools/armada-checks",
    "web",
    "zerver",
    "zilencer",
    "zproject",
];
const SKIPPED_NAMES = new Set(["__pycache__", "node_modules", ".git"]);
const SKIPPED_PATHS = new Set(["tools/armada-checks/baseline"]);

export function is_local(base) {
    const {hostname} = new URL(base);
    return (
        ["localhost", "127.0.0.1", "[::1]", "zulipdev.com"].includes(hostname) ||
        hostname.endsWith(".localhost") ||
        hostname.endsWith(".zulipdev.com")
    );
}

function read(file) {
    try {
        return fs.readFileSync(file, "utf8");
    } catch {
        return null;
    }
}

// The server processes running from `tree`: [{role, pid, start, state}].
export function server_processes(proc, tree) {
    const found = [];
    for (const name of fs.readdirSync(proc)) {
        if (!/^\d+$/.test(name)) {
            continue;
        }
        const command = read(path.join(proc, name, "cmdline"))?.replaceAll("\0", " ") ?? "";
        const role = Object.keys(ROLES).find((key) => ROLES[key].test(command));
        if (!role) {
            continue;
        }
        let cwd;
        try {
            cwd = fs.readlinkSync(path.join(proc, name, "cwd"));
        } catch {
            continue;
        }
        const stat = read(path.join(proc, name, "stat"));
        if (cwd !== tree || stat === null) {
            continue;
        }
        // After the command's closing parenthesis: state, ppid, ... and the
        // start time, the 22nd field of the line.
        const fields = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
        found.push({role, pid: Number(name), start: fields[19], state: fields[0]});
    }
    return found.toSorted((a, b) => a.pid - b.pid);
}

// Every file and directory under the watched parts of `tree` whose inode
// changed after `since` (ms). A changed time is what a save, a sync (which
// keeps the edit's own modification time) and a deletion all leave.
export function changed_files(tree, since, watched = WATCHED) {
    const changed = [];
    const visit = (relative) => {
        let stats;
        try {
            stats = fs.lstatSync(path.join(tree, relative));
        } catch {
            return;
        }
        if (stats.ctimeMs > since) {
            changed.push(relative);
        }
        if (!stats.isDirectory()) {
            return;
        }
        for (const name of fs.readdirSync(path.join(tree, relative)).toSorted()) {
            const child = path.join(relative, name);
            if (!SKIPPED_NAMES.has(name) && !SKIPPED_PATHS.has(child)) {
                visit(child);
            }
        }
    };
    for (const relative of watched) {
        visit(relative);
    }
    return changed;
}

export function meminfo_available(text) {
    const match = /^MemAvailable:\s+(\d+) kB$/m.exec(text ?? "");
    return match ? Number(match[1]) * 1024 : null;
}

// What the environment looks like now. `io` holds what touches the outside
// world: {fetch, proc, statfs}.
export async function snapshot({base, tree, local = is_local(base), io}) {
    let health;
    try {
        const response = await io.fetch(`${base}/health`, {signal: AbortSignal.timeout(10000)});
        health = response.status;
    } catch (error) {
        health = String(error?.message ?? error);
    }
    if (!local) {
        return {local, health};
    }
    const disk = io.statfs(tree);
    return {
        local,
        health,
        boot_id: read(path.join(io.proc, "sys/kernel/random/boot_id"))?.trim() ?? null,
        processes: server_processes(io.proc, tree),
        free_disk: disk.bavail * disk.bsize,
        available_memory: meminfo_available(read(path.join(io.proc, "meminfo"))),
    };
}

const gib = (bytes) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;

// What is wrong with the environment in one snapshot.
export function problems(now, tree) {
    const found = [];
    if (now.health !== 200) {
        found.push(`the server's /health answered ${now.health}`);
    }
    if (!now.local) {
        return found;
    }
    for (const role of Object.keys(ROLES)) {
        const running = now.processes.filter((entry) => entry.role === role);
        if (running.length === 0) {
            found.push(
                `no ${role} process runs from ${tree} (ARMADA_SERVED_TREE names the tree the server runs from)`,
            );
        }
        for (const entry of running.filter((candidate) => candidate.state === "Z")) {
            found.push(`the ${role} process ${entry.pid} is a zombie`);
        }
    }
    if (now.free_disk < MIN_FREE_DISK) {
        found.push(`only ${gib(now.free_disk)} of disk is free`);
    }
    if (now.available_memory !== null && now.available_memory < MIN_AVAILABLE_MEMORY) {
        found.push(`only ${gib(now.available_memory)} of memory is available`);
    }
    return found;
}

// What changed between two snapshots that a check could not have caused.
export function changes(before, after) {
    if (!before.local || !after.local) {
        return [];
    }
    const found = [];
    if (before.boot_id !== after.boot_id) {
        found.push("the machine rebooted");
    }
    for (const role of Object.keys(ROLES)) {
        const identity = (snap) =>
            snap.processes
                .filter((entry) => entry.role === role)
                .map((entry) => `${entry.pid}@${entry.start}`)
                .join(",");
        if (identity(before) !== identity(after)) {
            found.push(
                `the ${role} processes restarted (${identity(before)} -> ${identity(after)})`,
            );
        }
    }
    return found;
}

export function describe_changed_files(files) {
    if (files.length === 0) {
        return [];
    }
    const shown = files.slice(0, 5).join(", ");
    const more = files.length > 5 ? ` and ${files.length - 5} more` : "";
    return [`the served tree changed during the check: ${shown}${more}`];
}

// A response a check did not answer itself that says the server was not
// all the run's: someone else's traffic used up the rate limit, or the
// server failed.
export function response_problem(status, url) {
    if (status === 400 && url.startsWith("/json/events")) {
        // Tornado's autoreloader restarts it in the same process, so only
        // the page sees it: its event queue is gone.
        return `400 for ${url}: the server lost the page's event queue (Tornado restarted)`;
    }
    if (status === 429) {
        return `429 (rate limited) for ${url}: someone else's traffic as this user`;
    }
    if (status >= 500) {
        return `${status} from the server for ${url}`;
    }
    return null;
}

// Events in a /json/events response that another person caused, as lines
// for the report. `user_id` is the checks' own user.
export function foreign_events(events, user_id) {
    const found = [];
    for (const event of events ?? []) {
        let actor = null;
        let what = event.type;
        switch (event.type) {
            case "message": {
                const recipient = event.message?.display_recipient;
                actor = event.message?.sender_id;
                what =
                    typeof recipient === "string"
                        ? `a message in ${recipient} > ${event.message.subject}`
                        : "a direct message";
                break;
            }
            case "update_message":
            case "reaction": {
                actor = event.user_id;
                break;
            }
            case "typing": {
                actor = event.sender?.user_id;
                break;
            }
            // No default: other events are nobody's doing.
        }
        if (typeof actor === "number" && actor !== user_id) {
            found.push(`${what} from user ${actor}`);
        }
    }
    return found;
}
