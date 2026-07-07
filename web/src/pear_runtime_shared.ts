// Helpers shared by pear_panel.ts and pear_runtime_menu.ts. Both modules
// render the same runtime facts (model, reasoning effort, topic context
// keys); these previously existed as byte-identical copies in each file
// and drifted independently.

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
