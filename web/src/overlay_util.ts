import $ from "jquery";

export function get_visible_focusable_elements_in_overlay_container(
    $container: JQuery,
): HTMLElement[] {
    const visible_focusable_elements = [
        ...$container.find(
            "input, button, select, .input, .sidebar-item, .ind-tab.first, a[href], a[tabindex='0']",
        ),
    ].filter(
        (element) =>
            element.getClientRects().length > 0 && $(element).css("visibility") !== "hidden",
    );
    return visible_focusable_elements;
}
