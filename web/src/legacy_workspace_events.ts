export type ArmadaWorkspaceChangedEvent = {
    type: "workspace_changed";
    workspace_id: string;
    revision: number;
};

type Listener = (event: ArmadaWorkspaceChangedEvent) => void;

const listeners = new Set<Listener>();

export function apply_armada_workspace_changed_event(event: ArmadaWorkspaceChangedEvent): void {
    for (const listener of listeners) {
        listener(event);
    }
}

export function on_armada_workspace_changed(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}
