import type {
    PearWorkspaceApiError,
    PearWorkspaceEntry,
    pear_workspace_raw_url,
} from "./pear_workspace_api.ts";
import type {PearWorkspaceEditorSurface} from "./pear_workspace_editor_contract.ts";

const raster_extensions = new Set(["avif", "gif", "jpeg", "jpg", "png", "webp"]);
const binary_extensions = new Set([
    "7z",
    "bz2",
    "dmg",
    "doc",
    "docx",
    "gz",
    "ico",
    "jar",
    "mp3",
    "mp4",
    "mov",
    "pdf",
    "tar",
    "wasm",
    "xls",
    "xlsx",
    "zip",
]);

function extension_for_path(path: string): string {
    const filename = path.split("/").at(-1) ?? "";
    const dot = filename.lastIndexOf(".");
    return dot <= 0 ? "" : filename.slice(dot + 1).toLowerCase();
}

export function pear_workspace_api_error_message(error: PearWorkspaceApiError): string {
    return error.kind === "server" ? error.error.msg : error.message;
}

export function pear_workspace_initial_preview(
    workspace_id: string,
    entry: PearWorkspaceEntry,
    limits: {rawBytes: number | null; textBytes: number},
    raw_url: typeof pear_workspace_raw_url,
): PearWorkspaceEditorSurface | undefined {
    const extension = extension_for_path(entry.path);
    const size = entry.size ?? 0;
    if (raster_extensions.has(extension)) {
        if (limits.rawBytes !== null && size > limits.rawBytes) {
            return {
                kind: "oversize",
                entry,
                maxBytes: limits.rawBytes,
                rawAvailable: false,
            };
        }
        return {kind: "raster", entry, rawUrl: raw_url(workspace_id, entry.entry_id)};
    }
    if (binary_extensions.has(extension)) {
        if (limits.rawBytes !== null && size > limits.rawBytes) {
            return {
                kind: "oversize",
                entry,
                maxBytes: limits.rawBytes,
                rawAvailable: false,
            };
        }
        return {kind: "binary", entry, message: "Preview is not available for this file."};
    }
    if (size > limits.textBytes) {
        return {
            kind: "oversize",
            entry,
            maxBytes: limits.textBytes,
            rawAvailable: limits.rawBytes === null || size <= limits.rawBytes,
        };
    }
    return undefined;
}
