// One run of the checks or of the proof at a time: a run takes
// /tmp/armada-check.lock itself (ARMADA_CHECKS_LOCK names another), by
// running itself again under flock(1). Long runs side by side once took the
// development server down, and two runs share its database and its users.
//
// A run started under the lock already (`flock /tmp/armada-check.lock arm
// check`, or run.mjs started by prove.mjs) finds the lock held by one of its
// ancestors in /proc/locks and goes on without taking it again, which would
// wait for itself for ever.

import * as fs from "node:fs";
import path from "node:path";

export const DEFAULT_LOCK = "/tmp/armada-check.lock";

// flock's exit status when it gives up waiting, and the runner's for a
// result that says nothing about the tree (ENV-ERROR).
export const LOCK_TIMEOUT_STATUS = 75;
export const ENV_ERROR_STATUS = 3;

// The device and inode a /proc/locks line names, as "major:minor:inode"
// with the device numbers in hex, the way the kernel prints them.
export function lock_key(dev, ino) {
    const major = Math.floor(dev / 256) % 4096;
    const minor = (dev % 256) + Math.floor(dev / 1048576) * 256;
    return `${major.toString(16).padStart(2, "0")}:${minor.toString(16).padStart(2, "0")}:${ino}`;
}

// Pids holding a lock on `key` ("major:minor:inode"), from /proc/locks.
export function lock_holders(locks_text, key) {
    const holders = [];
    for (const line of locks_text.split("\n")) {
        const fields = line.trim().split(/\s+/);
        // "1: FLOCK  ADVISORY  WRITE 4242 fd:01:1234 0 EOF"; a waiter's
        // line starts "1: -> FLOCK".
        if (fields[1] === "FLOCK" && fields[5]?.toLowerCase() === key.toLowerCase()) {
            holders.push(Number(fields[4]));
        }
    }
    return holders;
}

// This process and its ancestors, nearest first, from /proc/<pid>/stat.
export function lineage(proc, pid) {
    const pids = [];
    let current = pid;
    while (current > 0 && !pids.includes(current)) {
        pids.push(current);
        let stat;
        try {
            stat = fs.readFileSync(path.join(proc, String(current), "stat"), "utf8");
        } catch {
            break;
        }
        // "pid (command) state ppid ...": the command may hold spaces and
        // parentheses, so read after the last one.
        current = Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1]);
    }
    return pids;
}

export function held_by_lineage({lock, proc = "/proc", pid = process.pid}) {
    let stats;
    let locks_text;
    try {
        stats = fs.statSync(lock);
        locks_text = fs.readFileSync(path.join(proc, "locks"), "utf8");
    } catch {
        return false;
    }
    const holders = new Set(lock_holders(locks_text, lock_key(stats.dev, stats.ino)));
    return lineage(proc, pid).some((ancestor) => holders.has(ancestor));
}

// Run this script again under the lock unless an ancestor holds it. Returns
// undefined when the caller should go on (it holds the lock), else the exit
// status to end with. `spawn` is node's spawnSync.
export function run_under_lock({
    spawn,
    argv,
    env = process.env,
    proc = "/proc",
    pid = process.pid,
    print = console.log,
}) {
    const lock = env.ARMADA_CHECKS_LOCK ?? DEFAULT_LOCK;
    if (held_by_lineage({lock, proc, pid})) {
        return undefined;
    }
    const wait = env.ARMADA_CHECKS_LOCK_WAIT ?? "3600";
    const busy = spawn("flock", ["-n", "-E", String(LOCK_TIMEOUT_STATUS), lock, "true"]);
    if (busy.error) {
        print(`ENV-ERROR: cannot take ${lock}: flock(1) is not available (${busy.error.code})`);
        return ENV_ERROR_STATUS;
    }
    if (busy.status === LOCK_TIMEOUT_STATUS) {
        print(`waiting for ${lock}: another run of the checks or the proof holds it`);
    }
    const {status} = spawn(
        "flock",
        ["-w", wait, "-E", String(LOCK_TIMEOUT_STATUS), lock, ...argv],
        {stdio: "inherit"},
    );
    if (status === LOCK_TIMEOUT_STATUS) {
        print(`ENV-ERROR: ${lock} was still held after ${wait} s (ARMADA_CHECKS_LOCK_WAIT)`);
        return ENV_ERROR_STATUS;
    }
    return status ?? ENV_ERROR_STATUS;
}
