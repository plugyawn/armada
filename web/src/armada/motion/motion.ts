// The motion kit, script half: the one module that animates. Everything
// here moves transform and opacity only, starts in the task that calls it
// and never holds a result back: callers write the new state first, then
// ask for the difference to be released. The names are the design's.
//
//   release(el, {x, y, sx, sy}, "move")  displace el from its rest by this
//       much and let it go on the named spring. If el is already in flight
//       it starts from where it visibly is and at the speed it is moving,
//       so nothing jumps and nothing queues.
//   flip(el, before, "move")   release the difference between a rectangle
//       read before a write and el's rest now
//   rest(el)                   el's rectangle with every spring in flight
//       on it and its ancestors taken out
//   fade(el, to)  leave(el)    opacity, from its current value
//   thumb, figure, swap, cross, grow, fly, carry, land, kick, squash,
//   burst, shake, refuse       the fixed choreography's pieces

import * as guard from "./guard.ts";
import {amounts, flight, reduced, sample, springs, times} from "./springs.ts";
import type {Flight, SpringName, Vector} from "./springs.ts";

// A timing function that is not a spring is linear.
const LINEAR = "linear";

type InFlight = Flight & {started: number; lasts: number; animation: Animation};

const in_flight = new WeakMap<Element, Map<string, InFlight>>();

// Run `done` when an animation ends; `cancelled` when something newer
// took its place.
async function when_finished(
    animation: Animation,
    done: () => void,
    cancelled: () => void = done,
): Promise<void> {
    try {
        await animation.finished;
    } catch {
        cancelled();
        return;
    }
    done();
}

async function when_all_finished(animations: Animation[], done: () => void): Promise<void> {
    await Promise.allSettled(animations.map(async (animation) => animation.finished));
    done();
}

function nothing(): void {
    // Whoever cancelled the animation owns what it was moving.
}

export function now(): number {
    return performance.now();
}

function transform_of(vector: Vector): string {
    const {x = 0, y = 0, sx = 0, sy = 0} = vector;
    return `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px) scale(${(1 + sx).toFixed(4)}, ${(1 + sy).toFixed(4)})`;
}

// Where el's spring has it now, as displacement from rest, and how fast.
export function current(element: Element, id = "m"): [Vector, Vector] {
    const spring = in_flight.get(element)?.get(id);
    if (spring === undefined) {
        return [{}, {}];
    }
    // Where the animation itself is: it starts on the frame after the call
    // that made it, so the clock of the call would run a frame ahead.
    const time = spring.animation.currentTime;
    const elapsed = typeof time === "number" ? time : now() - spring.started;
    return elapsed >= spring.lasts ? [{}, {}] : sample(spring, elapsed / 1000);
}

export function halt(element: Element, id = "m"): void {
    const all = in_flight.get(element);
    const spring = all?.get(id);
    if (all !== undefined && spring !== undefined) {
        spring.animation.cancel();
        all.delete(id);
    }
}

export type ReleaseOptions = {
    // A second spring on the same element (a kick, a squash) has its own.
    id?: string | undefined;
    // An impulse, in units a second.
    kick?: Vector | undefined;
    // The furthest a channel may start from rest.
    clamp?: Vector | undefined;
    pseudo?: string | undefined;
};

