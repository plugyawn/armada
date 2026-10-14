// Written by tools/armada/icons.py from tools/armada/icons_gen.py; never
// edit it by hand. The names of the drawings in sprite.svg: a component
// uses a glyph, a mark or the sigil by one of these and draws none itself.

export const glyph_groups = [
    {
        group: "Nav",
        glyphs: [
            {
                name: "search",
                means: "Jump to; find in files",
            },
            {
                name: "home",
                means: "Home",
            },
            {
                name: "repo",
                means: "repository, folded",
            },
            {
                name: "repo-open",
                means: "repository, unfolded",
            },
            {
                name: "hash",
                means: "channel with no repository",
            },
            {
                name: "lock",
                means: "private channel",
            },
            {
                name: "plus",
                means: "new; add; stage",
            },
            {
                name: "nav",
                means: "show or hide the nav",
            },
        ],
    },
    {
        group: "Header",
        glyphs: [
            {
                name: "branch",
                means: "branch",
            },
            {
                name: "commit",
                means: "commit",
            },
            {
                name: "pr",
                means: "pull request",
            },
            {
                name: "more",
                means: "more actions",
            },
            {
                name: "panel",
                means: "show or hide the panel",
            },
        ],
    },
    {
        group: "Composer",
        glyphs: [
            {
                name: "clip",
                means: "attach",
            },
            {
                name: "send",
                means: "send",
            },
            {
                name: "steer",
                means: "steer the live run",
            },
            {
                name: "answer",
                means: "answer the agent's question",
            },
            {
                name: "queue",
                means: "queue behind the run",
            },
            {
                name: "stop",
                means: "stop the run",
            },
            {
                name: "down",
                means: "opens a picker; expanded",
            },
        ],
    },
    {
        group: "Message",
        glyphs: [
            {
                name: "react",
                means: "add a reaction",
            },
            {
                name: "thread",
                means: "reply in thread",
            },
            {
                name: "copy",
                means: "copy",
            },
            {
                name: "link",
                means: "copy link",
            },
            {
                name: "edit",
                means: "edit; the agent edited a file",
            },
            {
                name: "retry",
                means: "retry",
            },
            {
                name: "open",
                means: "open in the panel or outside",
            },
            {
                name: "latest",
                means: "jump to latest",
            },
        ],
    },
    {
        group: "Files",
        glyphs: [
            {
                name: "folder",
                means: "folder, shut",
            },
            {
                name: "folder-open",
                means: "folder, open",
            },
            {
                name: "file",
                means: "file",
            },
            {
                name: "doc",
                means: "document; README",
            },
            {
                name: "image",
                means: "image",
            },
            {
                name: "table",
                means: "table",
            },
            {
                name: "chart",
                means: "chart",
            },
            {
                name: "term",
                means: "command; terminal output",
            },
        ],
    },
    {
        group: "Changes",
        glyphs: [
            {
                name: "diff",
                means: "changed; a diff",
            },
            {
                name: "minus",
                means: "remove; unstage",
            },
            {
                name: "check",
                means: "chosen; passed",
            },
            {
                name: "x",
                means: "close; dismiss",
            },
            {
                name: "expand",
                means: "give it the pane",
            },
            {
                name: "right",
                means: "collapsed; goes deeper",
            },
        ],
    },
    {
        group: "Trace",
        glyphs: [
            {
                name: "trace",
                means: "the run's trace",
            },
            {
                name: "eye",
                means: "the agent read; a monitor",
            },
            {
                name: "globe",
                means: "the agent fetched from the web",
            },
        ],
    },
    {
        group: "Settings",
        glyphs: [
            {
                name: "person",
                means: "person; members",
            },
            {
                name: "settings",
                means: "settings",
            },
            {
                name: "sound",
                means: "sounds on",
            },
            {
                name: "mute",
                means: "sounds off",
            },
        ],
    },
] as const;

export type GlyphName = (typeof glyph_groups)[number]["glyphs"][number]["name"];

export const mark_names = [
    "done",
    "live",
    "waiting",
    "needs",
    "failed",
    "paused",
    "stopped",
] as const;

export type MarkName = (typeof mark_names)[number];

export const sigil_names = ["20", "28", "64", "20-outline"] as const;

export type SigilName = (typeof sigil_names)[number];

// The working mark is one sprite of this many drawings side by side.
export const working_steps = 4;
