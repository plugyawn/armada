"use strict";

// What a browser gives the motion kit and jsdom does not: animations that
// can be finished or cancelled by a test, a clock a test moves, the top
// layer, animation frames, and rectangles a test sets.

const fs = require("node:fs");
const path = require("node:path");

const {set_global} = require("../lib/namespace.cjs");
const {make_real_dom} = require("../lib/real_dom.cjs");

const TOKENS = fs.readFileSync(path.join(__dirname, "../../src/armada/tokens/tokens.css"), "utf8");

// The first value each token is given: the one outside every media query.
exports.token_source = (overrides = {}) => {
    const values = new Map();
    for (const [, name, value] of TOKENS.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
        if (!values.has(name)) {
            values.set(name, value.replaceAll(/\s+/g, " "));
        }
    }
    return {
        getPropertyValue: (name) => overrides[name.slice(2)] ?? values.get(name.slice(2)) ?? "",
    };
};

exports.make_motion_dom = (body_html = "") => {
    const dom = make_real_dom(body_html);
    const {window} = dom;
    const state = {now: 1000, animations: [], frames: []};
    for (const name of ["HTMLTextAreaElement", "SVGElement", "WeakMap", "AbortController"]) {
        set_global(name, window[name] ?? globalThis[name]);
    }
    set_global("window", window);
    set_global("performance", {now: () => state.now});
    set_global("requestAnimationFrame", (callback) => {
        state.frames.push(callback);
        return state.frames.length;
    });
    window.matchMedia = () => ({addEventListener() {}});
    // The document's timeline: the time of the frame on screen.
    Object.defineProperty(window.document, "timeline", {
        value: {
            get currentTime() {
                return state.now;
            },
        },
    });
    // Resize observers a test fires with `resize(element)`.
    const observers = new Set();
    set_global(
        "ResizeObserver",
        class {
            constructor(callback) {
                this.callback = callback;
                this.targets = new Set();
            }
            observe(target) {
                this.targets.add(target);
                observers.add(this);
            }
            disconnect() {
                observers.delete(this);
            }
        },
    );
    window.Element.prototype.animate = function (keyframes, options) {
        const animation = {
            target: this,
            keyframes,
            options,
            cancelled: false,
            cancel() {
                this.cancelled = true;
                this.reject(new Error("cancelled"));
            },
            finish() {
                this.resolve();
            },
        };
        animation.finished = new Promise((resolve, reject) => {
            animation.resolve = resolve;
            animation.reject = reject;
        });
        // A cancelled animation that nobody waits for is not an error.
        void (async () => {
            try {
                await animation.finished;
            } catch {
                // Cancelled.
            }
        })();
        state.animations.push(animation);
        return animation;
    };
    window.Element.prototype.getAnimations = function () {
        return state.animations.filter((animation) => animation.target === this);
    };
    window.HTMLElement.prototype.showPopover = function () {
        if (this.dataset.noPopover !== undefined) {
            throw new Error("no top layer");
        }
        this.dataset.shown = "";
    };
    window.HTMLElement.prototype.hidePopover = function () {
        if (this.dataset.shown === undefined) {
            throw new Error("not shown");
        }
        delete this.dataset.shown;
    };
    return {
        ...dom,
        state,
        advance(ms) {
            state.now += ms;
        },
        resize(element) {
            for (const observer of observers) {
                if (observer.targets.has(element)) {
                    observer.callback([{target: element}]);
                }
            }
        },
        // How many observe an element's size now.
        resize_observers: () => observers.size,
        // Run the animation frames that are due, once.
        frame() {
            for (const callback of state.frames.splice(0)) {
                callback(state.now);
            }
        },
        // Give an element a rectangle, as layout would.
        place(element, left, top, width, height) {
            element.getBoundingClientRect = () => ({
                left,
                top,
                width,
                height,
                right: left + width,
                bottom: top + height,
            });
            for (const [name, value] of Object.entries({
                offsetLeft: left,
                offsetTop: top,
                offsetWidth: width,
                offsetHeight: height,
            })) {
                Object.defineProperty(element, name, {value, configurable: true});
            }
        },
        last() {
            return state.animations.at(-1);
        },
        // Let what waits on a finished or cancelled animation run.
        async settle() {
            for (let turn = 0; turn < 10; turn += 1) {
                await Promise.resolve();
            }
        },
    };
};
