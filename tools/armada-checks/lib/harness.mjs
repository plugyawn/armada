// The context object handed to every check: one fresh page, a CDP session,
// navigation helpers, and the assertion recorder.

/* global window, document */

import {BASE, VIEWPORT, sleep} from "./browser.mjs";
import {mutate_script, mutation_applied, requested_mutation} from "./mutate.mjs";
import {install_page_helpers} from "./page.mjs";
import {PROBE, SELECTORS, TOPICS} from "./selectors.mjs";
import {round} from "./stats.mjs";

const OPERATORS = {
    "<=": (value, threshold) => value <= threshold,
    ">=": (value, threshold) => value >= threshold,
    "==": (value, threshold) => value === threshold,
};

// Console noise that is produced by the development tooling rather than by
// the application.
const IGNORED_CONSOLE = [/\[webpack-dev-server\]/, /\[HMR\]/, /Download the React DevTools/];

// Whether this run's probe topic (selectors.mjs, PROBE) has a message yet;
// one run is one process, shared by every check of the run.
let probe_ready = false;

export class CheckContext {
    constructor(browser, name) {
        this.browser = browser;
        this.name = name;
        this.base = BASE;
        this.topics = TOPICS;
        this.assertions = [];
        this.measurements = [];
        this.console_errors = [];
        this.page_errors = [];
        this.requests = [];
        this.failed_responses = [];
        this.page = undefined;
        this.client = undefined;
        this.routes = [];
        this.intercepting = false;
        // See lib/mutate.mjs: a deliberately broken application, to show
        // that an assertion can fail.
        this.mutation = requested_mutation();
        this.only = process.env.ARMADA_CHECKS_ONLY
            ? new RegExp(process.env.ARMADA_CHECKS_ONLY)
            : undefined;
    }

    async init() {
        this.page = await this.browser.newPage();
        await this.page.setViewport(VIEWPORT);
        this.client = await this.page.createCDPSession();
        await this.client.send("Performance.enable");
        await this.client.send("Network.enable");
        await this.page.evaluateOnNewDocument(install_page_helpers, SELECTORS);
        this.page.on("pageerror", (error) => {
            this.page_errors.push(String(error?.stack ?? error).slice(0, 600));
        });
        this.page.on("console", (message) => {
            if (message.type() !== "error") {
                return;
            }
            const text = message.text();
            if (IGNORED_CONSOLE.some((pattern) => pattern.test(text))) {
                return;
            }
            const location = message.location();
            this.console_errors.push(
                `${text.slice(0, 400)}${location?.url ? ` @ ${location.url.replace(this.base, "")}` : ""}`,
            );
        });
        if (this.mutation) {
            await this.client.send("Network.setCacheDisabled", {cacheDisabled: true});
            await this.intercept();
        }
        this.client.on("Network.requestWillBeSent", (event) => {
            this.requests.push({
                id: event.requestId,
                url: event.request.url.replace(this.base, ""),
                method: event.request.method,
                type: event.type,
                time: event.timestamp,
            });
        });
    }

    // Call after `init` to see every later response with an error status.
    watch_responses() {
        this.client.on("Network.responseReceived", (event) => {
            if (event.response.status >= 400) {
                this.failed_responses.push(
                    `${event.response.status} ${event.response.url.replace(this.base, "").slice(0, 120)}`,
                );
            }
        });
    }

    async close() {
        if (this.mutation) {
            this.info(`mutation.${this.mutation.name}.applied`, mutation_applied(this.mutation), {
                note: `edits made per pattern: ${this.mutation.applied.join(", ")}`,
            });
        }
        await this.page?.close().catch(() => undefined);
    }

    // ---- requests the check answers itself -----------------------------

