// 13. What the keyboard does: the field's Return rules, the order in which
// Esc dismisses things, Tab through the composer, N and P, and the twelve
// Mac conventions the rebuild adds (PLAN.md, Appendix B).

/* global window, document, getComputedStyle */

import {ensure_pins, set_read} from "../lib/fixtures.mjs";

export const name = "keyboard-semantics";
export const order = 130;
export const description =
    "Return, Shift-Return and Return during input-method composition in the field; Esc closes the topmost thing first; Tab reaches every composer control; N and P; the twelve Mac conventions";

async function chord(ctx, modifier, key) {
    await ctx.page.keyboard.down(modifier);
    await ctx.page.keyboard.press(key);
    await ctx.page.keyboard.up(modifier);
}

async function field_keys(ctx) {
    await ctx.open(ctx.topics.probe, {rows: false, fresh: true});
    await ctx.sleep(500);
    await ctx.open_compose();
    const sends = await ctx.hold_sends();
    try {
        const stamp = Date.now().toString(36);
        await ctx.page.keyboard.type(`armada-checks ac-keys-${stamp}`);
        await chord(ctx, "Shift", "Enter");
        await ctx.page.keyboard.type("second line");
        await ctx.sleep(400);
        let value = await ctx.compose_value();
        ctx.assert(
            "field.shift-return-newline",
            value === `armada-checks ac-keys-${stamp}\nsecond line` && sends.seen.length === 0,
            "==",
            1,
            {
                note: `the field holds ${JSON.stringify(value)}; ${sends.seen.length} send request(s)`,
            },
        );

        // An input method composing a candidate, committed with Return.
        // Chromium reports that Return as a keydown with isComposing set.
        await ctx.page.evaluate(() => {
            window.__ac.composing_returns = [];
            window.addEventListener(
                "keydown",
                (event) => {
                    if (event.key === "Enter") {
                        window.__ac.composing_returns.push(event.isComposing);
                    }
                },
                true,
            );
        });
        await ctx.client.send("Input.imeSetComposition", {
            text: "にほん",
            selectionStart: 3,
            selectionEnd: 3,
        });
        await ctx.client.send("Input.dispatchKeyEvent", {
            type: "rawKeyDown",
            key: "Enter",
            code: "Enter",
            windowsVirtualKeyCode: 13,
            nativeVirtualKeyCode: 13,
        });
        await ctx.client.send("Input.dispatchKeyEvent", {
            type: "keyUp",
            key: "Enter",
            code: "Enter",
            windowsVirtualKeyCode: 13,
            nativeVirtualKeyCode: 13,
        });
        await ctx.sleep(600);
        const during_composition = sends.seen.length;
        const composing = await ctx.page.evaluate(() => window.__ac.composing_returns);
        await ctx.client.send("Input.insertText", {text: "にほん"});
        await ctx.sleep(300);
        value = await ctx.compose_value();

        // The same key, not composing: it sends.
        await ctx.page.keyboard.press("Enter");
        await ctx.sleep(700);
        const sent = new URLSearchParams(sends.seen[0]?.body ?? "").get("content") ?? "";
        ctx.assert(
            "field.return-sends",
            sends.seen.length === 1 && sent.includes(`ac-keys-${stamp}`) && sent.includes("にほん"),
            "==",
            1,
            {
                note: `Return in the field made ${sends.seen.length} send request(s) (held and failed by the check, nothing posted) carrying ${sent.length} characters`,
            },
        );
        ctx.assert(
            "field.ime-return-inert",
            // Only a Return the browser reported as part of a composition
            // says anything.
            composing.length > 0 && composing[0] === true ? during_composition : null,
            "==",
            0,
            {
                note: `send requests made by the Return that committed an input-method candidate (keydown isComposing=${composing[0]}); the field then held ${JSON.stringify(value)}`,
            },
        );
    } finally {
        await sends.fail();
        await sends.stop();
    }
}

async function layers(ctx) {
    return await ctx.page.evaluate(() => ({
        hash: window.location.hash,
        popover: window.__ac.all("menu").length > 0,
        overlay: window.__ac.all("overlay").length > 0,
        field: window.__ac.last("composeTextarea")?.value ?? null,
    }));
}

