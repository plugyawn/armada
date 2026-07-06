import $ from "jquery";

import render_pear_panel from "../templates/pear_panel.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import {message_render_response_schema} from "./message_store.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_canvas from "./pear_canvas.ts";
import * as pear_run_state from "./pear_run_state.ts";
import {postprocess_content} from "./postprocess_content.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as stream_data from "./stream_data.ts";

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

type PearTopicContextResponse = {
    pear_topic_context?: PearTopicContext | null;
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

type PearRuntimeChoice = {
    value: string;
    label: string;
    selected: boolean;
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
    head_short?: string;
    upstream?: string;
    ahead?: number;
    behind?: number;
    dirty?: boolean;
    changed_count?: number;
    detached?: boolean;
    branch_matches_topic?: boolean;
    changed_files?: PearGitChangedFile[];
    changed_files_truncated?: boolean;
    diff_shortstat?: string;
    recent_commits?: PearGitCommit[];
};

type PearGitTree = {
    available: boolean;
    workdir: string;
    graph: string;
    status_short: string;
    delta?: PearGitContext;
};

type PearGitTreeResponse = {
    git_tree: PearGitTree;
};

type PearCanvas = {
    content: string;
    version: string;
    relative_path: string;
    exists: boolean;
    max_bytes: number;
    rendered_html?: string;
    render_failed?: boolean;
};

type PearCanvasResponse = {
    pear_canvas: PearCanvas;
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

type PearWorkbenchView = "diff" | "history" | "canvas" | "side";
type PearDisplayTopicContext = NonNullable<ReturnType<typeof display_topic_context>>;

let initialized = false;
let refresh_timer: number | undefined;
let live_refresh_timer: number | undefined;
let live_refresh_in_flight = false;
let live_refresh_backoff_until = 0;
let last_error = "";
let last_runs: PearRun[] = [];
let last_topic_context: PearTopicContext | null | undefined;
let last_render_state_key = "";
let last_context_refresh_key = "";
let last_context_refresh_at = 0;
const work_panel_open_by_context = new Map<string, boolean>();
const workbench_view_by_context = new Map<string, PearWorkbenchView>();
const side_request_by_context = new Map<string, string>();
const git_tree_by_context = new Map<string, PearGitTree>();
const git_tree_error_by_context = new Map<string, string>();
const git_tree_loading_contexts = new Set<string>();
const canvas_by_context = new Map<string, PearCanvas>();
const canvas_error_by_context = new Map<string, string>();
const canvas_loading_contexts = new Set<string>();
const canvas_fetch_sequence_by_context = new Map<string, number>();
const canvas_rendering_contexts = new Set<string>();
const canvas_render_sequence_by_context = new Map<string, number>();
let workbench_anchor_element: HTMLElement | undefined;
let workbench_anchor_context_key = "";
let topic_context_update_in_flight = false;
const LIVE_REFRESH_INTERVAL_MS = 500;
const LIVE_REFRESH_RATE_LIMIT_BACKOFF_MS = 1500;
const FULL_REFRESH_INTERVAL_MS = 5000;
const CONTEXT_REFRESH_INTERVAL_MS = 60_000;
const WORKBENCH_MIN_VIEWPORT_WIDTH_PX = 320;
const WORKBENCH_POPOVER_MARGIN_PX = 8;

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
            return $t({defaultMessage: "cancelled"});
        case "done":
            return $t({defaultMessage: "done"});
        case "failed":
            return $t({defaultMessage: "failed"});
        case "paused":
            return $t({defaultMessage: "paused"});
        case "queued":
            return $t({defaultMessage: "queued"});
        case "running":
            return $t({defaultMessage: "working"});
        default:
            return compact_panel_text(status);
    }
}

function status_marker(status: string): string {
    switch (status) {
        case "cancelled":
        case "failed":
            return "✷";
        case "done":
            return "·";
        case "paused":
            return "✧";
        case "queued":
        case "running":
            return "·";
        default:
            return "·";
    }
}

export const status_marker_for_testing = status_marker;
export const status_label_for_testing = status_label;

function compact_reasoning_effort(effort: string | undefined): string {
    if (effort === undefined || effort === "") {
        return "";
    }
    if (effort === "xhigh") {
        return "xh";
    }
    return effort.at(0)?.toLowerCase() ?? "";
}

const fallback_reasoning_efforts: PearReasoningEffort[] = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

function model_reasoning_efforts(
    topic_context: PearTopicContext | undefined | null,
    model: string | undefined,
): PearReasoningEffort[] {
    const catalog_entry = topic_context?.model_catalog?.find((entry) => entry.slug === model);
    const model_efforts = catalog_entry?.supported_reasoning_levels;
    if (model_efforts !== undefined && model_efforts.length > 0) {
        return model_efforts;
    }
    if (
        topic_context?.reasoning_efforts !== undefined &&
        topic_context.reasoning_efforts.length > 0
    ) {
        return topic_context.reasoning_efforts;
    }
    return fallback_reasoning_efforts;
}

function model_choices(topic_context: PearTopicContext): PearRuntimeChoice[] {
    const catalog = topic_context.model_catalog ?? [];
    const choices = catalog.map((entry) => ({
        value: entry.slug,
        label: entry.display_name || entry.slug,
        selected: entry.slug === topic_context.model,
    }));
    const seen_models = new Set(choices.map((choice) => choice.value));
    for (const model of topic_context.model_choices ?? []) {
        if (!seen_models.has(model)) {
            choices.push({
                value: model,
                label: model,
                selected: model === topic_context.model,
            });
            seen_models.add(model);
        }
    }
    if (topic_context.model && !seen_models.has(topic_context.model)) {
        choices.unshift({
            value: topic_context.model,
            label: topic_context.model,
            selected: true,
        });
    }
    return choices;
}

function effort_choices(
    topic_context: PearTopicContext,
    model = topic_context.model,
): PearRuntimeChoice[] {
    return model_reasoning_efforts(topic_context, model).map((choice) => ({
        value: choice.effort,
        label: choice.label,
        selected: choice.effort === topic_context.reasoning_effort,
    }));
}

