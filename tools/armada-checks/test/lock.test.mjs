import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import path from "node:path";
import {afterEach, beforeEach, test} from "node:test";

import {
    DEFAULT_LOCK,
    ENV_ERROR_STATUS,
    LOCK_TIMEOUT_STATUS,
    held_by_lineage,
    lineage,
    lock_holders,
    lock_key,
    run_under_lock,
} from "../lib/lock.mjs";

let root;
let proc;
let lock;

// A /proc of four processes, 1 <- 40 <- 41 <- 42, where 40 holds `lock`.
function fake_proc(holder) {
    const stats = fs.statSync(lock);
    const key = lock_key(stats.dev, stats.ino);
    fs.writeFileSync(
        path.join(proc, "locks"),
        [
            "1: POSIX  ADVISORY  WRITE 7 fd:01:99 0 EOF",
            `2: FLOCK  ADVISORY  WRITE ${holder} ${key} 0 EOF`,
            `2: -> FLOCK  ADVISORY  WRITE 41 ${key} 0 EOF`,
            "",
        ].join("\n"),
    );
    for (const [pid, parent] of [
        [1, 0],
        [40, 1],
        [41, 40],
        [42, 41],
    ]) {
        fs.mkdirSync(path.join(proc, String(pid)), {recursive: true});
        fs.writeFileSync(
            path.join(proc, String(pid), "stat"),
            `${pid} (node (worker) x) S ${parent} ${pid} 0 0 -1\n`,
        );
    }
}

beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), "armada-checks-lock-"));
    proc = path.join(root, "proc");
    lock = path.join(root, "check.lock");
    fs.mkdirSync(proc);
    fs.writeFileSync(lock, "");
});

afterEach(() => {
    fs.rmSync(root, {recursive: true});
});

test("lock_key prints the device the way /proc/locks does", () => {
    // makedev(253, 1) and makedev(0, 300), as glibc encodes them: the
    // minor's low byte in bits 0-7, the major in bits 8-19, the rest of the
    // minor from bit 20.
    assert.equal(lock_key(253 * 256 + 1, 3386), "fd:01:3386");
    assert.equal(lock_key(256 * 4096 + 44, 7), "00:12c:7");
});

test("lock_holders reads the holders of one file, not its waiters", () => {
    const text = [
        "1: FLOCK  ADVISORY  WRITE 10 fd:01:5 0 EOF",
        "1: -> FLOCK  ADVISORY  WRITE 11 fd:01:5 0 EOF",
        "2: FLOCK  ADVISORY  WRITE 12 FD:01:6 0 EOF",
        "3: POSIX  ADVISORY  WRITE 13 fd:01:5 0 EOF",
        "",
    ].join("\n");
    assert.deepEqual(lock_holders(text, "fd:01:5"), [10]);
    assert.deepEqual(lock_holders(text, "fd:01:6"), [12]);
});

test("lineage walks up the parents and stops where /proc does", () => {
    fake_proc(40);
    assert.deepEqual(lineage(proc, 42), [42, 41, 40, 1]);
    assert.deepEqual(lineage(proc, 99), [99]);
    // A loop in a damaged /proc ends too.
    fs.writeFileSync(path.join(proc, "1", "stat"), "1 (init) S 42 1 0\n");
    assert.deepEqual(lineage(proc, 42), [42, 41, 40, 1]);
});

test("held_by_lineage finds the lock held by an ancestor only", () => {
    fake_proc(40);
    assert.equal(held_by_lineage({lock, proc, pid: 42}), true);
    fake_proc(77);
    assert.equal(held_by_lineage({lock, proc, pid: 42}), false);
    assert.equal(held_by_lineage({lock: path.join(root, "missing.lock"), proc, pid: 42}), false);
    assert.equal(held_by_lineage({lock, proc: path.join(root, "no-proc"), pid: 42}), false);
});

function fake_spawn(results) {
    const calls = [];
    const spawn = (command, args, options) => {
        calls.push({command, args, options});
        return results.shift();
    };
    return {spawn, calls};
}

test("run_under_lock goes on when an ancestor holds the lock", () => {
    fake_proc(40);
    const {spawn, calls} = fake_spawn([]);
    const status = run_under_lock({
        spawn,
        argv: ["node", "run.mjs"],
        env: {ARMADA_CHECKS_LOCK: lock},
        proc,
        pid: 42,
    });
    assert.equal(status, undefined);
    assert.deepEqual(calls, []);
});

test("run_under_lock runs the script again under flock and returns its status", () => {
    const lines = [];
    const {spawn, calls} = fake_spawn([{status: 0}, {status: 1}]);
    const status = run_under_lock({
        spawn,
        argv: ["node", "run.mjs", "kit"],
        env: {ARMADA_CHECKS_LOCK: lock, ARMADA_CHECKS_LOCK_WAIT: "5"},
        proc,
        pid: 42,
        print: (line) => lines.push(line),
    });
    assert.equal(status, 1);
    assert.deepEqual(lines, []);
    assert.deepEqual(calls[0].args, ["-n", "-E", "75", lock, "true"]);
    assert.deepEqual(calls[1].args, ["-w", "5", "-E", "75", lock, "node", "run.mjs", "kit"]);
    assert.deepEqual(calls[1].options, {stdio: "inherit"});
});

test("run_under_lock says it waits, and gives up as ENV-ERROR", () => {
    const lines = [];
    const {spawn, calls} = fake_spawn([
        {status: LOCK_TIMEOUT_STATUS},
        {status: LOCK_TIMEOUT_STATUS},
    ]);
    const status = run_under_lock({
        spawn,
        argv: ["node", "prove.mjs"],
        env: {},
        proc,
        pid: 42,
        print: (line) => lines.push(line),
    });
    assert.equal(status, ENV_ERROR_STATUS);
    assert.equal(calls[1].args[1], "3600");
    assert.equal(calls[1].args[4], DEFAULT_LOCK);
    assert.deepEqual(lines, [
        `waiting for ${DEFAULT_LOCK}: another run of the checks or the proof holds it`,
        `ENV-ERROR: ${DEFAULT_LOCK} was still held after 3600 s (ARMADA_CHECKS_LOCK_WAIT)`,
    ]);
});

test("run_under_lock without flock, or killed, is ENV-ERROR", () => {
    const lines = [];
    const missing = fake_spawn([{error: {code: "ENOENT"}}]);
    assert.equal(
        run_under_lock({
            spawn: missing.spawn,
            argv: [],
            env: {ARMADA_CHECKS_LOCK: lock},
            proc,
            print: (line) => lines.push(line),
        }),
        ENV_ERROR_STATUS,
    );
    assert.deepEqual(lines, [`ENV-ERROR: cannot take ${lock}: flock(1) is not available (ENOENT)`]);
    const killed = fake_spawn([{status: 0}, {status: null, signal: "SIGTERM"}]);
    assert.equal(
        run_under_lock({spawn: killed.spawn, argv: [], env: {ARMADA_CHECKS_LOCK: lock}, proc}),
        ENV_ERROR_STATUS,
    );
});
