// Armada's sounds, as data and the arithmetic that renders them. One small
// bar of hardwood struck with felt for people's events; the same pitches
// gated on and off for the agent. D major pentatonic. No reverb, no glide,
// nothing below 150 Hz. No audio file is shipped: the application and
// tools/armada/reel both render from this file, and each sound is
// normalised so that its loudest 100 ms sits at its tier.

export const SAMPLE_RATE = 44100;
// Every sound is rendered into a buffer this long, in seconds.
const BUFFER_SECONDS = 0.8;

const NOTE = {
    D4: 293.66,
    F4: 349.23,
    A4: 440,
    D5: 587.33,
    E5: 659.25,
    Fs5: 739.99,
    A5: 880,
    B5: 987.77,
    D6: 1174.66,
};

type Part = {
    // A struck bar, or a gated chip tone.
    voice: "bar" | "chip";
    hertz: number;
    // Decay of the bar, or the gate of the chip, in seconds.
    length: number;
    // When it starts, in seconds, and its level against the other parts.
    start: number;
    decibels: number;
    // A bar may be darkened, and its mallet softened or left out (-90).
    low_pass?: number;
    mallet?: number;
};

export type Recipe = {
    // The level, in dBFS, of its loudest 100 ms.
    tier: number;
    // Which gives way when the limiter is full: the lowest.
    priority: number;
    // Full adds the sounds that are chatter with an agent that answers in
    // 300 ms.
    set: "default" | "full";
    // What I did always sounds, and is never the one that gives way.
    mine: boolean;
    parts: Part[];
};

const bar = (hertz: number, length: number, start = 0, decibels = 0): Part => ({
    voice: "bar",
    hertz,
    length,
    start,
    decibels,
});
const chip = (hertz: number, length: number, start = 0, decibels = 0): Part => ({
    voice: "chip",
    hertz,
    length,
    start,
    decibels,
});

export const sound_names = [
    "send",
    "steer",
    "nope",
    "ask",
    "fail",
    "done",
    "done-short",
    "mention",
    "react",
    "ack",
    "arrive",
] as const;
export type SoundName = (typeof sound_names)[number];

export const recipes: Record<SoundName, Recipe> = {
    // My message leaves; a queued message too.
    send: {tier: -32, priority: 1, set: "default", mine: true, parts: [bar(NOTE.E5, 0.11)]},
    // My message enters a live run.
    steer: {
        tier: -32,
        priority: 1,
        set: "default",
        mine: true,
        parts: [bar(NOTE.E5, 0.11), chip(NOTE.A5, 0.04, 0.055, -8)],
    },
    // An action of mine is refused.
    nope: {
        tier: -32,
        priority: 1,
        set: "default",
        mine: true,
        parts: [
            {...bar(NOTE.D4, 0.06), low_pass: 700, mallet: -10},
            {...bar(NOTE.D4, 0.06, 0.09, -2), low_pass: 700, mallet: -10},
        ],
    },
    // A run of mine needs an answer.
    ask: {
        tier: -22,
        priority: 3,
        set: "default",
        mine: false,
        parts: [bar(NOTE.E5, 0.26), bar(NOTE.B5, 0.3, 0.13)],
    },
    // A run of mine fails.
    fail: {
        tier: -22,
        priority: 3,
        set: "default",
        mine: false,
        parts: [
            {...bar(NOTE.A4, 0.24), low_pass: 1200},
            {...bar(NOTE.F4, 0.28, 0.14, -2), low_pass: 1200},
        ],
    },
    // A run of mine ends, after 30 s or more.
    done: {
        tier: -22,
        priority: 2,
        set: "default",
        mine: false,
        parts: [
            chip(NOTE.D5, 0.045, 0, -6),
            chip(NOTE.Fs5, 0.045, 0.07, -6),
            chip(NOTE.A5, 0.045, 0.14, -6),
            bar(NOTE.D6, 0.42, 0.21),
        ],
    },
    // A run of mine ends sooner. Under a fast exchange it goes quiet.
    "done-short": {
        tier: -32,
        priority: 0,
        set: "default",
        mine: false,
        parts: [chip(NOTE.A5, 0.04, 0, -6), bar(NOTE.D6, 0.2, 0.07)],
    },
    // A mention or a direct message while I am elsewhere.
    mention: {
        tier: -20,
        priority: 3,
        set: "default",
        mine: false,
        parts: [bar(NOTE.D5, 0.38), bar(NOTE.A5, 0.38, 0, -3), bar(NOTE.D6, 0.4, 0.12, -2)],
    },
    // My reaction lands.
    react: {tier: -36, priority: 0, set: "default", mine: true, parts: [bar(NOTE.B5, 0.06)]},
    // A send starts a run.
    ack: {
        tier: -34,
        priority: 0,
        set: "full",
        mine: false,
        parts: [chip(NOTE.D5, 0.04), chip(NOTE.A5, 0.05, 0.06)],
    },
    // A person's message lands in view.
    arrive: {
        tier: -36,
        priority: 0,
        set: "full",
        mine: false,
        parts: [{...bar(NOTE.Fs5, 0.12), mallet: -90}],
    },
};

export function gain_of(decibels: number): number {
    return 10 ** (decibels / 20);
}

// A noise source that is the same on every render, so that a sound is the
// same samples in the application, in the reel and in a test.
function noise(): () => number {
    let seed = 1;
    return () => {
        seed = (seed * 1664525 + 1013904223) % 2 ** 32;
        return seed / 2 ** 31 - 1;
    };
}

