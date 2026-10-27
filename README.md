# Skyshard Atlas

An offline, read-only tracker for Deadmano's 108 Skyshards in Skyrim Special Edition. It reads the newest `.ess` save, validates the absorbed-shard FormList and counters, and shows collected, uncollected, or unknown shards on a world-aligned terrain map. It also shows a saved player position when the save confirms an outdoor Tamriel location.

## Requirements

- Windows, Python 3.14, and [uv](https://docs.astral.sh/uv/).
- Skyrim Special Edition with `Skyrim.esm`, `Skyrim - Textures7.bsa`, and `Skyrim - Meshes1.bsa` in the game's `Data` directory.
- Deadmano's original regular `Skyshards.esp` or the ESL-flagged, FormID-compacted **Skyshards - Tweaks** variant. The importer checks their shard, quest, script and ledger structures. Vicn's Skyshards Framework is not supported. See [compatibility details](docs/portable-assets.md).
- A directory of Skyrim Special Edition `.ess` saves. A matching `.skse` co-save is optional for this tracker.

Game, mod, and map assets are **not** included. You must obtain and install them under their own terms. The MIT license applies to original Skyshard Atlas code; bundled Leaflet has its own [BSD 2-Clause notice](src/skyshard_tracker/static/vendor/leaflet/LICENSE).

## Install and configure

```powershell
git clone https://github.com/wordedword75049/skyrim-skyshards-atlas.git
cd skyrim-skyshards-atlas
uv sync
Copy-Item .env.example .env
```

Edit `.env` with **two paths**:

```dotenv
SKYRIM_GAME_DIR=C:\Games\Skyrim Special Edition
SKYRIM_SAVES_DIR=C:\Users\Username\Documents\My Games\Skyrim Special Edition\Saves
```

`SKYRIM_GAME_DIR` is the folder containing `Data`; `SKYRIM_SAVES_DIR` contains `.ess` files. System environment variables with these names override `.env`. MO2 users can set `SKYRIM_SAVES_DIR` to their profile's saves directory. `.env` is ignored by Git. No MO2 discovery or load-order changes occur.

## Build and run

```powershell
uv run skyshard-tracker build-map
uv run skyshard-tracker
```

The first command extracts 1,024 Tamriel LOD4 diffuse/normal/mesh triplets from **official local game archives** into a temporary ignored directory, generates terrain and water imagery, and builds 24 vanilla landmark positions from `Skyrim.esm`. It may take a few minutes and needs several gigabytes of free project-disk space during generation. Subsequent runs skip a valid map with matching archive hashes. To rebuild explicitly:

```powershell
uv run skyshard-tracker build-map --force
```

Map generation is never triggered by starting the server or opening the page. Finished assets stay in ignored `assets/local/`. An interrupted build leaves the previous map active.

If a compatible `Skyshards.esp` is in the configured game's `Data` directory, startup creates the local 108-shard catalog automatically when none is imported. An explicitly imported catalog remains selected. If a mod manager keeps the effective plugin elsewhere, import that file explicitly:

```powershell
uv run skyshard-tracker import-plugin "C:\path\to\Skyshards.esp"
```

The importer discovers FormIDs from the supplied ESP and checks all 108 shard EditorIDs and references against the activator base, helper quest aliases, map-marker list, absorption script properties, FormList and four counters. Users of the three validated location patches may add a repeatable `--patch "C:\path\to\patch.esp"` for each enabled patch. Those patches were validated against the compacted variant and must override one imported shard each. No imported ESP is copied into the repository. A changed plugin in `Data` is revalidated automatically; a changed manually imported plugin or patch requires reimport.

Open <http://127.0.0.1:8000/>. The service binds to loopback only. `GET /api/progress`, `POST /api/refresh`, `GET /api/health`, `GET /api/map`, and `GET /api/landmarks` are available locally. Press Ctrl+C to stop.

## Tracking behavior

The backend checks the newest stable `.ess` every 15 seconds, including overwritten quicksaves. The page polls the local API without moving the map or resetting filters. It shows save identity and a warning if a newer save cannot be parsed. Each save's own full or light plugin table maps imported local FormIDs to runtime IDs; no Nolvus load-order index is assumed. An absent shard is marked **uncollected only when** all 108 identities, the absorbed FormList, both activated counters, and both total counters agree. Otherwise unresolved shards remain **unknown**. A full/light mismatch also remains unknown. A missing or unstable `.skse` co-save is diagnostic; the progress ledger is in the `.ess` file.

The player marker appears only when the save verifies a current outdoor Tamriel position. Interior and other-worldspace positions are hidden. Map projection is exact for the world grid: `pixel_x=(world_x+262144)/64`, `pixel_y=(262144-world_y)/64` in the 8,192-pixel overview. The map has 512-pixel regional tiles, a separate water layer, and vanilla city and landmark labels.

## Tests

```powershell
uv run --group test python -m unittest discover -s tests -t . -v
node --test tests/*.test.mjs
```

The publishable suite builds synthetic ESS files for both full and light load-order mappings and tests parsing, classification, configuration, cache safety and the local API. Structural integration tests run when both ignored Deadmano ESPs are available. Real archive integration tests run when `SKYRIM_GAME_DIR` points to a game installation. Historical tests using real private saves are retained locally under ignored `research/tmp/private_tests/` and are not distributed. Optional Chrome smoke test: `uv run --group test --with playwright python -m tests.browser_smoke` (requires Chrome and local private fixtures). See [release audit](docs/public-release-audit.md) for what was actually tested.

## Limits and troubleshooting

- **Unsupported plugin:** import an original Deadmano ESP or structurally compatible Tweaks variant. A similar filename or mod version alone does not establish compatibility; the importer reports the missing structural relationship.
- **Original ESP validation limit:** its structure and script properties match the supported ledger, and synthetic regular-ESP saves parse at multiple full indices. No real original-ESP before/after save pair has been tested yet.
- **Clean-install limit:** end-to-end setup has not been tested on a separate clean Steam Skyrim installation.
- **Broader mod compatibility:** other Skyshards implementations, arbitrary ESP variants, and unvalidated location patches are not supported or verified.
- **Map unavailable:** run `build-map`; confirm the two named vanilla BSAs and `Skyrim.esm` exist in `Data`. Use `--force` if the ignored cache is incomplete.
- **Unknown progress:** check the warning shown by `/api/progress`; missing or inconsistent saved ledger/counters are not treated as zero collected.
- **No player marker:** interior cells, other worldspaces, stale saves, and unsupported location fields intentionally hide it.
- **Modded geography:** the map and landmark overlay use official game assets. Third-party terrain and map-marker overrides are not applied automatically. Validated Skyshard location patches can be imported explicitly.
- **Offline use:** install Python dependencies once; the running service, Leaflet assets, map imagery, and APIs use no remote service.

The tracker opens game archives, plugins, and saves for reading only. It writes solely to ignored project-local cache and temporary test directories. It never edits the game installation, saves, MO2 profile, or load order. [Map sources and constraints](docs/portable-assets.md) and [implementation status](docs/implementation-plan.md) provide further detail.
