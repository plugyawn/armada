"use strict";

// The interim beta's personal pages (web/src/armada/beta/pages.js,
// contract C5), with a real DOM and a stand-in server: a person's page
// lists their line and posts newest first; a post is drawn from the
// server's rendering and says when the agent drafted it; my own page
// has the editor (draft or publish, edit, delete, a preview), keeps
// what I did not save, and takes the armada_post event live. The beta
// reads the kit's window globals when it draws; the few it needs here
// are stand-ins that draw nothing of their own. Deleted with
// web/src/armada/beta/.

const assert = require("node:assert/strict");

const {clock, mock_esm, set_global, zrequire} = require("../lib/namespace.cjs");
const real_dom = require("../lib/real_dom.cjs");
const {run_test} = require("../lib/test.cjs");

const dom = real_dom.make_real_dom(
    `<div id="beta-data"></div><main><div class="pane"><div class="lane" id="page"></div></div></main>`,
);
set_global("window", dom.window);
set_global("location", dom.window.location);
set_global("history", dom.window.history);
set_global("localStorage", dom.window.localStorage);
set_global("getSelection", () => dom.window.getSelection());
set_global("requestAnimationFrame", (fn) => setTimeout(fn, 0));
set_global("addEventListener", (...args) => dom.window.addEventListener(...args));
set_global("removeEventListener", (...args) => dom.window.removeEventListener(...args));
set_global("FormData", dom.window.FormData);
const DOMParser = dom.window.DOMParser;
const noop = () => {};
const said = [];
set_global("M", {
    T: {},
    SLOW: 1,
    reduced: () => true,
    release: noop,
    appear: noop,
    say(_element, text) {
        said.push(text);
    },
    leave(element, done) {
        element.remove();
        if (done) {
            done();
        }
    },
});
set_global("S", {play: noop});
set_global("host", noop);
set_global("fig", String);
set_global("ic", (name) => `<svg class="ic ${name}"></svg>`);
set_global("mk", (name) => `<svg class="mk ${name}"></svg>`);
set_global(
    "face",
    (spec, cls) => `<i class="av ${cls || ""}">${spec.split(" ")[0].split(":")[0]}</i>`,
);

const ME = 3;
const IAGO = 5;
const AGENT = 9;
// Noon UTC, so the dates read the same in every time zone.
const OCT_15 = Date.UTC(2026, 9, 15, 12) / 1000;
const OCT_9 = Date.UTC(2026, 9, 9, 12) / 1000;

// The stand-in server: each request is kept, and answered from the table
// a test sets.
const requests = [];
let answers = [];
set_global("fetch", async (url, init = {}) => {
    const method = init.method ?? "GET";
    const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) : null;
    requests.push({method, url, body});
    const answer = answers.find(([m, pattern]) => m === method && pattern.test(url.split("?")[0]));
    assert.ok(answer, `no stand-in answer for ${method} ${url}`);
    const reply = answer[2]({url, body});
    return {
        ok: true,
        status: reply.status ?? 200,
        headers: {get: () => null},
        json: async () => reply,
    };
});
const success = (data = {}) => ({result: "success", msg: "", ...data});

// The frame around the page is not drawn here: opening a place (routing.js)
// draws only the page, and every opening is kept; the header and the
// column draw nothing; dialogs go into <main>.
let opened = [];
const routing = mock_esm("../../src/armada/beta/routing.js", {
    openPlace(place, options) {
        opened.push([place.key, options ?? {}]);
        beta_state.state.place = place;
        pages.openPage(place);
    },
});
mock_esm("../../src/armada/beta/header.js", {drawHeader: noop});
mock_esm("../../src/armada/beta/column.js", {drawSide: noop});
mock_esm("../../src/armada/beta/frame.js", {main: document.querySelector("main")});

const beta_state = zrequire("armada/beta/state.js");
const pages = zrequire("armada/beta/pages.js");

