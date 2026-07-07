import $ from "jquery";
import type * as tippy from "tippy.js";
import * as z from "zod/mini";

import render_pear_runtime_menu_popover from "../templates/pear_runtime_menu_popover.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
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

type RuntimeSave = {
    stream: string;
    topic: string;
    model: string;
    reasoning_effort: string;
};

let topic_context: PearRuntimeContext | undefined;
let latest_runs: PearRuntimeRun[] = [];
let context_key = "";
let last_error = "";
let request_in_flight = false;
let pending_runtime_save: RuntimeSave | undefined;
let runtime_save_generation = 0;
let initialized = false;
let context_refresh_timer: number | undefined;

const RUNTIME_CONTEXT_REFRESH_INTERVAL_MS = 60_000;
const runtime_menu_selectors = [
    "#pear-panel .pear-runtime-menu-trigger",
    ".message_header_stream .pear-topic-header-runtime-control.pear-runtime-menu-trigger",
];
const runtime_menu_selector = runtime_menu_selectors.join(", ");
const forbidden_header_mount_selectors = [
    "#top_navbar",
    "#navbar-middle",
    "#message_view_header",
    ".top-navbar-container",
    ".message-header-navbar-title",
];

export const runtime_menu_selector_for_testing = runtime_menu_selector;
export const forbidden_header_mount_selectors_for_testing = forbidden_header_mount_selectors;

export function should_accept_runtime_fetch_for_testing(options: {
    fetch_generation: number;
    runtime_save_generation: number;
    fetch_started_during_save: boolean;
}): boolean {
    return (
        options.fetch_generation === options.runtime_save_generation &&
        !options.fetch_started_during_save
    );
}

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
        compact_label: `${model_label} · ${effort_label || compact_effort}`,
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

export function runtime_status_for_testing(run: {status: string} | undefined): PearRuntimeStatus {
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
    let model_label = "";
    let effort_label = "";
    const active_run = active_runtime_run(latest_runs);
    let runtime_status = runtime_status_for_testing(active_run);
    const runtime_meter = runtime_usage_meter_for_testing(active_run?.usage);
    if (topic_context !== undefined && context_key !== "") {
        const summary = runtime_summary(topic_context);
        runtime_status = runtime_status_for_testing(active_run);
        compact_label = summary.compact_label;
        model_label = summary.model_label;
        effort_label = summary.effort_label;
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
    if (model_label !== "" || effort_label !== "") {
        $button.find(".pear-runtime-menu-model-label").text(model_label || compact_label);
        $button.find(".pear-runtime-menu-effort-label").text(effort_label);
    }
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
    const fetch_generation = runtime_save_generation;
    const fetch_started_during_save = request_in_flight || pending_runtime_save !== undefined;
    // Same endpoint and payload shape as pear_panel.refresh.
    void channel.get({
        url: "/json/pear/runs",
        data: {
            include_events: false,
            include_context: true,
            include_git: false,
            stream,
            topic,
        },
        success(raw_data) {
            const data = pear_runtime_response_schema.parse(raw_data);
            const now = current_narrow();
            const narrow_changed = now.stream !== stream || now.topic !== topic;
            if (
                narrow_changed ||
                !should_accept_runtime_fetch_for_testing({
                    fetch_generation,
                    runtime_save_generation,
                    fetch_started_during_save,
                })
            ) {
                // Discard responses for a save we superseded or a topic the
                // user has since navigated away from, so the header never shows
                // the previous topic's model/effort.
                rerender_popover_if_open();
                return;
            }
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
            const now = current_narrow();
            const narrow_changed = now.stream !== stream || now.topic !== topic;
            if (
                narrow_changed ||
                !should_accept_runtime_fetch_for_testing({
                    fetch_generation,
                    runtime_save_generation,
                    fetch_started_during_save,
                })
            ) {
                return;
            }
            last_error = $t({defaultMessage: "Pear runtime is unavailable."});
            blueslip.warn(`Failed to fetch Pear runtime: ${xhr.responseText}`);
            rerender_popover_if_open();
        },
    });
}

function apply_runtime_locally(model: string, reasoning_effort: string): void {
    if (topic_context === undefined) {
        return;
    }
    topic_context = {
        ...topic_context,
        model,
        reasoning_effort,
    };
    update_runtime_button_label();
    rerender_popover_if_open();
}

function same_runtime_save(a: RuntimeSave, b: RuntimeSave): boolean {
    return (
        a.stream === b.stream &&
        a.topic === b.topic &&
        a.model === b.model &&
        a.reasoning_effort === b.reasoning_effort
    );
}

function post_runtime_save(
    save: RuntimeSave,
    previous_context: PearRuntimeContext | undefined,
): void {
    request_in_flight = true;
    $("#pear-runtime-menu-popover").addClass("pear-runtime-saving");
    // Same endpoint and payload shape as pear_panel.save_topic_context.
    void channel.post({
        url: "/json/pear/topic-context",
        data: {
            stream: save.stream,
            topic: save.topic,
            model: save.model,
            reasoning_effort: save.reasoning_effort,
            include_git: false,
        },
        success(raw_data) {
            const data = pear_runtime_response_schema.parse(raw_data);
            last_error = "";
            if (data.pear_topic_context !== undefined && data.pear_topic_context !== null) {
                topic_context =
                    pending_runtime_save === undefined
                        ? data.pear_topic_context
                        : {
                              ...data.pear_topic_context,
                              model: pending_runtime_save.model,
                              reasoning_effort: pending_runtime_save.reasoning_effort,
                          };
                context_key = narrow_context_key(save.stream, save.topic);
            }
            latest_runs = data.pear_runs ?? latest_runs;
            update_runtime_button_label();
            rerender_popover_if_open();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                last_error = $t({defaultMessage: "Failed to update the Pear runtime."});
                blueslip.warn(`Failed to update Pear runtime: ${xhr.responseText}`);
                if (pending_runtime_save === undefined) {
                    topic_context = previous_context;
                    update_runtime_button_label();
                }
                rerender_popover_if_open();
            }
        },
        complete() {
            request_in_flight = false;
            $("#pear-runtime-menu-popover").removeClass("pear-runtime-saving");
            const pending_save = pending_runtime_save;
            pending_runtime_save = undefined;
            if (pending_save !== undefined && !same_runtime_save(pending_save, save)) {
                post_runtime_save(pending_save, topic_context);
            }
        },
    });
}