// The one helper. Displacement and velocity carry across a retarget.
export function release(
    element: Element,
    add: Vector,
    token: SpringName = "move",
    options: ReleaseOptions = {},
): Animation | null {
    const id = options.id ?? "m";
    const retarget = in_flight.get(element)?.has(id) ?? false;
    const [displaced, moving] = current(element, id);
    const spring = springs[token];
    const kick = options.kick ?? {};
    const from: Vector = {};
    const velocity: Vector = {};
    let far = 0;
    for (const channel of new Set([
        ...Object.keys(displaced),
        ...Object.keys(add),
        ...Object.keys(kick),
    ])) {
        let start = (displaced[channel] ?? 0) + (add[channel] ?? 0);
        const limit = options.clamp?.[channel];
        if (limit !== undefined) {
            start = Math.max(-limit, Math.min(limit, start));
        }
        from[channel] = start;
        velocity[channel] = (moving[channel] ?? 0) + (kick[channel] ?? 0);
        // A scale of 1/300 is as visible as one pixel.
        far = Math.max(
            far,
            (Math.abs(start) + Math.abs(velocity[channel]) / 20) *
                (channel.startsWith("s") ? 300 : 1),
        );
    }
    halt(element, id);
    // Reduced motion, or nothing to release: the state is already there.
    if (!(spring.duration > 1) || far < 0.5) {
        return null;
    }
    const state = flight(spring, from, velocity);
    const steps = Math.max(2, Math.round(spring.duration / 12));
    const keyframes: Keyframe[] = [];
    for (let step = 0; step <= steps; step += 1) {
        keyframes.push({
            transform: transform_of(
                step === steps ? {} : sample(state, ((spring.duration / 1000) * step) / steps)[0],
            ),
        });
    }
    const animation = element.animate(keyframes, {
        duration: spring.duration,
        easing: LINEAR,
        composite: "add",
        ...(options.pseudo === undefined ? {} : {pseudoElement: options.pseudo}),
    });
    // A retarget goes on from the frame on screen, where `current` read
    // the old spring: left to start on the next frame, the new animation
    // would hold that frame once more, and the thing would stall.
    if (retarget) {
        animation.startTime = document.timeline.currentTime;
    }
    const all = in_flight.get(element) ?? new Map<string, InFlight>();
    in_flight.set(element, all);
    all.set(id, {...state, started: now(), lasts: spring.duration, animation});
    return animation;
}

export type Box = {
    left: number;
    top: number;
    width: number;
    height: number;
    right: number;
    bottom: number;
};

// el's rectangle at rest: what layout says, with the springs in flight on
// it and above it removed. A thing in flight above every pane is its own
// frame of reference.
export function rest(element: Element): Box {
    guard.reading("rest");
    const box = element.getBoundingClientRect();
    let x = box.left;
    let y = box.top;
    for (
        let ancestor: Element | null = element;
        ancestor !== null && ancestor !== document.documentElement;
        ancestor = ancestor.hasAttribute("data-armada-flying") ? null : ancestor.parentElement
    ) {
        for (const id of in_flight.get(ancestor)?.keys() ?? []) {
            const [displaced] = current(ancestor, id);
            x -= displaced["x"] ?? 0;
            y -= displaced["y"] ?? 0;
        }
    }
    return {
        left: x,
        top: y,
        width: box.width,
        height: box.height,
        right: x + box.width,
        bottom: y + box.height,
    };
}

// Layout was written once; release the difference with a transform.
export function flip(
    element: Element,
    before: Box,
    token: SpringName = "move",
    options: ReleaseOptions & {resize?: boolean | undefined} = {},
): Animation | null {
    const after = rest(element);
    const difference: Vector = {x: before.left - after.left, y: before.top - after.top};
    if (options.resize) {
        difference["sx"] = before.width / after.width - 1;
        difference["sy"] = before.height / after.height - 1;
    }
    return release(element, difference, token, options);
}

// ---- fades

const fades = new WeakMap<Element, Animation>();

function opacity_of(element: Element): number {
    return Number(getComputedStyle(element).opacity);
}

// Opacity, from its current value: in over the fade-in, out over the
// fade-out.
export function fade(element: HTMLElement, to: number, lasts?: number): Animation | null {
    const from = opacity_of(element);
    fades.get(element)?.cancel();
    element.style.opacity = String(to);
    if (from === to) {
        return null;
    }
    const animation = element.animate(
        {opacity: [from, to]},
        {duration: lasts ?? (to > from ? times["fade-in"] : times["fade-out"]), easing: LINEAR},
    );
    fades.set(element, animation);
    return animation;
}