    // Pause requests in the browser (CDP Fetch) so that a check can look
    // at the page while the server has not answered, or answer in its
    // place. Nothing is paused until a route asks for it.
    async intercept() {
        if (this.intercepting) {
            return;
        }
        this.intercepting = true;
        const patterns = [
            {urlPattern: "*/json/*", requestStage: "Request"},
            // File uploads.
            {urlPattern: "*/api/v1/tus*", requestStage: "Request"},
        ];
        if (this.mutation) {
            patterns.push({
                urlPattern: "*/webpack/*",
                resourceType: "Script",
                requestStage: "Response",
            });
        }
        await this.client.send("Fetch.enable", {patterns});
        this.client.on("Fetch.requestPaused", (event) => {
            void this.paused(event);
        });
    }

    async paused(event) {
        try {
            if (event.responseStatusCode !== undefined) {
                await this.serve_mutated(event);
                return;
            }
            const request = {
                id: event.requestId,
                method: event.request.method,
                url: event.request.url.replace(this.base, ""),
                body: event.request.postData ?? "",
            };
            for (const route of this.routes) {
                if (route.active && route.accepts(request)) {
                    route.seen.push(request);
                    if (route.answer === undefined) {
                        route.pending.push(request);
                    } else {
                        await this.fulfil(request, route.answer(request));
                    }
                    return;
                }
            }
            await this.client.send("Fetch.continueRequest", {requestId: event.requestId});
        } catch {
            // The page went away with the request.
        }
    }

    async serve_mutated(event) {
        const {body, base64Encoded} = await this.client.send("Fetch.getResponseBody", {
            requestId: event.requestId,
        });
        const text = base64Encoded ? Buffer.from(body, "base64").toString("utf8") : body;
        const mutated = mutate_script(this.mutation, text);
        if (mutated === undefined) {
            await this.client.send("Fetch.continueRequest", {requestId: event.requestId});
            return;
        }
        await this.client.send("Fetch.fulfillRequest", {
            requestId: event.requestId,
            responseCode: 200,
            responseHeaders: [{name: "Content-Type", value: "application/javascript"}],
            body: Buffer.from(mutated, "utf8").toString("base64"),
        });
    }

    async fulfil(request, data, status = 200) {
        await this.client.send("Fetch.fulfillRequest", {
            requestId: request.id,
            responseCode: status,
            responseHeaders: [{name: "Content-Type", value: "application/json"}],
            body: Buffer.from(JSON.stringify(data), "utf8").toString("base64"),
        });
    }

    // Take over the requests `accepts` says yes to. Without `answer` they are
    // held until released; with it each is answered at once and never
    // reaches the server.
    async route(accepts, {answer} = {}) {
        await this.intercept();
        const route = {accepts, answer, active: true, seen: [], pending: []};
        this.routes.push(route);
        const settle = async (action) => {
            for (const request of route.pending.splice(0)) {
                try {
                    await action(request);
                } catch {
                    // Already gone.
                }
            }
        };
        return {
            seen: route.seen,
            // Let the held requests through to the server, oldest first.
            release: async () => {
                await settle(async (request) => {
                    await this.client.send("Fetch.continueRequest", {requestId: request.id});
                });
            },
            // Let only the oldest held request through.
            release_one: async () => {
                const request = route.pending.shift();
                if (request) {
                    await this.client
                        .send("Fetch.continueRequest", {requestId: request.id})
                        .catch(() => undefined);
                }
            },
            // Fail the held requests: the server never sees them.
            fail: async () => {
                await settle(async (request) => {
                    await this.client.send("Fetch.failRequest", {
                        requestId: request.id,
                        errorReason: "Aborted",
                    });
                });
            },
            respond: async (data, status = 200) => {
                await settle(async (request) => {
                    await this.fulfil(request, data, status);
                });
            },
            // Stop taking requests; anything still held goes through.
            stop: async () => {
                route.active = false;
                await settle(async (request) => {
                    await this.client.send("Fetch.continueRequest", {requestId: request.id});
                });
            },
        };
    }

    // ---- results -------------------------------------------------------

