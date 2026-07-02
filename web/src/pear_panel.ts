// The Pear panel: live run status, run verbs, runtime controls, and
// the git-status delta for the current topic (R3: the chat header is
// authoritative for stream > topic == repo > branch, so the panel
// shows only what the header can't). It renders as an on-demand
// pane inside the reclaimed right area (#pear-right-area) — not on a
// permanent rail — and is a diff-patching subscriber of
// pear_runs_store: opening the pane subscribes (starting the poll),
// closing unsubscribes (stopping it), and every update is patched
// into the existing DOM so clicks, focus, tooltips, and the side
// textarea survive a 125ms live tick.

import $ from "jquery";

import render_pear_panel from "../templates/pear_panel.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import {patch_html} from "./pear_dom_patch.ts";
import * as pear_runs_store from "./pear_runs_store.ts";
import type {
    PearReasoningEffort,
    PearRun,
    PearRunEvent,
    PearSnapshot,
    PearStatusTransition,
    PearTopicContext,
    PearUsage,
} from "./pear_runs_store.ts";

type PearModelChoice = {
    value: string;
    label: string;
    selected: boolean;
};

type PearEffortChoice = PearReasoningEffort & {
    index: number;
    selected: boolean;
};

let initialized = false;
let panel_open = false;
let unsubscribe_from_store: (() => void) | undefined;
let last_error = "";
let last_model_error = "";
let last_runs: PearRun[] = [];
let last_topic_context: PearTopicContext | null | undefined;
// Matches the pear-status-flash animation duration in pear.css.
const STATUS_FLASH_DURATION_MS = 900;
const flash_by_run_id = new Map<number, {css_class: string; timer: number}>();

const fallback_efforts: PearReasoningEffort[] = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

function model_reasoning_efforts(
    topic_context: PearTopicContext | null | undefined,
    model: string | undefined,
): PearReasoningEffort[] {
    const selected_model_entry = topic_context?.model_catalog?.find(
        (entry) => entry.slug === model,
    );
    const model_efforts = selected_model_entry?.supported_reasoning_levels;
    if (model_efforts !== undefined && model_efforts.length > 0) {
        return model_efforts;
    }
    if (
        topic_context?.reasoning_efforts !== undefined &&
        topic_context.reasoning_efforts.length > 0
    ) {
        return topic_context.reasoning_efforts;
    }
    return fallback_efforts;
}

function compact_panel_text(text: string): string {
    return text
        .replaceAll(/!\[([^\]\n]*)\]\s*\(\s*([^)]+?)\s*\)/g, (_match, label: string) => {
            const clean_label = label.trim();
            return clean_label === "" ? $t({defaultMessage: "image"}) : clean_label;
        })
        .replaceAll(/\[([^\]\n]+)\]\s*\(\s*([^)]+?)\s*\)/g, (_match, label: string) => label.trim())
        .replaceAll(/\s+/g, " ")
        .trim();
}

function model_label(runtime_label: string): string {
    const match = /\bmodel=([^\s]+)/.exec(runtime_label);
    return match?.[1] ?? compact_panel_text(runtime_label);
}

function status_class(status: string): string {
    if (status === "running" || status === "queued") {
        return "active";
    }
    if (status === "failed" || status === "cancelled") {
        return "blocked";
    }
    return "idle";
}

function status_label(status: string): string {
    switch (status) {
        case "cancelled":
            return $t({defaultMessage: "Cancelled"});
        case "done":
            return $t({defaultMessage: "Done"});
        case "failed":
            return $t({defaultMessage: "Failed"});
        case "paused":
            return $t({defaultMessage: "Paused"});
        case "queued":
            return $t({defaultMessage: "Queued"});
        case "running":
            return $t({defaultMessage: "Running"});
        default:
            return compact_panel_text(status);
    }
}

function format_count(value: number | undefined): string {
    return value === undefined ? "0" : value.toLocaleString();
}

