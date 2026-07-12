// The wall component kit: the render vocabulary shared by wall blocks
// and agent-composed chat widgets. Pure context-building plus a few
// post-render hooks; no polling, no network — hosts own their lifecycle.

import $ from "jquery";

import {sanitize_pear_svg} from "./pear_svg.ts";

export type PearKitComponent = {
    type: string;
    props: Record<string, unknown>;
};

export const PEAR_KIT_COMPONENT_TYPES = new Set([
    "text",
    "svg",
    "meter",
    "stat",
    "sparkline",
    "logtail",
    "badge",
    "clock",
    "countdown",
    "calendar",
    "keyvals",
]);

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

function prop_value(
    component: PearKitComponent,
    index: number,
    prop: string,
    live_props: Record<string, unknown>,
): unknown {
    const live = live_props[`${index}.${prop}`];
    if (live !== undefined && live !== null) {
        return live;
    }
    return component.props[prop];
}

export function component_context(
    component: PearKitComponent,
    index: number,
    live_props: Record<string, unknown>,
    history: number[],
): Record<string, unknown> {
    const type = component.type;
    const value_of = (prop: string): unknown => prop_value(component, index, prop, live_props);
    const label = as_text(value_of("label"));
    const context: Record<string, unknown> = {
        [`is_${type}`]: true,
        component_index: index,
        label,
    };
    switch (type) {
        case "text":
            context["eyebrow"] = as_text(value_of("eyebrow"));
            context["heading"] = as_text(value_of("heading"));
            context["body"] = as_text(value_of("body"));
            break;
        case "svg":
            break;
        case "meter": {
            const value = as_number(value_of("value")) ?? 0;
            const total = as_number(value_of("total"));
            const percent = Math.max(
                0,
                Math.min(100, total !== undefined && total > 0 ? (value / total) * 100 : value),
            );
            const meta_parts = [as_text(value_of("rate")), as_text(value_of("eta"))].filter(
                (part) => part !== "",
            );
            context["percent"] = Math.round(percent);
            context["percent_label"] =
                total !== undefined && total > 0
                    ? `${format_number(value)}/${format_number(total)}`
                    : `${Math.round(percent)}%`;
            context["meta_line"] = meta_parts.join(" · ");
            break;
        }
        case "stat": {
            const value = as_number(value_of("value"));
            context["value"] =
                value === undefined ? as_text(value_of("value")) : format_number(value);
            context["unit"] = as_text(value_of("unit"));
            const delta = as_number(value_of("delta"));
            if (delta !== undefined && delta !== 0) {
                context["delta_label"] =
                    `${delta > 0 ? "▲" : "▼"}${format_number(Math.abs(delta))}`;
                context["delta_class"] = delta > 0 ? "pear-wall-delta-up" : "pear-wall-delta-down";
            }
            break;
        }
        case "sparkline": {
            const value = as_number(value_of("value"));
            context["value"] = value === undefined ? "" : format_number(value);
            context["unit"] = as_text(value_of("unit"));
            context["spark_data"] = history.map((point) => point.toPrecision(6)).join(",");
            break;
        }
        case "logtail": {
            const lines = value_of("lines");
            const limit = as_number(value_of("limit")) ?? 8;
            context["lines"] = Array.isArray(lines)
                ? lines.slice(-limit).map((line) => as_text(line))
                : [];
            break;
        }
        case "badge": {
            const state = as_text(value_of("state"));
            context["state"] = ["ok", "warn", "err"].includes(state) ? state : "ok";
            context["text"] = as_text(value_of("text"));
            break;
        }
        case "clock": {
            context["timezone"] = as_text(value_of("timezone"));
            break;
        }
        case "countdown": {
            context["target"] = as_text(value_of("target"));
            break;
        }
        case "calendar": {
            const highlights = value_of("highlights");
            const highlight_set = new Set(
                Array.isArray(highlights) ? highlights.map((entry) => as_text(entry)) : [],
            );
            Object.assign(context, calendar_cells(highlight_set));
            context["day_headers"] = DAY_HEADERS;
            break;
        }
        case "keyvals": {
            const pairs = value_of("pairs");
            context["pairs"] = Array.isArray(pairs)
                ? pairs
                      .filter((pair) => Array.isArray(pair) && pair.length >= 2)
                      .slice(0, 12)
                      .map((pair: unknown[]) => ({
                          key: as_text(pair[0]),
                          value: as_text(pair[1]),
                      }))
                : [];
            break;
        }
        default:
            break;
    }
    return context;
}

export function mount_svg_components(
    $scope: JQuery,
    components: PearKitComponent[],
    live_props: Record<string, unknown>,
    last_valid_markup: Map<number, string>,
): boolean {
    let all_valid = true;
    $scope.find("[data-pear-svg-component]").each((_placeholder_index, host) => {
        const index = Number(host.dataset["pearSvgComponent"] ?? "-1");
        const component = components[index];
        if (component?.type !== "svg") {
            all_valid = false;
            host.textContent = "diagram unavailable";
            host.classList.add("pear-explainer-svg-invalid");
            return;
        }
        const current = as_text(prop_value(component, index, "markup", live_props));
        let markup = current;
        let svg = sanitize_pear_svg(markup);
        if (svg === undefined) {
            all_valid = false;
            markup = last_valid_markup.get(index) ?? "";
            svg = sanitize_pear_svg(markup);
            host.classList.add("pear-explainer-svg-stale");
        } else {
            last_valid_markup.set(index, markup);
        }
        if (svg === undefined) {
            host.textContent = "diagram unavailable";
            host.classList.add("pear-explainer-svg-invalid");
            return;
        }
        host.replaceChildren(svg);
    });
    return all_valid;
}

export function draw_sparklines($scope: JQuery): void {
    $scope.find<HTMLCanvasElement>("canvas[data-wall-spark]").each((_index, canvas) => {
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

export function apply_meter_fills($scope: JQuery): void {
    $scope.find(".pear-wall-meter-fill").each(function () {
        const percent = Number($(this).attr("data-wall-percent") ?? "0");
        $(this).css("width", `${Math.max(0, Math.min(100, percent))}%`);
    });
}

export function update_clocks($scope: JQuery): void {
    $scope.find("[data-wall-clock]").each(function () {
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
    $scope.find("[data-wall-countdown]").each(function () {
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

export function has_client_ticked_components($scope: JQuery): boolean {
    return $scope.find("[data-wall-clock], [data-wall-countdown]").length > 0;
}
