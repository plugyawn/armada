---
name: armada-plugin
description: Make or change an Armada plugin, the small program behind a widget in the team's nav or a channel's column, and behind tools you can call. Use it when someone asks for a widget, a plugin, or a change to one ("make us a widget for the eval queue", "show the GPUs in the nav", "add a column for the owner").
---

# Making an Armada plugin

A plugin is a small program that Armada runs on the server. It feeds one or
more **widgets** (a row in everyone's nav, a list in a channel's column, a
sheet that opens from a row), and it can offer **tools** to you over MCP. The
team's plugins live in one repository, the `plugins` channel: each plugin is
a worktree, `plugins › <id>`, with its code in `plugins/<id>/`.

## First: does it need code?

A request about a widget is one of three kinds. Say which in your reply's
first words.

1. **For the person only, no code**: hiding a widget, its label, the figure
   at rest, which rows it shows, a threshold that marks it. Set it with
   `armada_widget_preference`; it changes only the asker's view. Begin "For
   you:".
2. **For the team, no code**: links, the team default, the plugin's own
   settings. Begin "For everyone:".
3. **Code**: a new field, metric, row, source, widget, tool or cadence.
   That changes the plugin for everyone, so say so. A personal option that
   doesn't exist yet (a filter on a field the widget lacks) is code: everyone
   gets the option, and only the asker turns it on.

