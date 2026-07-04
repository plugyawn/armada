import $ from "jquery";
import type * as tippy from "tippy.js";
import * as z from "zod/mini";

import render_pear_runtime_menu_popover from "../templates/pear_runtime_menu_popover.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_panel from "./pear_panel.ts";
import * as popover_menus from "./popover_menus.ts";
import {parse_html} from "./ui_util.ts";

const pear_reasoning_effort_schema = z.object({
    effort: z.string(),
    label: z.string(),
});

const pear_model_catalog_entry_schema = z.object({
    slug: z.string(),
    display_name: z.string(),
    default_reasoning_level: z.optional(z.string()),
    supported_reasoning_levels: z.optional(z.array(pear_reasoning_effort_schema)),
});

const pear_runtime_usage_schema = z.object({
    input_tokens: z.optional(z.number()),
    cached_input_tokens: z.optional(z.number()),
    output_tokens: z.optional(z.number()),
    reasoning_output_tokens: z.optional(z.number()),
    total_tokens: z.optional(z.number()),
    cache_percent: z.optional(z.number()),
    duration_seconds: z.nullish(z.number()),
    summary: z.optional(z.string()),
});

const pear_runtime_run_schema = z.object({
    status: z.string(),
    usage: z.nullish(pear_runtime_usage_schema),
});

// Subset of the `pear_topic_context` object returned by the
// `GET /json/pear/runs` and `POST /json/pear/topic-context`
// endpoints; see web/src/pear_panel.ts for the full shape.
const pear_runtime_context_schema = z.object({
    stream: z.string(),
    topic: z.string(),
    model: z.string(),
    model_choices: z.optional(z.array(z.string())),
    model_catalog: z.optional(z.array(pear_model_catalog_entry_schema)),
    reasoning_effort: z.string(),
    reasoning_efforts: z.optional(z.array(pear_reasoning_effort_schema)),
});

const pear_runtime_response_schema = z.object({
    pear_runs: z.optional(z.array(pear_runtime_run_schema)),
    pear_topic_context: z.nullish(pear_runtime_context_schema),
});

export type PearReasoningEffort = z.infer<typeof pear_reasoning_effort_schema>;
export type PearRuntimeContext = z.infer<typeof pear_runtime_context_schema>;

export type PearRuntimeMenuChoice = {
    value: string;
    label: string;
    selected: boolean;
};

export type PearRuntimeEffortSlider = {
    has_efforts: boolean;
    selected_label: string;
    value: number;
    max: number;
};

export type PearRuntimeUsage = z.infer<typeof pear_runtime_usage_schema>;
export type PearRuntimeRun = z.infer<typeof pear_runtime_run_schema>;

export type PearRuntimeUsageRow = {
    label: string;
    value: string;
};

export type PearRuntimeSummary = {
    model_label: string;
    effort_label: string;
    compact_label: string;
};

let topic_context: PearRuntimeContext | undefined;
let latest_runs: PearRuntimeRun[] = [];
let context_key = "";
let last_error = "";
let request_in_flight = false;

const fallback_efforts: PearReasoningEffort[] = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

function current_narrow(): {stream: string | undefined; topic: string | undefined} {
    return {
        stream: narrow_state.stream_name(),
        topic: narrow_state.topic(),
    };
}

function narrow_context_key(stream: string | undefined, topic: string | undefined): string {
    if (stream === undefined || topic === undefined) {
        return "";
    }
    return `${stream}\n${topic}`;
}

export function model_reasoning_efforts(
    context: PearRuntimeContext | undefined,
    model: string | undefined,
): PearReasoningEffort[] {
    const catalog_entry = context?.model_catalog?.find((entry) => entry.slug === model);
    const model_efforts = catalog_entry?.supported_reasoning_levels;
    if (model_efforts !== undefined && model_efforts.length > 0) {
        return model_efforts;
    }
    if (context?.reasoning_efforts !== undefined && context.reasoning_efforts.length > 0) {
        return context.reasoning_efforts;
    }
    return fallback_efforts;
}

