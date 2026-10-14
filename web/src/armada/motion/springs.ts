// The motion tokens, read once from the stylesheet: a duration or a spring
// exists in tokens/tokens.css and nowhere else. Also the spring itself, in
// closed form, so script can sample one from any displacement and speed.

export const spring_names = ["touch", "move", "settle", "land"] as const;
export type SpringName = (typeof spring_names)[number];

export type Spring = {
    // Seconds for one undamped period; the damping ratio; the duration, in
    // milliseconds, after which it is at rest; and the same spring as a
    // CSS timing function, for a one-shot that starts from rest.
    response: number;
    damping: number;
    duration: number;
    css: string;
};

const TIME_NAMES = [
    "fade-in",
    "fade-out",
    "trail",
    "step",
    "burst",
    "shake",
    "tip-delay",
    "tip-linger",
    "caret-hold",
    "caret-period",
    "pending",
    "quiet-soft",
    "quiet",
    "overlay-rest",
    "mod-hold",
    "announce",
] as const;
const AMOUNT_NAMES = ["rise", "pane-rise", "dip", "from", "follow", "ring-near"] as const;

export type TimeName = (typeof TIME_NAMES)[number];
export type AmountName = (typeof AMOUNT_NAMES)[number];

// Before the tokens are read nothing moves: a release finds no time to
// take and leaves the state where it was written.
const UNREAD = 0;
const NO_SPRING: Spring = {response: 1, damping: 1, duration: UNREAD, css: ""};

export const springs: Record<SpringName, Spring> = {
    touch: NO_SPRING,
    move: NO_SPRING,
    settle: NO_SPRING,
    land: NO_SPRING,
};
// Milliseconds.
export const times: Record<TimeName, number> = {
    "fade-in": UNREAD,
    "fade-out": UNREAD,
    trail: UNREAD,
    step: UNREAD,
    burst: UNREAD,
    shake: UNREAD,
    "tip-delay": UNREAD,
    "tip-linger": UNREAD,
    "caret-hold": UNREAD,
    "caret-period": UNREAD,
    pending: UNREAD,
    "quiet-soft": UNREAD,
    quiet: UNREAD,
    "overlay-rest": UNREAD,
    "mod-hold": UNREAD,
    announce: UNREAD,
};
// Pixels, or a ratio.
export const amounts: Record<AmountName, number> = {
    rise: UNREAD,
    "pane-rise": UNREAD,
    dip: UNREAD,
    from: UNREAD,
    follow: UNREAD,
    "ring-near": UNREAD,
};

// A CSS time as milliseconds, whether the token is written in
// milliseconds or in seconds.
export function milliseconds(value: string): number {
    const number = Number.parseFloat(value);
    return /\ds\s*$/.test(value) && !/ms\s*$/.test(value) ? number * 1000 : number;
}

export type TokenSource = {getPropertyValue: (name: string) => string};

export function read(source: TokenSource): void {
    const text = (name: string): string => source.getPropertyValue(`--${name}`).trim();
    for (const name of spring_names) {
        springs[name] = {
            response: Number.parseFloat(text(`${name}-r`)),
            damping: Number.parseFloat(text(`${name}-z`)),
            duration: milliseconds(text(`${name}-t`)),
            css: text(`spring-${name}`),
        };
    }
    for (const name of TIME_NAMES) {
        times[name] = milliseconds(text(name));
    }
    for (const name of AMOUNT_NAMES) {
        amounts[name] = Number.parseFloat(text(name));
    }
}

// Read the tokens as they stand now: at startup, and again when the
// person's motion preference changes.
export function load(): void {
    read(getComputedStyle(document.documentElement));
}

// Reduced motion collapses the spring durations to 1 ms; the fades stay.
export function reduced(): boolean {
    return !(springs.move.duration > 1);
}

export type Vector = Record<string, number>;

export type Flight = {
    // Angular frequency and damping ratio, and where each channel stood
    // and how fast it moved when the spring was let go.
    omega: number;
    damping: number;
    from: Vector;
    velocity: Vector;
};

export function flight(spring: Spring, from: Vector, velocity: Vector): Flight {
    return {omega: (2 * Math.PI) / spring.response, damping: spring.damping, from, velocity};
}

// Displacement and velocity of every channel, `seconds` after release.
// Critically damped or underdamped, in closed form: rest is 0.
export function sample(spring: Flight, seconds: number): [Vector, Vector] {
    const position: Vector = {};
    const velocity: Vector = {};
    const {omega, damping} = spring;
    for (const [channel, start] of Object.entries(spring.from)) {
        const speed = spring.velocity[channel] ?? 0;
        if (damping >= 1) {
            const slope = speed + omega * start;
            const decay = Math.exp(-omega * seconds);
            position[channel] = (start + slope * seconds) * decay;
            velocity[channel] = (slope - omega * (start + slope * seconds)) * decay;
        } else {
            const damped = omega * Math.sqrt(1 - damping * damping);
            const swing = (speed + damping * omega * start) / damped;
            const decay = Math.exp(-damping * omega * seconds);
            const cosine = Math.cos(damped * seconds);
            const sine = Math.sin(damped * seconds);
            position[channel] = decay * (start * cosine + swing * sine);
            velocity[channel] =
                decay *
                (-damping * omega * (start * cosine + swing * sine) -
                    start * damped * sine +
                    swing * damped * cosine);
        }
    }
    return [position, velocity];
}

// How far a spring released from rest at 1 has come, at a fraction of its
// duration: what its CSS timing function says at that point.
export function progress(spring: Spring, fraction: number): number {
    if (fraction >= 1) {
        return 1;
    }
    const [position] = sample(flight(spring, {x: 1}, {x: 0}), (spring.duration / 1000) * fraction);
    return 1 - position["x"]!;
}
