import $ from "jquery";

export function handle_overlay_scrollbars(): void {
    // The layout is always full width, so whenever the browser uses
    // overlay scrollbars, the right column's scrollbar can overlap
    // the browser scrollbar; move it to the left in that case.
    const scrollbar_width = window.innerWidth - document.documentElement.clientWidth;
    $("body").toggleClass("has-overlay-scrollbar", scrollbar_width === 0);
}

export function initialize(): void {
    handle_overlay_scrollbars();
}
