// The kit's keyboard. A group of choices (a menu, the picker's models and
// its efforts, segments, views) is one Tab stop: the arrows move between
// its items, Home and End go to its ends, and the item focus left is the
// one Tab comes back to. A raised surface opens in the top layer, a dialog
// as the platform's modal <dialog> and the rest as popovers, so Esc and a
// press outside close it with no script of ours; focus goes back to what
// opened it however it closes. A palette keeps focus in its line of input
// and moves its highlighted row instead.
//
// Nothing here reads layout: what is next is document order.

import type {Handler} from "./tree.ts";

export type Axis = "vertical" | "horizontal";

const KEYS: Record<Axis, {next: string; previous: string}> = {
    vertical: {next: "ArrowDown", previous: "ArrowUp"},
    horizontal: {next: "ArrowRight", previous: "ArrowLeft"},
};

// A group's own items: not those of a group inside it, nor a disabled
// one, which a button cannot be focused as.
function items_of(group: Element): HTMLElement[] {
    return [...group.querySelectorAll<HTMLElement>("[data-rove]")].filter(
        (item) => item.closest("[data-roving]") === group && !item.matches(":disabled"),
    );
}

function index_to(key: string, axis: Axis, at: number, count: number): number | undefined {
    if (key === "Home") {
        return 0;
    }
    if (key === "End") {
        return count - 1;
    }
    // From outside the group's items (a group inside it), the arrows
    // enter at the near end.
    if (key === KEYS[axis].next) {
        return (at + 1) % count;
    }
    if (key === KEYS[axis].previous) {
        return at === -1 ? count - 1 : (at - 1 + count) % count;
    }
    return undefined;
}

// The attributes of a group whose items the arrows move between.
export function roving(axis: Axis): {
    attributes: {"data-roving": Axis};
    on: {keydown: Handler};
} {
    return {
        attributes: {"data-roving": axis},
        on: {
            keydown(event) {
                const group = event.currentTarget;
                if (!(event instanceof KeyboardEvent) || !(group instanceof Element)) {
                    return;
                }
                const items = items_of(group);
                const at = items.findIndex((item) => item.contains(document.activeElement));
                const to = index_to(event.key, axis, at, items.length);
                if (to === undefined || items.length === 0) {
                    return;
                }
                // A group inside another answers its own arrows.
                event.preventDefault();
                event.stopPropagation();
                for (const [index, item] of items.entries()) {
                    item.tabIndex = index === to ? 0 : -1;
                }
                items[to]!.focus();
            },
        },
    };
}

// The attributes of one item of a group: the current one is the group's
// Tab stop (the first when none is).
export function rove_item(is_stop: boolean): {"data-rove": true; tabindex: 0 | -1} {
    return {"data-rove": true, tabindex: is_stop ? 0 : -1};
}

// Which of a group's items is its Tab stop: the current one, else the
// first that is not disabled.
export function stop_of(items: {current: boolean; disabled?: boolean | undefined}[]): number {
    const current = items.findIndex((item) => item.current);
    return current === -1 ? items.findIndex((item) => !item.disabled) : current;
}

// ---- raised surfaces

const openers = new WeakMap<HTMLElement, HTMLElement>();

function give_focus_back(surface: HTMLElement): void {
    const opener = openers.get(surface);
    openers.delete(surface);
    // Only when focus was inside: a press elsewhere keeps where it went.
    if (
        opener?.isConnected &&
        (document.activeElement === null ||
            document.activeElement === document.body ||
            surface.contains(document.activeElement))
    ) {
        opener.focus();
    }
}

function closed(event: Event): void {
    const surface = event.currentTarget;
    if (
        surface instanceof HTMLElement &&
        (event.type === "close" || ("newState" in event && event.newState === "closed"))
    ) {
        give_focus_back(surface);
    }
}

// Open a raised surface from `opener` (what has focus, by default) and put
// focus on its Tab stop: the current item of its first group, or its first
// control.
export function open_raised(
    surface: HTMLElement,
    opener: Element | null = document.activeElement,
): void {
    if (opener instanceof HTMLElement) {
        openers.set(surface, opener);
    }
    if (surface instanceof HTMLDialogElement) {
        surface.addEventListener("close", closed, {once: true});
        surface.showModal();
    } else {
        surface.addEventListener("toggle", closed);
        surface.showPopover();
    }
    const first =
        surface.querySelector<HTMLElement>("[data-rove][tabindex='0']") ??
        surface.querySelector<HTMLElement>("button:not(:disabled), textarea, input");
    first?.focus();
}

export function close_raised(surface: HTMLElement): void {
    if (surface instanceof HTMLDialogElement) {
        surface.close();
    } else {
        surface.hidePopover();
    }
}

// A menu closes when one of its items is chosen.
export const close_on_choice: Handler = (event) => {
    const menu = event.currentTarget;
    if (
        menu instanceof HTMLElement &&
        event.target instanceof Element &&
        event.target.closest("[data-rove]") !== null &&
        menu.hasAttribute("popover")
    ) {
        close_raised(menu);
    }
};

// ---- the palette: focus stays in its input; the arrows move the
// highlighted row, and Return presses it.

export const palette_keys: Handler = (event) => {
    const palette = event.currentTarget;
    if (!(event instanceof KeyboardEvent) || !(palette instanceof Element)) {
        return;
    }
    const rows = [...palette.querySelectorAll<HTMLButtonElement>("[role=option]:not(:disabled)")];
    const at = rows.findIndex((row) => row.hasAttribute("data-highlighted"));
    if (event.key === "Enter") {
        if (at !== -1) {
            event.preventDefault();
            rows[at]!.click();
        }
        return;
    }
    const to = index_to(event.key, "vertical", at, rows.length);
    if (to === undefined || rows.length === 0 || event.key === "Home" || event.key === "End") {
        // Home and End move the caret in the input.
        return;
    }
    event.preventDefault();
    for (const [index, row] of rows.entries()) {
        row.toggleAttribute("data-highlighted", index === to);
    }
    const input = palette.querySelector("input, textarea, [role=combobox]");
    if (input !== null && rows[to]!.id !== "") {
        input.setAttribute("aria-activedescendant", rows[to]!.id);
    }
};
