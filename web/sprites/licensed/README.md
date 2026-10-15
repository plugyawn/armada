# Pokémon sprites (not in git)

Armada's team theme draws each person's partner Pokémon: a face is its
Scarlet/Violet menu icon standing on a circle (`sv/`). The Sword/Shield party
icons (`pokesprite/`, `smogon-gen9/`) still decide which forms the server
counts as drawable, and stand in where a form has no Scarlet/Violet icon. The
images are © Nintendo / Creatures Inc. / GAME FREAK Inc., so
this directory is git-ignored: only this README is committed, and the server
serves the files to signed-in people only. Nothing here is bundled.

A checkout without these files still works: faces fall back to plain marks.

To set the directory up on a new machine, fetch exactly these, at these
versions, into the subdirectories named:

- `pokesprite/`: Gen 1–8 icons (68×56) from PokéSprite at commit
  `c5aaa610ff2acdf7fd8e2dccd181bca8be9fcb3e`, published as
  https://registry.npmjs.org/pokesprite-images/-/pokesprite-images-2.7.0.tgz
  (sha512 `LQuzyaEDl4ow75fPzrVuJB0cIRmxVq4XBecDFajA0WrZI1xHCmXn5OSBul44D+24tA1kMT7GSROmct5FRg8dtQ==`).
  Keep `pokemon-gen8/`, `data/pokemon.json`, `license.md` and `package.json`
  (`tar --strip-components 1`), then delete the tarball.
- `smogon-gen9/`: the 147 Gen 9 minisprites (Pokédex 906–1025 and their forms,
  44,553 bytes) listed in `smogon-gen9.txt` (committed), from
  `https://raw.githubusercontent.com/smogon/sprites/8d8d474e677e5a524f6564f2f208111d70cc3a4a/src/minisprites/pokemon/gen6/<file>`.
- `showdown/pokedex.ts`: species and evolution data (MIT), 538,263 bytes, from
  https://raw.githubusercontent.com/smogon/pokemon-showdown/3a766f2cfb5e51c1e428cfe87d0dba91a8f01640/data/pokedex.ts
- `sv/`: the Scarlet/Violet menu icons (smooth renders, trimmed, one PNG a
  species named by its Pokédex number, as `4.png`; PokéAPI's own ids name a
  few forms, which nothing reads), 1,018 files, about 16.6 MB, from
  `https://raw.githubusercontent.com/PokeAPI/sprites/0dc105d5b12416932f29b594078282ab4be045f5/sprites/pokemon/versions/generation-ix/scarlet-violet/icons/<file>`.
  Species that are not in Scarlet/Violet (Onix and Steelix among them) have
  none; the picker does not offer them.

## `species.json`: the partner pack's species (generated)

`tools/armada/sprites.py` (committed) reads `showdown/pokedex.ts` (as text,
never run), `pokesprite/data/pokemon.json` and the sprites, measures each
sprite's content box, and writes `species.json` here: every species and
regional form, its types, its line (prevo, evolutions, how), its sprites
(regular and shiny) and their boxes, its Scarlet/Violet icon and its box
(`sv`), and whether the picker offers it (`offer`: a first form with an
icon). It names Pokémon, so it stays here
and is never committed. Run it after the sprites change:

    python3 tools/armada/sprites.py [--dir <this directory>]

(Python 3.10 or later; in the dev VM, `limactl shell armada -- arm sh
"python tools/armada/sprites.py --dir /Users/progyan/runix/zulip/web/sprites/licensed"`.)
The server serves it and the sprites to signed-in people only, at
`/beta/sprites/<path>` (`zerver/views/armada_beta.py`); without it,
partners are off and faces are initials.

The study that chose these sources is `var/armada-overhaul/design/r4/sprites/SPRITES.md`
on the machine the rebuild is done on.
