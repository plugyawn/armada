import autosize from "autosize";
import assert from "minimalistic-assert";

import render_compose_editor from "../templates/compose_editor.hbs";

import * as compose_banner from "./compose_banner.ts";
import * as compose_controller from "./compose_controller.ts";
import type * as compose_send from "./compose_send.ts";
import * as compose_validate from "./compose_validate.ts";
import * as composebox_typeahead from "./composebox_typeahead.ts";
import * as drafts from "./drafts.ts";
import * as compose_ui from "./compose_ui.ts";
import {$t} from "./i18n.ts";
import {realm} from "./state_data.ts";
import * as typing from "./typing.ts";
import type {ComposeTypingRecipient} from "./typing.ts";
import * as upload from "./upload.ts";

let next_instance_id = 1;

export type ComposeEditorOptions = {
    $host: JQuery;
    recipient?: compose_send.ResolvedComposeRecipient;
    get_recipient?: () => compose_send.ResolvedComposeRecipient | undefined;
    thread_root_message_id?: number;
    content?: string;
    placeholder?: string;
    aria_label?: string;
    send_label?: string;
    on_draft_change?: (content: string) => void;
    on_sent?: (
        sent_message: compose_send.SentComposeMessageData,
        data: compose_send.SendMessageAPIResponse,
    ) => void;
};

export type ComposeEditor = {
    readonly element: HTMLFormElement;
    readonly textarea: HTMLTextAreaElement;
    focus: () => void;
    get_content: () => string;
    notify_content_changed: () => void;
    set_content: (content: string) => void;
    refresh_recipient: () => void;
    send: () => boolean;
    destroy: () => void;
};

