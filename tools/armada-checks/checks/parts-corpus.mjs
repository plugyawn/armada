// 15. Do the client's Markdown and the server's give the same bubbles and
// blocks? The rebuilt feed shows a sent message from the client's
// rendering and keeps the row when the server's arrives; that works only
// where both split the same way.

/* global window */

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {parts_from_html} from "../lib/parts.mjs";

export const name = "parts-corpus";
export const order = 150;
export const description =
    "the Markdown test cases and the fixture topics rendered by the client's engine and by the server, split into bubbles and blocks: cases in which the two differ";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");

// Rendered by the client's own engine, reached through the development
// bundle's module table: the engine has no handle for tests.
function render_with_client(inputs) {
    let require_module;
    window.webpackChunk.push([
        [Symbol("armada-checks")],
        {},
        (webpack_require) => {
            require_module = webpack_require;
        },
    ]);
    const id = Object.keys(require_module.m).find((key) => key.endsWith("/src/markdown.ts"));
    const markdown = require_module(id);
    return inputs.map((input) => {
        if (markdown.contains_backend_only_syntax(input)) {
            return {echoed: false};
        }
        try {
            return {echoed: true, html: markdown.render(input).content};
        } catch (error) {
            return {echoed: true, error: String(error?.message ?? error)};
        }
    });
}

async function fixture_messages(ctx, topics) {
    const cases = [];
    for (const topic of topics) {
        const narrow = [
            {operator: "channel", operand: 22},
            {operator: "topic", operand: topic},
        ];
        const fetch_all = async (apply_markdown) =>
            await ctx.api("GET", "/json/messages", {
                anchor: "oldest",
                num_before: 0,
                num_after: 200,
                narrow,
                apply_markdown,
            });
        const raw = await fetch_all(false);
        const rendered = await fetch_all(true);
        if (raw.status !== 200 || rendered.status !== 200) {
            throw new Error(`cannot read ${topic}: HTTP ${raw.status}, ${rendered.status}`);
        }
        const html = new Map(
            rendered.data.messages.map((message) => [message.id, message.content]),
        );
        for (const message of raw.data.messages) {
            cases.push({
                name: `${topic} #${message.id}`,
                input: message.content,
                server: html.get(message.id),
            });
        }
    }
    return cases;
}

async function render_with_server(ctx, input) {
    for (let attempt = 0; attempt < 5; attempt += 1) {
        const response = await ctx.api("POST", "/json/messages/render", {content: input});
        if (response.status === 200) {
            return response.data.rendered;
        }
        if (response.status !== 429) {
            throw new Error(`render failed: HTTP ${response.status}`);
        }
        await ctx.sleep((Number(response.data?.["retry-after"]) || 10) * 1000 + 500);
    }
    throw new Error("render failed: rate limited");
}

export async function run(ctx) {
    await ctx.scenario("corpus", async () => {
        const corpus = JSON.parse(
            fs.readFileSync(path.join(HERE, "../fixtures/parts-corpus.json"), "utf8"),
        );
        const tests = JSON.parse(
            fs.readFileSync(path.join(REPO, corpus.markdown_test_cases), "utf8"),
        ).regular_tests;
        await ctx.open(ctx.topics.short, {fresh: true});

        const cases = tests
            .filter((test) => !test.backend_only_rendering)
            .map((test) => ({
                name: `test case: ${test.name}`,
                input: test.input,
                server: test.expected_output,
            }));
        for (const extra of corpus.extra) {
            cases.push({
                name: `extra: ${extra.name}`,
                input: extra.input,
                server: await render_with_server(ctx, extra.input),
            });
        }
        cases.push(...(await fixture_messages(ctx, corpus.fixture_topics)));

        const client = await ctx.page.evaluate(
            render_with_client,
            cases.map((entry) => entry.input),
        );
        // The reference split runs in the page, where there is a DOM.
        await ctx.page.evaluate(`window.__ac.parts_from_html = ${parts_from_html.toString()}`);
        const split = async (html_list) =>
            await ctx.page.evaluate(
                (list) =>
                    list.map((html) => (html === null ? null : window.__ac.parts_from_html(html))),
                html_list,
            );
        const client_parts = await split(client.map((entry) => entry.html ?? null));
        const server_parts = await split(cases.map((entry) => entry.server));

        const signature = (parts) => parts.map((part) => `${part.kind}:${part.text}`).join(" | ");
        const kinds = (parts) => parts.map((part) => part.kind[1]).join("");
        let compared = 0;
        let not_echoed = 0;
        let blocks = 0;
        const differing = [];
        const differing_in_kind = [];
        for (const [index, entry] of cases.entries()) {
            if (!client[index].echoed) {
                not_echoed += 1;
                continue;
            }
            compared += 1;
            blocks += server_parts[index].filter((part) => part.kind === "block").length;
            if (
                client_parts[index] === null ||
                signature(client_parts[index]) !== signature(server_parts[index])
            ) {
                if (
                    client_parts[index] === null ||
                    kinds(client_parts[index]) !== kinds(server_parts[index])
                ) {
                    differing_in_kind.push(entry.name);
                }
                differing.push(
                    `${entry.name} (client ${client_parts[index] ? kinds(client_parts[index]) || "nothing" : "threw"}, server ${kinds(server_parts[index]) || "nothing"})`,
                );
            }
        }
        ctx.assert("cases-compared", compared, ">=", 150, {
            note: `${cases.length} cases: ${tests.length} Markdown test cases less those marked server-only, ${corpus.extra.length} extra, the messages of ${corpus.fixture_topics.length} fixture topics; ${not_echoed} are not rendered by the client at all; the server's renderings hold ${blocks} blocks`,
        });
        // What keeping the row depends on: the same bubbles and blocks in
        // the same order. A difference in the words alone is repaired in
        // place.
        ctx.assert(
            "cases-differing-in-kind",
            compared > 0 ? differing_in_kind.length : null,
            "==",
            0,
            {
                note: `cases whose sequence of bubbles and blocks differs between client and server: ${differing_in_kind.join("; ") || "none"}`,
            },
        );
        ctx.assert("cases-differing", differing.length, "==", 0, {
            note: `u = bubble, l = block, in order; differing: ${differing.slice(0, 12).join("; ") || "none"}${differing.length > 12 ? `; and ${differing.length - 12} more (all in the report)` : ""}`,
        });
        ctx.info("cases-differing.names", differing.length, {note: differing.join("; ")});
    });
}
