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

const monitor_schema = z.object({
    id: z.number(),
    title: z.string(),
    ask: z.string(),
    status: z.string(),
    version: z.number(),
    cadence_seconds: z.number(),
    block_spec: z.object({
        components: z.optional(
            z.array(
                z.object({
                    type: z.string(),
                    props: z.record(z.string(), z.unknown()),
                }),
            ),
        ),
    }),
    props: z.record(z.string(), z.unknown()),
    history: z.array(z.number()),
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
let clock_timer: ReturnType<typeof setInterval> | undefined;
let backoff_ms = 0;
let last_render_signature = "";
let last_context_key = "";
const collapsed_ids = new Set<number>();
const seen_versions = new Map<number, number>();

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

function prop_value(monitor: PearMonitorPayload, index: number, prop: string): unknown {
    const live = monitor.props[`${index}.${prop}`];
    if (live !== undefined && live !== null) {
        return live;
    }
    return monitor.block_spec.components?.[index]?.props[prop];
}

function as_number(value: unknown): number | undefined {
    if (typeof value === "number" && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
        return Number(value);
    }
    return undefined;
}

function as_text(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
    }
    return "";
}

function format_number(value: number): string {
    if (Number.isInteger(value)) {
        return String(value);
    }
    return value >= 100 ? value.toFixed(1) : value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

const DAY_HEADERS = ["m", "t", "w", "t", "f", "s", "s"];

function calendar_cells(highlights: Set<string>): {
    month_label: string;
    cells: {day: number; in_month: boolean; highlight: boolean; today: boolean}[];
} {
    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth();
    const first = new Date(year, month, 1);
    // Monday-first grid.
    const lead = (first.getDay() + 6) % 7;
    const start = new Date(year, month, 1 - lead);
    const cells = [];
    const iso = (date: Date): string =>
        `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const today_iso = iso(now);
    for (let index = 0; index < 42; index += 1) {
        const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
        cells.push({
            day: date.getDate(),
            in_month: date.getMonth() === month,
            highlight: highlights.has(iso(date)),
            today: iso(date) === today_iso,
        });
    }
    const month_label = now.toLocaleDateString(undefined, {month: "short"}).toLowerCase();
    return {month_label, cells};
}

function component_context(
    monitor: PearMonitorPayload,
    component: {type: string; props: Record<string, unknown>},
    index: number,
): Record<string, unknown> {
    const type = component.type;
    const label = as_text(prop_value(monitor, index, "label"));
    const context: Record<string, unknown> = {[`is_${type}`]: true, label};
    switch (type) {
    case "meter": {
        const value = as_number(prop_value(monitor, index, "value")) ?? 0;
        const total = as_number(prop_value(monitor, index, "total"));
        const percent = Math.max(
            0,
            Math.min(100, total !== undefined && total > 0 ? (value / total) * 100 : value),
        );
        const meta_parts = [
            as_text(prop_value(monitor, index, "rate")),
            as_text(prop_value(monitor, index, "eta")),
        ].filter((part) => part !== "");
        context["percent"] = Math.round(percent);
        context["percent_label"] =
            total !== undefined && total > 0
                ? `${format_number(value)}/${format_number(total)}`
                : `${Math.round(percent)}%`;
        context["meta_line"] = meta_parts.join(" · ");
    
    break;
    }
    case "stat": {
        const value = as_number(prop_value(monitor, index, "value"));
        context["value"] = value === undefined ? as_text(prop_value(monitor, index, "value")) : format_number(value);
        context["unit"] = as_text(prop_value(monitor, index, "unit"));
        const delta = as_number(prop_value(monitor, index, "delta"));
        if (delta !== undefined && delta !== 0) {
            context["delta_label"] = `${delta > 0 ? "▲" : "▼"}${format_number(Math.abs(delta))}`;
            context["delta_class"] = delta > 0 ? "pear-wall-delta-up" : "pear-wall-delta-down";
        }
    
    break;
    }
    case "sparkline": {
        const value = as_number(prop_value(monitor, index, "value"));
        context["value"] = value === undefined ? "" : format_number(value);
        context["unit"] = as_text(prop_value(monitor, index, "unit"));
        context["spark_data"] = monitor.history.map((point) => point.toPrecision(6)).join(",");
    
    break;
    }
    case "logtail": {
        const lines = prop_value(monitor, index, "lines");
        const limit = as_number(prop_value(monitor, index, "limit")) ?? 8;
        context["lines"] = Array.isArray(lines)
            ? lines.slice(-limit).map((line) => as_text(line))
            : [];
    
    break;
    }
    case "badge": {
        const state = as_text(prop_value(monitor, index, "state"));
        context["state"] = ["ok", "warn", "err"].includes(state) ? state : "ok";
        context["text"] = as_text(prop_value(monitor, index, "text"));
    
    break;
    }
    case "clock": {
        context["timezone"] = as_text(prop_value(monitor, index, "timezone"));
    
    break;
    }
    case "countdown": {
        context["target"] = as_text(prop_value(monitor, index, "target"));
    
    break;
    }
    case "calendar": {
        const highlights = prop_value(monitor, index, "highlights");
        const highlight_set = new Set(
            Array.isArray(highlights) ? highlights.map((entry) => as_text(entry)) : [],
        );
        Object.assign(context, calendar_cells(highlight_set));
        context["day_headers"] = DAY_HEADERS;
    
    break;
    }
    case "keyvals": {
        const pairs = prop_value(monitor, index, "pairs");
        context["pairs"] = Array.isArray(pairs)
            ? pairs
                  .filter((pair) => Array.isArray(pair) && pair.length >= 2)
                  .slice(0, 12)
                  .map((pair: unknown[]) => ({key: as_text(pair[0]), value: as_text(pair[1])}))
            : [];
    
    break;
    }
    // No default
    }
    return context;
}

function render_context(): Record<string, unknown> {
    const blocks = monitors.map((monitor) => ({
        id: monitor.id,
        title: monitor.title || monitor.ask.slice(0, 24),
        ask: monitor.ask,
        status: monitor.status,
        collapsed: collapsed_ids.has(monitor.id),
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
        components: (monitor.block_spec.components ?? []).map((component, index) =>
            component_context(monitor, component, index),
        ),
    }));
    return {has_blocks: blocks.length > 0, blocks};
}

function draw_sparklines($wall: JQuery): void {
    $wall.find<HTMLCanvasElement>("canvas[data-wall-spark]").each((_index, canvas) => {
        const points = (canvas.dataset["wallSpark"] ?? "")
            .split(",")
            .map(Number)
            .filter((point) => Number.isFinite(point));
        const width = canvas.parentElement?.clientWidth ?? 120;
        canvas.width = width;
        const height = canvas.height;
        const context = canvas.getContext("2d");
        if (!context || points.length < 2) {
            return;
        }
        const accent = getComputedStyle(canvas).getPropertyValue("--pear-accent-ink").trim();
        const low = Math.min(...points);
        const high = Math.max(...points);
        const range = high - low || 1;
        context.fillStyle = accent || "#8bd0a2";
        // Pixel steps, not a smooth line: one 2px column per sample.
        const step = Math.max(2, Math.floor(width / points.length));
        for (const [index, point] of points.entries()) {
            const x = index * step;
            const bar_height = Math.max(2, Math.round(((point - low) / range) * (height - 4)) + 2);
            context.fillRect(x, height - bar_height, Math.max(1, step - 1), 2);
        }
    });
}

function apply_meter_fills($wall: JQuery): void {
    $wall.find(".pear-wall-meter-fill").each(function () {
        const percent = Number($(this).attr("data-wall-percent") ?? "0");
        $(this).css("width", `${Math.max(0, Math.min(100, percent))}%`);
    });
}

function update_clocks(): void {
    const $wall = $("#pear-wall");
    $wall.find("[data-wall-clock]").each(function () {
        const timezone = $(this).attr("data-wall-clock") ?? "";
        try {
            $(this).text(
                new Date().toLocaleTimeString(undefined, {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                    hour12: false,
                    ...(timezone === "" ? {} : {timeZone: timezone}),
                }),
            );
        } catch {
            $(this).text(new Date().toLocaleTimeString(undefined, {hour12: false}));
        }
    });
    $wall.find("[data-wall-countdown]").each(function () {
        const target = new Date($(this).attr("data-wall-countdown") ?? "");
        if (Number.isNaN(target.getTime())) {
            $(this).text("—");
            return;
        }
        let remaining = Math.max(0, Math.floor((target.getTime() - Date.now()) / 1000));
        const days = Math.floor(remaining / 86400);
        remaining -= days * 86400;
        const hours = Math.floor(remaining / 3600);
        remaining -= hours * 3600;
        const minutes = Math.floor(remaining / 60);
        const seconds = remaining - minutes * 60;
        const pad = (value: number): string => String(value).padStart(2, "0");
        $(this).text(
            days > 0
                ? `${days}d ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
                : `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`,
        );
    });
}

