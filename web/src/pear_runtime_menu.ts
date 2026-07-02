import $ from "jquery";
import type * as tippy from "tippy.js";
import * as z from "zod/mini";

import render_pear_runtime_menu_popover from "../templates/pear_runtime_menu_popover.hbs";

import * as blueslip from "./blueslip.ts";
import * as channel from "./channel.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import * as pear_panel from "./pear_panel.ts";
import * as popover_menus from "./popover_menus.ts";
import {parse_html} from "./ui_util.ts";

const pear_reasoning_effort_schema = z.object({
    effort: z.string(),
    label: z.string(),
});

const pear_model_catalog_entry_schema = z.object({
    slug: z.string(),
    display_name: z.string(),
    default_reasoning_level: z.optional(z.string()),
    supported_reasoning_levels: z.optional(z.array(pear_reasoning_effort_schema)),
});

// Subset of `topic_context.git`; see web/src/pear_panel.ts
// (PearGitContext) for the full shape.
const pear_git_context_schema = z.object({
    available: z.boolean(),
    branch: z.optional(z.string()),
    head: z.optional(z.string()),
    dirty: z.optional(z.boolean()),
    changed_count: z.optional(z.number()),
    diff_shortstat: z.optional(z.string()),
});

// Subset of the `pear_topic_context` object returned by the
// `GET /json/pear/runs` and `POST /json/pear/topic-context`
// endpoints; see web/src/pear_panel.ts for the full shape.
const pear_runtime_context_schema = z.object({
    stream: z.string(),
    topic: z.string(),
    model: z.string(),
    model_choices: z.optional(z.array(z.string())),
    model_catalog: z.optional(z.array(pear_model_catalog_entry_schema)),
    reasoning_effort: z.string(),
    reasoning_efforts: z.optional(z.array(pear_reasoning_effort_schema)),
    github_repo: z.optional(z.string()),
    repo_label: z.optional(z.string()),
    git: z.optional(pear_git_context_schema),
});

const pear_runtime_response_schema = z.object({
    pear_topic_context: z.nullish(pear_runtime_context_schema),
});

type PearReasoningEffort = z.infer<typeof pear_reasoning_effort_schema>;
type PearRuntimeContext = z.infer<typeof pear_runtime_context_schema>;

type PearRuntimeMenuChoice = {
    value: string;
    label: string;
    selected: boolean;
};

type PearGitDisplay = {
    has_repo_branch: boolean;
    repo_branch_label: string;
    head: string;
    status_label: string;
};

let topic_context: PearRuntimeContext | undefined;
let context_key = "";
let last_error = "";
let request_in_flight = false;

const fallback_efforts: PearReasoningEffort[] = [
    {effort: "low", label: "Low"},
    {effort: "medium", label: "Medium"},
    {effort: "high", label: "High"},
    {effort: "xhigh", label: "XHigh"},
];

function current_narrow(): {stream: string | undefined; topic: string | undefined} {
    return {
        stream: narrow_state.stream_name(),
        topic: narrow_state.topic(),
    };
}

function narrow_context_key(stream: string | undefined, topic: string | undefined): string {
    if (stream === undefined || topic === undefined) {
        return "";
    }
    return `${stream}\n${topic}`;
}

function model_reasoning_efforts(model: string | undefined): PearReasoningEffort[] {
    const catalog_entry = topic_context?.model_catalog?.find((entry) => entry.slug === model);
    const model_efforts = catalog_entry?.supported_reasoning_levels;
    if (model_efforts !== undefined && model_efforts.length > 0) {
        return model_efforts;
    }
    if (
        topic_context?.reasoning_efforts !== undefined &&
        topic_context.reasoning_efforts.length > 0
    ) {
        return topic_context.reasoning_efforts;
    }
    return fallback_efforts;
}

function model_choices(): PearRuntimeMenuChoice[] {
    if (topic_context === undefined) {
        return [];
    }
    const catalog = topic_context.model_catalog ?? [];
    const choices = catalog.map((entry) => ({
        value: entry.slug,
        label: entry.display_name || entry.slug,
        selected: entry.slug === topic_context!.model,
    }));
    const seen_models = new Set(choices.map((choice) => choice.value));
    for (const model of topic_context.model_choices ?? []) {
        if (!seen_models.has(model)) {
            choices.push({
                value: model,
                label: model,
                selected: model === topic_context.model,
            });
            seen_models.add(model);
        }
    }
    if (topic_context.model && !seen_models.has(topic_context.model)) {
        choices.unshift({
            value: topic_context.model,
            label: topic_context.model,
            selected: true,
        });
    }
    return choices;
}