async function escape_order(ctx) {
    await ctx.open(ctx.topics.probe, {fresh: true});
    await ctx.open_compose();
    await ctx.page.keyboard.type("esc draft");
    await ctx.page.evaluate(() => {
        document.activeElement.blur();
    });
    await ctx.sleep(300);
    const home = (await layers(ctx)).hash;
    const trail = [];
    let in_order = 0;
    let opened = 0;
    // One transient thing over the composer and its draft: Esc closes
    // that thing, and the draft, the composer and the view are untouched.
    const layer = async (label, open, is_open) => {
        await open();
        if (await is_open()) {
            opened += 1;
        } else {
            trail.push(`${label}: did not open`);
            return;
        }
        await ctx.page.keyboard.press("Escape");
        await ctx.sleep(700);
        const state = await layers(ctx);
        const ok =
            !(await is_open()) &&
            !state.popover &&
            !state.overlay &&
            state.field === "esc draft" &&
            state.hash === home;
        trail.push(`${label}: ${ok ? "closed, nothing else changed" : JSON.stringify(state)}`);
        if (ok) {
            in_order += 1;
        }
    };
    const shows = async (selector_name) =>
        await ctx.page.evaluate((key) => window.__ac.all(key).length > 0, selector_name);
    await layer(
        "a message menu",
        async () => {
            await ctx.page.keyboard.press("i");
            await ctx.sleep(600);
        },
        async () => shows("menu"),
    );
    await layer(
        "the shortcut sheet",
        async () => {
            await ctx.page.keyboard.press("?");
            await ctx.sleep(900);
        },
        async () => shows("overlay"),
    );
    await layer(
        "the reaction picker",
        async () => {
            await ctx.page.keyboard.press(":");
            await ctx.sleep(700);
        },
        async () => shows("reactionPicker"),
    );

    // Today, a further Esc closes the composer and keeps the draft for
    // the next time it opens. The rebuilt composer is always there
    // (CUTS.md, K-09), so this is pinned by itself: the package that
    // makes `mac.esc-dismisses-topmost-only` pass retires it.
    const before_close = await layers(ctx);
    await ctx.page.keyboard.press("Escape");
    await ctx.sleep(700);
    const closed = await layers(ctx);
    ctx.assert(
        "esc.closes-open-composer",
        before_close.field === "esc draft" && !before_close.popover && !before_close.overlay
            ? closed.field === null && closed.hash === home
            : null,
        "==",
        1,
        {
            note: `Esc with nothing above the open composer: the field is ${closed.field === null ? "gone" : `still there holding ${JSON.stringify(closed.field)}`}, view ${closed.hash === home ? "unchanged" : closed.hash}`,
        },
    );
    // Nothing open at all.
    await ctx.page.keyboard.press("Escape");
    await ctx.sleep(900);
    const after = await layers(ctx);
    ctx.assert("esc.never-navigates", after.hash === home, "==", 1, {
        note: `Esc with nothing open: ${home} -> ${after.hash || "(home view)"}`,
    });

    // A thread beside the conversation: Esc closes it and the topic stays.
    await ctx.open(ctx.topics.threads, {fresh: true});
    const topic = (await layers(ctx)).hash;
    // The last launch: in the frame's pane the first can lie under the
    // recipient bar that sticks to the pane's top.
    await ctx.click("threadLaunch");
    await ctx.page
        .waitForFunction(() => window.__ac.all("threadPane").length > 0, {timeout: 5000})
        .catch(() => undefined);
    await ctx.sleep(500);
    if (await shows("threadPane")) {
        opened += 1;
        await ctx.page.keyboard.press("Escape");
        await ctx.sleep(900);
        const state = await layers(ctx);
        const ok = !(await shows("threadPane")) && state.hash === topic;
        trail.push(`a thread: ${ok ? "closed, the topic stayed" : JSON.stringify(state)}`);
        if (ok) {
            in_order += 1;
        }
    } else {
        trail.push("a thread: did not open");
    }
    ctx.assert("esc.order", opened === 4 ? in_order : null, "==", 4, {
        note: `one Esc with each of four things open, the first three over a composer holding a draft: ${trail.join("; ")}`,
    });
}

