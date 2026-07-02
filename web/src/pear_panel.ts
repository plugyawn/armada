import $ from "jquery";

import render_pear_panel from "../templates/pear_panel.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_run_state from "./pear_run_state.ts";

type PearRunEvent = {
    id: number;
    event_type: string;
    summary: string;
    payload?: {
        kind?: string;
        status?: string;
        title?: string;
        body?: string;
    };
};

type PearUsage = {
    input_tokens?: number;
    cached_input_tokens?: number;
    output_tokens?: number;
    reasoning_output_tokens?: number;
    total_tokens?: number;
    cache_percent?: number;
    duration_seconds?: number | null;
    summary?: string;
};

type PearLiveRun = {
    status: string;
    content: string;
    updated_at: number;
};

type PearRun = {
    id: number;
    trigger_message_id: number;
    result_message_id?: number | null;
    status: string;
    session_kind: string;
    source_stream: string;
    source_topic: string;
    summary: string;
    current_activity: string;
    runtime_label: string;
    sandbox: string;
    approval_policy: string;
    workdir: string;
    codex_thread_id: string;
    usage?: PearUsage | null;
    live?: PearLiveRun | null;
    events?: PearRunEvent[];
};

type PearRunsResponse = {
    pear_runs: PearRun[];
    pear_topic_context?: PearTopicContext | null;
    runtime_config?: {
        model?: string | null;
        model_choices?: string[];
        model_catalog?: PearModelCatalogEntry[];
        reasoning_effort?: string | null;
        reasoning_efforts?: PearReasoningEffort[];
    };
};

type PearReasoningEffort = {
    effort: string;
    label: string;
    description?: string;
};

type PearModelCatalogEntry = {
    slug: string;
    display_name: string;
    default_reasoning_level?: string;
    supported_reasoning_levels?: PearReasoningEffort[];
};

type PearGitChangedFile = {
    status: string;
    kind: string;
    path: string;
};

type PearGitCommit = {
    hash: string;
    summary: string;
};

type PearGitContext = {
    available: boolean;
    workdir?: string;
    repo_name?: string;
    repo_label?: string;
    remote_url?: string;
    worktree_name?: string;
    branch?: string;
    head?: string;
    dirty?: boolean;
    changed_count?: number;
    changed_files?: PearGitChangedFile[];
    changed_files_truncated?: boolean;
    diff_shortstat?: string;
    recent_commits?: PearGitCommit[];
};

type PearTopicContext = {
    stream: string;
    topic: string;
    model: string;
    default_model: string;
    model_choices: string[];
    model_catalog?: PearModelCatalogEntry[];
    reasoning_effort: string;
    default_reasoning_effort: string;
    reasoning_efforts: PearReasoningEffort[];
    github_repo: string;
    pinned_repo?: string;
    repo_label?: string;
    repo_title?: string;
    worktree_label?: string;
    workdir: string;
    git: PearGitContext;
};

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
let refresh_timer: number | undefined;
let live_refresh_timer: number | undefined;
let live_refresh_in_flight = false;
let last_error = "";
let last_model_error = "";
let last_runs: PearRun[] = [];
let last_topic_context: PearTopicContext | null | undefined;
let rendered_context_key = "";
const worktree_details_open_by_context = new Map<string, boolean>();
const LIVE_REFRESH_INTERVAL_MS = 125;
const FULL_REFRESH_INTERVAL_MS = 5000;

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

function context_key(stream: string | undefined, topic: string | undefined): string {
    if (stream === undefined || topic === undefined) {
        return "";
    }
    return `${stream}\n${topic}`;
}

function remember_worktree_details_state(): void {
    if (rendered_context_key === "") {
        return;
    }
    const details = document.querySelector<HTMLDetailsElement>(
        "#pear-panel details.pear-worktree-details",
    );
    if (details !== null) {
        worktree_details_open_by_context.set(rendered_context_key, details.open);
    }
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
          has_changed_files: boolean;
          has_recent_commits: boolean;
          changed_label: string;
          has_pinned_repo: boolean;
          workspace_primary_name: string;
          workspace_primary_title: string;
          workspace_kind_label: string;
          workspace_ref_label: string;
          worktree_name: string;
          worktree_details_open: boolean;
          local_worktree_summary_label: string;
          show_primary_git_status: boolean;
      })
    | undefined {
    if (topic_context === undefined || topic_context === null) {
        return undefined;
    }
    const git = topic_context.git;
    const changed_count = git.changed_count ?? 0;
    const changed_label =
        changed_count === 1
            ? $t({defaultMessage: "1 changed file"})
            : $t({defaultMessage: "{changed_count} changed files"}, {changed_count});
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
    const github_repo = topic_context.github_repo.trim();
    const pinned_repo = topic_context.pinned_repo?.trim() ?? "";
    const has_pinned_repo = pinned_repo.length > 0;
    const git_repo_label = git.repo_label?.trim() ?? "";
    const repo_label = topic_context.repo_label?.trim() || git_repo_label;
    const has_workspace_repo =
        repo_label.length > 0 || has_pinned_repo || (git.available && git_repo_label.length > 0);
    const workdir_parts = topic_context.workdir.split("/").filter((part) => part.trim().length > 0);
    const worktree_name =
        topic_context.worktree_label?.trim() ||
        git.worktree_name ||
        workdir_parts[workdir_parts.length - 1] ||
        "";
    const workspace_primary_name = repo_label || $t({defaultMessage: "No repo pinned"});
    const workspace_ref_parts = [git.branch, git.head].filter(
        (part): part is string => part !== undefined && part.trim().length > 0,
    );
    const git_status_label = git.dirty ? changed_label : $t({defaultMessage: "clean"});
    const local_worktree_ref_label = workspace_ref_parts.join(" / ");
    const local_worktree_summary_label = [worktree_name, git_status_label, local_worktree_ref_label]
        .filter((part) => part.trim().length > 0)
        .join(" / ");
    return {
        ...topic_context,
        github_repo,
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
        has_changed_files: (git.changed_files ?? []).length > 0,
        has_recent_commits: (git.recent_commits ?? []).length > 0,
        changed_label,
        has_pinned_repo,
        workspace_primary_name,
        workspace_primary_title:
            topic_context.repo_title?.trim() ||
            pinned_repo ||
            (has_workspace_repo
                ? topic_context.workdir
                : $t({defaultMessage: "Ask Pear to pin a Git repository for this topic."})),
        workspace_kind_label: $t({defaultMessage: "Repo"}),
        workspace_ref_label: "",
        worktree_name,
        worktree_details_open:
            worktree_details_open_by_context.get(
                context_key(topic_context.stream, topic_context.topic),
            ) ?? false,
        local_worktree_summary_label,
        show_primary_git_status: has_workspace_repo && git.available,
    };
}