function biquad(kind: "low" | "band", hertz: number, q: number): (x: number) => number {
    const w = (2 * Math.PI * hertz) / SAMPLE_RATE;
    const cosine = Math.cos(w);
    const alpha = Math.sin(w) / (2 * q);
    const [b0, b1, b2] =
        kind === "low" ? [(1 - cosine) / 2, 1 - cosine, (1 - cosine) / 2] : [alpha, 0, -alpha];
    const a0 = 1 + alpha;
    const a1 = -2 * cosine;
    const a2 = 1 - alpha;
    let x1 = 0;
    let x2 = 0;
    let y1 = 0;
    let y2 = 0;
    return (x) => {
        const y = (b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
        x2 = x1;
        x1 = x;
        y2 = y1;
        y1 = y;
        return y;
    };
}

// The partials of a free bar: ratio to the fundamental, level, and how
// much sooner than the fundamental each has died.
const BAR_PARTIALS = [
    [1, 0, 1],
    [4, -15, 0.3],
    [9.2, -27, 0.12],
] as const;

function render_bar(part: Part, random: () => number): Float32Array<ArrayBuffer> {
    const samples = new Float32Array(Math.round((part.length + 0.02) * SAMPLE_RATE));
    const partials = BAR_PARTIALS.filter(([ratio]) => ratio * part.hertz < 7000);
    for (const [index] of samples.entries()) {
        const t = index / SAMPLE_RATE;
        const attack = t < 0.003 ? 0.5 - 0.5 * Math.cos((Math.PI * t) / 0.003) : 1;
        let sum = 0;
        for (const [ratio, level, decay] of partials) {
            sum +=
                gain_of(level) *
                Math.sin(2 * Math.PI * part.hertz * ratio * t) *
                Math.exp((-6.9 * t) / (part.length * decay));
        }
        samples[index] = sum * attack;
    }
    const mallet = part.mallet ?? -16;
    if (mallet > -90) {
        // Felt on wood: a short band of noise at the strike.
        const band = biquad("band", Math.min(3000, Math.max(1200, 3 * part.hertz)), 1.2);
        for (let index = 0; index < Math.round(0.02 * SAMPLE_RATE); index += 1) {
            const t = index / SAMPLE_RATE;
            samples[index]! +=
                gain_of(mallet) *
                3 *
                band(random()) *
                Math.min(1, t / 0.001) *
                Math.exp((-6.9 * t) / 0.014);
        }
    }
    if (part.low_pass !== undefined) {
        const low = biquad("low", part.low_pass, 0.7);
        for (const [index, sample] of samples.entries()) {
            samples[index] = low(sample);
        }
    }
    return samples;
}

// The agent's voice: the same pitch as a gated tone, a triangle with a
// little square in it, softened.
function render_chip(part: Part): Float32Array<ArrayBuffer> {
    const samples = new Float32Array(Math.round((part.length + 0.02) * SAMPLE_RATE));
    const low = biquad("low", 2200, 0.7);
    for (const [index] of samples.entries()) {
        const t = index / SAMPLE_RATE;
        const phase = (part.hertz * t) % 1;
        const triangle = 4 * Math.abs(phase - 0.5) - 1;
        const square = phase < 0.5 ? 1 : -1;
        const gate =
            t < 0.002
                ? t / 0.002
                : t < part.length
                  ? 1
                  : Math.max(0, 1 - (t - part.length) / 0.012);
        samples[index] = low((triangle + gain_of(-16) * square) * gate);
    }
    return samples;
}

// The root mean square of the loudest 100 ms of a buffer.
export function loudest_100ms(buffer: Float32Array): number {
    const window = Math.round(0.1 * SAMPLE_RATE);
    let best = 0;
    let energy = 0;
    for (const [index, sample] of buffer.entries()) {
        energy += sample * sample;
        if (index >= window) {
            energy -= buffer[index - window]! ** 2;
        }
        best = Math.max(best, energy);
    }
    return Math.sqrt(best / window);
}

// One sound as samples, at its tier.
export function render(name: SoundName): Float32Array<ArrayBuffer> {
    const recipe = recipes[name];
    const random = noise();
    const buffer = new Float32Array(Math.round(BUFFER_SECONDS * SAMPLE_RATE));
    for (const part of recipe.parts) {
        const samples = part.voice === "bar" ? render_bar(part, random) : render_chip(part);
        const offset = Math.round(part.start * SAMPLE_RATE);
        const gain = gain_of(part.decibels);
        for (const [index, sample] of samples.entries()) {
            if (offset + index < buffer.length) {
                buffer[offset + index]! += sample * gain;
            }
        }
    }
    const gain = gain_of(recipe.tier) / loudest_100ms(buffer);
    for (const [index, sample] of buffer.entries()) {
        buffer[index] = sample * gain;
    }
    return buffer;
}

export type Measured = {
    name: SoundName;
    set: Recipe["set"];
    tier: number;
    // The loudest 100 ms and the peak, in dBFS, and how long it sounds.
    rms100: number;
    peak: number;
    milliseconds: number;
};

const decibels_of = (amplitude: number): number => 20 * Math.log10(amplitude);

// What each sound measures: the figures the reel prints and the tests
// hold to the tiers.
export function table(): Measured[] {
    return sound_names.map((name) => {
        const buffer = render(name);
        let peak = 0;
        for (const sample of buffer) {
            peak = Math.max(peak, Math.abs(sample));
        }
        const last = buffer.findLastIndex((sample) => Math.abs(sample) > peak * 0.001);
        return {
            name,
            set: recipes[name].set,
            tier: recipes[name].tier,
            rms100: Number(decibels_of(loudest_100ms(buffer)).toFixed(2)),
            peak: Number(decibels_of(peak).toFixed(2)),
            milliseconds: Math.round((last / SAMPLE_RATE) * 1000),
        };
    });
}
