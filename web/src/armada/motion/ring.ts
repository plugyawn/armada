// The window's one focus ring: one object, shown for keyboard focus only.
// It arrives from 3 px out; between neighbours it travels; and while it
// shows it follows its control through scroll, resize and layout. It reads
// its control's place in the next animation frame, where layout is due
// anyway, never in the task of the input that moved focus: that task may
// have written (a menu opening, a group moving its Tab stop).

import {in_top_layer, into_top_layer, raise} from "./layer.ts";
import {fade, halt, release, rest} from "./motion.ts";
import {amounts} from "./springs.ts";

type Place = {x: number; y: number; w: number; h: number};

// 2 px off its control, on its radius.
const GAP = 2;

let ring_element: HTMLElement | undefined;
let ring_at: Place | null = null;
let ring_for: Element | null = null;
let follow_frame = 0;
// Focus has moved to ring_for and the ring has not yet gone there.
let arriving = false;

function box_of(target: Element): Place {
    const box = rest(target);
    return {x: box.left - GAP, y: box.top - GAP, w: box.width + 2 * GAP, h: box.height + 2 * GAP};
}

function place(element: HTMLElement, to: Place, target: Element): void {
    const radius = Number.parseFloat(getComputedStyle(target).borderTopLeftRadius) || 0;
    element.style.width = `${to.w}px`;
    element.style.height = `${to.h}px`;
    element.style.translate = `${to.x}px ${to.y}px`;
    element.style.borderRadius = `${Math.min(radius + GAP, to.h / 2)}px`;
}

function moved(a: Place, b: Place): boolean {
    return (
        Math.abs(a.x - b.x) > 0.25 ||
        Math.abs(a.y - b.y) > 0.25 ||
        Math.abs(a.w - b.w) > 0.25 ||
        Math.abs(a.h - b.h) > 0.25
    );
}

// While the ring shows it is re-placed whenever its control's resting
// place may have changed: a scroll anywhere, a resize of the window or of
// the control, or a change to the document. Each wakes a loop of frames
// that reads the control's place and stops once it has held for two
// frames, so a ring at rest costs nothing. A spring on the control or
// above it is not a change of place.
const SETTLED_FRAMES = 2;
let still_frames = 0;
let unwatch: (() => void) | undefined;

function step(): void {
    follow_frame = 0;
    const target = ring_for;
    if (target === null || ring_element === undefined) {
        return;
    }
    if (!target.isConnected || target.closest("[hidden], [inert]") !== null) {
        ring(null);
        return;
    }
    const to = box_of(target);
    if (arriving) {
        arriving = false;
        arrive(ring_element, to, target);
        still_frames = 0;
    } else if (ring_at === null || moved(to, ring_at)) {
        place(ring_element, to, target);
        ring_at = to;
        still_frames = 0;
    } else {
        still_frames += 1;
    }
    if (still_frames < SETTLED_FRAMES) {
        follow_frame = requestAnimationFrame(step);
    }
}

function wake(): void {
    still_frames = 0;
    if (follow_frame === 0 && ring_for !== null) {
        follow_frame = requestAnimationFrame(step);
    }
}

function follow(target: Element): void {
    unwatch?.();
    const resized = new ResizeObserver(wake);
    resized.observe(target);
    // The ring's own writes are not a change of its control's place.
    const changed = new MutationObserver((records) => {
        if (records.some((record) => record.target !== ring_element)) {
            wake();
        }
    });
    changed.observe(document.body, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true,
    });
    const options = {capture: true, passive: true};
    window.addEventListener("scroll", wake, options);
    window.addEventListener("resize", wake, options);
    unwatch = () => {
        resized.disconnect();
        changed.disconnect();
        window.removeEventListener("scroll", wake, options);
        window.removeEventListener("resize", wake, options);
        unwatch = undefined;
    };
    wake();
}

// A frame already asked for finds no ring to place, and stops.
function stop_following(): void {
    unwatch?.();
}

export function ring_target(): Element | null {
    return ring_for;
}

export function ring(target: Element | null): void {
    if (ring_element === undefined) {
        ring_element = document.createElement("i");
        ring_element.className = "armada-ring";
        ring_element.dataset["armada"] = "ring";
        document.body.append(ring_element);
        into_top_layer(ring_element);
    }
    if (target === null) {
        ring_at = null;
        ring_for = null;
        arriving = false;
        stop_following();
        fade(ring_element, 0);
        return;
    }
    if (in_top_layer(target)) {
        raise(ring_element);
    }
    ring_for = target;
    arriving = true;
    follow(target);
}

function arrive(element: HTMLElement, to: Place, target: Element): void {
    const was = ring_at;
    place(element, to, target);
    ring_at = to;
    fade(element, 1);
    const near =
        was !== null &&
        Math.hypot(was.x + was.w / 2 - to.x - to.w / 2, was.y + was.h / 2 - to.y - to.h / 2) <=
            amounts["ring-near"];
    if (near) {
        release(
            element,
            {x: was.x - to.x, y: was.y - to.y, sx: was.w / to.w - 1, sy: was.h / to.h - 1},
            "touch",
        );
    } else {
        halt(element);
        release(element, {x: -3, y: -3, sx: 6 / to.w, sy: 6 / to.h}, "touch");
    }
}
