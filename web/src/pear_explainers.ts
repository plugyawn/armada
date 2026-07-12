// Agent-composed explainers in chat messages.
//
// The Pear bot posts either a declarative ```pear-explainer spec or a
// shorthand ```pear-svg drawing. This pass uses the same component kit
// as the Wall, then offers pin-to-Wall and explicit agent actions.

import $ from "jquery";

import render_pear_chat_explainer from "../templates/pear_chat_explainer.hbs";

import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as message_store from "./message_store.ts";
import {send_pear_explainer_action} from "./pear_explainer_actions.ts";
import {type PearExplainerSpec, parse_pear_explainer_spec} from "./pear_explainer_spec.ts";
import * as pear_wall from "./pear_wall.ts";
import * as pear_wall_components from "./pear_wall_components.ts";
import * as stream_data from "./stream_data.ts";

// Specs keyed by explainer element, kept off the DOM (attributes would be
// both bulky and attacker-visible surface).
const explainer_specs = new WeakMap<HTMLElement, PearExplainerSpec>();

export function enhance_pear_chat_explainers($row: JQuery): void {
    if (!$row.hasClass("pear-message") || typeof document.createElementNS !== "function") {
        return;
    }
    $row.find(
        '.message_content div.codehilite[data-code-language="pear-explainer"], .message_content div.codehilite[data-code-language="pear-svg"]',
    ).each((_index, code_block) => {
        const code = code_block.querySelector("pre");
        const language = code_block.getAttribute("data-code-language");
        if (language !== "pear-explainer" && language !== "pear-svg") {
            return;
        }
        const spec = parse_pear_explainer_spec(code?.textContent ?? "", language);
        if (spec === undefined) {
            // Malformed specs stay visible as the code block — an
            // honest failure the author can read and fix.
            return;
        }
        const explainer = document.createElement("article");
        explainer.setAttribute("class", "pear-chat-explainer");
        // The morph keys top-level children by the pristine source
        // fingerprint; carrying it over keeps the widget alive
        // across streaming edits when the JSON is unchanged.
        const source_fingerprint = code_block.getAttribute("data-pear-source");
        if (source_fingerprint !== null) {
            explainer.setAttribute("data-pear-source", source_fingerprint);
        }
        explainer.innerHTML = render_pear_chat_explainer({
            title: spec.title,
            has_actions: (spec.block.actions ?? []).length > 0,
            pin_label: $t({defaultMessage: "pin to wall"}),
            pin_tooltip: $t({defaultMessage: "Keep this live on the wall"}),
            components: spec.block.components.map((component, index) =>
                pear_wall_components.component_context(component, index, {}, []),
            ),
            actions: (spec.block.actions ?? []).map((action, action_index) => ({
                action_index,
                label: action.label,
            })),
        });
        const $explainer = $(explainer);
        if (
            !pear_wall_components.mount_svg_components(
                $explainer,
                spec.block.components,
                {},
                new Map(),
            )
        ) {
            return;
        }
        explainer_specs.set(explainer, spec);
        code_block.replaceWith(explainer);
        pear_wall_components.apply_meter_fills($explainer);
        pear_wall_components.draw_sparklines($explainer);
        pear_wall_components.update_clocks($explainer);
    });
    sync_widget_clock_timer();
}

let widget_clock_timer: ReturnType<typeof setInterval> | undefined;

function sync_widget_clock_timer(): void {
    if (typeof document === "undefined" || typeof setInterval !== "function") {
        return;
    }
    const $widgets = $(".pear-chat-explainer");
    const needs_clock =
        $widgets.length > 0 && pear_wall_components.has_client_ticked_components($widgets);
    if (needs_clock && widget_clock_timer === undefined) {
        widget_clock_timer = setInterval(() => {
            const $live = $(".pear-chat-explainer");
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

function message_target_for_explainer(
    explainer: HTMLElement,
): {stream: string; topic: string} | undefined {
    const row = explainer.closest(".message_row");
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

function pin_explainer(explainer: HTMLElement, $button: JQuery): void {
    const spec = explainer_specs.get(explainer);
    const target = message_target_for_explainer(explainer);
    if (spec === undefined || target === undefined) {
        return;
    }
    $button.prop("disabled", true).text($t({defaultMessage: "pinning…"}));
    void channel.post({
        url: "/json/pear/monitors",
        data: {
            stream: target.stream,
            topic: target.topic,
            request: spec.title ?? $t({defaultMessage: "pinned explainer"}),
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
    $("body").on("click", ".pear-explainer-pin", function (this: HTMLElement, event) {
        event.preventDefault();
        event.stopPropagation();
        const explainer = this.closest(".pear-chat-explainer");
        if (explainer instanceof HTMLElement) {
            pin_explainer(explainer, $(this));
        }
    });
    $("body").on(
        "click",
        ".pear-chat-explainer .pear-explainer-action",
        function (this: HTMLButtonElement, event) {
            event.preventDefault();
            event.stopPropagation();
            const explainer = this.closest(".pear-chat-explainer");
            if (!(explainer instanceof HTMLElement) || this.disabled) {
                return;
            }
            const spec = explainer_specs.get(explainer);
            const index = Number(this.dataset["pearExplainerAction"] ?? "-1");
            const action = spec?.block.actions?.[index];
            const target = message_target_for_explainer(explainer);
            if (action === undefined || target === undefined) {
                return;
            }
            const label = this.textContent ?? action.label;
            this.disabled = true;
            this.setAttribute("aria-busy", "true");
            this.textContent = $t({defaultMessage: "asking…"});
            send_pear_explainer_action(target, action.prompt, {
                success: () => {
                    this.removeAttribute("aria-busy");
                    this.textContent = $t({defaultMessage: "asked ✓"});
                },
                error: () => {
                    this.disabled = false;
                    this.removeAttribute("aria-busy");
                    this.textContent = label;
                },
            });
        },
    );
}