function effort_slider_value(choices: PearRuntimeChoice[]): number {
    return Math.max(
        0,
        choices.findIndex((choice) => choice.selected),
    );
}

export function workbench_effort_for_index_for_testing(
    topic_context: PearTopicContext | undefined | null,
    model: string | undefined,
    index: number,
): string | undefined {
    return model_reasoning_efforts(topic_context, model)[index]?.effort;
}

function next_effort_for_model(topic_context: PearTopicContext, model: string): string {
    const efforts = model_reasoning_efforts(topic_context, model);
    if (efforts.some((choice) => choice.effort === topic_context.reasoning_effort)) {
        return topic_context.reasoning_effort;
    }
    const catalog_entry = topic_context.model_catalog?.find((entry) => entry.slug === model);
    return (
        catalog_entry?.default_reasoning_level ??
        efforts[0]?.effort ??
        topic_context.reasoning_effort
    );
}

export function panel_runtime_label_for_testing(options: {
    model?: string;
    reasoning_effort?: string;
}): string {
    const model = options.model?.trim() ?? "";
    if (model === "") {
        return "";
    }
    const effort = compact_reasoning_effort(options.reasoning_effort);
    return effort === "" ? model : `${model} / ${effort}`;
}

function reasoning_effort_meter_class(effort: string | undefined): string {
    if (effort === "low" || effort === "medium" || effort === "high" || effort === "xhigh") {
        return effort;
    }
    return "medium";
}

function is_pear_runs_response(data: unknown): data is PearRunsResponse {
    return (
        typeof data === "object" &&
        data !== null &&
        "pear_runs" in data &&
        Array.isArray(data.pear_runs)
    );
}

function is_pear_topic_context_response(data: unknown): data is PearTopicContextResponse {
    return typeof data === "object" && data !== null && "pear_topic_context" in data;
}

function is_pear_side_response(data: unknown): data is {dm_narrow_url?: string} {
    return (
        typeof data === "object" &&
        data !== null &&
        (!("dm_narrow_url" in data) || typeof data.dm_narrow_url === "string")
    );
}

function is_pear_git_tree_response(data: unknown): data is PearGitTreeResponse {
    if (typeof data !== "object" || data === null || !("git_tree" in data)) {
        return false;
    }
    const git_tree = data.git_tree;
    return (
        typeof git_tree === "object" &&
        git_tree !== null &&
        "available" in git_tree &&
        typeof git_tree.available === "boolean" &&
        "workdir" in git_tree &&
        typeof git_tree.workdir === "string" &&
        "graph" in git_tree &&
        typeof git_tree.graph === "string" &&
        "status_short" in git_tree &&
        typeof git_tree.status_short === "string"
    );
}

