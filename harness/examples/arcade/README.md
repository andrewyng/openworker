# Arcade Studio

A desktop game builder on the OpenWorker Harness SDK. Describe a game, watch it
take shape, approve a saved version, and play it. Return to your library to edit,
clone, restore a previous version, or export a complete game project.

## Agent playtesting

New v7 builders can submit `testing.report`: an honest checklist for the exact
draft digest. The Testing panel separates Arcade-recorded screenshot/input counts
from the agent's Passed / Failed / Not tested assessment. Inputs alone never become
automatic passes. Reports survive restart, become stale after draft changes or
rollback, and cannot certify durable saves or platform compatibility. Corrupt or
future-format evidence is preserved and shown as unavailable.

At a `project.publish` approval, choose **Compare update** to switch between the
current version and the exact proposed snapshot and inspect changed filenames.
Both comparison panes have temporary saves only. The proposal is not published
until approved; changed bytes invalidate its approval digest. Existing Versions
controls provide one-click rollback. Legacy single-file `game.publish` approvals
retain their original flow without a project comparison.

Pip's Adventure 2.1 includes HTML/CSS HUD refinements produced by a real v6 agent
remix: larger minimap labels, less overlay clutter, desktop/touch distinction and
menus that hide the map. Gameplay code and save formats are unchanged. The first
attempt hit the 20-step turn limit; a second, budgeted run completed inspection,
editing, playtesting and approval. New v7 guidance budgets reads/edits/testing/report
and publication together rather than spending the full budget on preview actions.

Builder sessions (v6 and v7) can use `preview.playtest` to open their current draft,
inspect screenshots and console diagnostics, press keys, click/drag, wait and close.
Use a vision-capable model. Screenshots are sent to that configured provider; a local
vision model keeps that inference local. Provider-side retention is governed by that
provider, not by Arcade's local image cache.

The preview runs in a separate hidden, sandboxed Electron renderer, not the user's
desktop or playable window. It serves one immutable draft snapshot with no external
network, file access, preload, native bridge, popups, downloads or permission grants.
Test saves/scores are temporary memory and never access real player progress.
Editing the draft marks the preview stale; reopening loads the new digest and resets
test progress. The published version stays intact until normal publication approval.

Actions are sequential and bounded: key holds up to 2 seconds, pointer drags up to
1 second, waits up to 3 seconds, screenshots at 960×600 or 1280×800, and a 12-second
operation deadline. Inputs release in cleanup; cancellation, end-of-turn and app exit
close the renderer. An idle preview expires after two minutes. Metrics report heap,
DOM nodes and cumulative task/script time, not an FPS or GPU benchmark.

Only the latest screenshot is ephemeral model context (five-minute maximum), never
pixels in durable harness events/history. The builder should record concise visual
observations before its next action. Console/page text remains untrusted game data.
No video or microphone capture is implemented; recording is optional future work,
not a prerequisite for building or playtesting.

Existing v1–v6 sessions keep their pinned definitions and tools. Start a fresh game
or remix to use v7; existing conversations are not silently reset or upgraded.

Checks: `npm run test:playtest` exercises real hidden-renderer input and isolation;
`npm run test:integration` verifies image delivery through real SDK callbacks and
that pixels do not appear in replayed events. `test/live-playtest.mjs` is an explicit
opt-in billable test using a temporary library and memory-only credentials. Set
`ARCADE_VISION_PROBE=1` to verify random canvas codes that cannot be read from DOM text.

## Run from source

For Windows review/playtest qualification, use `scripts/qualify-review-windows.ps1`
with `-NativeRoot`, an existing `-BuildCache`, and optionally `-Package`. A source
transfer must include both `harness/` and `harness/tests/contracts/` (the Rust runtime
embeds two contract fixtures). Windows file-symlink unit fixtures need creation
privileges; run that unit suite in a separate privileged test process if necessary,
then use `-SkipUnit` for the normal-user app/UI run. Do not elevate Arcade or enable
system-wide developer policies merely to run the application. No cloud keys are
needed for this fixture-based qualification.

Requires Node.js 24+, npm, and the Rust toolchain for your OS. On Windows, use
the MSVC Rust target with Visual Studio C++ build tools and Clang (SQLite build).

From this repository's `harness` directory:

```sh
npm --prefix typescript-sdk ci
cd examples/arcade
npm ci
npm run runtime
npm run dev
```

