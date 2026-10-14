// The window's ring and tooltip stand over whatever is open. Each is a
// manual popover, shown once when it is made, and shown again, so that it
// is last in the top layer, when what it is for is itself in the top layer
// (a menu, a sheet, a dialog). A manual popover closes nothing else.

export function into_top_layer(element: HTMLElement): void {
    element.setAttribute("popover", "manual");
    raise(element);
}

export function raise(element: HTMLElement): void {
    try {
        element.hidePopover();
    } catch {
        // It was not shown yet.
    }
    try {
        element.showPopover();
    } catch {
        // No top layer here: it stands where the page put it.
    }
}

// Whether a control is inside something in the top layer.
export function in_top_layer(target: Element): boolean {
    return target.closest("[popover], dialog") !== null;
}