function is_pear_canvas_response(data: unknown): data is PearCanvasResponse {
    if (typeof data !== "object" || data === null || !("pear_canvas" in data)) {
        return false;
    }
    const canvas = data.pear_canvas;
    return (
        typeof canvas === "object" &&
        canvas !== null &&
        "content" in canvas &&
        typeof canvas.content === "string" &&
        "version" in canvas &&
        typeof canvas.version === "string" &&
        "relative_path" in canvas &&
        typeof canvas.relative_path === "string" &&
        "exists" in canvas &&
        typeof canvas.exists === "boolean" &&
        "max_bytes" in canvas &&
        typeof canvas.max_bytes === "number"
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

function run_shows_compact_panel_status(run: {status: string} | undefined): boolean {
    return run?.status === "running" || run?.status === "queued" || run?.status === "paused";
}

export function workbench_viewport_allows_popover_for_testing(width: number): boolean {
    return width >= WORKBENCH_MIN_VIEWPORT_WIDTH_PX;
}

export const workbench_viewport_allows_split_for_testing =
    workbench_viewport_allows_popover_for_testing;

function current_viewport_allows_workbench(): boolean {
    return workbench_viewport_allows_popover_for_testing(window.innerWidth);
}

export function workbench_open_for_testing(options: {
    context_key_value: string;
    manually_open: boolean;
    has_error: boolean;
}): boolean {
    if (options.context_key_value === "") {
        return false;
    }
    return options.manually_open || options.has_error;
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
    return options.manually_open || options.has_error;
}

function panel_has_active_status_for_testing(status: string | undefined): boolean {
    return run_shows_compact_panel_status(
        status === undefined
            ? undefined
            : {
                  status,
              },
    );
}

function git_status_label(git: PearGitContext, changed_label: string): string {
    if (!git.available) {
        return "";
    }
    if (git.dirty) {
        return changed_label;
    }
    if ((git.ahead ?? 0) > 0 && (git.behind ?? 0) > 0) {
        return $t(
            {defaultMessage: "+{ahead} -{behind}"},
            {ahead: git.ahead ?? 0, behind: git.behind ?? 0},
        );
    }
    if ((git.ahead ?? 0) > 0) {
        return $t({defaultMessage: "+{ahead}"}, {ahead: git.ahead ?? 0});
    }
    if ((git.behind ?? 0) > 0) {
        return $t({defaultMessage: "-{behind}"}, {behind: git.behind ?? 0});
    }
    return $t({defaultMessage: "clean"});
}

function git_status_class(git: PearGitContext): string {
    if (!git.available) {
        return "unavailable";
    }
    if (git.dirty || (git.ahead ?? 0) > 0 || (git.behind ?? 0) > 0 || git.detached === true) {
        return "dirty";
    }
    return "clean";
}

function git_ref_label(git: PearGitContext): string {
    const branch = git.detached === true ? $t({defaultMessage: "detached"}) : (git.branch ?? "");
    return [branch.trim(), (git.head_short ?? "").trim()].filter((part) => part !== "").join(" / ");
}

function git_has_diff(git: PearGitContext): boolean {
    return (git.changed_files ?? []).length > 0 || (git.diff_shortstat?.trim() ?? "") !== "";
}

function git_has_history(git: PearGitContext): boolean {
    return git.available || (git.recent_commits ?? []).length > 0;
}

function pear_git_shortstat_count(shortstat: string, label: "insertion" | "deletion"): number {
    const match = new RegExp(`(\\d+) ${label}s?\\(`).exec(shortstat);
    return match === null ? 0 : Number.parseInt(match[1]!, 10);
}

function pear_git_shortstat_file_count(shortstat: string): number {
    const match = /(\d+) files? changed/.exec(shortstat);
    return match === null ? 0 : Number.parseInt(match[1]!, 10);
}

export function workbench_view_for_testing(options: {
    requested: string | undefined;
    has_diff: boolean;
    has_history: boolean;
}): PearWorkbenchView {
    if (options.requested === "side") {
        return "side";
    }
    if (options.requested === "canvas") {
        return "canvas";
    }
    if (options.requested === "history") {
        return "history";
    }
    if (options.requested === "diff") {
        return "diff";
    }
    if (options.has_diff) {
        return "diff";
    }
    if (options.has_history) {
        return "history";
    }
    return "history";
}

export function workbench_view_for_artifact_label(
    label: string,
): Exclude<PearWorkbenchView, "side"> | undefined {
    const normalized_label = label.trim().toLowerCase();
    if (normalized_label === "diff") {
        return "diff";
    }
    if (["canvas", "doc", "document"].includes(normalized_label)) {
        return "canvas";
    }
    if (
        [
            "repo",
            "repository",
            "runtime",
            "branch",
            "commit",
            "sha",
            "pr",
            "pull request",
            "session",
            "session record",
            "pear session",
            "codex session",
        ].includes(normalized_label)
    ) {
        return "history";
    }
    return undefined;
}

export function open_work_details(
    requested_view?: PearWorkbenchView,
    anchor_element?: HTMLElement,
): void {
    const key = current_context_key();
    const viewport_allows_workbench = current_viewport_allows_workbench();
    if (key !== "" && viewport_allows_workbench) {
        if (anchor_element !== undefined) {
            workbench_anchor_element = anchor_element;
            workbench_anchor_context_key = key;
        }
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
    } else if (key !== "") {
        work_panel_open_by_context.set(key, false);
    }
    render();
    if (!viewport_allows_workbench) {
        return;
    }
    const workbench = document.querySelector<HTMLElement>("#pear-panel .pear-workbench");
    if (workbench !== null) {
        position_workbench_popover();
        return;
    }
    refresh({force_context: true});
}

export function open_work_details_for_artifact_label(label: string): void {
    open_work_details(workbench_view_for_artifact_label(label));
}

function close_work_details(): void {
    const key = current_context_key();
    if (key !== "") {
        work_panel_open_by_context.set(key, false);
    }
    workbench_anchor_element = undefined;
    workbench_anchor_context_key = "";
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
    show_status: boolean;
    details_open: boolean;
    is_active: boolean;
    can_pause: boolean;
    can_resume: boolean;
    can_cancel: boolean;
    has_actions: boolean;
    has_events: boolean;
    events: PearRunEvent[];
} {
    const events = (run.events ?? []).map((event) => display_event(event));
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
        show_status: run.status !== "done",
        details_open: run.status === "running" || run.status === "queued",
        is_active: run.status === "running" || run.status === "queued",
        can_pause,
        can_resume,
        can_cancel,
        has_actions: can_pause || can_resume || can_cancel,
        has_events: events.length > 0,
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
          workbench_canvas_selected: boolean;
          workbench_side_selected: boolean;
          workbench_diff_disabled: boolean;
          workbench_history_disabled: boolean;
          diff_tab_label: string;
          history_tab_label: string;
          canvas_tab_label: string;
          side_tab_label: string;
          git_tree: PearGitTree | undefined;
          git_tree_loading: boolean;
          git_tree_error: string;
          has_git_tree_graph: boolean;
          has_git_tree_status: boolean;
          canvas: PearCanvas | undefined;
          canvas_loading: boolean;
          canvas_rendering: boolean;
          canvas_error: string;
          canvas_title: string;
          canvas_has_content: boolean;
          canvas_has_rendered_html: boolean;
          canvas_rendered_html: string;
          canvas_render_failed: boolean;
          history_commits: (PearGitCommit & {is_head: boolean; ref_label: string})[];
          workbench_title: string;
          workbench_meta: string;
          panel_repo_label: string;
          panel_worktree_label: string;
          panel_git_status_label: string;
          panel_git_status_class: string;
          panel_git_ref_label: string;
          panel_runtime_label: string;
          panel_reasoning_effort_label: string;
          panel_reasoning_effort_meter_class: string;
          workbench_model_choices: PearRuntimeChoice[];
          workbench_has_model_choices: boolean;
          workbench_effort_choices: PearRuntimeChoice[];
          workbench_effort_slider_value: number;
          workbench_effort_slider_max: number;
          workbench_has_efforts: boolean;
          has_workbench_diff_summary: boolean;
          workbench_diff_summary_label: string;
          workbench_diff_file_label: string;
          workbench_diff_insertions_label: string;
          workbench_diff_deletions_label: string;
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
    const has_workbench_diff = git_has_diff(git);
    const has_workbench_history = git_has_history(git);
    const diff_shortstat = git.diff_shortstat?.trim() ?? "";
    const workbench_title = topic_context.worktree_label?.trim() || topic_context.topic;
    const workbench_repo_label =
        topic_context.repo_label?.trim() || topic_context.repo_title?.trim() || github_repo;
    const panel_repo_label =
        workbench_repo_label ||
        git.repo_label?.trim() ||
        git.repo_name?.trim() ||
        $t({defaultMessage: "No repo pinned"});
    const panel_worktree_label =
        topic_context.worktree_label?.trim() || topic_context.topic || git.worktree_name?.trim() || "";
    const workbench_meta = [workbench_repo_label, git.branch?.trim() ?? ""]
        .filter((part) => part !== "")
        .join(" / ");
    const diff_file_count = pear_git_shortstat_file_count(diff_shortstat) || changed_count;
    const diff_insertions = pear_git_shortstat_count(diff_shortstat, "insertion");
    const diff_deletions = pear_git_shortstat_count(diff_shortstat, "deletion");
    const workbench_diff_file_label =
        diff_file_count === 0
            ? ""
            : diff_file_count === 1
              ? $t({defaultMessage: "1 file"})
              : $t({defaultMessage: "{diff_file_count} files"}, {diff_file_count});
    const workbench_diff_insertions_label =
        diff_insertions > 0 ? $t({defaultMessage: "+{diff_insertions}"}, {diff_insertions}) : "";
    const workbench_diff_deletions_label =
        diff_deletions > 0 ? $t({defaultMessage: "-{diff_deletions}"}, {diff_deletions}) : "";
    const workbench_diff_summary_label = [
        workbench_diff_file_label,
        workbench_diff_insertions_label,
        workbench_diff_deletions_label,
    ]
        .filter((part) => part !== "")
        .join(" ");
    const history_commits = (git.recent_commits ?? []).map((commit, index) => ({
        ...commit,
        is_head: index === 0,
        ref_label: index === 0 ? $t({defaultMessage: "HEAD"}) : "",
    }));
    const recent_commit_count = history_commits.length;
    const topic_key = context_key(topic_context.stream, topic_context.topic);
    const git_tree = git_tree_by_context.get(topic_key);
    const git_tree_graph = git_tree?.graph.trim() ?? "";
    const git_tree_status = git_tree?.status_short.trim() ?? "";
    const canvas = canvas_by_context.get(topic_key);
    const canvas_content = canvas?.content ?? "";
    const canvas_rendered_html = canvas?.rendered_html ?? "";
    const reasoning_effort_label =
        topic_context.reasoning_efforts.find(
            (effort) => effort.effort === topic_context.reasoning_effort,
        )?.label ||
        topic_context.reasoning_effort ||
        $t({defaultMessage: "Medium"});
    const workbench_model_choices = model_choices(topic_context);
    const workbench_effort_choices = effort_choices(topic_context);
    const workbench_effort_slider_value = effort_slider_value(workbench_effort_choices);
    const workbench_view = workbench_view_for_testing({
        requested: workbench_view_by_context.get(topic_key),
        has_diff: has_workbench_diff,
        has_history: has_workbench_history,
    });
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
        workbench_canvas_selected: workbench_view === "canvas",
        workbench_side_selected: workbench_view === "side",
        workbench_diff_disabled: false,
        workbench_history_disabled: false,
        diff_tab_label:
            changed_count > 0
                ? $t({defaultMessage: "diff {changed_count}"}, {changed_count})
                : $t({defaultMessage: "diff"}),
        history_tab_label:
            recent_commit_count > 0
                ? $t({defaultMessage: "history {recent_commit_count}"}, {recent_commit_count})
                : $t({defaultMessage: "history"}),
        canvas_tab_label: $t({defaultMessage: "canvas"}),
        side_tab_label: $t({defaultMessage: "whisper"}),
        git_tree,
        git_tree_loading: git_tree_loading_contexts.has(topic_key),
        git_tree_error: git_tree_error_by_context.get(topic_key) ?? "",
        has_git_tree_graph: git_tree_graph !== "",
        has_git_tree_status: git_tree_status !== "",
        canvas,
        canvas_loading: canvas_loading_contexts.has(topic_key),
        canvas_rendering: canvas_rendering_contexts.has(topic_key),
        canvas_error: canvas_error_by_context.get(topic_key) ?? "",
        canvas_title: canvas?.relative_path ?? "CANVAS.md",
        canvas_has_content: canvas_content.trim() !== "",
        canvas_has_rendered_html: canvas_rendered_html !== "",
        canvas_rendered_html,
        canvas_render_failed: canvas?.render_failed === true,
        history_commits,
        workbench_title,
        workbench_meta,
        panel_repo_label,
        panel_worktree_label,
        panel_git_status_label: git_status_label(git, changed_label),
        panel_git_status_class: git_status_class(git),
        panel_git_ref_label: git_ref_label(git),
        panel_runtime_label: panel_runtime_label_for_testing({
            model: topic_context.model,
            reasoning_effort: topic_context.reasoning_effort,
        }),
        panel_reasoning_effort_label: reasoning_effort_label,
        panel_reasoning_effort_meter_class: reasoning_effort_meter_class(
            topic_context.reasoning_effort,
        ),
        workbench_model_choices,
        workbench_has_model_choices: workbench_model_choices.length > 0,
        workbench_effort_choices,
        workbench_effort_slider_value,
        workbench_effort_slider_max: Math.max(0, workbench_effort_choices.length - 1),
        workbench_has_efforts: workbench_effort_choices.length > 0,
        has_workbench_diff_summary: workbench_diff_summary_label !== "",
        workbench_diff_summary_label,
        workbench_diff_file_label,
        workbench_diff_insertions_label,
        workbench_diff_deletions_label,
    };
}

const pear_topic_header_status_selector = ".pear-topic-header-status";
const pear_topic_header_control_selector =
    ".pear-topic-header-status, .pear-topic-header-control";

function ensure_pear_panel_host(): HTMLElement | undefined {
    let panel = document.querySelector<HTMLElement>("body > #pear-panel");
    if (panel === null) {
        panel = document.querySelector<HTMLElement>("#pear-panel");
        if (panel === null) {
            return undefined;
        }
        document.body.append(panel);
    }
    $("#pear-panel").not(panel).remove();
    return panel;
}

function topic_header_controls_are_current(
    $headers: JQuery,
    control_key: string,
): boolean {
    if ($headers.length === 0) {
        return false;
    }
    return $headers.toArray().every((header) => {
        const $header = $(header);
        return (
            $header
                .find(pear_topic_header_status_selector)
                .filter((_index, element) => $(element).attr("data-pear-header-key") === control_key)
                .length > 0
        );
    });
}

function topic_header_work_label(display_context: PearDisplayTopicContext): string {
    if (display_context.panel_git_status_class !== "clean") {
        const changed_count = display_context.git.changed_count ?? 0;
        if (changed_count === 1) {
            return $t({defaultMessage: "1 file changed"});
        }
        if (changed_count > 1) {
            return $t(
                {defaultMessage: "{changed_count} files changed"},
                {changed_count},
            );
        }
        return display_context.panel_git_status_label;
    }
    const branch_label = display_context.panel_git_ref_label.split(" / ")[0]?.trim();
    return (
        branch_label ||
        display_context.panel_worktree_label ||
        display_context.panel_repo_label ||
        $t({defaultMessage: "work"})
    );
}

function topic_header_work_title(display_context: PearDisplayTopicContext): string {
    return [
        display_context.panel_repo_label,
        display_context.panel_worktree_label,
        display_context.panel_git_ref_label,
        display_context.workdir,
    ]
        .filter((part) => part.trim() !== "")
        .join(" · ");
}

function render_topic_header_controls(options: {
    stream: string | undefined;
    topic: string | undefined;
    topic_context: PearDisplayTopicContext | undefined;
    active_run_status: string | undefined;
}): void {
    const {stream, topic, topic_context} = options;
    if (stream === undefined || topic === undefined || topic_context === undefined) {
        return;
    }
    const stream_id = stream_data.get_stream_id(stream);
    if (stream_id === undefined) {
        return;
    }
    const runtime_label = topic_context.panel_runtime_label || $t({defaultMessage: "Pear"});
    const runtime_status = options.active_run_status ?? "done";
    const runtime_active = runtime_status === "running" || runtime_status === "queued";
    const runtime_title = $t(
        {defaultMessage: "Pear runtime: {runtime_label}"},
        {runtime_label},
    );
    const work_label = topic_header_work_label(topic_context);
    const work_title = topic_header_work_title(topic_context);
    const control_key = JSON.stringify({
        stream_id,
        topic,
        runtime_label,
        runtime_active,
        reasoning_effort: topic_context.reasoning_effort,
        work_label,
        work_title,
        git_status_class: topic_context.panel_git_status_class,
    });
    const $headers = $(".message_header_stream").filter((_index, header) => {
        const $header = $(header);
        return (
            Number.parseInt($header.attr("data-stream-id") ?? "", 10) === stream_id &&
            $header.attr("data-topic-name") === topic
        );
    });
    if ($headers.length === 0) {
        return;
    }
    if (topic_header_controls_are_current($headers, control_key)) {
        return;
    }

    $(pear_topic_header_control_selector)
        .filter((_index, element) => !$headers.toArray().some((header) => header.contains(element)))
        .remove();

    $headers.each((_index, header) => {
        const $controls = $(header).find(".recipient_bar_controls").first();
        if ($controls.length === 0) {
            return;
        }
        $(header).find(pear_topic_header_control_selector).remove();
        const $status_cluster = $("<span>", {
            class: `pear-topic-header-status no-select${runtime_active ? " pear-liveness-active" : ""}`,
            "data-pear-header-key": control_key,
        });
        const $runtime_control = $("<button>", {
            type: "button",
            class: `pear-topic-header-control pear-topic-header-runtime-control pear-runtime-menu-trigger tippy-zulip-delayed-tooltip${
                runtime_active ? " pear-liveness-active" : ""
            }`,
            "aria-label": runtime_title,
            "data-tippy-content": runtime_title,
            "data-pear-header-key": control_key,
        });
        $runtime_control.append(
            $("<span>", {
                class: runtime_active
                    ? "pear-ui-glyph pear-topic-header-active-glyph pear-thinking-glyph"
                    : "pear-topic-header-idle-dot",
                "aria-hidden": "true",
                text: runtime_active ? "·" : "•",
            }),
            $("<span>", {
                class: "pear-runtime-menu-label",
                text: runtime_label,
            }),
        );
        const $work_control = $("<button>", {
            type: "button",
            class: `pear-topic-header-control pear-topic-header-work-control pear-panel-open-action pear-topic-header-work-${topic_context.panel_git_status_class}`,
            "aria-label": $t(
                {defaultMessage: "Open Pear work details: {work_label}"},
                {work_label},
            ),
            title: work_title || work_label,
            "data-pear-header-key": control_key,
        }).append(
            $("<span>", {
                class: "pear-topic-header-work-label",
                text: work_label,
            }),
        );
        $status_cluster.append($runtime_control, $work_control);
        $controls.before($status_cluster);
    });
    $(document).trigger("pear_liveness_markers_changed");
}

function visible_header_anchor(): HTMLElement | undefined {
    if (
        workbench_anchor_context_key === current_context_key() &&
        workbench_anchor_element?.isConnected === true &&
        workbench_anchor_element.getClientRects().length > 0
    ) {
        return workbench_anchor_element;
    }
    return [
        ...document.querySelectorAll<HTMLElement>(
            ".message_header_stream .pear-topic-header-work-control, .message_header_stream .pear-topic-header-runtime-control",
        ),
    ].find((element) => element.getClientRects().length > 0);
}

function position_workbench_popover(): void {
    const panel = ensure_pear_panel_host();
    if (panel === undefined || !panel.classList.contains("pear-workbench-popover-open")) {
        return;
    }
    const anchor = visible_header_anchor();
    const margin = WORKBENCH_POPOVER_MARGIN_PX;
    const viewport_width = window.innerWidth;
    const viewport_height = window.innerHeight;
    const compact_viewport = viewport_width < 760;
    const width = compact_viewport
        ? Math.max(280, viewport_width - margin * 2)
        : Math.min(500, Math.max(420, viewport_width - margin * 2));
    const anchor_rect = anchor?.getBoundingClientRect();
    const anchor_center =
        anchor_rect === undefined ? viewport_width / 2 : anchor_rect.left + anchor_rect.width / 2;
    const header_rect = anchor
        ?.closest<HTMLElement>(".message_header_stream")
        ?.getBoundingClientRect();
    const min_left = compact_viewport ? margin : Math.max(margin, header_rect?.left ?? margin);
    const left = compact_viewport
        ? margin
        : Math.min(
              Math.max(anchor_center - width / 2, min_left),
              viewport_width - width - margin,
          );
    const top = Math.min(
        anchor_rect === undefined ? 76 : anchor_rect.bottom + 8,
        viewport_height - margin,
    );
    const arrow_left = Math.min(Math.max(anchor_center - left, 18), width - 18);
    panel.style.setProperty("--pear-workbench-popover-left", `${Math.round(left)}px`);
    panel.style.setProperty("--pear-workbench-popover-top", `${Math.round(top)}px`);
    panel.style.setProperty("--pear-workbench-popover-width", `${Math.round(width)}px`);
    panel.style.setProperty(
        "--pear-workbench-popover-max-height",
        `${Math.max(260, Math.round(viewport_height - top - margin))}px`,
    );
    panel.style.setProperty(
        "--pear-workbench-popover-arrow-left",
        `${Math.round(arrow_left)}px`,
    );
}

function set_workbench_popover_open(workbench_open: boolean): void {
    const panel = ensure_pear_panel_host();
    if (panel === undefined) {
        return;
    }
    const $panel = $(panel);
    $panel.toggleClass("pear-workbench-popover-open", workbench_open);
    if (workbench_open) {
        position_workbench_popover();
        return;
    }
    panel.style.removeProperty("--pear-workbench-popover-left");
    panel.style.removeProperty("--pear-workbench-popover-top");
    panel.style.removeProperty("--pear-workbench-popover-width");
    panel.style.removeProperty("--pear-workbench-popover-max-height");
    panel.style.removeProperty("--pear-workbench-popover-arrow-left");
}

function fetch_git_tree_for_context(topic_context: PearTopicContext): void {
    const key = context_key(topic_context.stream, topic_context.topic);
    if (
        key === "" ||
        git_tree_by_context.has(key) ||
        git_tree_loading_contexts.has(key) ||
        !topic_context.git.available
    ) {
        return;
    }
    git_tree_loading_contexts.add(key);
    git_tree_error_by_context.delete(key);
    render();
    void channel.get({
        url: "/json/pear/git-tree",
        data: {
            stream: topic_context.stream,
            topic: topic_context.topic,
        },
        success(raw_data) {
            if (!is_pear_git_tree_response(raw_data)) {
                git_tree_error_by_context.set(
                    key,
                    $t({defaultMessage: "Pear returned an invalid Git history payload."}),
                );
                return;
            }
            git_tree_by_context.set(key, raw_data.git_tree);
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                git_tree_error_by_context.set(
                    key,
                    $t({defaultMessage: "Pear Git history is unavailable."}),
                );
                blueslip.warn(`Failed to fetch Pear Git history: ${xhr.responseText}`);
            }
        },
        complete() {
            git_tree_loading_contexts.delete(key);
            render();
        },
    });
}

