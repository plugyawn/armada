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

export type PearRuntimeUsageMeter = {
    has_meter: boolean;
    label: string;
    value: string;
    percent: number;
    is_hot: boolean;
};

export type PearRuntimeStatus = {
    glyph: string;
    label: string;
    class_name: string;
    is_active: boolean;
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
let initialized = false;
let context_refresh_timer: number | undefined;

const RUNTIME_CONTEXT_REFRESH_INTERVAL_MS = 60_000;
const runtime_menu_selector =
    "#pear-panel .pear-runtime-menu-trigger, .message_header_stream .pear-runtime-menu-trigger";
const forbidden_header_mount_selectors = [
    "#top_navbar",
    "#navbar-middle",
    "#message_view_header",
    ".top-navbar-container",
    ".message-header-navbar-title",
];

export const runtime_menu_selector_for_testing = runtime_menu_selector;
export const forbidden_header_mount_selectors_for_testing = forbidden_header_mount_selectors;

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
    const compact_effort =
        context.reasoning_effort === "xhigh"
            ? "xh"
            : (context.reasoning_effort.at(0)?.toLowerCase() ??
              effort_label.at(0)?.toLowerCase() ??
              "");
    return {
        model_label,
        effort_label,
        compact_label: `${context.model} / ${compact_effort}`,
    };
}

function format_count(value: number | undefined): string {
    return value === undefined ? "0" : value.toLocaleString();
}

function format_tokens_per_second(tokens: number, seconds: number): string {
    const rate = tokens / seconds;
    const display_rate = rate < 10 ? rate.toFixed(1) : Math.round(rate).toLocaleString();
    return `${display_rate} tok/s`;
}

function active_runtime_run(runs: PearRuntimeRun[]): PearRuntimeRun | undefined {
    return (
        runs.find((run) => run.status === "running" || run.status === "queued") ??
        runs.find((run) => run.status === "paused") ??
        runs[0]
    );
}

export function runtime_status_for_testing(
    run: {status: string} | undefined,
): PearRuntimeStatus {
    switch (run?.status.toLowerCase()) {
        case "running":
            return {
                glyph: "·",
                label: $t({defaultMessage: "working"}),
                class_name: "pear-runtime-status-active",
                is_active: true,
            };
        case "queued":
            return {
                glyph: "·",
                label: $t({defaultMessage: "queued"}),
                class_name: "pear-runtime-status-active",
                is_active: true,
            };
        case "loading":
            return {
                glyph: "·",
                label: $t({defaultMessage: "loading"}),
                class_name: "pear-runtime-status-active",
                is_active: true,
            };
        case "paused":
            return {
                glyph: "✧",
                label: $t({defaultMessage: "paused"}),
                class_name: "pear-runtime-status-paused",
                is_active: false,
            };
        case "failed":
        case "error":
        case "blocked":
            return {
                glyph: "✷",
                label: $t({defaultMessage: "failed"}),
                class_name: "pear-runtime-status-blocked",
                is_active: false,
            };
        case "cancelled":
        case "canceled":
            return {
                glyph: "✷",
                label: $t({defaultMessage: "cancelled"}),
                class_name: "pear-runtime-status-blocked",
                is_active: false,
            };
        default:
            return {
                glyph: "·",
                label: $t({defaultMessage: "idle"}),
                class_name: "pear-runtime-status-idle",
                is_active: false,
            };
    }
}

export function build_runtime_usage_rows(
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
        const speed_tokens = usage.output_tokens ?? usage.total_tokens;
        if (speed_tokens !== undefined && usage.duration_seconds > 0) {
            rows.push({
                label: $t({defaultMessage: "speed"}),
                value: format_tokens_per_second(speed_tokens, usage.duration_seconds),
            });
        }
    }
    if (usage.reasoning_output_tokens !== undefined && usage.reasoning_output_tokens > 0) {
        rows.push({
            label: $t({defaultMessage: "reasoning"}),
            value: format_count(usage.reasoning_output_tokens),
        });
    }
    return rows;
}

export function runtime_usage_meter_for_testing(
    usage: PearRuntimeUsage | null | undefined,
): PearRuntimeUsageMeter {
    if (usage?.cache_percent === undefined) {
        return {
            has_meter: false,
            label: "",
            value: "",
            percent: 0,
            is_hot: false,
        };
    }
    const percent = Math.min(100, Math.max(0, usage.cache_percent));
    return {
        has_meter: true,
        label: $t({defaultMessage: "cached context"}),
        value: `${percent.toFixed(1)}%`,
        percent,
        is_hot: percent >= 70,
    };
}