// What I did not ask for fades in; what I asked for starts part of the way
// there, in the frame.
export function appear(
    element: Element,
    from = 0,
    lasts?: number,
    options: {wait?: number | undefined; pseudo?: string | undefined} = {},
): Animation {
    return element.animate(
        {opacity: [from, 1]},
        {
            duration: lasts ?? times["fade-in"],
            easing: LINEAR,
            delay: options.wait ?? 0,
            fill: options.wait ? "backwards" : "none",
            ...(options.pseudo === undefined ? {} : {pseudoElement: options.pseudo}),
        },
    );
}

// The thing is already gone as far as the application is concerned (inert,
// out of the tab order); this lets it be seen leaving.
export function leave(element: HTMLElement | SVGElement, then?: () => void): Animation {
    element.setAttribute("inert", "");
    const animation = element.animate(
        {opacity: [opacity_of(element), 0]},
        {duration: times["fade-out"], easing: LINEAR, fill: "forwards"},
    );
    void when_finished(
        animation,
        () => {
            element.remove();
            then?.();
        },
        nothing,
    );
    return animation;
}

// The agent's forms change in steps: this part appears n steps late, as a
// cut.
export function hold(element: Element, steps = 1): Animation | null {
    return reduced()
        ? null
        : element.animate({opacity: [0, 0]}, {duration: times.step * steps, easing: LINEAR});
}

// ---- selection: one thumb a list

// Slide a selection thumb to `target`, which is read before any write.
export function thumb(
    marker: HTMLElement,
    target: HTMLElement | null,
    token: SpringName = "touch",
): Animation | null {
    if (target === null) {
        marker.dataset["off"] = "";
        return null;
    }
    guard.reading("thumb");
    const read = (name: string): number =>
        Number.parseFloat(marker.style.getPropertyValue(`--${name}`));
    const was =
        "off" in marker.dataset ? null : {x: read("x"), y: read("y"), w: read("w"), h: read("h")};
    const to = {
        x: target.offsetLeft,
        y: target.offsetTop,
        w: target.offsetWidth,
        h: target.offsetHeight,
    };
    for (const [name, value] of Object.entries(to)) {
        marker.style.setProperty(`--${name}`, `${value}px`);
    }
    delete marker.dataset["off"];
    if (!was?.w || !to.w) {
        return appear(marker);
    }
    return release(
        marker,
        {x: was.x - to.x, y: was.y - to.y, sx: was.w / to.w - 1, sy: was.h / to.h - 1},
        token,
    );
}

// ---- a figure in fixed cells

// A digit takes one width, punctuation half of it, a letter its own.
export function cell_kind(character: string): "digit" | "mark" | "word" {
    return /\d/.test(character) ? "digit" : /[A-Za-z−+]/.test(character) ? "word" : "mark";
}

export function cells(value: string | number): HTMLSpanElement[] {
    return [...String(value)].map((character) => {
        const cell = document.createElement("span");
        const kind = cell_kind(character);
        if (kind !== "digit") {
            cell.dataset["cell"] = kind;
        }
        cell.textContent = character;
        return cell;
    });
}

// Change a figure in its fixed cells; only the characters that changed
// move. `cut`: a clock, whose digit changes in a step and nothing travels.
export function figure(
    cell: HTMLElement,
    value: string | number,
    options: {direction?: number; quiet?: boolean; cut?: boolean} = {},
): void {
    const next = String(value);
    // What it reads now, without the ghosts of digits still leaving.
    const old =
        cell.firstElementChild === null
            ? cell.textContent
            : [...cell.children].map((box) => box.firstChild?.nodeValue ?? "").join("");
    if (old === next) {
        return;
    }
    if (old.length !== next.length || cell.firstElementChild === null) {
        cell.replaceChildren(...cells(next));
        if (old !== "") {
            appear(cell, 0.4);
        }
        return;
    }
    const digits = (text: string): number => Number.parseFloat(text.replaceAll(/\D/g, ""));
    const up = options.direction ?? (digits(next) >= digits(old) ? 1 : -1);
    for (const [index, box] of [...cell.children].entries()) {
        const text = box.firstChild;
        const character = [...next][index]!;
        if (text === null || text.nodeValue === character) {
            continue;
        }
        const ghost = document.createElement("i");
        ghost.textContent = text.nodeValue;
        text.nodeValue = character;
        for (const stale of box.querySelectorAll("i")) {
            stale.remove();
        }
        box.append(ghost);
        leave(ghost);
        if (!options.cut && !options.quiet) {
            release(box, {y: amounts.rise * up}, "touch");
        }
    }
}

