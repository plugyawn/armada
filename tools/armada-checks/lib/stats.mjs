// Small numeric helpers. Times are noisy on a shared development machine:
// report medians of repeated samples, never a single reading.

export function round(value, digits = 2) {
    if (value === null || value === undefined || !Number.isFinite(value)) {
        return null;
    }
    const scale = 10 ** digits;
    return Math.round(value * scale) / scale;
}

export function quantile(values, fraction) {
    const sorted = values.filter((value) => Number.isFinite(value)).toSorted((a, b) => a - b);
    if (sorted.length === 0) {
        return null;
    }
    return sorted[Math.min(sorted.length - 1, Math.floor(fraction * sorted.length))];
}

export function median(values) {
    return quantile(values, 0.5);
}

export function sum(values) {
    let total = 0;
    for (const value of values) {
        total += value;
    }
    return total;
}

export function max(values) {
    const finite = values.filter((value) => Number.isFinite(value));
    return finite.length === 0 ? null : Math.max(...finite);
}

// Largest change of any edge or dimension between two rectangles.
export function rect_delta(a, b, {ignore_height = false} = {}) {
    if (!a || !b) {
        return null;
    }
    return Math.max(
        Math.abs(a.x - b.x),
        Math.abs(a.y - b.y),
        Math.abs(a.w - b.w),
        ignore_height ? 0 : Math.abs(a.h - b.h),
    );
}

// How far one element strayed from where it first appeared in a recording.
// Returns null when the element never appeared. `vanished` counts frames in
// which it was gone after having been shown.
export function drift(frames, name, {from = 0, ignore_height = false} = {}) {
    let first = null;
    let worst = null;
    let delta = 0;
    let vanished = 0;
    let first_t = null;
    let worst_t = null;
    for (const frame of frames.slice(from)) {
        const rect = frame.rects[name];
        if (first === null) {
            if (rect) {
                first = rect;
                first_t = frame.t;
            }
            continue;
        }
        if (!rect) {
            vanished += 1;
            continue;
        }
        const change = rect_delta(first, rect, {ignore_height});
        if (change > delta) {
            delta = change;
            worst = rect;
            worst_t = frame.t;
        }
    }
    if (first === null) {
        return null;
    }
    return {delta: round(delta), vanished, first, worst, first_t, worst_t};
}

export function describe_rect(rect) {
    return rect ? `${rect.x},${rect.y} ${rect.w}x${rect.h}` : "absent";
}

// Same for a per-frame scalar probe: largest deviation from its first
// non-null value.
export function probe_drift(frames, label, {from = 0} = {}) {
    let first = null;
    let delta = 0;
    let worst = null;
    for (const frame of frames.slice(from)) {
        const value = frame.probes[label];
        if (value === null || value === undefined) {
            continue;
        }
        if (first === null) {
            first = value;
            continue;
        }
        if (Math.abs(value - first) > delta) {
            delta = Math.abs(value - first);
            worst = value;
        }
    }
    return first === null ? null : {delta: round(delta), first, worst};
}

// For a probe that maps labels to {x, y} (rows of a list): the row that
// moved furthest, on one axis, among rows present in both snapshots.
export function largest_move(before, after, axis, only = () => true) {
    let worst = {delta: 0, label: null};
    let compared = 0;
    for (const [label, position] of Object.entries(before ?? {})) {
        const next = after?.[label];
        if (!next || !only(label, position)) {
            continue;
        }
        compared += 1;
        const delta = Math.abs(next[axis] - position[axis]);
        if (delta > worst.delta) {
            worst = {delta: round(delta), label, from: position[axis], to: next[axis]};
        }
    }
    return compared === 0 ? null : {...worst, compared};
}

// Across a whole recording: the row that strayed furthest from where it
// first appeared.
export function label_drift(frames, probe, axis, {from = 0, only = () => true} = {}) {
    const first = new Map();
    let worst = {delta: 0, label: null};
    for (const frame of frames.slice(from)) {
        for (const [label, position] of Object.entries(frame.probes[probe] ?? {})) {
            if (!first.has(label)) {
                if (only(label, position)) {
                    first.set(label, {...position, t: frame.t});
                }
                continue;
            }
            const start = first.get(label);
            const delta = Math.abs(position[axis] - start[axis]);
            if (delta > worst.delta) {
                worst = {
                    delta: round(delta),
                    label,
                    from: start[axis],
                    to: position[axis],
                    after_ms: Math.round(frame.t - start.t),
                };
            }
        }
    }
    return first.size === 0 ? null : {...worst, compared: first.size};
}
