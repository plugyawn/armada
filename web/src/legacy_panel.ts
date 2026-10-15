import $ from "jquery";
import * as z from "zod/mini";

import render_legacy_panel from "../templates/legacy_panel.hbs";
import render_legacy_topic_header_controls from "../templates/legacy_topic_header_controls.hbs";
import render_legacy_workspace_rail from "../templates/legacy_workspace_rail.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as legacy_run_state from "./legacy_run_state.ts";
import {
    active_plan_presentation,
    compact_reasoning_effort,
    context_key,
    model_reasoning_efforts,
} from "./legacy_runtime_shared.ts";
import * as legacy_wall from "./legacy_wall.ts";
import * as legacy_workspace from "./legacy_workspace.ts";
import * as legacy_workspace_marks from "./legacy_workspace_marks.ts";
import * as message_lists from "./message_lists.ts";
import * as narrow_state from "./narrow_state.ts";
import * as stream_data from "./stream_data.ts";
import * as timerender from "./timerender.ts";
import * as typing_events from "./typing_events.ts";

const armada_run_event_schema = z.object({
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

const armada_usage_schema = z.object({
    input_tokens: z.optional(z.number()),
    cached_input_tokens: z.optional(z.number()),
    output_tokens: z.optional(z.number()),
    reasoning_output_tokens: z.optional(z.number()),
    total_tokens: z.optional(z.number()),
    cache_percent: z.optional(z.number()),
    duration_seconds: z.nullish(z.number()),
    summary: z.optional(z.string()),
});

const armada_live_run_schema = z.object({
    status: z.string(),
    updated_at: z.number(),
    operation: z.nullish(z.object({kind: z.string(), label: z.string()})),
    has_visible_assistant_text: z.optional(z.boolean()),
});

const armada_agent_thread_schema = z.object({
    thread_id: z.string(),
    nickname: z.nullish(z.string()),
    role: z.nullish(z.string()),
    name: z.nullish(z.string()),
    status: z.string(),
    current_activity: z.optional(z.string()),
});

const armada_run_schema = z.object({
    id: z.number(),
    snapshot_sequence: z.optional(z.number()),
    trigger_message_id: z.number(),
    result_message_id: z.nullish(z.number()),
    stream: z.optional(z.string()),
    topic: z.optional(z.string()),
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
    ambient_triggered: z.boolean(),
    usage: z.nullish(armada_usage_schema),
    tool_count: z.optional(z.number()),
    date_created: z.optional(z.number()),
    date_updated: z.optional(z.number()),
    plan: z.nullish(z.array(z.object({step: z.string(), status: z.string()}))),
    overview: z.nullish(z.string()),
    live: z.nullish(armada_live_run_schema),
    agents: z.optional(z.array(armada_agent_thread_schema)),
    events: z.optional(z.array(armada_run_event_schema)),
});

const armada_reasoning_effort_schema = z.object({
    effort: z.string(),
    label: z.string(),
    description: z.optional(z.string()),
});

const armada_model_catalog_entry_schema = z.object({
    slug: z.string(),
    display_name: z.string(),
    default_reasoning_level: z.optional(z.string()),
    supported_reasoning_levels: z.optional(z.array(armada_reasoning_effort_schema)),
});

const armada_git_changed_file_schema = z.object({
    status: z.string(),
    kind: z.string(),
    path: z.string(),
});

const armada_git_commit_schema = z.object({
    hash: z.string(),
    summary: z.string(),
});

const armada_git_context_schema = z.object({
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
    changed_files: z.optional(z.array(armada_git_changed_file_schema)),
    changed_files_truncated: z.optional(z.boolean()),
    recent_commits: z.optional(z.array(armada_git_commit_schema)),
    last_commit: z.optional(
        z.object({
            sha: z.string(),
            subject: z.string(),
            author: z.string(),
            timestamp: z.nullable(z.number()),
        }),
    ),
    commit_count: z.optional(z.number()),
});

const armada_topic_context_schema = z.object({
    stream: z.string(),
    topic: z.string(),
    model: z.string(),
    default_model: z.string(),
    model_choices: z.array(z.string()),
    model_catalog: z.optional(z.array(armada_model_catalog_entry_schema)),
    reasoning_effort: z.string(),
    default_reasoning_effort: z.string(),
    reasoning_efforts: z.array(armada_reasoning_effort_schema),
    response_policy: z.union([z.literal("ambient"), z.literal("mentions_only")]),
    github_repo: z.string(),
    pinned_repo: z.optional(z.string()),
    repo_label: z.optional(z.string()),
    repo_title: z.optional(z.string()),
    worktree_label: z.optional(z.string()),
    workdir: z.string(),
    branch: z.optional(z.string()),
    branch_is_topic_default: z.optional(z.boolean()),
    workspace_uuid: z.string(),
    git: armada_git_context_schema,
});

const armada_runs_response_schema = z.object({
    armada_runs: z.array(armada_run_schema),
    armada_topic_context: z.nullish(armada_topic_context_schema),
    runtime_config: z.optional(
        z.object({
            model: z.nullish(z.string()),
            model_choices: z.optional(z.array(z.string())),
            model_catalog: z.optional(z.array(armada_model_catalog_entry_schema)),
            reasoning_effort: z.nullish(z.string()),
            reasoning_efforts: z.optional(z.array(armada_reasoning_effort_schema)),
        }),
    ),
});

type ArmadaRunEvent = z.infer<typeof armada_run_event_schema>;
type ArmadaRun = z.infer<typeof armada_run_schema>;
type ArmadaRunsResponse = z.infer<typeof armada_runs_response_schema>;
type ArmadaGitContext = z.infer<typeof armada_git_context_schema>;
type ArmadaWorkspaceContext = z.infer<typeof armada_topic_context_schema>;
type ArmadaGitCommit = z.infer<typeof armada_git_commit_schema>;

type ArmadaWorkbenchView = "diff" | "history";
type ArmadaDisplayTopicContext = NonNullable<ReturnType<typeof display_topic_context>>;

let initialized = false;
let last_error = "";
let last_runs: ArmadaRun[] = [];
let last_topic_context: ArmadaWorkspaceContext | null | undefined;
let resize_render_frame_id: number | undefined;
let live_render_frame_id: number | undefined;
let last_render_state_key = "";
let last_auxiliary_render_key = "";
let last_context_refresh_key = "";
let last_context_refresh_at = 0;
const work_panel_open_by_context = new Map<string, boolean>();
const dismissed_error_by_context = new Map<string, string>();
// Contexts whose last runs payload included an active run; used to detect
// the active→idle transition that retires the agent's typing line.
const contexts_with_active_run = new Set<string>();
const workbench_view_by_context = new Map<string, ArmadaWorkbenchView>();
const topic_context_by_context = new Map<string, ArmadaWorkspaceContext>();
type RefreshOptions = {force_context?: boolean; live?: boolean};
type RefreshRequest = {
    generation: number;
    pending: RefreshOptions | undefined;
};
const refresh_requests_by_context = new Map<string, RefreshRequest>();
let next_refresh_generation = 0;
let workbench_anchor_element: HTMLElement | undefined;
let workbench_anchor_context_key = "";
type WorkbenchAnchorKind = "context" | "git";
let workbench_anchor_kind: WorkbenchAnchorKind | undefined;
// Undefined until the first message view is shown, so that the first one
// always asks for its runs, a view with no topic included.
let last_observed_context_key: string | undefined;
const CONTEXT_REFRESH_INTERVAL_MS = 60_000;
const WORKBENCH_MIN_VIEWPORT_WIDTH_PX = 320;
const WORKBENCH_POPOVER_MARGIN_PX = 8;
const RUN_LIVENESS_RECONCILIATION_MS = 20_000;
let liveness_reconciliation_timer: ReturnType<typeof setTimeout> | undefined;
let liveness_reconciliation_owner: string | undefined;
let liveness_reconciliation_attempted = false;

function first_nonempty(...values: (string | undefined)[]): string {
    return values.find((value) => value !== undefined && value !== "") ?? "";
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

function next_effort_for_model(topic_context: ArmadaWorkspaceContext, model: string): string {
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
    topic_context: ArmadaWorkspaceContext,
    effort = topic_context.reasoning_effort,
): string {
    return first_nonempty(
        topic_context.reasoning_efforts.find((choice) => choice.effort === effort)?.label,
        effort,
        $t({defaultMessage: "Medium"}),
    );
}

function display_model_label(topic_context: ArmadaWorkspaceContext): string {
    const catalog_entry = topic_context.model_catalog?.find(
        (entry) => entry.slug === topic_context.model,
    );
    return first_nonempty(
        catalog_entry?.display_name,
        topic_context.model,
        topic_context.default_model,
    );
}

function reasoning_effort_meter_class(effort: string | undefined): string {
    if (effort === "low" || effort === "medium" || effort === "high" || effort === "xhigh") {
        return effort;
    }
    if (effort === "max" || effort === "ultra") {
        // The new top levels reuse the full-meter styling.
        return "xhigh";
    }
    return "medium";
}

function is_armada_runs_response(data: unknown): data is ArmadaRunsResponse {
    return armada_runs_response_schema.safeParse(data).success;
}

function display_event(event: ArmadaRunEvent): ArmadaRunEvent & {
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

function current_context_key(): string {
    const {stream, topic} = current_context();
    return context_key(stream, topic);
}

function topic_context_matches(
    topic_context: ArmadaWorkspaceContext | null | undefined,
    stream: string | undefined,
    topic: string | undefined,
): topic_context is ArmadaWorkspaceContext {
    return (
        stream !== undefined &&
        topic !== undefined &&
        topic_context?.stream.toLowerCase() === stream.toLowerCase() &&
        topic_context.topic.toLowerCase() === topic.toLowerCase()
    );
}

function cached_topic_context(
    stream: string | undefined,
    topic: string | undefined,
): ArmadaWorkspaceContext | undefined {
    return topic_context_by_context.get(context_key(stream, topic));
}

export function topic_context_for_display_for_testing(options: {
    stream: string | undefined;
    topic: string | undefined;
    render_topic_context: ArmadaWorkspaceContext | null | undefined;
    cached_topic_context: ArmadaWorkspaceContext | undefined;
}): ArmadaWorkspaceContext | undefined {
    if (topic_context_matches(options.render_topic_context, options.stream, options.topic)) {
        return options.render_topic_context;
    }
    return options.cached_topic_context;
}

function display_source_topic_context(
    stream: string | undefined,
    topic: string | undefined,
    topic_context: ArmadaWorkspaceContext | null | undefined = last_topic_context,
): ArmadaWorkspaceContext | undefined {
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
}): boolean {
    if (options.context_key_value === "") {
        return false;
    }
    // Status fetch failures are quiet inline workbench errors. They must not
    // seize focus by force-opening a dialog the user did not request.
    return options.manually_open;
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

function git_status_label(git: ArmadaGitContext, changed_label: string): string {
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

function git_status_class(git: ArmadaGitContext): string {
    if (!git.available) {
        return "unavailable";
    }
    if (git.dirty || (git.ahead ?? 0) > 0 || (git.behind ?? 0) > 0 || git.detached === true) {
        return "dirty";
    }
    return "clean";
}

function git_ref_label(git: ArmadaGitContext): string {
    const branch = git.detached === true ? $t({defaultMessage: "detached"}) : (git.branch ?? "");
    return [branch.trim(), (git.head_short ?? "").trim()].filter((part) => part !== "").join(" / ");
}

function git_has_diff(git: ArmadaGitContext): boolean {
    return (git.changed_files ?? []).length > 0;
}

function git_has_history(git: ArmadaGitContext): boolean {
    return git.available || (git.recent_commits ?? []).length > 0;
}

export function workbench_view_for_testing(options: {
    requested: string | undefined;
    has_diff: boolean;
    has_history: boolean;
}): ArmadaWorkbenchView {
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

export function workbench_view_for_artifact_label(label: string): ArmadaWorkbenchView | undefined {
    const normalized_label = label.trim().toLowerCase();
    if (normalized_label === "diff") {
        return "diff";
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
            "armada session",
            "codex session",
        ].includes(normalized_label)
    ) {
        return "history";
    }
    return undefined;
}

export function open_work_details(
    requested_view?: ArmadaWorkbenchView,
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
    const workbench = document.querySelector<HTMLElement>("#legacy-panel .legacy-workbench");
    if (workbench !== null) {
        position_workbench_popover();
        if (!workbench.contains(document.activeElement)) {
            workbench.focus({preventScroll: true});
        }
        return;
    }
    refresh({force_context: true});
}

function select_workbench_view(view: ArmadaWorkbenchView): void {
    const key = current_context_key();
    if (key === "") {
        return;
    }
    workbench_view_by_context.set(key, view);
    work_panel_open_by_context.set(key, true);
    render();
}

function workbench_focusable_elements(workbench: HTMLElement): HTMLElement[] {
    return [
        ...workbench.querySelectorAll<HTMLElement>(
            'button:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
        ),
    ].filter((element) => !element.hasAttribute("hidden"));
}

export function open_work_details_for_artifact_label(label: string): void {
    if (["doc", "document"].includes(label.trim().toLowerCase())) {
        legacy_workspace.set_tab("readme");
        return;
    }
    open_work_details(workbench_view_for_artifact_label(label));
}

function close_work_details(options: {restore_focus?: boolean} = {}): void {
    const key = current_context_key();
    // Mirror the workbench-open predicate: when nothing could be visible,
    // skip the close-and-render so the document-level outside-click handler
    // doesn't re-render the panel on every stray click in the app.
    const manually_open = key !== "" && (work_panel_open_by_context.get(key) ?? false);
    if (!manually_open && workbench_anchor_element === undefined) {
        return;
    }
    if (key !== "") {
        work_panel_open_by_context.set(key, false);
    }
    const anchor_element = workbench_anchor_element;
    workbench_anchor_element = undefined;
    workbench_anchor_context_key = "";
    workbench_anchor_kind = undefined;
    render();
    // Closing removes the popover from the DOM; if focus fell back to the
    // body, return it to the control that opened the workbench.
    if (
        options.restore_focus !== false &&
        anchor_element !== undefined &&
        document.contains(anchor_element)
    ) {
        anchor_element.focus();
    }
}

function display_run(run: ArmadaRun): ArmadaRun & {
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
    events: ArmadaRunEvent[];
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

function display_topic_context(topic_context: ArmadaWorkspaceContext | undefined | null):
    | (ArmadaWorkspaceContext & {
          has_changed_files: boolean;
          has_recent_commits: boolean;
          changed_label: string;
          has_workbench_diff: boolean;
          has_workbench_history: boolean;
          workbench_view: ArmadaWorkbenchView;
          workbench_diff_selected: boolean;
          workbench_history_selected: boolean;
          workbench_diff_disabled: boolean;
          workbench_history_disabled: boolean;
          diff_tab_label: string;
          history_tab_label: string;
          history_commits: (ArmadaGitCommit & {is_head: boolean; ref_label: string})[];
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
          panel_branch_is_topic_default: boolean;
          panel_has_commitline: boolean;
          panel_commit_sha: string;
          panel_commit_subject: string;
          panel_commit_meta: string;
          panel_header_git_label: string;
          panel_header_commit_label: string;
          panel_header_git_title: string;
          has_workbench_diff_summary: boolean;
          workbench_diff_summary_label: string;
          workbench_diff_file_label: string;
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
    const workbench_title = first_nonempty(
        topic_context.worktree_label?.trim(),
        topic_context.topic,
    );
    const workbench_repo_label = first_nonempty(
        topic_context.repo_label?.trim(),
        topic_context.repo_title?.trim(),
        github_repo,
    );
    const panel_repo_label = first_nonempty(
        workbench_repo_label,
        git.repo_label?.trim(),
        git.repo_name?.trim(),
        $t({defaultMessage: "No repo pinned"}),
    );
    const panel_worktree_label = first_nonempty(
        topic_context.worktree_label?.trim(),
        topic_context.topic,
        git.worktree_name?.trim(),
    );
    // Drop meta parts that just repeat the title: with topic-named
    // branches the branch and the worktree label are usually the same
    // string, and printing it twice (both truncated) reads as a glitch.
    const workbench_meta = [workbench_repo_label, git.branch?.trim() ?? ""]
        .filter((part) => part !== "" && part !== workbench_title)
        .join(" / ");
    const topic_branch_label = first_nonempty(
        git.detached === true ? $t({defaultMessage: "detached"}) : git.branch?.trim(),
        topic_context.topic,
    );
    // The workspace branch is derived from the topic; when the server says
    // they still agree and the live checkout matches, the header chip
    // would just repeat the topic name beside itself. A surprising branch
    // (legacy default, custom, detached, drifted checkout) still shows.
    const live_branch = git.branch?.trim() ?? "";
    const branch_is_topic_default =
        topic_context.branch_is_topic_default === true &&
        git.detached !== true &&
        (live_branch === "" || live_branch === topic_context.branch?.trim());
    const visible_changed_count = (git.changed_files ?? []).length;
    const diff_file_count = visible_changed_count > 0 ? visible_changed_count : changed_count;
    const workbench_diff_file_label =
        diff_file_count === 0
            ? ""
            : diff_file_count === 1
              ? $t({defaultMessage: "1 file"})
              : $t({defaultMessage: "{diff_file_count} files"}, {diff_file_count});
    const workbench_diff_summary_label = workbench_diff_file_label;
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
        workbench_diff_disabled: false,
        workbench_history_disabled: false,
        diff_tab_label:
            changed_count > 0
                ? $t({defaultMessage: "Diff {changed_count}"}, {changed_count})
                : $t({defaultMessage: "Diff"}),
        history_tab_label:
            recent_commit_count > 0
                ? $t({defaultMessage: "History {recent_commit_count}"}, {recent_commit_count})
                : $t({defaultMessage: "History"}),
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
        panel_branch_is_topic_default: branch_is_topic_default,
        panel_has_commitline: git.last_commit !== undefined,
        panel_commit_sha: git.last_commit?.sha ?? "",
        panel_commit_subject: git.last_commit?.subject ?? "",
        panel_commit_meta: commit_meta_sentence(git),
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
    };
}

const armada_topic_header_status_selector = ".legacy-topic-header-status";
const armada_topic_header_control_selector =
    ".legacy-topic-header-status, .legacy-topic-header-control";

function ensure_armada_panel_host(): HTMLElement | undefined {
    let panel = document.querySelector<HTMLElement>("body > #legacy-panel");
    if (panel === null) {
        panel = document.querySelector<HTMLElement>("#legacy-panel");
        if (panel === null) {
            return undefined;
        }
        document.body.append(panel);
    }
    $("#legacy-panel").not(panel).remove();
    return panel;
}

function topic_header_work_label(display_context: ArmadaDisplayTopicContext): string {
    return display_context.panel_header_git_label;
}

function commit_meta_sentence(git: ArmadaGitContext): string {
    // The GitHub-grammar tail of the header commit sentence:
    // "— author · 2m ago · 12 commits". Parts degrade independently.
    const last_commit = git.last_commit;
    if (last_commit === undefined) {
        return "";
    }
    const parts: string[] = [];
    if (last_commit.author !== "") {
        parts.push(`— ${last_commit.author}`);
    }
    if (last_commit.timestamp !== null) {
        parts.push(
            timerender.relative_time_string_from_date(new Date(last_commit.timestamp * 1000), true),
        );
    }
    const commit_count = git.commit_count ?? 0;
    if (commit_count === 1) {
        parts.push($t({defaultMessage: "1 commit"}));
    } else if (commit_count > 1) {
        parts.push($t({defaultMessage: "{commit_count} commits"}, {commit_count}));
    }
    // The separator is spaced padding, not chip furniture.
    return parts.join(" · ");
}

function topic_header_git_label(git: ArmadaGitContext, changed_count: number): string {
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
    topic_context: ArmadaDisplayTopicContext | undefined;
    active_run_status: string | undefined;
}): void {
    const {stream, topic, topic_context} = options;
    const workspace_uuid = topic_context?.workspace_uuid.trim();
    legacy_workspace.sync({
        stream,
        topic,
        workspaceId: workspace_uuid === "" ? undefined : workspace_uuid,
    });
    if (stream === undefined || topic === undefined || topic_context === undefined) {
        return;
    }
    const stream_id = stream_data.get_stream_id(stream);
    if (stream_id === undefined) {
        return;
    }
    const runtime_label = first_nonempty(
        topic_context.panel_runtime_label,
        $t({defaultMessage: "Onix"}),
    );
    const runtime_status = options.active_run_status ?? "done";
    const runtime_active = runtime_status === "running" || runtime_status === "queued";
    const runtime_title = $t(
        {defaultMessage: "Onix runtime: {runtime_label}"},
        {runtime_label: first_nonempty(topic_context.panel_header_runtime_label, runtime_label)},
    );
    const branch_title = $t(
        {defaultMessage: "Onix topic branch: {branch_label}"},
        {branch_label: topic_context.panel_header_branch_label},
    );
    const work_label = topic_header_work_label(topic_context);
    const work_title = first_nonempty(topic_context.panel_header_git_title, work_label);
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

    $(armada_topic_header_control_selector)
        .filter((_index, element) => ![...$headers].some((header) => header.contains(element)))
        .remove();

    $headers.each((_index, header) => {
        const $controls = $(header).find(".recipient_bar_controls").first();
        if ($controls.length === 0) {
            return;
        }
        let $status_cluster = $(header)
            .find(armada_topic_header_status_selector)
            .filter(
                (_inner_index, element) =>
                    $(element).attr("data-legacy-header-key") === control_key,
            )
            .first();
        $(header)
            .find(armada_topic_header_status_selector)
            .filter(
                (_inner_index, element) =>
                    $(element).attr("data-legacy-header-key") !== control_key,
            )
            .remove();
        if ($status_cluster.length === 0) {
            // The static skeleton comes from the template; the label and
            // tooltip updates below fill it in place so tippy instances
            // survive re-renders.
            $status_cluster = $(render_legacy_topic_header_controls({control_key}));
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
            branch_is_topic_default: topic_context.panel_branch_is_topic_default,
            work_label,
            work_title,
            commit_sha: topic_context.panel_commit_sha,
            commit_subject: topic_context.panel_commit_subject,
            commit_meta: topic_context.panel_commit_meta,
            git_status_class: topic_context.panel_git_status_class,
            has_workbench_diff: topic_context.has_workbench_diff,
        });
        if ($status_cluster.attr("data-legacy-header-signature") === header_signature) {
            return;
        }
        $status_cluster.attr("data-legacy-header-signature", header_signature);
        $status_cluster.toggleClass("legacy-topic-header-running", runtime_active);

        const $runtime_controls = $status_cluster.find(".legacy-topic-header-runtime-control");
        $runtime_controls.removeClass("tippy-zulip-delayed-tooltip").each((_index, element) => {
            const tippy_element: HTMLElement & {_tippy?: {destroy: () => void}} = element;
            tippy_element._tippy?.destroy();
        });
        $runtime_controls.attr({
            "aria-label": runtime_title,
            "data-tippy-content": runtime_title,
        });
        $runtime_controls.toggleClass("legacy-topic-header-running", runtime_active);
        $status_cluster
            .find(".legacy-runtime-menu-model-label")
            .text(topic_context.panel_model_label ?? runtime_label);
        $status_cluster
            .find(".legacy-runtime-menu-effort-label")
            .text(topic_context.panel_reasoning_effort_label ?? "");

        const $branch_control = $status_cluster.find(".legacy-topic-header-branch-control");
        $branch_control.attr({
            "aria-label": branch_title,
            "data-tippy-content": branch_title,
        });
        $branch_control
            .find(".legacy-topic-header-branch-label")
            .text(topic_context.panel_header_branch_label);

        // Hide the branch chip when it has no label, or when the branch
        // is just the topic-derived default — repeating the topic name
        // beside itself says nothing. Only surprising branches show.
        const branch_label_present =
            topic_context.panel_header_branch_label !== "" &&
            !topic_context.panel_branch_is_topic_default;
        $branch_control.toggleClass("legacy-topic-header-control-hidden", !branch_label_present);

        // The commit line (design §8.2): silence is the empty state — no
        // commits, no line, no "no repo" furniture.
        $status_cluster
            .find(".legacy-topic-header-commitline")
            .toggleClass("legacy-topic-header-control-hidden", !topic_context.panel_has_commitline);

        const $work_control = $status_cluster.find(".legacy-topic-header-git-control");
        $work_control
            .removeClass(
                "legacy-topic-header-work-clean legacy-topic-header-work-dirty legacy-topic-header-work-unknown",
            )
            .addClass(`legacy-topic-header-work-${topic_context.panel_git_status_class}`);
        $work_control.attr({
            "aria-label": $t({defaultMessage: "Open Onix Git details: {work_label}"}, {work_label}),
            title: first_nonempty(work_title, work_label),
            "data-legacy-workbench-view": topic_context.has_workbench_diff ? "diff" : "history",
        });
        $work_control.find(".legacy-commitline-sha").text(topic_context.panel_commit_sha);
        $work_control.find(".legacy-commitline-subject").text(topic_context.panel_commit_subject);
        $work_control.find(".legacy-commitline-meta").text(topic_context.panel_commit_meta);

        // A freshly injected cluster carries the template's hidden,
        // unselected tab strip; reapply the strip state in the same
        // pass or it blinks until the next panel render.
        legacy_workspace.refresh_tab_strips();
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
    return element.classList.contains("legacy-topic-header-git-control") ? "git" : "context";
}

function visible_header_anchor_for_kind(kind: WorkbenchAnchorKind): HTMLElement | undefined {
    const {stream, topic} = current_context();
    const stream_id = stream === undefined ? undefined : stream_data.get_stream_id(stream);
    if (stream_id === undefined || topic === undefined) {
        return undefined;
    }
    const selector =
        kind === "context"
            ? ".message_header_stream .legacy-topic-header-runtime-control"
            : ".message_header_stream .legacy-topic-header-git-control";
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
    const panel = ensure_armada_panel_host();
    if (!panel?.classList.contains("legacy-workbench-popover-open")) {
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
    panel.style.setProperty("--legacy-workbench-popover-left", `${Math.round(left)}px`);
    panel.style.setProperty("--legacy-workbench-popover-top", `${Math.round(top)}px`);
    panel.style.setProperty("--legacy-workbench-popover-width", `${Math.round(width)}px`);
    // Available space below the (clamped) top; the body scrolls internally if
    // its content is taller, so it can never extend past the viewport bottom.
    panel.style.setProperty(
        "--legacy-workbench-popover-max-height",
        `${Math.max(margin, Math.round(viewport_height - top - margin))}px`,
    );
    panel.style.setProperty("--legacy-workbench-popover-arrow-left", `${Math.round(arrow_left)}px`);
}

function set_workbench_popover_open(workbench_open: boolean): void {
    const panel = ensure_armada_panel_host();
    if (panel === undefined) {
        return;
    }
    const $panel = $(panel);
    $panel.toggleClass("legacy-workbench-popover-open", workbench_open);
    if (workbench_open) {
        position_workbench_popover();
        return;
    }
    panel.style.removeProperty("--legacy-workbench-popover-left");
    panel.style.removeProperty("--legacy-workbench-popover-top");
    panel.style.removeProperty("--legacy-workbench-popover-width");
    panel.style.removeProperty("--legacy-workbench-popover-max-height");
    panel.style.removeProperty("--legacy-workbench-popover-arrow-left");
}

function active_display_run(runs: ArmadaRun[]): ReturnType<typeof display_run> | undefined {
    const active_run =
        runs.find(
            (run) => run.status === "running" || run.status === "queued" || run.status === "paused",
        ) ?? runs[0];
    return active_run === undefined ? undefined : display_run(active_run);
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
    has_active_run: boolean;
}): boolean {
    if (options.context_key_value === "") {
        return false;
    }
    // The end of a run changes its worktree. A request made while a run
    // is live asks for the context too, so that an answer saying the run
    // has ended needs no second request for it.
    if (options.has_active_run) {
        return true;
    }
    if (options.live) {
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
        has_active_run: has_active_run(last_runs),
    });
}

function record_context_refresh(stream: string | undefined, topic: string | undefined): void {
    if (stream === undefined || topic === undefined) {
        return;
    }
    last_context_refresh_key = context_key(stream, topic);
    last_context_refresh_at = Date.now();
}

function remember_topic_context(topic_context: ArmadaWorkspaceContext | null | undefined): void {
    if (topic_context === undefined || topic_context === null) {
        return;
    }
    topic_context_by_context.set(
        context_key(topic_context.stream, topic_context.topic),
        topic_context,
    );
    // Keep the sidebar's workspace marks honest without a server event
    // stream: this context already says whether the channel is bound
    // and whether this topic has a worktree.
    const sub = stream_data.get_sub(topic_context.stream);
    if (sub !== undefined) {
        const has_pinned_repo =
            topic_context.pinned_repo !== undefined && topic_context.pinned_repo !== "";
        if (has_pinned_repo || topic_context.workdir !== "") {
            legacy_workspace_marks.note_pinned_repo_for_stream(sub.stream_id);
        }
        if (topic_context.workdir !== "") {
            legacy_workspace_marks.note_topic_workspace(sub.stream_id, topic_context.topic);
        }
    }
}

// The panel owns the single topic-context/runs store; the runtime menu
// (and any future consumer) reads from it and subscribes to changes
// instead of creating another fetch authority for the same endpoint.
const topic_context_listeners = new Set<() => void>();

export function on_topic_context_changed(listener: () => void): void {
    topic_context_listeners.add(listener);
}

function notify_topic_context_listeners(): void {
    for (const listener of topic_context_listeners) {
        listener();
    }
}

export function shared_topic_context(
    stream: string | undefined,
    topic: string | undefined,
): ArmadaWorkspaceContext | undefined {
    return cached_topic_context(stream, topic);
}

export function shared_runs(): ArmadaRun[] {
    return last_runs;
}

export function handle_armada_run_event(event: {
    sequence: number;
    run_id: number;
    status: string;
    result_message_id: number | null;
    stream: string;
    topic: string;
    current_activity: string;
    tool_count: number;
    plan: {step: string; status: string}[] | null;
    overview: string | null;
    has_visible_assistant_text: boolean | null;
    usage: z.infer<typeof armada_usage_schema> | null;
    duration_seconds: number | null;
    agent_thread_id?: string | null | undefined;
    agent?: legacy_run_state.ArmadaAgentThreadDelta | null | undefined;
    agents?: legacy_run_state.ArmadaAgentThread[] | null | undefined;
}): void {
    const {stream, topic} = current_context();
    if (stream !== event.stream || topic !== event.topic) {
        return;
    }
    if (!legacy_run_state.event_is_latest(event.run_id, event.sequence)) {
        return;
    }
    const run_index = last_runs.findIndex((run) => run.id === event.run_id);
    if (run_index === -1) {
        // One reconciliation fetch establishes immutable run metadata for a
        // newly observed run; subsequent progress arrives only as agent_run.
        refresh({live: true});
        return;
    }
    const run = last_runs[run_index]!;
    last_runs[run_index] = {
        ...run,
        status: event.status,
        result_message_id: event.result_message_id,
        current_activity: event.current_activity,
        tool_count: event.tool_count,
        plan: event.plan ?? run.plan,
        overview: event.overview ?? (event.plan === null ? run.overview : null),
        agents: legacy_run_state.get_run_view_by_id(event.run_id)?.agents ?? run.agents,
        usage: {
            ...run.usage,
            ...event.usage,
            duration_seconds: event.duration_seconds ?? run.usage?.duration_seconds,
        },
    };
    note_live_run_event(event.run_id);
    schedule_live_render();
}

function clear_liveness_reconciliation_timer(): void {
    if (liveness_reconciliation_timer !== undefined) {
        clearTimeout(liveness_reconciliation_timer);
        liveness_reconciliation_timer = undefined;
    }
}

function note_live_run_event(run_id: number): void {
    if (liveness_reconciliation_owner !== `run:${run_id}`) {
        return;
    }
    clear_liveness_reconciliation_timer();
    liveness_reconciliation_attempted = false;
}

export function liveness_reconciliation_owner_for_testing(options: {
    run_id?: number | undefined;
    run_status?: string | undefined;
    placeholder_message_id?: number | undefined;
}): string | undefined {
    if (
        options.run_id !== undefined &&
        (options.run_status === "running" || options.run_status === "queued")
    ) {
        return `run:${options.run_id}`;
    }
    if (options.placeholder_message_id !== undefined) {
        return `placeholder:${options.placeholder_message_id}`;
    }
    return undefined;
}

export function liveness_reconciliation_needs_timer_for_testing(options: {
    owner: string | undefined;
    attempted: boolean;
    timer_armed: boolean;
}): boolean {
    return options.owner !== undefined && !options.attempted && !options.timer_armed;
}

function visible_working_placeholder_message_id(): number | undefined {
    const rows = document.querySelectorAll<HTMLElement>(
        '.focused-message-list .message_row[data-legacy-working-placeholder="true"]',
    );
    const row = rows.item(rows.length - 1);
    const message_id = Number.parseInt(row?.dataset["messageId"] ?? "", 10);
    return Number.isInteger(message_id) ? message_id : undefined;
}

function arm_liveness_reconciliation(run: ArmadaRun | undefined): void {
    const owner = liveness_reconciliation_owner_for_testing({
        run_id: run?.id,
        run_status: run?.status,
        placeholder_message_id: visible_working_placeholder_message_id(),
    });
    // A new run or exact unbound placeholder owns a fresh one-shot budget.
    // Reset before consulting the previous owner's attempted flag or the new
    // visible chrome could remain unguarded.
    if (owner !== undefined && liveness_reconciliation_owner !== owner) {
        clear_liveness_reconciliation_timer();
        liveness_reconciliation_owner = owner;
        liveness_reconciliation_attempted = false;
    }
    if (owner === undefined) {
        clear_liveness_reconciliation_timer();
        liveness_reconciliation_owner = undefined;
        liveness_reconciliation_attempted = false;
        return;
    }
    if (
        !liveness_reconciliation_needs_timer_for_testing({
            owner,
            attempted: liveness_reconciliation_attempted,
            timer_armed: liveness_reconciliation_timer !== undefined,
        })
    ) {
        return;
    }
    liveness_reconciliation_timer = setTimeout(() => {
        liveness_reconciliation_timer = undefined;
        liveness_reconciliation_attempted = true;
        // Server truth decides whether the run is still live. This is one
        // bounded reconciliation per quiet event gap or exact unbound
        // sentinel, never a polling loop or client-authored terminal state.
        refresh({live: true});
    }, RUN_LIVENESS_RECONCILIATION_MS);
}

function schedule_live_render(): void {
    if (live_render_frame_id !== undefined) {
        return;
    }
    if (typeof requestAnimationFrame !== "function") {
        render(last_runs, last_topic_context);
        return;
    }
    live_render_frame_id = requestAnimationFrame(() => {
        live_render_frame_id = undefined;
        render(last_runs, last_topic_context);
    });
}

function merge_refresh_options(
    current: RefreshOptions | undefined,
    incoming: RefreshOptions,
): RefreshOptions {
    if (current === undefined) {
        return {...incoming};
    }
    return {
        force_context: current.force_context === true || incoming.force_context === true,
        // A normal explicit refresh must not be weakened into the cheaper
        // live-reconciliation shape when requests are coalesced.
        live: current.live === true && incoming.live === true,
    };
}

function refresh_request_is_current(options: {
    request_context_key: string;
    current_context_key: string;
    request_generation: number;
    latest_generation: number | undefined;
}): boolean {
    return (
        options.request_context_key === options.current_context_key &&
        options.request_generation === options.latest_generation
    );
}

export function reconcile_run_snapshot_for_testing(
    run: ArmadaRun,
    previous: ArmadaRun | undefined,
): ArmadaRun {
    if (legacy_run_state.snapshot_is_current(run.id, run.snapshot_sequence)) {
        return run;
    }
    const view = legacy_run_state.get_run_view_by_id(run.id);
    if (view === undefined) {
        return previous ?? run;
    }
    const base = previous ?? run;
    return {
        ...base,
        status: view.status,
        result_message_id:
            view.result_message_id !== undefined ? view.result_message_id : base.result_message_id,
        stream: view.stream ?? base.stream,
        topic: view.topic ?? base.topic,
        current_activity: view.current_activity ?? base.current_activity,
        plan: view.plan !== undefined ? view.plan : base.plan,
        overview: view.overview !== undefined ? view.overview : base.overview,
        agents: view.agents ?? base.agents,
        tool_count: view.tool_count ?? base.tool_count,
        date_created: view.date_created ?? base.date_created,
        date_updated: view.updated_at ?? base.date_updated,
        usage: {...base.usage, ...view.usage},
        live:
            view.live_updated_at === undefined
                ? base.live
                : {
                      status: view.status,
                      updated_at: view.live_updated_at,
                      operation: base.live?.operation,
                      has_visible_assistant_text: view.has_visible_assistant_text,
                  },
    };
}

function reconcile_run_snapshots(runs: ArmadaRun[]): ArmadaRun[] {
    const previous_by_id = new Map(last_runs.map((run) => [run.id, run]));
    return runs.map((run) => reconcile_run_snapshot_for_testing(run, previous_by_id.get(run.id)));
}

export function remember_saved_topic_context(raw_topic_context: unknown): void {
    // Called by the runtime menu when a model/effort save succeeds, so
    // the header pill and panel reflect the change immediately instead
    // of waiting out the next context refresh. The menu parses the save
    // response with its own (narrower) schema, so re-validate here.
    const parsed = armada_topic_context_schema.safeParse(raw_topic_context);
    if (!parsed.success) {
        return;
    }
    remember_topic_context(parsed.data);
    record_context_refresh(parsed.data.stream, parsed.data.topic);
    render();
}

export function handle_narrow_activated(): void {
    legacy_wall.handle_narrow_changed();
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

function render_state_key(render_state: object): string {
    return JSON.stringify(render_state);
}

export function topic_context_after_refresh_for_testing(options: {
    include_context: boolean;
    live: boolean;
    response_topic_context: ArmadaWorkspaceContext | null | undefined;
    last_topic_context: ArmadaWorkspaceContext | null | undefined;
    cached_topic_context?: ArmadaWorkspaceContext | null | undefined;
}): ArmadaWorkspaceContext | null | undefined {
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

let last_workspace_rail_signature = "";

function armada_rail_duration_label(duration_seconds: number): string {
    const total_seconds = Math.round(duration_seconds);
    if (total_seconds < 60) {
        return `${total_seconds}s`;
    }
    return `${Math.floor(total_seconds / 60)}m ${total_seconds % 60}s`;
}

type ArmadaRailRunEntry = {
    glyph: string;
    failed_class: string;
    label: string;
    when: string;
    has_result_message_id: boolean;
    result_message_id: number | undefined;
};

type ArmadaRailPlanStep = {
    step: string;
    status_glyph: string;
    status_class: string;
    glyph_class: string;
    is_ellipsis?: boolean;
    is_current?: boolean;
};

export function armada_rail_plan_steps_for_testing(runs: ArmadaRun[]): ArmadaRailPlanStep[] {
    // Plans are run-scoped. While a new run is active, never fall back to a
    // completed checklist from an earlier turn. Keep the rail resident from
    // the first queued event with the same quiet Working state used by chat.
    const active_source = runs.find((run) => run.status === "running" || run.status === "queued");
    if (active_source === undefined) {
        return [];
    }
    const presentation = active_plan_presentation(active_source.plan ?? []);
    if (presentation.kind === "working") {
        return [
            {
                step: $t({defaultMessage: "Working"}),
                status_glyph: "",
                status_class: "legacy-rail-plan-active",
                glyph_class: "legacy-thinking-glyph",
                is_current: true,
            },
        ];
    }
    return presentation.rows.map((row) => {
        if (row.kind === "ellipsis") {
            return {
                step: "…",
                status_glyph: "",
                status_class: "legacy-rail-plan-ellipsis",
                glyph_class: "",
                is_ellipsis: true,
            };
        }
        if (row.is_current) {
            return {
                step: row.step,
                status_glyph: row.status === "completed" ? "✓" : "",
                status_class: "legacy-rail-plan-active",
                glyph_class: row.status === "completed" ? "" : "legacy-thinking-glyph",
                is_current: true,
            };
        }
        if (row.status === "completed") {
            return {
                step: row.step,
                status_glyph: "✓",
                status_class: "legacy-rail-plan-done",
                glyph_class: "",
            };
        }
        return {
            step: row.step,
            status_glyph: "○",
            status_class: "legacy-rail-plan-pending",
            glyph_class: "",
        };
    });
}

function armada_rail_run_entries(runs: ArmadaRun[]): ArmadaRailRunEntry[] {
    const settled = runs.filter((run) => ["done", "failed", "cancelled"].includes(run.status));
    return settled.slice(0, 3).map((run) => {
        const failed = run.status !== "done";
        const parts: string[] = [];
        const duration = run.usage?.duration_seconds;
        if (typeof duration === "number" && duration > 0) {
            parts.push(armada_rail_duration_label(duration));
        }
        const tool_count = run.tool_count ?? 0;
        if (tool_count > 0) {
            parts.push(
                $t(
                    {defaultMessage: "{tool_count, plural, one {# tool} other {# tools}}"},
                    {tool_count},
                ),
            );
        }
        const when =
            typeof run.date_created === "number"
                ? timerender.relative_time_string_from_date(new Date(run.date_created * 1000), true)
                : "";
        const result_message_id = run.result_message_id ?? undefined;
        return {
            glyph: failed ? "✗" : "✓",
            failed_class: failed ? "legacy-rail-run-failed" : "",
            label: first_nonempty(parts.join(" · "), run.status),
            when,
            has_result_message_id: result_message_id !== undefined,
            result_message_id,
        };
    });
}

function render_workspace_rail(
    topic_context: ArmadaWorkspaceContext | undefined,
    runs: ArmadaRun[] = last_runs,
): void {
    const $rail = $("#legacy-workspace-rail");
    if ($rail.length === 0) {
        return;
    }
    const plan_steps = armada_rail_plan_steps_for_testing(runs);
    const has_workspace = topic_context !== undefined && topic_context.workdir !== "";
    if (!has_workspace && plan_steps.length === 0) {
        if (last_workspace_rail_signature !== "") {
            last_workspace_rail_signature = "";
            $rail.attr("hidden", "hidden");
            $rail.empty();
        }
        return;
    }
    const git = topic_context?.git;
    const repo_label = first_nonempty(
        topic_context?.repo_label?.trim(),
        topic_context?.repo_title?.trim(),
        topic_context?.github_repo.trim(),
        topic_context?.worktree_label?.trim(),
        topic_context?.topic,
    );
    const branch_label = first_nonempty(
        git?.branch?.trim(),
        topic_context?.branch?.trim(),
        topic_context?.topic,
    );
    const dirty = git?.dirty === true;
    const changed_count = git?.changed_count ?? 0;
    const run_entries = armada_rail_run_entries(runs);
    const active_runs = runs.filter((run) => run.status === "running" || run.status === "queued");
    const active_run = active_runs[0];
    const active_meta_parts: string[] = [];
    if (active_run !== undefined) {
        const duration = active_run.usage?.duration_seconds;
        if (typeof duration === "number" && duration > 0) {
            active_meta_parts.push(armada_rail_duration_label(duration));
        } else if (typeof active_run.date_created === "number") {
            active_meta_parts.push(
                armada_rail_duration_label(Date.now() / 1000 - active_run.date_created),
            );
        }
        if ((active_run.tool_count ?? 0) > 0) {
            active_meta_parts.push(
                $t(
                    {defaultMessage: "{tool_count, plural, one {# tool} other {# tools}}"},
                    {tool_count: active_run.tool_count ?? 0},
                ),
            );
        }
        const queue_depth = armada_rail_queue_depth_for_testing(active_runs);
        if (queue_depth > 0) {
            active_meta_parts.push(
                $t(
                    {defaultMessage: "{queue_depth, plural, one {# waiting} other {# waiting}}"},
                    {queue_depth},
                ),
            );
        }
    }
    const args = {
        has_workspace,
        repo_label: armada_rail_repo_display_label(repo_label),
        repo_title: repo_label,
        branch_label,
        head_short: git?.head_short?.trim() ?? "",
        status_class: dirty ? "dirty" : "clean",
        status_label: dirty
            ? $t({defaultMessage: "{changed_count} changed"}, {changed_count})
            : $t({defaultMessage: "clean"}),
        runs: run_entries,
        has_runs: run_entries.length > 0,
        plan_steps,
        has_plan: plan_steps.length > 0,
        plan_meta: active_meta_parts.join(" · "),
        has_plan_meta: active_meta_parts.length > 0,
        show_workspace_line: has_workspace,
    };
    const signature = JSON.stringify(args);
    if (signature === last_workspace_rail_signature) {
        return;
    }
    last_workspace_rail_signature = signature;
    const rail = $rail[0];
    if (!(rail instanceof HTMLElement)) {
        return;
    }
    const template = document.createElement("template");
    template.innerHTML = render_legacy_workspace_rail(args);
    $rail.empty();
    rail.append(template.content);
    $rail.removeAttr("hidden");
}

// A clone URL is the repository's address, not its name: the rail says
// "pallets/click" and keeps the full URL for the tooltip.
export function armada_rail_repo_display_label(label: string): string {
    const match = /^(?:[a-z][a-z\d+.-]*:\/\/|git@)[^/:]+[/:](.+?)(?:\.git)?\/?$/i.exec(
        label.trim(),
    );
    return match?.[1] ?? label;
}

export function armada_rail_queue_depth_for_testing(runs: {status: string}[]): number {
    return Math.max(
        0,
        runs.filter((run) => run.status === "running" || run.status === "queued").length - 1,
    );
}

export function visible_panel_error_for_testing(
    error: string,
    dismissed_error: string | undefined,
): string {
    return error !== "" && dismissed_error !== error ? error : "";
}

function render(
    runs: ArmadaRun[] = last_runs,
    topic_context: ArmadaWorkspaceContext | null | undefined = last_topic_context,
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
    arm_liveness_reconciliation(active_run);
    if (has_topic && active_run === undefined && contexts_with_active_run.has(next_context_key)) {
        // The run just ended; make sure the "agent is working…" typing
        // line dies with it even if the worker's typing-stop event was
        // lost — the client-side expiry would otherwise show 45s of
        // false status. Clearing only on the active→idle transition
        // avoids fighting legitimate typing signals from turns whose
        // run rows are not (yet) visible in the runs payload.
        const typing_stream_id = stream_data.get_stream_id(stream);
        if (typing_stream_id !== undefined) {
            typing_events.clear_armada_typists_for_topic(typing_stream_id, topic);
        }
    }
    if (active_run === undefined) {
        contexts_with_active_run.delete(next_context_key);
    } else {
        contexts_with_active_run.add(next_context_key);
    }
    const manual_workbench_open = work_panel_open_by_context.get(next_context_key) ?? false;
    const viewport_allows_workbench = current_viewport_allows_workbench();
    const visible_error = visible_panel_error_for_testing(
        last_error,
        dismissed_error_by_context.get(next_context_key),
    );
    const workbench_open = workbench_open_for_testing({
        context_key_value: next_context_key,
        manually_open: viewport_allows_workbench && manual_workbench_open,
    });
    const panel_open = workbench_open;
    legacy_run_state.remember_runs(runs);
    if (has_topic) {
        legacy_run_state.set_active_run_result_messages(runs, {
            force_reconcile: active_run === undefined,
            stream,
            topic,
        });
    }
    legacy_run_state.set_active_run_topic(
        active_run === undefined ? undefined : stream,
        active_run === undefined ? undefined : topic,
    );
    const panel_status = active_run?.status ?? "idle";
    const display_context = has_topic ? display_topic_context(source_topic_context) : undefined;
    const auxiliary_render_key = JSON.stringify({
        stream,
        topic,
        topic_context: display_context,
        runs: runs.map((run) => ({
            id: run.id,
            status: run.status,
            plan: run.plan,
            overview: run.overview,
            tool_count: run.tool_count,
            result_message_id: run.result_message_id,
            usage: run.usage,
        })),
    });
    if (last_auxiliary_render_key !== auxiliary_render_key) {
        render_topic_header_controls({
            stream,
            topic,
            topic_context: display_context,
            active_run_status: active_run?.status,
        });
        render_workspace_rail(display_context, runs);
        last_auxiliary_render_key = auxiliary_render_key;
    }
    legacy_workspace.refresh_if_open_on_settle(active_run !== undefined);
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
        show_runtime_trigger: panel_header_state.show_runtime_trigger,
        show_panel_header: panel_header_state.show_panel_header,
        panel_header_quiet:
            active_run !== undefined && !active_run.show_status && active_run.has_actions,
        active_run_show_status: active_run?.show_status === true,
        active_run_is_active: active_run?.is_active === true,
        panel_has_active_status: panel_has_active_status_for_testing(active_run?.status),
        topic_context: display_context,
        active_run: active_run_for_template,
        error: visible_error,
        status_label: panel_status,
        status_class: status_class(panel_status),
    };
    const next_render_state_key = render_state_key(render_state);
    const panel = ensure_armada_panel_host();
    if (panel === undefined) {
        return;
    }
    const $panel = $(panel);
    if (last_render_state_key !== next_render_state_key) {
        // The html() swap below replaces every node in the panel. Preserve
        // focus for the workbench controls across live renders.
        const focused = document.activeElement;
        const focused_id =
            focused instanceof HTMLElement && panel.contains(focused) ? focused.id : "";
        $panel.html(render_legacy_panel(render_state));
        $panel.toggleClass("legacy-panel-collapsed", !panel_open && visible_error === "");
        set_workbench_popover_open(workbench_open);
        if (focused_id !== "") {
            const refocused = panel.querySelector(`#${focused_id}`);
            if (refocused instanceof HTMLElement) {
                refocused.focus();
            }
        }
        legacy_run_state.notify_liveness_markers_changed();
        last_render_state_key = next_render_state_key;
    }
    $panel.toggleClass("legacy-panel-collapsed", !panel_open && visible_error === "");
    set_workbench_popover_open(workbench_open);
    notify_topic_context_listeners();
}

export function refresh_paused_for_testing(options: {
    hidden: boolean;
    force_context: boolean;
}): boolean {
    // Skip reconciliation on a hidden tab; an explicit forced refresh still
    // runs, and a visibilitychange listener catches up on return.
    return options.hidden && !options.force_context;
}

export function set_refresh_state_for_testing(options: {runs: ArmadaRun[]; error?: string}): void {
    last_runs = options.runs;
    last_error = options.error ?? "";
}

export function refresh_state_for_testing(): {run_ids: number[]; error: string} {
    return {run_ids: last_runs.map((run) => run.id), error: last_error};
}

export function refresh(options: RefreshOptions = {}): void {
    const live = options.live === true;
    if (
        typeof document !== "undefined" &&
        refresh_paused_for_testing({
            hidden: document.hidden,
            force_context: options.force_context === true,
        })
    ) {
        return;
    }
    const request_context_key = current_context_key();
    const existing_request = refresh_requests_by_context.get(request_context_key);
    if (existing_request !== undefined) {
        existing_request.pending = merge_refresh_options(existing_request.pending, options);
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
    next_refresh_generation += 1;
    const request_generation = next_refresh_generation;
    refresh_requests_by_context.set(request_context_key, {
        generation: request_generation,
        pending: undefined,
    });
    const request_is_current = (): boolean =>
        refresh_request_is_current({
            request_context_key,
            current_context_key: current_context_key(),
            request_generation,
            latest_generation: refresh_requests_by_context.get(request_context_key)?.generation,
        });
    void channel.get({
        url: "/json/armada/runs",
        data,
        success(raw_data) {
            if (!request_is_current()) {
                return;
            }
            if (!is_armada_runs_response(raw_data)) {
                last_error = $t({defaultMessage: "The server returned an invalid runs payload."});
                render();
                return;
            }
            const data = {
                ...raw_data,
                armada_runs: reconcile_run_snapshots(raw_data.armada_runs),
            };
            last_error = "";
            dismissed_error_by_context.delete(request_context_key);
            const had_active_run = has_active_run(last_runs);
            const has_active_run_after_refresh = has_active_run(data.armada_runs);
            const previous_topic_context = display_source_topic_context(stream, topic);
            const topic_context = topic_context_after_refresh_for_testing({
                include_context,
                live,
                response_topic_context: data.armada_topic_context,
                last_topic_context: previous_topic_context,
                cached_topic_context: cached_topic_context(stream, topic),
            });
            remember_topic_context(topic_context);
            if (data.armada_topic_context !== undefined && data.armada_topic_context !== null) {
                record_context_refresh(stream, topic);
            }
            render(data.armada_runs, topic_context);
            if (had_active_run && !has_active_run_after_refresh && !include_context) {
                refresh({force_context: true});
            }
        },
        error(xhr) {
            if (!request_is_current()) {
                return;
            }
            if (xhr.readyState === 0) {
                return;
            }
            last_error =
                xhr.status === 429
                    ? $t({
                          defaultMessage: "Run status is rate limited; waiting for the next event.",
                      })
                    : $t({defaultMessage: "Run status is unavailable."});
            blueslip.warn(`Failed to fetch agent runs: ${xhr.responseText}`);
            render();
        },
        complete() {
            const request = refresh_requests_by_context.get(request_context_key);
            if (request?.generation !== request_generation) {
                return;
            }
            refresh_requests_by_context.delete(request_context_key);
            if (request.pending !== undefined && current_context_key() === request_context_key) {
                refresh(request.pending);
            }
        },
    });
}

function post_run_action(run_id: string, action: string): void {
    void channel.post({
        url: `/json/armada/runs/${run_id}/${action}`,
        success() {
            refresh({force_context: true});
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                blueslip.warn(`Failed to ${action} agent run: ${xhr.responseText}`);
            }
        },
    });
}

export function optimistic_topic_context_for_testing(
    topic_context: ArmadaWorkspaceContext,
    options: {model?: string; reasoning_effort?: string},
): ArmadaWorkspaceContext {
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

export function initialize(): void {
    if (initialized) {
        refresh();
        return;
    }
    initialized = true;
    // A freshly built workspace-pane shell needs its header cluster
    // and rail filled right away, even when the topic/run signatures are
    // unchanged from the DOM shell that Zulip just replaced.
    $(document).on("armada:pane-shell-rendered.armada_panel", () => {
        last_auxiliary_render_key = "";
        last_workspace_rail_signature = "";
        schedule_live_render();
    });
    $(document).on(`${legacy_run_state.ARMADA_LIVENESS_MARKERS_CHANGED_EVENT}.armada_panel`, () => {
        arm_liveness_reconciliation(active_display_run(last_runs));
    });
    $("body").on("click.armada_panel", "#legacy-panel-refresh", () => {
        refresh({force_context: true});
    });
    $("body").on("click.armada_panel", ".legacy-panel-error-dismiss", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const key = current_context_key();
        if (key !== "" && last_error !== "") {
            dismissed_error_by_context.set(key, last_error);
        }
        schedule_live_render();
    });
    $("body").on("click.armada_panel", ".legacy-panel-close-action", () => {
        close_work_details();
    });
    $("body").on("click.armada_panel", ".legacy-rail-workspace-line", (e) => {
        e.preventDefault();
        e.stopPropagation();
        legacy_workspace.set_tab("files");
    });
    $("body").on("click.armada_panel", ".legacy-rail-run", (e) => {
        const message_id = Number.parseInt($(e.currentTarget).attr("data-message-id") ?? "", 10);
        if (!Number.isInteger(message_id)) {
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        message_lists.current?.select_id(message_id, {then_scroll: true});
    });
    $("body").on("click.armada_panel", ".legacy-panel-open-action", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const requested_view = $(e.currentTarget).attr("data-legacy-workbench-view");
        open_work_details(
            requested_view === "diff" || requested_view === "history" ? requested_view : undefined,
            e.currentTarget instanceof HTMLElement ? e.currentTarget : undefined,
        );
    });
    $(document).on("keydown.armada_panel", (e) => {
        const workbench = $(e.target).closest(".legacy-workbench")[0];
        if (!(workbench instanceof HTMLElement)) {
            return;
        }
        if (e.key === "Escape") {
            const open_actions = $(e.target).closest<HTMLDetailsElement>("details[open]")[0];
            if (open_actions instanceof HTMLDetailsElement) {
                open_actions.open = false;
                open_actions.querySelector<HTMLElement>("summary")?.focus();
                e.preventDefault();
                return;
            }
            close_work_details();
            e.preventDefault();
            return;
        }
        const tab = $(e.target).closest<HTMLButtonElement>('[role="tab"]')[0];
        if (
            tab instanceof HTMLButtonElement &&
            ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
        ) {
            const tabs = [
                ...workbench.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)'),
            ];
            const current_index = tabs.indexOf(tab);
            const next_index =
                e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? tabs.length - 1
                      : (current_index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
                        tabs.length;
            const next_tab = tabs[next_index];
            const view = next_tab?.dataset["legacyWorkbenchView"];
            if (view === "diff" || view === "history") {
                select_workbench_view(view);
                document
                    .querySelector<HTMLElement>(
                        `#legacy-panel .legacy-workbench-tab[data-legacy-workbench-view="${view}"]`,
                    )
                    ?.focus({preventScroll: true});
            }
            e.preventDefault();
            return;
        }
        if (e.key !== "Tab") {
            return;
        }
        const focusable = workbench_focusable_elements(workbench);
        const first = focusable[0];
        const last = focusable.at(-1);
        if (first === undefined || last === undefined) {
            e.preventDefault();
            workbench.focus({preventScroll: true});
        } else if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus({preventScroll: true});
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus({preventScroll: true});
        }
    });
    $(document).on("click.armada_panel", (e) => {
        // Ignore clicks inside the workbench itself.  Using a target check here
        // instead of stopPropagation on #legacy-panel means other delegated
        // document handlers (e.g. Zulip's own popovers closing on outside
        // click) still see clicks that land inside the agent panel.
        if ($(e.target).closest("#legacy-panel").length > 0) {
            return;
        }
        close_work_details({restore_focus: false});
    });
    $("body").on("click.armada_panel", ".legacy-workbench-tab", function () {
        const view = $(this).attr("data-legacy-workbench-view");
        if (view !== "diff" && view !== "history") {
            return;
        }
        select_workbench_view(view);
    });
    $("body").on("click.armada_panel", ".legacy-run-action", function () {
        const $button = $(this);
        const run_id = $button.attr("data-run-id");
        const action = $button.attr("data-action");
        if (run_id === undefined || action === undefined) {
            return;
        }
        post_run_action(run_id, action);
    });
    $(window).on("resize.armada_panel", () => {
        // Coalesce the resize-event burst into one render per frame.
        if (resize_render_frame_id !== undefined) {
            return;
        }
        resize_render_frame_id = requestAnimationFrame(() => {
            resize_render_frame_id = undefined;
            render();
        });
    });
    $(window).on("scroll.armada_panel", () => {
        position_workbench_popover();
    });
    $(document).on("visibilitychange.armada_panel", () => {
        if (!document.hidden) {
            refresh({force_context: true});
        }
    });
    // No request here: nothing is on screen yet that shows a run. The first
    // message view asks for its own (handle_narrow_activated), so a page
    // that opens on a topic makes one request, not two, and one that opens
    // on Inbox or Recent makes none.
    schedule_live_render();
}

export function stop(): void {
    $(window).off("resize.armada_panel");
    $(window).off("scroll.armada_panel");
    $(document).off("click.armada_panel");
    $(document).off("visibilitychange.armada_panel");
    $(document).off("keydown.armada_panel");
    $(document).off("armada:pane-shell-rendered.armada_panel");
    $(document).off(`${legacy_run_state.ARMADA_LIVENESS_MARKERS_CHANGED_EVENT}.armada_panel`);
    $("body").off(".armada_panel");
    clear_liveness_reconciliation_timer();
    liveness_reconciliation_owner = undefined;
    liveness_reconciliation_attempted = false;
    if (live_render_frame_id !== undefined) {
        cancelAnimationFrame(live_render_frame_id);
        live_render_frame_id = undefined;
    }
    if (resize_render_frame_id !== undefined) {
        cancelAnimationFrame(resize_render_frame_id);
        resize_render_frame_id = undefined;
    }
    refresh_requests_by_context.clear();
    dismissed_error_by_context.clear();
    initialized = false;
}