function display_usage(usage: PearUsage | null | undefined): {
    has_usage: boolean;
    usage_label: string;
    usage_title: string;
} {
    if (usage === undefined || usage === null) {
        return {has_usage: false, usage_label: "", usage_title: ""};
    }
    const cache_percent = usage.cache_percent ?? 0;
    const duration = usage.duration_seconds;
    const duration_label =
        typeof duration === "number" ? ` · ${duration.toFixed(duration < 10 ? 1 : 0)}s` : "";
    const usage_label = `cache ${cache_percent.toFixed(1)}%${duration_label}`;
    const cached = format_count(usage.cached_input_tokens);
    const input = format_count(usage.input_tokens);
    const output = format_count(usage.output_tokens);
    const reasoning = format_count(usage.reasoning_output_tokens);
    const usage_title =
        usage.summary ?? `${cached}/${input} cached; ${output} output; ${reasoning} reasoning.`;
    return {has_usage: true, usage_label, usage_title};
}

function display_event(event: PearRunEvent): PearRunEvent & {
    event_label: string;
    detail: string;
    has_detail: boolean;
} {
    const payload = event.payload ?? {};
    const event_label = payload.kind ?? event.event_type;
    const summary = compact_panel_text(event.summary);
    const detail = compact_panel_text(payload.body ?? payload.title ?? "");
    return {
        ...event,
        summary,
        event_label,
        detail,
        has_detail: detail.length > 0 && detail !== summary,
    };
}

function current_context(): {stream: string | undefined; topic: string | undefined} {
    return {
        stream: narrow_state.stream_name(),
        topic: narrow_state.topic(),
    };
}

function panel_element(): Element | null {
    // Guarded for the node-test environment, where there is no real
    // DOM for the panel to mount into.
    if (typeof document === "undefined") {
        return null;
    }
    return document.querySelector("#pear-panel");
}

function display_run(run: PearRun): PearRun & {
    status_class: string;
    status_label: string;
    session_kind_label: string;
    show_session_kind: boolean;
    source_label: string;
    model_label: string;
    details_open: boolean;
    can_pause: boolean;
    can_resume: boolean;
    can_cancel: boolean;
    has_actions: boolean;
    has_events: boolean;
    has_usage: boolean;
    usage_label: string;
    usage_title: string;
    flash_class: string;
    events: PearRunEvent[];
} {
    const events = (run.events ?? []).map((event) => display_event(event));
    const usage = display_usage(run.usage);
    const source_label =
        run.source_stream && run.source_topic ? `${run.source_stream} > ${run.source_topic}` : "";
    const can_pause = run.status === "running" || run.status === "queued";
    const can_resume = run.status === "paused";
    const can_cancel =
        run.status === "running" || run.status === "queued" || run.status === "paused";
    return {
        ...run,
        summary: compact_panel_text(run.summary),
        current_activity: compact_panel_text(run.current_activity),
        status_class: status_class(run.status),
        status_label: status_label(run.status),
        session_kind_label:
            run.session_kind === "side"
                ? $t({defaultMessage: "side"})
                : $t({defaultMessage: "shared"}),
        show_session_kind: run.session_kind === "side",
        source_label,
        model_label: model_label(run.runtime_label),
        details_open: run.status === "running" || run.status === "queued",
        can_pause,
        can_resume,
        can_cancel,
        has_actions: can_pause || can_resume || can_cancel,
        has_events: events.length > 0,
        ...usage,
        flash_class: flash_by_run_id.get(run.id)?.css_class ?? "",
        events,
    };
}

