# Skyshard Atlas

<img width="958" height="453" alt="image" src="https://github.com/user-attachments/assets/5be3cb9f-1ff7-454d-ac9c-259cf37d94da" />


Skyshard Atlas is a local, read-only map and save tracker for the 108 collectibles in Deadmano's Skyshards mod for Skyrim Special Edition. It shows collected, uncollected, and unknown shards on a terrain map built from your installed game assets. It can also show your last saved outdoor position in Skyrim.

The application runs on your PC at <http://127.0.0.1:8000/>. It does not connect to Skyrim's live memory or require an internet connection during use.

## Requirements

- Windows, Python 3.14, [uv](https://docs.astral.sh/uv/), and a local Skyrim Special Edition installation.
- The game's `Data` folder must contain `Skyrim.esm`, `Skyrim - Textures7.bsa`, and `Skyrim - Meshes1.bsa` to build the map.
- Deadmano's original `Skyshards.esp` or a structurally compatible ESL-flagged, FormID-compacted **Skyshards - Tweaks** variant. Vicn's Skyshards Framework is not supported.
- A directory containing Skyrim Special Edition `.ess` saves. SKSE co-saves are optional for tracking shards.

Skyrim, Skyshards, and their assets are not included in this repository.

## Quick Start

1. Install Skyrim Special Edition and a supported Deadmano Skyshards mod. Enable the mod in your game setup.
2. Install Python 3.14 and uv, then clone and configure Atlas:

   ```powershell
   git clone https://github.com/wordedword75049/skyrim-skyshards-atlas.git
   cd skyrim-skyshards-atlas
   uv sync
   Copy-Item .env.example .env
   ```

3. Edit `.env` so `SKYRIM_GAME_DIR` points to the game folder containing `Data`, and `SKYRIM_SAVES_DIR` points to the folder containing your `.ess` saves:

   ```dotenv
   SKYRIM_GAME_DIR=C:\Games\Skyrim Special Edition
   SKYRIM_SAVES_DIR=C:\Users\Username\Documents\My Games\Skyrim Special Edition\Saves
   ```

4. Generate the map from your local game archives:

   ```powershell
   uv run skyshard-tracker build-map
   ```

   This normally runs only once. Generated images stay in the ignored `assets/local/` cache. Starting the app does not regenerate the map.

5. If Atlas cannot find the effective `Skyshards.esp` in the configured game's `Data` folder, import it from its actual location:

   ```powershell
   uv run skyshard-tracker import-plugin "C:\path\to\Skyshards.esp"
   ```

6. Start Atlas and open <http://127.0.0.1:8000/>:

   ```powershell
   uv run skyshard-tracker
   ```

Press Ctrl+C to stop the server.

## Basic Gameplay Workflow

1. Start Skyrim with Deadmano Skyshards enabled and create or load a character.
2. Save the game, then open Atlas to see the 108 shard locations and the collection states supported by that save.
3. Collect a Skyshard in Skyrim. After the absorption animation and game updates finish, save again.
4. Atlas detects the new or overwritten save and refreshes the counts and markers automatically. Continue exploring and saving normally.

Atlas reads saved game data, not live game memory. Collection progress and the player marker reflect the latest **successfully parsed save**; without a new save, they cannot update. An initial save may not yet contain the absorbed-shard ledger. Incomplete or inconsistent evidence appears as **unknown**, never as an assumed uncollected shard. If a newer save fails to parse, Atlas keeps the last valid snapshot and shows a stale-data warning. Atlas never modifies Skyrim, installs mods, or changes saves.

## Configuration and Commands

The two required settings are `SKYRIM_GAME_DIR` and `SKYRIM_SAVES_DIR`. They can be set in `.env` or as system environment variables; system values take precedence. Mod-manager users can point `SKYRIM_SAVES_DIR` to a profile-specific saves folder. `.env` is ignored by Git.

| Command | Purpose |
| --- | --- |
| `uv run skyshard-tracker build-map` | Build local terrain, water, and landmark assets; skip a valid existing map. |
| `uv run skyshard-tracker build-map --force` | Rebuild the map cache. |
| `uv run skyshard-tracker import-plugin "C:\path\to\Skyshards.esp"` | Select and validate an ESP outside game `Data`. |
| `uv run skyshard-tracker` | Start the local web application. |

With no imported catalog, Atlas discovers a compatible `Data\Skyshards.esp` automatically. A manual import takes precedence while its source file remains present and unchanged, even when a different plugin exists in physical `Data`; this supports mod managers with virtual game folders. If the selected source or a selected location patch is moved, deleted, or changed, startup stops with a reimport instruction instead of using stale data or guessing which plugin is active. Reimport the **effective** ESP to recover. A changed automatically discovered `Data` plugin is structurally revalidated and reimported. Do not import an inactive plugin: the imported full or light layout must match the plugin used by the save.

The importer checks all 108 shard references, their activator, helper quest aliases, marker list, absorption script links, FormList, and four counters. It resolves regular and light-plugin FormIDs using each save's own plugin tables. Three specifically validated location patches may be supplied with repeatable `--patch "C:\path\to\patch.esp"` arguments during import; arbitrary patches are not accepted.

Local API endpoints are `GET /api/progress`, `POST /api/refresh`, `GET /api/health`, `GET /api/map`, and `GET /api/landmarks`. The save watcher checks for stable new and overwritten `.ess` files every 15 seconds by default. The page refreshes data without moving your map view or resetting filters.

## Compatibility and Limitations

- ESS parsing supports the verified combination of **header version 12, form version 78, and LZ4 compression**. Other combinations return an explicit unsupported-format error.
- The original regular Deadmano ESP has been validated end to end on a clean Steam Skyrim SE installation, including local map generation and authentic before/after absorption saves. The compacted Tweaks ESP has also been validated with real saves. Other Skyshards implementations and arbitrary modded geography are outside the verified scope.
- The map uses official local terrain archives and landmark records. Third-party terrain, water, and marker overrides are not automatically applied. Narrow streams and some roads may be less detailed than the game world.
- The player marker appears only when the save verifies a current outdoor Tamriel location using the player reference and Skyrim master identity. Interior cells, other worldspaces, and uncertain location data hide it. Localized save-header location names are retained as metadata but do not determine the map position.

## Troubleshooting

- **Map missing:** confirm the named game archives and `Skyrim.esm` exist under `SKYRIM_GAME_DIR\Data`, then run `build-map`. Use `--force` if a valid cache needs rebuilding.
- **Plugin missing, changed, or conflicting:** run `import-plugin` with the ESP actually active in your game. Do not use an obsolete copy left in `Data` by a mod manager.
- **All or some statuses unknown:** save after Skyshards initializes; inspect the warnings in Atlas or `/api/progress`. A missing ledger, mismatched counters, or a full/light plugin mismatch cannot safely be treated as zero collected.
- **Progress has not updated:** save again after the collection animation finishes, then use the Refresh button or wait for the next poll. Atlas reports the save it is currently showing and warns about newer unreadable saves.
- **No player marker:** the latest verified save may be indoors, in another worldspace, or lack reliable location evidence.

Atlas opens game archives, plugins, and saves for reading only. It writes generated assets and its imported catalog inside ignored project-local directories. Runtime map tiles, Leaflet files, and API calls are served locally.

## Credits and Licenses

Skyshards is Deadmano's mod; Skyrim and its game assets belong to Bethesda. The application generates map imagery locally from the user's installation and does not redistribute game or mod assets. Original Skyshard Atlas code is [MIT licensed](LICENSE). The bundled Leaflet library retains its [BSD 2-Clause license](src/skyshard_tracker/static/vendor/leaflet/LICENSE).
