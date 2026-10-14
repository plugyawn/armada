// 12. What happens to a message between Return and the server's answer,
// and to its row afterwards (PLAN.md, Appendix B).

/* global window */

export const name = "echo-semantics";
export const order = 120;
export const description =
    "local echo: row in the first frame, place kept when the server's id arrives, rapid sends in order, a refused send stays with Retry, unsent text survives a reload, the server's rendering is final, an edit updates in place, a message for the agent is addressed to it";

const PX = {unit: "px"};

async function prepare(ctx, hash) {
    await ctx.open(hash, {rows: false, fresh: true});
    await ctx.sleep(500);
    await ctx.open_compose();
}

async function track(ctx, token) {
    await ctx.page.evaluate((value) => {
        window.__ac.start_tracking(value);
    }, token);
}

async function tracked(ctx) {
    return await ctx.page.evaluate(() => window.__ac.stop_tracking());
}

const is_local = (row) => row.local || !/^\d+$/.test(row.id);

async function echo_and_acknowledgement(ctx) {
    await prepare(ctx, ctx.topics.probe);
    const sends = await ctx.hold_sends();
    const stamp = Date.now().toString(36);
    const token = `ac-echo-${stamp}`;
    // A code block: the client renders it plain, the server highlights it.
    await ctx.fill_compose(`armada-checks ${token}\n\`\`\`python\nprint("${stamp}")\n\`\`\``);
    await track(ctx, token);
    await ctx.page.keyboard.press("Enter");
    await ctx.sleep(600);
    const held = sends.seen.length;
    await sends.release();
    await ctx.sleep(2500);
    const frames = await tracked(ctx);

    const first = frames.findIndex((frame) => frame.rows.length > 0);
    ctx.assert("echo.first-frame", first === -1 ? null : first, "<=", 0, {
        note: `index of the first frame after Return that shows the row, with the request held in the browser (${held} held)`,
    });

    // The frame in which the row first carries the server's id.
    const swap = frames.findIndex(
        (frame, at) =>
            at > 0 &&
            frame.rows.some((row) => !is_local(row)) &&
            frames[at - 1].rows.length > 0 &&
            frames[at - 1].rows.every((row) => is_local(row)),
    );
    const before = frames[swap - 1]?.rows[0];
    const after = frames.at(-1).rows;
    const settled = after[0];
    ctx.assert(
        "echo.id-swap.position",
        first === -1 || settled === undefined
            ? null
            : swap > 0
              ? Math.abs(settled.y - before.y) + (after.length === 1 && settled.last ? 0 : 1000)
              : 9999,
        "<=",
        0.5,
        {
            ...PX,
            note:
                swap > 0
                    ? `row id ${frames[first].rows[0].id} -> ${settled?.id}; y in the feed ${before.y} -> ${settled?.y}; ${after.length} row(s) carry the text afterwards, ${settled?.last ? "the last of the feed" : "not the last of the feed"} (1000 is added when it is not exactly one row at the end)`
                    : `the echoed row never took the server's id (9999); ${after.length} row(s) carry the text afterwards`,
        },
    );

    // The server's rendering is what stays: the row must show what a new
    // page shows for the same message.
    const echoed_html = frames[first]?.rows[0]?.html ?? "";
    const final_html = settled?.html ?? "";
    await ctx.open(ctx.topics.probe, {fresh: true});
    const reloaded_html = await ctx.page.evaluate((text) => {
        const row = window.__ac
            .all("messageRow")
            .findLast((candidate) => candidate.textContent.includes(text));
        return (
            row
                ?.querySelector(".message_content, [data-message-content]")
                ?.innerHTML.replaceAll(/\s+/g, " ")
                .trim() ?? null
        );
    }, token);
    ctx.assert(
        "echo.server-html-final",
        reloaded_html !== null && echoed_html !== "" && echoed_html !== reloaded_html
            ? Number(final_html === reloaded_html)
            : null,
        "==",
        1,
        {
            note: `the echo showed ${echoed_html.length} characters of HTML, the acknowledged row ${final_html.length}, a new page ${reloaded_html?.length ?? "nothing"}; the echo ${echoed_html === reloaded_html ? "equals" : "differs from"} the server's rendering`,
        },
    );
    await sends.stop();
}

