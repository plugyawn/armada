import $ from "jquery";

import {$t} from "./i18n.ts";
import {create_sigil, display_name_for_testing} from "./legacy_agent_sigil.ts";
import * as legacy_run_state from "./legacy_run_state.ts";
import * as message_thread_hooks from "./message_thread_hooks.ts";
import * as narrow_state from "./narrow_state.ts";
import * as right_sidebar_tabs from "./right_sidebar_tabs.ts";
import * as timerender from "./timerender.ts";

/* The Agents tab's home surface: the topic's Codex child agents grouped
   by the turn that spawned them (codex-cli has no cross-turn agent
   identity -- each run's children are fresh threads). Live turns first,
   then recently settled. A turn header opens the turn itself; an agent
   row opens that agent's trace in the thread pane, one selection model
   with the cycler. Rendered like codex-cli's /agent picker: sigil, name
   [role], status, last activity. */

let roster_signature = "";
let roster_filter_query = "";
let initialized = false;
const landed_turn_ids = new Set<number>();

type RosterEntry = {
    agent: legacy_run_state.ArmadaAgentThread;
    run: legacy_run_state.ArmadaRunLiveView;
};

type TurnGroup = {
    run: legacy_run_state.ArmadaRunLiveView;
    entries: RosterEntry[];
};

function noop_enhance(_root: HTMLElement): void {
    return;
}

function run_is_live(run: legacy_run_state.ArmadaRunLiveView): boolean {
    return ["active", "busy", "running", "working", "starting", "queued"].includes(
        run.status.trim().toLowerCase(),
    );
}

function entry_matches_filter({agent}: RosterEntry, query: string): boolean {
    if (query === "") {
        return true;
    }
    return [agent.nickname, agent.role, agent.name, agent.status, agent.current_activity]
        .filter((value) => value !== undefined && value !== null && value !== "")
        .join(" ")
        .toLowerCase()
        .includes(query);
}

function groups_for_current_context(): TurnGroup[] {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined) {
        return [];
    }
    const groups: TurnGroup[] = [];
    const query = roster_filter_query;
    for (const run of legacy_run_state.runs_for_context(stream, topic)) {
        const entries = (run.agents ?? [])
            .map((agent) => ({agent, run}))
            .filter((entry) => entry_matches_filter(entry, query));
        if (entries.length > 0) {
            groups.push({run, entries});
        }
    }
    return groups.toSorted((a, b) => {
        const live_delta = Number(run_is_live(b.run)) - Number(run_is_live(a.run));
        if (live_delta !== 0) {
            return live_delta;
        }
        return (b.run.date_created ?? 0) - (a.run.date_created ?? 0);
    });
}

function signature_for(groups: TurnGroup[], stream: string, topic: string): string {
    return [
        stream,
        topic,
        ...groups.flatMap(({run, entries}) => [
            `run:${run.id}:${run.status}:${run.result_message_id ?? ""}`,
            ...entries.map(({agent}) =>
                [agent.thread_id, agent.status, agent.current_activity ?? ""].join(" "),
            ),
        ]),
    ].join(" ");
}

function format_tokens(total_tokens: number | undefined): string | undefined {
    if (total_tokens === undefined) {
        return undefined;
    }
    if (total_tokens >= 1_000_000) {
        return `${(total_tokens / 1_000_000).toFixed(1)}M tokens`;
    }
    if (total_tokens >= 1000) {
        return `${(total_tokens / 1000).toFixed(1)}k tokens`;
    }
    return `${total_tokens} tokens`;
}

