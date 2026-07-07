import $ from "jquery";
import * as z from "zod/mini";

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

const pear_run_event_schema = z.object({
    id: z.number(),
    event_type: z.string(),
    summary: z.string(),
    payload: z.optional(
        z.object({
            kind: z.optional(z.string()),
            status: z.optional(z.string()),
            title: z.optional(z.string()),
            body: z.optional(z.string()),
        }),
    ),
});

const pear_usage_schema = z.object({
    input_tokens: z.optional(z.number()),
    cached_input_tokens: z.optional(z.number()),
    output_tokens: z.optional(z.number()),
    reasoning_output_tokens: z.optional(z.number()),
    total_tokens: z.optional(z.number()),
    cache_percent: z.optional(z.number()),
    duration_seconds: z.nullish(z.number()),
    summary: z.optional(z.string()),
});

const pear_live_run_schema = z.object({
    status: z.string(),
    content: z.string(),
    updated_at: z.number(),
});

const pear_run_schema = z.object({
    id: z.number(),
    trigger_message_id: z.number(),
    result_message_id: z.nullish(z.number()),
    status: z.string(),
    session_kind: z.string(),
    source_stream: z.string(),
    source_topic: z.string(),
    summary: z.string(),
    current_activity: z.string(),
    runtime_label: z.string(),
    sandbox: z.string(),
    approval_policy: z.string(),
    workdir: z.string(),
    codex_thread_id: z.string(),
    usage: z.nullish(pear_usage_schema),
    live: z.nullish(pear_live_run_schema),
    events: z.optional(z.array(pear_run_event_schema)),
});

const pear_reasoning_effort_schema = z.object({
    effort: z.string(),
    label: z.string(),
    description: z.optional(z.string()),
});

const pear_model_catalog_entry_schema = z.object({
    slug: z.string(),
    display_name: z.string(),
    default_reasoning_level: z.optional(z.string()),
    supported_reasoning_levels: z.optional(z.array(pear_reasoning_effort_schema)),
});

const pear_git_changed_file_schema = z.object({
    status: z.string(),
    kind: z.string(),
    path: z.string(),
});

const pear_git_commit_schema = z.object({
    hash: z.string(),
    summary: z.string(),
});

const pear_git_context_schema = z.object({
    available: z.boolean(),
    workdir: z.optional(z.string()),
    repo_name: z.optional(z.string()),
    repo_label: z.optional(z.string()),
    remote_url: z.optional(z.string()),
    worktree_name: z.optional(z.string()),
    branch: z.optional(z.string()),
    head: z.optional(z.string()),
    head_short: z.optional(z.string()),
    upstream: z.optional(z.string()),
    ahead: z.optional(z.number()),
    behind: z.optional(z.number()),
    dirty: z.optional(z.boolean()),
    changed_count: z.optional(z.number()),
    detached: z.optional(z.boolean()),
    branch_matches_topic: z.optional(z.boolean()),
    changed_files: z.optional(z.array(pear_git_changed_file_schema)),
    changed_files_truncated: z.optional(z.boolean()),
    diff_shortstat: z.optional(z.string()),
    recent_commits: z.optional(z.array(pear_git_commit_schema)),
});

const pear_git_tree_schema = z.object({
    available: z.boolean(),
    workdir: z.string(),
    graph: z.string(),
    status_short: z.string(),
    delta: z.optional(pear_git_context_schema),
});

const pear_git_tree_response_schema = z.object({
    git_tree: pear_git_tree_schema,
});

const pear_canvas_schema = z.object({
    content: z.string(),
    version: z.string(),
    relative_path: z.string(),
    exists: z.boolean(),
    max_bytes: z.number(),
    rendered_html: z.optional(z.string()),
    render_failed: z.optional(z.boolean()),
});

const pear_canvas_response_schema = z.object({
    pear_canvas: pear_canvas_schema,
});

const pear_topic_context_schema = z.object({
    stream: z.string(),
    topic: z.string(),
    model: z.string(),
    default_model: z.string(),
    model_choices: z.array(z.string()),
    model_catalog: z.optional(z.array(pear_model_catalog_entry_schema)),
    reasoning_effort: z.string(),
    default_reasoning_effort: z.string(),
    reasoning_efforts: z.array(pear_reasoning_effort_schema),
    github_repo: z.string(),
    pinned_repo: z.optional(z.string()),
    repo_label: z.optional(z.string()),
    repo_title: z.optional(z.string()),
    worktree_label: z.optional(z.string()),
    workdir: z.string(),
    git: pear_git_context_schema,
});