// Every control of the open composer that follows the field, in document
// order, with what it looks like at rest. They are kept by element, so
// that two controls with the same label are still two.
function composer_controls() {
    // What can draw a focus ring: an outline or a shadow, on the control
    // or on a pseudo-element of it.
    const ring = (element) =>
        [undefined, "::before", "::after"].map((pseudo) => {
            const style = getComputedStyle(element, pseudo);
            const outline =
                style.outlineStyle !== "none" && Number.parseFloat(style.outlineWidth) > 0
                    ? `${style.outlineWidth} ${style.outlineStyle} ${style.outlineColor}`
                    : "none";
            return `${outline} / ${style.boxShadow || "none"}`;
        });
    const describe = (element) =>
        (element.getAttribute("aria-label") ?? element.textContent ?? "").trim().slice(0, 30) ||
        element.className.toString().split(" ")[0];
    const root = window.__ac.last("compose");
    const field = window.__ac.last("composeTextarea");
    const all = [
        ...root.querySelectorAll(
            "a[href], a[tabindex], button, input, select, textarea, [tabindex]",
        ),
    ];
    // Tab goes forward: the controls that follow the field.
    const controls = all
        .slice(all.indexOf(field) + 1)
        .filter(
            (element) =>
                element.getClientRects().length > 0 &&
                getComputedStyle(element).visibility !== "hidden" &&
                !element.disabled &&
                element.tabIndex >= 0,
        );
    window.__ac.tab_walk = {
        controls,
        at_rest: controls.map((element) => ring(element).join(" | ")),
        ring,
        describe,
    };
    return controls.map((element) => describe(element));
}

function focused_control() {
    const element = document.activeElement;
    const root = window.__ac.last("compose");
    if (!element || !root?.contains(element)) {
        return null;
    }
    const walk = window.__ac.tab_walk;
    const index = walk.controls.indexOf(element);
    const now = walk.ring(element);
    const box = element.getBoundingClientRect();
    return {
        index,
        label: walk.describe(element),
        // A ring is something drawn for focus: an outline or a shadow the
        // control did not have at rest. A stop that was not among the
        // controls at rest has nothing to be compared with, and needs an
        // outline or a shadow at all.
        ring:
            index === -1
                ? now.some((part) => part !== "none / none")
                : now.join(" | ") !== walk.at_rest[index],
        left: box.left,
        top: box.top,
        height: box.height,
    };
}

