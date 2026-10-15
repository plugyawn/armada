import * as sent_messages from "./sent_messages.ts";
import * as server_events_state from "./server_events_state.ts";
import {current_user} from "./state_data.ts";
import * as transmit from "./transmit.ts";

const ARMADA_AGENT_MENTION = "@**Pear**";
const ARMADA_ARTIFACT_ACTION_PROMPT_LIMIT = 1000;

export type ArmadaArtifactActionTarget = {
    stream: string;
    topic: string;
};

export function send_armada_artifact_action(
    target: ArmadaArtifactActionTarget,
    prompt: string,
    callbacks: {success: () => void; error: (message: string) => void},
): void {
    const instruction = prompt.trim().slice(0, ARMADA_ARTIFACT_ACTION_PROMPT_LIMIT);
    if (instruction === "") {
        callbacks.error("empty action");
        return;
    }
    transmit.send_message(
        {
            type: "stream",
            local_id: sent_messages.get_new_local_id(),
            sender_id: current_user.user_id,
            queue_id: server_events_state.queue_id,
            to: target.stream,
            topic: target.topic,
            content: `${ARMADA_AGENT_MENTION} ${instruction}`,
        },
        callbacks.success,
        (message) => {
            callbacks.error(message);
        },
    );
}
