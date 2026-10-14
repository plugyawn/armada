/* Main compose box module for sending messages. */

import $ from "jquery";
import assert from "minimalistic-assert";
import * as z from "zod/mini";

import render_success_message_scheduled_banner from "../templates/compose_banner/success_message_scheduled_banner.hbs";
import render_wildcard_mention_not_allowed_error from "../templates/compose_banner/wildcard_mention_not_allowed_error.hbs";

import * as channel from "./channel.ts";
import * as compose_banner from "./compose_banner.ts";
import * as compose_controller from "./compose_controller.ts";
import * as compose_notifications from "./compose_notifications.ts";
import * as compose_pear from "./compose_pear.ts";
import * as compose_send from "./compose_send.ts";
import * as compose_state from "./compose_state.ts";
import * as compose_ui from "./compose_ui.ts";
import * as compose_validate from "./compose_validate.ts";
import * as drafts from "./drafts.ts";
import type {PostMessageAPIData} from "./echo.ts";
import type {LocalMessage} from "./message_helper.ts";
import * as onboarding_steps from "./onboarding_steps.ts";
import * as scheduled_messages from "./scheduled_messages.ts";
import * as typing from "./typing.ts";
import {user_settings} from "./user_settings.ts";

// Docs: https://zulip.readthedocs.io/en/latest/subsystems/sending-messages.html

export type SendMessageData = compose_send.ComposeSendMessageData & {draft_id: string};

export function clear_invites(): void {
    $(
        `#compose_banners .${CSS.escape(compose_banner.CLASSNAMES.recipient_not_subscribed)}`,
    ).remove();
}

export function clear_private_stream_alert(): void {
    $(`#compose_banners .${CSS.escape(compose_banner.CLASSNAMES.private_stream_warning)}`).remove();
}

export function clear_preview_area(): void {
    compose_ui.exit_preview_mode($("#compose"));
    compose_ui.resize_textarea_to_content($("textarea#compose-textarea"));
    compose_ui.clear_thumbnail_polling();
}

export function show_preview_area(): void {
    compose_ui.enter_preview_mode($("#compose"));
    render_preview_area();
}

export function render_preview_area(): void {
    const $compose_textarea = $<HTMLTextAreaElement>("textarea#compose-textarea");
    const content = compose_state.message_content();
    const $preview_message_area = $("#compose .preview_message_area");
    compose_ui.render_and_show_preview(
        $("#compose"),
        $("#compose .markdown_preview_spinner"),
        $("#compose .preview_content"),
        content,
    );
    const edit_height = $compose_textarea.height();
    $preview_message_area.css({"min-height": edit_height + "px"});
    $preview_message_area.show();
}

export function clear_compose_box(): void {
    /* Before clearing the compose box, we reset it to the
     * default/normal size. Note that for locally echoed messages, we
     * will have already done this action before echoing the message
     * to avoid the compose box triggering "new message out of view"
     * notifications incorrectly. */
    if (compose_ui.is_expanded()) {
        compose_ui.make_compose_box_original_size();
    }
    clear_preview_area();
    $("textarea#compose-textarea").val("").trigger("focus");
    compose_ui.compose_textarea_typeahead?.hide();
    compose_validate.check_overflow_text($("#send_message_form"));
    compose_validate.clear_topic_resolved_warning();
    drafts.set_compose_draft_id(undefined);
    compose_ui.autosize_textarea($("textarea#compose-textarea"));
    compose_banner.clear_errors();
    compose_banner.clear_warnings();
    compose_banner.clear_uploads();
    compose_ui.hide_compose_spinner();
    scheduled_messages.reset_selected_schedule_timestamp();
    $(".needs-empty-compose").removeClass("disabled-on-hover");
    compose_pear.clear();
}

export type SentMessageData = compose_send.SentComposeMessageData<SendMessageData>;

function after_main_send_success(
    sent_message: SentMessageData | LocalMessage,
    data: PostMessageAPIData,
): void {
    if (sent_message.type !== "stream") {
        return;
    }
    if (data.automatic_new_visibility_policy) {
        if (!onboarding_steps.ONE_TIME_NOTICES_TO_DISPLAY.has("visibility_policy_banner")) {
            return;
        }
        compose_notifications.notify_automatic_new_visibility_policy(sent_message, {
            ...data,
            automatic_new_visibility_policy: data.automatic_new_visibility_policy,
        });
        return;
    }
    const muted_narrow = compose_notifications.get_muted_narrow(sent_message);
    if (muted_narrow) {
        compose_notifications.notify_unmute(
            muted_narrow,
            sent_message.stream_id,
            sent_message.topic,
        );
    }
}

export function send_message_success(
    sent_message: SentMessageData | LocalMessage,
    data: PostMessageAPIData,
): void {
    if (!sent_message.locally_echoed) {
        clear_compose_box();
    }

    compose_send.finalize_message_success(sent_message, data);
    after_main_send_success(sent_message, data);
}

function main_destination(): compose_controller.ComposeDestination {
    const message_type = compose_state.get_message_type();
    assert(message_type !== undefined);
    let recipient: compose_send.ResolvedComposeRecipient;
    if (message_type === "private") {
        const recipient_emails = compose_state.private_message_recipient_emails();
        const recipient_ids = compose_state.private_message_recipient_ids();
        recipient = {
            type: "private",
            user_ids: recipient_ids,
            recipient_emails,
        };
    } else {
        const stream_id = compose_state.stream_id();
        assert(stream_id !== undefined);
        recipient = {
            type: "stream",
            stream_id,
            topic: compose_state.topic(),
        };
    }
    return {
        recipient,
        pear_followup: recipient.type === "stream" && compose_pear.is_followup_enabled(),
    };
}