async function tab_order(ctx) {
    await ctx.open(ctx.topics.probe, {fresh: true});
    await ctx.open_compose();
    const controls = await ctx.page.evaluate(composer_controls);
    const visited = [];
    const ringless = [];
    const reached = new Set();
    let first_is_send = false;
    // Where focus was when the walk ended: null outside the composer.
    let ended_on;
    for (let step = 0; step < controls.length + 4; step += 1) {
        await ctx.page.keyboard.press("Tab");
        await ctx.sleep(120);
        const stop = await ctx.page.evaluate(focused_control);
        // Stop when focus leaves the composer or comes round again.
        if (stop === null || (stop.index !== -1 && reached.has(stop.index))) {
            ended_on = stop?.index ?? null;
            break;
        }
        if (visited.length === 0) {
            first_is_send = await ctx.page.evaluate(
                () => window.__ac.last("composeSend") === document.activeElement,
            );
        }
        visited.push(stop);
        reached.add(stop.index);
        if (!stop.ring) {
            ringless.push(stop.label);
        }
    }
    const missed = controls.filter((_label, index) => !reached.has(index));
    ctx.assert("tab.composer-order", controls.length > 0 ? missed.length : null, "==", 0, {
        note: `${controls.length} controls follow the field in the open composer; Tab from the field stopped on ${visited.length} before leaving the composer or coming round: ${visited.map((stop) => stop.label).join(", ")}; not reached: ${missed.join(", ") || "none"}`,
    });
    ctx.assert("tab.first-stop-is-send", visited.length > 0 ? first_is_send : null, "==", 1, {
        note: `the first Tab from the field lands on "${visited[0]?.label}", so Tab then Return sends`,
    });
    ctx.assert("tab.composer-ring", visited.length > 0 ? ringless.length : null, "==", 0, {
        note: `stops that draw nothing for keyboard focus (the same outline and shadow, on the control and its pseudo-elements, as before it was focused): ${ringless.join(", ") || "none"}`,
    });

    // Shift-Tab goes back through the same stops, last first. When the
    // walk ended outside the composer the first press comes back to the
    // last stop; when Tab stayed on the last stop it goes to the one
    // before. Anywhere else, there is no walk to reverse.
    const forward = visited.map((stop) => stop.index);
    const reversible = ended_on === null || ended_on === forward.at(-1) || ended_on === undefined;
    const wanted = (ended_on === null ? forward : forward.slice(0, -1)).toReversed();
    const back = [];
    while (back.length < wanted.length) {
        await ctx.page.keyboard.down("Shift");
        await ctx.page.keyboard.press("Tab");
        await ctx.page.keyboard.up("Shift");
        await ctx.sleep(120);
        const stop = await ctx.page.evaluate(focused_control);
        back.push(stop === null ? null : stop.index);
    }
    ctx.assert(
        "tab.shift-tab-reverses",
        forward.length >= 3 && reversible
            ? wanted.filter((index, at) => back[at] !== index).length
            : null,
        "==",
        0,
        {
            note: `Shift-Tab pressed ${wanted.length} times after the walk forward: presses that did not land on the stop Tab had made before it (${back.filter((index) => index === null).length} left the composer)${reversible ? "" : "; the walk forward ended on a stop it had already made, so there is no order to reverse"}`,
        },
    );
    // Visual order: each stop is to the right of, or below, the one
    // before. The first stop is Send, at the end of the composer, by
    // today's design (tab.first-stop-is-send), so the order is judged
    // from the second.
    const out_of_place = visited.filter((stop, at) => {
        if (at < 2) {
            return false;
        }
        const previous = visited[at - 1];
        const same_line = Math.abs(stop.top - previous.top) < previous.height;
        return same_line ? stop.left < previous.left : stop.top < previous.top;
    });
    ctx.info("tab.stops-against-visual-order", out_of_place.length, {
        note: `stops left of, or above, the stop before them (Send, the first stop, aside): ${out_of_place.map((stop) => stop.label).join(", ") || "none"}`,
    });
    await ctx.page.keyboard.press("Escape");
}

