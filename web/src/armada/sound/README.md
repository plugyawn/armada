# sound

`sound.ts` is the engine (created on the first gesture, never while muted), its limiter and the rules for when a sound may fire. `recipes.ts` is the sounds as data and the arithmetic that renders them, shared with `tools/armada/reel`, which writes them to WAV files for an audition.

Not here: an audio file, or `AudioContext` anywhere else in the tree.

To add one: add its name and recipe to `recipes.ts` at its tier's level, hear it with `tools/armada/reel --audition` and on the showroom's bench, and trigger it from a command or a store, not from a component.
