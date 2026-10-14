// Helpers installed into every page before any application script runs.
// They are reached from checks as `window.__ac` inside `page.evaluate`.

/* global window, document, NodeFilter, requestAnimationFrame, getComputedStyle, MutationObserver */

export function install_page_helpers(selectors) {
    if (window.__ac !== undefined || window.location.protocol === "about:") {
        return;
    }
    const ac = {selectors, long_tasks: [], shifts: [], recording: undefined};
    window.__ac = ac;

    // Every check starts from the state of a fresh profile. Pages of one
    // browser share localStorage (drafts, collapsed sidebar sections...), so
    // what one check leaves there would change what the next one measures.
    // sessionStorage is per page, which makes this run once per check and
    // not again when the check reloads.
    try {
        if (window.sessionStorage.getItem("armada-checks") === null) {
            window.localStorage.clear();
            window.sessionStorage.setItem("armada-checks", "1");
        }
    } catch {
        // Storage is unavailable on this document; nothing to isolate.
    }

    const round = (value) => Math.round(value * 100) / 100;
    const visible = (element) =>
        element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden";

    // Visible matches of the first candidate selector that has any.
    ac.all = (name) => {
        for (const selector of selectors[name] ?? [name]) {
            const matches = [...document.querySelectorAll(selector)].filter((element) =>
                visible(element),
            );
            if (matches.length > 0) {
                return matches;
            }
        }
        return [];
    };
    ac.last = (name) => ac.all(name).at(-1) ?? null;
    ac.first = (name) => ac.all(name)[0] ?? null;
    ac.rect_of = (element) => {
        if (!element) {
            return null;
        }
        const box = element.getBoundingClientRect();
        return {x: round(box.x), y: round(box.y), w: round(box.width), h: round(box.height)};
    };
    ac.rect = (name) => ac.rect_of(ac.last(name));
    ac.rects = (names) => Object.fromEntries(names.map((name) => [name, ac.rect(name)]));
    // Where to point at an element. One that is outside the viewport is
    // scrolled into it first: a pointer cannot reach it otherwise.
    ac.center = (name, which = "last") => {
        const element = which === "first" ? ac.first(name) : ac.last(name);
        if (!element) {
            return null;
        }
        let box = element.getBoundingClientRect();
        const middle = () => ({x: box.x + box.width / 2, y: box.y + box.height / 2});
        if (
            middle().y < 0 ||
            middle().y > window.innerHeight ||
            middle().x < 0 ||
            middle().x > window.innerWidth
        ) {
            element.scrollIntoView({block: "center", inline: "nearest"});
            box = element.getBoundingClientRect();
        }
        return middle();
    };

    // A point at which a real pointer click lands on this element, or null
    // when something else covers it.
    ac.reachable_point = (element) => {
        element.scrollIntoView({block: "nearest", inline: "center"});
        const box = element.getBoundingClientRect();
        const point = {x: box.x + box.width / 2, y: box.y + box.height / 2};
        const hit = document.elementFromPoint(point.x, point.y);
        return hit && (hit === element || element.contains(hit) || hit.contains(element))
            ? point
            : null;
    };

    const label_of = (element) => element.textContent.trim().replaceAll(/\s+/g, " ");
    // The box of the first run of text inside an element: where the words
    // are, which is what a reader sees move when an icon is inserted before
    // them.
    const text_box = (element) => {
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node && node.textContent.trim() === "") {
            node = walker.nextNode();
        }
        if (!node) {
            return element.getBoundingClientRect();
        }
        const range = document.createRange();
        range.selectNodeContents(node);
        return range.getBoundingClientRect();
    };
    // Text position of every visible match, keyed by its text: used to
    // follow rows of a list while the list changes around them.
    ac.labelled = (name) => {
        const positions = {};
        for (const element of ac.all(name)) {
            const box = text_box(element);
            positions[label_of(element)] = {x: round(box.x), y: round(box.y)};
        }
        return positions;
    };
    ac.point_of = (name, label) => {
        for (const element of ac.all(name)) {
            if (label_of(element) === label) {
                const box = text_box(element);
                return {x: box.x + Math.min(box.width / 2, 40), y: box.y + box.height / 2};
            }
        }
        return null;
    };
    // Text of the visible match whose box contains a viewport point.
    ac.label_at = (name, x, y) => {
        for (const element of ac.all(name)) {
            const box = element.getBoundingClientRect();
            if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) {
                return label_of(element);
            }
        }
        return null;
    };

    // The element that scrolls the message feed: the nearest scrollable
    // ancestor of a message row, or the document when the page scrolls.
    ac.scroller = () => {
        let element = ac.last("messageRow")?.parentElement;
        while (element && element !== document.documentElement && element !== document.body) {
            const overflow = getComputedStyle(element).overflowY;
            if (
                (overflow === "auto" || overflow === "scroll") &&
                element.scrollHeight > element.clientHeight + 1
            ) {
                return element;
            }
            element = element.parentElement;
        }
        return document.scrollingElement;
    };
    ac.scroll_state = () => {
        const scroller = ac.scroller();
        return {
            top: round(scroller.scrollTop),
            max: round(scroller.scrollHeight - scroller.clientHeight),
            is_document: scroller === document.scrollingElement,
            doc_y: round(window.scrollY),
            doc_height: document.documentElement.scrollHeight,
        };
    };

    // Per-frame recorder. `names` are selector names; `probe_names` pick
    // entries of `ac.probes`, which a check may extend before recording.
    ac.probes = {
        scroll_top: () => ac.scroll_state().top,
        row_count: () => ac.all("messageRow").length,
        first_row_y: () => ac.rect_of(ac.first("messageRow"))?.y ?? null,
        first_text_x: () => ac.rect_of(ac.first("messageContent"))?.x ?? null,
        hash: () => window.location.hash,
        selected_id: () => ac.feed.list()?.selected_id() ?? null,
        selected_top() {
            const row = ac.feed.row(ac.feed.list()?.selected_id() ?? -1);
            return row ? round(row.getBoundingClientRect().top) : null;
        },
        model_length: () => ac.feed.list()?.all_messages().length ?? null,
        // An element a check has put in `ac.marked`: a message in the
        // middle of the conversation, say.
        marked_top: () => (ac.marked ? round(ac.marked.getBoundingClientRect().top) : null),
        marked_left: () => (ac.marked ? round(ac.marked.getBoundingClientRect().left) : null),
        marked_height: () => (ac.marked ? round(ac.marked.getBoundingClientRect().height) : null),
        marked_width: () => (ac.marked ? round(ac.marked.getBoundingClientRect().width) : null),
        channel_names: () => ac.labelled("channelName"),
        topic_names: () => ac.labelled("topicName"),
    };
    ac.start_recording = (names, probe_names = []) => {
        const recording = {on: true, frames: []};
        ac.recording = recording;
        const capture = () => {
            const frame = {
                t: round(performance.now()),
                rects: ac.rects(names),
                doc_y: round(window.scrollY),
                doc_height: document.documentElement.scrollHeight,
                probes: {},
            };
            for (const label of probe_names) {
                try {
                    frame.probes[label] = ac.probes[label]();
                } catch {
                    frame.probes[label] = null;
                }
            }
            recording.frames.push(frame);
        };
        const tick = () => {
            if (!recording.on) {
                return;
            }
            capture();
            requestAnimationFrame(tick);
        };
        // The first frame is the state before whatever the check does next.
        capture();
        requestAnimationFrame(tick);
        return performance.now();
    };
    ac.stop_recording = () => {
        const recording = ac.recording;
        recording.on = false;
        ac.recording = undefined;
        return recording.frames;
    };

    // ---- the message feed as the application sees it --------------------
    //
    // `zulip_test` is the application's own handle for browser tests. The
    // selected message, the rendered ids and the unread flags are read from
    // the model; positions are read from the DOM.
    const feed = {};
    ac.feed = feed;
    feed.list = () => window.zulip_test?.current_msg_list;
    feed.row = (id) =>
        ac.all("messageRow").find((row) => Number(row.dataset.messageId) === id) ?? null;
    // The part of the window in which a message can be read: below the
    // navbar and whatever recipient bar is stuck under it, above the
    // compose box.
    feed.viewport = () => {
        let top = ac.rect("navbar")?.h ?? 0;
        for (const bar of ac.all("stickyHeader")) {
            top = Math.max(top, bar.getBoundingClientRect().bottom);
        }
        const bottom = ac.last("compose")?.getBoundingClientRect().top ?? window.innerHeight;
        return {top: round(top), bottom: round(bottom), height: round(bottom - top)};
    };
    feed.rows = () =>
        ac.all("messageRow").map((row) => {
            const box = row.getBoundingClientRect();
            return {
                id: Number(row.dataset.messageId),
                top: round(box.top),
                bottom: round(box.bottom),
                height: round(box.height),
            };
        });
    feed.state = () => {
        const list = feed.list();
        const selected = list?.selected_id() ?? -1;
        const rows = feed.rows();
        const selected_row = rows.find((row) => row.id === selected) ?? null;
        const view = feed.viewport();
        const scroll = ac.scroll_state();
        return {
            hash: window.location.hash,
            selected,
            selected_top: selected_row?.top ?? null,
            selected_bottom: selected_row?.bottom ?? null,
            selected_height: selected_row?.height ?? null,
            // Where the selected row sits in the readable area, 0 = its top
            // edge is at the top of the area, 1 = at the bottom.
            selected_fraction:
                selected_row === null
                    ? null
                    : Math.round(((selected_row.top - view.top) / view.height) * 1000) / 1000,
            ids: rows.map((row) => row.id),
            model_ids: list?.all_messages().map((message) => message.id) ?? [],
            view,
            scroll_top: scroll.top,
            scroll_max: scroll.max,
            at_bottom: scroll.max - scroll.top <= 1,
        };
    };
    feed.unread = (ids) => ids.filter((id) => window.zulip_test.get_message(id)?.unread === true);
    // The message the application's rules select after a scroll the user
    // made, given where the rows are now: the selection stays unless its
    // row has left the band between 10% and 90% of the readable area, and
    // then it moves to the nearest row inside it.
    feed.keep_in_view_model = (selected) => {
        const rows = feed.rows();
        const view = feed.viewport();
        let index = rows.findIndex((row) => row.id === selected);
        if (index === -1) {
            return null;
        }
        const far_enough_down = (row) =>
            row.top >= view.top || row.bottom >= view.top + view.height / 10;
        const far_enough_up = (row) => row.top <= view.top + (view.height * 9) / 10;
        if (far_enough_down(rows[index])) {
            while (!far_enough_up(rows[index]) && index > 0) {
                index -= 1;
            }
        } else {
            while (!far_enough_down(rows[index]) && index < rows.length - 1) {
                index += 1;
            }
        }
        return rows[index].id;
    };
    // Rows that lie entirely inside the readable area.
    feed.fully_visible = () => {
        const view = feed.viewport();
        return feed
            .rows()
            .filter((row) => row.top >= view.top && row.bottom <= view.bottom)
            .map((row) => row.id);
    };

    // Follow the rows that carry `token` through every frame: how many
    // there are, which element and which message id each is, where it
    // sits relative to the feed's own scrolling, and what it shows.
    const identities = new WeakMap();
    let next_identity = 1;
    const identity = (element) => {
        if (!identities.has(element)) {
            identities.set(element, next_identity);
            next_identity += 1;
        }
        return identities.get(element);
    };
    ac.start_tracking = (token) => {
        const tracking = {on: true, frames: [], t0: performance.now(), return_at: null};
        ac.tracking = tracking;
        const on_key = (event) => {
            if (event.key === "Enter" && !event.shiftKey && tracking.return_at === null) {
                tracking.return_at = round(performance.now() - tracking.t0);
            }
        };
        window.addEventListener("keydown", on_key, true);
        tracking.stop = () => {
            window.removeEventListener("keydown", on_key, true);
        };
        const tick = () => {
            if (!tracking.on) {
                return;
            }
            const all = ac.all("messageRow");
            const scroll = ac.scroll_state().top;
            tracking.frames.push({
                t: round(performance.now() - tracking.t0),
                total: all.length,
                rows: all
                    .filter((row) => row.textContent.includes(token))
                    .map((row) => {
                        const content = row.querySelector(
                            ".message_content, [data-message-content]",
                        );
                        return {
                            element: identity(row),
                            id: row.dataset.messageId ?? "",
                            y: round(row.getBoundingClientRect().top + scroll),
                            last: row === all.at(-1),
                            local: row.matches(".locally-echoed, [data-local]"),
                            html: content?.innerHTML.replaceAll(/\s+/g, " ").trim() ?? "",
                        };
                    }),
            });
            requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
    };
    ac.stop_tracking = () => {
        const tracking = ac.tracking;
        tracking.on = false;
        tracking.stop();
        ac.tracking = undefined;
        // Only what happened after Return was pressed.
        return tracking.return_at === null
            ? []
            : tracking.frames.filter((frame) => frame.t >= tracking.return_at);
    };

    ac.visible_count = () => {
        let count = 0;
        for (const element of document.body.querySelectorAll("*")) {
            if (element.getClientRects().length > 0) {
                count += 1;
            }
        }
        return count;
    };

    // Startup marks, taken once per frame until both are known.
    ac.marks = {};
    const mark = () => {
        if (document.body) {
            if (
                ac.marks.splash_gone === undefined &&
                ac.last("leftSidebar") &&
                !ac.last("splash")
            ) {
                ac.marks.splash_gone = round(performance.now());
            }
            if (ac.marks.first_row === undefined && ac.last("messageRow")) {
                ac.marks.first_row = round(performance.now());
            }
        }
        if (
            (ac.marks.splash_gone === undefined || ac.marks.first_row === undefined) &&
            performance.now() < 30000
        ) {
            requestAnimationFrame(mark);
        }
    };
    requestAnimationFrame(mark);

    ac.frames = async (count = 2) => {
        for (let index = 0; index < count; index += 1) {
            await new Promise((resolve) => {
                requestAnimationFrame(resolve);
            });
        }
    };

    // Resolves once the DOM has gone `quiet_ms` without a mutation, or after
    // `timeout_ms` (live topics tick for ever).
    ac.quiet = async (quiet_ms = 400, timeout_ms = 6000) =>
        await new Promise((resolve) => {
            const start = performance.now();
            let timer;
            const observer = new MutationObserver(() => {
                arm();
            });
            const done = (quiet) => {
                observer.disconnect();
                clearTimeout(timer);
                resolve({quiet, waited: round(performance.now() - start)});
            };
            function arm() {
                clearTimeout(timer);
                if (performance.now() - start > timeout_ms) {
                    done(false);
                    return;
                }
                timer = setTimeout(() => {
                    done(true);
                }, quiet_ms);
            }
            observer.observe(document.documentElement, {
                subtree: true,
                childList: true,
                attributes: true,
                characterData: true,
            });
            arm();
        });

    try {
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                ac.long_tasks.push({t: round(entry.startTime), dur: round(entry.duration)});
            }
        }).observe({type: "longtask", buffered: true});
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                ac.shifts.push({
                    t: round(entry.startTime),
                    value: entry.value,
                    input: entry.hadRecentInput,
                });
            }
        }).observe({type: "layout-shift", buffered: true});
    } catch {
        // Not a Chromium build with these entry types; the checks report null.
    }
}
