# Portable asset pipeline and compatibility

## Input classification

| Input | Origin | Runtime role |
| --- | --- | --- |
| `Data/Skyrim - Textures7.bsa` | Official Skyrim Special Edition archive | 1,024 Tamriel LOD4 diffuse DDS files and 1,024 normal DDS files |
| `Data/Skyrim - Meshes1.bsa` | Official Skyrim Special Edition archive | 1,024 matching Tamriel LOD4 BTR land/water meshes |
| `Data/Skyrim.esm` | Official Skyrim Special Edition plugin | 24 curated map-marker references |
| `Skyshards.esp` | Third-party Deadmano original or structurally compatible ESL-flagged Tweaks variant | 108 shard references, FormList and global identities |
| Three optional validated location patches | Third-party mod patches | One shard position override each |
| Code, site names and projection constants | This project | Extracts, checks and renders local output |

The prior Enhanced Terrain V2 used Nolvus-generated `LODGEN - Ultra` files and three winning `Solitude Ground Fix` resources. The current generator reads **none** of those. It reads specific members of the official BSAs with `sse-bsa`; it never extracts whole archives or writes to `Data`.

## Verified build

On 2026-10-27, a complete build ran against a local stock-game `Data` directory, without opening the Nolvus-generated LOD directory. Both archives contained the complete 1,024-triplet grid: cell origins `x=-64..60`, `y=-64..60` in steps of four cells. Their SHA-256 values were:

- `Skyrim - Textures7.bsa`: `1503de093d34ac3f933d553c378287e6c48cddc8a00f7de2490a38967c16edbb`
- `Skyrim - Meshes1.bsa`: `c69af326be8b6f0c9030e6f47fd1f1a480ae09c60463804b8e0447360459014b`
- `Skyrim.esm`: `06a9881f6ab277afd2a82e71f8a3719183b7031db4d9c78a7f40c08e1e4ffa91`

All 1,024 diffuse and 1,024 normal textures decoded as 256×256 DDS. The complete BTR set rendered successfully. Output: one 8,192×8,192 terrain overview, one matching water overview, and 1,024 512×512 detail tiles for each layer. The BTR-derived water mask is nonempty in 496 tiles. The completed preview visibly contained the Sea of Ghosts coast, major lakes, rivers, snow ranges and valleys. A real Chrome smoke test loaded the generated overview and regional tile requests locally. No generated imagery is included in Git.

This is a verified **stock-game archive build**, but a separate clean Steam installation without Nolvus has not yet been used for end-to-end validation. The generator rejects missing or incompatible members rather than falling back to an approximate image.

## Projection

The fixed 32×32 LOD4 grid covers `[-262144,262144]` Skyrim world units on both axes. North is up. The generator records `[1/64, 0, 4096, 0, -1/64, 4096]` in its manifest. The backend sends that same manifest projection to the frontend; shard, player and landmark positions use it. An overview pixel represents 64 world units; detail output is 32 world units per pixel. The web viewer performs no landmark fit or manual geographic shift.

Validation: the 1,024 archive tile names establish the ordered world grid; sampled BTR meshes decoded as terrain plus authored water shapes; the entire BTR set generated without layout errors. `Skyrim.esm` map-marker coordinates and all 108 imported shard coordinates project inside the raster. The known limit is **source detail**, not a fitted coordinate error: vanilla diffuse and normal tiles are 256 native pixels, and BTR water/land polygons are LOD4 approximations. A shoreline may differ from high-detail in-game geometry by more than one output pixel. Exact shoreline or road error against in-game survey points has not been measured, so no survey-grade claim is made.

## Build and cache integrity

`skyshard_tracker.mapgen` fingerprints both complete source archives, extracts only named members into an ignored staging directory, renders all outputs there, verifies the completed manifest and 2,048 tile files, and moves the completed generation under `assets/local/map/vanilla/builds/`. The `current.json` pointer changes atomically only after validation. The prior working generation remains selected if extraction or rendering fails. `build-map --force` makes a new generation. Ordinary app startup only reads a validated pointer and never generates imagery.

