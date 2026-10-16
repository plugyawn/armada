// Records the two runs that `stream-cost` replays.
//
//   limactl shell armada -- arm sh "source ~/shot.env; node tools/armada-checks/lib/record-stream.mjs"
//
// A run is what the server sent while an agent answered when these were
// recorded: an `update_message` event every 200 ms carrying the whole
// text so far and the server's rendering of it. The server now sends
// the new words as `agent_stream` events and edits the message every 2 s
// (zerver/lib/armada.py, ARMADA_PERSISTENT_ASSISTANT_FLUSH_INTERVAL_SECONDS);
// the old interface these replay into still follows the edits. The text is
// fixtures/stream-source.md, repeated until it is long enough; each prefix
// is rendered by the running development server, so the HTML is exactly
// what the server would have put in the event. The words-per-second rate
// only decides how much text each event adds.

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {launch, login} from "./browser.mjs";
import {CheckContext} from "./harness.mjs";
import {keep_targets} from "./host.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(HERE, "../fixtures");
const FLUSH_MS = 200;
const SECONDS = 2;

function words_of(source, count) {
    // Words with the white space that follows each, so that prefixes keep
    // the line structure Markdown depends on.
    const pieces = [];
    let part = 1;
    while (pieces.length < count) {
        const text = part === 1 ? source : `\n\n**Part ${part}.** ${source}`;
        pieces.push(...(text.match(/\S+\s*/g) ?? []));
        part += 1;
    }
    return pieces.slice(0, count);
}

async function record(ctx, source, words_per_second) {
    const pieces = words_of(source, words_per_second * SECONDS);
    const per_flush = (words_per_second * FLUSH_MS) / 1000;
    const edits = [];
    for (let words = per_flush; words <= pieces.length; words += per_flush) {
        const content = pieces.slice(0, words).join("").trimEnd();
        let response;
        for (let attempt = 0; attempt < 5; attempt += 1) {
            response = await ctx.api("POST", "/json/messages/render", {content});
            if (response.status !== 429) {
                break;
            }
            await ctx.sleep((Number(response.data?.["retry-after"]) || 10) * 1000 + 500);
        }
        if (response.status !== 200) {
            throw new Error(`render failed: HTTP ${response.status}`);
        }
        edits.push({
            at_ms: (words / words_per_second) * 1000,
            words,
            content,
            rendered_content: response.data.rendered,
        });
    }
    return {
        recorded: new Date().toISOString().slice(0, 10),
        about: "update_message events of one agent answer as today's server sends them: the whole text so far, every 200 ms",
        words_per_second,
        flush_ms: FLUSH_MS,
        duration_ms: SECONDS * 1000,
        words: pieces.length,
        edits,
    };
}

const browser = await launch();
try {
    await login(browser);
    const ctx = new CheckContext(browser, "record-stream");
    await ctx.init();
    await ctx.open("#inbox", {rows: false});
    const source = fs.readFileSync(path.join(FIXTURES, "stream-source.md"), "utf8").trim();
    for (const rate of [400, 1200]) {
        const run = await record(ctx, source, rate);
        const text = `${JSON.stringify(run, null, 4)}\n`;
        for (const target of keep_targets(`tools/armada-checks/fixtures/stream-${rate}.json`)) {
            fs.writeFileSync(target, text);
        }
        console.log(
            `stream-${rate}.json: ${run.edits.length} events, ${run.words} words, ${text.length} bytes`,
        );
    }
} finally {
    await browser.close();
}