function next_canvas_render_sequence(key: string): number {
    const sequence = (canvas_render_sequence_by_context.get(key) ?? 0) + 1;
    canvas_render_sequence_by_context.set(key, sequence);
    return sequence;
}

function next_canvas_fetch_sequence(key: string): number {
    const sequence = (canvas_fetch_sequence_by_context.get(key) ?? 0) + 1;
    canvas_fetch_sequence_by_context.set(key, sequence);
    return sequence;
}

function render_canvas_for_context(key: string, canvas: PearCanvas): void {
    if (
        canvas.content.trim() === "" ||
        canvas.rendered_html !== undefined ||
        canvas.render_failed === true ||
        canvas_rendering_contexts.has(key)
    ) {
        return;
    }
    const sequence = next_canvas_render_sequence(key);
    canvas_rendering_contexts.add(key);
    render();
    void channel.post({
        url: "/json/messages/render",
        data: {content: canvas.content},
        success(raw_data) {
            if (canvas_render_sequence_by_context.get(key) !== sequence) {
                return;
            }
            const data = message_render_response_schema.parse(raw_data);
            const current_canvas = canvas_by_context.get(key);
            if (current_canvas === undefined || current_canvas.version !== canvas.version) {
                return;
            }
            canvas_by_context.set(key, {
                ...current_canvas,
                rendered_html: postprocess_content(data.rendered),
                render_failed: false,
            });
        },
        error(xhr) {
            if (canvas_render_sequence_by_context.get(key) !== sequence) {
                return;
            }
            if (xhr.readyState !== 0) {
                const current_canvas = canvas_by_context.get(key);
                if (current_canvas !== undefined && current_canvas.version === canvas.version) {
                    canvas_by_context.set(key, {...current_canvas, render_failed: true});
                }
                blueslip.warn(`Failed to render Pear canvas: ${xhr.responseText}`);
            }
        },
        complete() {
            if (canvas_render_sequence_by_context.get(key) !== sequence) {
                return;
            }
            canvas_rendering_contexts.delete(key);
            render();
        },
    });
}

