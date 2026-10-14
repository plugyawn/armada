// The sound engine: when a sound may fire, and the limiter. The engine
// does not exist while muted and is not created before the first gesture.
// The gesture only creates the context: each sound is rendered once, in
// idle time after it, or when it is first played if that comes sooner, so
// that the person's first key or press does no audio work.
//
// The limiter:
// - What I did always sounds: send, steer, nope and my reaction are never
//   refused, whatever else is playing.
// - Of everything else, at most 3 in any 2 s. When a fourth is due, the
//   lowest priority gives way: a newcomer that is the lowest is not played;
//   otherwise the lowest one still sounding is faded out and the newcomer
//   plays. My own sounds are not counted.
// - Under a fast exchange it is the consequent that goes quiet, on a rule
//   the person can predict: from the third send inside 4 s, done-short is
//   silent until there have been 4 s without a send.
// - Repeats of my own send inside 4 s fall 4 dB each, to a floor of -8.
// - A sound that cannot start within 30 ms of its event is not played at
//   all (a suspended context, a long task). Nothing is queued.

import {SAMPLE_RATE, gain_of, recipes, render, sound_names} from "./recipes.ts";
import type {SoundName} from "./recipes.ts";

// Seconds and milliseconds of the rules above.
const WINDOW_MS = 2000;
const WINDOW_SOUNDS = 3;
const EXCHANGE_MS = 4000;
const REPEAT_STEP_DB = -4;
const REPEAT_FLOOR_DB = -8;
const LATE_MS = 30;
const TAIL_MS = 400;
const LOG_LINES = 400;
const IDLE_FALLBACK_MS = 50;

type Sounding = {
    name: SoundName;
    at: number;
    priority: number;
    source: AudioBufferSourceNode;
    gain: GainNode;
    ends: number;
    cut: boolean;
};

export type LogLine = {
    name: SoundName;
    at: number;
    played: boolean;
    // Why it was not played, or what it gave way to.
    why: string;
};

let context: AudioContext | null = null;
let buffers = new Map<SoundName, AudioBuffer>();
let muted = false;
let full = false;
let level_db = 0;
let sends: number[] = [];
let exchanges: number[] = [];
let recent: Sounding[] = [];
let last_send = 0;
let hushed = false;
let lines: LogLine[] = [];

function note(name: SoundName, at: number, why = ""): boolean {
    lines.push({name, at: Math.round(at), played: why === "", why});
    if (lines.length > LOG_LINES) {
        lines.shift();
    }
    return why === "";
}

function buffer_of(audio: AudioContext, name: SoundName): AudioBuffer {
    let buffer = buffers.get(name);
    if (buffer === undefined) {
        const samples = render(name);
        buffer = audio.createBuffer(1, samples.length, SAMPLE_RATE);
        buffer.copyToChannel(samples, 0);
        buffers.set(name, buffer);
    }
    return buffer;
}

// One sound a slice of idle time, in the order of the table (my own
// first), until all are rendered or this context is gone.
function render_when_idle(audio: AudioContext): void {
    const pending = sound_names.find((name) => !buffers.has(name));
    const next = pending;
    if (context !== audio || next === undefined) {
        return;
    }
    const later = (): void => {
        if (context === audio) {
            buffer_of(audio, next);
            render_when_idle(audio);
        }
    };
    if (typeof requestIdleCallback === "function") {
        requestIdleCallback(later);
    } else {
        // Safari has no idle callback: a task after the gesture's.
        setTimeout(later, IDLE_FALLBACK_MS);
    }
}

function create(): void {
    context = new AudioContext({sampleRate: SAMPLE_RATE});
    buffers = new Map();
    render_when_idle(context);
}

// Sound is on by default in the product; one control mutes everything.
// Unmuting from a gesture creates the engine at once.
export function set_muted(value: boolean, options: {gesture?: boolean} = {}): void {
    muted = value;
    if (muted) {
        void context?.close();
        context = null;
        buffers = new Map();
        return;
    }
    if (options.gesture && context === null) {
        create();
    }
}