The separate 24-place landmark export records the SHA-256 of `Skyrim.esm` and its generator version. A matching valid export is reused; a rebuild writes to a temporary file and replaces the export atomically. `build-map --force` regenerates it.

A real forced rebuild from the same archives produced 2,051 output files with the same aggregate SHA-256 as the first build: `366e7ddb0e1b8dad28d044b0d1a67d18a6a75b4354893ff90e57242fad6c44a7`. This confirms byte-for-byte deterministic output for the tested environment and dependency versions.

The BTR water shape defines where water may exist; terrain triangles above the water plane are clipped out. This is geographically grounded LOD water, including rivers and lakes where the official mesh contains them. Narrow streams missing from LOD4 cannot be recreated precisely. Roads are only those baked into the diffuse tiles; no unverified road vectors are drawn.

## Skyshard catalog boundary

The importer accepts the original regular Deadmano ESP and the ESL-flagged Tweaks ESP by structure. The actual local files inspected were ignored `research/inputs/Skyshards.esp` (SHA-256 `24F1F5CDFAE1CCBD291006537A3E76B6D4EEDA4692EF9FCE7947FBDDB6B44006`) and `research/inputs/Skyshards-loaded.esp` (SHA-256 `0616B20B9DE2F110D2F9641AF79A4DD2D1F356E3EF07B0AF7CBE29BE39FBB671`). Their hashes matched the corresponding installed original and Tweaks mod copies. Both have only `Skyrim.esm` as a master, but TES4 flags are `00000000` and `00000200` respectively. Their 108 shard EditorIDs and unpatched XYZ positions agree exactly; every shard and ledger FormID differs. For example, Falkreath04 is `0102C037` in the original and `01000881` in Tweaks. The absorbed FormList is `01017CB3` versus `0100080A`.

The structural validator requires the exact 108 Deadmano EditorID set and hold counts, a single `DMN_Skyshard` ACTI base, exactly 108 matching REFR bases and Tamriel world groups, one `DMN_SkyshardsAbsorb` attachment on every shard, its activated-counter object property, complete helper quest aliases, all 108 map-marker references and FormList members, an empty static absorbed FormList, four named globals with expected initial values, the activator script's property link to that FormList, and the quest data script's links to all four globals. Any missing, duplicate or changed relationship is rejected. Hashes are recorded for diagnostics and cache invalidation, not used as a compatibility allowlist. The plugin itself is never copied. The addon marker plugin is not needed.

Plugin record IDs use a master-relative high byte. Import converts self-owned records to 24-bit local IDs for regular ESPs or 12-bit IDs for ESL-flagged ESPs. Each ESS is parsed with its own full and light plugin tables; runtime IDs are `full_index << 24 | local24` or `0xFE000000 | light_index << 12 | local12`. ESS FormID-array and ChangeForm lookup use those runtime IDs. A full/light mismatch, incomplete list, missing 108 IDs or counter disagreement produces unknown states, never a zero-collected assumption.

The compiled original Papyrus behavior adds an activated shard to `DMN_SkyshardsAbsorbedList`; both observed ESPs bind the same scripts and structural ledger links. Real Nolvus ESL saves 439/440 confirm 22→23 collected, with Falkreath04 as the only transition. The original regular ESP has **not** been validated with an authentic before/after ESS pair; full-plugin save mapping is currently exercised with synthetic ESS fixtures built from the real original catalog. Do not infer real original-save compatibility beyond that evidence. Vicn's Skyshards Framework has a different state mechanism and is unsupported.

Optional location patches remain limited to the three previously validated files and must override one reference in the imported catalog. Their FormIDs match the compacted variant; importing them against an incompatible original layout is rejected. Import any enabled location patches explicitly. No patch, ESP, save or generated map is committed.

Bethesda archives, mod plugins, extracted DDS/BTR resources, generated WebP maps, screenshots, and saves remain excluded from Git and any distribution. The MIT license covers original tracker code only.