const pear_runs_response_schema = z.object({
    pear_runs: z.array(pear_run_schema),
    pear_topic_context: z.nullish(pear_topic_context_schema),
    runtime_config: z.optional(
        z.object({
            model: z.nullish(z.string()),
            model_choices: z.optional(z.array(z.string())),
            model_catalog: z.optional(z.array(pear_model_catalog_entry_schema)),
            reasoning_effort: z.nullish(z.string()),
            reasoning_efforts: z.optional(z.array(pear_reasoning_effort_schema)),
        }),
    ),
});

const pear_side_response_schema = z.object({
    dm_narrow_url: z.optional(z.string()),
});

type PearRunEvent = z.infer<typeof pear_run_event_schema>;
type PearRun = z.infer<typeof pear_run_schema>;
type PearRunsResponse = z.infer<typeof pear_runs_response_schema>;
type PearReasoningEffort = z.infer<typeof pear_reasoning_effort_schema>;
type PearGitContext = z.infer<typeof pear_git_context_schema>;
type PearGitTreeResponse = z.infer<typeof pear_git_tree_response_schema>;
type PearCanvas = z.infer<typeof pear_canvas_schema>;
type PearCanvasResponse = z.infer<typeof pear_canvas_response_schema>;
type PearTopicContext = z.infer<typeof pear_topic_context_schema>;
type PearGitTree = z.infer<typeof pear_git_tree_schema>;
type PearGitCommit = z.infer<typeof pear_git_commit_schema>;

type PearWorkbenchView = "diff" | "history" | "canvas" | "side";
type PearDisplayTopicContext = NonNullable<ReturnType<typeof display_topic_context>>;

let initialized = false;
let refresh_timer: number | undefined;
let live_refresh_timer: number | undefined;
let live_refresh_in_flight = false;
let live_refresh_backoff_until = 0;
let last_error = "";
// When an error auto-opens the workbench, closing it sets this so the 5s
// poll's still-present error can't re-force the popover open on the next
// render. Reset whenever a successful fetch clears last_error.
let error_dismissed = false;
let last_runs: PearRun[] = [];
let last_topic_context: PearTopicContext | null | undefined;
let resize_render_frame_id: number | undefined;
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
const canvas_fetched_at_by_context = new Map<string, number>();
const canvas_error_by_context = new Map<string, string>();
const canvas_loading_contexts = new Set<string>();
const canvas_fetch_sequence_by_context = new Map<string, number>();
const canvas_rendering_contexts = new Set<string>();
const canvas_render_sequence_by_context = new Map<string, number>();
const topic_context_by_context = new Map<string, PearTopicContext>();
let workbench_anchor_element: HTMLElement | undefined;
let workbench_anchor_context_key = "";
type WorkbenchAnchorKind = "context" | "git";
let workbench_anchor_kind: WorkbenchAnchorKind | undefined;
let last_observed_context_key = "";
const LIVE_REFRESH_INTERVAL_MS = 500;
const LIVE_REFRESH_RATE_LIMIT_BACKOFF_MS = 1500;
const FULL_REFRESH_INTERVAL_MS = 5000;
const CONTEXT_REFRESH_INTERVAL_MS = 60_000;
// The agent edits CANVAS.md server-side during a run, so a canvas tab left
// open must re-fetch periodically instead of showing a permanently cached copy.
const CANVAS_REFRESH_INTERVAL_MS = 30_000;
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

function full_reasoning_effort_label(
    topic_context: PearTopicContext,
    effort = topic_context.reasoning_effort,
): string {
    return (
        topic_context.reasoning_efforts.find((choice) => choice.effort === effort)?.label ||
        effort ||
        $t({defaultMessage: "Medium"})
    );
}

function display_model_label(topic_context: PearTopicContext): string {
    const catalog_entry = topic_context.model_catalog?.find(
        (entry) => entry.slug === topic_context.model,
    );
    return catalog_entry?.display_name || topic_context.model || topic_context.default_model;
}

function reasoning_effort_meter_class(effort: string | undefined): string {
    if (effort === "low" || effort === "medium" || effort === "high" || effort === "xhigh") {
        return effort;
    }
    return "medium";
}

function is_pear_runs_response(data: unknown): data is PearRunsResponse {
    return pear_runs_response_schema.safeParse(data).success;
}

function is_pear_side_response(data: unknown): data is {dm_narrow_url?: string} {
    return pear_side_response_schema.safeParse(data).success;
}

function is_pear_git_tree_response(data: unknown): data is PearGitTreeResponse {
    return pear_git_tree_response_schema.safeParse(data).success;
}