export function create(options: ComposeEditorOptions): ComposeEditor {
    const instance_key = `compose-editor-${next_instance_id}`;
    next_instance_id += 1;
    const file_upload_enabled = realm.max_file_upload_size_mib > 0 && upload.feature_check();
    options.$host.html(
        render_compose_editor({
            instance_key,
            placeholder: options.placeholder ?? $t({defaultMessage: "Write a message…"}),
            aria_label: options.aria_label ?? $t({defaultMessage: "Write a message"}),
            send_label: options.send_label ?? $t({defaultMessage: "Send"}),
            file_upload_enabled,
        }),
    );
    const $form = options.$host.find<HTMLFormElement>("form.compose-editor").expectOne();
    const $textarea = $form.find<HTMLTextAreaElement>("textarea.message-textarea").expectOne();
    const $submit = $form.find<HTMLButtonElement>(".compose-editor-submit").expectOne();
    const $status = $form.find(".compose-editor-status").expectOne();
    const $banners = $form.find(".compose-editor-banners").expectOne();
    const preview_tracker = compose_ui.create_preview_render_tracker();
    const upload_config = upload.instance_config($form, instance_key);
    const uploader = file_upload_enabled ? upload.setup_upload(upload_config) : undefined;
    const typeahead = composebox_typeahead.initialize_compose_typeahead($textarea, {
        register_as_primary: false,
    });
    const typing_controller = typing.create_compose_typing_controller();
    const existing_draft =
        options.thread_root_message_id === undefined
            ? undefined
            : drafts.get_message_thread_draft(options.thread_root_message_id);
    let draft_id = existing_draft?.id;
    let destroyed = false;
    let sending = false;

    function recipient(): compose_send.ResolvedComposeRecipient | undefined {
        return options.get_recipient?.() ?? options.recipient;
    }

    function update_recipient_context(
        resolved_recipient: compose_send.ResolvedComposeRecipient | undefined,
    ): void {
        $textarea.removeAttr(
            "data-compose-stream-id data-compose-topic data-compose-private data-compose-private-user-ids",
        );
        if (resolved_recipient?.type === "stream") {
            $textarea.attr("data-compose-stream-id", String(resolved_recipient.stream_id));
            $textarea.attr("data-compose-topic", resolved_recipient.topic);
        } else if (resolved_recipient?.type === "private") {
            $textarea.attr("data-compose-private", "true");
            $textarea.attr("data-compose-private-user-ids", resolved_recipient.user_ids.join(","));
        }
    }

    function typing_recipient(
        resolved_recipient: compose_send.ResolvedComposeRecipient | undefined,
    ): ComposeTypingRecipient | null {
        if (resolved_recipient === undefined) {
            return null;
        }
        return resolved_recipient.type === "stream"
            ? {
                  notification_event_type: "typing",
                  message_type: "stream",
                  stream_id: resolved_recipient.stream_id,
                  topic: resolved_recipient.topic,
              }
            : {
                  notification_event_type: "typing",
                  message_type: "direct",
                  ids: resolved_recipient.user_ids,
              };
    }

    function content(): string {
        return $textarea.val() ?? "";
    }

    function set_busy(value: boolean): void {
        sending = value;
        $submit.prop("disabled", value);
        $form.toggleClass("compose-editor-sending", value);
    }

    function set_status(message: string): void {
        $status.text(message);
    }

    function save_draft(is_sending_saving = false): string | undefined {
        if (options.thread_root_message_id === undefined) {
            return undefined;
        }
        const resolved_recipient = recipient();
        if (resolved_recipient === undefined) {
            return draft_id;
        }
        draft_id = drafts.update_message_thread_draft({
            draft_id,
            thread_root_message_id: options.thread_root_message_id,
            recipient:
                resolved_recipient.type === "stream"
                    ? resolved_recipient
                    : {type: "private", user_ids: resolved_recipient.user_ids},
            content: content(),
            is_sending_saving,
        });
        return draft_id;
    }

    function exit_preview(): void {
        compose_ui.exit_preview_mode($form);
        autosize.update($textarea);
    }

    function show_preview(): void {
        compose_ui.enter_preview_mode($form);
        const $preview = $form.find(".preview_message_area");
        compose_ui.render_and_show_preview(
            $form,
            $form.find(".markdown_preview_spinner"),
            $form.find(".preview_content"),
            content(),
            true,
            preview_tracker,
        );
        $preview.css({"min-height": `${$textarea.height() ?? 0}px`}).show();
    }

    function set_content(new_content: string, persist = true): void {
        $textarea.val(new_content);
        compose_ui.autosize_textarea($textarea);
        options.on_draft_change?.(new_content);
        if (persist) {
            controller.notify_content_changed();
        }
    }

    function notify_content_changed(): void {
        compose_ui.autosize_textarea($textarea);
        options.on_draft_change?.(content());
        controller.notify_content_changed();
    }

    function validate(): boolean {
        const resolved_recipient = recipient();
        if (resolved_recipient === undefined) {
            set_status($t({defaultMessage: "The message destination is no longer available."}));
            return false;
        }
        update_recipient_context(resolved_recipient);
        const message_content = content();
        const validation = compose_validate.validate_resolved_message({
            recipient: resolved_recipient,
            content: message_content,
            $banner_container: $banners,
            $textarea,
            upload_in_progress:
                uploader !== undefined &&
                Object.keys(uploader.getState().currentUploads).length > 0,
        });
        if (!validation.valid) {
            set_status(validation.error ?? "");
            $textarea.trigger("focus");
            return false;
        }
        return true;
    }

    const controller = compose_controller.create({
        get_content: content,
        get_destination() {
            const resolved_recipient = recipient();
            if (resolved_recipient === undefined) {
                return undefined;
            }
            return {
                recipient: resolved_recipient,
                ...(options.thread_root_message_id === undefined
                    ? {}
                    : {thread_root_message_id: options.thread_root_message_id}),
            };
        },
        validate,
        save_draft,
        is_busy: () => sending,
        set_busy,
        clear() {
            draft_id = undefined;
            set_content("", false);
            exit_preview();
        },
        show_send_error(response) {
            set_status(response);
            $textarea.trigger("focus");
        },
        stop_typing: typing_controller.destroy,
        ...(options.on_sent === undefined ? {} : {after_success: options.on_sent}),
        update_typing() {
            const resolved_recipient = recipient();
            update_recipient_context(resolved_recipient);
            typing_controller.update(typing_recipient(resolved_recipient), content().trim() !== "");
        },
        draft_save_delay_ms: 500,
    });

    function send(): boolean {
        return controller.finish() === true;
    }

    $form.on("submit.composeEditor", (event) => {
        event.preventDefault();
        send();
    });
    $form.on("input.composeEditor", "textarea.message-textarea", () => {
        set_status("");
        notify_content_changed();
    });
    $form.on("keydown.composeEditor", "textarea.message-textarea", (event) => {
        compose_ui.handle_keydown(event, $textarea);
        if (!event.isPropagationStopped() && composebox_typeahead.should_enter_send(event)) {
            event.preventDefault();
            send();
        }
    });
    $form.on("keyup.composeEditor", "textarea.message-textarea", (event) => {
        compose_ui.handle_keyup(event, $textarea);
    });
    $form.on("click.composeEditor", ".formatting_button", (event) => {
        event.preventDefault();
        // Formatting controls in an instance editor are owned by this adapter.
        // Do not let the body's primary-compose delegate format the same range
        // a second time.
        event.stopPropagation();
        const format_type = $(event.currentTarget).attr("data-format-type");
        if (format_type !== undefined) {
            compose_ui.format_text($textarea, format_type);
            compose_ui.autosize_textarea($textarea);
            controller.notify_content_changed();
        }
    });
    $form.on("click.composeEditor", ".markdown_preview", (event) => {
        event.preventDefault();
        show_preview();
    });
    $form.on("click.composeEditor", ".undo_markdown_preview", (event) => {
        event.preventDefault();
        exit_preview();
    });
    $form.on("click.composeEditor", ".compose_upload_file", (event) => {
        event.preventDefault();
        event.stopPropagation();
        $form.find<HTMLInputElement>("input.file_input").trigger("click");
    });
    $form.on(
        "click.composeEditor",
        `.${CSS.escape(compose_banner.CLASSNAMES.wildcard_warning)} .main-view-banner-action-button`,
        (event) => {
            event.preventDefault();
            compose_validate.clear_stream_wildcard_warnings($banners);
            compose_validate.set_user_acknowledged_stream_wildcard_flag(true);
            send();
        },
    );

    update_recipient_context(recipient());
    const initial_content = options.content ?? existing_draft?.content;
    if (initial_content !== undefined) {
        set_content(initial_content, false);
    } else {
        compose_ui.autosize_textarea($textarea);
    }

    const element = $form.get(0);
    const textarea = $textarea.get(0);
    assert(element !== undefined);
    assert(textarea !== undefined);
    return {
        element,
        textarea,
        focus() {
            $textarea.trigger("focus");
        },
        get_content: content,
        notify_content_changed,
        set_content,
        refresh_recipient() {
            const resolved_recipient = recipient();
            update_recipient_context(resolved_recipient);
            save_draft();
            typing_controller.update(typing_recipient(resolved_recipient), content().trim() !== "");
        },
        send,
        destroy() {
            if (destroyed) {
                return;
            }
            destroyed = true;
            preview_tracker.render_count += 1;
            controller.destroy();
            $form.off(".composeEditor");
            if (uploader !== undefined) {
                upload.deactivate_upload(upload_config, uploader);
            }
            typeahead.hide();
            typeahead.unlisten();
            typeahead.$container.remove();
            autosize.destroy($textarea);
            options.$host.empty();
        },
    };
}