beta_state.state.ME = ME;
beta_state.state.meName = "Desdemona";
beta_state.armadaBots.add(AGENT);
for (const [id, name] of [
    [ME, "Desdemona"],
    [IAGO, "Iago Smith"],
    [AGENT, "Onix"],
]) {
    beta_state.people.set(id, {
        id,
        name,
        email: `${id}@example.com`,
        bot: id === AGENT,
        active: true,
    });
}
pages.wirePages();

const IAGO_PAGE = {
    user_id: IAGO,
    bio: "Builds the parts of click nobody notices until they break.",
    posts: [
        {id: 21, title: "Option groups in --help", created: OCT_15, updated: OCT_15, draft: false},
        {id: 20, title: "The case for boring CLIs", created: OCT_9, updated: OCT_9, draft: false},
    ],
};
const MY_PAGE = {
    user_id: ME,
    bio: "",
    posts: [
        {
            id: 31,
            title: "Write-up of the option groups",
            created: OCT_15,
            updated: OCT_15,
            draft: true,
        },
        {id: 30, title: "Notes on Rich", created: OCT_9, updated: OCT_9, draft: false},
    ],
};
const DRAFTED = {
    id: 31,
    user_id: ME,
    title: "Write-up of the option groups",
    content: "Hello **there**\n\n```\nx = 1\n```",
    rendered:
        '<p>Hello <strong>there</strong></p>\n<div class="codehilite"><pre><span></span><code>x = 1\n</code></pre></div>',
    created: OCT_15,
    updated: OCT_15,
    draft: true,
    drafted_by_agent: true,
};
const IAGO_POST = {
    ...DRAFTED,
    id: 21,
    user_id: IAGO,
    title: "Option groups in --help",
    draft: false,
    drafted_by_agent: false,
};

function serve(extra = []) {
    requests.length = 0;
    answers = [
        ...extra,
        [
            "GET",
            /^\/json\/armada\/pages$/,
            () =>
                success({
                    pages: [
                        {user_id: IAGO, post_count: 2, updated: OCT_15},
                        {user_id: ME, post_count: 1, updated: OCT_15},
                    ],
                }),
        ],
        ["GET", /^\/json\/armada\/pages\/5$/, () => success(structuredClone(IAGO_PAGE))],
        ["GET", /^\/json\/armada\/pages\/3$/, () => success(structuredClone(MY_PAGE))],
        ["GET", /^\/json\/armada\/posts\/31$/, () => success({post: structuredClone(DRAFTED)})],
        ["GET", /^\/json\/armada\/posts\/21$/, () => success({post: structuredClone(IAGO_POST)})],
        [
            "GET",
            /^\/json\/armada\/posts\/77$/,
            () =>
                success({
                    post: {
                        ...structuredClone(DRAFTED),
                        id: 77,
                        title: "Grouped help",
                        content: "It **works**.",
                        rendered: "<p>It <strong>works</strong>.</p>",
                        draft: false,
                        drafted_by_agent: false,
                    },
                }),
        ],
        ["POST", /^\/json\/armada\/posts$/, () => success({id: 77})],
        ["PATCH", /^\/json\/armada\/posts\/\d+$/, () => success()],
        ["DELETE", /^\/json\/armada\/posts\/\d+$/, () => success()],
        ["PATCH", /^\/json\/armada\/pages\/me$/, () => success()],
        [
            "POST",
            /^\/json\/messages\/render$/,
            () => success({rendered: "<p>Rendered by the server</p>"}),
        ],
    ];
}
const writes = () => requests.filter((r) => r.method !== "GET");

// Every promise the page waits on resolves through promises alone.
async function settle() {
    for (let i = 0; i < 50; i += 1) {
        await Promise.resolve();
    }
}
const view = () => document.querySelector("#page");
const buttons = () => new Set([...view().querySelectorAll("button, a")].map((b) => b.textContent));
const button = (text) =>
    [...view().querySelectorAll("button, a")].find((b) => b.textContent === text);

