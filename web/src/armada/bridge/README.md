# bridge

Temporary hosts and styles that keep an old surface alive inside the new frame until its replacement lands. Each file is listed below with the package that deletes it; this directory must be empty at the final close.

Not here: new features, or anything a new surface imports.

To add one: name it `legacy_<area>.ts`, add a line here (`- legacy_<area>.ts: what it hosts; deleted by <package>`), and record the old files as `bridged:<package>` in `tools/armada/fork_ledger.d/`.

## In the tree

- legacy_feed.ts (+ .css): the old message view in the frame's pane, its rows in the new faces and inks; deleted by D1
- legacy_nav.ts (+ .css): the old left sidebar in the nav column; deleted by B1
- legacy_header.ts (+ .css): the old title, search and menus on the header's first line and the worktree's controls (runtime, branch, commit, views) that pear_panel.ts draws into it; deleted by B2 (the search box by D1, the menus by C5)
- legacy_composer.ts (+ .css): the old compose box in the field's track; deleted by B3
- legacy_column.ts (+ .css): the old right rail (wall, roster, people, thread pane) in a sheet from the window's right edge, closed at rest; deleted by B4 (B8 for the wall and the people)
- legacy_home.ts (+ .css): Inbox and Recent as panes, each its own scroller; deleted by B5

Not built yet, though the plan names them for F5: legacy_search.ts (the search box is still on the header's first line, not in a sheet from Jump to), legacy_account_menu.ts (the gear and personal menus are still on that line), legacy_overlays.ts (the old overlays are fixed over the frame already and needed no host).