function popover_context(): {
    has_runtime: boolean;
    status_message: string;
    runtime_model_label: string;
    runtime_effort_label: string;
    runtime_status: PearRuntimeStatus;
    has_runtime_usage: boolean;
    runtime_usage_title: string;
    runtime_usage_rows: PearRuntimeUsageRow[];
    runtime_usage_meter: PearRuntimeUsageMeter;
    model_choices: PearRuntimeMenuChoice[];
    effort_choices: PearRuntimeMenuChoice[];
    effort_slider: PearRuntimeEffortSlider;
} {
    const {stream, topic} = current_narrow();
    let status_message = "";
    let runtime_status = runtime_status_for_testing(undefined);
    if (stream === undefined || topic === undefined) {
        status_message = $t({defaultMessage: "Open a topic to configure the Pear runtime."});
    } else if (last_error !== "") {
        status_message = last_error;
        runtime_status = runtime_status_for_testing({status: "failed"});
    } else if (topic_context === undefined) {
        status_message = $t({defaultMessage: "Pear runtime is loading."});
        runtime_status = runtime_status_for_testing({status: "loading"});
    }
    if (status_message !== "") {
        return {
            has_runtime: false,
            status_message,
            runtime_model_label: "",
            runtime_effort_label: "",
            runtime_status,
            has_runtime_usage: false,
            runtime_usage_title: "",
            runtime_usage_rows: [],
            runtime_usage_meter: runtime_usage_meter_for_testing(undefined),
            model_choices: [],
            effort_choices: [],
            effort_slider: effort_slider_for_testing([]),
        };
    }
    const choices = effort_choices(topic_context);
    const active_run = active_runtime_run(latest_runs);
    const usage = active_run?.usage;
    const runtime_usage_rows = build_runtime_usage_rows(usage);
    const summary = runtime_summary(topic_context);
    return {
        has_runtime: true,
        status_message: "",
        runtime_model_label: summary.model_label,
        runtime_effort_label: summary.effort_label,
        runtime_status: runtime_status_for_testing(active_run),
        has_runtime_usage: runtime_usage_rows.length > 0,
        runtime_usage_title: usage?.summary ?? "",
        runtime_usage_rows,
        runtime_usage_meter: runtime_usage_meter_for_testing(usage),
        model_choices: model_choices(topic_context),
        effort_choices: choices,
        effort_slider: effort_slider_for_testing(choices),
    };
}

function render_popover_content(instance: tippy.Instance): void {
    instance.setContent(parse_html(render_pear_runtime_menu_popover(popover_context())));
    $(document).trigger("pear_liveness_markers_changed");
}

function rerender_popover_if_open(): void {
    const instance = popover_menus.popover_instances.pear_runtime_menu;
    if (instance?.state.isVisible) {
        render_popover_content(instance);
    }
}

function update_runtime_button_label(): void {
    if (typeof document === "undefined") {
        return;
    }
    const $button = $(runtime_menu_selector);
    if ($button.length === 0) {
        return;
    }
    let label = $t({defaultMessage: "Pear runtime"});
    let compact_label = $t({defaultMessage: "Pear"});
    const active_run = active_runtime_run(latest_runs);
    let runtime_status = runtime_status_for_testing(active_run);
    const runtime_meter = runtime_usage_meter_for_testing(active_run?.usage);
    if (topic_context !== undefined && context_key !== "") {
        const summary = runtime_summary(topic_context);
        runtime_status = runtime_status_for_testing(active_run);
        compact_label = summary.compact_label;
        label = runtime_meter.has_meter
            ? $t(
                  {
                      defaultMessage:
                          "Pear runtime: {model} ({effort}), {status}, {meter_label} {meter_value}",
                  },
                  {
                      model: summary.model_label,
                      effort: summary.effort_label,
                      status: runtime_status.label,
                      meter_label: runtime_meter.label,
                      meter_value: runtime_meter.value,
                  },
              )
            : $t(
                  {defaultMessage: "Pear runtime: {model} ({effort}), {status}"},
                  {
                      model: summary.model_label,
                      effort: summary.effort_label,
                      status: runtime_status.label,
                  },
              );
    }
    $button.attr("aria-label", label);
    $button.attr("data-tippy-content", label);
    $button.find(".pear-runtime-menu-label").text(compact_label);
    $button.toggleClass("pear-liveness-active", runtime_status.is_active);
    $button
        .find(".pear-runtime-menu-glyph")
        .text(runtime_status.glyph)
        .attr(
            "class",
            `pear-ui-glyph pear-runtime-menu-glyph ${runtime_status.class_name}${
                runtime_status.is_active ? " pear-thinking-glyph" : ""
            }`,
        );
    $(document).trigger("pear_liveness_markers_changed");
}

function fetch_topic_context(): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined) {
        topic_context = undefined;
        latest_runs = [];
        context_key = "";
        update_runtime_button_label();
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
            update_runtime_button_label();
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
            update_runtime_button_label();
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
    if (initialized) {
        fetch_topic_context();
        return;
    }
    initialized = true;

    // register_popover_menu attaches a body-delegated click handler,
    // so this works across Pear panel rerenders.
    popover_menus.register_popover_menu(runtime_menu_selector, {
        theme: "popover-menu",
        placement: "bottom-start",
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
                latest_runs = [];
                context_key = "";
                update_runtime_button_label();
            }
            last_error = "";
            render_popover_content(instance);
            fetch_topic_context();
            $(runtime_menu_selector).addClass("active-right-sidebar-menu");
        },
        onHidden(instance) {
            instance.destroy();
            popover_menus.popover_instances.pear_runtime_menu = null;
            $(runtime_menu_selector).removeClass("active-right-sidebar-menu");
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

    $("body").on("click", "#pear-runtime-menu-popover .pear-workbench-menu-link", (e) => {
        e.preventDefault();
        e.stopPropagation();
        popover_menus.hide_current_popover_if_visible(
            popover_menus.popover_instances.pear_runtime_menu,
        );
        pear_panel.open_work_details();
    });

    update_runtime_button_label();
    fetch_topic_context();
    window.addEventListener("hashchange", fetch_topic_context);
    context_refresh_timer ??= window.setInterval(
        fetch_topic_context,
        RUNTIME_CONTEXT_REFRESH_INTERVAL_MS,
    );
}