async function open(key) {
    serve();
    routing.openPlace(pages.pageFromKey(key));
    await settle();
}

run_test("a page's places and addresses", () => {
    const page = pages.pageFromKey("page:5");
    assert.equal(page.kind, "page");
    assert.equal(page.view, "index");
    assert.equal(page.uid, IAGO);
    assert.equal(pages.pageHash(page), "#page/5");
    // the agent has no page; a post needs its number
    assert.equal(pages.pageFromKey(`page:${AGENT}`), null);
    assert.equal(pages.pageFromKey("page:404"), null);
    assert.equal(pages.pageFromKey("post:new"), null);
    assert.equal(pages.pageFromKey("post:12").view, "post");
    assert.equal(pages.pageFromKey("edit:new").view, "edit");
    for (const hash of ["#page/5", "#post/12", "#edit/new", "#edit/12"]) {
        assert.equal(pages.pageHash(pages.pageFromHash(hash)), hash);
    }
    assert.equal(pages.pageFromHash("#post/abc"), null);
    assert.equal(pages.pageFromHash("#c/1/topic"), null);
});

run_test("the column lists the pages once the server says their order", async () => {
    let answer_index;
    serve([
        [
            "GET",
            /^\/json\/armada\/pages$/,
            () => new Promise((resolve) => (answer_index = resolve)),
        ],
    ]);
    routing.openPlace(pages.pageFromKey("page:5"));
    await settle();
    const rows = () =>
        [
            ...new DOMParser()
                .parseFromString(pages.pagesSideHTML(), "text/html")
                .querySelectorAll("li .t"),
        ].map((t) => t.textContent);
    // rows drawn in another order first would move when the server's came
    assert.deepEqual(rows(), []);
    answer_index(success({pages: [{user_id: IAGO, post_count: 2, updated: OCT_15}]}));
    await settle();
    assert.deepEqual(rows(), ["Iago Smith"]);
});

run_test("someone's page: their line and posts newest first, nothing to write with", async () => {
    opened = [];
    await open("page:5");
    const text = view().textContent;
    assert.match(text, /Builds the parts of click/);
    const titles = [...view().querySelectorAll("li a")].map((a) => a.textContent);
    assert.deepEqual(titles, ["Option groups in --help", "The case for boring CLIs"]);
    assert.deepEqual(
        [...view().querySelectorAll("li time")].map((t) => t.textContent),
        ["Oct 15, 2026", "Oct 9, 2026"],
    );
    assert.match(text, /2 posts/);
    assert.match(text, /Readable by everyone here and by Onix/);
    assert.ok(!buttons().has("New post"));
    assert.equal(view().querySelector("input, textarea, button"), null);
    assert.equal(button("Option groups in --help").getAttribute("href"), "#post/21");
    // the column lists everyone's pages, mine first, this one current
    const side = new DOMParser().parseFromString(pages.pagesSideHTML(), "text/html");
    const rows = [...side.querySelectorAll("li")];
    assert.deepEqual(
        rows.map((li) => li.querySelector(".t").textContent),
        ["Your page", "Iago Smith"],
    );
    assert.equal(rows[1].getAttribute("aria-current"), "page");
    assert.equal(rows[1].dataset.k, "page:5");
});
run_test(
    "my page: New post, my drafts said to be mine alone, my line edited in place",
    async () => {
        opened = [];
        await open(`page:${ME}`);
        assert.ok(buttons().has("New post"));
        assert.match(view().textContent, /1 post, 1 draft only you can see/);
        const draft = [...view().querySelectorAll("li")].find((li) =>
            /Write-up/.test(li.textContent),
        );
        assert.match(draft.textContent, /Draft$/);
        // the line: pressed, a field; a word still being composed is not saved; Return saves
        const line = button("Add a line about yourself");
        dom.click(line);
        const field = view().querySelector("textarea");
        assert.equal(document.activeElement, field);
        // the line stays in its place, out of reach, holding the words as typed, so the posts below do not move
        assert.ok(line.isConnected);
        assert.equal(line.tabIndex, -1);
        dom.type(field, "W");
        assert.equal(line.textContent, "W");
        field.value = "";
        assert.equal(dom.compose_with_input_method(field, "Wri"), true);
        assert.equal(writes().length, 0, "Return that commits a composed word saves nothing");
        field.value = "Writes about terminals.";
        dom.press("Enter", {target: field});
        await settle();
        assert.deepEqual(writes(), [
            {method: "PATCH", url: "/json/armada/pages/me", body: {bio: "Writes about terminals."}},
        ]);
        assert.ok(buttons().has("Writes about terminals."));
        // Esc puts it back untouched
        dom.click(button("Writes about terminals."));
        view().querySelector("textarea").value = "Something else";
        dom.press("Escape", {target: view().querySelector("textarea")});
        await settle();
        assert.equal(writes().length, 1);
        assert.ok(buttons().has("Writes about terminals."));
        // going elsewhere while it is written saves it, and the next place is drawn at once
        dom.click(button("Writes about terminals."));
        view().querySelector("textarea").value = "Writes about shells.";
        routing.openPlace(pages.pageFromKey("page:5"));
        assert.match(view().textContent, /Builds the parts of click/);
        await settle();
        assert.deepEqual(writes().at(-1), {
            method: "PATCH",
            url: "/json/armada/pages/me",
            body: {bio: "Writes about shells."},
        });
    },
);