async function rapid_sends(ctx) {
    await prepare(ctx, ctx.topics.probe);
    const sends = await ctx.hold_sends();
    const stamp = Date.now().toString(36);
    const tokens = [0, 1, 2].map((index) => `ac-rapid-${stamp}-${index}`);
    for (const token of tokens) {
        await ctx.fill_compose(`armada-checks ${token}`);
        await ctx.page.keyboard.press("Enter");
        await ctx.sleep(200);
    }
    await ctx.sleep(400);
    const order = async () =>
        await ctx.page.evaluate(
            (prefix) =>
                window.__ac
                    .all("messageRow")
                    .map((row) => new RegExp(`${prefix}-(\\d)`).exec(row.textContent)?.[1])
                    .filter((digit) => digit !== undefined)
                    .join(""),
            `ac-rapid-${stamp}`,
        );
    const unacknowledged = await order();
    // One at a time, so that the server receives them in the order sent.
    for (const token of tokens) {
        await sends.release_one();
        await ctx.page
            .waitForFunction(
                (text) =>
                    window.__ac
                        .all("messageRow")
                        .some(
                            (row) =>
                                row.textContent.includes(text) &&
                                /^\d+$/.test(row.dataset.messageId ?? ""),
                        ),
                {timeout: 5000},
                token,
            )
            .catch(() => undefined);
    }
    await ctx.sleep(1500);
    const acknowledged = await order();
    ctx.assert("echo.rapid.order", unacknowledged === "012" && acknowledged === "012", "==", 1, {
        note: `three sends 200 ms apart with every request held, then let through one by one: rows in order "${unacknowledged}" before the server answered, "${acknowledged}" after`,
    });
    await sends.stop();
}

async function refused_send(ctx) {
    await prepare(ctx, ctx.topics.probe);
    const sends = await ctx.hold_sends();
    const token = `ac-refused-${Date.now().toString(36)}`;
    await ctx.fill_compose(`armada-checks ${token}`);
    await ctx.page.keyboard.press("Enter");
    await ctx.sleep(500);
    // The check answers in the server's place; nothing is posted.
    await sends.respond(
        {result: "error", msg: "armada-checks refused this send", code: "BAD_REQUEST"},
        400,
    );
    await ctx.sleep(1500);
    const state = await ctx.page.evaluate((text) => {
        const row = window.__ac
            .all("messageRow")
            .findLast((candidate) => candidate.textContent.includes(text));
        const retry = window.__ac.all("failedRetry").filter((control) => row?.contains(control));
        return {row: row !== undefined, retry: retry.length};
    }, token);
    ctx.assert("echo.failed.stays-with-retry", state.row && state.retry === 1, "==", 1, {
        note: `after the server refused the send (HTTP 400 answered by the check): row ${state.row ? "still shown" : "gone"}, ${state.retry} Retry control(s) in it`,
    });
    await sends.stop();
}

async function unsent_text(ctx) {
    await prepare(ctx, ctx.topics.probe);
    const token = `ac-unsent-${Date.now().toString(36)}`;
    await ctx.page.keyboard.type(`armada-checks ${token} not sent yet`);
    await ctx.sleep(400);
    await ctx.page.reload({waitUntil: "load"});
    await ctx.app_ready();
    await ctx.settle();
    // Open the composer for the same conversation again, as a reader does.
    if (!(await ctx.exists("composeTextarea"))) {
        await ctx.page.keyboard.press("r");
        await ctx.sleep(800);
    }
    const value = await ctx.compose_value();
    ctx.assert(
        "echo.unsent.survives-reload",
        value === null ? null : value.includes(token),
        "==",
        1,
        {
            note: `typed, reloaded the page, opened the composer on the same topic: the field holds ${value === null ? "nothing (no field)" : `${value.length} characters`}`,
        },
    );
    if (value) {
        await ctx.click("composeTextarea");
        await ctx.clear_compose();
    }
}

