"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

set_global("$", $);

const message_data = {
    type: "stream",
    sender_id: 42,
    queue_id: "queue-4",
    to: "[17]",
    content: "Thread reply",
    stream_id: 17,
    topic: "design",
    thread_root_message_id: 901,
    draft_id: "draft-901",
};
let build_args;
let send_args;
let finalized;
let draft_updates = [];
let formatting_calls = [];
mock_esm("../src/compose_send", {
    build_message_data(args) {
        build_args = args;
        return message_data;
    },
    send_message(args) {
        send_args = args;
        args.on_success(
            {...message_data, local_id: "loc-1", locally_echoed: false, resend: false},
            {id: 902},
        );
    },
    finalize_message_success(sent_message, data) {
        finalized = {sent_message, data};
    },
});
mock_esm("../src/compose_banner", {
    CLASSNAMES: {wildcard_warning: "wildcard-warning"},
});
mock_esm("../src/compose_validate", {
    validate_resolved_message({content}) {
        return content.trim() === ""
            ? {valid: false, error: "translated: Enter a message to send."}
            : {valid: true};
    },
    clear_stream_wildcard_warnings: noop,
    set_user_acknowledged_stream_wildcard_flag: noop,
});
mock_esm("../src/composebox_typeahead", {
    initialize_compose_typeahead: () => ({
        hide: noop,
        unlisten: noop,
        $container: {remove: noop},
    }),
    should_enter_send: () => false,
});
mock_esm("../src/drafts", {
    get_message_thread_draft: () => undefined,
    update_message_thread_draft(args) {
        draft_updates.push(args);
        return args.content === "" ? undefined : "draft-901";
    },
    draft_model: {
        getDraft: () => false,
    },
});
mock_esm("../src/echo", {
    try_deliver_locally: () => undefined,
});
mock_esm("../src/message_events", {insert_new_messages: noop});
mock_esm("../src/message_viewport", {bottom_of_feed: {reset: noop}});
mock_esm("../src/reload", {maybe_reset_pending_reload_timeout: noop});
mock_esm("../src/zcommand", {process: () => false});
mock_esm("../src/typing", {
    create_compose_typing_controller: () => ({update: noop, destroy: noop}),
});
mock_esm("../src/compose_ui", {
    create_preview_render_tracker: () => ({render_count: 0}),
    autosize_textarea: noop,
    resize_textarea_to_content: noop,
    size_textarea_to_content: noop,
    stop_sizing_textarea_to_content: noop,
    exit_preview_mode: noop,
    handle_keydown: noop,
    handle_keyup: noop,
    format_text($textarea, format_type) {
        formatting_calls.push({$textarea, format_type});
    },
});
mock_esm("../src/upload", {
    feature_check: () => false,
    instance_config: () => ({mode: "instance"}),
});

const {set_realm} = zrequire("state_data");
set_realm(make_realm({max_file_upload_size_mib: 0}));
const compose_editor = zrequire("compose_editor");

function make_editor_dom() {
    const $host = $.create("compose-editor-host");
    const $form = $.create("compose-editor-form");
    const $textarea = $.create("compose-editor-textarea");
    const $submit = $.create("compose-editor-submit");
    const $status = $.create("compose-editor-status");
    const $banners = $.create("compose-editor-banners");
    $host.set_find_results("form.compose-editor", $form);
    $form.set_find_results("textarea.message-textarea", $textarea);
    $form.set_find_results(".compose-editor-submit", $submit);
    $form.set_find_results(".compose-editor-status", $status);
    $form.set_find_results(".compose-editor-banners", $banners);
    return {$host, $form, $textarea, $submit, $status, $banners};
}

run_test("instance editor sends through the shared compose lifecycle", () => {
    build_args = undefined;
    send_args = undefined;
    finalized = undefined;
    draft_updates = [];
    const {$host, $textarea, $submit} = make_editor_dom();
    $textarea.val("Thread reply");
    let changed_to;
    let sent_data;
    const editor = compose_editor.create({
        $host,
        recipient: {type: "stream", stream_id: 17, topic: "design"},
        thread_root_message_id: 901,
        on_draft_change(content) {
            changed_to = content;
        },
        on_sent(sent_message, data) {
            sent_data = {sent_message, data};
        },
    });

    assert.equal(editor.send(), true);
    assert.deepEqual(build_args, {
        recipient: {type: "stream", stream_id: 17, topic: "design"},
        content: "Thread reply",
        draft_id: "draft-901",
        thread_root_message_id: 901,
    });
    assert.deepEqual(send_args.message_data, message_data);
    assert.equal($textarea.val(), "");
    assert.equal(changed_to, "");
    // After send + clear the editor is empty, so the submit greys out
    // (same contract as the main compose).
    assert.equal($submit.prop("disabled"), true);
    assert.equal(sent_data.data.id, 902);
    assert.equal(finalized.data.id, 902);
    assert.deepEqual(draft_updates[0], {
        draft_id: undefined,
        thread_root_message_id: 901,
        recipient: {type: "stream", stream_id: 17, topic: "design"},
        content: "Thread reply",
        is_sending_saving: true,
    });
    editor.destroy();
});

run_test("instance editor preserves an empty draft instead of sending", () => {
    build_args = undefined;
    send_args = undefined;
    const {$host, $textarea, $status} = make_editor_dom();
    $textarea.val("   ");
    const editor = compose_editor.create({
        $host,
        recipient: {type: "stream", stream_id: 17, topic: "design"},
    });
    assert.equal(editor.send(), false);
    assert.equal(build_args, undefined);
    assert.equal(send_args, undefined);
    assert.equal($status.text(), "translated: Enter a message to send.");
    editor.destroy();
});

run_test("instance formatting owns the click before the global compose delegate", () => {
    formatting_calls = [];
    const {$host, $form, $textarea} = make_editor_dom();
    const editor = compose_editor.create({
        $host,
        recipient: {type: "stream", stream_id: 17, topic: "design"},
    });
    const $button = $.create("instance-formatting-button");
    $button.attr("data-format-type", "bold");
    let propagation_stopped = false;
    const event = {
        currentTarget: $button.get(0),
        preventDefault: noop,
        stopPropagation() {
            propagation_stopped = true;
        },
    };

    // These are the two real delegation levels: the instance form first and
    // the body-level primary-compose delegate only if the event still bubbles.
    const instance_delegate = $form.get_on_handler("click.composeEditor", ".formatting_button");
    let global_delegate_calls = 0;
    $("body").on("click", ".formatting_button", () => {
        global_delegate_calls += 1;
    });
    const global_delegate = $("body").get_on_handler("click", ".formatting_button");
    instance_delegate(event);
    if (!propagation_stopped) {
        global_delegate(event);
    }

    assert.equal(formatting_calls.length, 1);
    assert.equal(formatting_calls[0].$textarea.get(0), $textarea.get(0));
    assert.equal(formatting_calls[0].format_type, "bold");
    assert.equal(global_delegate_calls, 0);
    editor.destroy();
});