function fetch_canvas_for_context(topic_context: PearTopicContext): void {
    const key = context_key(topic_context.stream, topic_context.topic);
    const cached_canvas = canvas_by_context.get(key);
    if (cached_canvas !== undefined) {
        render_canvas_for_context(key, cached_canvas);
        return;
    }
    if (key === "" || canvas_loading_contexts.has(key)) {
        return;
    }
    canvas_loading_contexts.add(key);
    canvas_error_by_context.delete(key);
    const sequence = next_canvas_fetch_sequence(key);
    render();
    void channel.get({
        url: "/json/pear/canvas",
        data: {
            stream: topic_context.stream,
            topic: topic_context.topic,
        },
        success(raw_data) {
            if (canvas_fetch_sequence_by_context.get(key) !== sequence) {
                return;
            }
            if (!is_pear_canvas_response(raw_data)) {
                canvas_error_by_context.set(
                    key,
                    $t({defaultMessage: "Pear returned an invalid canvas payload."}),
                );
                return;
            }
            canvas_by_context.set(key, raw_data.pear_canvas);
            render_canvas_for_context(key, raw_data.pear_canvas);
        },
        error(xhr) {
            if (canvas_fetch_sequence_by_context.get(key) !== sequence) {
                return;
            }
            if (xhr.readyState !== 0) {
                canvas_error_by_context.set(
                    key,
                    $t({defaultMessage: "Pear canvas is unavailable."}),
                );
                blueslip.warn(`Failed to fetch Pear canvas: ${xhr.responseText}`);
            }
        },
        complete() {
            if (canvas_fetch_sequence_by_context.get(key) !== sequence) {
                return;
            }
            canvas_loading_contexts.delete(key);
            render();
        },
    });
}

