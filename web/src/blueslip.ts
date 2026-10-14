/* eslint-disable no-console */

// System documented in https://zulip.readthedocs.io/en/latest/subsystems/logging.html

// This must be included before the first call to $(document).ready
// in order to be able to report exceptions that occur during their
// execution.

import $ from "jquery";

export class BlueslipError extends Error {
    override name = "BlueslipError";
    more_info?: object;
    constructor(msg: string, more_info?: object, cause?: unknown) {
        super(msg, {cause});
        if (more_info !== undefined) {
            this.more_info = more_info;
        }
    }
}

if (Error.stackTraceLimit !== undefined) {
    Error.stackTraceLimit = 100000;
}

function make_logger_func(name: "debug" | "log" | "info" | "warn" | "error") {
    return function Logger_func(this: Logger, ...args: unknown[]) {
        const date_str = new Date().toISOString();

        const str_args = args.map((x) => (typeof x === "string" ? x : JSON.stringify(x)));

        const log_entry = date_str + " " + name.toUpperCase() + ": " + str_args.join("");
        this._memory_log.push(log_entry);

        // Don't let the log grow without bound
        if (this._memory_log.length > 1000) {
            this._memory_log.shift();
        }

        if (console[name] !== undefined) {
            console[name](...args);
        }
    };
}

class Logger {
    debug = make_logger_func("debug");
    log = make_logger_func("log");
    info = make_logger_func("info");
    warn = make_logger_func("warn");
    error = make_logger_func("error");

    _memory_log: string[] = [];
    get_log(): string[] {
        return this._memory_log;
    }
}

const logger = new Logger();

export function get_log(): string[] {
    return logger.get_log();
}

function build_arg_list(
    msg: string,
    more_info?: Record<string, unknown>,
): [string, string?, unknown?] {
    const args: [string, string?, unknown?] = [msg];
    if (more_info !== undefined) {
        args.push("\nAdditional information: ", more_info);
    }
    return args;
}

export function debug(msg: string, more_info?: Record<string, unknown>): void {
    const args = build_arg_list(msg, more_info);
    logger.debug(...args);
}

export function log(msg: string, more_info?: Record<string, unknown>): void {
    const args = build_arg_list(msg, more_info);
    logger.log(...args);
}

export function info(msg: string, more_info?: Record<string, unknown>): void {
    const args = build_arg_list(msg, more_info);
    logger.info(...args);
}

export function warn(msg: string, more_info?: Record<string, unknown>): void {
    const args = build_arg_list(msg, more_info);
    logger.warn(...args);
    if (DEVELOPMENT) {
        console.trace();
    }
}

export function error(
    msg: string,
    more_info?: Record<string, unknown>,
    original_error?: unknown,
): void {
    const args = build_arg_list(msg, more_info);
    logger.error(...args);

    // Throw an error in development; this will show a dialog (see below).
    if (DEVELOPMENT) {
        throw new BlueslipError(msg, more_info, original_error);
    }
    // This function returns to its caller in production!  To raise a
    // fatal error even in production, use throw new Error(…) instead.
}

// Install a window-wide onerror handler in development to display the stacktraces, to make them
// hard to miss
if (DEVELOPMENT) {
    // The display and its source-map reader are a chunk of their own,
    // which a production build does not contain.
    const display_stacktrace = async (ex: unknown, message?: string): Promise<void> => {
        const blueslip_stacktrace = await import("./blueslip_stacktrace.ts");
        await blueslip_stacktrace.display_stacktrace(ex, message);
    };
    $(window).on("error", (event: JQuery.TriggeredEvent) => {
        const {originalEvent} = event;
        if (originalEvent instanceof ErrorEvent) {
            void display_stacktrace(originalEvent.error, originalEvent.message);
        }
    });
    $(window).on("unhandledrejection", (event: JQuery.TriggeredEvent) => {
        const {originalEvent} = event;
        if (originalEvent instanceof PromiseRejectionEvent) {
            void display_stacktrace(originalEvent.reason);
        }
    });
}

// Click handlers for the blueslip overlay.
$(".blueslip-error-container").on("click", ".stackframe", function () {
    $(this).siblings(".code-context").toggle("fast");
});

$(".blueslip-error-container").on("click", ".exit", function () {
    const $stacktrace = $(this).closest(".stacktrace");
    $stacktrace.addClass("fade-out");
    setTimeout(() => {
        $stacktrace.removeClass("fade-out show");
    }, 300);
});
