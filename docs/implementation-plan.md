# Implementation status

## Current components

- `config.py` reads only `SKYRIM_GAME_DIR` and `SKYRIM_SAVES_DIR` from system variables or `.env`, with system values taking precedence. All cache, host and polling settings are internal. The server binds to `127.0.0.1:8000` and polls saves every 15 seconds.
- `catalog_import.py` structurally validates Deadmano's regular ESP and the compacted ESL-flagged Tweaks variant; optional known location patches adjust three positions when their overrides match. `catalog.py` loads the 108-row ignored export and checks FormID uniqueness, counters and finite coordinates.
- `form_ids.py`, `ess.py`, `progress.py` and `watcher.py` retain read-only ESS parsing and tri-state classification. They resolve each save's own full or light plugin table and track outdoor Tamriel player position. A newer bad save never replaces the last valid snapshot.
- `mapgen.py`, `enhanced_terrain.py` and `projection.py` generate and serve the sole Enhanced Terrain V2 basemap from official local archives. Generation is explicit. The frontend consumes the manifest's projection and uses bundled Leaflet, local tile URLs, filters, geographic labels and save polling.
- `landmarks.py` reads 24 curated vanilla `Skyrim.esm` map-marker coordinates into ignored local JSON. It does not consult MO2 or apply unknown marker overrides.

## Validation completed

- Full generation from the stock-game official archives: 1,024 terrain tiles, 1,024 water tiles, 8,192-square overview, 496 nonempty water tiles. A second CLI invocation reused the cache.
- The imported plugin with the three validated location patches reproduced the previously validated 108 coordinate triples with zero mismatches.
- Both actual Deadmano ESPs imported as 108 matching EditorIDs with identical unpatched positions. Synthetic regular-ESP saves at two full indices and synthetic compacted saves at two light indices decode correctly. The original ESP has no authentic before/after ESS pair yet.
- The private 439/440 ESS pair still produced 22/86 and 23/85, with one Falkreath04 transition. Full Python, Node and real Chrome tests are recorded in [the release audit](public-release-audit.md).

## Release gates still open

1. Run the same setup on a separate clean Skyrim Special Edition installation with the compatible ESL Skyshards plugin placed in `Data`. This machine's stock-game archive directory may have been prepared by a modlist installer, even though the inputs are named official vanilla archives.
2. Test an authentic original-ESP before/after absorption save pair and a public user's compatible setup. Structural and synthetic full-plugin validation is complete; original-ESP save behavior still needs real evidence.
3. Evaluate broader mod and location-patch compatibility only with traceable plugin and save evidence. Unsupported variants must continue to report unknown progress or fail import safely.

The map generator is functional with the verified stock-game assets. The importer supports the two inspected Deadmano layouts, while broad clean-install tracking compatibility remains unverified. Do not describe the whole project as universally release-ready until the remaining gates are met.