// ---- a glyph or a word in a fixed cell

// Change what a fixed cell holds. `sequence`: words read where the eye is,
// so the old one goes and then the new one comes, never two at once.
export function swap(
    cell: Element,
    next: HTMLElement | SVGElement,
    options: {direction?: number; quiet?: boolean; sequence?: boolean} = {},
): Element {
    const direction = options.direction ?? 1;
    for (const gone of cell.querySelectorAll(":scope > [data-gone]")) {
        gone.remove();
    }
    const old = cell.querySelector<HTMLElement | SVGElement>(":scope > :not([data-sizer])");
    if (old !== null) {
        old.dataset["gone"] = "";
        leave(old);
        if (!options.quiet && !reduced()) {
            release(old, {y: 0}, "touch", {kick: {y: -direction * 160}});
        }
    }
    cell.append(next);
    if (options.sequence && old !== null) {
        appear(next, 0, undefined, {wait: times["fade-out"]});
    } else {
        appear(next, 0.3);
    }
    if (!options.quiet) {
        release(next, {y: amounts.rise * direction}, "touch");
    }
    return next;
}

// One mark becomes another, in place: the new one is whole in the frame
// and the old one fades off it. Never a frame with neither.
export function cross<Mark extends HTMLElement | SVGElement>(
    old: HTMLElement | SVGElement,
    next: Mark,
): Mark {
    guard.reading("cross");
    const box = old.getBoundingClientRect();
    const ink = getComputedStyle(old).color;
    old.replaceWith(next);
    if (reduced() || box.width === 0) {
        return next;
    }
    Object.assign(old.style, {
        position: "fixed",
        left: `${box.left}px`,
        top: `${box.top}px`,
        width: `${box.width}px`,
        height: `${box.height}px`,
        margin: "0",
        color: ink,
        pointerEvents: "none",
    });
    document.body.append(old);
    for (const animation of old.getAnimations()) {
        animation.cancel();
    }
    leave(old);
    appear(next, 0.3);
    return next;
}

// ---- grow: nine slices, so a fill can change size on a spring with its
// corners intact

const growing = new WeakMap<HTMLElement, {fill: string; isolation: string}>();

