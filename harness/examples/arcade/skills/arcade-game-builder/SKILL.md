---
name: arcade-game-builder
description: Build and improve offline games inside Arcade Studio, including responsive controls, local assets, bounded 3D scenes, and versioned player progress. Use with Arcade's project tools, not for modifying the desktop host or harness.
---

# Arcade game building

Preserve the user's requested gameplay and established controls when polishing art.
Work on the current project files, not a remembered revision. Keep the approved
published game intact until the user accepts publication. Do not claim visual or
playthrough verification unless available tools actually performed it.

## Visual decisions

- Pick a coherent visual language. Improve silhouette, material contrast, scale,
  lighting and composition before adding more objects. A collection of primitives
  is a useful blockout, not a finished environment.
- In 3D, use shaped meshes or available GLB assets for objects seen from many angles;
  textures for surface detail; sprites for foliage, particles and distant accents.
  Sprites are not a substitute for a car's silhouette or a building's depth.
- Inspect available assets first. Never assume an image/model generation service is
  available. Locally generated canvas textures and geometry are valid alternatives.
  Preserve licenses and provenance. Avoid promotional art that misrepresents gameplay.
- Make one representative scene look good at normal and compact sizes before
  repeating the treatment across a level. Keep controls legible against the scene.

## Controls and runtime

- Clear held inputs on blur/visibility loss; pause motion rather than allowing a
  hidden game to continue. Support pointer cancellation and key release. Optional
  audio starts only after a user gesture; handle unavailable audio gracefully.
- Use elapsed time, bound large frame gaps, and keep simulation rules separate from
  rendering where practical. Explicitly handle win, loss, restart and pause states.
- Reuse geometry/materials, instance repeated scenery, cap device pixel ratio and
  active lights. Dispose owned textures/geometries/materials when changing levels;
  do not dispose shared assets still used by the player. Pool short-lived effects.
- Verify triangle winding, normals and transparent surfaces from actual camera
  angles. Test a whole level, not just the title screen or a render counter.

## Local player progress

Arcade injects `window.arcadeSave` into the isolated preview. This is the only
persistent game API. `localStorage` and `sessionStorage` remain temporary memory.
Exports outside Arcade may have no `arcadeSave`; keep the game playable without it.

```js
const saves = window.arcadeSave;
const record = saves ? await saves.load("campaign", 1) : null;
// Validate record.value against this game's own state schema before restoring it.
// Show Continue; restore paused, and wait for the player's input to resume.
await saves?.save("campaign", 1, { level: 2, inventory: ["key"] });
const best = await saves?.submitScore("time-trial-v1", seconds, "lower");
// best.score is the best value, not necessarily the latest submitted value.
const previous = await saves?.best("time-trial-v1");
// Only an explicit New game/reset or successful completion should discard a save.
await saves?.remove("campaign");
```

`load(slot, version)` returns null or `{version, value, updatedAt}`. `save` resolves
after the host database write. `best` returns null or `{score, order, updatedAt}`.
`submitScore` accepts `lower` or `higher`; an existing board's ordering is fixed.
Scores are local convenience data, not anti-cheat or multiplayer authority.

Slot/board names: 1–40 ASCII letters, digits, `_` or `-`. Format versions: integers
1–10000. Limits: 256 KB JSON per save, 16 slots and 1 MB per game, 64 score boards.
Scores must be finite numbers from 0 to 1e12. The host assigns the game scope;
never supply IDs, URLs, database paths or SQL. Remixes/clones have separate progress.

Serialize writes so older checkpoints cannot replace newer ones. Save at meaningful
checkpoints and periodically (not every frame); show failures honestly. A crash
can lose progress since the last acknowledged save. Do not rely on unload handlers.
Keep checkpoints separate from best scores. Store only gameplay state, not scene
objects, keys, audio contexts or personal information. Validate finite numbers,
level identifiers and bounds; restore paused. A different format version is an
error, not an invitation to overwrite old progress. Preserve it and offer an
explicit new game or implement a deliberate compatibility path. Art-only changes
should not invalidate otherwise compatible saves. Use new score-board names when
course/rule changes make times incomparable.

## Acceptance

When runtime inspection is available, exercise actual keyboard/pointer controls,
complete a level, restart, pause, switch levels and test compact UI. Check console
errors, asset loading and stable resource counts. For persistence, close/reopen,
continue from a checkpoint, verify best-score ordering and test malformed/version-
mismatched data without destroying it. A demo running in memory is not proof that
the packaged game or durable save path works.
