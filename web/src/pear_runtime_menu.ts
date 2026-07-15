import $ from "jquery";
import type * as tippy from "tippy.js";
import * as z from "zod/mini";

import render_pear_runtime_menu_popover from "../templates/pear_runtime_menu_popover.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_panel from "./pear_panel.ts";
import {model_reasoning_efforts, context_key as narrow_context_key} from "./pear_runtime_shared.ts";
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
    response_policy: z.union([z.literal("ambient"), z.literal("mentions_only")]),
});

const pear_runtime_response_schema = z.object({
    pear_runs: z.optional(z.array(pear_runtime_run_schema)),
    pear_topic_context: z.nullish(pear_runtime_context_schema),
});

export type PearReasoningEffort = z.infer<typeof pear_reasoning_effort_schema>;

export {model_reasoning_efforts} from "./pear_runtime_shared.ts";
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
    response_policy: "ambient" | "mentions_only";
};

let topic_context: PearRuntimeContext | undefined;
let latest_runs: PearRuntimeRun[] = [];
let context_key = "";
let last_error = "";
let request_in_flight = false;
let pending_runtime_save: RuntimeSave | undefined;
let initialized = false;

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

function current_narrow(): {stream: string | undefined; topic: string | undefined} {
    return {
        stream: narrow_state.stream_name(),
        topic: narrow_state.topic(),
    };
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

export function presence_choices(context: PearRuntimeContext | undefined): PearRuntimeMenuChoice[] {
    if (context === undefined) {
        return [];
    }
    return [
        {
            value: "ambient",
            label: $t({defaultMessage: "Join when useful"}),
            selected: context.response_policy === "ambient",
        },
        {
            value: "mentions_only",
            label: $t({defaultMessage: "Only when asked"}),
            selected: context.response_policy === "mentions_only",
        },
    ];
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
    presence_choices: PearRuntimeMenuChoice[];
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
            presence_choices: [],
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
        presence_choices: presence_choices(topic_context),
    };
}

function render_popover_content(instance: tippy.Instance): void {
    // setContent replaces the focused choice's DOM node, dropping keyboard
    // focus to the document root mid-interaction; remember which choice held
    // focus so the rebuilt menu can pick it back up.
    const focused = document.activeElement;
    let refocus_selector: string | undefined;
    if (focused instanceof HTMLElement && instance.popper.contains(focused)) {
        const choice = focused.closest<HTMLElement>("[data-model], [data-effort], [data-policy]");
        if (choice?.dataset["model"] !== undefined) {
            refocus_selector = `.pear-runtime-model-menu-choice[data-model='${CSS.escape(choice.dataset["model"])}']`;
        } else if (choice?.dataset["effort"] !== undefined) {
            refocus_selector = `.pear-runtime-effort-menu-choice[data-effort='${CSS.escape(choice.dataset["effort"])}']`;
        } else if (choice?.dataset["policy"] !== undefined) {
            refocus_selector = `.pear-runtime-presence-menu-choice[data-policy='${CSS.escape(choice.dataset["policy"])}']`;
        }
    }
    const context = popover_context();
    instance.setContent(parse_html(render_pear_runtime_menu_popover(context)));
    // The meter width is set here rather than through a style attribute in
    // the template, which the repo's template lint forbids.
    const meter_fill = instance.popper.querySelector<HTMLElement>(".pear-runtime-meter-fill");
    if (meter_fill !== null) {
        meter_fill.style.width = `${context.runtime_usage_meter.percent}%`;
    }
    if (refocus_selector !== undefined) {
        instance.popper.querySelector<HTMLElement>(refocus_selector)?.focus({preventScroll: true});
    }
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

function apply_shared_topic_context(): boolean {
    // The panel owns the topic-context/runs store (#36): read from it
    // rather than running a second polling loop against the same
    // endpoint. Returns whether a cached context was applied.
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined) {
        topic_context = undefined;
        latest_runs = [];
        context_key = "";
        update_runtime_button_label();
        rerender_popover_if_open();
        return true;
    }
    if (request_in_flight || pending_runtime_save !== undefined) {
        // A save is in flight; its completion handler owns local state.
        return true;
    }
    const cached = pear_panel.shared_topic_context(stream, topic);
    if (cached === undefined) {
        return false;
    }
    last_error = "";
    topic_context = cached;
    latest_runs = pear_panel.shared_runs();
    context_key = narrow_context_key(stream, topic);
    update_runtime_button_label();
    rerender_popover_if_open();
    return true;
}