run_test("a post: the server's rendering, and who drafted it", async () => {
    opened = [];
    await open("post:31");
    const article = view().querySelector("article");
    assert.equal(article.querySelector("h1").textContent, "Write-up of the option groups");
    assert.match(article.textContent, /Hello there/);
    assert.equal(article.querySelector("pre").textContent, "x = 1");
    assert.match(
        article.textContent,
        /Oct 15, 2026 · Draft: only you can see it · drafted by Onix/,
    );
    for (const verb of ["Publish", "Edit", "Delete", "All posts"]) {
        assert.ok(buttons().has(verb), verb);
    }
    // publishing the draft, from the post itself
    dom.click(button("Publish"));
    await settle();
    assert.deepEqual(writes(), [
        {method: "PATCH", url: "/json/armada/posts/31", body: {draft: "false"}},
    ]);
    assert.ok(!buttons().has("Publish"));
    assert.doesNotMatch(view().textContent, /only you can see/);
    // someone else's post offers nothing to change
    await open("post:21");
    assert.match(view().querySelector("article").textContent, /Hello there/);
    assert.doesNotMatch(view().textContent, /drafted by/);
    assert.deepEqual([...buttons()], ["All posts"]);
});

run_test("a new post: nothing is sent without a title; Publish shows it at once", async () => {
    opened = [];
    await open("edit:new");
    const [title, words] = [view().querySelector("input"), view().querySelector("textarea")];
    assert.equal(document.activeElement, title);
    dom.click(button("Publish"));
    await settle();
    assert.equal(writes().length, 0);
    assert.match(view().textContent, /A post needs a title/);
    dom.type(title, "Grouped help");
    // Return in the title goes on to the words
    dom.press("Enter", {target: title});
    assert.equal(document.activeElement, words);
    dom.type(words, "It **works**.");
    // the preview: the words at once as typed, then the server's rendering
    dom.click(button("Preview"));
    assert.ok(words.hidden);
    assert.match(view().querySelector("article").textContent, /It works\./);
    await settle();
    assert.match(view().querySelector("article").textContent, /Rendered by the server/);
    dom.click(button("Write"));
    assert.equal(document.activeElement, words);
    // ⌘Return publishes
    dom.press("Enter", {target: words, metaKey: true});
    await settle();
    assert.deepEqual(writes().slice(-1), [
        {
            method: "POST",
            url: "/json/armada/posts",
            body: {title: "Grouped help", content: "It **works**.", draft: "false"},
        },
    ]);
    assert.deepEqual(opened.at(-1), ["post:77", {replace: true}]);
    assert.equal(view().querySelector("h1").textContent, "Grouped help");
    assert.equal(pages.pages.get(ME).posts[0].title, "Grouped help");
});