    // A pass/fail statement about a measured number. `value` null means the
    // thing could not be measured, which is a failure: a check that silently
    // measures nothing is worse than a red line.
    assert(id, value, operator, threshold, {unit = "", note = ""} = {}) {
        const measured = typeof value === "boolean" ? Number(value) : value;
        const usable = typeof measured === "number" && Number.isFinite(measured);
        const pass = usable && OPERATORS[operator](measured, threshold);
        this.assertions.push({
            id: `${this.name}/${id}`,
            value: usable ? round(measured, 3) : null,
            operator,
            threshold,
            unit,
            pass,
            note: usable ? note : `${note ? `${note}; ` : ""}not measurable`,
        });
        return pass;
    }

    // A number worth recording that must not gate anything (times on a busy
    // development machine, sizes of a development build).
    info(id, value, {unit = "", note = ""} = {}) {
        this.measurements.push({
            id: `${this.name}/${id}`,
            value: typeof value === "number" ? round(value, 3) : value,
            unit,
            note,
        });
    }

    // Run one scenario of a check. A scenario that throws becomes a single
    // failing assertion instead of taking the rest of the check with it.
    async scenario(label, body) {
        if (this.only && !this.only.test(label)) {
            return;
        }
        try {
            await body();
        } catch (error) {
            await this.debug_shot(label);
            this.assertions.push({
                id: `${this.name}/${label}.scenario-completed`,
                value: 0,
                operator: "==",
                threshold: 1,
                unit: "",
                pass: false,
                note: String(error?.message ?? error)
                    .split("\n")[0]
                    .slice(0, 300),
            });
        }
    }

    // With ARMADA_CHECKS_SHOTS=<directory> set, a failed scenario leaves a
    // screenshot of the page as it was when it gave up.
    async debug_shot(label) {
        const directory = process.env.ARMADA_CHECKS_SHOTS;
        if (!directory || !this.page) {
            return;
        }
        const file = `${directory}/${this.name}-${label.replaceAll(/[^\w-]+/g, "_")}.png`;
        await this.page.screenshot({path: file}).catch(() => undefined);
    }

    // ---- navigation ----------------------------------------------------

    // Full page load of a view, then wait for the application to be usable.
    // Going from one view to another changes only the fragment, which
    // the browser does without loading the document again; `fresh` asks
    // for a new document, with none of the previous page's state.
    async open(hash, {rows = hash.startsWith("#narrow"), fresh = false} = {}) {
        if (fresh) {
            await this.page.goto("about:blank");
        }
        await this.page.goto(`${this.base}/${hash}`, {waitUntil: "load"});
        await this.app_ready();
        if (hash === TOPICS.probe) {
            await this.ensure_probe();
        }
        if (rows) {
            await this.wait_for_rows();
        }
        await this.dismiss_banner();
        await this.settle();
    }

    async app_ready() {
        await this.page.waitForFunction(
            () =>
                window.__ac !== undefined &&
                window.__ac.last("leftSidebar") !== null &&
                window.__ac.last("splash") === null,
            {timeout: 60000},
        );
    }

    // Same-document navigation, the way a click on a link does it.
    async navigate(hash, {rows = hash.startsWith("#narrow")} = {}) {
        if (hash === TOPICS.probe) {
            await this.ensure_probe();
        }
        await this.page.evaluate((target) => {
            window.location.hash = target;
        }, hash);
        await this.page.waitForFunction(
            (target) => decodeURIComponent(window.location.hash).startsWith(target),
            {timeout: 15000},
            decodeURIComponent(hash),
        );
        if (rows) {
            await this.wait_for_rows();
        }
        await this.settle();
    }

