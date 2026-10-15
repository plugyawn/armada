// The old right rail (the wall, the agent roster, the people and the
// thread pane) in a sheet from the window's right edge, closed at rest.
// Deleted by B4 (B8 for the wall and the people), when the column holds
// what the rail held. A thread opens it by itself (legacy_column.css).

import "./legacy_column.css";

function sheet(): HTMLElement | null {
    return document.querySelector<HTMLElement>("[data-armada=sheet]");
}

// What had focus when the sheet opened (More, or the column's heading):
// focus goes back there when the sheet closes by a key or a press on it.
let opener: HTMLElement | undefined;

// The sheet's first control that can take focus: the rail's filter, or
// the first of its controls the rail shows at the time.
function focus_first_control(element: HTMLElement): void {
    for (const control of element.querySelectorAll<HTMLElement>(
        "input:not(:disabled), button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])",
    )) {
        control.focus({preventScroll: true});
        if (document.activeElement === control) {
            return;
        }
    }
}

export function is_open(): boolean {
    return sheet()?.dataset["state"] === "open";
}

export function open(): void {
    const element = sheet();
    if (element === null || is_open()) {
        return;
    }
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    element.setAttribute("data-state", "open");
    focus_first_control(element);
}

export function close({restore_focus = true}: {restore_focus?: boolean} = {}): void {
    const element = sheet();
    if (element === null || !is_open()) {
        return;
    }
    const focus_was_inside = element.contains(document.activeElement);
    element.removeAttribute("data-state");
    if (restore_focus && focus_was_inside) {
        opener?.focus({preventScroll: true});
    }
    opener = undefined;
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
        // The press puts focus where it lands.
        close({restore_focus: false});
    });
}
