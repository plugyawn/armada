// This module is shared with portico code (login/signup pages).
// Do not import any web app-only modules here (e.g., user_settings, ui_init, etc.).
// Only import modules that are available in both contexts.

import assert from "minimalistic-assert";

import octopus_url from "../../static/generated/emoji/images-google-64/1f419.png";
import google_sheet from "../generated/emoji/google.webp";
import twitter_sheet from "../generated/emoji/twitter.webp";

import * as blueslip from "./blueslip.ts";

type EmojiSetCss = {use: () => void; unuse: () => void};

type EmojiSet = {
    // The sprite's stylesheet is 1,900 rules; it is a chunk of its own,
    // fetched with the sheet the first time an emoji is shown.
    load_css: () => Promise<{default: EmojiSetCss}>;
    sheet: string;
    css?: EmojiSetCss;
};

const emojisets = new Map<string, EmojiSet>([
    [
        "google",
        {
            load_css: async () =>
                import(
                    /* webpackChunkName: "emoji-google" */ "!style-loader?injectType=lazyStyleTag!css-loader!../generated/emoji-styles/google-sprite.css"
                ),
            sheet: google_sheet,
        },
    ],
    [
        "twitter",
        {
            load_css: async () =>
                import(
                    /* webpackChunkName: "emoji-twitter" */ "!style-loader?injectType=lazyStyleTag!css-loader!../generated/emoji-styles/twitter-sprite.css"
                ),
            sheet: twitter_sheet,
        },
    ],
]);

// For `text` emoji set we fallback to `google` emoji set
// for displaying emojis in emoji picker and typeahead.
emojisets.set("text", emojisets.get("google")!);

let current_emojiset: EmojiSet | undefined;

async function fetch_emojiset(name: string, url: string): Promise<void> {
    const MAX_RETRIES = 3;
    const RETRY_DELAY = 10000; // 10 seconds

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt += 1) {
        try {
            const response = await fetch(url);

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // If the fetch is successful, resolve the promise and return
            return;
        } catch (error) {
            if (!navigator.onLine) {
                // If the user is offline, retry once they are online.
                await new Promise<void>((resolve) => {
                    // We don't want to throw an error here since this is clearly something wrong with user's network.
                    blueslip.warn(
                        `Failed to load emojiset ${name} from ${url}. Retrying when online.`,
                    );
                    window.addEventListener(
                        "online",
                        () => {
                            resolve();
                        },
                        {once: true},
                    );
                });
            } else {
                blueslip.warn(
                    `Failed to load emojiset ${name} from ${url}. Attempt ${attempt} of ${MAX_RETRIES}.`,
                );

                // If this was the last attempt, rethrow the error
                if (attempt === MAX_RETRIES) {
                    blueslip.error(
                        `Failed to load emojiset ${name} from ${url} after ${MAX_RETRIES} attempts.`,
                    );
                    throw error;
                }

                // Wait before the next attempt
                await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY));
            }
        }
    }
}

// The set the page should show: the last one asked for.
let wanted_emojiset_name: string | undefined;

export async function select(name: string): Promise<void> {
    const new_emojiset = emojisets.get(name);
    if (!new_emojiset) {
        throw new Error("Unknown emojiset " + name);
    }
    wanted_emojiset_name = name;
    stop_waiting_for_an_emoji();
    if (new_emojiset === current_emojiset) {
        return;
    }

    const [css_module] = await Promise.all([
        new_emojiset.load_css(),
        fetch_emojiset(name, new_emojiset.sheet),
    ]);
    new_emojiset.css = css_module.default;
    if (wanted_emojiset_name !== name || new_emojiset === current_emojiset) {
        // Another set was chosen, or this one was put in place, while
        // this request was on its way.
        return;
    }

    current_emojiset?.css?.unuse();
    new_emojiset.css.use();
    current_emojiset = new_emojiset;
}

// Until its sheet has arrived an emoji is an empty box of its final size,
// not its name as text: these are the declarations of the sprite
// stylesheet's first rule that are not the image.
const EMOJI_SELECTOR = "div.emoji, span.emoji";
const placeholder_css = `@layer zulip { ${EMOJI_SELECTOR} { display: inline-block; text-indent: 100%; white-space: nowrap; overflow: hidden; } }`;

let emoji_observer: MutationObserver | undefined;

function stop_waiting_for_an_emoji(): void {
    emoji_observer?.disconnect();
    emoji_observer = undefined;
}

function is_or_holds_an_emoji(node: Node): boolean {
    return (
        node instanceof Element &&
        (node.matches(EMOJI_SELECTOR) || node.querySelector(EMOJI_SELECTOR) !== null)
    );
}

// Call `callback` once, when the document first holds an emoji drawn from
// the sprite: one in a message, a reaction, the picker, the typeahead.
function when_an_emoji_appears(callback: () => void): void {
    if (document.querySelector(EMOJI_SELECTOR) !== null) {
        callback();
        return;
    }
    emoji_observer = new MutationObserver((records) => {
        for (const record of records) {
            if (
                record.type === "attributes"
                    ? record.target instanceof Element && record.target.matches(EMOJI_SELECTOR)
                    : [...record.addedNodes].some((node) => is_or_holds_an_emoji(node))
            ) {
                stop_waiting_for_an_emoji();
                callback();
                return;
            }
        }
    });
    emoji_observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
    });
}

export function initialize(emojiset_name: string): void {
    wanted_emojiset_name = emojiset_name;
    const placeholder_style = document.createElement("style");
    placeholder_style.textContent = placeholder_css;
    document.head.append(placeholder_style);

    // The sheet is 2.4 MB and most pages show no emoji: it is fetched when
    // the first one appears, not when the page starts.
    when_an_emoji_appears(() => {
        assert(wanted_emojiset_name !== undefined);
        void select(wanted_emojiset_name);

        // Load the octopus image in the background, so that the browser
        // will cache it for later use.  Note that we hardcode the octopus
        // emoji to the old Google one because it's better.
        //
        // TODO: We should probably just make this work just like the Zulip emoji.
        const octopus_image = new Image();
        octopus_image.src = octopus_url;
    });
}