function sync_clock_timer(): void {
    const needs_clock = $("#pear-wall").find("[data-wall-clock], [data-wall-countdown]").length > 0;
    if (needs_clock && clock_timer === undefined && !document.hidden) {
        update_clocks();
        clock_timer = setInterval(update_clocks, 1000);
    } else if ((!needs_clock || document.hidden) && clock_timer !== undefined) {
        clearInterval(clock_timer);
        clock_timer = undefined;
    }
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

function render(): void {
    const $wall = $("#pear-wall");
    if ($wall.length === 0) {
        return;
    }
    const context = wall_context();
    if (context === undefined) {
        $wall.attr("hidden", "hidden");
        sync_clock_timer();
        return;
    }
    $wall.removeAttr("hidden");
    const template_context = render_context();
    const signature = JSON.stringify(template_context);
    if (signature === last_render_signature) {
        pulse_changed_blocks($wall);
        return;
    }
    last_render_signature = signature;

    const $input = $wall.find<HTMLInputElement>(".pear-wall-input");
    const input_value = $input.val() ?? "";
    const input_focused = $input.is(":focus");

    $wall.html(render_pear_wall(template_context));

    const $new_input = $wall.find<HTMLInputElement>(".pear-wall-input");
    $new_input.val(input_value);
    if (input_focused) {
        $new_input.trigger("focus");
    }
    apply_meter_fills($wall);
    draw_sparklines($wall);
    pulse_changed_blocks($wall);
    sync_clock_timer();
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

function monitor_action(monitor_id: number, action: string, extra: Record<string, string> = {}): void {
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
            const parsed = z
                .object({msg: z.string()})
                .safeParse(xhr.responseJSON ?? {});
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
        sync_clock_timer();
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
