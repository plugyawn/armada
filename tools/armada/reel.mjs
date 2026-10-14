#!/usr/bin/env node
// The sound reel: every recipe rendered to a WAV by the code the
// application runs (web/src/armada/sound/recipes.ts), for an audition.
//
//   tools/armada/reel [directory] [--audition]   (this file, through a shell wrapper)
//
// Writes one file a sound and reel.wav, all of them in turn, into the
// directory (default var/armada-sound/), and prints each sound's measured
// level beside its tier. --audition adds 12 dB, so that laptop speakers
// carry it; without it the files are at the shipped levels. Exits 1 if a
// sound is more than 0.1 dB from its tier.
import fs from "node:fs";
import path from "node:path";

const AUDITION_DB = 12;
const GAP_SECONDS = 0.5;
const TOLERANCE_DB = 0.1;

function wav(samples, rate, gain) {
    const data = Buffer.alloc(44 + samples.length * 2);
    data.write("RIFF", 0);
    data.writeUInt32LE(36 + samples.length * 2, 4);
    data.write("WAVEfmt ", 8);
    data.writeUInt32LE(16, 16);
    data.writeUInt16LE(1, 20);
    data.writeUInt16LE(1, 22);
    data.writeUInt32LE(rate, 24);
    data.writeUInt32LE(rate * 2, 28);
    data.writeUInt16LE(2, 32);
    data.writeUInt16LE(16, 34);
    data.write("data", 36);
    data.writeUInt32LE(samples.length * 2, 40);
    for (const [index, sample] of samples.entries()) {
        const value = Math.max(-1, Math.min(1, sample * gain));
        data.writeInt16LE(Math.round(value * 32767), 44 + index * 2);
    }
    return data;
}

async function main() {
    const arguments_ = process.argv.slice(2);
    const audition = arguments_.includes("--audition");
    const directory =
        arguments_.find((argument) => !argument.startsWith("--")) ?? "var/armada-sound";
    const {SAMPLE_RATE, gain_of, render, table, sound_names} = await import(
        path.join(import.meta.dirname, "../../web/src/armada/sound/recipes.ts")
    );
    const gain = gain_of(audition ? AUDITION_DB : 0);
    fs.mkdirSync(directory, {recursive: true});
    const gap = new Float32Array(Math.round(GAP_SECONDS * SAMPLE_RATE));
    const reel = [];
    for (const name of sound_names) {
        const samples = render(name);
        fs.writeFileSync(path.join(directory, `${name}.wav`), wav(samples, SAMPLE_RATE, gain));
        reel.push(samples, gap);
    }
    const all = new Float32Array(reel.reduce((length, part) => length + part.length, 0));
    let offset = 0;
    for (const part of reel) {
        all.set(part, offset);
        offset += part.length;
    }
    fs.writeFileSync(path.join(directory, "reel.wav"), wav(all, SAMPLE_RATE, gain));
    let failed = false;
    for (const row of table()) {
        const off = Math.abs(row.rms100 - row.tier);
        failed ||= off > TOLERANCE_DB;
        console.log(
            `${row.name.padEnd(11)} ${row.set.padEnd(8)} tier ${String(row.tier).padStart(4)} dBFS  loudest 100 ms ${row.rms100.toFixed(2).padStart(7)}  peak ${row.peak.toFixed(2).padStart(7)}  ${String(row.milliseconds).padStart(4)} ms${off > TOLERANCE_DB ? "  OFF ITS TIER" : ""}`,
        );
    }
    console.log(
        `${sound_names.length} sounds and reel.wav in ${directory}${audition ? `, ${AUDITION_DB} dB over the shipped levels` : ", at the shipped levels"}`,
    );
    process.exitCode = failed ? 1 : 0;
}

await main();
