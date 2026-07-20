import $ from "jquery";

import {$t} from "./i18n.ts";
import * as message_thread_hooks from "./message_thread_hooks.ts";
import * as narrow_state from "./narrow_state.ts";
import {
    create_sigil,
    display_name_for_testing,
    status_tone_for_testing,
} from "./pear_agent_sigil.ts";
import * as pear_run_state from "./pear_run_state.ts";
import * as right_sidebar_tabs from "./right_sidebar_tabs.ts";

/* The Agents tab's home surface: every Codex child agent in the current
   topic context, live first, then recently settled. Selecting one opens
   its trace in the thread pane (one selection model with the cycler).
   Rendered like codex-cli's /agent picker: sigil, name [role], status,
   last activity. */

let roster_signature = "";
let initialized = false;

type RosterEntry = {
    agent: pear_run_state.PearAgentThread;
    run: pear_run_state.PearRunLiveView;
};

function noop_enhance(_root: HTMLElement): void {
    return;
}

function entries_for_current_context(): RosterEntry[] {
    const stream = narrow_state.stream_name();
    const topic = narrow_state.topic();
    if (stream === undefined || topic === undefined) {
        return [];
    }
    const entries = new Map<string, RosterEntry>();
    for (const run of pear_run_state.runs_for_context(stream, topic)) {
        for (const agent of run.agents ?? []) {
            entries.set(agent.thread_id, {agent, run});
        }
    }
    // Live agents first, then settled; stable nickname order within.
    const tone_rank = {active: 0, failed: 1, idle: 2};
    return [...entries.values()].toSorted((a, b) => {
        const rank_delta =
            tone_rank[status_tone_for_testing(a.agent.status)] -
            tone_rank[status_tone_for_testing(b.agent.status)];
        if (rank_delta !== 0) {
            return rank_delta;
        }
        return (b.run.date_created ?? 0) - (a.run.date_created ?? 0);
    });
}

function signature_for(entries: RosterEntry[], stream: string, topic: string): string {
    return [
        stream,
        topic,
        ...entries.map(({agent, run}) =>
            [
                agent.thread_id,
                agent.status,
                agent.current_activity ?? "",
                run.result_message_id ?? "",
            ].join(" "),
        ),
    ].join("");
}

function create_entry_row(entry: RosterEntry, index: number): HTMLElement {
    const {agent, run} = entry;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "pear-agent-roster-row";
    const name = display_name_for_testing(agent, index);
    row.setAttribute("aria-label", $t({defaultMessage: "Open {name} in the thread pane"}, {name}));

    row.append(create_sigil(agent));

    const text = document.createElement("span");
    text.className = "pear-agent-roster-text";
    const title = document.createElement("span");
    title.className = "pear-agent-roster-name";
    title.textContent = name;
    if (agent.role?.trim()) {
        const role = document.createElement("span");
        role.className = "pear-agent-roster-role";
        role.textContent = `[${agent.role.trim()}]`;
        title.append(" ", role);
    }
    const activity = document.createElement("span");
    activity.className = "pear-agent-roster-activity";
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
    const host = document.querySelector<HTMLElement>("#pear-agent-roster");
    if (host === null) {
        return;
    }
    const stream = narrow_state.stream_name() ?? "";
    const topic = narrow_state.topic() ?? "";
    const entries = entries_for_current_context();
    const signature = signature_for(entries, stream, topic);
    if (signature === roster_signature) {
        return;
    }
    roster_signature = signature;

    host.replaceChildren();
    if (entries.length === 0) {
        const empty = document.createElement("div");
        empty.className = "pear-agent-roster-empty";
        empty.textContent = $t({
            defaultMessage: "No agents in this topic yet. Ask @**Pear** for a few.",
        });
        host.append(empty);
        return;
    }
    const list = document.createElement("div");
    list.className = "pear-agent-roster-list";
    list.setAttribute("role", "list");
    for (const [index, entry] of entries.entries()) {
        const row = create_entry_row(entry, index);
        row.setAttribute("role", "listitem");
        list.append(row);
    }
    host.append(list);
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    initialized = true;
    pear_run_state.on_run_state_changed(() => {
        if (right_sidebar_tabs.current() === "agents") {
            render();
        } else {
            // Invalidate so the next switch to Agents re-renders fresh.
            roster_signature = "";
        }
    });
    $(window).on("hashchange", () => {
        roster_signature = "";
        render();
    });
    render();
}

export function clear_for_testing(): void {
    roster_signature = "";
    initialized = false;
}
