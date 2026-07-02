// Pear runtime controls: the topbar "◇ model · effort" chip and its
// render-once topic-settings popover (model radios, segmented effort,
// pause/resume of active runs). This REPLACES the pear_panel's bare
// <select> + fake range slider, which the panel's 125ms diff-patch
// re-rendered on every tick and thereby fought the user (flicker).
//
// The chip lives in the Pear topbar's right cluster (rebuilt only on
// narrow change) and the popover body lives in tippy's popper appended
// to <body> — both provably OUTSIDE the 125ms-patched #pear-panel
// subtree. The popover renders ONCE per open (onShow → setContent) and
// re-renders its body only in direct response to the user's own
// save/toggle — never from a poll tick. Chip data comes from a single
// GET /json/pear/topic-context per narrow (cached), not a subscription.

import $ from "jquery";

import render_pear_runtime_popover from "../templates/popovers/pear_runtime_popover.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_runs_store from "./pear_runs_store.ts";
import type {PearReasoningEffort, PearTopicContext} from "./pear_runs_store.ts";
import * as popover_menus from "./popover_menus.ts";
import {parse_html} from "./ui_util.ts";

// Copied verbatim from pear_panel.ts (deleted there in P5d-4); the
// duplication is deliberate so P5d-3 and P5d-4 stay independent and no
// import cycle forms between the panel and these controls.
const fallback_efforts: PearReasoningEffort[] = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

export function model_reasoning_efforts(
    topic_context: PearTopicContext | null | undefined,
    model: string | undefined,
): PearReasoningEffort[] {
    const entry = topic_context?.model_catalog?.find((candidate) => candidate.slug === model);
    if (entry?.supported_reasoning_levels !== undefined && entry.supported_reasoning_levels.length > 0) {
        return entry.supported_reasoning_levels;
    }
    if (topic_context?.reasoning_efforts !== undefined && topic_context.reasoning_efforts.length > 0) {
        return topic_context.reasoning_efforts;
    }
    return fallback_efforts;
}

let initialized = false;
// Fetched once per narrow, refreshed by our own save responses. The
// popover reads this; it is never re-rendered by a poller.
let cached_context: PearTopicContext | undefined;
let cached_key = "";

function narrow_key(): string {
    return `${narrow_state.stream_name() ?? ""}\u0000${narrow_state.topic() ?? ""}`;
}

export function effort_label(context: PearTopicContext): string {
    const efforts = model_reasoning_efforts(context, context.model);
    return (
        efforts.find((effort) => effort.effort === context.reasoning_effort)?.label ??
        context.reasoning_effort
    );
}

export function chip_label(context: PearTopicContext): string {
    const model =
        context.model_catalog?.find((entry) => entry.slug === context.model)?.display_name ??
        context.model;
    return `◇ ${model} · ${effort_label(context)}`;
}

function render_chip(): void {
    // Guard for the node-test environment (no real DOM).
    if (typeof document === "undefined") {
        return;
    }
    // The branch≠topic mismatch chip (Row A) shares this context, so we
    // drive it here rather than adding a second topic-context fetch.
    const mismatch = document.querySelector("#pear-topbar .pear-branch-mismatch-chip");
    if (mismatch !== null) {
        const show = cached_context?.git.branch_matches_topic === false;
        mismatch.classList.toggle("notdisplayed", !show);
    }
    const cluster = document.querySelector("#pear-topbar .pear-topbar-right-cluster");
    if (cluster === null) {
        return;
    }
    let chip = cluster.querySelector("#pear-runtime-chip");
    if (cached_context === undefined || !cached_context.git.available) {
        chip?.remove();
        return;
    }
    if (chip === null) {
        chip = document.createElement("button");
        chip.id = "pear-runtime-chip";
        chip.className = "pear-runtime-chip";
        chip.setAttribute("type", "button");
        chip.setAttribute("aria-haspopup", "true");
        cluster.append(chip);
    }
    chip.textContent = chip_label(cached_context);
}

// Called at the end of pear_topbar.rerender() (single choke point) and
// by our own save/fetch success paths.
export function update_chip(): void {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    const key = narrow_key();
    if (stream === undefined || topic === undefined) {
        cached_context = undefined;
        cached_key = key;
        render_chip();
        return;
    }
    if (key === cached_key && cached_context !== undefined) {
        render_chip();
        return;
    }
    cached_key = key;
    void channel.get({
        url: "/json/pear/topic-context",
        data: {stream, topic},
        success(raw_data) {
            if (narrow_key() !== key) {
                // The narrow moved on while the request was in flight.
                return;
            }
            const data = raw_data as {pear_topic_context?: PearTopicContext};
            cached_context = data.pear_topic_context;
            render_chip();
        },
        error() {
            // Non-pear / failed narrow: keep the chip hidden.
            if (narrow_key() === key) {
                cached_context = undefined;
                render_chip();
            }
        },
    });
}

type PopoverContext = {
    models: {value: string; label: string; selected: boolean}[];
    efforts: (PearReasoningEffort & {selected: boolean})[];
    active_runs: {id: number; summary: string; is_paused: boolean}[];
};

