// The Wall: persistent monitor blocks in the right sidebar.
//
// Each block is owned by a standing subroutine (its own Codex thread on
// the server) whose recipe re-runs at a low cadence with no model call.
// This module only *displays*: it polls a compact endpoint, re-renders
// blocks whose version changed, and keeps the whole thing cheap — no
// polling when the tab is hidden or the narrow has no topic, and the
// poll interval stretches to match the slowest wall.

import $ from "jquery";
import * as z from "zod/mini";

import render_pear_wall from "../templates/pear_wall.hbs";

import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import type {PearArtifactDocument, PearArtifactModule} from "./pear_artifact.ts";
import {send_pear_artifact_action} from "./pear_artifact_actions.ts";
import {mount_pear_artifact_lifecycle} from "./pear_artifact_lifecycle.ts";

const monitor_schema = z.object({
    id: z.number(),
    title: z.string(),
    ask: z.string(),
    status: z.string(),
    version: z.number(),
    cadence_seconds: z.number(),
    artifact_spec: z.record(z.string(), z.unknown()),
    data: z.record(z.string(), z.unknown()),
    last_error: z.string(),
    position: z.number(),
});
type PearMonitorPayload = z.infer<typeof monitor_schema>;

const monitors_response_schema = z.object({
    pear_monitors: z.array(monitor_schema),
});

const POLL_MIN_MS = 5000;
const POLL_MAX_MS = 30000;
const CADENCE_PRESETS = [5, 20, 60, 300, 3600, 86400];

let monitors: PearMonitorPayload[] = [];
let poll_timer: ReturnType<typeof setTimeout> | undefined;
let backoff_ms = 0;
let last_render_signature = "";
let last_context_key = "";
const collapsed_ids = new Set<number>();
const seen_versions = new Map<number, number>();
let artifact_module: PearArtifactModule | undefined;
let artifact_module_promise: Promise<PearArtifactModule> | undefined;
const last_valid_artifacts = new Map<number, PearArtifactDocument>();
const rendered_versions = new Map<number, number>();

function wall_context(): {stream: string; topic: string} | undefined {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined) {
        return undefined;
    }
    return {stream, topic};
}

function cadence_label(seconds: number): string {
    if (seconds < 60) {
        return `${seconds}s`;
    }
    if (seconds < 3600) {
        return `${Math.round(seconds / 60)}m`;
    }
    if (seconds < 86400) {
        return `${Math.round(seconds / 3600)}h`;
    }
    return `${Math.round(seconds / 86400)}d`;
}

function render_context(): Record<string, unknown> {
    const blocks = monitors.map((monitor) => ({
        id: monitor.id,
        title: monitor.title || monitor.ask.slice(0, 24),
        ask: monitor.ask,
        status: monitor.status,
        collapsed: collapsed_ids.has(monitor.id),
        collapsed_class: collapsed_ids.has(monitor.id) ? "pear-wall-collapsed" : "",
        aria_expanded: collapsed_ids.has(monitor.id) ? "false" : "true",
        collapse_glyph: collapsed_ids.has(monitor.id) ? "▸" : "▾",
        is_provisioning: monitor.status === "provisioning",
        is_error: monitor.status === "error",
        is_live: monitor.status === "live",
        is_paused: monitor.status === "paused",
        show_cadence: monitor.status === "live" || monitor.status === "paused",
        cadence_label: cadence_label(monitor.cadence_seconds),
        last_error: monitor.last_error,
        last_error_short:
            monitor.last_error.length > 60
                ? monitor.last_error.slice(0, 60) + "…"
                : monitor.last_error,
    }));
    return {has_blocks: blocks.length > 0, blocks};
}

function pulse_changed_blocks($wall: JQuery): void {
    for (const monitor of monitors) {
        const seen = seen_versions.get(monitor.id);
        if (seen !== undefined && seen !== monitor.version) {
            const $dot = $wall
                .find(`.pear-wall-block[data-monitor-id="${monitor.id}"] .pear-wall-dot`)
                .removeClass("pear-wall-dot-pulse");
            // Force a restart of the animation for back-to-back samples.
            void $dot.get(0)?.offsetWidth;
            $dot.addClass("pear-wall-dot-pulse");
        }
        seen_versions.set(monitor.id, monitor.version);
    }
    for (const id of seen_versions.keys()) {
        if (!monitors.some((monitor) => monitor.id === id)) {
            seen_versions.delete(id);
        }
    }
}

async function settle_artifact_module(): Promise<void> {
    try {
        const pending = artifact_module_promise;
        if (pending === undefined) {
            return;
        }
        artifact_module = await pending;
        render_artifacts($("#pear-wall"));
    } catch {
        for (const monitor of monitors) {
            if (monitor.status !== "live" && monitor.status !== "paused") {
                continue;
            }
            const $host = $("#pear-wall").find(
                `.pear-wall-block[data-monitor-id="${monitor.id}"] [data-pear-artifact-host]`,
            );
            mount_pear_artifact_lifecycle($host, "wall", "failed", "artifact renderer unavailable");
        }
    }
}

