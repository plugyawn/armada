# Pokémon sprites (not in git)

Armada's team theme draws each person's partner Pokémon with the Sword/Shield
party icons. The images are © Nintendo / Creatures Inc. / GAME FREAK Inc., so
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

The study that chose these sources is `var/armada-overhaul/design/r4/sprites/SPRITES.md`
on the machine the rebuild is done on.