run_test("what I typed and did not save comes back; Cancel drops it", async () => {
    opened = [];
    await open("edit:new");
    dom.type(view().querySelector("input"), "Half a thought");
    await open(`page:${ME}`);
    await open("edit:new");
    assert.equal(view().querySelector("input").value, "Half a thought");
    assert.match(view().textContent, /What you had not saved is back/);
    dom.click(button("Cancel"));
    await settle();
    assert.deepEqual(opened.at(-1), [`page:${ME}`, {replace: true}]);
    await open("edit:new");
    assert.equal(view().querySelector("input").value, "");
});

run_test("editing my post: Save draft keeps it mine; Delete asks, standing on Cancel", async () => {
    opened = [];
    pages.posts.delete(31);
    await open("edit:31");
    assert.equal(view().querySelector("input").value, "Write-up of the option groups");
    assert.equal(view().querySelector("textarea").value, DRAFTED.content);
    assert.ok(buttons().has("Save draft"));
    dom.click(button("Save draft"));
    await settle();
    assert.deepEqual(writes(), [
        {
            method: "PATCH",
            url: "/json/armada/posts/31",
            body: {title: DRAFTED.title, content: DRAFTED.content, draft: "true"},
        },
    ]);
    assert.deepEqual(opened.at(-1), ["post:31", {replace: true}]);
    dom.click(button("Delete"));
    let dialog = document.querySelector('[role="dialog"]');
    assert.equal(document.activeElement.textContent, "Cancel");
    dom.press("Enter");
    await settle();
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(writes().length, 1, "Return alone deleted nothing");
    assert.equal(document.activeElement, button("Delete"), "focus goes back to what opened it");
    dom.click(button("Delete"));
    dialog = document.querySelector('[role="dialog"]');
    dom.click([...dialog.querySelectorAll("button")].find((b) => b.textContent === "Delete"));
    await settle();
    assert.deepEqual(writes().slice(-1), [
        {method: "DELETE", url: "/json/armada/posts/31", body: null},
    ]);
    assert.deepEqual(opened.at(-1), [`page:${ME}`, {replace: true}]);
});

run_test("someone else's post is not mine to edit", async () => {
    opened = [];
    await open("edit:21");
    assert.deepEqual(opened.at(-1), ["post:21", {replace: true}]);
    assert.equal(view().querySelector("textarea"), null);
});

run_test("names, titles and lines are words, never markup", async () => {
    const HOSTILE = 7;
    const name = '<img src=x onerror="window.hit=1">';
    beta_state.people.set(HOSTILE, {
        id: HOSTILE,
        name,
        email: "7@example.com",
        bot: false,
        active: true,
    });
    const title = "<script>window.hit=1</script>";
    serve();
    answers.unshift(
        [
            "GET",
            /^\/json\/armada\/pages\/7$/,
            () =>
                success({
                    user_id: HOSTILE,
                    bio: '<b onmouseover="window.hit=1">bold</b>',
                    posts: [{id: 70, title, created: OCT_15, updated: OCT_15, draft: false}],
                }),
        ],
        [
            "GET",
            /^\/json\/armada\/pages$/,
            () => success({pages: [{user_id: HOSTILE, post_count: 1, updated: OCT_15}]}),
        ],
    );
    routing.openPlace(pages.pageFromKey("page:7"));
    await settle();
    assert.equal(view().querySelector("img, script, b"), null);
    assert.equal(view().querySelector("li a").textContent, title);
    assert.match(view().textContent, /<b onmouseover/);
    const side = new DOMParser().parseFromString(pages.pagesSideHTML(), "text/html");
    assert.equal(side.querySelector("img, script"), null);
    assert.equal(side.querySelector("li .t").textContent, name);
    assert.equal(window.hit, undefined);
    beta_state.people.delete(HOSTILE);
});

