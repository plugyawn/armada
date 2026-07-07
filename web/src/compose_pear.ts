import $ from "jquery";

import * as compose_actions from "./compose_actions.ts";
import * as compose_state from "./compose_state.ts";
import * as compose_ui from "./compose_ui.ts";
import * as compose_validate from "./compose_validate.ts";
import {$t} from "./i18n.ts";

let followup_enabled = false;

function is_stream_compose(): boolean {
    return compose_state.get_message_type() === "stream";
}

function update_controls(): void {
    const active = followup_enabled && is_stream_compose();
    $("#compose").toggleClass("pear-compose-active", active);
    const label = active
        ? $t({defaultMessage: "Pear will respond to this message"})
        : $t({defaultMessage: "Ask Pear in this topic"});
    $(".compose-pear-button")
        .toggleClass("active", active)
        .attr("aria-pressed", active ? "true" : "false")
        .attr("aria-label", label)
        .attr("data-tippy-content", label);
    if (active) {
        $("textarea#compose-textarea").attr(
            "placeholder",
            $t({defaultMessage: "Ask Pear in this topic"}),
        );
    }
}

function focus_compose_box(): void {
    $("textarea#compose-textarea").trigger("focus");
    // Apply the Pear labels now for the current frame, and once more after
    // compose_actions.start()'s own recipient-row updates land (they run
    // between this call and the next paint and reset the placeholder).
    update_controls();
    requestAnimationFrame(update_controls);
}

export function is_followup_enabled(): boolean {
    return followup_enabled && is_stream_compose();
}

export function clear(): void {
    followup_enabled = false;
    update_controls();
}

export function enable(): void {
    if (!compose_state.composing() || !is_stream_compose()) {
        compose_actions.start({
            message_type: "stream",
            trigger: "pear compose",
            keep_composebox_empty: false,
        });
    }
    followup_enabled = true;
    focus_compose_box();
    compose_ui.autosize_textarea($("textarea#compose-textarea"));
    compose_validate.validate_and_update_send_button_status();
}

export function toggle(): void {
    if (is_followup_enabled()) {
        clear();
        focus_compose_box();
        return;
    }
    enable();
}