export function model_choices(context: PearRuntimeContext | undefined): PearRuntimeMenuChoice[] {
    if (context === undefined) {
        return [];
    }
    const catalog = context.model_catalog ?? [];
    const choices = catalog.map((entry) => ({
        value: entry.slug,
        label: entry.display_name || entry.slug,
        selected: entry.slug === context.model,
    }));
    const seen_models = new Set(choices.map((choice) => choice.value));
    for (const model of context.model_choices ?? []) {
        if (!seen_models.has(model)) {
            choices.push({
                value: model,
                label: model,
                selected: model === context.model,
            });
            seen_models.add(model);
        }
    }
    if (context.model && !seen_models.has(context.model)) {
        choices.unshift({
            value: context.model,
            label: context.model,
            selected: true,
        });
    }
    return choices;
}

export function effort_choices(context: PearRuntimeContext | undefined): PearRuntimeMenuChoice[] {
    if (context === undefined) {
        return [];
    }
    return model_reasoning_efforts(context, context.model).map((choice) => ({
        value: choice.effort,
        label: choice.label,
        selected: choice.effort === context.reasoning_effort,
    }));
}

export function effort_slider_for_testing(
    choices: PearRuntimeMenuChoice[],
): PearRuntimeEffortSlider {
    if (choices.length === 0) {
        return {
            has_efforts: false,
            selected_label: "",
            value: 0,
            max: 0,
        };
    }
    const selected_index = choices.findIndex((choice) => choice.selected);
    const value = Math.max(0, selected_index);
    return {
        has_efforts: true,
        selected_label: choices[value]!.label,
        value,
        max: choices.length - 1,
    };
}

export function runtime_summary(context: PearRuntimeContext | undefined): PearRuntimeSummary {
    if (context === undefined) {
        return {
            model_label: $t({defaultMessage: "Pear"}),
            effort_label: "",
            compact_label: $t({defaultMessage: "Pear"}),
        };
    }
    const catalog_entry = context.model_catalog?.find((entry) => entry.slug === context.model);
    const display_name = catalog_entry?.display_name.trim();
    const model_label =
        display_name === undefined || display_name === "" ? context.model : display_name;
    const effort_label =
        model_reasoning_efforts(context, context.model).find(
            (choice) => choice.effort === context.reasoning_effort,
        )?.label ?? context.reasoning_effort;
    return {
        model_label,
        effort_label,
        compact_label: `${model_label} · ${effort_label}`,
    };
}

function format_count(value: number | undefined): string {
    return value === undefined ? "0" : value.toLocaleString();
}

function active_runtime_run(runs: PearRuntimeRun[]): PearRuntimeRun | undefined {
    return (
        runs.find((run) => run.status === "running" || run.status === "queued") ??
        runs.find((run) => run.status === "paused") ??
        runs[0]
    );
}

function build_runtime_usage_rows(
    usage: PearRuntimeUsage | null | undefined,
): PearRuntimeUsageRow[] {
    if (usage === undefined || usage === null) {
        return [];
    }
    const rows: PearRuntimeUsageRow[] = [];
    if (usage.cache_percent !== undefined) {
        rows.push({
            label: $t({defaultMessage: "cache"}),
            value: `${usage.cache_percent.toFixed(1)}%`,
        });
    }
    if (usage.total_tokens !== undefined) {
        rows.push({
            label: $t({defaultMessage: "tokens"}),
            value: format_count(usage.total_tokens),
        });
    } else if (usage.input_tokens !== undefined || usage.output_tokens !== undefined) {
        rows.push({
            label: $t({defaultMessage: "tokens"}),
            value: `${format_count(usage.input_tokens)} in · ${format_count(usage.output_tokens)} out`,
        });
    }
    if (typeof usage.duration_seconds === "number") {
        rows.push({
            label: $t({defaultMessage: "time"}),
            value: `${usage.duration_seconds.toFixed(usage.duration_seconds < 10 ? 1 : 0)}s`,
        });
    }
    if (usage.reasoning_output_tokens !== undefined && usage.reasoning_output_tokens > 0) {
        rows.push({
            label: $t({defaultMessage: "reasoning"}),
            value: format_count(usage.reasoning_output_tokens),
        });
    }
    return rows;
}

