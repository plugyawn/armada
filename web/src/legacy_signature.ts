// The Pear console signature — flavor register item, printed once at
// boot (armada/ARMADA_DESIGN_LANGUAGE.md §11). Decoration only; must never
// throw or block.
export function print_armada_signature(): void {
    try {
        // eslint-disable-next-line no-console -- the signature is console output by design
        console.log(
            "%c" +
                String.raw`      _` +
                "\n" +
                String.raw`     //` +
                "\n" +
                String.raw`   .--.` +
                "\n" +
                String.raw`  (    )` +
                "\n" +
                String.raw` (      )` +
                "\n" +
                String.raw`  '-..-'   armada — the machine speaks in pixels`,
            "color: #3aa469; font-family: ui-monospace, monospace;",
        );
    } catch {
        // Consoles that dislike %c formatting get silence, not errors.
    }
}