function effort_choices(): PearRuntimeMenuChoice[] {
    if (topic_context === undefined) {
        return [];
    }
    return model_reasoning_efforts(topic_context.model).map((choice) => ({
        value: choice.effort,
        label: choice.label,
        selected: choice.effort === topic_context!.reasoning_effort,
    }));
}

function git_display(): PearGitDisplay | undefined {
    const git = topic_context?.git;
    if (git?.available !== true) {
        // Read-only context block is hidden entirely when git is unavailable.
        return undefined;
    }
    const repo =
        (topic_context?.github_repo ?? "").trim() || (topic_context?.repo_label ?? "").trim();
    const branch = git.branch?.trim() ?? "";
    const repo_branch_label = [repo, branch].filter((part) => part.length > 0).join(" · ");
    let status_label: string;
    if (git.dirty === true) {
        const changed_count = git.changed_count ?? 0;
        const shortstat = git.diff_shortstat?.trim() ?? "";
        if (changed_count > 0) {
            status_label = $t({defaultMessage: "{changed_count} changed"}, {changed_count});
        } else if (shortstat.length > 0) {
            status_label = shortstat;
        } else {
            status_label = $t({defaultMessage: "changed"});
        }
    } else {
        status_label = $t({defaultMessage: "clean"});
    }
    return {
        has_repo_branch: repo_branch_label.length > 0,
        repo_branch_label,
        head: git.head?.trim() ?? "",
        status_label,
    };
}

function popover_context(): {
    has_runtime: boolean;
    status_message: string;
    git: PearGitDisplay | undefined;
    model_choices: PearRuntimeMenuChoice[];
    effort_choices: PearRuntimeMenuChoice[];
} {
    const {stream, topic} = current_narrow();
    let status_message = "";
    if (stream === undefined || topic === undefined) {
        status_message = $t({defaultMessage: "Open a topic to configure the Pear runtime."});
    } else if (last_error !== "") {
        status_message = last_error;
    } else if (topic_context === undefined) {
        status_message = $t({defaultMessage: "Pear runtime is loading."});
    }
    if (status_message !== "") {
        return {
            has_runtime: false,
            status_message,
            git: undefined,
            model_choices: [],
            effort_choices: [],
        };
    }
    return {
        has_runtime: true,
        status_message: "",
        git: git_display(),
        model_choices: model_choices(),
        effort_choices: effort_choices(),
    };
}

function render_popover_content(instance: tippy.Instance): void {
    instance.setContent(parse_html(render_pear_runtime_menu_popover(popover_context())));
}

function rerender_popover_if_open(): void {
    const instance = popover_menus.popover_instances.pear_runtime_menu;
    if (instance?.state.isVisible) {
        render_popover_content(instance);
    }
}

function update_navbar_button_label(): void {
    if (typeof document === "undefined") {
        return;
    }
    const $button = $("#pear-runtime-menu .header-button");
    if ($button.length === 0) {
        return;
    }
    let label = $t({defaultMessage: "Pear runtime"});
    if (topic_context !== undefined && context_key !== "") {
        const catalog_entry = topic_context.model_catalog?.find(
            (entry) => entry.slug === topic_context!.model,
        );
        const model_label = catalog_entry?.display_name ?? topic_context.model;
        const effort_label =
            model_reasoning_efforts(topic_context.model).find(
                (choice) => choice.effort === topic_context!.reasoning_effort,
            )?.label ?? topic_context.reasoning_effort;
        label = $t(
            {defaultMessage: "Pear runtime: {model} ({effort})"},
            {model: model_label, effort: effort_label},
        );
    }
    $button.attr("aria-label", label);
    $button.attr("data-tippy-content", label);
}

function fetch_topic_context(): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined) {
        topic_context = undefined;
        context_key = "";
        rerender_popover_if_open();
        return;
    }
    // Same endpoint and payload shape as pear_panel.refresh.
    void channel.get({
        url: "/json/pear/runs",
        data: {
            include_events: false,
            include_context: true,
            stream,
            topic,
        },
        success(raw_data) {
            const data = pear_runtime_response_schema.parse(raw_data);
            last_error = "";
            topic_context = data.pear_topic_context ?? undefined;
            context_key = narrow_context_key(stream, topic);
            update_navbar_button_label();
            rerender_popover_if_open();
        },
        error(xhr) {
            if (xhr.readyState === 0) {
                return;
            }
            last_error = $t({defaultMessage: "Pear runtime is unavailable."});
            blueslip.warn(`Failed to fetch Pear runtime: ${xhr.responseText}`);
            rerender_popover_if_open();
        },
    });
}