For code, do the work in the plugin's own place. If you were asked
elsewhere, answer there in one line ("I'll make it in plugins › eval-queue
and post it here when it's in"), then call `armada_start_run` with
`channel: "plugins"`, the plugin's id as the topic, and the request as the
brief. The build runs there.

## The build

Make a checklist with your plan tool, and keep it current:

1. Read the request and this skill.
2. Write the plugin and its tests.
3. Check it and look at the preview.
4. Ask the person to set it up.
5. Install it for the team.
6. Post it where it was asked for.

**Write it.** Copy `template/` to `plugins/<id>/`. Rename `id`, `name` and
the widget keys, then write the real source of the data and the tools. Run
`uv lock`, then `uv run pytest` until the tests pass. Commit, including
`uv.lock`; the manifest's setup is `uv sync --frozen`.

**Check it.** `armada_plugin_check(path)`, with `path` as `plugins/<id>`,
validates the manifest and every document against the contract and starts the
plugin in a trial. In the trial its pushes are captured and reach no one, and
it is stopped after 60 seconds; the trial sets `ARMADA_TRIAL=1` in its
environment. The check renders the widget with Armada's own kit, light and
dark, and returns
`{ok, problems: [{where, what}], waiting_for, widgets, tools, previews, notes}`.
The first check keeps the plugin's id as a draft for the person you act for,
and their settings are kept there until it is installed. `waiting_for` lists
the settings and secrets to ask for. `notes` are not the plugin's fault, such
as no browser to draw with.

- Look at each preview with `view_image`. Fix what looks wrong as well as
  what the check reports, then check again.
- A figure wider than its cell is the usual problem: make `rest.sizer` as
  wide as the longest figure.

**Ask for settings.** `armada_plugin_request_settings(id, fields, prefill)`
asks the person you act for to fill in the plugin's settings sheet, then
waits. It returns the names that were set and the names still missing,
never a value.

- Never ask for a key, token or password in the conversation; that is what
  the sheet is for.
- If anything is still missing when it returns, say plainly what is still
  needed, and stop. Don't install a plugin that is half set up.

**Install it.** Use `armada_plugin_install(id, commit)` for a new plugin, or
`armada_plugin_update(id, commit)` with a higher `version` for a new one.

- `commit` is the full sha, from `git rev-parse HEAD`. It is released to
  the team's repository: `main` moves fast-forward, and the version is
  tagged.
- If `main` has moved, the answer says how to rebase. Do that, check again,
  and install the new commit.
- Install refuses until the settings are set.
- You install for the person who asked; Armada starts the plugin and records
  it.
- An update tells you what the new version asks for that the old one didn't
  (hosts, secrets, events), and which widgets it drops. Say so in your reply.
  If the update now waits for a setting, ask for it.

**Post it.** Use `armada_post` with the previews as files, in the thread where
it was asked: "Eval queue is in everyone's nav…".

## The manifest, `armada-plugin.json`

`reference/manifest.schema.json` is the whole contract, and
`reference/examples/` holds two real plugins: W&B and GPUs. In short:

- `id`: lowercase letters, digits and `-`, as the directory is named. `name`,
  `version`, `description` and a `glyph` from the kit's list in the schema.
- `run`: `setup` and `command` as argument lists, run in the plugin's
  directory. A plugin that is a service elsewhere gives `remote: {url}`
  instead, and then may not ask for secrets.
- `settings`: a JSON-schema form (strings, numbers, booleans, enums, lists)
  that people fill in. The program reads them as JSON from `ARMADA_SETTINGS`.
- `secrets`: each with a `title` and the `env` name the program reads it
  from. A secret never appears anywhere but the program's environment, so
  never print or post one.
- `provides.widgets`: up to four. `in` is any of `nav`, `column` (with
  `column_slots`, 1 to 3) and `open` (a sheet opened from a row). Give a
  `cadence` in seconds, how often it is fed.
- `provides.links`: what a channel or topic can be linked to (a project, a
  pool). A widget in the column shows where its plugin is linked, so a
  plugin with one offers a link.
- `provides.tools`: `{path: "/mcp", instructions}`. The instructions are the
  one line you are given about the plugin, so make it say when to use it.
- `reaches`: the hosts it talks to, shown to whoever installs it.

**Declare the obvious personal options**, so Edit has real choices from the
first version:

- `figures`: every number worth seeing at rest, each with its own `sizer`.
- `facets`: every person or machine field in the rows, such as an owner, a
  user or a GPU type.
- `alerts`: a threshold on a number that matters, with `op` (`>=` or `<=`)
  and its range.

The widget's `team_default` is `shown` or `offered`. The first six widgets in
a team are shown in everyone's nav; after that a new one arrives offered.

## The widget document

A widget is data only. There is no HTML, CSS, colour, image or font, because
Armada draws it with its own kit. `reference/widget.schema.json` has every
field.

- `rest`: what the nav row shows at rest. `figure` binds a short string, such
  as `data.waiting` holding "12 waiting". `sizer` is a string as wide as the
  longest figure it will ever show ("88 waiting"), so the row never moves.
  `news` binds to `failed` or `needs`, which takes the figure's place.
- `blocks`: a `list` (at most 32 rows, 3 in the column) bound to
  `data.<rows>`, and `metric`, `meter`, `series`, `key_value`, `log` and
  `copy`.
- A row has a `key` (stable: actions and personal views use it), a `label`,
  and optionally:
  - `sub`, a `figure`, and `code: true` for names in mono;
  - a `state`, one of `done`, `live`, `waiting`, `needs`, `failed`, `paused`
    or `stopped`;
  - a `glyph`, and a `spark` (with a `domain` such as `[0, 100]` for
    percentages);
  - `facets` (at most four plain values), `open` (`{url}` or
    `{widget}`), and an `action`.
- `actions` (at most 4):
  - `prompt` asks you, in the place the person is;
  - `tool` calls one of the plugin's tools for the person who pressed. Give
    it `confirm` when it can't be undone;
  - `link` opens an http(s) page.

## The program

`template/plugin.py` shows the whole shape:

- It serves MCP's Streamable HTTP transport on `127.0.0.1:$ARMADA_PORT` at
  the manifest's tools path, and `GET /health`.
- It refuses any request without `Authorization: Bearer $ARMADA_PLUGIN_TOKEN`.
- A tool call's `_meta["armada/user"]` names the person it is for.
- It pushes each widget every cadence with `push()` (`armada_push.py`), as
  its own bot (`ARMADA_SITE`, `ARMADA_EMAIL`, `ARMADA_API_KEY`). A widget not
  fed for three cadences shows as paused.
- It posts in its linked topics with the Zulip client, as the same bot.
