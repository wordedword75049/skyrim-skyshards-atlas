# Initial private repository audit

Audit date: 2026-10-28. This records the safety checks before Skyshard Atlas's first Git commit. It does not claim that a separate clean Skyrim installation or original-ESP save pair has been tested.

## Repository and license

`origin` targets `https://github.com/wordedword75049/skyrim-skyshards-atlas.git`. An authenticated GitHub API query confirmed `wordedword75049/skyrim-skyshards-atlas` is **private** and its default branch is `main`. `git ls-remote --heads --tags origin` returned no refs before the first push. No remote history needs replacement.

The MIT `LICENSE` covers original Skyshard Atlas code and names “Skyshard Atlas contributors.” Bundled Leaflet 1.9.4 retains its BSD 2-Clause notice at `src/skyshard_tracker/static/vendor/leaflet/LICENSE`. Bethesda, Deadmano, Nolvus and other game or mod assets are neither included nor relicensed.

## Candidate-file review

The publishable set consists of Python and browser source, tests, documentation, `.env.example`, `.gitignore`, `pyproject.toml`, `uv.lock`, the MIT license, and Leaflet's licensed JavaScript, CSS and five small PNG interface icons. The five PNGs were opened as PNG files and have Leaflet icon dimensions. No game-derived image is among them.

Every Git-visible candidate was checked for forbidden game/save suffixes, unexpected binary content, personal usernames and absolute paths, and common credential or private-key signatures. Configuration examples use placeholders. The full staged set must be reviewed again before committing; `.gitignore` alone is not sufficient.

`.gitignore` excludes `.env`, `research/`, `assets/local/`, virtual environments, caches, screenshots, logs, generated map images, plugins, archives, scripts and saves. The private original and compacted ESPs, ESS/SKSE pairs, research outputs, extracted scripts, terrain tiles and browser screenshots remain in ignored local directories. The application reads game and save inputs without writing to them.

## Verification for the initial private push

- Public Python suite: 30 tests run; 27 passed and three optional vanilla-asset integration checks skipped without a configured game directory.
- Private regression suite: 17 tests run; 16 passed and one optional live outdoor/interior/outdoor location test skipped. Real Nolvus saves 439 and 440 remain 22→23 collected, with Falkreath04 the only transition.
- JavaScript suite: 10 passed.
- Headless Chrome smoke: one passed with local terrain and water imagery, 108 markers and an automatic 22→23 save update.
- Source-only checkout simulation: 30 public Python tests ran from only Git-visible files; 20 passed and ten private/asset integration tests skipped as expected. Package imports resolved from the copied source tree.
- Offline package build: `uv build --offline` produced a wheel (34 members) and source distribution (50 members). Their member lists contain the Leaflet license and no research material, saves, plugins, game archives, local map assets, `.env`, virtual environment or cache.
- CLI entry points: `skyshard-tracker --help`, `build-map --help` and `import-plugin --help` succeeded. No full map regeneration was performed for this audit.

Structural import tests use both ignored real Deadmano ESP layouts. Synthetic ESS tests cover multiple full and light plugin indices. No authentic before/after ESS pair from the original regular ESP is available.

## Later public-release validation

1. Test an authentic before/after absorption ESS pair made with the original un-compacted ESP.
2. Run the full setup on a separate clean Steam Skyrim Special Edition installation.
3. Evaluate broader mod variants and location patches with matching plugin and save evidence.

These are future compatibility checks, not blockers for the first **private** repository push. Do not describe the project as universally compatible with all Skyshards implementations.

Suggested GitHub Topics for the private repository: `skyrim-special-edition`, `skyshards`, `python`, `fastapi`, `offline`, `map`. Topics are metadata recommendations; none are set by this audit.
