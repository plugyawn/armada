export type PearLivePlanStep = {
    step: string;
    status: string;
};

export function pear_live_progress_signature(
    plan: PearLivePlanStep[],
    overview: string | null | undefined,
): string {
    return JSON.stringify({
        plan: plan.map(({step, status}) => ({step, status})),
        overview: overview?.trim() ?? "",
    });
}

export function pear_live_progress_matches(current: Element | null, signature: string): boolean {
    return current?.getAttribute("data-pear-progress-signature") === signature;
}

export function pear_live_overview_key(
    run_key: number | string,
    current_step: string,
    overview: string,
): string {
    return JSON.stringify([run_key, current_step, overview.trim()]);
}

export function preserve_unchanged_pear_overview(
    current_root: ParentNode,
    next_root: ParentNode,
): boolean {
    const current = current_root.querySelector<HTMLElement>(
        ".pear-live-overview[data-pear-overview-key]",
    );
    const next = next_root.querySelector<HTMLElement>(
        ".pear-live-overview[data-pear-overview-key]",
    );
    const current_key = current?.dataset["pearOverviewKey"];
    const next_key = next?.dataset["pearOverviewKey"];
    if (current === null || next === null || !current_key || current_key !== next_key) {
        return false;
    }
    next.replaceWith(current);
    return true;
}
