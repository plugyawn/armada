// Agent-composed widgets in chat messages.
//
// The Pear bot posts a fenced block ```pear-widget containing the same
// spec JSON the wall's monitors use; this pass swaps the rendered code
// block for the real components (shared kit) plus a pin-to-wall button
// that promotes the widget to a live monitor with no agent run.

import $ from "jquery";
import * as z from "zod/mini";

import render_pear_chat_widget from "../templates/pear_chat_widget.hbs";

import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as message_store from "./message_store.ts";
import * as pear_wall from "./pear_wall.ts";
import * as pear_wall_components from "./pear_wall_components.ts";
import * as stream_data from "./stream_data.ts";

const widget_spec_schema = z.object({
    title: z.optional(z.string()),
    cadence_seconds: z.optional(z.number()),
    block: z.object({
        components: z.array(
            z.object({
                type: z.string(),
                props: z.record(z.string(), z.unknown()),
            }),
        ),
    }),
    recipe: z.optional(
        z.object({
            command: z.optional(z.string()),
            parse: z.object({mode: z.string(), pattern: z.optional(z.string())}),
            bindings: z.optional(z.record(z.string(), z.string())),
        }),
    ),
});
type PearWidgetSpec = z.infer<typeof widget_spec_schema>;

// Specs keyed by widget element, kept off the DOM (attributes would be
// both bulky and attacker-visible surface).
const widget_specs = new WeakMap<HTMLElement, PearWidgetSpec>();

function parse_widget_spec(raw_text: string): PearWidgetSpec | undefined {
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw_text);
    } catch {
        return undefined;
    }
    const result = widget_spec_schema.safeParse(parsed);
    if (!result.success) {
        return undefined;
    }
    const components = result.data.block.components;
    if (
        components.length === 0 ||
        components.length > 4 ||
        !components.every((component) =>
            pear_wall_components.PEAR_KIT_COMPONENT_TYPES.has(component.type),
        )
    ) {
        return undefined;
    }
    return result.data;
}

export function enhance_pear_chat_widgets($row: JQuery): void {
    if (!$row.hasClass("pear-message") || typeof document.createElementNS !== "function") {
        return;
    }
    $row.find('.message_content div.codehilite[data-code-language="pear-widget"]').each(
        (_index, code_block) => {
            const code = code_block.querySelector("pre");
            const spec = parse_widget_spec(code?.textContent ?? "");
            if (spec === undefined) {
                // Malformed specs stay visible as the code block — an
                // honest failure the author can read and fix.
                return;
            }
            const widget = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
            widget.setAttribute("class", "pear-chat-widget");
            // The morph keys top-level children by the pristine source
            // fingerprint; carrying it over keeps the widget alive
            // across streaming edits when the JSON is unchanged.
            const source_fingerprint = code_block.getAttribute("data-pear-source");
            if (source_fingerprint !== null) {
                widget.setAttribute("data-pear-source", source_fingerprint);
            }
            widget.innerHTML = render_pear_chat_widget({
                components: spec.block.components.map((component, index) =>
                    pear_wall_components.component_context(component, index, {}, []),
                ),
            });
            widget_specs.set(widget, spec);
            code_block.replaceWith(widget);
            const $widget = $(widget);
            pear_wall_components.apply_meter_fills($widget);
            pear_wall_components.draw_sparklines($widget);
            pear_wall_components.update_clocks($widget);
        },
    );
    sync_widget_clock_timer();
}

let widget_clock_timer: ReturnType<typeof setInterval> | undefined;

function sync_widget_clock_timer(): void {
    if (typeof document === "undefined" || typeof setInterval !== "function") {
        return;
    }
    const $widgets = $(".pear-chat-widget");
    const needs_clock =
        $widgets.length > 0 && pear_wall_components.has_client_ticked_components($widgets);
    if (needs_clock && widget_clock_timer === undefined) {
        widget_clock_timer = setInterval(() => {
            const $live = $(".pear-chat-widget");
            if ($live.length === 0 || !pear_wall_components.has_client_ticked_components($live)) {
                if (widget_clock_timer !== undefined) {
                    clearInterval(widget_clock_timer);
                    widget_clock_timer = undefined;
                }
                return;
            }
            if (!document.hidden) {
                pear_wall_components.update_clocks($live);
            }
        }, 1000);
    }
}

function message_target_for_widget(
    widget: HTMLElement,
): {stream: string; topic: string} | undefined {
    const row = widget.closest(".message_row");
    if (!(row instanceof HTMLElement)) {
        return undefined;
    }
    const message_id = Number.parseFloat(row.dataset["messageId"] ?? "");
    const message = Number.isNaN(message_id) ? undefined : message_store.get(message_id);
    if (message?.type !== "stream") {
        return undefined;
    }
    const sub = stream_data.get_sub_by_id(message.stream_id);
    if (sub === undefined) {
        return undefined;
    }
    return {stream: sub.name, topic: message.topic};
}

function pin_widget(widget: HTMLElement, $button: JQuery): void {
    const spec = widget_specs.get(widget);
    const target = message_target_for_widget(widget);
    if (spec === undefined || target === undefined) {
        return;
    }
    $button.prop("disabled", true).text($t({defaultMessage: "pinning…"}));
    void channel.post({
        url: "/json/pear/monitors",
        data: {
            stream: target.stream,
            topic: target.topic,
            request: spec.title ?? $t({defaultMessage: "pinned widget"}),
            spec: JSON.stringify(spec),
        },
        success() {
            $button.text($t({defaultMessage: "pinned ✓"}));
            pear_wall.refresh();
        },
        error() {
            $button.prop("disabled", false).text($t({defaultMessage: "pin failed — retry"}));
        },
    });
}

export function initialize(): void {
    if (typeof document === "undefined") {
        return;
    }
    $("body").on("click", ".pear-widget-pin", function (this: HTMLElement, event) {
        event.preventDefault();
        event.stopPropagation();
        const widget = this.closest(".pear-chat-widget");
        if (widget instanceof HTMLElement) {
            pin_widget(widget, $(this));
        }
    });
}
