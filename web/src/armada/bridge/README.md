# bridge

Temporary hosts and styles that keep an old surface alive inside the new frame until its replacement lands. Each file is listed below with the package that deletes it; this directory must be empty at the final close.

Not here: new features, or anything a new surface imports.

To add one: name it `legacy_<area>.ts`, add a line here (`- legacy_<area>.ts: what it hosts; deleted by <package>`), and record the old files as `bridged:<package>` in `tools/armada/fork_ledger.d/`.

## In the tree

- legacy_feed.ts (+ .css): the old message view in the frame's pane, its rows in the new faces and inks; deleted by D1
- legacy_nav.ts (+ .css): the old left sidebar in the nav column; deleted by B1
- legacy_header.ts (+ .css): the old title, search and menus on the header's first line and the worktree's controls (runtime, branch, commit, views) that legacy_panel.ts draws into it; deleted by B2 (the search box by D1, the menus by C5)
- legacy_composer.ts (+ .css): the old compose box in the field's track; deleted by B3
- legacy_column.ts (+ .css): the old right rail (wall, roster, people, thread pane) in a sheet from the window's right edge, closed at rest; deleted by B4 (B8 for the wall and the people). While the sheet is closed the wall draws nothing, so `idle-cost/wall-monitors.*` left `known.json` at F5 for that reason, not because the wall got cheaper: B8 meets them again when it draws monitors in the column
- legacy_home.ts (+ .css): Inbox and Recent as panes, each its own scroller; deleted by B5

Not built, though the plan names them for F5:

- legacy_search.ts: the search box stays on the header's first line (legacy_header) until the nav has a Jump to row for its sheet (B1); `/` opens it there.
- legacy_account_menu.ts: the help, gear and personal menus stay together at the end of that line. The workspace's name is already the nav's top row, but the menus are one ring for the keyboard (G opens the gear menu, the arrows move between the three), so the gear menu moves to that row with the other two, when the nav's account row exists (B1, C5).
- legacy_overlays.ts: the old overlays (settings, channels, drafts and the rest) already sit fixed over the frame and need no host until C5 replaces them.

The old navbar's alerts are raised under the header, across the lane, by rules in frame.css and legacy_header.css.