function popover_context(): {
    has_runtime: boolean;
    status_message: string;
    runtime_model_label: string;
    runtime_effort_label: string;
    has_runtime_usage: boolean;
    runtime_usage_title: string;
    runtime_usage_rows: PearRuntimeUsageRow[];
    model_choices: PearRuntimeMenuChoice[];
    effort_choices: PearRuntimeMenuChoice[];
    effort_slider: PearRuntimeEffortSlider;
} {
    const {stream, topic} = current_narrow();
    let status_message = "";
    if (stream === undefined || topic === undefined) {
        status_message = $t({defaultMessage: "Open a topic to configure the Pear runtime."});
    } else if (last_error !== "") {
        status_message = last_error;
    } else if (topic_context === undefined) {
        status_message = $t({defaultMessage: "Pear runtime is loading."});
    }
    if (status_message !== "") {
        return {
            has_runtime: false,
            status_message,
            runtime_model_label: "",
            runtime_effort_label: "",
            has_runtime_usage: false,
            runtime_usage_title: "",
            runtime_usage_rows: [],
            model_choices: [],
            effort_choices: [],
            effort_slider: effort_slider_for_testing([]),
        };
    }
    const choices = effort_choices(topic_context);
    const usage = active_runtime_run(latest_runs)?.usage;
    const runtime_usage_rows = build_runtime_usage_rows(usage);
    const summary = runtime_summary(topic_context);
    return {
        has_runtime: true,
        status_message: "",
        runtime_model_label: summary.model_label,
        runtime_effort_label: summary.effort_label,
        has_runtime_usage: runtime_usage_rows.length > 0,
        runtime_usage_title: usage?.summary ?? "",
        runtime_usage_rows,
        model_choices: model_choices(topic_context),
        effort_choices: choices,
        effort_slider: effort_slider_for_testing(choices),
    };
}

function render_popover_content(instance: tippy.Instance): void {
    instance.setContent(parse_html(render_pear_runtime_menu_popover(popover_context())));
}

function rerender_popover_if_open(): void {
    const instance = popover_menus.popover_instances.pear_runtime_menu;
    if (instance?.state.isVisible) {
        render_popover_content(instance);
    }
}

function update_navbar_button_label(): void {
    if (typeof document === "undefined") {
        return;
    }
    const $button = $("#pear-runtime-menu .header-button");
    if ($button.length === 0) {
        return;
    }
    let label = $t({defaultMessage: "Pear runtime"});
    let compact_label = $t({defaultMessage: "Pear"});
    if (topic_context !== undefined && context_key !== "") {
        const summary = runtime_summary(topic_context);
        compact_label = summary.compact_label;
        label = $t(
            {defaultMessage: "Pear runtime: {model} ({effort})"},
            {model: summary.model_label, effort: summary.effort_label},
        );
    }
    $button.attr("aria-label", label);
    $button.attr("data-tippy-content", label);
    $button.find(".pear-navbar-runtime-label").text(compact_label);
}

function fetch_topic_context(): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined) {
        topic_context = undefined;
        latest_runs = [];
        context_key = "";
        rerender_popover_if_open();
        return;
    }
    // Same endpoint and payload shape as pear_panel.refresh.
    void channel.get({
        url: "/json/pear/runs",
        data: {
            include_events: false,
            include_context: true,
            stream,
            topic,
        },
        success(raw_data) {
            const data = pear_runtime_response_schema.parse(raw_data);
            last_error = "";
            latest_runs = data.pear_runs ?? [];
            topic_context = data.pear_topic_context ?? undefined;
            context_key = narrow_context_key(stream, topic);
            update_navbar_button_label();
            rerender_popover_if_open();
        },
        error(xhr) {
            if (xhr.readyState === 0) {
                return;
            }
            last_error = $t({defaultMessage: "Pear runtime is unavailable."});
            blueslip.warn(`Failed to fetch Pear runtime: ${xhr.responseText}`);
            rerender_popover_if_open();
        },
    });
}

function save_runtime(model: string, reasoning_effort: string): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined || request_in_flight) {
        return;
    }
    request_in_flight = true;
    $("#pear-runtime-menu-popover input").prop("disabled", true);
    // Same endpoint and payload shape as pear_panel.save_topic_context.
    void channel.post({
        url: "/json/pear/topic-context",
        data: {
            stream,
            topic,
            model,
            reasoning_effort,
        },
        success(raw_data) {
            const data = pear_runtime_response_schema.parse(raw_data);
            last_error = "";
            if (data.pear_topic_context !== undefined && data.pear_topic_context !== null) {
                topic_context = data.pear_topic_context;
                context_key = narrow_context_key(stream, topic);
            }
            update_navbar_button_label();
            rerender_popover_if_open();
            // Keep the Pear panel in the right sidebar in sync.
            pear_panel.refresh();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                last_error = $t({defaultMessage: "Failed to update the Pear runtime."});
                blueslip.warn(`Failed to update Pear runtime: ${xhr.responseText}`);
                rerender_popover_if_open();
            }
        },
        complete() {
            request_in_flight = false;
            $("#pear-runtime-menu-popover input").prop("disabled", false);
        },
    });
}

