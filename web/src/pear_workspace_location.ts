export type PearWorkspaceLocation = {
    view: "chat" | "readme" | "files";
    workspaceId: string | undefined;
    path: string | undefined;
    mode: "rich" | "source" | undefined;
};

export function read_pear_workspace_location(): PearWorkspaceLocation {
    const url = new URL(window.location.href);
    const mode = url.searchParams.get("pear_mode");
    const requested_view = url.searchParams.get("pear_view");
    return {
        view:
            requested_view === "readme"
                ? "readme"
                : requested_view === "files" || requested_view === "workspace"
                  ? "files"
                  : "chat",
        workspaceId: url.searchParams.get("pear_workspace") ?? undefined,
        path: url.searchParams.get("pear_path") ?? undefined,
        mode: mode === "rich" || mode === "source" ? mode : undefined,
    };
}

export function write_pear_workspace_location(
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
        url.searchParams.delete("pear_view");
        url.searchParams.delete("pear_workspace");
        url.searchParams.delete("pear_path");
        url.searchParams.delete("pear_mode");
    } else {
        if (update.view === "readme" || update.view === "files") {
            url.searchParams.set("pear_view", update.view);
        }
        if (update.workspaceId === null) {
            url.searchParams.delete("pear_workspace");
        } else if (update.workspaceId !== undefined) {
            url.searchParams.set("pear_workspace", update.workspaceId);
        }
        if (update.path === null) {
            url.searchParams.delete("pear_path");
        } else if (update.path !== undefined) {
            url.searchParams.set("pear_path", update.path);
        }
        if (update.mode === null) {
            url.searchParams.delete("pear_mode");
        } else if (update.mode !== undefined) {
            url.searchParams.set("pear_mode", update.mode);
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