`npm run runtime` builds and bundles the harness for the current OS/architecture.
To use an existing compatible build, set `OPENWORKER_RUNTIME` to its absolute path.
The app is not a browser website; Electron manages the Rust child using private
stdio. No application server is required.

## Connect a model

Open **Settings → Model**. Choose OpenAI, Anthropic, OpenRouter, Ollama, vLLM,
or a custom endpoint. Enter an exact model ID. Connection details let you change
the API and base URL. Saving does not make a model request; **Test connection**
sends a short, potentially billable request explicitly through the harness.

Keys go to the SDK's platform credential store, never the Arcade catalog or
settings file. Leave the key blank to keep an existing credential. For local
models, start your model server first; Arcade does not install or download models.
The model must support tool calls. Default-model changes affect **new games**;
existing sessions retain their original provider/model. Updating a credential for
the same named provider/API/endpoint also supports its existing games.

Defaults: [Ollama](https://docs.ollama.com/api/openai-compatibility) uses
`http://127.0.0.1:11434/v1`; vLLM uses `http://127.0.0.1:8000/v1`.
[OpenRouter](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
uses `https://openrouter.ai/api/v1`. Custom plain HTTP endpoints are restricted to
loopback; remote endpoints require HTTPS.

## Create and play

Home is a game-first shelf with a featured game and six bundled, playable
starters. **My games** holds your saved collection. Every saved card offers
**Play**, **Clone**, and **Continue building**. Play opens an immersive view without starting the harness.
Clone copies all published files into an independent project with a fresh session.
Ideas live in **New game**, not among games you can already play.

## Bundled games and remixing

No separate repository or game server is needed yet. `starter-games/` is a
self-contained source collection ready to extract later:

```text
starter-games/
  moonlight-rally/  Three.js free-driving time trial with two coastal courses
  skybound-carrier/ Three.js delivery runner with animated GLB, artwork and sound
  neon-drift/       Canvas collect-and-dodge arcade game
  lumen-garden/    Canvas light-path puzzle, six gardens
  orbit-runner/    Three.js lane runner
    manifest.json  Stable ID, version, title, engine, tags, cover and file list
    index.html     Entrypoint; CSS/JS live alongside or in subdirectories
    LICENSE.txt    MIT license for original game code and artwork
```

- App packaging includes the source collection. `StarterCatalog` loads an explicit
  allowlist, validates manifests and bounded project paths, and freezes the files
  in memory. There is no remote discovery, executable plugin or network loader.
- **Play now** serves a version-pinned bundled original through the same restricted
  preview scheme as saved games. It creates no user project or model session.
- **Make it yours / Remix** copies the entire project into a new directory under
  the configured games root. It creates its first immutable revision, preserves
  the license and `starter-origin.json` (source ID/version/content hash), and opens
  the studio. A fresh SDK session is created lazily when the user starts editing
  with a configured model. No source files are shared by reference with the bundle.
- App upgrades can ship new starter versions; existing remixes keep their copied
  code. Clone, restore and ZIP export use the existing whole-project mechanisms.
- Three.js is injected from the app's pinned dependency into Orbit Runner and its
  remixes, including the upstream license. The source folder does not duplicate
  the dependency. To run that folder outside Arcade, supply the same pinned files
  in `vendor/`, or export a complete remix ZIP from the app.
- Cover and hero artwork is original illustration, not gameplay recordings.
  Skybound uses an original AI-generated sky background in the real-time scene;
  its creation prompt/provenance is in `asset-credits.json`. All six starters run without credentials or an internet connection.
  Gameplay scores/progress remain frame-local and reset on reopen.

To add a starter, create its folder, complete the manifest and MIT attribution,
then add its ID to `electron/starters.ts`. Keep `index.html` as entrypoint and use
only relative bundled assets. Run `npm test`, `npm run test:starters` and
`npm run test:ui`; verify the packaged app too. The starters include
reproducible gameplay tests outside their manifest file lists.

The MIT license allows redistribution, but this change does not publish a new
public repository. An AWS hub, uploads, sharing, accounts and remote package
verification are intentionally separate future work. Stable package identities
and copied provenance provide a foundation, not a claim that remote games are
already trusted or supported.

1. Choose an idea or enter your own. Game experience, look, and pace are optional.
2. Follow the live response and expandable activity row. Private reasoning is not
   displayed. An agent question or approval remains attached to the correct game.
3. Approve **once** or for **this session**, or deny the write. A successful publish
   creates an immutable revision and refreshes the preview.
4. Play, ask for a change, restore a version, or export. Saved games play offline.

One build runs at a time. Other saved games can be played while it works. Restarting
at an approval restores the same approval; opening a completed game does not send
a new model request. A failed/cancelled build preserves the previous playable
revision. Open an interrupted game to reconcile the SDK's authoritative state.

## Multi-file projects and Three.js

New builds use a working draft of HTML, CSS, JavaScript, JSON, SVG and text files.
The agent can list/read/write/delete these scoped draft files. Publication requests
approval for an **exact content digest**, then saves the entire project as one
immutable version. Draft changes do not affect the playable game. A changed digest
invalidates a pending publication; the agent must inspect and request it again.

Restoring switches every file together and resets the working draft. Cloning copies
the selected snapshot, including its library files. **Files** in the studio is a
read-only view of the published version. Export produces HTML for one-file games or
a ZIP for multi-file projects. Serve an exported module-based project with a static
web server; browsers generally block ES modules opened directly via `file://`.

Choose **Another dimension** for a Three.js build. **Try the playable 3D starter**
creates an editable local copy of Orbit Runner with no model call or key required.
Three.js **0.186.1** is pinned, installed with the app, and copied into each 3D
snapshot alongside its MIT license. No CDN is needed. Import it from
`../vendor/three.module.js` in `src/game.js`. New Three.js projects include
`vendor/addons/loaders/GLTFLoader.js` and its BufferGeometryUtils/SkeletonUtils
dependencies, with bare `three` imports rewritten to the pinned local module.
`scripts/bundle-libraries.mjs` stages these files into app-owned build resources
because Electron packaging omits dependency example directories by default.
Other addons, Phaser, package installation and arbitrary dependencies are not
included. See the [Three.js module installation guide](https://threejs.org/manual/pages/installation.html).

## Images, sound and 3D assets

Supported binary assets: PNG, WebP, JPEG, WAV, OGG and self-contained uncompressed
glTF 2.0 GLB files. In Build, **Import assets** opens a native picker. Files are
copied into `assets/imported/` in the working draft; conflicting names are rejected.
Import is unavailable during a build. The app prepares a message listing the new
paths, but does **not** send it or publish automatically. Send your instruction,
then approve the resulting publication. Use assets you have permission to use.

`project.list_files` returns paths, types and sizes; binary `read_file` returns
metadata, not encoded contents. Text tools cannot create binary files. The source
inspector shows binary metadata, while the game preview loads actual bytes.
Approvals bind to the digest of **all** code and assets. Restoring and cloning
include every asset; ZIP export contains original binary files, not base64 text.
Draft JSON uses an explicit encoded representation; published revisions store
real bytes and SHA-256 checksums. Existing text-only hashes remain compatible.
The older app cannot play new binary-asset revisions; use the updated app.

Limits: 8 MB per binary asset; 40 MB per complete project including libraries;
64 files; 600 KB authored text; 90 KB per agent text write. Image headers are
checked for dimensions (at most 8192 per side / 32 million pixels). Binary file
signatures are checked; GLB external/data URI references and required extensions
are rejected. This is not a complete media-codec validator or a CPU/GPU quota.
Preview networking remains restricted to the same immutable revision and local
Blob URLs (needed to decode textures embedded inside GLBs). HTTP(S), other game
origins, workers, child frames and native APIs remain unavailable to games.

New sessions use `arcade-studio-builder-v5` with asset-aware instructions and the
bundled [game-building skill](skills/arcade-game-builder/SKILL.md). Existing
v1/v2/v3/v4 sessions resume with their original definitions. Clone an older game to use
the newest builder. Asset support and the game are app-owned; the Rust harness
and SDK protocol have not been changed.

### Curated 3D assets

The small offline pilot library in `asset-library/` contains **Retro Coupe** and
**Pip the Robot**. New builders can call `assets.list` to inspect parts, scale,
orientation, animation names and licenses, then `assets.add` with the chosen ID.
This copies the model plus metadata/credits into `assets/library/` in the working
draft. It is idempotent, refuses to overwrite modified files and does not change
the published game until the usual publication approval. Published revisions,
remixes, restores and ZIP exports carry their own copy, never a mutable hub URL.

For example, ask: “Make a small garden game using Pip the Robot. Use its Idle,
Running and Jump clips, then Dance when I collect five fireflies.” This is an
agent-facing pilot, not yet a visual asset marketplace or a general downloader.

- Retro Coupe: CC0 model by Guido Odendahl / Eric Chadwick from Khronos's ToyCar.
  Prepared with separate named wheel pivots, Y-up / -Z-forward orientation and
  metre scale. About 91,000 triangles and 6.49 MB: a hero car, not a traffic LOD.
- Pip: Quaternius's CC0 RobotExpressive, converted by Don McCurdy, unchanged at
  464 KB with 14 animation clips. Metadata explains visual normalization and
  independent gameplay transforms; a source mesh is not itself a controller.
- These are curated reference assets, **not Meshy-generated assets**. Meshy output
  still needs independent quality, licensing and format qualification.

The source URLs and SHA-256 hashes are in `asset-library/catalog.json`.
`node scripts/prepare-asset-pilot.mjs /path/ToyCar.glb /path/RobotExpressive.glb`
reproduces preparation and rejects unexpected source hashes. Original source
files are downloaded separately; the application never downloads them at runtime.
The catalog is deliberately small and remains inside the existing 8 MB asset /
40 MB project limits. Asset quality does not establish a heavy-game performance
guarantee; qualify on target hardware, especially alongside local inference.

Moonlight 1.3 uses the detailed car at Balanced/High quality. Low quality uses the
original procedural coupe; a failed model/texture load also falls back visibly.
The driving physics, three-lap rules and saved race data are unchanged.

**Pip's Adventure** expands the original small garden demo into two connected
areas: Home Garden and Woodland Trail. Find three copper parts, repair the bridge,
match the woodland lanterns to their carved directions, and restore the heart tree.
Six optional fireflies reward exploration. WASD/arrows move, Space jumps and E
interacts; touch controls, a north-up map, pause, sound and graphics settings are
included. This is the first two-area chapter, not the full proposed campaign.

Progress uses Arcade's scoped save API: acknowledged saves survive restart,
Continue restores paused, and checkpoint respawn retains puzzle/collection state.
New adventure requires confirmation before replacing an existing save. Unknown
save formats/layouts are preserved until explicit reset. Exports without Arcade
remain playable without durable persistence. Old user remixes retain the original
immutable demo; the bundled starter keeps ID `pip-garden` and is now version 2.0.

The expansion is development-authored, not claimed as an autonomous builder result.
`generation.json` records only the original version-1 live-agent pilot and its hashes.
That pilot used the real harness/catalog and succeeded after one continuation to
split an oversized response into smaller modules. v5 builders retain that lesson.
`art-credits.json` records the built-in image-generation prompts for meadow/sky,
Kenney's CC0 nature assets, and the existing Quaternius character provenance.

Adventure lessons for future builder guidance: keep progression/collision rules
independent of rendering; give physical interaction props colliders and approachable
activation radii; inspect imported PBR defaults (leaves should not be metallic);
fix path winding rather than masking it with double-sided materials; offset crossing
surfaces to avoid z-fighting; snapshot saves before queuing and order reset after
earlier writes. Do not overwrite an unknown save or auto-resume a moving character.
Run `npm run test:adventure` for the actual Electron journey and recovery checks.

**Skybound Carrier** is a finite 1,100-meter run with lanes, jump, slide, collectibles,
three chances, pause/restart, optional sound and low/balanced/high graphics.
Code and geometry/audio are original MIT-licensed work; generated sky artwork is
documented separately in the included credits. `build-assets.mjs` reproduces its
animated courier GLB and PCM WAV sounds. Browser-side glowing sprites are drawn
locally. `config.json` controls distance, speed, seed, lives and environment colors.
It is a stylized 3D starter, not an AAA-generation or NVIDIA performance claim.

**Moonlight Rally** is a free-steering coastal time trial with two selectable courses:
Neon Harbor (769 m) and Sunset Coast (951 m, with elevation). WASD or arrow keys
drive, Space holds the handbrake; pointer controls are available on screen. Brake
before corners, pass all 12 ordered checkpoints on each lap, and complete a
three-lap race for a medal. The HUD shows lap number and total race time; the menu
shows gold/silver targets and your personal best. Lap splits, medal and improvement
over the previous best appear after finishing.
Pause, focus-loss protection, reset-to-road, sound and three graphics settings are
included. Personal-best times persist per course/race length; unfinished races autosave roughly
every two seconds and on pause. **Continue saved race** restores in pause until you
resume; **New race** replaces that course's checkpoint without deleting best scores.
At the finish, the competitive clock stops immediately and the car coasts along
the road for four seconds before results appear. That presentation-only coast-down
does not add time, checkpoints or collisions. There is no steering assistance
during racing, opponents or online leaderboard. Everything is
original procedural geometry/audio. Cover images
are actual renders of the game. `courses/*.json` controls layout, palette and medal
times; `physics.js` is independent of Three.js for deterministic tests and remixes.
Version 1.2 extends the material/storefront treatment to both courses, adds rounded
island silhouettes, shoreline detail, a harbor observation wheel and coastal chapel,
and improves wheels, reflective glass, body detail and rear aero. Pooled tire smoke,
skid audio and checkpoint/lap feedback supplement the existing controls. Low graphics
and reduced-motion settings omit smoke. This remains stylized, not photorealistic.

One-lap saves from 1.1 remain playable as explicitly labeled one-lap races. They
keep their original record board. New three-lap races use separate version-2 saves
and record boards; old times are never compared against three-lap times. A resumed
legacy race is checkpointed into the new format without changing its race length.

Reusable racing lessons: separate competitive completion from celebration; commit
the result at the finish line, freeze the timer, and let the presentation settle.
Version scoreboards when rules change. Keep completed lap splits in the checkpoint.
For reproducible visual QA, pause before expensive screenshot encoding rather than
leaving steering input held while a test controller cannot react. These examples
complement the bundled game-building skill without changing existing v4 agents.

## Scores and saved progress

Arcade stores player progress in `library.sqlite`, separately from game source and
immutable revisions. Before upgrading an existing catalog to schema 4, it creates
a consistent `library-before-progress-v4-<id>.sqlite` backup in the data directory.
Older app builds cannot open schema 4; keep that backup if you need to roll back.

Games opt in through `window.arcadeSave`. Existing games that do not call the API
do not acquire progress automatically. For example:

```js
const save = await window.arcadeSave.load("campaign", 1);
// Validate save.value, show Continue, and restore paused.
await window.arcadeSave.save("campaign", 1, { level: 2, inventory: ["key"] });
await window.arcadeSave.submitScore("time-trial-v1", 42.5, "lower");
```

The trusted parent supplies game identity over a per-frame MessagePort. Game code
cannot choose another game, invoke app actions, access SQLite, or choose paths.
The main process also checks IPC sender, preview identity and input bounds.
There are 16 slots / 1 MB per game, 256 KB per JSON save, and 64 score boards;
requests are rate-limited. Best scores only improve under their declared ordering.
The save format version is independent of the source revision. Mismatched versions
are rejected without modifying old data; explicit reset or a deliberate game-side
compatibility change is required. Builtin identity survives bundle updates; clones
and remixes get independent saves. Player data is not included in project ZIPs.

These are local convenience scores, not anti-cheat records. Abrupt exits can lose
progress since the last acknowledged checkpoint. Saves are not automatically cloud
synced. Exported games outside Arcade have no injected API and should remain playable
without it. API details and tested design lessons live in the bundled skill above;
v4 and v5 system instructions actually load that file, rather than merely linking it.

Existing single-file games and versions are preserved. Their old agent sessions
retain their original tools and conversation. **Clone** an older game to give it a
fresh multi-file agent; new games use the multi-file builder automatically.

## Data and security boundaries

Arcade uses its own OS application-data directory, independent of OpenWorker and
the starter apps. `ARCADE_DATA_DIR` can override it for testing.

```text
Arcade Studio/
  settings.json              Non-secret preferences and new-game root
  library.sqlite             Game IDs, pinned models and SDK session references
  harness/                   SDK-owned durable agent state
  games/<name>-<id>/
    game.json                Portable game metadata
    index.html               Legacy shortcut, or multi-file project information
    draft.json               Working copy, separate from the published game
    thumbnail.png            Capture of the app's game preview only
    revisions/<id>/
      index.html
      src/game.js
      styles/game.css
      assets/                Images, audio, GLB models and other game assets
      vendor/                Pinned libraries and licenses, when used
      arcade-project.json    Manifest of file paths and integrity hashes
```

**Settings → Storage** changes where new games go. Existing games do not move.
Missing folders, malformed data and unsupported schemas produce errors instead of
replacing data with an empty library. Exported games are ordinary active web content;
the isolated in-app preview protections do not follow it into another browser.

Arcade owns the catalog, artifacts and UI. The SDK owns model requests, sessions,
approvals, questions, cancellation, credential handling and recovery. App callbacks
are `project.list_files`, `read_file`, `write_file`, `delete_file`, and approval-gated
`project.publish`, plus the legacy `game.read_current`/`game.publish` pair. The game
root comes from the SDK's trusted session ID, never the game selected on screen.
Agent-supplied paths are validated relative paths within that game's draft.
Publish retries use durable effect IDs.

The harness uses `sandbox: "off"` here: it has only the scoped app callbacks and
the built-in question tool, with no general shell/file tools. This is **not OS
isolation**. Generated games run inside a sandboxed iframe with no Node/native
bridge, external network, child frames, popups or forms. Each revision has its own
preview origin. ES modules and fetch can read only that revision's files, not other
games or unpublished drafts. No generated server or fixed game port is started.
`localStorage` and `sessionStorage` are emulated in frame-local memory (bounded to
512K characters / 4096 keys each) for generated-game compatibility. They reset on
Restart/reopen and never access the app's or another game's persistent storage.
Limits are described above. Validation covers paths, bounds, entrypoint presence and integrity;
it does not prove the generated game is free of logic bugs. This does not impose per-game CPU or memory
quotas; a pathological game can still make its renderer unresponsive.

The one persistent preview capability is the scoped save/score MessagePort API
described above. The old in-memory storage shim has not been made persistent.

## Build a desktop bundle

For local iteration use `npm run dev`. To produce a convenient development app,
run `npm run package:dev` after the runtime has been built once. It outputs to
`release-dev/`, never publishes or notarizes, and on macOS requests only ad-hoc
signing (`identity: -`), not a Developer ID certificate. Close the older app before
opening the new build so the single-instance lock does not focus the old window.

```sh
npm run package      # Unpacked native application in release/
npm run dist:app     # macOS DMG or Windows installer
```

Build on the target OS/architecture. Production distribution requires your own
signing/notarization setup. For an unsigned development build, set
`CSC_IDENTITY_AUTO_DISCOVERY=false`. Do not distribute a development bundle as a
signed production release.

If Node itself runs under x64 emulation on Windows ARM, set
`ARCADE_TARGET_ARCH=arm64` and run `npm run package -- --arm64`. The runtime bundler
checks the helper's PE architecture against the explicitly selected Electron target.
This is not a cross-compiled Intel/x64 qualification.

## Tests

```sh
npm test                 # Catalog, path boundaries, failures and UI projection
npm run test:integration # Real Rust host + controlled HTTP/SSE model
npm run test:ui          # Actual Electron app, offline and model-driven flows
npm run test:starters    # Deterministic starter gameplay rules
npm run test:skybound    # Full delivery run, real assets, import/remix/ZIP
npm run test:rally       # Both complete courses with keyboard input, remix/ZIP
npm run test:progress    # Process-restart Continue, isolation, score and format checks
```

Build the runtime first. Tests use temporary state and a local deterministic model
fixture, not real credentials. `ARCADE_PACKAGED_APP` points the UI test at a packaged
executable. Screenshots are under ignored `test-results/`.

An optional live test is explicit and billable:

```sh
ARCADE_LIVE_ACCEPT=1 ARCADE_CREDENTIAL_FILE=/path/to/approved-model.env node test/live.mjs
```

Prefer supplying `ARCADE_CREDENTIAL_FILE` via your existing secret mechanism
instead of typing a key into shell history. `ARCADE_LIVE_MODEL` selects a model.
For Anthropic, also set `ARCADE_LIVE_PROVIDER=anthropic` and supply a credential file
containing `ANTHROPIC_API_KEY`. `node test/live-ui.mjs` plays the generated test game
from its saved evidence after a successful live test.
The live test preserves its synthetic game and evidence in a temporary directory.
See [Versions and compatibility](../../../docs/harness/guides/releases.md) for verified results and
remaining qualification gaps; fixture success is not a live-model claim.

## Move into its own repository

This folder contains the entire app. Its only source dependency outside the folder
is `../../typescript-sdk`, and the runtime build defaults to `../..` (harness root).
To extract it, replace the file SDK dependency with your distributed SDK package,
replace the `sdk` build script accordingly, and supply `OPENWORKER_RUNTIME` or
`OPENWORKER_SDK_ROOT`. No machine-specific source paths are part of the app.
