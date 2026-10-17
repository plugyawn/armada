// The pages before the app (templates/zerver/armada_portico.html): the
// server's forms, drawn in the tokens and the kit, whose sheets the
// armada-portico bundle loads. This adds the window's ring and the little
// a form does in the page: showing a password, carrying the address's
// fragment through a sign-in, a new account's time zone, a name taken
// from another account, and the challenge a new organization may ask
// for. Every check of what was typed is the server's.

/* global AltchaWidgetMethods */
import {wire} from "../../motion/index.ts";

// A password's control shows what is typed and hides it again; its two
// names come translated from the page.
function wire_reveal(button: HTMLButtonElement): void {
    const input = document.querySelector(`#${CSS.escape(button.dataset["armadaReveal"] ?? "")}`);
    if (!(input instanceof HTMLInputElement)) {
        return;
    }
    button.addEventListener("click", () => {
        const shown = input.type === "password";
        input.type = shown ? "text" : "password";
        button.setAttribute("aria-pressed", String(shown));
        const label = shown ? button.dataset["labelHide"] : button.dataset["labelShow"];
        if (label !== undefined) {
            button.setAttribute("aria-label", label);
        }
    });
}

// The server never sees an address's fragment, so a sign-in that should
// land on a conversation carries it in `next` from here.
function carry_fragment(): void {
    const fragment = window.location.hash;
    if (!fragment.startsWith("#")) {
        return;
    }
    for (const input of document.querySelectorAll<HTMLInputElement>(
        "[data-armada-carry-fragment] input[name='next']",
    )) {
        input.value += fragment;
    }
}

// Choosing another account to copy settings from takes its name too.
function wire_source_account(select: HTMLSelectElement): void {
    const name = document.querySelector("#id_full_name");
    select.addEventListener("change", () => {
        const full_name = select.selectedOptions[0]?.dataset["fullName"];
        if (name instanceof HTMLInputElement && full_name !== undefined) {
            name.value = full_name;
        }
    });
}

// A new organization may ask the browser to solve a small challenge
// before its form is sent; the button waits for it.
async function wire_challenge(widget: AltchaWidgetMethods & HTMLElement): Promise<void> {
    const submit = widget.closest("form")?.querySelector("button[type='submit']");
    if (submit instanceof HTMLButtonElement) {
        submit.disabled = true;
        widget.addEventListener("statechange", (event) => {
            const detail: unknown = event instanceof CustomEvent ? event.detail : undefined;
            if (
                typeof detail === "object" &&
                detail !== null &&
                "state" in detail &&
                detail.state === "verified"
            ) {
                submit.disabled = false;
            }
        });
    }
    await import("altcha");
    await customElements.whenDefined("altcha-widget");
    widget.configure({auto: "onload"});
}

// An organization that has moved sends you on after a few seconds.
function wire_redirect(link: HTMLAnchorElement): void {
    const seconds = Number(link.dataset["armadaRedirectAfter"]);
    const url = new URL(link.href);
    if (window.location.hash.startsWith("#")) {
        url.hash = window.location.hash;
        link.href = url.toString();
    }
    setTimeout(
        () => {
            window.location.href = link.href;
        },
        Math.max(0, seconds) * 1000,
    );
}

wire();
for (const button of document.querySelectorAll<HTMLButtonElement>("[data-armada-reveal]")) {
    wire_reveal(button);
}
carry_fragment();
for (const input of document.querySelectorAll<HTMLInputElement>("[data-armada-timezone]")) {
    input.value = new Intl.DateTimeFormat().resolvedOptions().timeZone;
}
const source_account = document.querySelector("#source_realm_select");
if (source_account instanceof HTMLSelectElement) {
    wire_source_account(source_account);
}
const challenge = document.querySelector<AltchaWidgetMethods & HTMLElement>("altcha-widget");
if (challenge !== null) {
    void wire_challenge(challenge);
}
const redirect = document.querySelector("[data-armada-redirect-after]");
if (redirect instanceof HTMLAnchorElement) {
    wire_redirect(redirect);
}