export function grow(
    element: HTMLElement,
    from: Box,
    token: SpringName = "land",
    radius = 18,
): Animation | null {
    const to = rest(element);
    const wider = to.width - from.width;
    const taller = to.height - from.height;
    if (reduced() || (Math.abs(wider) < 1 && Math.abs(taller) < 1)) {
        return null;
    }
    const style = getComputedStyle(element);
    const corners = [
        ["borderTopLeftRadius", style.borderTopLeftRadius],
        ["borderTopRightRadius", style.borderTopRightRadius],
        ["borderBottomLeftRadius", style.borderBottomLeftRadius],
        ["borderBottomRightRadius", style.borderBottomRightRadius],
    ] as const;
    for (const stale of element.querySelectorAll(":scope > .armada-nine")) {
        stale.remove();
    }
    const nine = document.createElement("span");
    nine.className = "armada-nine";
    nine.style.setProperty("--nine", style.backgroundColor);
    // The slices stand behind what the element holds, inside its own
    // stacking context: a layer of this element, not one of the window's.
    nine.style.zIndex = "-1";
    const middle_width = to.width - 2 * radius + 2;
    const middle_height = to.height - 2 * radius + 2;
    const columns = [
        [0, radius],
        [radius - 1, middle_width],
        [to.width - radius, radius],
    ] as const;
    const rows = [
        [0, radius],
        [radius - 1, middle_height],
        [to.height - radius, radius],
    ] as const;
    const pieces: [HTMLElement, Vector][] = [];
    for (const [row, [top, height]] of rows.entries()) {
        for (const [column, [left, width]] of columns.entries()) {
            if (width <= 0 || height <= 0) {
                continue;
            }
            const slice = document.createElement("i");
            Object.assign(slice.style, {
                left: `${left}px`,
                top: `${top}px`,
                width: `${width}px`,
                height: `${height}px`,
            });
            if (row !== 1 && column !== 1) {
                const [property, value] = corners[(row === 0 ? 0 : 2) + (column === 0 ? 0 : 1)]!;
                slice.style[property] = value;
            }
            const offset: Vector = {};
            if (column === 2) {
                offset["x"] = -wider;
            }
            if (column === 1) {
                offset["sx"] = -wider / middle_width;
            }
            if (row === 2) {
                offset["y"] = -taller;
            }
            if (row === 1) {
                offset["sy"] = -taller / middle_height;
            }
            nine.append(slice);
            pieces.push([slice, offset]);
        }
    }
    // A second arrival before the first has settled keeps the first's
    // record of what the element looked like.
    const kept = growing.get(element) ?? {
        fill: element.style.background,
        isolation: element.style.isolation,
    };
    growing.set(element, kept);
    element.style.background = "transparent";
    element.style.isolation = "isolate";
    element.prepend(nine);
    const released = pieces.flatMap(([slice, offset]) => release(slice, offset, token) ?? []);
    const done = (): void => {
        if (nine.isConnected) {
            nine.remove();
            if (element.querySelector(":scope > .armada-nine") === null) {
                element.style.background = kept.fill;
                element.style.isolation = kept.isolation;
                growing.delete(element);
            }
        }
    };
    void when_all_finished(released, done);
    return released.at(-1) ?? null;
}

// ---- fly: travel from a rectangle to the element's place, in the top
// layer, so no scroller or pane clips it

type Flying = {element: HTMLElement; spacer: HTMLElement; kept: string; end: () => void};

export const flying = new Set<Flying>();

function ground(flight_: Flying): void {
    const {element, spacer, kept} = flight_;
    try {
        element.hidePopover();
    } catch {
        // It was never shown, or is already hidden.
    }
    element.removeAttribute("popover");
    element.removeAttribute("data-armada-flying");
    element.style.cssText = kept;
    spacer.remove();
}

// `from` is a rectangle that was read while layout was clean (the default
// button's cell is cached for the send); el's own place is measured here,
// so call it in the after-layout phase.
export function fly(
    element: HTMLElement,
    from: Box,
    token: SpringName = "move",
    options: ReleaseOptions & {in_place?: boolean | undefined} = {},
): Animation | null {
    const to = rest(element);
    if (reduced() || !("showPopover" in element)) {
        return null;
    }
    // What keeps its room while it is away is its own picture, unseen: the
    // same box and the same baseline, so nothing set beside it (a time)
    // sits anywhere else during the flight.
    const spacer = element.cloneNode(true);
    if (!(spacer instanceof HTMLElement)) {
        return null;
    }
    spacer.removeAttribute("id");
    for (const named of spacer.querySelectorAll("[id]")) {
        named.removeAttribute("id");
    }
    spacer.style.visibility = "hidden";
    spacer.style.width = `${to.width}px`;
    spacer.setAttribute("aria-hidden", "true");
    spacer.inert = true;
    // An absolutely placed thing holds no room, so it needs none kept.
    if (!options.in_place) {
        element.after(spacer);
    }
    const flight_: Flying = {
        element,
        spacer,
        kept: element.style.cssText,
        end() {
            if (flying.delete(flight_)) {
                ground(flight_);
            }
        },
    };
    element.setAttribute("popover", "manual");
    element.dataset["armadaFlying"] = "";
    // The top layer's own defaults (a border, a margin, a clip) are not
    // this element's.
    Object.assign(element.style, {
        position: "fixed",
        inset: "auto",
        left: `${to.left}px`,
        top: `${to.top}px`,
        width: `${to.width}px`,
        margin: "0",
        border: "0",
        overflow: "visible",
        maxWidth: "none",
    });
    try {
        element.showPopover();
    } catch {
        ground(flight_);
        return null;
    }
    flying.add(flight_);
    const animation = release(
        element,
        {x: from.left - to.left, y: from.top - to.top},
        token,
        options,
    );
    if (animation === null) {
        flight_.end();
    } else {
        void when_finished(animation, flight_.end, nothing);
    }
    return animation;
}

