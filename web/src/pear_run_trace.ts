import $ from "jquery";
import * as z from "zod/mini";

import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as ui_util from "./ui_util.ts";

const pear_trace_response_schema = z.object({
    rendered: z.string(),
    has_more: z.optional(z.boolean()),
    trace: z.array(
        z.object({
            id: z.number(),
            kind: z.string(),
            title: z.string(),
            body: z.optional(z.string()),
            plan: z.optional(z.array(z.object({step: z.string(), status: z.string()}))),
        }),
    ),
});

export function rendered_for_testing(raw_data: unknown): string | undefined {
    const result = pear_trace_response_schema.safeParse(raw_data);
    return result.success ? result.data.rendered : undefined;
}

export function should_load_on_open_for_testing(
    loaded: boolean,
    dirty: boolean,
    failed: boolean,
): boolean {
    return !loaded || dirty || failed;
}

export function event_should_load_for_testing(expanded: boolean): boolean {
    return expanded;
}

export function duplicate_tail_count_for_testing(
    trace_blocks: readonly string[],
    message_blocks: readonly string[],
): number {
    let count = 0;
    while (
        count < trace_blocks.length &&
        count < message_blocks.length &&
        trace_blocks[trace_blocks.length - 1 - count] ===
            message_blocks[message_blocks.length - 1 - count]
    ) {
        count += 1;
    }
    return count;
}

export type PearRunTraceRequestState = "idle" | "loading" | "ready" | "failed";

export type PearRunTraceController = {
    destroy: () => void;
    load: () => void;
    mark_dirty: (load_now: boolean) => void;
    readonly dirty: boolean;
    readonly failed: boolean;
    readonly in_flight: boolean;
    readonly loaded: boolean;
    readonly request_state: PearRunTraceRequestState;
};

type PearRunTraceControllerOptions = {
    run_id: number;
    thread_id?: string | undefined;
    host: HTMLElement;
    status?: HTMLElement | undefined;
    after_render?: (() => void) | undefined;
    on_state_change?: ((controller: PearRunTraceController) => void) | undefined;
};

export function create_controller(options: PearRunTraceControllerOptions): PearRunTraceController {
    let request: JQuery.jqXHR<unknown> | undefined;
    let request_generation = 0;
    let destroyed = false;
    let dirty = false;
    let loaded = false;
    let request_state: PearRunTraceRequestState = "idle";

    const controller: PearRunTraceController = {
        destroy() {
            destroyed = true;
            request_generation += 1;
            request?.abort();
            request = undefined;
        },
        load() {
            if (destroyed) {
                return;
            }
            if (request !== undefined) {
                dirty = true;
                return;
            }
            dirty = false;
            request_generation += 1;
            const generation = request_generation;
            const first_load = !loaded;
            request_state = "loading";
            if (first_load) {
                options.host.textContent = $t({defaultMessage: "Loading work…"});
                if (options.status !== undefined) {
                    options.status.textContent = $t({defaultMessage: "Loading work"});
                }
            }
            options.on_state_change?.(controller);
            request = channel.get({
                url: `/json/pear/runs/${options.run_id}/trace`,
                data: {
                    limit: 100,
                    ...(options.thread_id === undefined ? {} : {thread_id: options.thread_id}),
                },
                success(raw_data) {
                    if (destroyed || generation !== request_generation) {
                        return;
                    }
                    if (dirty) {
                        // An event arrived after this request began.  Do not
                        // paint a response that is already known to be stale;
                        // preserve the current trace until the single-flight
                        // follow-up returns.
                        request = undefined;
                        controller.load();
                        return;
                    }
                    try {
                        const rendered = rendered_for_testing(raw_data);
                        if (rendered === undefined) {
                            fail(generation);
                            return;
                        }
                        const fragment = ui_util.parse_html(rendered);
                        if (pear_trace_response_schema.parse(raw_data).has_more === true) {
                            const notice = document.createElement("div");
                            notice.className = "pear-run-trace-truncated";
                            notice.textContent = $t({
                                defaultMessage: "Earlier work is available in the run trace.",
                            });
                            fragment.append(notice);
                        }
                        options.host.replaceChildren(fragment);
                        options.host.classList.add("rendered_markdown");
                        rendered_markdown.update_elements($(options.host));
                        options.after_render?.();
                        loaded = true;
                        request_state = "ready";
                        request = undefined;
                        if (options.status !== undefined) {
                            options.status.textContent = first_load
                                ? $t({defaultMessage: "Work loaded"})
                                : "";
                        }
                        options.on_state_change?.(controller);
                    } catch {
                        fail(generation);
                    }
                },
                error(_xhr, error_type) {
                    if (error_type === "abort" || destroyed || generation !== request_generation) {
                        return;
                    }
                    fail(generation);
                },
            });
            // A reload can suppress requests entirely. Keep the controller
            // retryable instead of leaving it permanently in-flight.
            if (request === undefined) {
                fail(generation);
            }
        },
        mark_dirty(load_now) {
            if (destroyed) {
                return;
            }
            dirty = true;
            if (load_now) {
                controller.load();
            }
        },
        get dirty() {
            return dirty;
        },
        get failed() {
            return request_state === "failed";
        },
        get in_flight() {
            return request !== undefined;
        },
        get loaded() {
            return loaded;
        },
        get request_state() {
            return request_state;
        },
    };

    function fail(generation: number): void {
        if (destroyed || generation !== request_generation) {
            return;
        }
        const had_loaded_trace = loaded;
        request = undefined;
        request_state = "failed";
        dirty = had_loaded_trace;
        const error = had_loaded_trace
            ? $t({defaultMessage: "Could not refresh work. Retry available."})
            : $t({defaultMessage: "Could not load work."});
        if (!had_loaded_trace) {
            const error_row = document.createElement("div");
            error_row.className = "pear-run-trace-error";
            const error_text = document.createElement("span");
            error_text.textContent = error;
            const retry = document.createElement("button");
            retry.className = "pear-run-trace-retry";
            retry.type = "button";
            retry.textContent = $t({defaultMessage: "Retry"});
            retry.addEventListener("click", controller.load);
            error_row.append(error_text, retry);
            options.host.replaceChildren(error_row);
        }
        if (options.status !== undefined) {
            options.status.textContent = error;
        }
        options.on_state_change?.(controller);
    }

    return controller;
}
