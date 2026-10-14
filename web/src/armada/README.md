# web/src/armada

The rebuilt frontend. `boot.ts` is the one module `ui_init.js` calls: it mounts the frame, wires stores to server events and registers commands. Everything else is in a directory named for its job; each has a README like this one. The rules are in `.claude/rules/` and `docs/armada/`.

Not here: anything under `web/styles/` or `web/templates/`; a module that imports jQuery, Handlebars, tippy.js or micromodal; the word `pear` outside `api/`.

To add one: pick the directory by what the code does (draws a component: `kit/`; holds data: `state/`; talks to the server: `api/`; is a place on screen: `surfaces/<name>/`). A new top-level directory needs a change to `docs/armada/architecture.md` first.