function fetch_topic_context(): void {
    if (!apply_shared_topic_context()) {
        // Nothing cached yet: ask the panel to fetch; the store listener
        // applies the result when it lands. This path is only reachable
        // from user-initiated entry points (open, init, hashchange), so
        // it cannot recurse through the listener.
        pear_panel.refresh({force_context: true});
    }
}

function apply_runtime_locally(
    model: string,
    reasoning_effort: string,
    response_policy: "ambient" | "mentions_only",
): void {
    if (topic_context === undefined) {
        return;
    }
    topic_context = {
        ...topic_context,
        model,
        reasoning_effort,
        response_policy,
    };
    update_runtime_button_label();
    rerender_popover_if_open();
}

function same_runtime_save(a: RuntimeSave, b: RuntimeSave): boolean {
    return (
        a.stream === b.stream &&
        a.topic === b.topic &&
        a.model === b.model &&
        a.reasoning_effort === b.reasoning_effort &&
        a.response_policy === b.response_policy
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
            response_policy: save.response_policy,
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
                              response_policy: pending_runtime_save.response_policy,
                          };
                context_key = narrow_context_key(save.stream, save.topic);
                // Push the saved context into the shared store so the
                // header pill and panel update immediately rather than on
                // their next context refresh.
                pear_panel.remember_saved_topic_context(topic_context);
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

function save_runtime(
    model: string,
    reasoning_effort: string,
    response_policy: "ambient" | "mentions_only",
): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined) {
        return;
    }
    const save = {stream, topic, model, reasoning_effort, response_policy};
    const previous_context = topic_context;
    apply_runtime_locally(model, reasoning_effort, response_policy);
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
    save_runtime(model, effort, topic_context?.response_policy ?? "ambient");
}

function on_effort_change(effort: string): void {
    const model = selected_choice("pear-runtime-model") ?? topic_context?.model;
    if (model === undefined) {
        return;
    }
    save_runtime(model, effort, topic_context?.response_policy ?? "ambient");
}

function on_presence_change(response_policy: string): void {
    if (
        topic_context === undefined ||
        (response_policy !== "ambient" && response_policy !== "mentions_only")
    ) {
        return;
    }
    save_runtime(topic_context.model, topic_context.reasoning_effort, response_policy);
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
            // Start keyboard focus on the selected choice so Tab and the
            // arrow keys operate inside the menu instead of walking the
            // header controls behind it.
            instance.popper
                .querySelector<HTMLElement>(".pear-runtime-menu-choice-selected")
                ?.focus({preventScroll: true});
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
            const reference = instance.reference;
            instance.destroy();
            popover_menus.popover_instances.pear_runtime_menu = null;
            $(reference).removeClass("active-right-sidebar-menu");
            // Escape (or selecting with the keyboard) tears down the popper
            // that held focus; hand focus back to the trigger rather than
            // letting it fall to the document root. A focus target the user
            // chose deliberately (e.g. clicking another control) is kept.
            const active = document.activeElement;
            if (
                (active === null ||
                    active === document.body ||
                    active === document.documentElement) &&
                reference instanceof HTMLElement &&
                document.contains(reference)
            ) {
                reference.focus({preventScroll: true});
            }
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
            if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") {
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
        "#pear-runtime-menu-popover .pear-runtime-presence-menu-choice",
        function (this: HTMLElement, e) {
            if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") {
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            const response_policy = this.dataset["policy"];
            if (
                response_policy !== undefined &&
                response_policy !== topic_context?.response_policy
            ) {
                on_presence_change(response_policy);
            }
        },
    );

    $("body").on(
        "click keydown",
        "#pear-runtime-menu-popover .pear-runtime-effort-menu-choice",
        function (this: HTMLElement, e) {
            if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") {
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
    // The panel's store is the single fetch/poll authority; re-read it
    // whenever it changes instead of running a second interval against
    // the same endpoint. Read-only: a fetch here could recurse through
    // the store notification.
    pear_panel.on_topic_context_changed(() => {
        apply_shared_topic_context();
    });
}