function refresh_canvas_for_context(topic_context: PearTopicContext | null | undefined): void {
    if (topic_context === undefined || topic_context === null) {
        return;
    }
    const key = context_key(topic_context.stream, topic_context.topic);
    if (key === "") {
        return;
    }
    canvas_by_context.delete(key);
    canvas_error_by_context.delete(key);
    canvas_loading_contexts.delete(key);
    canvas_rendering_contexts.delete(key);
    next_canvas_render_sequence(key);
    fetch_canvas_for_context(topic_context);
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

export function panel_header_state_for_testing(
    active_run:
        | {
              show_status: boolean;
              has_actions: boolean;
          }
        | undefined,
    options: {
        has_topic?: boolean;
        show_workbench?: boolean;
    } = {},
): {
    show_panel_header: boolean;
    show_runtime_trigger: boolean;
    has_panel_menu: boolean;
} {
    const show_runtime_trigger = options.has_topic === true;
    return {
        show_panel_header: active_run?.show_status === true || active_run?.has_actions === true,
        show_runtime_trigger,
        has_panel_menu: active_run?.has_actions === true,
    };
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
    const delay = Math.max(LIVE_REFRESH_INTERVAL_MS, live_refresh_backoff_until - Date.now());
    live_refresh_timer = window.setTimeout(() => {
        live_refresh_timer = undefined;
        refresh({live: true});
    }, delay);
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

function pear_rate_limit_backoff_ms(xhr: JQuery.jqXHR): number {
    const retry_after = Number(xhr.getResponseHeader("retry-after"));
    if (Number.isFinite(retry_after) && retry_after > 0) {
        return Math.max(LIVE_REFRESH_RATE_LIMIT_BACKOFF_MS, Math.ceil(retry_after * 1000));
    }
    return LIVE_REFRESH_RATE_LIMIT_BACKOFF_MS;
}

function render(
    runs: PearRun[] = last_runs,
    topic_context: PearTopicContext | null | undefined = last_topic_context,
): void {
    last_runs = runs;
    last_topic_context = topic_context;
    const {stream, topic} = current_context();
    const has_stream = stream !== undefined;
    const has_topic = has_stream && topic !== undefined;
    const next_context_key = context_key(stream, topic);
    const active_run = active_display_run(runs);
    const manual_workbench_open = work_panel_open_by_context.get(next_context_key) ?? false;
    const viewport_allows_workbench = current_viewport_allows_workbench();
    const workbench_open = workbench_open_for_testing({
        context_key_value: next_context_key,
        manually_open: viewport_allows_workbench && manual_workbench_open,
        has_error: viewport_allows_workbench && last_error !== "",
    });
    const panel_open = workbench_open;
    pear_run_state.set_active_run_result_messages(runs, {
        force_reconcile: active_run === undefined,
    });
    const panel_status = active_run?.status ?? "idle";
    const display_context =
        has_topic && topic_context?.stream === stream && topic_context.topic === topic
            ? display_topic_context(topic_context)
            : undefined;
    render_topic_header_controls({
        stream,
        topic,
        topic_context: display_context,
        active_run_status: active_run?.status,
    });
    const panel_header_state = panel_header_state_for_testing(active_run, {
        has_topic,
        show_workbench: workbench_open,
    });
    const active_run_for_template =
        active_run === undefined
            ? undefined
            : {
                  id: active_run.id,
                  status: active_run.status,
                  status_class: active_run.status_class,
                  status_label: active_run.status_label,
                  status_marker: active_run.status_marker,
                  show_status: active_run.show_status,
                  is_active: active_run.is_active,
                  can_pause: active_run.can_pause,
                  can_resume: active_run.can_resume,
                  can_cancel: active_run.can_cancel,
                  has_actions: active_run.has_actions,
              };
    const render_state = {
        has_topic,
        context_label: has_topic ? `${stream} > ${topic}` : (stream ?? ""),
        panel_open,
        show_workbench: workbench_open,
        panel_can_close: workbench_open,
        has_panel_menu: panel_header_state.has_panel_menu,
        show_topic_control: has_stream && !workbench_open,
        show_runtime_trigger: panel_header_state.show_runtime_trigger,
        show_panel_header: panel_header_state.show_panel_header,
        panel_header_quiet:
            active_run !== undefined &&
            active_run.show_status !== true &&
            active_run.has_actions === true,
        active_run_show_status: active_run?.show_status === true,
        active_run_is_active: active_run?.is_active === true,
        panel_has_active_status: panel_has_active_status_for_testing(active_run?.status),
        topic_context: display_context,
        active_run: active_run_for_template,
        error: last_error,
        status_label: panel_status,
        status_class: status_class(panel_status),
    };
    const next_render_state_key = render_state_key(render_state);
    const panel = ensure_pear_panel_host();
    if (panel === undefined) {
        return;
    }
    const $panel = $(panel);
    if (last_render_state_key !== next_render_state_key) {
        const side_request = $panel.find("#pear-side-request").val();
        if (typeof side_request === "string" && next_context_key !== "") {
            side_request_by_context.set(next_context_key, side_request);
        }
        $panel.html(render_pear_panel(render_state));
        $panel.toggleClass("pear-panel-collapsed", !panel_open);
        set_workbench_popover_open(workbench_open);
        const stored_side_request = side_request_by_context.get(next_context_key);
        if (stored_side_request !== undefined) {
            $panel.find("#pear-side-request").val(stored_side_request);
        }
        rendered_markdown.update_elements($panel.find(".pear-workbench-canvas-rendered"));
        $(document).trigger("pear_liveness_markers_changed");
        last_render_state_key = next_render_state_key;
    }
    $panel.toggleClass("pear-panel-collapsed", !panel_open);
    set_workbench_popover_open(workbench_open);
    if (workbench_open && display_context?.workbench_history_selected) {
        fetch_git_tree_for_context(display_context);
    }
    if (workbench_open && display_context?.workbench_canvas_selected) {
        fetch_canvas_for_context(display_context);
    }
    schedule_live_refresh(active_run);
}

export function refresh(options: {force_context?: boolean; live?: boolean} = {}): void {
    const live = options.live === true;
    if (!live && has_active_run(last_runs) && live_refresh_backoff_until > Date.now()) {
        return;
    }
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
            if (live) {
                live_refresh_backoff_until = 0;
            }
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
            if ((live || has_active_run(last_runs)) && xhr.status === 429) {
                live_refresh_backoff_until = Date.now() + pear_rate_limit_backoff_ms(xhr);
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

function save_topic_context(options: {model?: string; reasoning_effort?: string}): void {
    const {stream, topic} = current_context();
    if (stream === undefined || topic === undefined || topic_context_update_in_flight) {
        return;
    }
    const model = options.model ?? last_topic_context?.model;
    const reasoning_effort = options.reasoning_effort ?? last_topic_context?.reasoning_effort;
    if (model === undefined || reasoning_effort === undefined) {
        return;
    }
    topic_context_update_in_flight = true;
    const $controls = $(
        "#pear-panel .pear-workbench-model-select, #pear-panel .pear-workbench-effort-range",
    );
    $controls.prop("disabled", true);
    void channel.post({
        url: "/json/pear/topic-context",
        data: {
            stream,
            topic,
            model,
            reasoning_effort,
        },
        success(raw_data) {
            if (!is_pear_topic_context_response(raw_data)) {
                blueslip.warn("Pear topic context returned an invalid response.");
                refresh({force_context: true});
                return;
            }
            const data = raw_data;
            if (data.pear_topic_context !== undefined && data.pear_topic_context !== null) {
                last_topic_context = data.pear_topic_context;
                record_context_refresh(stream, topic);
                render(last_runs, data.pear_topic_context);
                return;
            }
            refresh({force_context: true});
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to update Pear topic context: ${xhr.responseText}`);
            }
        },
        complete() {
            topic_context_update_in_flight = false;
            $controls.prop("disabled", false);
        },
    });
}

function on_workbench_model_change(model: string): void {
    if (last_topic_context === undefined || last_topic_context === null) {
        return;
    }
    save_topic_context({
        model,
        reasoning_effort: next_effort_for_model(last_topic_context, model),
    });
}

function on_workbench_effort_index_change(index: number): void {
    if (last_topic_context === undefined || last_topic_context === null) {
        return;
    }
    const effort = workbench_effort_for_index_for_testing(
        last_topic_context,
        last_topic_context.model,
        index,
    );
    if (effort === undefined) {
        return;
    }
    save_topic_context({reasoning_effort: effort});
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
    $("body").on("click", ".pear-panel-close-action", () => {
        close_work_details();
    });
    $("body").on("click", ".pear-panel-open-action", (e) => {
        e.preventDefault();
        e.stopPropagation();
        open_work_details(undefined, e.currentTarget);
    });
    $("body").on("click", "#pear-panel", (e) => {
        e.stopPropagation();
    });
    $(document).on("click.pear_panel", () => {
        close_work_details();
    });
    $("body").on("click", ".pear-workbench-tab", function () {
        const view = $(this).attr("data-pear-workbench-view");
        if (view !== "diff" && view !== "history" && view !== "canvas" && view !== "side") {
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
    $("body").on("change", ".pear-workbench-model-select", function (this: HTMLElement) {
        const model = $(this).val();
        if (typeof model === "string") {
            on_workbench_model_change(model);
        }
    });
    $("body").on("input", ".pear-workbench-effort-range", function (this: HTMLElement) {
        const index = Number($(this).val());
        if (
            !Number.isInteger(index) ||
            last_topic_context === undefined ||
            last_topic_context === null
        ) {
            return;
        }
        const effort = workbench_effort_for_index_for_testing(
            last_topic_context,
            last_topic_context.model,
            index,
        );
        const label = model_reasoning_efforts(last_topic_context, last_topic_context.model).find(
            (choice) => choice.effort === effort,
        )?.label;
        if (label !== undefined) {
            $(this)
                .closest(".pear-workbench-control-row")
                .find(".pear-workbench-control-value")
                .text(label);
        }
    });
    $("body").on("change", ".pear-workbench-effort-range", function (this: HTMLElement) {
        const index = Number($(this).val());
        if (Number.isInteger(index)) {
            on_workbench_effort_index_change(index);
        }
    });
    $("body").on("click", "#pear-side-start", () => {
        start_side_session();
    });
    $("body").on("click", ".pear-workbench-canvas-open", () => {
        pear_canvas.open_canvas({
            on_saved() {
                refresh_canvas_for_context(last_topic_context);
            },
        });
    });
    $(window).on("resize.pear_panel", () => {
        render();
    });
    $(window).on("scroll.pear_panel", () => {
        position_workbench_popover();
    });
    refresh();
    refresh_timer = window.setInterval(refresh, FULL_REFRESH_INTERVAL_MS);
}

export function stop(): void {
    if (refresh_timer !== undefined) {
        window.clearInterval(refresh_timer);
        refresh_timer = undefined;
    }
    $(window).off("resize.pear_panel");
    $(window).off("scroll.pear_panel");
    $(document).off("click.pear_panel");
    clear_live_refresh();
    live_refresh_in_flight = false;
}