function save_runtime(model: string, reasoning_effort: string): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined) {
        return;
    }
    runtime_save_generation += 1;
    const save = {stream, topic, model, reasoning_effort};
    const previous_context = topic_context;
    apply_runtime_locally(model, reasoning_effort);
    if (request_in_flight) {
        pending_runtime_save = save;
        return;
    }
    post_runtime_save(save, previous_context);
}

function selected_choice(name: string): string | undefined {
    const select_value = $(`#pear-runtime-menu-popover select[name='${name}']`).val();
    if (typeof select_value === "string") {
        return select_value;
    }
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

function on_effort_change(effort: string): void {
    const model = selected_choice("pear-runtime-model") ?? topic_context?.model;
    if (model === undefined) {
        return;
    }
    save_runtime(model, effort);
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

    // register_popover_menu attaches body-delegated click handlers,
    // so this works across Pear panel/header rerenders. Register each
    // selector separately; comma selectors have proved fragile for
    // dynamically injected message-header controls.
    const runtime_menu_popover_props: Partial<tippy.Props> = {
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
            $(instance.reference).addClass("active-right-sidebar-menu");
        },
        onHidden(instance) {
            instance.destroy();
            popover_menus.popover_instances.pear_runtime_menu = null;
            $(instance.reference).removeClass("active-right-sidebar-menu");
        },
    };
    for (const selector of runtime_menu_selectors) {
        popover_menus.register_popover_menu(selector, runtime_menu_popover_props, {
            show_as_overlay_on_mobile: true,
        });
    }

    $("body").on(
        "click keydown",
        "#pear-runtime-menu-popover .pear-runtime-model-menu-choice",
        function (this: HTMLElement, e) {
            if (
                e.type === "keydown" &&
                (e.key !== "Enter" && e.key !== " ")
            ) {
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            const model = this.dataset["model"];
            if (model !== undefined && model !== topic_context?.model) {
                on_model_change(model);
            }
        },
    );

    $("body").on(
        "click keydown",
        "#pear-runtime-menu-popover .pear-runtime-effort-menu-choice",
        function (this: HTMLElement, e) {
            if (
                e.type === "keydown" &&
                (e.key !== "Enter" && e.key !== " ")
            ) {
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            const effort = this.dataset["effort"];
            if (effort !== undefined && effort !== topic_context?.reasoning_effort) {
                on_effort_change(effort);
            }
        },
    );

    update_runtime_button_label();
    fetch_topic_context();
    window.addEventListener("hashchange", fetch_topic_context);
    context_refresh_timer ??= window.setInterval(
        fetch_topic_context,
        RUNTIME_CONTEXT_REFRESH_INTERVAL_MS,
    );
}
