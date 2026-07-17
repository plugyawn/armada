export type PearWorkspaceChangedEvent = {
    type: "pear_workspace_changed";
    workspace_id: string;
    revision: number;
};

type Listener = (event: PearWorkspaceChangedEvent) => void;

const listeners = new Set<Listener>();

export function apply_pear_workspace_changed_event(event: PearWorkspaceChangedEvent): void {
    for (const listener of listeners) {
        listener(event);
    }
}

export function on_pear_workspace_changed(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}
