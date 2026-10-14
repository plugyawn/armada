// The old right rail (the wall, the agent roster, the people and the
// thread pane) in a sheet from the window's right edge, closed at rest.
// Deleted by B4 (B8 for the wall and the people), when the column holds
// what the rail held. A thread opens it by itself (legacy_column.css).

import "./legacy_column.css";

function sheet(): HTMLElement | null {
    return document.querySelector<HTMLElement>("[data-armada=sheet]");
}

export function is_open(): boolean {
    return sheet()?.dataset["state"] === "open";
}

export function open(): void {
    sheet()?.setAttribute("data-state", "open");
}

export function close(): void {
    sheet()?.removeAttribute("data-state");
}

export function toggle(): void {
    if (is_open()) {
        close();
    } else {
        open();
    }
}

export function initialize(): void {
    // Esc closes the sheet and nothing else: it is the topmost layer.
    document.addEventListener(
        "keydown",
        (event) => {
            if (event.key === "Escape" && is_open()) {
                event.preventDefault();
                event.stopPropagation();
                close();
            }
        },
        {capture: true},
    );
    // A press outside it closes it, except on what opens it.
    document.addEventListener("pointerdown", (event) => {
        const target = event.target;
        if (
            !is_open() ||
            !(target instanceof Element) ||
            target.closest("[data-armada=sheet], [data-armada=column], [data-armada=more]") !== null
        ) {
            return;
        }
        close();
    });
}