function mount_monitor_artifact($host: JQuery, document_value: PearArtifactDocument): void {
    artifact_module?.mount_pear_artifact($host, document_value, {
        density: "wall",
        onAction(action, button) {
            const target = wall_context();
            if (target === undefined || button.disabled) {
                return;
            }
            const label = button.textContent ?? action.label;
            button.disabled = true;
            button.textContent = $t({defaultMessage: "asking…"});
            send_pear_artifact_action(target, action.prompt, {
                success() {
                    button.textContent = $t({defaultMessage: "asked ✓"});
                },
                error() {
                    button.disabled = false;
                    button.textContent = label;
                },
            });
        },
    });
}

function render_artifacts($wall: JQuery): void {
    const renderable = monitors.filter(
        (monitor) => monitor.status === "live" || monitor.status === "paused",
    );
    if (renderable.length === 0) {
        return;
    }
    if (artifact_module === undefined) {
        for (const monitor of renderable) {
            const $host = $wall.find(
                `.pear-wall-block[data-monitor-id="${monitor.id}"] [data-pear-artifact-host]`,
            );
            if ($host.children().length === 0) {
                mount_pear_artifact_lifecycle($host, "wall", "preparing", "preparing artifact…");
            }
        }
        if (artifact_module_promise === undefined) {
            artifact_module_promise = import("./pear_artifact.ts");
            void settle_artifact_module();
        }
        return;
    }

    for (const monitor of renderable) {
        const $host = $wall.find(
            `.pear-wall-block[data-monitor-id="${monitor.id}"] [data-pear-artifact-host]`,
        );
        const base_data = monitor.artifact_spec["data"];
        const raw_document = {
            ...monitor.artifact_spec,
            data: {
                ...(typeof base_data === "object" && base_data !== null && !Array.isArray(base_data)
                    ? base_data
                    : {}),
                ...monitor.data,
            },
        };
        const parsed = artifact_module.parse_pear_artifact(JSON.stringify(raw_document));
        if (parsed === undefined) {
            const last_valid = last_valid_artifacts.get(monitor.id);
            if (last_valid !== undefined && $host.children(".pear-artifact").length === 0) {
                mount_monitor_artifact($host, last_valid);
            }
            const existing = $host.children(".pear-artifact")[0];
            if (last_valid !== undefined && existing !== undefined) {
                existing.dataset["lifecycle"] = "stale";
                existing.removeAttribute("aria-busy");
                existing.setAttribute("aria-label", "Showing the last valid artifact update");
            } else {
                mount_pear_artifact_lifecycle($host, "wall", "failed", "artifact update rejected");
            }
            continue;
        }
        last_valid_artifacts.set(monitor.id, parsed);
        if (
            rendered_versions.get(monitor.id) === monitor.version &&
            $host.children('.pear-artifact[data-lifecycle="ready"]').length > 0
        ) {
            continue;
        }
        const existing = $host.children(".pear-artifact")[0];
        existing?.setAttribute("data-lifecycle", "updating");
        existing?.setAttribute("aria-busy", "true");
        mount_monitor_artifact($host, parsed);
        $host.attr("data-pear-artifact-version", String(monitor.version));
        rendered_versions.set(monitor.id, monitor.version);
    }

    for (const monitor_id of last_valid_artifacts.keys()) {
        if (!monitors.some((monitor) => monitor.id === monitor_id)) {
            last_valid_artifacts.delete(monitor_id);
            rendered_versions.delete(monitor_id);
        }
    }
}

function render(): void {
    const $wall = $("#pear-wall");
    if ($wall.length === 0) {
        return;
    }
    const context = wall_context();
    if (context === undefined) {
        $wall.attr("hidden", "hidden");
        return;
    }
    $wall.removeAttr("hidden");
    const template_context = render_context();
    const signature = JSON.stringify(template_context);
    if (signature === last_render_signature) {
        render_artifacts($wall);
        pulse_changed_blocks($wall);
        return;
    }
    last_render_signature = signature;

    const $input = $wall.find<HTMLInputElement>(".pear-wall-input");
    const input_value = $input.val() ?? "";
    const input_focused = $input.is(":focus");

    $wall.html(render_pear_wall(template_context));

    render_artifacts($wall);

    const $new_input = $wall.find<HTMLInputElement>(".pear-wall-input");
    $new_input.val(input_value);
    if (input_focused) {
        $new_input.trigger("focus");
    }
    pulse_changed_blocks($wall);
}

function poll_delay_ms(): number {
    if (backoff_ms > 0) {
        return backoff_ms;
    }
    const live = monitors.filter((monitor) => monitor.status === "live");
    const provisioning = monitors.some((monitor) => monitor.status === "provisioning");
    if (provisioning) {
        return POLL_MIN_MS;
    }
    if (live.length === 0) {
        return POLL_MAX_MS;
    }
    const fastest = Math.min(...live.map((monitor) => monitor.cadence_seconds)) * 1000;
    return Math.max(POLL_MIN_MS, Math.min(POLL_MAX_MS, fastest));
}

function schedule_poll(delay_ms = poll_delay_ms()): void {
    if (poll_timer !== undefined) {
        clearTimeout(poll_timer);
    }
    poll_timer = setTimeout(refresh, delay_ms);
}