async function next_unread(ctx) {
    await ctx.open(ctx.topics.inbox, {rows: false, fresh: true});
    const ids = await ensure_pins(ctx);
    await set_read(ctx, ids, true);
    await set_read(ctx, ids.slice(-3), false);
    const dm = await ctx.api("GET", "/json/messages", {
        anchor: "newest",
        num_before: 1,
        num_after: 0,
        narrow: [{operator: "is", operand: "dm"}],
        apply_markdown: false,
    });
    const dm_message = dm.data?.messages?.[0];
    const dm_id = dm_message?.id;
    // The conversation that message belongs to, as the URL names it.
    const me = await ctx.page.evaluate(() => window.zulip_test.current_user.user_id);
    const others = (dm_message?.display_recipient ?? [])
        .map((person) => person.id)
        .filter((id) => id !== me)
        .toSorted((a, b) => a - b);
    const dm_prefix = `#narrow/dm/${(others.length > 0 ? others : [me]).join(",")}-`;
    if (dm_id !== undefined) {
        await set_read(ctx, [dm_id], false);
    }
    await ctx.sleep(700);
    const arrive = async (key, wanted, known_unread = "") => {
        await ctx.open(ctx.topics.probe, {fresh: true});
        // An unfocused window marks nothing read on arrival, so what was
        // unread there can still be counted.
        const from = await ctx.page.evaluate(() => {
            window.dispatchEvent(new window.Event("blur"));
            return window.location.hash;
        });
        await ctx.page.keyboard.press(key);
        await ctx.page
            .waitForFunction((hash) => window.location.hash !== hash, {timeout: 5000}, from)
            .catch(() => undefined);
        await ctx.sleep(600);
        const state = await ctx.page.evaluate(() => ({
            hash: decodeURIComponent(window.location.hash),
            unread:
                window.__ac.feed
                    .list()
                    ?.all_messages()
                    .filter((message) => message.unread).length ?? 0,
        }));
        // Either unread messages are still counted there, or it is the
        // conversation this check made unread.
        const had_unread =
            state.unread > 0 || (known_unread !== "" && state.hash.startsWith(known_unread));
        return {...state, ok: state.hash !== from && wanted.test(state.hash) && had_unread};
    };
    const topic = await arrive("n", /^#narrow\/(channel|stream)\/[^/]+\/topic\//);
    const direct = dm_id === undefined ? null : await arrive("p", /^#narrow\/dm\//, dm_prefix);
    await set_read(ctx, ids.slice(-3), true);
    if (dm_id !== undefined) {
        await set_read(ctx, [dm_id], true);
    }
    ctx.assert(
        "keys.next-unread",
        direct === null ? null : Number(topic.ok) + Number(direct.ok),
        "==",
        2,
        {
            note: `N went to ${topic.hash} (${topic.unread} unread there); P went to ${direct?.hash ?? "nowhere: the user has no direct message"} (the conversation made unread for this is ${dm_prefix}...)`,
        },
    );
}

// ---- the twelve Mac conventions --------------------------------------
//
// New behaviour: each is expected to fail on today's application and is
// listed in known.json until the package named in PLAN.md builds it. A
// convention is asserted through something a person can see (what opened,
// where the view went, what the field holds), never through a handler.

async function mac(ctx, id, note, body) {
    let value = null;
    let detail = "";
    try {
        const result = await body();
        // A convention says nothing unless the page took itself to be on
        // a Mac when it was pressed.
        const on_mac = await ctx.page.evaluate(() => /mac/i.test(window.navigator.platform));
        value = on_mac ? Number(result.ok) : null;
        detail = on_mac ? (result.detail ?? "") : "the page does not report a Mac";
    } catch (error) {
        detail = `could not be driven: ${String(error?.message ?? error).split("\n")[0]}`;
    }
    ctx.assert(`mac.${id}`, value, "==", 1, {note: `${note}${detail ? `; ${detail}` : ""}`});
}

// The checks run in a Linux Chromium, where today's application drops
// every chord with the command key (hotkey.ts: `!has_mac_keyboard() &&
// e.metaKey`) and the rebuilt registry may map the command key by
// platform. For this scenario the page is told it runs on a Mac, before
// the application loads; the browser's own keys are still Linux's.
async function as_mac(ctx, body) {
    const {userAgent} = await ctx.client.send("Browser.getVersion");
    const real_platform = await ctx.page.evaluate(() => window.navigator.platform);
    await ctx.client.send("Emulation.setUserAgentOverride", {
        userAgent: userAgent.replace(/\(X11; Linux [^)]*\)/, "(Macintosh; Intel Mac OS X 10_15_7)"),
        platform: "MacIntel",
    });
    try {
        await body();
    } finally {
        await ctx.client.send("Emulation.setUserAgentOverride", {
            userAgent,
            platform: real_platform,
        });
    }
}

async function mac_conventions(ctx) {
    const shows = async (selector_name) =>
        await ctx.page.evaluate((key) => window.__ac.all(key).length > 0, selector_name);
    const hash = async () => await ctx.page.evaluate(() => window.location.hash);
    // What a chord did instead, for the record.
    const seen = async () =>
        await ctx.page.evaluate(() => {
            const element = document.activeElement;
            const focus =
                element && element !== document.body
                    ? `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ""}`
                    : "nothing";
            return `view ${window.location.hash}, focus on ${focus}, ${window.__ac.all("menu").length} menu(s) and ${window.__ac.all("overlay").length} overlay(s) open`;
        });

    await mac(ctx, "cmd-k-opens-palette", "Cmd-K opens the command palette", async () => {
        await ctx.open(ctx.topics.pins, {fresh: true});
        await chord(ctx, "Meta", "k");
        await ctx.sleep(600);
        return {ok: await shows("commandPalette"), detail: await seen()};
    });

    await mac(
        ctx,
        "esc-dismisses-topmost-only",
        "Esc closes the topmost thing and nothing else: with a menu over a draft, two presses leave the draft in the field and the view where it was",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            await ctx.open_compose();
            await ctx.page.keyboard.type("keep me");
            await ctx.page.evaluate(() => {
                document.activeElement.blur();
            });
            const home = await hash();
            await ctx.page.keyboard.press("i");
            await ctx.sleep(500);
            await ctx.page.keyboard.press("Escape");
            await ctx.sleep(400);
            await ctx.page.keyboard.press("Escape");
            await ctx.sleep(600);
            const state = await layers(ctx);
            return {
                ok: state.field === "keep me" && state.hash === home && !state.popover,
                detail: `after two presses the field holds ${JSON.stringify(state.field)} at ${state.hash}`,
            };
        },
    );

    await mac(
        ctx,
        "menus-press-drag-release",
        "a menu opens on the press, so that one press, drag and release picks an item",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            const point = await ctx.center("personalMenu");
            await ctx.page.mouse.move(point.x, point.y);
            await ctx.page.mouse.down();
            await ctx.sleep(400);
            const open_while_pressed = await shows("menu");
            await ctx.page.mouse.up();
            await ctx.sleep(300);
            return {
                ok: open_while_pressed,
                detail: `menu ${open_while_pressed ? "open" : "not open"} while the button is held`,
            };
        },
    );

    await mac(
        ctx,
        "context-menu-on-right-click",
        "a right-click on a row of the nav, and on a message, opens the application's menu for it",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            await ctx.page.evaluate(() => {
                window.__ac.context_menu = null;
                window.addEventListener("contextmenu", (event) => {
                    window.__ac.context_menu = event.defaultPrevented;
                });
            });
            const results = [];
            for (const [what, selector_name, which] of [
                ["a nav row", "channelRow", "first"],
                ["a message", "messageContent", "last"],
            ]) {
                await ctx.page.evaluate(() => {
                    window.__ac.context_menu = null;
                });
                const point = await ctx.center(selector_name, which);
                await ctx.page.mouse.click(point.x, point.y, {button: "right"});
                await ctx.sleep(500);
                const prevented = await ctx.page.evaluate(() => window.__ac.context_menu);
                const menu = await shows("menu");
                results.push({what, ok: prevented === true && menu, prevented, menu});
                // Esc with nothing open leaves the view today.
                if (menu) {
                    await ctx.page.keyboard.press("Escape");
                    await ctx.sleep(300);
                }
            }
            return {
                ok: results.every((result) => result.ok),
                detail: results
                    .map(
                        (result) =>
                            `on ${result.what} the browser's menu was ${result.prevented ? "replaced" : "not replaced"} and an application menu is ${result.menu ? "open" : "not open"}`,
                    )
                    .join("; "),
            };
        },
    );

    await mac(
        ctx,
        "cmd-j-opens-checklist",
        "Cmd-J opens the checklist of the topic's run",
        async () => {
            await ctx.open(ctx.topics.live, {fresh: true});
            await chord(ctx, "Meta", "j");
            await ctx.sleep(600);
            return {ok: await shows("checklist"), detail: await seen()};
        },
    );

    await mac(
        ctx,
        "cmd-period-stops-run",
        "Cmd-. asks the server to stop the live run",
        async () => {
            await ctx.open(ctx.topics.live, {fresh: true});
            // Answered by the check: the fixture's run is never really stopped.
            const stops = await ctx.route(
                (request) =>
                    request.method !== "GET" &&
                    request.url.startsWith("/json/pear/") &&
                    /cancel|stop|interrupt/.test(request.url),
                {answer: () => ({result: "success", msg: ""})},
            );
            await chord(ctx, "Meta", ".");
            await ctx.sleep(800);
            const asked = stops.seen.length;
            await stops.stop();
            return {ok: asked >= 1, detail: `${asked} stop request(s); ${await seen()}`};
        },
    );

    await mac(ctx, "cmd-comma-opens-settings", "Cmd-, opens Settings", async () => {
        await ctx.open(ctx.topics.pins, {fresh: true});
        await chord(ctx, "Meta", ",");
        await ctx.sleep(700);
        const at = await hash();
        return {
            ok: (await shows("settingsView")) || at.startsWith("#settings"),
            detail: await seen(),
        };
    });

    await mac(
        ctx,
        "cmd-f-finds-in-conversation",
        "Cmd-F puts the caret in the application's own find field",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            await chord(ctx, "Meta", "f");
            await ctx.sleep(600);
            return {
                ok: await ctx.page.evaluate(() =>
                    window.__ac.all("findField").includes(document.activeElement),
                ),
                detail: await seen(),
            };
        },
    );

    await mac(
        ctx,
        "cmd-w-closes-pane",
        "Cmd-W closes what is open beside the conversation (here a thread)",
        async () => {
            await ctx.open(ctx.topics.threads, {fresh: true});
            await ctx.click("threadLaunch");
            await ctx.page.waitForFunction(() => window.__ac.all("threadPane").length > 0, {
                timeout: 5000,
            });
            await chord(ctx, "Meta", "w");
            await ctx.sleep(700);
            const still_open = await shows("threadPane");
            return {
                ok: !still_open,
                detail: `the thread is ${still_open ? "still open" : "closed"}`,
            };
        },
    );

    await mac(
        ctx,
        "cmd-brackets-go-back-and-forward",
        "Cmd-[ and Cmd-] go back and forward",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            const first = await hash();
            await ctx.navigate(ctx.topics.probe);
            const second = await hash();
            await chord(ctx, "Meta", "[");
            await ctx.sleep(900);
            const back = await hash();
            await chord(ctx, "Meta", "]");
            await ctx.sleep(900);
            const forward = await hash();
            return {
                ok: back === first && forward === second,
                detail: `Cmd-[ led to ${back}, Cmd-] to ${forward}`,
            };
        },
    );

    await mac(
        ctx,
        "file-drop-attaches",
        "a file dropped on the conversation is attached to the message being written",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            // Answered by the check: nothing is uploaded.
            const uploads = await ctx.route(
                (request) => request.method !== "GET" && /user_uploads|\/tus/.test(request.url),
                {
                    answer: () => ({
                        result: "error",
                        msg: "armada-checks does not upload",
                        code: "BAD_REQUEST",
                    }),
                },
            );
            await ctx.page.evaluate(() => {
                const target = window.__ac.last("messageRow");
                const data = new window.DataTransfer();
                data.items.add(
                    new window.File(["armada-checks"], "armada-drop.txt", {type: "text/plain"}),
                );
                for (const type of ["dragenter", "dragover", "drop"]) {
                    target.dispatchEvent(
                        new window.DragEvent(type, {
                            bubbles: true,
                            cancelable: true,
                            dataTransfer: data,
                        }),
                    );
                }
            });
            await ctx.sleep(1200);
            const field = await ctx.compose_value();
            const asked = uploads.seen.length;
            await uploads.stop();
            return {
                ok: asked >= 1 || (field ?? "").includes("armada-drop"),
                detail: `${asked} upload request(s) started (answered by the check, nothing stored); the field holds ${JSON.stringify(field)}`,
            };
        },
    );

    await mac(
        ctx,
        "restore-after-reload",
        "a reload brings back the view, the selected message where it was, and the draft in the field",
        async () => {
            await ctx.open(ctx.topics.pins, {fresh: true});
            for (let step = 0; step < 6; step += 1) {
                await ctx.page.keyboard.press("k");
                await ctx.sleep(150);
            }
            await ctx.open_compose();
            await ctx.page.keyboard.type("restore me");
            await ctx.sleep(500);
            const before = await ctx.feed();
            await ctx.page.reload({waitUntil: "load"});
            await ctx.app_ready();
            await ctx.wait_for_rows();
            await ctx.settle();
            const after = await ctx.feed();
            const field = await ctx.compose_value();
            const same_place =
                after.hash === before.hash &&
                after.selected === before.selected &&
                Math.abs(after.selected_top - before.selected_top) <= 2;
            if (field) {
                await ctx.click("composeTextarea");
                await ctx.clear_compose();
            }
            return {
                ok: same_place && (field ?? "").includes("restore me"),
                detail: `selected message ${before.selected} at y ${before.selected_top} before, ${after.selected} at y ${after.selected_top} after; the field holds ${JSON.stringify(field)}`,
            };
        },
    );
}

export async function run(ctx) {
    await ctx.scenario("field", async () => {
        await field_keys(ctx);
    });
    await ctx.scenario("esc", async () => {
        await escape_order(ctx);
    });
    await ctx.scenario("tab", async () => {
        await tab_order(ctx);
    });
    await ctx.scenario("next-unread", async () => {
        await next_unread(ctx);
    });
    await ctx.scenario("mac", async () => {
        await as_mac(ctx, async () => {
            await mac_conventions(ctx);
        });
    });
}
