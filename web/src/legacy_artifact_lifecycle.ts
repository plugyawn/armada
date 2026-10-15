export type ArmadaArtifactDensity = "chat" | "wall";
export type ArmadaArtifactLifecycle = "preparing" | "rendering" | "failed";

type ArmadaArtifactHostCleanup = () => void;

const mounted_artifact_hosts = new Map<HTMLElement, ArmadaArtifactHostCleanup>();
let disconnect_observer: MutationObserver | undefined;

function host_element(host: JQuery | HTMLElement): HTMLElement | undefined {
    return host instanceof HTMLElement ? host : host[0];
}

function stop_disconnect_observer_when_idle(): void {
    if (mounted_artifact_hosts.size === 0) {
        disconnect_observer?.disconnect();
        disconnect_observer = undefined;
    }
}

function watch_for_disconnected_hosts(): void {
    if (
        disconnect_observer !== undefined ||
        mounted_artifact_hosts.size === 0 ||
        typeof MutationObserver === "undefined"
    ) {
        return;
    }
    disconnect_observer = new MutationObserver(() => {
        for (const host of mounted_artifact_hosts.keys()) {
            if (!host.isConnected) {
                unmount_armada_artifact(host);
            }
        }
    });
    disconnect_observer.observe(document.documentElement, {childList: true, subtree: true});
}

export function unmount_armada_artifact(host: JQuery | HTMLElement): void {
    const element = host_element(host);
    if (element === undefined) {
        return;
    }
    const cleanup = mounted_artifact_hosts.get(element);
    mounted_artifact_hosts.delete(element);
    try {
        cleanup?.();
    } finally {
        element.replaceChildren();
        stop_disconnect_observer_when_idle();
    }
}

export function replace_armada_artifact_host(
    host: JQuery | HTMLElement,
    content: HTMLElement,
    cleanup?: ArmadaArtifactHostCleanup,
): void {
    const element = host_element(host);
    if (element === undefined) {
        cleanup?.();
        return;
    }
    unmount_armada_artifact(element);
    element.append(content);
    if (cleanup !== undefined) {
        mounted_artifact_hosts.set(element, cleanup);
        watch_for_disconnected_hosts();
    }
}

export function mount_armada_artifact_lifecycle(
    $host: JQuery,
    density: ArmadaArtifactDensity,
    lifecycle: ArmadaArtifactLifecycle,
    label: string,
): void {
    const shell = document.createElement("div");
    shell.className = "legacy-artifact legacy-artifact-lifecycle";
    shell.dataset["density"] = density;
    shell.dataset["lifecycle"] = lifecycle;
    shell.setAttribute("role", lifecycle === "failed" ? "alert" : "status");
    shell.setAttribute("aria-live", "polite");

    const cells = document.createElement("span");
    cells.className = "legacy-artifact-lifecycle-cells";
    cells.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) {
        const cell = document.createElement("span");
        cell.className = "legacy-artifact-lifecycle-cell";
        cells.append(cell);
    }

    const text = document.createElement("span");
    text.className = "legacy-artifact-lifecycle-label";
    text.textContent = label;
    shell.append(cells, text);
    replace_armada_artifact_host($host, shell);
}
