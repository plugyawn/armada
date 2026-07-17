// Deterministic presentation helpers shared by Pear runtime surfaces. Keep
// model options, topic keys, and Codex plan projection here so chat, the rail,
// and the runtime menu do not grow parallel interpretations of the same state.

export type PearReasoningEffortOption = {
    effort: string;
    label: string;
};

type PearRuntimeEffortSource =
    | {
          model_catalog?:
              | {
                    slug: string;
                    supported_reasoning_levels?: PearReasoningEffortOption[] | undefined;
                }[]
              | undefined;
          reasoning_efforts?: PearReasoningEffortOption[] | undefined;
      }
    | undefined
    | null;

export const fallback_reasoning_efforts: PearReasoningEffortOption[] = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

export function model_reasoning_efforts(
    context: PearRuntimeEffortSource,
    model: string | undefined,
): PearReasoningEffortOption[] {
    const catalog_entry = context?.model_catalog?.find((entry) => entry.slug === model);
    const model_efforts = catalog_entry?.supported_reasoning_levels;
    if (model_efforts !== undefined && model_efforts.length > 0) {
        return model_efforts;
    }
    if (context?.reasoning_efforts !== undefined && context.reasoning_efforts.length > 0) {
        return context.reasoning_efforts;
    }
    return fallback_reasoning_efforts;
}

export function context_key(stream: string | undefined, topic: string | undefined): string {
    if (stream === undefined || topic === undefined) {
        return "";
    }
    return `${stream}\n${topic}`;
}

export function compact_reasoning_effort(effort: string | undefined): string {
    if (effort === undefined || effort === "") {
        return "";
    }
    if (effort === "xhigh") {
        return "xh";
    }
    return effort.at(0)?.toLowerCase() ?? "";
}

export type PearWindowedIndex = number | "ellipsis";

export function windowed_plan_indices(length: number, current_index: number): PearWindowedIndex[] {
    if (length <= 0 || current_index < 0 || current_index >= length) {
        return [];
    }
    const first = Math.max(0, current_index - 1);
    const last = Math.min(length - 1, current_index + 1);
    const result: PearWindowedIndex[] = [];
    if (first > 0) {
        result.push("ellipsis");
    }
    for (let index = first; index <= last; index += 1) {
        result.push(index);
    }
    if (last < length - 1) {
        result.push("ellipsis");
    }
    return result;
}

export type PearPlanSourceStep = {
    step: string;
    status: string;
};

export type PearActivePlanRow =
    | {
          kind: "ellipsis";
      }
    | {
          kind: "step";
          index: number;
          step: string;
          status: "completed" | "current" | "pending";
          is_current: boolean;
      };

export type PearActivePlanPresentation =
    | {
          kind: "working";
          current_key: "working";
          rows: [];
      }
    | {
          kind: "plan";
          current_key: string;
          current_index: number;
          rows: PearActivePlanRow[];
      };

// Codex owns the plan and its statuses. This helper only projects a bounded,
// deterministic window for the two places that display that plan; it never
// generates, renames, or summarizes a step.
export function active_plan_presentation(
    plan: readonly PearPlanSourceStep[],
): PearActivePlanPresentation {
    if (plan.length === 0) {
        return {kind: "working", current_key: "working", rows: []};
    }
    let current_index = plan.findIndex((step) => step.status === "in_progress");
    if (current_index === -1) {
        current_index = plan.findIndex((step) => step.status === "pending");
    }
    if (current_index === -1) {
        current_index = plan.length - 1;
    }
    const rows: PearActivePlanRow[] = windowed_plan_indices(plan.length, current_index).map(
        (index) => {
            if (index === "ellipsis") {
                return {kind: "ellipsis"};
            }
            const source = plan[index]!;
            const is_completed = source.status === "completed" || source.status === "done";
            return {
                kind: "step",
                index,
                step: source.step,
                status: is_completed
                    ? "completed"
                    : index === current_index
                      ? "current"
                      : "pending",
                is_current: index === current_index,
            };
        },
    );
    return {
        kind: "plan",
        current_key: `${current_index}:${plan[current_index]!.step}`,
        current_index,
        rows,
    };
}
