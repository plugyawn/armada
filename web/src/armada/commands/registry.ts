// The one list of commands (A5 fills it and draws the palette, menus and
// the shortcut sheet from it). A surface lists its own in its commands.ts;
// boot.ts registers every surface's list here once.

export type Command = {
    // One name everywhere: "column.open", "run.stop".
    id: string;
    // What a person reads in the palette and in menus, already translated.
    label: string;
    // The key, in keymap.ts's spelling ("Mod+J"), if it has one.
    key?: string | undefined;
    run: () => void;
};

const commands = new Map<string, Command>();

export function register(list: readonly Command[]): void {
    for (const command of list) {
        if (commands.has(command.id)) {
            throw new Error(`command registered twice: ${command.id}`);
        }
        commands.set(command.id, command);
    }
}

export function get(id: string): Command | undefined {
    return commands.get(id);
}

export function all(): Command[] {
    return [...commands.values()];
}

export function run(id: string): boolean {
    const command = commands.get(id);
    if (command === undefined) {
        return false;
    }
    command.run();
    return true;
}

export function clear_for_testing(): void {
    commands.clear();
}