function checked_choice(name: string): string | undefined {
    const value = $(`#pear-runtime-menu-popover input[name='${name}']:checked`).val();
    return typeof value === "string" ? value : undefined;
}

function on_model_change(model: string): void {
    const efforts = model_reasoning_efforts(topic_context, model);
    let effort = topic_context?.reasoning_effort ?? "medium";
    if (!efforts.some((choice) => choice.effort === effort)) {
        const catalog_entry = topic_context?.model_catalog?.find((entry) => entry.slug === model);
        effort = catalog_entry?.default_reasoning_level ?? efforts[0]?.effort ?? effort;
    }
    save_runtime(model, effort);
}

function on_effort_index_change(index: number): void {
    const model = checked_choice("pear-runtime-model") ?? topic_context?.model;
    if (model === undefined) {
        return;
    }
    const effort = model_reasoning_efforts(topic_context, model)[index]?.effort;
    if (effort === undefined) {
        return;
    }
    save_runtime(model, effort);
}

function update_effort_slider_display($range: JQuery): void {
    const index = Number($range.val());
    if (!Number.isInteger(index)) {
        return;
    }
    const $control = $range.closest(".pear-runtime-effort-control");
    const $ticks = $control.find(".pear-runtime-effort-ticks span");
    const $selected_tick = $ticks.eq(index);
    if ($selected_tick.length === 0) {
        return;
    }
    $ticks.removeClass("pear-runtime-effort-tick-selected");
    $selected_tick.addClass("pear-runtime-effort-tick-selected");
    $control.find(".pear-runtime-effort-value").text($selected_tick.text());
}

export function initialize(): void {
    if (typeof document === "undefined") {
        // Node test environments mock the DOM minimally; this module
        // only makes sense in a browser.
        return;
    }

    // register_popover_menu attaches a body-delegated click handler,
    // so this works even if the navbar has not been rendered yet;
    // ui_init.js additionally calls us after initialize_navbar().
    popover_menus.register_popover_menu("#pear-runtime-menu", {
        theme: "popover-menu",
        placement: "bottom",
        offset: popover_menus.NAVBAR_POPOVER_OFFSET,
        // The strategy: "fixed"; and eventlisteners modifier option
        // ensure that the popover does not modify its position
        // or disappear when user zooms the page.
        popperOptions: {
            strategy: "fixed",
            modifiers: [
                {
                    name: "eventListeners",
                    options: {
                        scroll: false,
                    },
                },
            ],
        },
        onMount(instance) {
            popover_menus.popover_instances.pear_runtime_menu = instance;
        },
        onShow(instance) {
            const {stream, topic} = current_narrow();
            if (narrow_context_key(stream, topic) !== context_key) {
                // The cached runtime belongs to a different narrow.
                topic_context = undefined;
                context_key = "";
                update_navbar_button_label();
            }
            last_error = "";
            render_popover_content(instance);
            fetch_topic_context();
            $("#pear-runtime-menu").addClass("active-navbar-menu");
        },
        onHidden(instance) {
            instance.destroy();
            popover_menus.popover_instances.pear_runtime_menu = null;
            $("#pear-runtime-menu").removeClass("active-navbar-menu");
        },
    });

    $("body").on(
        "change",
        "#pear-runtime-menu-popover input[name='pear-runtime-model']",
        function (this: HTMLElement) {
            const model = $(this).val();
            if (typeof model === "string") {
                on_model_change(model);
            }
        },
    );

    $("body").on(
        "input",
        "#pear-runtime-menu-popover .pear-runtime-effort-range",
        function (this: HTMLElement) {
            update_effort_slider_display($(this));
        },
    );

    $("body").on(
        "change",
        "#pear-runtime-menu-popover .pear-runtime-effort-range",
        function (this: HTMLElement) {
            update_effort_slider_display($(this));
            const index = Number($(this).val());
            if (Number.isInteger(index)) {
                on_effort_index_change(index);
            }
        },
    );
}
