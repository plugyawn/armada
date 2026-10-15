export type ArmadaWorkspaceMetricSnapshot = {
    counts: Record<string, number>;
    lastDurationsMs: Record<string, number>;
};

const counts = new Map<string, number>();
const starts = new Map<string, number>();
const last_durations = new Map<string, number>();

function now(): number {
    return globalThis.performance?.now?.() ?? Date.now();
}

export function begin_armada_workspace_metric(name: string): void {
    starts.set(name, now());
    globalThis.performance?.mark?.(`${name}:start`);
}

export function finish_armada_workspace_metric(name: string): void {
    const started = starts.get(name);
    if (started === undefined) {
        return;
    }
    starts.delete(name);
    const duration = Math.max(0, now() - started);
    last_durations.set(name, duration);
    increment_armada_workspace_counter(name);
    globalThis.performance?.mark?.(`${name}:end`);
    try {
        globalThis.performance?.measure?.(name, `${name}:start`, `${name}:end`);
    } catch {
        // User agents may evict marks; the in-memory duration remains available.
    }
}

export function increment_armada_workspace_counter(name: string): void {
    counts.set(name, (counts.get(name) ?? 0) + 1);
}

export function record_armada_workspace_error(status: number | undefined): void {
    increment_armada_workspace_counter("workspace_error");
    if (status === 429) {
        increment_armada_workspace_counter("workspace_429");
    }
}

export function armada_workspace_metrics_snapshot(): ArmadaWorkspaceMetricSnapshot {
    return {
        counts: Object.fromEntries(counts),
        lastDurationsMs: Object.fromEntries(last_durations),
    };
}

export function reset_armada_workspace_metrics_for_testing(): void {
    counts.clear();
    starts.clear();
    last_durations.clear();
}