function display_topic_context(topic_context: PearTopicContext | undefined | null):
    | (PearTopicContext & {
          has_model_choices: boolean;
          display_model_choices: PearModelChoice[];
          display_reasoning_efforts: PearEffortChoice[];
          effort_index: number;
          max_effort_index: number;
          effort_label: string;
          git_available: boolean;
          git_status_class: string;
          git_status_label: string;
          git_head_short: string;
          show_ahead_behind: boolean;
          ahead_behind_label: string;
          ahead_behind_title: string;
          show_detached: boolean;
          detached_title: string;
          show_branch_mismatch: boolean;
          branch_mismatch_title: string;
      })
    | undefined {
    if (topic_context === undefined || topic_context === null) {
        return undefined;
    }
    const model_catalog = topic_context.model_catalog ?? [];
    const model_choices_from_catalog = model_catalog.map((entry) => ({
        value: entry.slug,
        label: entry.display_name || entry.slug,
        selected: entry.slug === topic_context.model,
    }));
    const seen_models = new Set(model_choices_from_catalog.map((entry) => entry.value));
    const display_model_choices = [...model_choices_from_catalog];
    for (const model of topic_context.model_choices ?? []) {
        if (!seen_models.has(model)) {
            display_model_choices.push({
                value: model,
                label: model,
                selected: model === topic_context.model,
            });
            seen_models.add(model);
        }
    }
    if (topic_context.model && !seen_models.has(topic_context.model)) {
        display_model_choices.unshift({
            value: topic_context.model,
            label: topic_context.model,
            selected: true,
        });
    }
    const reasoning_efforts = model_reasoning_efforts(topic_context, topic_context.model);
    const selected_effort_index = Math.max(
        0,
        reasoning_efforts.findIndex((effort) => effort.effort === topic_context.reasoning_effort),
    );
    const display_reasoning_efforts = reasoning_efforts.map((effort, index) => ({
        ...effort,
        index,
        selected: index === selected_effort_index,
    }));

    // The git DELTA row (R3): the chat header already states
    // stream > topic == repo > branch, so the panel renders only what
    // the header cannot say — dirty/clean, ahead/behind, detached
    // HEAD, and the branch≠topic edge case.
    const git = topic_context.git;
    const changed_count = git.changed_count ?? 0;
    const changed_label =
        changed_count === 1
            ? $t({defaultMessage: "1 changed"})
            : $t({defaultMessage: "{changed_count} changed"}, {changed_count});
    const git_status_label = git.dirty ? changed_label : $t({defaultMessage: "Clean"});
    const ahead = git.ahead ?? 0;
    const behind = git.behind ?? 0;
    const ahead_behind_parts = [];
    if (ahead > 0) {
        ahead_behind_parts.push(`↑${ahead}`);
    }
    if (behind > 0) {
        ahead_behind_parts.push(`↓${behind}`);
    }
    const upstream = git.upstream?.trim() ?? "";
    const ahead_behind_title = upstream
        ? $t(
              {defaultMessage: "{ahead} ahead, {behind} behind {upstream}"},
              {ahead, behind, upstream},
          )
        : $t({defaultMessage: "{ahead} ahead, {behind} behind"}, {ahead, behind});
    const detached = git.detached === true;
    const git_head_short = git.head_short?.trim() ?? git.head?.trim() ?? "";
    const detached_title = git_head_short
        ? $t({defaultMessage: "Detached HEAD at {head}"}, {head: git_head_short})
        : $t({defaultMessage: "Detached HEAD"});
    const checked_out_branch = git.branch?.trim() ?? "";
    // Detached worktrees show only the detached badge; the mismatch
    // badge covers the named-branch ≠ topic edge case.
    const show_branch_mismatch = git.available && !detached && git.branch_matches_topic === false;
    const branch_mismatch_title = checked_out_branch
        ? $t(
              {defaultMessage: "Checked out {branch}, which is not this topic's branch."},
              {branch: checked_out_branch},
          )
        : $t({defaultMessage: "The checked-out branch does not match this topic."});

    return {
        ...topic_context,
        github_repo: topic_context.github_repo.trim(),
        model_choices: topic_context.model_choices ?? [],
        has_model_choices: display_model_choices.length > 0,
        display_model_choices,
        display_reasoning_efforts,
        effort_index: selected_effort_index,
        max_effort_index: Math.max(0, display_reasoning_efforts.length - 1),
        effort_label:
            display_reasoning_efforts[selected_effort_index]?.label ??
            topic_context.reasoning_effort,
        git_available: git.available,
        git_status_class: git.dirty ? "dirty" : "clean",
        git_status_label,
        git_head_short,
        show_ahead_behind: ahead_behind_parts.length > 0,
        ahead_behind_label: ahead_behind_parts.join(" "),
        ahead_behind_title,
        show_detached: detached,
        detached_title,
        show_branch_mismatch,
        branch_mismatch_title,
    };
}

