export type ArmadaWorkspaceSourceLanguage =
    | "plain"
    | "markdown"
    | "javascript"
    | "typescript"
    | "json"
    | "python"
    | "css"
    | "html"
    | "yaml";

export function armada_workspace_source_language_for_path(
    path: string,
): ArmadaWorkspaceSourceLanguage {
    const extension = path.split("/").at(-1)?.toLowerCase().split(".").at(-1);
    switch (extension) {
        case "md":
        case "markdown":
        case "mdx":
            return "markdown";
        case "js":
        case "jsx":
        case "mjs":
        case "cjs":
            return "javascript";
        case "ts":
        case "tsx":
        case "mts":
        case "cts":
            return "typescript";
        case "json":
            return "json";
        case "py":
        case "pyi":
            return "python";
        case "css":
            return "css";
        case "html":
        case "htm":
            return "html";
        case "yaml":
        case "yml":
            return "yaml";
        default:
            return "plain";
    }
}