function format_duration(duration_seconds: number | null | undefined): string | undefined {
    if (duration_seconds === null || duration_seconds === undefined) {
        return undefined;
    }
    const seconds = Math.round(duration_seconds);
    if (seconds < 60) {
        return `${seconds}s`;
    }
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function turn_header_text_for_testing(run: legacy_run_state.ArmadaRunLiveView): string {
    const parts = [
        format_duration(run.usage?.duration_seconds),
        run.tool_count !== undefined
            ? $t(
                  {defaultMessage: "{count, plural, one {# tool} other {# tools}}"},
                  {
                      count: run.tool_count,
                  },
              )
            : undefined,
        format_tokens(run.usage?.total_tokens),
        run.date_created !== undefined
            ? timerender.relative_time_string_from_date(new Date(run.date_created * 1000), true)
            : undefined,
    ].filter((part) => part !== undefined && part !== "");
    const meta = parts.join(" · ");
    return meta === "" ? "run" : `run ${meta}`;
}

function create_turn_header(group: TurnGroup): HTMLElement {
    const {run} = group;
    const header = document.createElement("button");
    header.type = "button";
    header.className = "legacy-agent-roster-turn";
    if (run_is_live(run)) {
        header.classList.add("legacy-agent-roster-turn-live");
    }
    header.textContent = turn_header_text_for_testing(run);
    header.setAttribute("aria-label", $t({defaultMessage: "Open this turn in the thread pane"}));
    header.addEventListener("click", () => {
        if (typeof run.result_message_id === "number") {
            message_thread_hooks.open_thread(
                run.result_message_id,
                message_thread_hooks.HUMAN_THREAD_KEY,
                header,
                noop_enhance,
            );
        }
    });
    return header;
}

function create_entry_row(entry: RosterEntry, index: number): HTMLElement {
    const {agent, run} = entry;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "legacy-agent-roster-row";
    const name = display_name_for_testing(agent, index);
    row.setAttribute("aria-label", $t({defaultMessage: "Open {name} in the thread pane"}, {name}));

    row.append(create_sigil(agent));

    const text = document.createElement("span");
    text.className = "legacy-agent-roster-text";
    const title = document.createElement("span");
    title.className = "legacy-agent-roster-name";
    title.textContent = name;
    if (agent.role?.trim()) {
        const role = document.createElement("span");
        role.className = "legacy-agent-roster-role";
        role.textContent = `[${agent.role.trim()}]`;
        title.append(" ", role);
    }
    const activity = document.createElement("span");
    activity.className = "legacy-agent-roster-activity";
    const current_activity = agent.current_activity?.trim();
    activity.textContent =
        current_activity !== undefined && current_activity !== "" ? current_activity : agent.status;
    text.append(title, activity);
    row.append(text);

    row.addEventListener("click", () => {
        if (typeof run.result_message_id === "number") {
            message_thread_hooks.open_thread(
                run.result_message_id,
                agent.thread_id,
                row,
                noop_enhance,
            );
        }
    });
    return row;
}

export function render(): void {
    const host = document.querySelector<HTMLElement>("#legacy-agent-roster");
    if (host === null) {
        return;
    }
    const stream = narrow_state.stream_name() ?? "";
    const topic = narrow_state.topic() ?? "";
    const groups = groups_for_current_context();
    const signature = signature_for(groups, stream, topic);
    if (signature === roster_signature) {
        return;
    }
    roster_signature = signature;
    host.hidden = false;

    host.replaceChildren();
    if (groups.length === 0) {
        const empty = document.createElement("div");
        empty.className = "legacy-agent-roster-empty";
        empty.textContent =
            roster_filter_query === ""
                ? $t({
                      defaultMessage: "No agents in this topic yet. Ask @**Pear** for a few.",
                  })
                : $t({defaultMessage: "No agents match the filter."});
        host.append(empty);
        return;
    }
    const list = document.createElement("div");
    list.className = "legacy-agent-roster-list";
    list.setAttribute("role", "list");
    for (const group of groups) {
        const header = create_turn_header(group);
        header.setAttribute("role", "listitem");
        list.append(header);
        for (const [index, entry] of group.entries.entries()) {
            const row = create_entry_row(entry, index);
            row.setAttribute("role", "listitem");
            list.append(row);
        }
    }
    host.append(list);
}

function maybe_land_on_live_turn(): void {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined) {
        return;
    }
    const live_turn = legacy_run_state
        .runs_for_context(stream, topic)
        .find((run) => run_is_live(run) && (run.agents ?? []).length > 0);
    if (
        live_turn === undefined ||
        typeof live_turn.result_message_id !== "number" ||
        landed_turn_ids.has(live_turn.id)
    ) {
        return;
    }
    // First sighting of a live turn with agents: land on it (Replies;
    // the cycler walks its agents one Alt+arrow away). Once per turn per
    // session, so the roster stays reachable afterwards.
    landed_turn_ids.add(live_turn.id);
    message_thread_hooks.open_thread(
        live_turn.result_message_id,
        message_thread_hooks.HUMAN_THREAD_KEY,
        document.querySelector<HTMLElement>("#legacy-agent-roster") ?? document.body,
        noop_enhance,
    );
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    legacy_run_state.on_run_state_changed(() => {
        if (right_sidebar_tabs.current() === "agents") {
            render();
        } else {
            // Invalidate so the next switch to Agents re-renders fresh.
            roster_signature = "";
        }
    });
    right_sidebar_tabs.on_tab_changed((tab) => {
        if (tab === "agents") {
            render();
            maybe_land_on_live_turn();
        }
    });
    $("#userlist-header").on("input", ".user-list-filter", (event) => {
        roster_filter_query =
            event.target instanceof HTMLInputElement ? event.target.value.trim().toLowerCase() : "";
        if (right_sidebar_tabs.current() === "agents") {
            roster_signature = "";
            render();
        }
    });
    window.addEventListener("hashchange", () => {
        roster_signature = "";
        render();
    });
    render();
}

export function clear_for_testing(): void {
    roster_signature = "";
    roster_filter_query = "";
    landed_turn_ids.clear();
    initialized = false;
}