// A real edit, made through the API by the page's own session.
async function edit_in_place(ctx) {
    await ctx.open(ctx.topics.probe, {fresh: true});
    const target = await ctx.page.evaluate(() => {
        const mine = window.zulip_test.current_user.user_id;
        const message = window.__ac.feed
            .list()
            .all_messages()
            .findLast(
                (candidate) =>
                    candidate.sender_id === mine && window.__ac.feed.row(candidate.id) !== null,
            );
        return message ? {id: message.id} : null;
    });
    if (target === null) {
        ctx.assert("edit.in-place", null, "==", 1, {
            note: "no message of mine in the probe topic to edit",
        });
        return;
    }
    const where = async () =>
        await ctx.page.evaluate((id) => {
            const row = window.__ac.feed.row(id);
            return row
                ? {
                      y: row.getBoundingClientRect().top + window.__ac.scroll_state().top,
                      text:
                          row
                              .querySelector(".message_content, [data-message-content]")
                              ?.textContent.trim() ?? "",
                      rows: window.__ac.all("messageRow").length,
                  }
                : null;
        }, target.id);
    const before = await where();
    const token = `ac-edited-${Date.now().toString(36)}`;
    const response = await ctx.api("PATCH", `/json/messages/${target.id}`, {
        content: `armada-checks ${token}`,
    });
    await ctx.sleep(1500);
    const after = await where();
    ctx.assert(
        "edit.in-place",
        response.status === 200 && before !== null && after !== null
            ? after.text.includes(token) &&
                  Math.abs(after.y - before.y) <= 0.5 &&
                  after.rows === before.rows
            : null,
        "==",
        1,
        {
            note: `edited message ${target.id} through the API (HTTP ${response.status}): the row shows "${after?.text.slice(0, 40)}", y in the feed ${before?.y.toFixed(2)} -> ${after?.y.toFixed(2)}, rows ${before?.rows} -> ${after?.rows}`,
        },
    );
}

// In the topic of a live run, the message that leaves the browser says it
// is for the agent when the agent control is on, and does not when it is
// off. That field is all the client contributes: which run the message
// reaches is the server's decision, so a run being live is a precondition
// here, not something the request shows.
async function steering(ctx) {
    await prepare(ctx, ctx.topics.live);
    const live = await ctx.exists("agentLiveRow");
    const sends = await ctx.hold_sends();
    try {
        const stamp = Date.now().toString(36);
        const send = async (token) => {
            await ctx.click("composeTextarea");
            await ctx.fill_compose(`armada-checks ${token}`);
            await ctx.page.keyboard.press("Enter");
            await ctx.sleep(800);
            const request = sends.seen.find((entry) => entry.body.includes(token));
            return request === undefined ? null : new URLSearchParams(request.body);
        };
        // The control off: an ordinary message.
        const plain = await send(`ac-plain-${stamp}`);
        await ctx.click("composeAgent", {which: "first"});
        await ctx.sleep(300);
        const steer = await send(`ac-steer-${stamp}`);
        const addressed =
            steer?.get("pear_followup") === "true" && steer?.get("topic") === "demo-live-run";
        const plain_addressed = plain?.get("pear_followup") === "true";
        ctx.assert(
            "steer.request-addresses-live-run",
            live && plain !== null && steer !== null ? addressed && !plain_addressed : null,
            "==",
            1,
            {
                note: `two requests in the topic of a live run (${live ? "its message is on screen" : "no live run on screen"}), both held and then failed, nothing posted and no run started; agent control on: pear_followup=${steer?.get("pear_followup")}, topic=${steer?.get("topic")}; off: pear_followup=${plain?.get("pear_followup")}`,
            },
        );
    } finally {
        await sends.fail();
        await sends.stop();
    }
}

export async function run(ctx) {
    await ctx.scenario("echo", async () => {
        await echo_and_acknowledgement(ctx);
        await rapid_sends(ctx);
    });
    await ctx.scenario("failed", async () => {
        await refused_send(ctx);
    });
    await ctx.scenario("unsent", async () => {
        await unsent_text(ctx);
    });
    await ctx.scenario("edit", async () => {
        await edit_in_place(ctx);
    });
    await ctx.scenario("steer", async () => {
        await steering(ctx);
    });
}