function popover_context(): PopoverContext | undefined {
    const context = cached_context;
    if (context === undefined) {
        return undefined;
    }
    const catalog = context.model_catalog ?? [];
    const seen = new Set(catalog.map((entry) => entry.slug));
    const models = catalog.map((entry) => ({
        value: entry.slug,
        label: entry.display_name || entry.slug,
        selected: entry.slug === context.model,
    }));
    for (const slug of context.model_choices ?? []) {
        if (!seen.has(slug)) {
            models.push({value: slug, label: slug, selected: slug === context.model});
            seen.add(slug);
        }
    }
    if (context.model !== "" && !seen.has(context.model)) {
        models.unshift({value: context.model, label: context.model, selected: true});
    }
    const efforts = model_reasoning_efforts(context, context.model).map((effort) => ({
        ...effort,
        selected: effort.effort === context.reasoning_effort,
    }));
    // Pause section: active runs only. The server exposes pause only at
    // the per-run endpoints; a run-independent topic pause needs a small
    // additive server param, deferred out of P5d.
    const active_runs = pear_runs_store
        .get_snapshot("narrow")
        .runs.filter((run) => ["running", "queued", "paused"].includes(run.status))
        .map((run) => ({
            id: run.id,
            summary: run.summary,
            is_paused: run.status === "paused",
        }));
    return {models, efforts, active_runs};
}

function refresh_popover_body($popper: JQuery): void {
    const context = popover_context();
    if (context === undefined) {
        return;
    }
    // Replace only the popover body; the tippy instance is untouched.
    $popper
        .find(".pear-runtime-popover")
        .replaceWith(parse_html(render_pear_runtime_popover(context)));
}

function save(overrides: {model?: string; reasoning_effort?: string}, $popper: JQuery): void {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    const context = cached_context;
    if (stream === undefined || topic === undefined || context === undefined) {
        return;
    }
    const model = overrides.model ?? context.model;
    // Clamp effort to the (possibly new) model's supported levels so the
    // UI never shows a level the model rejects.
    const efforts = model_reasoning_efforts(context, model);
    let reasoning_effort = overrides.reasoning_effort ?? context.reasoning_effort;
    if (!efforts.some((effort) => effort.effort === reasoning_effort)) {
        reasoning_effort = efforts[0]?.effort ?? reasoning_effort;
    }
    // Guard against rapid clicks: each model change posts a bot message.
    $popper.find(".pear-runtime-popover-control").prop("disabled", true);
    void channel.post({
        url: "/json/pear/topic-context",
        data: {stream, topic, model, reasoning_effort},
        success(raw_data) {
            const data = raw_data as {pear_topic_context?: PearTopicContext};
            if (data.pear_topic_context !== undefined) {
                cached_context = data.pear_topic_context;
            }
            render_chip();
            refresh_popover_body($popper);
            pear_runs_store.refresh();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to update Pear runtime: ${xhr.responseText}`);
                $popper
                    .find(".pear-runtime-popover-error")
                    .text($t({defaultMessage: "Saving failed."}))
                    .removeClass("hidden");
            }
        },
        complete() {
            $popper.find(".pear-runtime-popover-control").prop("disabled", false);
        },
    });
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    popover_menus.register_popover_menu(
        "#pear-runtime-chip",
        {
            theme: "popover-menu",
            placement: "bottom-end",
            popperOptions: {
                modifiers: [{name: "flip", options: {fallbackPlacements: ["top", "left"]}}],
            },
            onShow(instance) {
                popover_menus.popover_instances.pear_runtime = instance;
                popover_menus.on_show_prep(instance);
                const context = popover_context();
                if (context === undefined) {
                    return false;
                }
                instance.setContent(parse_html(render_pear_runtime_popover(context)));
                return undefined;
            },
            onMount(instance) {
                popover_menus.focus_popover(instance);
                const $popper = $(instance.popper);
                $popper.on("change", "input[name='pear-model-select']", (e) => {
                    save({model: $(e.currentTarget).attr("data-model-slug")!}, $popper);
                });
                $popper.on("click", ".pear-effort-segment", (e) => {
                    e.preventDefault();
                    save({reasoning_effort: $(e.currentTarget).attr("data-effort")!}, $popper);
                });
                $popper.on("click", ".pear-run-pause-toggle", (e) => {
                    e.preventDefault();
                    const $button = $(e.currentTarget);
                    pear_runs_store.post_run_action(
                        $button.attr("data-run-id")!,
                        $button.attr("data-action")!,
                    );
                    // post_run_action already refresh()es the store; give
                    // it a beat, then re-render the body once.
                    setTimeout(() => {
                        refresh_popover_body($popper);
                    }, 400);
                });
            },
            onHidden(instance) {
                // Required by the register_popover_menu contract, else
                // tippy instances leak.
                instance.destroy();
                popover_menus.popover_instances.pear_runtime = null;
            },
        },
        {also_trigger_on_enter: true},
    );
}