export function is_muted(): boolean {
    return muted;
}

// Full adds the sounds that are chatter by default: ack and arrive.
export function set_full(value: boolean): void {
    full = value;
}

// A level over the tiers, in decibels: the audition's +12, so that laptop
// speakers carry it. The shipped level is 0.
export function set_level(decibels: number): void {
    level_db = decibels;
}

// The first gesture creates the engine, unless sound is muted.
export function arm(root: Document = document): void {
    const wake = (): void => {
        if (!muted && context === null) {
            create();
        }
        if (context !== null && context.state !== "running") {
            void context.resume();
        }
    };
    root.addEventListener("pointerdown", wake, {capture: true, once: true});
    root.addEventListener("keydown", wake, {capture: true, once: true});
}

export function state(): AudioContextState | "none" {
    return context?.state ?? "none";
}

export function log(): LogLine[] {
    return lines;
}

// Forget what has played: a new conversation with the limiter.
export function clear(): void {
    lines = [];
    sends = [];
    exchanges = [];
    recent = [];
    last_send = 0;
    hushed = false;
}

const is_send = (name: SoundName): boolean => name === "send" || name === "steer";

// `at` is the time of the event that caused it (performance.now()); the
// sound starts at `at` plus `delay` seconds, or not at all.
export function play(name: SoundName, delay = 0, at: number = performance.now()): boolean {
    const recipe = recipes[name];
    if (muted || context === null || (recipe.set === "full" && !full)) {
        return false;
    }
    const now = performance.now();
    if (is_send(name)) {
        if (last_send !== 0 && now - last_send > EXCHANGE_MS) {
            exchanges = [];
            hushed = false;
        }
        last_send = now;
        exchanges = exchanges.filter((time) => now - time < EXCHANGE_MS);
        exchanges.push(now);
        if (exchanges.length > 2) {
            hushed = true;
        }
    } else if (hushed && now - last_send > EXCHANGE_MS) {
        hushed = false;
    }
    if (context.state !== "running") {
        void context.resume();
        return note(name, now, `context ${context.state}`);
    }
    if (now - at > LATE_MS) {
        return note(name, now, `late ${Math.round(now - at)} ms`);
    }
    if (name === "done-short" && hushed) {
        return note(name, now, "fast exchange");
    }
    recent = recent.filter((sounding) => now - sounding.at < WINDOW_MS && !sounding.cut);
    if (!recipe.mine && recent.length >= WINDOW_SOUNDS) {
        let lowest = recent[0]!;
        for (const sounding of recent) {
            if (
                sounding.priority < lowest.priority ||
                (sounding.priority === lowest.priority && sounding.at < lowest.at)
            ) {
                lowest = sounding;
            }
        }
        if (recipe.priority <= lowest.priority) {
            return note(name, now, "limiter: lowest priority");
        }
        if (lowest.ends > now) {
            lowest.gain.gain.setTargetAtTime(0, context.currentTime, 0.004);
            lowest.source.stop(context.currentTime + 0.03);
            note(lowest.name, now, `gave way to ${name}`);
        }
        lowest.cut = true;
    }
    let repeat_db = 0;
    if (is_send(name)) {
        sends = sends.filter((time) => now - time < EXCHANGE_MS);
        repeat_db = Math.max(REPEAT_FLOOR_DB, REPEAT_STEP_DB * sends.length);
        sends.push(now);
    }
    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer_of(context, name);
    gain.gain.value = gain_of(level_db + repeat_db);
    source.connect(gain).connect(context.destination);
    source.start(context.currentTime + delay + Math.max(0, 0.005 - (now - at) / 1000));
    if (!recipe.mine) {
        recent.push({
            name,
            at: now,
            priority: recipe.priority,
            source,
            gain,
            ends: now + delay * 1000 + TAIL_MS,
            cut: false,
        });
    }
    return note(name, now);
}
