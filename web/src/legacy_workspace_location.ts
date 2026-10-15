export type ArmadaWorkspaceLocation = {
    view: "chat" | "readme" | "files";
    workspaceId: string | undefined;
    path: string | undefined;
    mode: "rich" | "source" | undefined;
};

export function read_armada_workspace_location(): ArmadaWorkspaceLocation {
    const url = new URL(window.location.href);
    const mode = url.searchParams.get("armada_mode");
    const requested_view = url.searchParams.get("armada_view");
    return {
        view:
            requested_view === "readme"
                ? "readme"
                : requested_view === "files" || requested_view === "workspace"
                  ? "files"
                  : "chat",
        workspaceId: url.searchParams.get("armada_workspace") ?? undefined,
        path: url.searchParams.get("armada_path") ?? undefined,
        mode: mode === "rich" || mode === "source" ? mode : undefined,
    };
}

export function write_armada_workspace_location(
    update: {
        view?: "chat" | "readme" | "files";
        workspaceId?: string | null;
        path?: string | null;
        mode?: "rich" | "source" | null;
    },
    history_mode: "push" | "replace",
): void {
    const url = new URL(window.location.href);
    if (update.view === "chat") {
        url.searchParams.delete("armada_view");
        url.searchParams.delete("armada_workspace");
        url.searchParams.delete("armada_path");
        url.searchParams.delete("armada_mode");
    } else {
        if (update.view === "readme" || update.view === "files") {
            url.searchParams.set("armada_view", update.view);
        }
        if (update.workspaceId === null) {
            url.searchParams.delete("armada_workspace");
        } else if (update.workspaceId !== undefined) {
            url.searchParams.set("armada_workspace", update.workspaceId);
        }
        if (update.path === null) {
            url.searchParams.delete("armada_path");
        } else if (update.path !== undefined) {
            url.searchParams.set("armada_path", update.path);
        }
        if (update.mode === null) {
            url.searchParams.delete("armada_mode");
        } else if (update.mode !== undefined) {
            url.searchParams.set("armada_mode", update.mode);
        }
    }
    if (url.href === window.location.href) {
        return;
    }
    if (history_mode === "push") {
        window.history.pushState(window.history.state, "", url);
    } else {
        window.history.replaceState(window.history.state, "", url);
    }
}