function show_main_send_error(response: string, server_error_code: string): void {
    if (server_error_code === "TOPIC_WILDCARD_MENTION_NOT_ALLOWED") {
        const new_row_html = render_wildcard_mention_not_allowed_error({
            banner_type: compose_banner.ERROR,
            classname: compose_banner.CLASSNAMES.wildcards_not_allowed,
        });
        compose_banner.append_compose_banner_to_banner_list($(new_row_html), $("#compose_banners"));
        return;
    }
    compose_banner.show_error_message(
        response,
        compose_banner.CLASSNAMES.generic_compose_error,
        $("#compose_banners"),
        $("textarea#compose-textarea"),
    );
}

let main_controller: compose_controller.ComposeController | undefined;

function get_main_controller(): compose_controller.ComposeController {
    main_controller ??= compose_controller.create({
        get_content: compose_state.message_content,
        get_destination: main_destination,
        validate: compose_validate.validate,
        save_draft(is_sending_saving) {
            return drafts.update_draft({
                no_notify: true,
                update_count: false,
                is_sending_saving,
                force_save: is_sending_saving,
            });
        },
        is_busy: () => compose_ui.compose_spinner_visible,
        set_busy(busy) {
            if (busy) {
                compose_ui.show_compose_spinner();
            } else {
                compose_ui.hide_compose_spinner();
            }
        },
        clear: clear_compose_box,
        show_send_error: show_main_send_error,
        stop_typing: typing.stop_typing_notifications,
        before_finish() {
            clear_invites();
            clear_private_stream_alert();
            compose_banner.clear_message_sent_banners();
        },
        before_send() {
            compose_state.set_recipient_edited_manually(false);
            compose_state.set_is_content_unedited_restored_draft(false);
        },
        dispatch_send() {
            send_message();
        },
        schedule: schedule_message_to_custom_date,
        after_success: after_main_send_success,
    });
    return main_controller;
}

export let send_message = (): void => {
    get_main_controller().send();
};

export function rewire_send_message(value: typeof send_message): void {
    send_message = value;
}

export function handle_enter_key_with_preview_open(cmd_or_ctrl_pressed = false): void {
    if (
        (user_settings.enter_sends && !cmd_or_ctrl_pressed) ||
        (!user_settings.enter_sends && cmd_or_ctrl_pressed)
    ) {
        // If this enter should send, we attempt to send the message.
        finish();
    } else {
        // Otherwise, we return to the normal compose state.
        clear_preview_area();
    }
}

// Common entrypoint for asking the server to send the message
// currently drafted in the compose box, including for scheduled
// messages.
export let finish = (scheduling_message = false): boolean | undefined =>
    get_main_controller().finish(scheduling_message);

export function rewire_finish(value: typeof finish): void {
    finish = value;
}

export function do_post_send_tasks(): void {
    compose_controller.run_post_send_tasks(typing.stop_typing_notifications);
}

function schedule_message_to_custom_date(): void {
    const deliver_at = scheduled_messages.get_formatted_selected_send_later_time();
    const scheduled_delivery_timestamp = scheduled_messages.get_selected_send_later_timestamp();

    const message_type = compose_state.get_message_type();
    let req_type;

    if (message_type === "private") {
        req_type = "direct";
    } else {
        req_type = message_type;
    }

    let message_to;
    if (message_type === "private") {
        message_to = compose_state.private_message_recipient_ids();
    } else {
        message_to = compose_state.stream_id();
    }

    const scheduled_message_data = {
        type: req_type,
        to: JSON.stringify(message_to),
        topic: message_type === "stream" ? compose_state.topic() : "",
        content: compose_state.message_content(),
        scheduled_delivery_timestamp,
    };

    const draft_id = drafts.update_draft({
        no_notify: true,
        update_count: false,
        is_sending_saving: true,
        force_save: true,
    });
    assert(draft_id !== undefined);

    const $banner_container = $("#compose_banners");
    const success = function (data: unknown): void {
        drafts.draft_model.deleteDrafts([draft_id]);
        clear_compose_box();
        const {scheduled_message_id} = z.object({scheduled_message_id: z.number()}).parse(data);
        const new_row_html = render_success_message_scheduled_banner({
            scheduled_message_id,
            minimum_scheduled_message_delay_minutes:
                scheduled_messages.MINIMUM_SCHEDULED_MESSAGE_DELAY_SECONDS / 60,
            deliver_at,
            minimum_scheduled_message_delay_minutes_note:
                scheduled_messages.show_minimum_scheduled_message_delay_minutes_note,
        });
        compose_banner.clear_message_sent_banners();
        compose_banner.append_compose_banner_to_banner_list($(new_row_html), $banner_container);
    };

    const error = function (xhr: JQuery.jqXHR<unknown>): void {
        const response = channel.xhr_error_message("Error sending message", xhr);
        compose_ui.hide_compose_spinner();
        compose_banner.show_error_message(
            response,
            compose_banner.CLASSNAMES.generic_compose_error,
            $banner_container,
            $("textarea#compose-textarea"),
        );
        const draft = drafts.draft_model.getDraft(draft_id);
        assert(draft !== false);
        draft.is_sending_saving = false;
        drafts.draft_model.editDraft(draft_id, draft);
    };

    channel.post({
        url: "/json/scheduled_messages",
        data: scheduled_message_data,
        success,
        error,
    });
}

export function is_topic_input_focused(): boolean {
    return $("#stream_message_recipient_topic").is(":focus");
}
