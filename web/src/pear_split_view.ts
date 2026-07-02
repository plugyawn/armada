// The reclaimed right area (#pear-right-area): an on-demand pane host
// appended to .app-main, replacing the permanent right rail as the
// home for Pear surfaces (R2). In this slice it hosts a single pane —
// the Pear panel — opened from the navbar toggle; session split-view
// panes and the canvas pane land here in later slices.

import $ from "jquery";

import render_pear_right_area from "../templates/pear_right_area.hbs";

import * as compose_ui from "./compose_ui.ts";
import * as pear_git_hover from "./pear_git_hover.ts";
import * as pear_panel from "./pear_panel.ts";

let initialized = false;
let area_open = false;

export function is_open(): boolean {
    return area_open;
}

function ensure_host(): boolean {
    if ($("#pear-right-area").length > 0) {
        return true;
    }
    const $app_main = $(".app-main");
    if ($app_main.length === 0) {
        // Guarded for the node-test environment, where the app grid
        // does not exist.
        return false;
    }
    $app_main.append($(render_pear_right_area()));
    return true;
}

function update_layout(): void {
    $("body").toggleClass("pear-right-area-open", area_open);
    $("#pear-pane-toggle-button").attr("aria-expanded", String(area_open));
    // The middle column width changes when the right area opens or
    // closes; recheck the compose formatting-buttons overflow state.
    compose_ui.maybe_show_scrolling_formatting_buttons("#message-formatting-controls-container");
}

export function open(): void {
    if (area_open || !ensure_host()) {
        return;
    }
    area_open = true;
    update_layout();
    // Opening the pane subscribes the panel to the runs store, which
    // starts the poll; closed ⇒ zero /json/pear/runs requests.
    pear_panel.open();
}

export function close(): void {
    if (!area_open) {
        return;
    }
    area_open = false;
    update_layout();
    pear_panel.close();
}

export function toggle(): void {
    if (area_open) {
        close();
    } else {
        open();
    }
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    pear_panel.initialize();
    pear_git_hover.initialize();
    $("body").on("click", "#pear-pane-toggle-button", (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle();
    });
    $("body").on("click", "#pear-right-area .pear-pane-close", () => {
        close();
    });
}
