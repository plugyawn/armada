// Keys to commands: one key, one command, everywhere. A key is written
// "Mod+J", where Mod is Command on a Mac and Control elsewhere. hotkey.ts
// still owns today's keys; each moves here with the package that rebuilds
// what it does.

import * as registry from "./registry.ts";

export type Chord = {key: string; mod: boolean; shift: boolean; alt: boolean};

export function parse(spelling: string): Chord {
    const parts = spelling.split("+");
    const key = parts.at(-1)!;
    const modifiers = new Set(parts.slice(0, -1));
    return {
        key: key.length === 1 ? key.toLowerCase() : key,
        mod: modifiers.has("Mod"),
        shift: modifiers.has("Shift"),
        alt: modifiers.has("Alt"),
    };
}

export function matches(
    chord: Chord,
    event: {key: string; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean},
    mac: boolean,
): boolean {
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    const mod = mac ? event.metaKey : event.ctrlKey;
    return (
        key === chord.key &&
        mod === chord.mod &&
        event.shiftKey === chord.shift &&
        event.altKey === chord.alt
    );
}

// The command a key press asks for, if any registered command has that key.
export function command_for(
    event: {key: string; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean},
    mac: boolean,
): registry.Command | undefined {
    return registry
        .all()
        .find((command) => command.key !== undefined && matches(parse(command.key), event, mac));
}