export function refresh(): void {
    if (document.hidden) {
        schedule_poll(POLL_MAX_MS);
        return;
    }
    const context = wall_context();
    const context_key = context === undefined ? "" : `${context.stream}>${context.topic}`;
    if (context_key !== last_context_key) {
        last_context_key = context_key;
        monitors = [];
        last_valid_artifacts.clear();
        rendered_versions.clear();
        last_render_signature = "";
        render();
    }
    if (context === undefined) {
        schedule_poll(POLL_MAX_MS);
        return;
    }
    void channel.get({
        url: "/json/pear/monitors",
        data: {stream: context.stream, topic: context.topic},
        success(raw_data) {
            backoff_ms = 0;
            const parsed = monitors_response_schema.safeParse(raw_data);
            if (parsed.success) {
                monitors = parsed.data.pear_monitors;
                render();
            }
        },
        error(xhr) {
            backoff_ms = xhr.status === 429 ? Math.max(backoff_ms * 2, 15000) : 0;
        },
        complete() {
            schedule_poll();
        },
    });
}

function monitor_action(
    monitor_id: number,
    action: string,
    extra: Record<string, string> = {},
): void {
    void channel.patch({
        url: `/json/pear/monitors/${monitor_id}`,
        data: {action, ...extra},
        success() {
            refresh();
        },
    });
}

function create_monitor(ask: string): void {
    const context = wall_context();
    if (context === undefined) {
        return;
    }
    void channel.post({
        url: "/json/pear/monitors",
        data: {stream: context.stream, topic: context.topic, request: ask},
        success() {
            refresh();
        },
        error(xhr) {
            const parsed = z.object({msg: z.string()}).safeParse(xhr.responseJSON ?? {});
            const $input = $("#pear-wall .pear-wall-input");
            $input.attr(
                "placeholder",
                parsed.success ? parsed.data.msg : $t({defaultMessage: "could not create monitor"}),
            );
            setTimeout(() => {
                $input.attr("placeholder", $t({defaultMessage: "watch something…"}));
            }, 4000);
        },
    });
}

export function initialize(): void {
    const $wall = $("#pear-wall");

    $wall.on("keydown", ".pear-wall-input", (event) => {
        if (event.key !== "Enter") {
            return;
        }
        const $input = $(event.currentTarget);
        const ask = String($input.val() ?? "").trim();
        if (ask === "") {
            return;
        }
        $input.val("");
        create_monitor(ask);
    });

    $wall.on("click", "[data-wall-action]", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const $button = $(event.currentTarget);
        const action = $button.attr("data-wall-action");
        const monitor_id = Number(
            $button.closest(".pear-wall-block").attr("data-monitor-id") ?? "0",
        );
        const monitor = monitors.find((entry) => entry.id === monitor_id);
        if (action === "collapse") {
            if (collapsed_ids.has(monitor_id)) {
                collapsed_ids.delete(monitor_id);
            } else {
                collapsed_ids.add(monitor_id);
            }
            last_render_signature = "";
            render();
            return;
        }
        if (monitor === undefined) {
            return;
        }
        switch (action) {
            case "pause":
                monitor_action(monitor_id, "pause");
                break;
            case "resume":
                monitor_action(monitor_id, "resume");
                break;
            case "refresh":
                monitor_action(monitor_id, "refresh_now");
                break;
            case "rebuild":
                monitor_action(monitor_id, "rebuild");
                break;
            case "cadence": {
                // Click cycles through the presets: 5s → 20s → 1m → 5m → 1h → 1d.
                const current_index = CADENCE_PRESETS.indexOf(monitor.cadence_seconds);
                const next =
                    CADENCE_PRESETS[(current_index + 1) % CADENCE_PRESETS.length] ??
                    CADENCE_PRESETS[0]!;
                monitor_action(monitor_id, "cadence", {cadence_seconds: String(next)});
                break;
            }
            case "delete": {
                // Two-step delete: first click arms, second within 2s fires.
                if (!$button.hasClass("pear-wall-armed")) {
                    $button.addClass("pear-wall-armed").text($t({defaultMessage: "sure?"}));
                    setTimeout(() => {
                        $button.removeClass("pear-wall-armed").text("✕");
                    }, 2000);
                    return;
                }
                void channel.del({
                    url: `/json/pear/monitors/${monitor_id}`,
                    success() {
                        refresh();
                    },
                });
                break;
            }
            default:
                break;
        }
    });

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            refresh();
        }
    });
    // Zulip narrows without firing hashchange on a fresh load, and the
    // wall must never lag a topic switch by a poll interval — so watch
    // the narrow with a string compare each second; the network is only
    // touched when the context actually changed.
    window.addEventListener("hashchange", () => {
        refresh();
    });
    setInterval(() => {
        if (document.hidden) {
            return;
        }
        const context = wall_context();
        const context_key = context === undefined ? "" : `${context.stream}>${context.topic}`;
        if (context_key !== last_context_key) {
            refresh();
        }
    }, 1000);

    refresh();
}