// Every active run gets its own status+verbs row, so multi-run topics
// never silently hide a live run; with nothing active, the most
// recent run keeps its terminal status (and done/failed flash)
// visible.
function display_runs(runs: PearRun[]): ReturnType<typeof display_run>[] {
    const active_runs = runs.filter(
        (run) => run.status === "running" || run.status === "queued" || run.status === "paused",
    );
    if (active_runs.length > 0) {
        return active_runs.map((run) => display_run(run));
    }
    return runs.slice(0, 1).map((run) => display_run(run));
}

// Pure rendering helper, shared by render() and the node test that
// asserts patch-vs-render equivalence.
export function render_panel_html(state: {
    runs: PearRun[];
    topic_context: PearTopicContext | null | undefined;
    stream: string | undefined;
    topic: string | undefined;
    error: string;
    model_error: string;
}): string {
    const has_topic = state.stream !== undefined && state.topic !== undefined;
    const runs = display_runs(state.runs);
    return render_pear_panel({
        has_topic,
        context_label: has_topic ? `${state.stream} > ${state.topic}` : "",
        topic_context: display_topic_context(state.topic_context),
        display_runs: runs,
        has_runs: runs.length > 0,
        error: state.error,
        model_error: state.model_error,
    });
}

function render(): void {
    const element = panel_element();
    if (element === null) {
        return;
    }
    const {stream, topic} = current_context();
    patch_html(
        element,
        render_panel_html({
            runs: last_runs,
            topic_context: last_topic_context,
            stream,
            topic,
            error: last_error,
            model_error: last_model_error,
        }),
    );
}

function on_store_update(snapshot: PearSnapshot): void {
    last_runs = snapshot.runs;
    last_topic_context = snapshot.topic_context;
    last_error = snapshot.error;
    render();
}

function on_status_transition(transition: PearStatusTransition): void {
    if (transition.from_status === undefined) {
        // First sighting of a run is not a transition worth flashing.
        return;
    }
    let css_class;
    if (transition.to_status === "done") {
        css_class = "pear-run-status-flash-done";
    } else if (transition.to_status === "failed" || transition.to_status === "cancelled") {
        css_class = "pear-run-status-flash-failed";
    } else {
        return;
    }
    const existing = flash_by_run_id.get(transition.run_id);
    if (existing !== undefined) {
        window.clearTimeout(existing.timer);
    }
    const timer = window.setTimeout(() => {
        flash_by_run_id.delete(transition.run_id);
        render();
    }, STATUS_FLASH_DURATION_MS);
    flash_by_run_id.set(transition.run_id, {css_class, timer});
    render();
}

export function is_open(): boolean {
    return panel_open;
}

export function open(): void {
    if (panel_open) {
        return;
    }
    panel_open = true;
    // Subscribing starts the store's polling; the panel re-renders on
    // every snapshot.
    unsubscribe_from_store = pear_runs_store.subscribe("narrow", on_store_update, {
        on_status_transition,
    });
}

export function close(): void {
    if (!panel_open) {
        return;
    }
    panel_open = false;
    if (unsubscribe_from_store !== undefined) {
        // Dropping the last subscriber stops the store's polling.
        unsubscribe_from_store();
        unsubscribe_from_store = undefined;
    }
}