    // The run's probe topic starts with one message, so that a check that
    // opens it before anything was sent there finds a conversation, with a
    // message of the user's own to reply to and to edit. The page must
    // already be showing the application.
    async ensure_probe() {
        if (probe_ready) {
            return;
        }
        const narrow = [
            {operator: "channel", operand: PROBE.stream_id},
            {operator: "topic", operand: PROBE.topic},
        ];
        const existing = await this.api("GET", "/json/messages", {
            anchor: "newest",
            num_before: 1,
            num_after: 0,
            narrow,
        });
        if (existing.status !== 200) {
            throw new Error(`cannot read ${PROBE.topic}: HTTP ${existing.status}`);
        }
        if (existing.data.messages.length === 0) {
            let response;
            for (let attempt = 0; attempt < 5; attempt += 1) {
                response = await this.api("POST", "/json/messages", {
                    type: "stream",
                    to: PROBE.stream_id,
                    topic: PROBE.topic,
                    content: "armada-checks: the messages of one run of the checks",
                });
                if (response.status !== 429) {
                    break;
                }
                // The server limits how fast one user may post.
                await sleep((Number(response.data?.["retry-after"]) || 10) * 1000 + 500);
            }
            if (response.status !== 200) {
                throw new Error(`cannot start ${PROBE.topic}: HTTP ${response.status}`);
            }
        }
        probe_ready = true;
    }

    async wait_for_rows() {
        await this.page.waitForFunction(() => window.__ac.all("messageRow").length > 0, {
            timeout: 30000,
        });
    }

    // The "enable desktop notifications" banner sits inside the fixed navbar
    // and changes every vertical position by its height. A fresh browser
    // profile always shows it; closing it gives every check the same frame.
    async dismiss_banner() {
        const point = await this.page.evaluate(() => window.__ac.center("alertBanner"));
        if (point) {
            await this.page.mouse.click(point.x, point.y);
            await sleep(150);
        }
    }

    async settle(quiet_ms = 400, timeout_ms = 6000) {
        return await this.page.evaluate(
            async (quiet, timeout) => await window.__ac.quiet(quiet, timeout),
            quiet_ms,
            timeout_ms,
        );
    }

    async sleep(ms) {
        await sleep(ms);
    }

    // ---- input ---------------------------------------------------------

    async center(name, which = "last") {
        return await this.page.evaluate(
            (selector_name, pick) => window.__ac.center(selector_name, pick),
            name,
            which,
        );
    }

    // A real pointer click on the middle of a named element.
    async click(name, {which = "last"} = {}) {
        const point = await this.center(name, which);
        if (!point) {
            throw new Error(`cannot click "${name}": no visible element matches`);
        }
        await this.page.mouse.click(point.x, point.y);
        return point;
    }

    async park_pointer() {
        await this.page.mouse.move(720, 2);
    }

    async rects(names) {
        return await this.page.evaluate((list) => window.__ac.rects(list), names);
    }

    async exists(name) {
        return await this.page.evaluate(
            (selector_name) => window.__ac.last(selector_name) !== null,
            name,
        );
    }

    async scroll_state() {
        return await this.page.evaluate(() => window.__ac.scroll_state());
    }

    // ---- recording -----------------------------------------------------

    async start_recording(names, probes = []) {
        await this.page.evaluate(
            (list, probe_names) => window.__ac.start_recording(list, probe_names),
            names,
            probes,
        );
    }

    async stop_recording() {
        return await this.page.evaluate(() => window.__ac.stop_recording());
    }

    async open_compose() {
        if (!(await this.exists("composeTextarea"))) {
            await this.page.evaluate(() => {
                document.activeElement?.blur?.();
            });
            await this.page.keyboard.press("r");
            await this.page.waitForFunction(() => window.__ac.last("composeTextarea") !== null, {
                timeout: 5000,
            });
        }
        await this.click("composeTextarea");
        await sleep(300);
        await this.clear_compose();
    }

    // A draft restored from an earlier page of the same browser would change
    // what is measured; start from an empty box.
    async clear_compose() {
        const length = await this.page.evaluate(
            () => window.__ac.last("composeTextarea")?.value.length ?? 0,
        );
        if (length > 0) {
            await this.page.keyboard.down("Control");
            await this.page.keyboard.press("a");
            await this.page.keyboard.up("Control");
            await this.page.keyboard.press("Backspace");
            await sleep(300);
        }
    }

