"use strict";

const assert = require("node:assert/strict");

const render_uploaded_files_list = require("../templates/settings/uploaded_files_list.hbs");

const {zrequire} = require("./lib/namespace.cjs");
const {make_real_dom} = require("./lib/real_dom.cjs");
const {run_test} = require("./lib/test.cjs");

const attachments_ui = zrequire("attachments_ui");
const {initialize_user_settings} = zrequire("user_settings");

initialize_user_settings({user_settings: {twenty_four_hour_time: false}});

function upload(id, {message_ids = [], post_ids = []} = {}) {
    return attachments_ui.format_attachment_data({
        id,
        name: `notes-${id}.txt`,
        path_id: `2/ab/${id}/notes.txt`,
        size: 6,
        create_time: 1_700_000_000,
        message_ids,
        post_ids,
    });
}

// Where the row says the upload is used: each link under "Mentioned in".
function mentioned_in(attachment) {
    const row_html = render_uploaded_files_list({attachment});
    const dom = make_real_dom(`<table><tbody>${row_html}</tbody></table>`);
    const cell = dom.find("tr").cells[2];
    return [...cell.querySelectorAll("a")].map((link) => [
        link.getAttribute("href"),
        link.textContent.trim(),
    ]);
}

run_test("an upload's pages posts are listed beside its messages", () => {
    // The test translator marks what it translates; a post's id reads
    // as it is, not as a formatted number.
    assert.deepEqual(mentioned_in(upload(1, {message_ids: [102], post_ids: [7, 1234]})), [
        ["/#narrow/id/102", "#102"],
        ["/#post/7", "translated: Post #7"],
        ["/#post/1234", "translated: Post #1234"],
    ]);
    assert.deepEqual(mentioned_in(upload(2, {post_ids: [7]})), [
        ["/#post/7", "translated: Post #7"],
    ]);
    assert.deepEqual(mentioned_in(upload(3)), []);
});

run_test("sorting by Mentioned in puts post-only uploads among used ones", () => {
    const uploads = [
        upload(1),
        upload(2, {post_ids: [7]}),
        upload(3, {message_ids: [90]}),
        upload(4, {message_ids: [50], post_ids: [8]}),
        upload(5),
    ];
    const order = (list) => list.toSorted(attachments_ui.sort_mentioned_in).map(({id}) => id);
    assert.deepEqual(order(uploads), [4, 3, 2, 1, 5]);
    assert.deepEqual(order(uploads.toReversed()), [4, 3, 2, 5, 1]);
});
