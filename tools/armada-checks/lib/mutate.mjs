// Deliberate breakage of the application under test, to prove that an
// assertion can fail.
//
// With ARMADA_CHECKS_MUTATE=<name> every page of the run is served the
// development bundle with the named edit applied: the scripts are rewritten
// in the browser as they arrive (CDP Fetch), so nothing on disk and nothing
// another browser sees is changed. `lib/prove.mjs` runs each mutation and
// requires the assertions it lists to turn red.
//
// `find` is matched against the compiled development bundle, which keeps
// function and property names; `min` is how many places must change for the
// mutation to count as applied. `only` limits a proving run to the scenarios
// that hold the assertions in `breaks`.

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";

const ident = String.raw`[\w$]+`;

const BUILT_IN = {
    // ---- feed-semantics ------------------------------------------------
    "code-scroll-counts-as-user-scroll": {
        about: "every scroll the application makes itself is treated as one the user made",
        check: "feed-semantics",
        only: "code-scroll",
        breaks: ["feed-semantics/code-scroll.nothing-marked-read"],
        edits: [
            {
                find: /\.set_update_selection_on_next_scroll\((?:false|update_selection_on_scroll)\)/g,
                replace: ".set_update_selection_on_next_scroll(true)",
                min: 3,
            },
        ],
    },
    "autoscroll-ignores-the-pointer": {
        about: "a new message always scrolls the feed by its full height, as if the user had sent it",
        check: "feed-semantics",
        only: "code-scroll",
        breaks: ["feed-semantics/code-scroll.selection-unchanged"],
        edits: [
            {
                find: /_maybe_autoscroll\(new_messages_height, sent_by_me\) \{/g,
                replace: "_maybe_autoscroll(new_messages_height, sent_by_me) { sent_by_me = true;",
                min: 1,
            },
        ],
    },
    "keys-do-not-move-the-pointer": {
        about: "j, k and the arrows no longer select the neighbouring message",
        check: "feed-semantics",
        only: "pointer",
        breaks: [
            "feed-semantics/pointer.keys.k.selected-id",
            "feed-semantics/pointer.keys.ArrowUp.selected-id",
            "feed-semantics/pointer.keys.j.selected-id",
            "feed-semantics/pointer.keys.ArrowDown.selected-id",
        ],
        edits: [
            {
                find: /function go_to_row\(msg_id\) \{/g,
                replace: "function go_to_row(msg_id) { return;",
                min: 1,
            },
        ],
    },
    "home-and-end-do-nothing": {
        about: "Home and End no longer jump to the oldest and newest message",
        check: "feed-semantics",
        only: "pointer",
        breaks: [
            "feed-semantics/pointer.keys.Home.selected-id",
            "feed-semantics/pointer.keys.End.selected-id",
        ],
        edits: [
            {find: /function to_home\(\) \{/g, replace: "function to_home() { return;", min: 1},
            {find: /function to_end\(\) \{/g, replace: "function to_end() { return;", min: 1},
        ],
    },
    "page-keys-move-two-lines": {
        about: "PgUp, PgDn and Space scroll 40 px instead of a page",
        check: "feed-semantics",
        only: "pointer",
        breaks: ["feed-semantics/pointer.page-keys.scroll-a-page"],
        edits: [
            {
                find: /function amount_to_paginate\(\) \{/g,
                replace: "function amount_to_paginate() { return 40;",
                min: 1,
            },
        ],
    },
    "pointer-is-left-behind": {
        about: "the selection no longer follows a scroll that takes it out of view",
        check: "feed-semantics",
        only: "pointer",
        // `keep-in-view-at-other-marks` is the edit that gives PageDown
        // and Space a failing value.
        breaks: [
            "feed-semantics/pointer.keep-in-view",
            "feed-semantics/pointer.keys.PageUp.selected-id",
        ],
        edits: [
            {
                find: /function keep_pointer_in_view\(\) \{/g,
                replace: "function keep_pointer_in_view() { return;",
                min: 1,
            },
        ],
    },
    "recentre-at-other-marks": {
        about: "the pointer is recentred at 1/4 and 1/3 instead of 1/2 and 1/7",
        check: "feed-semantics",
        only: "pointer",
        breaks: ["feed-semantics/pointer.recenter"],
        edits: [
            {find: /viewport_info, 1 \/ 2\)/g, replace: "viewport_info, 1 / 4)", min: 1},
            {find: /viewport_info, 1 \/ 7\)/g, replace: "viewport_info, 1 / 3)", min: 1},
        ],
    },
    "tall-messages-are-skipped": {
        about: "no message counts as long, so j leaves a tall message without paging through it",
        check: "feed-semantics",
        only: "pointer",
        breaks: ["feed-semantics/pointer.tall-message-clamp"],
        edits: [
            {
                find: /function is_long_message\(message_height\) \{/g,
                replace: "function is_long_message(message_height) { return false;",
                min: 1,
            },
        ],
    },
    "a-wheel-marks-the-whole-topic-read": {
        about: "a movement of the wheel marks every message of the view read, wherever it stops",
        check: "feed-semantics",
        only: "read",
        breaks: ["feed-semantics/read.fully-visible-only"],
        edits: [
            {
                // Opening the topic still marks nothing, so the
                // assertion's precondition (something unread at the
                // start, something marked by the scroll) holds and it
                // fails on its own count.
                find: new RegExp(
                    String.raw`function process_visible\(\) \{\s*if \((${ident})\.current !== undefined &&`,
                    "g",
                ),
                replace:
                    'function process_visible() { if (!window.armada_checks_wheel) { window.armada_checks_wheel = true; window.addEventListener("wheel", () => { setTimeout(() => { if ($1.current !== undefined) { notify_server_messages_read($1.current.all_messages()); } }, 200); }); } if ($1.current !== undefined &&',
                min: 1,
            },
        ],
    },
    "keep-in-view-at-other-marks": {
        about: "a scroll takes the selection along at 40% and 60% of the readable area instead of 10% and 90%",
        check: "feed-semantics",
        only: "pointer",
        breaks: [
            "feed-semantics/pointer.keep-in-view",
            "feed-semantics/pointer.keys.PageUp.selected-id",
            "feed-semantics/pointer.keys.PageDown.selected-id",
            "feed-semantics/pointer.keys.Space.selected-id",
        ],
        edits: [
            {
                find: /\(?1 \/ 10\)? \* info\.visible_height/g,
                replace: "(4 / 10) * info.visible_height",
                min: 1,
            },
            {
                find: /\(?9 \/ 10\)? \* info\.visible_height/g,
                replace: "(6 / 10) * info.visible_height",
                min: 1,
            },
        ],
    },
    "reading-ignores-overlay-and-focus": {
        about: "messages are marked read under an overlay and in an unfocused window",
        check: "feed-semantics",
        only: "read",
        breaks: ["feed-semantics/read.not-under-overlay", "feed-semantics/read.not-when-unfocused"],
        edits: [
            {
                find: /function viewport_is_visible_and_focused\(\) \{/g,
                replace: "function viewport_is_visible_and_focused() { return true;",
                min: 1,
            },
        ],
    },
    "near-view-reads-at-once": {
        about: "a /near/ view marks messages read before the user has scrolled",
        check: "feed-semantics",
        only: "read",
        breaks: ["feed-semantics/read.near-resumes-after-user-scroll"],
        edits: [
            {
                find: /opts\.reading_prevented \?\? this\.data\.filter\.is_conversation_view_with_near\(\)/g,
                replace: "opts.reading_prevented ?? false",
                min: 1,
            },
        ],
    },
    "new-messages-never-scroll": {
        about: "the feed does not follow a new message, the user's own included",
        check: "feed-semantics",
        only: "autoscroll",
        breaks: [
            "feed-semantics/autoscroll.at-bottom.follows",
            "feed-semantics/autoscroll.own-send.jumps",
        ],
        edits: [
            {
                find: /_maybe_autoscroll\(new_messages_height, sent_by_me\) \{/g,
                replace: "_maybe_autoscroll(new_messages_height, sent_by_me) { return false;",
                min: 1,
            },
        ],
    },
    "new-messages-always-scroll": {
        about: "a new message scrolls the feed even when the reader has scrolled up",
        check: "feed-semantics",
        only: "autoscroll",
        breaks: [
            "feed-semantics/autoscroll.scrolled-up.stays",
            "feed-semantics/anchor.append.offset",
        ],
        edits: [
            {
                find: new RegExp(
                    String.raw`const started_scrolled_up = this\.manages_primary_viewport && ${ident}\.is_scrolled_up\(\);`,
                    "g",
                ),
                replace:
                    "const started_scrolled_up = false; if (messages_are_new && this.manages_primary_viewport) { window.scrollBy(0, 300); }",
                min: 1,
            },
        ],
    },
    "prepend-loses-the-place": {
        about: "older messages are inserted without restoring the selected row's position",
        check: "feed-semantics",
        only: "fetch",
        breaks: ["feed-semantics/anchor.prepend.offset"],
        edits: [
            {
                find: /list\.view\.set_message_offset\(orig_scrolltop_offset\);/g,
                replace: "",
                min: 1,
            },
        ],
    },
    "history-is-never-fetched": {
        about: "the feed stops asking for older and newer messages",
        check: "feed-semantics",
        only: "fetch",
        breaks: ["feed-semantics/fetch.older-near-top", "feed-semantics/fetch.newer-near-bottom"],
        edits: [
            {
                find: /should_fetch_older_messages\(\) \{/g,
                replace: "should_fetch_older_messages() { return false;",
                min: 1,
            },
            {
                find: /should_fetch_newer_messages\(\) \{/g,
                replace: "should_fetch_newer_messages() { return false;",
                min: 1,
            },
        ],
    },
    "history-forgets-the-place": {
        about: "the selected message and its offset are not saved with the history entry",
        check: "feed-semantics",
        only: "history",
        breaks: [
            "feed-semantics/history.back.selection-and-offset",
            "feed-semantics/history.forward.selection-and-offset",
        ],
        edits: [
            {
                find: /function _save_narrow_state\(\) \{/g,
                replace: "function _save_narrow_state() { return;",
                min: 1,
            },
        ],
    },
    "scrolling-never-marks-read": {
        about: "reaching the end of a topic no longer marks it read",
        check: "feed-semantics",
        only: "code-scroll",
        breaks: ["feed-semantics/code-scroll.user-scroll-marks-read"],
        edits: [
            {
                find: /function process_visible\(\) \{/g,
                replace: "function process_visible() { return;",
                min: 1,
            },
        ],
    },
    "topic-groups-are-merged": {
        about: "every message counts as having the same recipient as the one before it",
        check: "feed-semantics",
        only: "history",
        breaks: ["feed-semantics/multi-topic.group-per-topic"],
        edits: [
            {
                // The grouping loop calls util.same_recipient, whose
                // function is named util_same_recipient in the bundle;
                // message_list_view.ts has a same_recipient of its own
                // that only joins two rendered batches.
                find: /function util_same_recipient\(a, b\) \{/g,
                replace: "function util_same_recipient(a, b) { return true;",
                min: 1,
            },
        ],
    },

    // ---- echo-semantics ------------------------------------------------
    "echo-forgets-which-row-it-sent": {
        about: "the server's copy of a sent message is no longer matched to its local echo",
        check: "echo-semantics",
        only: "echo",
        breaks: ["echo-semantics/echo.id-swap.position"],
        edits: [
            {
                find: /if \(event\.local_message_id\) \{/g,
                replace: "if (false) {",
                min: 1,
            },
        ],
    },
    "no-local-echo": {
        about: "nothing is shown until the server answers",
        check: "echo-semantics",
        only: "echo",
        breaks: ["echo-semantics/echo.first-frame", "echo-semantics/echo.rapid.order"],
        edits: [
            {
                find: /let try_deliver_locally = \(([^)]*)\) => \{/g,
                replace: "let try_deliver_locally = ($1) => { return undefined;",
                min: 1,
            },
        ],
    },
    "failed-send-is-dropped": {
        about: "a message the server refused is removed instead of staying with Retry",
        check: "echo-semantics",
        only: "failed",
        breaks: ["echo-semantics/echo.failed.stays-with-retry"],
        edits: [
            {
                find: /show_message_failed\(message_id, error_response\);/g,
                replace: "abort_message(message);",
                min: 1,
            },
        ],
    },
    "drafts-are-not-saved": {
        about: "text in the field is not saved as a draft",
        check: "echo-semantics",
        only: "unsent",
        breaks: ["echo-semantics/echo.unsent.survives-reload"],
        edits: [
            {
                find: /let update_draft = \(opts = \{\}\) => \{/g,
                replace: "let update_draft = (opts = {}) => { return undefined;",
                min: 1,
            },
        ],
    },
    "edits-are-ignored": {
        about: "an update_message event no longer changes what the row shows",
        check: "echo-semantics",
        only: "edit",
        breaks: ["echo-semantics/edit.in-place"],
        edits: [
            {
                find: /function update_messages\(events\) \{/g,
                replace: "function update_messages(events) { return;",
                min: 1,
            },
        ],
    },
    "echo-keeps-the-client-rendering": {
        about: "the server's rendering no longer replaces the client's after a send",
        check: "echo-semantics",
        only: "echo",
        breaks: ["echo-semantics/echo.server-html-final"],
        edits: [
            {
                find: /if \(client_message\.content !== message\.content\) \{/g,
                replace: "if (false) {",
                min: 1,
            },
        ],
    },
    "steering-is-not-addressed": {
        about: "a message sent with the agent selected no longer says so in its request",
        check: "echo-semantics",
        only: "steer",
        breaks: ["echo-semantics/steer.request-addresses-live-run"],
        edits: [
            {
                find: /pear_followup: recipient\.type === "stream" && /g,
                replace: "pear_followup: false && ",
                min: 1,
            },
        ],
    },

    // ---- keyboard-semantics --------------------------------------------
    "return-during-composition-sends": {
        about: "Return is treated as Return while an input method is composing",
        check: "keyboard-semantics",
        only: "field",
        breaks: ["keyboard-semantics/field.ime-return-inert"],
        edits: [
            {
                find: /const isComposing = event\.originalEvent\?\.isComposing \?\? false;/g,
                replace: "const isComposing = false;",
                min: 1,
            },
        ],
    },
    "return-does-not-send": {
        about: "Return in the field no longer sends",
        check: "keyboard-semantics",
        only: "field",
        breaks: ["keyboard-semantics/field.return-sends"],
        edits: [
            {
                find: /function should_enter_send\(e\) \{/g,
                replace: "function should_enter_send(e) { return false;",
                min: 1,
            },
        ],
    },
    "shift-return-sends": {
        about: "Shift-Return sends instead of breaking the line",
        check: "keyboard-semantics",
        only: "field",
        breaks: ["keyboard-semantics/field.shift-return-newline"],
        edits: [
            {
                find: /this_enter_sends = !has_modifier_key && /g,
                replace: "this_enter_sends = ",
                min: 1,
            },
        ],
    },
    "escape-skips-popovers": {
        about: "Esc no longer closes an open menu before anything else",
        check: "keyboard-semantics",
        only: "esc",
        breaks: ["keyboard-semantics/esc.order"],
        edits: [
            {
                find: new RegExp(
                    String.raw`if \(${ident}\.any_active\(\)\) \{\s*${ident}\.hide_all\(\);\s*return true;\s*\}`,
                    "g",
                ),
                replace: "",
                min: 1,
            },
        ],
    },
    "escape-leaves-the-composer-open": {
        about: "Esc with nothing above the open composer no longer closes it",
        check: "keyboard-semantics",
        only: "esc",
        breaks: ["keyboard-semantics/esc.closes-open-composer"],
        edits: [
            {
                // The branch taken when no field has the focus; the one
                // for a focused field has other statements before cancel.
                find: new RegExp(
                    String.raw`if \(${ident}\.composing\(\)\) \{\s*${ident}\.cancel\(\);\s*return true;\s*\}`,
                    "g",
                ),
                replace: "",
                min: 1,
            },
        ],
    },
    "n-and-p-do-nothing": {
        about: "N and P no longer go to the next unread topic and direct message",
        check: "keyboard-semantics",
        only: "next-unread",
        breaks: ["keyboard-semantics/keys.next-unread"],
        edits: [
            {
                find: /function narrow_to_next_topic\(opts\) \{/g,
                replace: "function narrow_to_next_topic(opts) { return;",
                min: 1,
            },
            {
                find: /function narrow_to_next_pm_string\(opts = \{\}\) \{/g,
                replace: "function narrow_to_next_pm_string(opts = {}) { return;",
                min: 1,
            },
        ],
    },
    "tab-stays-in-the-field": {
        about: "Tab in the field no longer moves to the Send button",
        check: "keyboard-semantics",
        only: "tab",
        breaks: ["keyboard-semantics/tab.first-stop-is-send"],
        edits: [
            {
                find: /\("#compose-send-button"\)\.trigger\("focus"\)/g,
                replace: '("#compose-send-button").trigger("armada-checks-nothing")',
                min: 1,
            },
        ],
    },
    "dropped-files-are-ignored": {
        about: "a file dropped on the application is no longer picked up",
        check: "keyboard-semantics",
        only: "mac",
        breaks: ["keyboard-semantics/mac.file-drop-attaches"],
        edits: [
            {
                find: /\.on\("drop", (event|e) => \{/g,
                replace: '.on("armada-checks-nothing", $1 => {',
                min: 2,
            },
        ],
    },

    // ---- send-cost -----------------------------------------------------
    "every-send-needs-a-scroll": {
        about: 'every own send shows "Sent! Scroll down to view your message." above the composer, as every send to the old, long probe topic did',
        check: "send-cost",
        only: "plain",
        breaks: ["send-cost/plain.compose-height-changes"],
        edits: [
            {
                find: /\.notify_local_mixes\(messages, need_user_to_scroll, \{/g,
                replace: ".notify_local_mixes(messages, true, {",
                min: 1,
            },
        ],
    },

    // ---- stream-cost ---------------------------------------------------
    "stream-edits-are-ignored": {
        about: "an update_message event no longer changes what the row shows",
        check: "stream-cost",
        only: "400wps",
        breaks: [
            "stream-cost/400wps.first-words.frames-to-paint",
            "stream-cost/400wps.settle.text-equal",
        ],
        edits: [
            {
                find: /function update_messages\(events\) \{/g,
                replace: "function update_messages(events) { return;",
                min: 1,
            },
        ],
    },
    "stream-thrashes": {
        about: "every edit is followed by thirty forced layouts and 250 ms of script",
        check: "stream-cost",
        only: "400wps",
        breaks: [
            "stream-cost/400wps.layouts-per-frame",
            "stream-cost/400wps.long-tasks",
            "stream-cost/400wps.script-ms-per-frame",
        ],
        edits: [
            {
                find: /function update_messages\(events\) \{/g,
                replace:
                    'function update_messages(events) { for (let i = 0; i < 30; i += 1) { setTimeout(() => { const list = document.querySelector(".focused-message-list"); list.append(document.createComment("armada-checks")); list.style.paddingBottom = `${i % 2}px`; void document.body.offsetHeight; }, i); } const until = performance.now() + 250; while (performance.now() < until) { /* busy */ }',
                min: 1,
            },
        ],
    },

    // ---- parts-corpus --------------------------------------------------
    "client-markdown-loses-code-fences": {
        about: "the client's Markdown drops code fences, so code is rendered as prose",
        check: "parts-corpus",
        only: "corpus",
        breaks: ["parts-corpus/cases-differing-in-kind"],
        edits: [
            {
                find: /function render\(raw_content, helper_config\) \{/g,
                replace:
                    'function render(raw_content, helper_config) { raw_content = raw_content.replaceAll("```", "").replaceAll("~~~", "");',
                min: 1,
            },
        ],
    },
};

// A package that moves a pinned behaviour to new code writes the equivalent
// breakage of its own code in tools/armada-checks/mutations/<package>.mjs
// (PLAN.md, rule 11): a file exports MUTATIONS, and an entry replaces the
// built-in one of the same name. Files load in name order; a later file
// replaces an earlier one's entry too.
const OVERRIDES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../mutations");

async function load_overrides() {
    const merged = {...BUILT_IN};
    const replaced = {};
    const files = fs.existsSync(OVERRIDES)
        ? fs
              .readdirSync(OVERRIDES)
              .filter((file) => file.endsWith(".mjs"))
              .toSorted()
        : [];
    for (const file of files) {
        const module = await import(pathToFileURL(path.join(OVERRIDES, file)).href);
        for (const [name, mutation] of Object.entries(module.MUTATIONS ?? {})) {
            merged[name] = mutation;
            replaced[name] = file;
        }
    }
    return {merged, replaced};
}

const loaded = await load_overrides();

export const MUTATIONS = loaded.merged;

// Which mutation each package's file replaced (or added), by name.
export const REPLACED_BY = loaded.replaced;

export function requested_mutation() {
    const name = process.env.ARMADA_CHECKS_MUTATE;
    if (!name) {
        return undefined;
    }
    const mutation = MUTATIONS[name];
    if (!mutation) {
        throw new Error(`no such mutation: ${name} (have: ${Object.keys(MUTATIONS).join(", ")})`);
    }
    return {name, ...mutation, applied: mutation.edits.map(() => 0)};
}

// Returns the rewritten script, or undefined when nothing in it matched.
export function mutate_script(mutation, text) {
    let changed = false;
    let result = text;
    for (const [index, edit] of mutation.edits.entries()) {
        const matches = result.match(edit.find);
        if (matches) {
            mutation.applied[index] += matches.length;
            result = result.replace(edit.find, edit.replace);
            changed = true;
        }
    }
    return changed ? result : undefined;
}

export function mutation_applied(mutation) {
    return mutation.edits.every((edit, index) => mutation.applied[index] >= edit.min);
}
