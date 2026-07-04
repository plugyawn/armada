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

type PearWorkCursorState = {
    visible: boolean;
    status_class: string;
    status_marker: string;
    repo_label: string;
    branch_label: string;
    head_label: string;
    status_label: string;
    title: string;
};

type PearWorkbenchView = "diff" | "history" | "side";

let initialized = false;
let refresh_timer: number | undefined;
let live_refresh_timer: number | undefined;
let live_refresh_in_flight = false;
let last_error = "";
let last_runs: PearRun[] = [];
let last_topic_context: PearTopicContext | null | undefined;
let last_render_state_key = "";
let last_context_refresh_key = "";
let last_context_refresh_at = 0;
const work_panel_open_by_context = new Map<string, boolean>();
const workbench_view_by_context = new Map<string, PearWorkbenchView>();
const side_request_by_context = new Map<string, string>();
const LIVE_REFRESH_INTERVAL_MS = 125;
const FULL_REFRESH_INTERVAL_MS = 5000;
const CONTEXT_REFRESH_INTERVAL_MS = 60_000;

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

function first_nonempty_string(values: (string | undefined)[]): string {
    return values.find((value) => value !== undefined && value.trim().length > 0) ?? "";
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

function status_marker(status: string): string {
    switch (status) {
        case "cancelled":
        case "failed":
            return "!";
        case "done":
            return "✓";
        case "paused":
            return "‖";
        case "queued":
        case "running":
            return "✦";
        default:
            return "·";
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

function is_pear_runs_response(data: unknown): data is PearRunsResponse {
    return (
        typeof data === "object" &&
        data !== null &&
        "pear_runs" in data &&
        Array.isArray(data.pear_runs)
    );
}

function is_pear_side_response(data: unknown): data is {dm_narrow_url?: string} {
    return (
        typeof data === "object" &&
        data !== null &&
        (!("dm_narrow_url" in data) || typeof data.dm_narrow_url === "string")
    );
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

function current_context_key(): string {
    const {stream, topic} = current_context();
    return context_key(stream, topic);
}

function run_keeps_work_panel_open(run: {status: string} | undefined): boolean {
    return run !== undefined && run.status !== "done";
}

export function work_panel_open_for_testing(options: {
    context_key_value: string;
    manually_open: boolean;
    active_run_status: string | undefined;
    has_error: boolean;
}): boolean {
    if (options.context_key_value === "") {
        return false;
    }
    if (options.has_error) {
        return true;
    }
    return (
        options.manually_open ||
        run_keeps_work_panel_open(
            options.active_run_status === undefined
                ? undefined
                : {status: options.active_run_status},
        )
    );
}

function right_sidebar_is_visible(): boolean {
    const $right_sidebar_column = $(".app-main .column-right");
    return (
        ($right_sidebar_column.css("display") !== "none" &&
            !$("body").hasClass("hide-right-sidebar")) ||
        $right_sidebar_column.hasClass("expanded")
    );
}

function git_has_diff(git: PearGitContext): boolean {
    return (git.changed_files ?? []).length > 0 || (git.diff_shortstat?.trim() ?? "") !== "";
}

function git_has_history(git: PearGitContext): boolean {
    return (git.recent_commits ?? []).length > 0;
}

export function workbench_view_for_testing(options: {
    requested: string | undefined;
    has_diff: boolean;
    has_history: boolean;
}): PearWorkbenchView {
    if (options.requested === "side") {
        return "side";
    }
    if (options.requested === "history" && options.has_history) {
        return "history";
    }
    if (options.requested === "diff" && options.has_diff) {
        return "diff";
    }
    if (options.has_diff) {
        return "diff";
    }
    if (options.has_history) {
        return "history";
    }
    return "side";
}

export function open_work_details(requested_view?: PearWorkbenchView): void {
    const key = current_context_key();
    if (key !== "") {
        work_panel_open_by_context.set(key, true);
        if (last_topic_context !== undefined && last_topic_context !== null) {
            workbench_view_by_context.set(
                key,
                workbench_view_for_testing({
                    requested: requested_view ?? workbench_view_by_context.get(key),
                    has_diff: git_has_diff(last_topic_context.git),
                    has_history: git_has_history(last_topic_context.git),
                }),
            );
        } else if (requested_view !== undefined) {
            workbench_view_by_context.set(key, requested_view);
        }
    }
    render();
    if (!right_sidebar_is_visible()) {
        $("#userlist-toggle-button").trigger("click");
    }
    const workbench = document.querySelector<HTMLElement>("#pear-panel .pear-workbench");
    if (workbench !== null) {
        workbench.scrollIntoView({block: "nearest"});
        return;
    }
    refresh({force_context: true});
}

function close_work_details(): void {
    const key = current_context_key();
    if (key !== "") {
        work_panel_open_by_context.set(key, false);
    }
    render();
}

function display_run(run: PearRun): PearRun & {
    status_class: string;
    status_label: string;
    status_marker: string;
    session_kind_label: string;
    show_session_kind: boolean;
    source_label: string;
    model_label: string;
    details_open: boolean;
    is_active: boolean;
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
        status_marker: status_marker(run.status),
        session_kind_label:
            run.session_kind === "side"
                ? $t({defaultMessage: "side"})
                : $t({defaultMessage: "shared"}),
        show_session_kind: run.session_kind === "side",
        source_label,
        model_label: model_label(run.runtime_label),
        details_open: run.status === "running" || run.status === "queued",
        is_active: run.status === "running" || run.status === "queued",
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
          has_changed_files: boolean;
          has_recent_commits: boolean;
          changed_label: string;
          has_workbench_diff: boolean;
          has_workbench_history: boolean;
          workbench_view: PearWorkbenchView;
          workbench_diff_selected: boolean;
          workbench_history_selected: boolean;
          workbench_side_selected: boolean;
          workbench_diff_disabled: boolean;
          workbench_history_disabled: boolean;
          diff_tab_label: string;
          history_tab_label: string;
          side_tab_label: string;
          workbench_title: string;
          workbench_title_attr: string;
          workbench_meta_label: string;
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
    const github_repo = topic_context.github_repo.trim();
    const pinned_repo = topic_context.pinned_repo?.trim() ?? "";
    const git_repo_label = git.repo_label?.trim() ?? "";
    const repo_label = first_nonempty_string([topic_context.repo_label?.trim(), git_repo_label]);
    const workdir_parts = topic_context.workdir.split("/").filter((part) => part.trim().length > 0);
    const worktree_name = first_nonempty_string([
        topic_context.worktree_label?.trim(),
        git.worktree_name,
        workdir_parts.at(-1),
    ]);
    const workspace_primary_name = first_nonempty_string([
        repo_label,
        $t({defaultMessage: "No repo pinned"}),
    ]);
    const has_workbench_diff = git_has_diff(git);
    const has_workbench_history = git_has_history(git);
    const recent_commit_count = (git.recent_commits ?? []).length;
    const workbench_view = workbench_view_for_testing({
        requested: workbench_view_by_context.get(
            context_key(topic_context.stream, topic_context.topic),
        ),
        has_diff: has_workbench_diff,
        has_history: has_workbench_history,
    });
    let workbench_meta_label = "";
    if (workbench_view === "diff") {
        workbench_meta_label = first_nonempty_string([
            git.diff_shortstat?.trim(),
            has_workbench_diff ? changed_label : $t({defaultMessage: "No local diff"}),
        ]);
    } else if (workbench_view === "history") {
        workbench_meta_label =
            recent_commit_count === 1
                ? $t({defaultMessage: "1 recent commit"})
                : $t(
                      {defaultMessage: "{recent_commit_count} recent commits"},
                      {recent_commit_count},
                  );
    } else {
        workbench_meta_label = $t({defaultMessage: "Private side request"});
    }
    const workspace_primary_title = first_nonempty_string([
        topic_context.repo_title?.trim(),
        pinned_repo,
        topic_context.workdir,
        $t({defaultMessage: "Ask Pear to pin a Git repository for this topic."}),
    ]);
    return {
        ...topic_context,
        github_repo,
        model_choices: topic_context.model_choices ?? [],
        has_changed_files: (git.changed_files ?? []).length > 0,
        has_recent_commits: (git.recent_commits ?? []).length > 0,
        changed_label,
        has_workbench_diff,
        has_workbench_history,
        workbench_view,
        workbench_diff_selected: workbench_view === "diff",
        workbench_history_selected: workbench_view === "history",
        workbench_side_selected: workbench_view === "side",
        workbench_diff_disabled: !has_workbench_diff && workbench_view !== "diff",
        workbench_history_disabled: !has_workbench_history && workbench_view !== "history",
        diff_tab_label:
            changed_count > 0
                ? $t({defaultMessage: "Diff ({changed_count})"}, {changed_count})
                : $t({defaultMessage: "Diff"}),
        history_tab_label:
            recent_commit_count > 0
                ? $t({defaultMessage: "History ({recent_commit_count})"}, {recent_commit_count})
                : $t({defaultMessage: "History"}),
        side_tab_label: $t({defaultMessage: "Side"}),
        workbench_title: first_nonempty_string([
            git.branch?.trim(),
            worktree_name,
            workspace_primary_name,
            $t({defaultMessage: "Workbench"}),
        ]),
        workbench_title_attr: first_nonempty_string([
            topic_context.workdir,
            workspace_primary_title,
            worktree_name,
        ]),
        workbench_meta_label,
    };
}

export function work_cursor_state_for_testing(
    topic_context: PearTopicContext | undefined | null,
): PearWorkCursorState {
    if (topic_context === undefined || topic_context === null) {
        return {
            visible: false,
            status_class: "",
            status_marker: "",
            repo_label: "",
            branch_label: "",
            head_label: "",
            status_label: "",
            title: "",
        };
    }
    const git = topic_context.git;
    const repo_label = first_nonempty_string([
        topic_context.github_repo.trim(),
        topic_context.repo_label?.trim(),
        git.repo_label?.trim(),
        topic_context.pinned_repo?.trim(),
    ]);
    const branch_label = git.branch?.trim() ?? "";
    const head_label = git.head?.trim() ?? "";
    const changed_count = git.changed_count ?? 0;
    const status_label = git.dirty
        ? changed_count > 0
            ? $t({defaultMessage: "+{changed_count}"}, {changed_count})
            : $t({defaultMessage: "changed"})
        : $t({defaultMessage: "clean"});
    const status_marker = git.dirty ? "✶" : "✓";
    const parts = [repo_label, branch_label, head_label, status_label].filter(
        (part) => part.length > 0,
    );
    return {
        visible: git.available && parts.length > 0,
        status_class: git.dirty ? "dirty" : "clean",
        status_marker,
        repo_label,
        branch_label,
        head_label,
        status_label,
        title: parts.join(" / "),
    };
}

export function update_work_cursor(
    topic_context: PearTopicContext | null | undefined = last_topic_context,
): void {
    const $cursor = $("#pear-work-cursor");
    if ($cursor.length === 0) {
        return;
    }
    const {stream, topic} = current_context();
    if (
        topic_context === undefined ||
        topic_context === null ||
        stream === undefined ||
        topic === undefined ||
        topic_context.stream !== stream ||
        topic_context.topic !== topic
    ) {
        $cursor.empty().addClass("hidden").removeAttr("role tabindex aria-label title");
        return;
    }
    const state = work_cursor_state_for_testing(topic_context);
    if (!state.visible) {
        $cursor.empty().addClass("hidden").removeAttr("role tabindex aria-label title");
        return;
    }
    $cursor
        .empty()
        .removeClass("hidden pear-work-cursor-clean pear-work-cursor-dirty")
        .addClass(`pear-work-cursor-${state.status_class}`)
        .attr({
            "aria-label": $t({defaultMessage: "Open Pear work details"}),
            role: "button",
            tabindex: "0",
            title: state.title,
        });
    $("<span>")
        .addClass("pear-ui-glyph pear-work-cursor-marker")
        .attr("aria-hidden", "true")
        .text(state.status_marker)
        .appendTo($cursor);
    $("<span>").addClass("pear-work-cursor-repo").text(state.repo_label).appendTo($cursor);
    if (state.branch_label !== "") {
        $("<span>")
            .addClass("pear-work-cursor-ref pear-work-cursor-target")
            .attr({
                "data-pear-workbench-view": "history",
                title: $t({defaultMessage: "Open Pear history"}),
            })
            .text(state.branch_label)
            .appendTo($cursor);
    }
    if (state.head_label !== "") {
        $("<span>")
            .addClass("pear-work-cursor-head pear-work-cursor-target")
            .attr({
                "data-pear-workbench-view": "history",
                title: $t({defaultMessage: "Open Pear history"}),
            })
            .text(state.head_label)
            .appendTo($cursor);
    }
    const $status = $("<span>").addClass("pear-work-cursor-status").text(state.status_label);
    if (state.status_class === "dirty") {
        $status.addClass("pear-work-cursor-target").attr({
            "data-pear-workbench-view": "diff",
            title: $t({defaultMessage: "Open Pear diff"}),
        });
    }
    $status.appendTo($cursor);
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

function has_active_run(runs: {status: string}[]): boolean {
    return runs.some((run) => run.status === "running" || run.status === "queued");
}

export function should_refresh_context_for_testing(options: {
    context_key_value: string;
    force_context: boolean;
    live: boolean;
    now: number;
    last_refresh_key: string;
    last_refresh_at: number;
    has_context: boolean;
}): boolean {
    if (options.live) {
        return false;
    }
    if (options.context_key_value === "") {
        return false;
    }
    if (options.force_context) {
        return true;
    }
    if (!options.has_context) {
        return true;
    }
    if (options.last_refresh_key !== options.context_key_value) {
        return true;
    }
    return options.now - options.last_refresh_at >= CONTEXT_REFRESH_INTERVAL_MS;
}

function should_refresh_context(
    stream: string | undefined,
    topic: string | undefined,
    options: {
        force_context: boolean;
        live: boolean;
    },
): boolean {
    return should_refresh_context_for_testing({
        context_key_value:
            stream === undefined || topic === undefined ? "" : context_key(stream, topic),
        force_context: options.force_context,
        live: options.live,
        now: Date.now(),
        last_refresh_key: last_context_refresh_key,
        last_refresh_at: last_context_refresh_at,
        has_context: last_topic_context !== undefined && last_topic_context !== null,
    });
}

function record_context_refresh(stream: string | undefined, topic: string | undefined): void {
    if (stream === undefined || topic === undefined) {
        return;
    }
    last_context_refresh_key = context_key(stream, topic);
    last_context_refresh_at = Date.now();
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

function render_state_key(render_state: object): string {
    return JSON.stringify(render_state);
}

export function topic_context_after_refresh_for_testing(options: {
    include_context: boolean;
    live: boolean;
    response_topic_context: PearTopicContext | null | undefined;
    last_topic_context: PearTopicContext | null | undefined;
}): PearTopicContext | null | undefined {
    if (options.live || !options.include_context) {
        return options.response_topic_context ?? options.last_topic_context;
    }
    return options.response_topic_context ?? null;
}

export function render_state_changed_for_testing(
    previous_key: string,
    render_state: object,
): boolean {
    return previous_key !== render_state_key(render_state);
}

function render(
    runs: PearRun[] = last_runs,
    topic_context: PearTopicContext | null | undefined = last_topic_context,
): void {
    last_runs = runs;
    last_topic_context = topic_context;
    const {stream, topic} = current_context();
    const has_topic = stream !== undefined && topic !== undefined;
    const next_context_key = context_key(stream, topic);
    const active_run = active_display_run(runs);
    const panel_open = work_panel_open_for_testing({
        context_key_value: next_context_key,
        manually_open: work_panel_open_by_context.get(next_context_key) ?? false,
        active_run_status: active_run?.status,
        has_error: last_error !== "",
    });
    pear_run_state.set_active_run_result_messages(runs, {
        force_reconcile: active_run === undefined,
    });
    const panel_status = active_run?.status ?? "idle";
    const render_state = {
        has_topic,
        context_label: has_topic ? `${stream} > ${topic}` : "",
        panel_open,
        panel_can_close: panel_open && !run_keeps_work_panel_open(active_run),
        topic_context: display_topic_context(topic_context),
        active_run,
        error: last_error,
        status_label: panel_status,
        status_class: status_class(panel_status),
    };
    const next_render_state_key = render_state_key(render_state);
    if (last_render_state_key !== next_render_state_key) {
        const side_request = $("#pear-side-request").val();
        if (typeof side_request === "string" && next_context_key !== "") {
            side_request_by_context.set(next_context_key, side_request);
        }
        $("#pear-panel").html(render_pear_panel(render_state));
        $("#pear-panel").toggleClass("pear-panel-collapsed", !panel_open);
        const stored_side_request = side_request_by_context.get(next_context_key);
        if (stored_side_request !== undefined) {
            $("#pear-side-request").val(stored_side_request);
        }
        $(document).trigger("pear_liveness_markers_changed");
        last_render_state_key = next_render_state_key;
    }
    $("#pear-panel").toggleClass("pear-panel-collapsed", !panel_open);
    update_work_cursor(topic_context);
    schedule_live_refresh(active_run);
}

export function refresh(options: {force_context?: boolean; live?: boolean} = {}): void {
    const live = options.live === true;
    if (live && live_refresh_in_flight) {
        return;
    }
    const {stream, topic} = current_context();
    const include_context = should_refresh_context(stream, topic, {
        force_context: options.force_context === true,
        live,
    });
    const data: Record<string, string | boolean> = {
        include_events: false,
        include_context,
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
            if (!is_pear_runs_response(raw_data)) {
                last_error = $t({defaultMessage: "Pear returned an invalid runs payload."});
                render();
                return;
            }
            const data = raw_data;
            last_error = "";
            const had_active_run = has_active_run(last_runs);
            const has_active_run_after_refresh = has_active_run(data.pear_runs);
            const topic_context = topic_context_after_refresh_for_testing({
                include_context,
                live,
                response_topic_context: data.pear_topic_context,
                last_topic_context,
            });
            if (data.pear_topic_context !== undefined && data.pear_topic_context !== null) {
                record_context_refresh(stream, topic);
            }
            render(data.pear_runs, topic_context);
            if (had_active_run && !has_active_run_after_refresh && !include_context) {
                refresh({force_context: true});
            }
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
            refresh({force_context: true});
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
            side_request_by_context.delete(context_key(stream, topic));
            refresh({force_context: true});
            if (!is_pear_side_response(raw_data)) {
                blueslip.warn("Pear side session returned an invalid response.");
                return;
            }
            const data = raw_data;
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

export function initialize(): void {
    if (initialized) {
        refresh();
        return;
    }
    initialized = true;
    $("body").on("click", "#pear-panel-refresh", () => {
        refresh({force_context: true});
    });
    $("body").on("click", "#pear-panel-close", () => {
        close_work_details();
    });
    $("body").on("click", ".pear-workbench-tab", function () {
        const view = $(this).attr("data-pear-workbench-view");
        if (view !== "diff" && view !== "history" && view !== "side") {
            return;
        }
        const key = current_context_key();
        if (key !== "") {
            workbench_view_by_context.set(key, view);
            work_panel_open_by_context.set(key, true);
        }
        render();
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
    $("body").on("click", "#pear-work-cursor", (event) => {
        const view = $(event.target)
            .closest("[data-pear-workbench-view]")
            .attr("data-pear-workbench-view");
        open_work_details(view === "diff" || view === "history" ? view : undefined);
    });
    $("body").on("keydown", "#pear-work-cursor", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
            return;
        }
        event.preventDefault();
        open_work_details();
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