function is_pear_canvas_response(data: unknown): data is PearCanvasResponse {
    return pear_canvas_response_schema.safeParse(data).success;
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

function topic_context_matches(
    topic_context: PearTopicContext | null | undefined,
    stream: string | undefined,
    topic: string | undefined,
): topic_context is PearTopicContext {
    return (
        stream !== undefined &&
        topic !== undefined &&
        topic_context?.stream === stream &&
        topic_context.topic === topic
    );
}

function cached_topic_context(
    stream: string | undefined,
    topic: string | undefined,
): PearTopicContext | undefined {
    return topic_context_by_context.get(context_key(stream, topic));
}

export function topic_context_for_display_for_testing(options: {
    stream: string | undefined;
    topic: string | undefined;
    render_topic_context: PearTopicContext | null | undefined;
    cached_topic_context: PearTopicContext | undefined;
}): PearTopicContext | undefined {
    if (topic_context_matches(options.render_topic_context, options.stream, options.topic)) {
        return options.render_topic_context;
    }
    return options.cached_topic_context;
}

function display_source_topic_context(
    stream: string | undefined,
    topic: string | undefined,
    topic_context: PearTopicContext | null | undefined = last_topic_context,
): PearTopicContext | undefined {
    return topic_context_for_display_for_testing({
        stream,
        topic,
        render_topic_context: topic_context,
        cached_topic_context: cached_topic_context(stream, topic),
    });
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
    error_dismissed: boolean;
}): boolean {
    if (options.context_key_value === "") {
        return false;
    }
    // An error only auto-opens the workbench until the user closes it; after
    // that a still-present error must not re-force it open on the next poll.
    return options.manually_open || (options.has_error && !options.error_dismissed);
}