function active_display_run(runs: PearRun[]): ReturnType<typeof display_run> | undefined {
    const active_run =
        runs.find(
            (run) => run.status === "running" || run.status === "queued" || run.status === "paused",
        ) ?? runs[0];
    return active_run === undefined ? undefined : display_run(active_run);
}

function run_is_active(run: {status: string} | undefined): boolean {
    return run?.status === "running" || run?.status === "queued";
}

function clear_live_refresh(): void {
    if (live_refresh_timer !== undefined) {
        window.clearTimeout(live_refresh_timer);
        live_refresh_timer = undefined;
    }
}

function schedule_live_refresh(active_run: {status: string} | undefined): void {
    if (!initialized || !run_is_active(active_run)) {
        clear_live_refresh();
        return;
    }
    if (live_refresh_timer !== undefined || live_refresh_in_flight) {
        return;
    }
    live_refresh_timer = window.setTimeout(() => {
        live_refresh_timer = undefined;
        refresh({live: true});
    }, LIVE_REFRESH_INTERVAL_MS);
}

function render(
    runs: PearRun[] = last_runs,
    topic_context: PearTopicContext | null | undefined = last_topic_context,
): void {
    last_runs = runs;
    last_topic_context = topic_context;
    remember_worktree_details_state();
    const {stream, topic} = current_context();
    const has_topic = stream !== undefined && topic !== undefined;
    const next_context_key = context_key(stream, topic);
    const active_run = active_display_run(runs);
    pear_run_state.set_active_run_result_messages(runs, {
        force_reconcile: active_run === undefined,
    });
    const panel_status = active_run?.status ?? "idle";
    $("#pear-panel").html(
        render_pear_panel({
            has_topic,
            context_label: has_topic ? `${stream} > ${topic}` : "",
            topic_context: display_topic_context(topic_context),
            active_run,
            error: last_error,
            model_error: last_model_error,
            status_label: panel_status,
            status_class: status_class(panel_status),
        }),
    );
    rendered_context_key = next_context_key;
    schedule_live_refresh(active_run);
}

export function refresh(options: {live?: boolean} = {}): void {
    const live = options.live === true;
    if (live && live_refresh_in_flight) {
        return;
    }
    const {stream, topic} = current_context();
    const data: Record<string, string | boolean> = {
        include_events: false,
        include_context: !live,
    };
    if (stream !== undefined && topic !== undefined) {
        data["stream"] = stream;
        data["topic"] = topic;
    }
    if (live) {
        live_refresh_in_flight = true;
    }
    void channel.get({
        url: "/json/pear/runs",
        data,
        success(raw_data) {
            const data = raw_data as PearRunsResponse;
            last_error = "";
            const topic_context = live
                ? (data.pear_topic_context ?? last_topic_context)
                : data.pear_topic_context === undefined
                  ? last_topic_context
                  : data.pear_topic_context;
            render(data.pear_runs, topic_context);
        },
        error(xhr) {
            if (xhr.readyState === 0) {
                return;
            }
            last_error = $t({defaultMessage: "Pear status is unavailable."});
            blueslip.warn(`Failed to fetch Pear runs: ${xhr.responseText}`);
            render();
        },
        complete() {
            if (live) {
                live_refresh_in_flight = false;
                schedule_live_refresh(active_display_run(last_runs));
            }
        },
    });
}

function post_run_action(run_id: string, action: string): void {
    void channel.post({
        url: `/json/pear/runs/${run_id}/${action}`,
        success() {
            refresh();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to ${action} Pear run: ${xhr.responseText}`);
            }
        },
    });
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
            refresh();
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
            render(last_runs, data.pear_topic_context);
            refresh();
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
        refresh();
        return;
    }
    initialized = true;
    $("body").on("click", "#pear-panel-refresh", () => {
        refresh();
    });
    $("body").on("click", ".pear-run-action", function () {
        const $button = $(this);
        const run_id = $button.attr("data-run-id");
        const action = $button.attr("data-action");
        if (run_id === undefined || action === undefined) {
            return;
        }
        post_run_action(run_id, action);
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
    refresh();
    refresh_timer = window.setInterval(refresh, FULL_REFRESH_INTERVAL_MS);
}

export function stop(): void {
    if (refresh_timer !== undefined) {
        window.clearInterval(refresh_timer);
        refresh_timer = undefined;
    }
    clear_live_refresh();
    live_refresh_in_flight = false;
}