    // Hold every "send message" request. Whatever the page shows while
    // they are held is client-side by construction; `fail()` afterwards
    // means nothing was posted.
    async hold_sends() {
        return await this.route(
            (request) => request.method === "POST" && /^\/json\/messages(\?|$)/.test(request.url),
        );
    }

    // Put text in the composer without typing it key by key, then leave
    // the caret after a space so that no completion menu is open.
    async fill_compose(text) {
        await this.page.evaluate((value) => {
            const textarea = window.__ac.last("composeTextarea");
            textarea.focus();
            textarea.value = value;
            textarea.setSelectionRange(value.length, value.length);
            textarea.dispatchEvent(new window.Event("input", {bubbles: true}));
        }, text);
        await sleep(250);
        await this.page.keyboard.type(" ");
        await sleep(200);
    }

    async compose_value() {
        return await this.page.evaluate(() => window.__ac.last("composeTextarea")?.value ?? null);
    }

    // ---- the application's own state ----------------------------------

    async feed() {
        return await this.page.evaluate(() => window.__ac.feed.state());
    }

    // A request to the server made by the page itself, with its session.
    async api(method, path, params = {}) {
        return await this.page.evaluate(
            async (verb, url, fields) => {
                const token =
                    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ??
                    /csrftoken=([^;]+)/.exec(document.cookie)?.[1] ??
                    "";
                const body = new URLSearchParams();
                for (const [key, value] of Object.entries(fields)) {
                    body.set(key, typeof value === "string" ? value : JSON.stringify(value));
                }
                const has_body = verb !== "GET";
                const response = await fetch(has_body ? url : `${url}?${body.toString()}`, {
                    method: verb,
                    headers: {"X-CSRFToken": token},
                    body: has_body ? body : undefined,
                });
                let data = null;
                try {
                    data = await response.json();
                } catch {
                    // Not JSON; the status says enough.
                }
                return {status: response.status, data};
            },
            method,
            path,
            params,
        );
    }

    // Hand events to the page exactly as a /json/events response would.
    // The id is negative so that the page's own event cursor is untouched.
    async inject(events) {
        await this.page.evaluate((list) => {
            window.zulip_test.inject_events(list.map((event) => ({id: -1, ...event})));
        }, events);
    }

    // A wheel movement over the feed: a scroll the user made.
    async wheel(delta_y) {
        const view = await this.page.evaluate(() => window.__ac.feed.viewport());
        const middle = await this.page.evaluate(() => {
            const box = window.__ac.last("messageRow")?.parentElement?.getBoundingClientRect();
            return box ? box.left + box.width / 2 : window.innerWidth / 2;
        });
        await this.page.mouse.move(middle, view.top + view.height / 2);
        await this.page.mouse.wheel({deltaY: delta_y});
    }

    // ---- CDP counters --------------------------------------------------

    async metrics() {
        const {metrics} = await this.client.send("Performance.getMetrics");
        return Object.fromEntries(metrics.map((metric) => [metric.name, metric.value]));
    }

    async emulate_latency(ms) {
        await this.client.send("Network.emulateNetworkConditions", {
            offline: false,
            latency: ms,
            downloadThroughput: -1,
            uploadThroughput: -1,
        });
    }

    requests_since(index) {
        return this.requests.slice(index);
    }
}

// Change of the cumulative CDP counters between two `metrics()` calls.
export function metrics_delta(before, after) {
    const delta = {};
    for (const key of [
        "LayoutCount",
        "RecalcStyleCount",
        "LayoutDuration",
        "RecalcStyleDuration",
        "ScriptDuration",
        "TaskDuration",
    ]) {
        delta[key] = round(after[key] - before[key], 4);
    }
    return delta;
}

// Application requests only: no static assets, no dev-server sockets.
export function is_api_request(request) {
    return /^\/(json|api)\//.test(request.url);
}