function save_runtime(model: string, reasoning_effort: string): void {
    const {stream, topic} = current_narrow();
    if (stream === undefined || topic === undefined || request_in_flight) {
        return;
    }
    request_in_flight = true;
    $("#pear-runtime-menu-popover input").prop("disabled", true);
    // Same endpoint and payload shape as pear_panel.save_topic_context.
    void channel.post({
        url: "/json/pear/topic-context",
        data: {
            stream,
            topic,
            model,
            reasoning_effort,
        },
        success(raw_data) {
            const data = pear_runtime_response_schema.parse(raw_data);
            last_error = "";
            if (data.pear_topic_context !== undefined && data.pear_topic_context !== null) {
                topic_context = data.pear_topic_context;
                context_key = narrow_context_key(stream, topic);
            }
            update_navbar_button_label();
            rerender_popover_if_open();
            // Keep the Pear panel in the right sidebar in sync.
            pear_panel.refresh();
        },
        error(xhr) {
            if (xhr.readyState !== 0) {
                last_error = $t({defaultMessage: "Failed to update the Pear runtime."});
                blueslip.warn(`Failed to update Pear runtime: ${xhr.responseText}`);
                rerender_popover_if_open();
            }
        },
        complete() {
            request_in_flight = false;
            $("#pear-runtime-menu-popover input").prop("disabled", false);
        },
    });
}

function checked_choice(name: string): string | undefined {
    const value = $(`#pear-runtime-menu-popover input[name='${name}']:checked`).val();
    return typeof value === "string" ? value : undefined;
}

function on_model_change(model: string): void {
    const efforts = model_reasoning_efforts(model);
    let effort =
        checked_choice("pear-runtime-effort") ?? topic_context?.reasoning_effort ?? "medium";
    if (!efforts.some((choice) => choice.effort === effort)) {
        const catalog_entry = topic_context?.model_catalog?.find((entry) => entry.slug === model);
        effort = catalog_entry?.default_reasoning_level ?? efforts[0]?.effort ?? effort;
    }
    save_runtime(model, effort);
}

function on_effort_change(effort: string): void {
    const model = checked_choice("pear-runtime-model") ?? topic_context?.model;
    if (model === undefined) {
        return;
    }
    save_runtime(model, effort);
}

export function initialize(): void {
    if (typeof document === "undefined") {
        // Node test environments mock the DOM minimally; this module
        // only makes sense in a browser.
        return;
    }

    // register_popover_menu attaches a body-delegated click handler,
    // so this works even if the navbar has not been rendered yet;
    // ui_init.js additionally calls us after initialize_navbar().
    popover_menus.register_popover_menu("#pear-runtime-menu", {
        theme: "popover-menu",
        placement: "bottom",
        offset: popover_menus.NAVBAR_POPOVER_OFFSET,
        // The strategy: "fixed"; and eventlisteners modifier option
        // ensure that the popover does not modify its position
        // or disappear when user zooms the page.
        popperOptions: {
            strategy: "fixed",
            modifiers: [
                {
                    name: "eventListeners",
                    options: {
                        scroll: false,
                    },
                },
            ],
        },
        onMount(instance) {
            popover_menus.popover_instances.pear_runtime_menu = instance;
        },
        onShow(instance) {
            const {stream, topic} = current_narrow();
            if (narrow_context_key(stream, topic) !== context_key) {
                // The cached runtime belongs to a different narrow.
                topic_context = undefined;
                context_key = "";
                update_navbar_button_label();
            }
            last_error = "";
            render_popover_content(instance);
            fetch_topic_context();
            $("#pear-runtime-menu").addClass("active-navbar-menu");
        },
        onHidden(instance) {
            instance.destroy();
            popover_menus.popover_instances.pear_runtime_menu = null;
            $("#pear-runtime-menu").removeClass("active-navbar-menu");
        },
    });

    $("body").on(
        "change",
        "#pear-runtime-menu-popover input[name='pear-runtime-model']",
        function (this: HTMLElement) {
            const model = $(this).val();
            if (typeof model === "string") {
                on_model_change(model);
            }
        },
    );

    $("body").on(
        "change",
        "#pear-runtime-menu-popover input[name='pear-runtime-effort']",
        function (this: HTMLElement) {
            const effort = $(this).val();
            if (typeof effort === "string") {
                on_effort_change(effort);
            }
        },
    );
}