export function work_panel_open_for_testing(options: {
    context_key_value: string;
    manually_open: boolean;
    active_run_status: string | undefined;
    has_error: boolean;
    error_dismissed: boolean;
}): boolean {
    if (options.context_key_value === "") {
        return false;
    }
    return options.manually_open || (options.has_error && !options.error_dismissed);
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
            workbench_anchor_kind = anchor_kind_for_element(anchor_element);
        }
        work_panel_open_by_context.set(key, true);
        const {stream, topic} = current_context();
        const topic_context = display_source_topic_context(stream, topic);
        if (topic_context !== undefined) {
            workbench_view_by_context.set(
                key,
                workbench_view_for_testing({
                    requested: requested_view ?? workbench_view_by_context.get(key),
                    has_diff: git_has_diff(topic_context.git),
                    has_history: git_has_history(topic_context.git),
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
    // Mirror the workbench-open predicate: when nothing could be visible,
    // skip the close-and-render so the document-level outside-click handler
    // doesn't re-render the panel on every stray click in the app.
    const manually_open = key !== "" && (work_panel_open_by_context.get(key) ?? false);
    const error_forced_open = last_error !== "" && !error_dismissed;
    if (!manually_open && !error_forced_open && workbench_anchor_element === undefined) {
        return;
    }
    if (key !== "") {
        work_panel_open_by_context.set(key, false);
    }
    // A close gesture must also dismiss an error-forced open, otherwise the
    // next poll re-opens the popover and the user can never close it.
    error_dismissed = true;
    const anchor_element = workbench_anchor_element;
    workbench_anchor_element = undefined;
    workbench_anchor_context_key = "";
    workbench_anchor_kind = undefined;
    render();
    // Closing removes the popover from the DOM; if focus fell back to the
    // body, return it to the control that opened the workbench.
    if (
        anchor_element !== undefined &&
        document.activeElement === document.body &&
        document.contains(anchor_element)
    ) {
        anchor_element.focus();
    }
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
          workbench_scope_note: string;
          panel_repo_label: string;
          panel_worktree_label: string;
          panel_git_status_label: string;
          panel_git_status_class: string;
          panel_git_ref_label: string;
          panel_runtime_label: string;
          panel_model_label: string;
          panel_reasoning_effort_label: string;
          panel_reasoning_effort_meter_class: string;
          panel_header_runtime_label: string;
          panel_header_branch_label: string;
          panel_header_git_label: string;
          panel_header_commit_label: string;
          panel_header_git_title: string;
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
        topic_context.worktree_label?.trim() ||
        topic_context.topic ||
        git.worktree_name?.trim() ||
        "";
    const workbench_meta = [workbench_repo_label, git.branch?.trim() ?? ""]
        .filter((part) => part !== "")
        .join(" / ");
    const topic_branch_label =
        (git.detached === true ? $t({defaultMessage: "detached"}) : git.branch?.trim()) ||
        topic_context.topic;
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
    const head_commit = history_commits[0];
    const head_commit_label =
        head_commit === undefined
            ? (git.head_short ?? "")
            : [head_commit.hash, head_commit.summary]
                  .filter((part) => part.trim() !== "")
                  .join(" ");
    const recent_commit_count = history_commits.length;
    const topic_key = context_key(topic_context.stream, topic_context.topic);
    const git_tree = git_tree_by_context.get(topic_key);
    const git_tree_graph = git_tree?.graph.trim() ?? "";
    const git_tree_status = git_tree?.status_short.trim() ?? "";
    const canvas = canvas_by_context.get(topic_key);
    const canvas_content = canvas?.content ?? "";
    const canvas_rendered_html = canvas?.rendered_html ?? "";
    const reasoning_effort_label = full_reasoning_effort_label(topic_context);
    const model_label = display_model_label(topic_context);
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
        workbench_scope_note: $t({
            defaultMessage: "Changes and commits are scoped to this topic worktree.",
        }),
        panel_repo_label,
        panel_worktree_label,
        panel_git_status_label: git_status_label(git, changed_label),
        panel_git_status_class: git_status_class(git),
        panel_git_ref_label: git_ref_label(git),
        panel_runtime_label: panel_runtime_label_for_testing({
            model: topic_context.model,
            reasoning_effort: topic_context.reasoning_effort,
        }),
        panel_model_label: model_label,
        panel_reasoning_effort_label: reasoning_effort_label,
        panel_reasoning_effort_meter_class: reasoning_effort_meter_class(
            topic_context.reasoning_effort,
        ),
        panel_header_runtime_label: `${model_label} · ${reasoning_effort_label}`,
        panel_header_branch_label: topic_branch_label,
        panel_header_git_label: topic_header_git_label(git, changed_count),
        panel_header_commit_label: head_commit_label,
        panel_header_git_title: topic_header_git_title({
            repo: panel_repo_label,
            worktree: panel_worktree_label,
            branch: topic_branch_label,
            commit: head_commit_label,
            workdir: topic_context.workdir,
        }),
        has_workbench_diff_summary: workbench_diff_summary_label !== "",
        workbench_diff_summary_label,
        workbench_diff_file_label,
        workbench_diff_insertions_label,
        workbench_diff_deletions_label,
    };
}

const pear_topic_header_status_selector = ".pear-topic-header-status";
const pear_topic_header_control_selector = ".pear-topic-header-status, .pear-topic-header-control";

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

function topic_header_work_label(display_context: PearDisplayTopicContext): string {
    return display_context.panel_header_git_label;
}

function topic_header_git_label(git: PearGitContext, changed_count: number): string {
    if (!git.available) {
        return $t({defaultMessage: "no repo"});
    }
    if (git.dirty) {
        if (changed_count === 1) {
            return $t({defaultMessage: "1 file"});
        }
        if (changed_count > 1) {
            return $t({defaultMessage: "{changed_count} files"}, {changed_count});
        }
        return $t({defaultMessage: "dirty"});
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
    return git.head_short ?? $t({defaultMessage: "clean"});
}

function topic_header_git_title(options: {
    repo: string;
    worktree: string;
    branch: string;
    commit: string;
    workdir: string;
}): string {
    return [options.repo, options.worktree, options.branch, options.commit, options.workdir]
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
        {runtime_label: topic_context.panel_header_runtime_label || runtime_label},
    );
    const branch_title = $t(
        {defaultMessage: "Pear topic branch: {branch_label}"},
        {branch_label: topic_context.panel_header_branch_label},
    );
    const work_label = topic_header_work_label(topic_context);
    const work_title = topic_context.panel_header_git_title || work_label;
    const control_key = JSON.stringify({
        stream_id,
        topic,
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

    $(pear_topic_header_control_selector)
        .filter((_index, element) => !$headers.toArray().some((header) => header.contains(element)))
        .remove();

    $headers.each((_index, header) => {
        const $controls = $(header).find(".recipient_bar_controls").first();
        if ($controls.length === 0) {
            return;
        }
        let $status_cluster = $(header)
            .find(pear_topic_header_status_selector)
            .filter(
                (_inner_index, element) => $(element).attr("data-pear-header-key") === control_key,
            )
            .first();
        $(header)
            .find(pear_topic_header_status_selector)
            .filter(
                (_inner_index, element) => $(element).attr("data-pear-header-key") !== control_key,
            )
            .remove();
        if ($status_cluster.length === 0) {
            const $status_dot = $("<span>")
                .addClass("pear-topic-header-status-dot")
                .attr("aria-hidden", "true");
            const $model_control = $("<button>")
                .addClass(
                    "pear-topic-header-control pear-topic-header-runtime-control pear-topic-header-model-control pear-runtime-menu-trigger",
                )
                .attr({
                    type: "button",
                    "data-pear-header-key": control_key,
                })
                .append(
                    $("<span>").addClass("pear-runtime-menu-model-label"),
                    $("<i>")
                        .addClass(
                            "zulip-icon zulip-icon-chevron-down pear-topic-header-control-chevron",
                        )
                        .attr("aria-hidden", "true"),
                );
            const $effort_control = $("<button>")
                .addClass(
                    "pear-topic-header-control pear-topic-header-runtime-control pear-topic-header-effort-control pear-runtime-menu-trigger",
                )
                .attr({
                    type: "button",
                    "data-pear-header-key": control_key,
                })
                .append(
                    $("<span>").addClass("pear-runtime-menu-effort-label"),
                    $("<i>")
                        .addClass(
                            "zulip-icon zulip-icon-chevron-down pear-topic-header-control-chevron",
                        )
                        .attr("aria-hidden", "true"),
                );
            $status_cluster = $("<span>")
                .addClass("pear-topic-header-status no-select")
                .attr("data-pear-header-key", control_key);
            const $runtime_group = $("<span>")
                .addClass("pear-topic-header-runtime-group")
                .append($status_dot, $model_control, $effort_control);
            const $subtitle = $("<span>").addClass("pear-topic-header-subtitle");
            const $branch_control = $("<span>")
                .addClass(
                    "pear-topic-header-control pear-topic-header-branch-control tippy-zulip-delayed-tooltip",
                )
                .attr("data-pear-header-key", control_key)
                .append($("<span>").addClass("pear-topic-header-branch-label"));
            const $work_control = $("<button>")
                .addClass(
                    "pear-topic-header-control pear-topic-header-git-control pear-panel-open-action",
                )
                .attr({
                    type: "button",
                    "data-pear-header-key": control_key,
                })
                .append($("<span>").addClass("pear-topic-header-work-label"));
            const $meta_separator = $("<span>")
                .addClass("pear-topic-header-meta-separator")
                .attr("aria-hidden", "true")
                .text("·");
            $subtitle.append($branch_control, $meta_separator, $work_control);
            $status_cluster.append($runtime_group, $subtitle);
            $controls.before($status_cluster);
        }
        // Skip the label/tooltip updates below when nothing this cluster
        // displays has changed.  render() runs up to twice a second during an
        // active run, and the block below tears down and rebuilds the runtime
        // tippy tooltips every time; without this guard they flicker constantly.
        const header_signature = JSON.stringify({
            runtime_active,
            runtime_title,
            model_label: topic_context.panel_model_label ?? runtime_label,
            effort_label: topic_context.panel_reasoning_effort_label ?? "",
            branch_title,
            branch_label: topic_context.panel_header_branch_label,
            work_label,
            work_title,
            git_status_class: topic_context.panel_git_status_class,
            has_workbench_diff: topic_context.has_workbench_diff,
        });
        if ($status_cluster.attr("data-pear-header-signature") === header_signature) {
            return;
        }
        $status_cluster.attr("data-pear-header-signature", header_signature);
        $status_cluster.toggleClass("pear-topic-header-running", runtime_active);

        const $runtime_controls = $status_cluster.find(".pear-topic-header-runtime-control");
        $runtime_controls.removeClass("tippy-zulip-delayed-tooltip").each((_index, element) => {
            const tippy_element: HTMLElement & {_tippy?: {destroy: () => void}} = element;
            tippy_element._tippy?.destroy();
        });
        $runtime_controls.attr({
            "aria-label": runtime_title,
            "data-tippy-content": runtime_title,
        });
        $runtime_controls.toggleClass("pear-topic-header-running", runtime_active);
        $status_cluster
            .find(".pear-topic-header-status-dot")
            .toggleClass("pear-topic-header-status-dot-active", runtime_active);
        $status_cluster
            .find(".pear-runtime-menu-model-label")
            .text(topic_context.panel_model_label ?? runtime_label);
        $status_cluster
            .find(".pear-runtime-menu-effort-label")
            .text(topic_context.panel_reasoning_effort_label ?? "");

        const $branch_control = $status_cluster.find(".pear-topic-header-branch-control");
        $branch_control.attr({
            "aria-label": branch_title,
            "data-tippy-content": branch_title,
        });
        $branch_control
            .find(".pear-topic-header-branch-label")
            .text(topic_context.panel_header_branch_label);

        const $work_control = $status_cluster.find(".pear-topic-header-git-control");
        $work_control
            .removeClass(
                "pear-topic-header-work-clean pear-topic-header-work-dirty pear-topic-header-work-unknown",
            )
            .addClass(`pear-topic-header-work-${topic_context.panel_git_status_class}`);
        $work_control.attr({
            "aria-label": $t({defaultMessage: "Open Pear Git details: {work_label}"}, {work_label}),
            title: work_title || work_label,
            "data-pear-workbench-view": topic_context.has_workbench_diff ? "diff" : "history",
        });
        $work_control.find(".pear-topic-header-work-label").text(work_label);
    });
}

export function refresh_topic_header_controls(): void {
    const {stream, topic} = current_context();
    const source_topic_context =
        stream !== undefined && topic !== undefined
            ? display_source_topic_context(stream, topic)
            : undefined;
    const display_context = display_topic_context(source_topic_context);
    render_topic_header_controls({
        stream,
        topic,
        topic_context: display_context,
        active_run_status: active_display_run(last_runs)?.status,
    });
}

function anchor_kind_for_element(element: HTMLElement): WorkbenchAnchorKind {
    return element.classList.contains("pear-topic-header-git-control") ? "git" : "context";
}

function visible_header_anchor_for_kind(kind: WorkbenchAnchorKind): HTMLElement | undefined {
    const {stream, topic} = current_context();
    const stream_id = stream === undefined ? undefined : stream_data.get_stream_id(stream);
    if (stream_id === undefined || topic === undefined) {
        return undefined;
    }
    const selector =
        kind === "context"
            ? ".message_header_stream .pear-topic-header-runtime-control"
            : ".message_header_stream .pear-topic-header-git-control";
    return [...document.querySelectorAll<HTMLElement>(selector)].find((element) => {
        const header = element.closest<HTMLElement>(".message_header_stream");
        return (
            header !== null &&
            Number.parseInt(header.getAttribute("data-stream-id") ?? "", 10) === stream_id &&
            header.getAttribute("data-topic-name") === topic &&
            element.getClientRects().length > 0
        );
    });
}

function visible_header_anchor(): HTMLElement | undefined {
    if (
        workbench_anchor_context_key === current_context_key() &&
        workbench_anchor_element?.isConnected === true &&
        workbench_anchor_element.getClientRects().length > 0
    ) {
        return workbench_anchor_element;
    }
    if (
        workbench_anchor_context_key === current_context_key() &&
        workbench_anchor_kind !== undefined
    ) {
        return (
            visible_header_anchor_for_kind(workbench_anchor_kind) ??
            visible_header_anchor_for_kind(workbench_anchor_kind === "git" ? "context" : "git")
        );
    }
    return visible_header_anchor_for_kind("context") ?? visible_header_anchor_for_kind("git");
}

// Minimum popover body height we try to preserve by pushing the popover up
// when the anchor sits near the bottom of the viewport.
const WORKBENCH_POPOVER_MIN_BODY_PX = 260;

// Clamp the popover's top so it stays fully on-screen: never above the top
// margin (a scrolled-away anchor yields a negative desired top), and never so
// low that the minimum body height would spill past the bottom edge.
export function workbench_popover_top_for_testing(options: {
    desired_top: number;
    viewport_height: number;
    margin: number;
    min_body_height: number;
}): number {
    const max_top = Math.max(
        options.margin,
        options.viewport_height - options.min_body_height - options.margin,
    );
    return Math.min(Math.max(options.desired_top, options.margin), max_top);
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
        : Math.min(420, Math.max(360, viewport_width - margin * 2));
    const anchor_rect = anchor?.getBoundingClientRect();
    const anchor_center =
        anchor_rect === undefined ? viewport_width / 2 : anchor_rect.left + anchor_rect.width / 2;
    const header_rect = anchor
        ?.closest<HTMLElement>(".message_header_stream")
        ?.getBoundingClientRect();
    const min_left = compact_viewport ? margin : Math.max(margin, header_rect?.left ?? margin);
    const left = compact_viewport
        ? margin
        : Math.min(Math.max(anchor_center - width / 2, min_left), viewport_width - width - margin);
    const top = workbench_popover_top_for_testing({
        desired_top: anchor_rect === undefined ? 76 : anchor_rect.bottom + 8,
        viewport_height,
        margin,
        min_body_height: WORKBENCH_POPOVER_MIN_BODY_PX,
    });
    const arrow_left = Math.min(Math.max(anchor_center - left, 18), width - 18);
    panel.style.setProperty("--pear-workbench-popover-left", `${Math.round(left)}px`);
    panel.style.setProperty("--pear-workbench-popover-top", `${Math.round(top)}px`);
    panel.style.setProperty("--pear-workbench-popover-width", `${Math.round(width)}px`);
    // Available space below the (clamped) top; the body scrolls internally if
    // its content is taller, so it can never extend past the viewport bottom.
    panel.style.setProperty(
        "--pear-workbench-popover-max-height",
        `${Math.max(margin, Math.round(viewport_height - top - margin))}px`,
    );
    panel.style.setProperty("--pear-workbench-popover-arrow-left", `${Math.round(arrow_left)}px`);
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

export function canvas_cache_is_stale_for_testing(options: {
    fetched_at: number;
    now: number;
}): boolean {
    return options.now - options.fetched_at >= CANVAS_REFRESH_INTERVAL_MS;
}

function fetch_canvas_for_context(topic_context: PearTopicContext): void {
    const key = context_key(topic_context.stream, topic_context.topic);
    const cached_canvas = canvas_by_context.get(key);
    const had_cache = cached_canvas !== undefined;
    if (cached_canvas !== undefined) {
        render_canvas_for_context(key, cached_canvas);
        const fresh = !canvas_cache_is_stale_for_testing({
            fetched_at: canvas_fetched_at_by_context.get(key) ?? 0,
            now: Date.now(),
        });
        if (fresh) {
            return;
        }
        // Stale cache: fall through to a background refresh that keeps the
        // cached canvas on screen (no loading flicker) until the new copy lands.
    }
    if (key === "" || canvas_loading_contexts.has(key)) {
        return;
    }
    // canvas_loading_contexts also guards against re-entrant fetches for the
    // silent refresh, but we only surface the loading state when nothing is
    // currently shown.
    canvas_loading_contexts.add(key);
    if (!had_cache) {
        canvas_error_by_context.delete(key);
    }
    const sequence = next_canvas_fetch_sequence(key);
    if (!had_cache) {
        render();
    }
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
            canvas_fetched_at_by_context.set(key, Date.now());
            render_canvas_for_context(key, raw_data.pear_canvas);
        },
        error(xhr) {
            if (canvas_fetch_sequence_by_context.get(key) !== sequence) {
                return;
            }
            if (xhr.readyState !== 0 && !had_cache) {
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
            if (!had_cache) {
                render();
            }
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
    canvas_fetched_at_by_context.delete(key);
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
        has_context: display_source_topic_context(stream, topic) !== undefined,
    });
}

function record_context_refresh(stream: string | undefined, topic: string | undefined): void {
    if (stream === undefined || topic === undefined) {
        return;
    }
    last_context_refresh_key = context_key(stream, topic);
    last_context_refresh_at = Date.now();
}

function remember_topic_context(topic_context: PearTopicContext | null | undefined): void {
    if (topic_context === undefined || topic_context === null) {
        return;
    }
    topic_context_by_context.set(
        context_key(topic_context.stream, topic_context.topic),
        topic_context,
    );
}

export function handle_narrow_activated(): void {
    const key = current_context_key();
    if (key === last_observed_context_key) {
        render();
        return;
    }
    last_observed_context_key = key;
    const cached_context = topic_context_by_context.get(key);
    // Render the new topic with a clean run slate; last_runs still holds the
    // previous topic's runs, so reusing it would flash that topic's run state
    // (and active-run chrome) in the new header until the fetch below returns.
    render([], cached_context);
    refresh({force_context: true});
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
    cached_topic_context?: PearTopicContext | null | undefined;
}): PearTopicContext | null | undefined {
    if (options.response_topic_context !== undefined && options.response_topic_context !== null) {
        return options.response_topic_context;
    }
    if (options.live || !options.include_context) {
        return options.last_topic_context ?? options.cached_topic_context;
    }
    return options.last_topic_context ?? options.cached_topic_context ?? null;
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
    const {stream, topic} = current_context();
    const has_stream = stream !== undefined;
    const has_topic = has_stream && topic !== undefined;
    const next_context_key = context_key(stream, topic);
    const source_topic_context = has_topic
        ? display_source_topic_context(stream, topic, topic_context)
        : undefined;
    last_topic_context = source_topic_context ?? topic_context;
    const active_run = active_display_run(runs);
    const manual_workbench_open = work_panel_open_by_context.get(next_context_key) ?? false;
    const viewport_allows_workbench = current_viewport_allows_workbench();
    const workbench_open = workbench_open_for_testing({
        context_key_value: next_context_key,
        manually_open: viewport_allows_workbench && manual_workbench_open,
        has_error: viewport_allows_workbench && last_error !== "",
        error_dismissed,
    });
    const panel_open = workbench_open;
    pear_run_state.set_active_run_result_messages(runs, {
        force_reconcile: active_run === undefined,
    });
    const panel_status = active_run?.status ?? "idle";
    const display_context = has_topic ? display_topic_context(source_topic_context) : undefined;
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
        // The html() swap below replaces every node in the panel, which drops
        // focus and the caret.  A live re-render fires up to twice a second
        // during an active run, so without this a user typing in the whisper
        // textarea would silently lose their cursor mid-word.  Capture the
        // focused element and caret, then restore them after the swap.  (We are
        // past ensure_pear_panel_host(), so the DOM globals are available.)
        const focused = document.activeElement;
        const focused_id =
            focused instanceof HTMLElement && panel.contains(focused) ? focused.id : "";
        const caret =
            focused instanceof HTMLTextAreaElement || focused instanceof HTMLInputElement
                ? {start: focused.selectionStart, end: focused.selectionEnd}
                : undefined;

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
        if (focused_id !== "") {
            const refocused = panel.querySelector(`#${focused_id}`);
            if (refocused instanceof HTMLElement) {
                refocused.focus();
                if (
                    caret !== undefined &&
                    (refocused instanceof HTMLTextAreaElement ||
                        refocused instanceof HTMLInputElement)
                ) {
                    refocused.setSelectionRange(caret.start, caret.end);
                }
            }
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

export function polling_paused_for_testing(options: {
    hidden: boolean;
    force_context: boolean;
}): boolean {
    // Skip timer-driven polls on a hidden tab (they otherwise keep spending the
    // 500ms/5s /json/pear/runs rate budget in the background); explicit forced
    // refreshes still run, and a visibilitychange listener catches up on return.
    return options.hidden && !options.force_context;
}

export function refresh(options: {force_context?: boolean; live?: boolean} = {}): void {
    const live = options.live === true;
    if (
        typeof document !== "undefined" &&
        polling_paused_for_testing({
            hidden: document.hidden === true,
            force_context: options.force_context === true,
        })
    ) {
        return;
    }
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
            error_dismissed = false;
            const had_active_run = has_active_run(last_runs);
            const has_active_run_after_refresh = has_active_run(data.pear_runs);
            const previous_topic_context = display_source_topic_context(stream, topic);
            const topic_context = topic_context_after_refresh_for_testing({
                include_context,
                live,
                response_topic_context: data.pear_topic_context,
                last_topic_context: previous_topic_context,
                cached_topic_context: cached_topic_context(stream, topic),
            });
            remember_topic_context(topic_context);
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

export function optimistic_topic_context_for_testing(
    topic_context: PearTopicContext,
    options: {model?: string; reasoning_effort?: string},
): PearTopicContext {
    const model = options.model ?? topic_context.model;
    const reasoning_effort =
        options.reasoning_effort ??
        (model === topic_context.model
            ? topic_context.reasoning_effort
            : next_effort_for_model(topic_context, model));
    return {
        ...topic_context,
        model,
        reasoning_effort,
    };
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
        const requested_view = $(e.currentTarget).attr("data-pear-workbench-view");
        open_work_details(
            requested_view === "diff" ||
                requested_view === "history" ||
                requested_view === "canvas" ||
                requested_view === "side"
                ? requested_view
                : undefined,
            e.currentTarget,
        );
    });
    $(document).on("keydown.pear_panel", (e) => {
        // Give keyboard users inside the workbench an exit; clicks outside
        // already close it. Scoped to the panel so global Escape handling
        // (compose, overlays) is unaffected.
        if (e.key !== "Escape") {
            return;
        }
        if ($(e.target).closest("#pear-panel").length === 0) {
            return;
        }
        close_work_details();
    });
    $(document).on("click.pear_panel", (e) => {
        // Ignore clicks inside the workbench itself.  Using a target check here
        // instead of stopPropagation on #pear-panel means other delegated
        // document handlers (e.g. Zulip's own popovers closing on outside
        // click) still see clicks that land inside the Pear panel.
        if ($(e.target).closest("#pear-panel").length > 0) {
            return;
        }
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
        // Coalesce the resize-event burst into one render per frame.
        if (resize_render_frame_id !== undefined) {
            return;
        }
        resize_render_frame_id = requestAnimationFrame(() => {
            resize_render_frame_id = undefined;
            render();
        });
    });
    $(window).on("scroll.pear_panel", () => {
        position_workbench_popover();
    });
    $(document).on("visibilitychange.pear_panel", () => {
        if (!document.hidden) {
            refresh({force_context: true});
        }
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
    $(document).off("visibilitychange.pear_panel");
    clear_live_refresh();
    live_refresh_in_flight = false;
}