// The pane under a flying thing moved by `distance` (a second send, a line
// of text): its place moves, and it is retargeted there.
export function carry(distance: number, token: SpringName = "move"): void {
    for (const flight_ of flying) {
        const {element} = flight_;
        element.style.top = `${Number.parseFloat(element.style.top) - distance}px`;
        const animation = release(element, {y: distance}, token);
        if (animation === null) {
            flight_.end();
        } else {
            void when_finished(animation, flight_.end, nothing);
        }
    }
}

// ---- arrivals and knocks

// A textless object arrives: from 0.7, past its size and back. Its
// transform origin is the caller's.
export function land(element: Element, options: ReleaseOptions = {}): Animation | null {
    appear(element, 0.4);
    return release(element, {sx: amounts.from - 1, sy: amounts.from - 1}, "land", options);
}

// An impulse: it leaves and comes back. A thing that holds words takes it
// on a critically damped spring.
export function kick(
    element: Element,
    velocity: Vector,
    token: SpringName = "touch",
): Animation | null {
    return release(element, {}, token, {kick: velocity, id: "k"});
}

// Struck: smaller in the frame of the blow, and it recovers without
// passing its size.
export function squash(element: Element, by = 0.03): Animation | null {
    return release(element, {sx: -by, sy: -by}, "touch", {id: "sq"});
}

// Small copies leave `at` and fade within the burst.
export function burst(at: Element, make: () => Node, count = 3): void {
    if (reduced()) {
        return;
    }
    guard.reading("burst");
    const box = at.getBoundingClientRect();
    const x = box.left + box.width / 2 - 6;
    const y = box.top + box.height / 2 - 6;
    for (let index = 0; index < count; index += 1) {
        const bit = document.createElement("i");
        bit.className = "armada-bit";
        bit.append(make());
        document.body.append(bit);
        const angle = ((-150 + index * 60 + (index % 2) * 14) * Math.PI) / 180;
        const far = 22 + index * 4;
        const animation = bit.animate(
            {
                transform: [
                    `translate(${x}px, ${y}px) scale(0.9)`,
                    `translate(${x + Math.cos(angle) * far}px, ${y + Math.sin(angle) * far}px) scale(0.6)`,
                ],
                opacity: [1, 1, 0],
                offset: [0, 0.55, 1],
            },
            {
                duration: times.burst,
                easing: springs.move.css,
                delay: (index * times.step) / 5,
                fill: "both",
            },
        );
        const remove = (): void => {
            bit.remove();
        };
        void when_finished(animation, remove);
    }
}

export function shake(element: Element): Animation | null {
    return reduced()
        ? null
        : element.animate(
              {
                  transform: [
                      "translateX(0)",
                      "translateX(-3px)",
                      "translateX(3px)",
                      "translateX(-2px)",
                      "translateX(0)",
                  ],
              },
              {duration: times.shake, easing: LINEAR, composite: "add"},
          );
}

// A refused action: the thing shakes once. With motion reduced its edge
// takes the failure ink for two steps of the agent's clock and lets it go,
// so the refusal still reads without sound.
export function refuse(element: Element): Animation | null {
    if (!reduced()) {
        return shake(element);
    }
    const edge = document.createElement("i");
    edge.className = "armada-refusal";
    element.append(edge);
    const animation = edge.animate(
        {opacity: [1, 1, 0], offset: [0, 0.3, 1]},
        {duration: times.step * 2, easing: LINEAR},
    );
    const remove = (): void => {
        edge.remove();
    };
    void when_finished(animation, remove);
    return animation;
}
