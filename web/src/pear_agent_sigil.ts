import {$t} from "./i18n.ts";
import type * as pear_run_state from "./pear_run_state.ts";

/* Agent identity helpers shared by the thread pane (cycler, strip) and
   the sidebar roster: deterministic 3x3 sigil from the thread id, status
   tone, and display naming. Kept dependency-light so any surface can
   import it without cycles. */

function hash_thread_id(thread_id: string): number {
    let hash = 2_166_136_261;
    for (const char of thread_id) {
        hash = (hash * 16_777_619 + (char.codePointAt(0) ?? 0)) % 4_294_967_296;
    }
    return hash;
}

export function sigil_cells_for_testing(thread_id: string): boolean[] {
    const hash = hash_thread_id(thread_id);
    const cells = Array.from({length: 9}, () => false);
    for (let row = 0; row < 3; row += 1) {
        const edge = Math.floor(hash / 2 ** (row * 2)) % 2 !== 0;
        cells[row * 3] = edge;
        cells[row * 3 + 2] = edge;
        cells[row * 3 + 1] = Math.floor(hash / 2 ** (row * 2 + 1)) % 2 !== 0;
    }
    cells[4] = true;
    if (cells.filter(Boolean).length < 4) {
        cells[0] = true;
        cells[2] = true;
        cells[7] = true;
    }
    return cells;
}

export function status_tone_for_testing(status: string): "active" | "failed" | "idle" {
    const normalized = status.trim().toLowerCase();
    if (["active", "busy", "running", "working"].includes(normalized)) {
        return "active";
    }
    if (["error", "failed", "cancelled"].includes(normalized)) {
        return "failed";
    }
    return "idle";
}

export function display_name_for_testing(
    agent: pear_run_state.PearAgentThread,
    index: number,
): string {
    return (
        [agent.nickname, agent.role, agent.name]
            .map((value) => value?.trim())
            .find((value) => value !== undefined && value !== "") ??
        $t({defaultMessage: "Agent {number}"}, {number: index + 1})
    );
}

export function create_sigil(agent: pear_run_state.PearAgentThread): HTMLElement {
    const sigil = document.createElement("span");
    sigil.className = `message-thread-agent-sigil message-thread-agent-sigil-tone-${hash_thread_id(agent.thread_id) % 6}`;
    sigil.setAttribute("aria-hidden", "true");
    for (const active of sigil_cells_for_testing(agent.thread_id)) {
        const cell = document.createElement("i");
        cell.className = active
            ? "message-thread-agent-sigil-cell message-thread-agent-sigil-cell-on"
            : "message-thread-agent-sigil-cell";
        sigil.append(cell);
    }
    const status = document.createElement("span");
    status.className = `message-thread-agent-status message-thread-agent-status-${status_tone_for_testing(agent.status)}`;
    sigil.append(status);
    return sigil;
}
