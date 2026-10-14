// The motion kit: the only caller of element.animate, and what every page
// gets without asking (the ring, the tooltip). Hover and press are the
// stylesheet's (kit/hover.css); the loops are kit/marks.css.

import {appear, release} from "./motion.ts";
import {ring} from "./ring.ts";
import {load} from "./springs.ts";
import {tip} from "./tip.ts";

export * from "./motion.ts";
export {ring, ring_target} from "./ring.ts";
export {say, tip} from "./tip.ts";
export {amounts, load, progress, reduced, springs, times} from "./springs.ts";
export type {SpringName, Vector} from "./springs.ts";
export {after_layout, watch} from "./guard.ts";

let keyboard = false;

// Whether focus is being moved by the keyboard: the ring shows only then.
export function by_keyboard(value?: boolean): boolean {
    if (value !== undefined) {
        keyboard = value;
    }
    return keyboard;
}

export function focus_by_keyboard(element: HTMLElement): void {
    keyboard = true;
    element.focus({preventScroll: true});
    ring(element);
}

function wants_ring(target: EventTarget | null): target is HTMLElement {
    return (
        target instanceof HTMLElement &&
        !target.isContentEditable &&
        !(target instanceof HTMLTextAreaElement) &&
        !("noring" in target.dataset) &&
        target.matches("button, a, [tabindex]")
    );
}

// Wire the ring and the tooltip to a document, and read the tokens. Call
// it once, at startup; the function it returns unwires it.
export function wire(root: Document = document): () => void {
    load();
    const view = root.defaultView!;
    const reduce = view.matchMedia("(prefers-reduced-motion: reduce)");
    const stop = new AbortController();
    const options = {capture: true, signal: stop.signal};
    reduce.addEventListener("change", load, {signal: stop.signal});
    root.addEventListener(
        "keydown",
        (event) => {
            if (event.key === "Tab" || event.key.startsWith("Arrow")) {
                keyboard = true;
            }
        },
        options,
    );
    root.addEventListener(
        "pointerdown",
        () => {
            keyboard = false;
            ring(null);
            tip(null);
        },
        options,
    );
    root.addEventListener(
        "focusin",
        (event) => {
            ring(keyboard && wants_ring(event.target) ? event.target : null);
        },
        {signal: stop.signal},
    );
    root.addEventListener(
        "focusout",
        (event) => {
            if (event.relatedTarget === null) {
                ring(null);
            }
        },
        {signal: stop.signal},
    );
    root.addEventListener(
        "pointerover",
        (event) => {
            const target =
                event.target instanceof Element ? event.target.closest("[data-tip]") : null;
            tip(target instanceof HTMLElement ? target : null);
        },
        {signal: stop.signal},
    );
    view.addEventListener(
        "blur",
        () => {
            ring(null);
            tip(null);
        },
        {signal: stop.signal},
    );
    return () => {
        stop.abort();
    };
}

// The first element.animate and the first trip to the top layer cost
// several milliseconds each; pay at idle, not on the first send.
export function warm(): void {
    const probe = document.createElement("i");
    probe.style.cssText =
        "position:fixed;left:-99px;top:-99px;width:1px;height:1px;opacity:0;pointer-events:none";
    document.body.append(probe);
    try {
        probe.setAttribute("popover", "manual");
        probe.showPopover();
        probe.hidePopover();
    } catch {
        // No top layer here; a flight falls back to its place.
    }
    release(probe, {y: 4, sx: 0.02}, "touch");
    appear(probe);
    for (const animation of probe.getAnimations()) {
        animation.finish();
    }
    probe.remove();
    ring(null);
}