function start_side_session(): void {
    const {stream, topic} = current_context();
    const side_request = $("#pear-side-request").val();
    if (stream === undefined || topic === undefined || typeof side_request !== "string") {
        return;
    }
    const request = side_request.trim();
    if (!request) {
        return;
    }
    void channel.post({
        url: "/json/pear/side",
        data: {
            stream,
            topic,
            request,
        },
        success(raw_data) {
            $("#pear-side-request").val("");
            pear_runs_store.refresh();
            const data = raw_data as {dm_narrow_url?: string};
            if (data.dm_narrow_url !== undefined) {
                window.location.hash = data.dm_narrow_url.slice(1);
            }
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to start Pear side session: ${xhr.responseText}`);
            }
        },
    });
}

function current_reasoning_effort_from_slider(): string {
    const slider_value = $("#pear-reasoning-effort-slider").val();
    const selected_model_value = $("#pear-topic-model-select").val();
    const selected_model =
        typeof selected_model_value === "string" ? selected_model_value : undefined;
    const efforts = model_reasoning_efforts(last_topic_context, selected_model);
    const effort_count = efforts.length;
    const index =
        typeof slider_value === "string"
            ? Math.min(Math.max(Number.parseInt(slider_value, 10) || 0, 0), effort_count - 1)
            : 0;
    return efforts[index]?.effort ?? last_topic_context?.reasoning_effort ?? "medium";
}

function update_reasoning_effort_label(): void {
    const effort = current_reasoning_effort_from_slider();
    const selected_model_value = $("#pear-topic-model-select").val();
    const selected_model =
        typeof selected_model_value === "string" ? selected_model_value : undefined;
    const choices = model_reasoning_efforts(last_topic_context, selected_model);
    const label = choices.find((choice) => choice.effort === effort)?.label ?? effort;
    $("#pear-reasoning-effort-value").text(label);
}

function save_topic_context(): void {
    const {stream, topic} = current_context();
    const model = $("#pear-topic-model-select").val();
    if (stream === undefined || topic === undefined || typeof model !== "string") {
        return;
    }
    const clean_model = model.trim();
    if (!clean_model) {
        last_model_error = $t({defaultMessage: "Choose a model."});
        render();
        return;
    }
    const reasoning_effort = current_reasoning_effort_from_slider();
    $(".pear-topic-runtime-control").prop("disabled", true);
    void channel.post({
        url: "/json/pear/topic-context",
        data: {
            stream,
            topic,
            model: clean_model,
            reasoning_effort,
        },
        success(raw_data) {
            const data = raw_data as {pear_topic_context?: PearTopicContext};
            last_model_error = "";
            if (data.pear_topic_context !== undefined) {
                last_topic_context = data.pear_topic_context;
            }
            render();
            pear_runs_store.refresh();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                last_model_error = $t({defaultMessage: "Model change failed."});
                blueslip.warn(`Failed to update Pear topic model: ${xhr.responseText}`);
                render();
            }
        },
        complete() {
            $(".pear-topic-runtime-control").prop("disabled", false);
        },
    });
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    $("body").on("click", "#pear-panel-refresh", () => {
        pear_runs_store.refresh();
    });
    $("body").on("click", ".pear-run-action", function () {
        const $button = $(this);
        const run_id = $button.attr("data-run-id");
        const action = $button.attr("data-action");
        if (run_id === undefined || action === undefined) {
            return;
        }
        pear_runs_store.post_run_action(run_id, action);
    });
    $("body").on("click", "#pear-side-start", () => {
        start_side_session();
    });
    $("body").on("change", "#pear-topic-model-select", () => {
        save_topic_context();
    });
    $("body").on("input", "#pear-reasoning-effort-slider", () => {
        update_reasoning_effort_label();
    });
    $("body").on("change", "#pear-reasoning-effort-slider", () => {
        save_topic_context();
    });
}
