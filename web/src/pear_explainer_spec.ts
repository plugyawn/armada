import * as z from "zod/mini";

import {PEAR_SVG_SOURCE_LIMIT} from "./pear_svg.ts";
import {PEAR_KIT_COMPONENT_PROPS, PEAR_KIT_COMPONENT_TYPES} from "./pear_wall_components.ts";

export const PEAR_EXPLAINER_COMPONENT_LIMIT = 4;
export const PEAR_EXPLAINER_ACTION_LIMIT = 4;
export const PEAR_EXPLAINER_ACTION_LABEL_LIMIT = 40;
export const PEAR_EXPLAINER_ACTION_PROMPT_LIMIT = 1000;
export const PEAR_EXPLAINER_TEXT_LIMIT = 4000;
export const PEAR_EXPLAINER_TITLE_LIMIT = 24;

const explainer_spec_schema = z.object({
    title: z.optional(z.string()),
    cadence_seconds: z.optional(z.number()),
    block: z.object({
        components: z.array(
            z.object({
                type: z.string(),
                props: z.record(z.string(), z.unknown()),
            }),
        ),
        actions: z.optional(
            z.array(
                z.object({
                    label: z.string(),
                    prompt: z.string(),
                }),
            ),
        ),
    }),
    recipe: z.optional(
        z.object({
            command: z.optional(z.string()),
            parse: z.object({mode: z.string(), pattern: z.optional(z.string())}),
            bindings: z.optional(z.record(z.string(), z.string())),
        }),
    ),
});

export type PearExplainerSpec = z.infer<typeof explainer_spec_schema>;

export function parse_pear_explainer_spec(
    raw_text: string,
    language: "pear-explainer" | "pear-svg",
): PearExplainerSpec | undefined {
    if (language === "pear-svg") {
        if (raw_text.trim() === "" || raw_text.length > PEAR_SVG_SOURCE_LIMIT) {
            return undefined;
        }
        return {
            title: "diagram",
            block: {components: [{type: "svg", props: {markup: raw_text}}]},
            recipe: {parse: {mode: "static"}},
        };
    }
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw_text);
    } catch {
        return undefined;
    }
    const result = explainer_spec_schema.safeParse(parsed);
    if (!result.success) {
        return undefined;
    }
    const components = result.data.block.components;
    const actions = result.data.block.actions ?? [];
    if (
        (result.data.title?.length ?? 0) > PEAR_EXPLAINER_TITLE_LIMIT ||
        components.length === 0 ||
        components.length > PEAR_EXPLAINER_COMPONENT_LIMIT ||
        !components.every((component) => PEAR_KIT_COMPONENT_TYPES.has(component.type)) ||
        components.some((component) => {
            const allowed = PEAR_KIT_COMPONENT_PROPS[component.type];
            return (
                allowed === undefined ||
                Object.keys(component.props).some((prop) => !allowed.has(prop))
            );
        }) ||
        actions.length > PEAR_EXPLAINER_ACTION_LIMIT ||
        actions.some(
            (action) =>
                action.label.trim() === "" ||
                action.label.length > PEAR_EXPLAINER_ACTION_LABEL_LIMIT ||
                action.prompt.trim() === "" ||
                action.prompt.length > PEAR_EXPLAINER_ACTION_PROMPT_LIMIT,
        ) ||
        components.some(
            (component) =>
                (component.type === "svg" &&
                    (typeof component.props["markup"] !== "string" ||
                        component.props["markup"].trim() === "" ||
                        component.props["markup"].length > PEAR_SVG_SOURCE_LIMIT ||
                        (component.props["label"] !== undefined &&
                            !["string", "number", "boolean"].includes(
                                typeof component.props["label"],
                            )))) ||
                (component.type === "text" &&
                    (Object.values(component.props).some(
                        (value) => !["string", "number", "boolean"].includes(typeof value),
                    ) ||
                        Object.values(component.props).every(
                            (value) => String(value).trim() === "",
                        ) ||
                        Object.values(component.props).some(
                            (value) => String(value).length > PEAR_EXPLAINER_TEXT_LIMIT,
                        ))),
        )
    ) {
        return undefined;
    }
    return result.data;
}
