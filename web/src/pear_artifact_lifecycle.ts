export type PearArtifactDensity = "chat" | "wall";
export type PearArtifactLifecycle = "preparing" | "rendering" | "failed";

export function mount_pear_artifact_lifecycle(
    $host: JQuery,
    density: PearArtifactDensity,
    lifecycle: PearArtifactLifecycle,
    label: string,
): void {
    const shell = document.createElement("div");
    shell.className = "pear-artifact pear-artifact-lifecycle";
    shell.dataset["density"] = density;
    shell.dataset["lifecycle"] = lifecycle;
    shell.setAttribute("role", lifecycle === "failed" ? "alert" : "status");
    shell.setAttribute("aria-live", "polite");

    const cells = document.createElement("span");
    cells.className = "pear-artifact-lifecycle-cells";
    cells.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) {
        const cell = document.createElement("span");
        cell.className = "pear-artifact-lifecycle-cell";
        cells.append(cell);
    }

    const text = document.createElement("span");
    text.className = "pear-artifact-lifecycle-label";
    text.textContent = label;
    shell.append(cells, text);
    $host.empty();
    $host[0]?.append(shell);
}