run_test("a post changed elsewhere is asked for again before I edit it", async () => {
    opened = [];
    await open("post:31");
    pages.onPost({
        type: "armada_post",
        op: "update",
        post: {
            id: 31,
            user_id: ME,
            title: "Retitled elsewhere",
            updated: OCT_15 + 200,
            draft: true,
        },
    });
    // I leave before the page asks again, so what I hold of the post is what it was
    await open(`page:${ME}`);
    clock.tick(300);
    await settle();
    serve([
        [
            "GET",
            /^\/json\/armada\/posts\/31$/,
            () =>
                success({
                    post: {
                        ...structuredClone(DRAFTED),
                        title: "Retitled elsewhere",
                        content: "New words",
                    },
                }),
        ],
    ]);
    routing.openPlace(pages.pageFromKey("edit:31"));
    await settle();
    assert.equal(view().querySelector("input").value, "Retitled elsewhere");
    assert.equal(view().querySelector("textarea").value, "New words");
});

run_test("a save that answers after I left leaves me where I went", async () => {
    opened = [];
    await open("edit:new");
    let answer_save;
    answers.unshift([
        "POST",
        /^\/json\/armada\/posts$/,
        () => new Promise((resolve) => (answer_save = resolve)),
    ]);
    dom.type(view().querySelector("input"), "Slow news");
    dom.click(button("Publish"));
    await settle();
    await open("page:5");
    answer_save(success({id: 78}));
    await settle();
    assert.deepEqual(opened.at(-1), ["page:5", {}]);
    assert.match(view().textContent, /Builds the parts of click/);
    assert.equal(pages.posts.get(78).title, "Slow news");
});

run_test("live: a post published, retitled or removed elsewhere", async () => {
    opened = [];
    await open("page:5");
    pages.onPost({
        type: "armada_post",
        op: "add",
        post: {
            id: 22,
            user_id: IAGO,
            title: "Fresh off the press",
            updated: OCT_15 + 60,
            draft: false,
        },
    });
    // drawn in the frame of the event, before the server is asked again
    assert.equal(view().querySelector("li a").textContent, "Fresh off the press");
    pages.onPost({
        type: "armada_post",
        op: "update",
        post: {
            id: 21,
            user_id: IAGO,
            title: "Option groups, grouped",
            updated: OCT_15 + 90,
            draft: false,
        },
    });
    assert.ok(buttons().has("Option groups, grouped"));
    pages.onPost({
        type: "armada_post",
        op: "remove",
        post: {
            id: 20,
            user_id: IAGO,
            title: "The case for boring CLIs",
            updated: OCT_15,
            draft: false,
        },
    });
    assert.ok(!buttons().has("The case for boring CLIs"));
    // a post that leaves while it is read says so
    await open("post:21");
    pages.onPost({
        type: "armada_post",
        op: "remove",
        post: {
            id: 21,
            user_id: IAGO,
            title: "Option groups, grouped",
            updated: OCT_15 + 99,
            draft: false,
        },
    });
    assert.match(view().textContent, /This post is no longer here/);
    await open("page:5");
    requests.length = 0;
    clock.tick(300);
    await settle();
    // then asked once, together, for what is in view
    assert.deepEqual(requests.map((r) => r.url).toSorted(), [
        "/json/armada/pages",
        "/json/armada/pages/5",
    ]);
    // an edit of the post I am editing elsewhere is said, and my words stay
    await open("edit:31");
    view().querySelector("textarea").value = "Mine";
    pages.onPost({
        type: "armada_post",
        op: "update",
        post: {id: 31, user_id: ME, title: "Other", updated: OCT_15 + 120, draft: true},
    });
    assert.match(view().textContent, /This post changed elsewhere/);
    assert.equal(view().querySelector("textarea").value, "Mine");
    clock.tick(300);
    await settle();
});
