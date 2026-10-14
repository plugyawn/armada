// 17. What the page holds at rest on a topic: elements, style rules and
// delegated handlers. Each of them is paid for on every interaction.

/* global window, document */

export const name = "weight";
export const order = 170;
export const description =
    "the short worktree topic at rest: DOM nodes, CSS rules, jQuery handlers delegated on body";

// SPEC-ENGINEERING.md section 8, "Weight at rest". Today's figures are far
// over; known.json holds them as ceilings (`max`).
const BUDGET = {nodes: 900, rules: 2500, body_listeners: 40};

function weigh() {
    const out = {nodes: document.querySelectorAll("*").length};

    // Every rule, the ones inside @layer, @media and nested rules included.
    // The emoji sprite's stylesheet, one rule per emoji, is fetched when
    // the first emoji is shown (emojisets.ts): whether it is in the page
    // depends on the topic's text, not on the code, so it is counted apart.
    const walk = (rules) => {
        let count = 0;
        let sprite = 0;
        for (const rule of rules) {
            count += 1;
            if (/^\.emoji-[\da-f]/.test(rule.selectorText ?? "")) {
                sprite += 1;
            }
            if (rule.cssRules && rule.cssRules.length > 0) {
                const inner = walk(rule.cssRules);
                count += inner.count;
                sprite += inner.sprite;
            }
        }
        return {count, sprite};
    };
    out.rules = 0;
    out.sprite_rules = 0;
    out.sheets = 0;
    out.unreadable_sheets = 0;
    const per_sheet = [];
    for (const sheet of document.styleSheets) {
        out.sheets += 1;
        try {
            const {count, sprite} = walk(sheet.cssRules);
            if (sprite > count / 2) {
                out.sprite_rules += count;
                continue;
            }
            out.rules += count;
            per_sheet.push([(sheet.href ?? "inline").split("/").pop().slice(0, 40), count]);
        } catch {
            out.unreadable_sheets += 1;
        }
    }
    out.largest_sheets = per_sheet
        .toSorted((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([sheet, count]) => `${count} ${sheet}`)
        .join("; ");

    // The old code delegates through jQuery, which keeps one native
    // listener per event type and its own list of handlers; that list is
    // what a click walks. The development bundle exposes jQuery through
    // debug-require.
    try {
        const module = window.require("jquery");
        const jquery = module.default ?? module;
        const count = (element) =>
            Object.values(jquery._data(element, "events") ?? {}).reduce(
                (sum, handlers) => sum + handlers.length,
                0,
            );
        out.body_listeners = count(document.body);
        out.document_listeners = count(document);
        out.window_listeners = count(window);
        const by_type = Object.entries(jquery._data(document.body, "events") ?? {})
            .map(([type, handlers]) => [type, handlers.length])
            .toSorted((a, b) => b[1] - a[1]);
        out.body_listener_types = by_type
            .slice(0, 5)
            .map(([type, handlers]) => `${handlers} ${type}`)
            .join(", ");
    } catch (error) {
        out.body_listeners = null;
        out.listeners_error = String(error).slice(0, 120);
    }
    return out;
}

export async function run(ctx) {
    await ctx.open(ctx.topics.short);
    await ctx.park_pointer();
    // Entry fetches and what they render (the topic bar, the rail) are part
    // of the page at rest.
    await ctx.settle(600, 8000);
    await ctx.sleep(1500);
    const rows = await ctx.page.evaluate(() => window.__ac.all("messageRow").length);
    const weight = await ctx.page.evaluate(`(${weigh.toString()})()`);

    ctx.assert("dom-nodes", rows > 0 ? weight.nodes : null, "<=", BUDGET.nodes, {
        note:
            rows > 0
                ? `elements in the document with ${rows} message rows on screen`
                : "not measurable: the topic showed no message",
    });
    ctx.assert(
        "css-rules",
        weight.unreadable_sheets === 0 && weight.rules > 0 ? weight.rules : null,
        "<=",
        BUDGET.rules,
        {
            note:
                weight.unreadable_sheets === 0
                    ? `in ${weight.sheets} sheets, nested rules included; largest: ${weight.largest_sheets}; ` +
                      (weight.sprite_rules > 0
                          ? `the emoji sprite's ${weight.sprite_rules} rules not counted`
                          : "the emoji sprite not loaded")
                    : `not measurable: ${weight.unreadable_sheets} of ${weight.sheets} sheets could not be read`,
        },
    );
    ctx.assert("body-listeners", weight.body_listeners, "<=", BUDGET.body_listeners, {
        note:
            weight.body_listeners === null
                ? `not measurable: ${weight.listeners_error}`
                : `jQuery handlers delegated on body (${weight.body_listener_types}); document ${weight.document_listeners}, window ${weight.window_listeners}`,
    });
}
