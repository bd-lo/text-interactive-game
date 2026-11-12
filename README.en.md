# Text Interactive Game

[![ci](https://github.com/bd-lo/text-interactive-game/actions/workflows/ci.yml/badge.svg)](https://github.com/bd-lo/text-interactive-game/actions/workflows/ci.yml)

A **text-adventure single-player game** in a web/terminal UI: you type natural-language actions, an LLM agent
interprets intent and writes the prose, while a deterministic rules engine owns every state change.
Default mode is **fully offline** — no API key, no Node/pnpm, no network after the first dependency sync.

Current version: **`0.3.0`** (2026-11-11, player launcher & exit experience: double-click launcher,
in-game quit, idle auto-exit). Last tagged release: `v0.2.0`.

**N-series batch (2026-11-12) is merged into `main` but not released yet**: scenario JSON **v2** conditions
(whitelist predicates + flags + multi-ending priority, v1 fully compatible, no scripts/expressions), scenario
**single-file share export** (`GET /api/v1/scenarios/{id}/export`), **sample content pack** with one-command
quick start, **streaming narrative phase 1** (`POST /api/v1/turns/stream` NDJSON + in-game incremental display,
fallback-once, rollback switch), **web accessibility** (keyboard/focus/aria/reduced-motion/WCAG 2.1 AA
contrast fixes), terminal **card-library browsing**, **bulk scenario import/export**, **save export/import**
(`GET /api/v1/games/{id}/export` + `POST /api/v1/saves/import`), and N4/N5 evaluations/tooling (controlled m3
re-baseline tool, long-session performance baseline, Playwright re-evaluation → keep dependency-free CDP
(D-N4-2), portable-package evaluation → not implemented this round (D-N5-1)). The version stays `0.3.0`;
tagging/releasing `0.4.0` (tag / GitHub Release / PyPI) is pending a maintainer decision.

Player-facing changes: [CHANGELOG.md](CHANGELOG.md).

> Full documentation is in Chinese: [README.md](README.md) · [docs/开发文档.md](docs/开发文档.md) ·
> [docs/PROGRESS.md](docs/PROGRESS.md) · [RELEASE.md](RELEASE.md) (release checklist & tag flow)

## Quick start (one command)

Prerequisite: [uv](https://github.com/astral-sh/uv) (Python 3.12 is downloaded and managed by uv).
The repository ships a prebuilt web bundle (`web/dist`), so **Node/pnpm is not needed to play**.

**Windows players (recommended): double-click `启动游戏.bat` in the repository root.** A small
launcher window starts the game and opens your browser at `http://127.0.0.1:8000`. Exit with the
in-game **「退出游戏」** button, by closing the launcher window, or double-click `退出游戏.bat` as a
fallback (`退出游戏.bat 8001` for a non-default port); closing only the browser tab triggers an idle
auto-exit after 5 minutes. On Linux/macOS or without a GUI, `./scripts/play.sh --launcher` falls back to a
console launcher. Launcher log: `data/logs/launcher-server.log`. Headless launcher check:
`pwsh -File scripts/play.ps1 -LauncherCheck` / `./scripts/play.sh --launcher-check`.

```powershell
git clone https://github.com/bd-lo/text-interactive-game.git
cd text-interactive-game
.\scripts\play.ps1            # opens http://127.0.0.1:8000 in your browser
```

```bash
git clone https://github.com/bd-lo/text-interactive-game.git
cd text-interactive-game
./scripts/play.sh             # http://127.0.0.1:8000
```

Options: `-Terminal` / `--terminal` (terminal UI), `-Port 8001` / `--port 8001`,
`-Check` / `--check` (start server, assert `/healthz` and `/` return 200, exit),
`-Launcher` / `--launcher` (launcher window + browser, console fallback without a GUI),
`-LauncherCheck` / `--launcher-check` (headless launcher control-plane check),
`-Llm` / `--llm` (real LLM narration; needs `TEXTGAME_LLM_API_KEY` in `.env`).

Terminal-only (no server):

```bash
uv sync --frozen --no-dev
uv run --no-dev python -m textgame.console
```

Saves go to `data/textgame.db` (gitignored). Scenarios: `--new --scenario story` (the "Ember Watch"
story with goals/endings, recommended for a first run), `m2|m3|m4` (single scene / multi-actor
conflict / crafting). Player-uploaded scenarios: `--list-scenarios` and `--scenario-id <ID>`.

Demo (fixed-seed, non-interactive): `uv run python -m textgame.demo --check` (7 screens, byte-identical
across runs) and `uv run python -m textgame.console --check` (terminal smoke).

Sample content pack (2 scenarios + world book + character card, temporary DB + offline):
`.\scripts\quickstart-content-pack.ps1` (Windows) / `./scripts/quickstart-content-pack.sh` (Linux/macOS);
add `-Check` / `--check` for a headless self-test. Details in
[examples/content-pack/README.md](examples/content-pack/README.md).

## What this project is

- **Deterministic core, generative shell**: dice, damage, crafting and all state transitions are computed
  by the rules engine; LLM agents only parse intent and narrate. Every write goes through a single writer
  (`GameEngine`) and an event log; fixed-seed golden replays guard behavior.
- **Safety by construction**: tool whitelist + `actor_scope` visibility, JSON-Schema validated LLM output
  (invalid output never mutates state), secrets only from env/local credential file, socket-disabled tests,
  and **no execution of user-supplied content** (scenario JSON conditions are a whitelist predicate DSL).
- **Packaged for players**: `web/dist` is committed for one-command play and embedded in the Python wheel
  (`textgame/web_dist`), so `pip install` users get the web UI too.

## Features

### Player-uploaded scenarios (Scenario JSON v1 & v2)

The story is not hard-coded: upload your own scenario JSON and start a new game.

- Web: **Scenarios** page (`/scenarios`) → import/paste JSON → validation preview (errors carry JSON paths)
  → import → "start with this scenario". The Home scenario dropdown lists the 4 built-in scenarios plus
  uploaded ones; the game page shows a scenario badge.
- Format: `schema_version/slug/meta/player/rooms/items/npcs/goal/endings/suggestions`. **v1 stays fully
  compatible**; `schema_version: 2` adds whitelist conditions (flag-derived predicates plus `objective_done`,
  `has_item_tag_count`, `item_in_room`, `npc_in_room`, `turn_gte`, `world_time_gte`, `hp_lte`, combined with
  `all/any/not`) and multi-ending `priority` semantics. Conditions are evaluated server-side by the whitelist
  DSL — **no eval, no user scripts/expressions**. See
  [docs/design/scenario-dsl-v2.md](docs/design/scenario-dsl-v2.md) and
  [docs/design/user-scenario-modules.md](docs/design/user-scenario-modules.md).
- Examples: [examples/scenarios/](examples/scenarios/) ships 4 files — v1 `tiny-tower.json` (2 rooms) and
  `ember-watch.json` (5 rooms, dialogue, hazard, two endings); v2 `lantern-vigil.json` (flags/objective
  progress/multi-ending) and `moonlit-market.json` (shareable v2 sample). Entity IDs are deterministic
  (`sha256(scenario_id|kind|key)`), goals/endings are evaluated at read time — no new event types, no
  `GameState` changes, old goldens unaffected.
- **Sample content pack (one-command quick start)**: [examples/content-pack/](examples/content-pack/) bundles
  2 playable v2 scenarios (ash lighthouse / mirror well) + 1 world book + 1 character card.
  Run `.\scripts\quickstart-content-pack.ps1` (Windows) or `./scripts/quickstart-content-pack.sh`
  (Linux/macOS); `-Check` / `--check` runs a headless self-test with a temporary DB and offline mode.
- **Sharing = file exchange**: `GET /api/v1/scenarios/{scenario_id}/export` returns the original scenario
  JSON (re-importable as-is, `Content-Disposition: attachment; filename="<slug>.json"`, read-only, no
  internal DB fields); import reuses `POST /api/v1/scenarios` (no cloud/accounts/marketplace, D-N5).
- **Terminal bulk import/export**: `--export-scenarios <dir>` writes one `<slug>.json` per scenario
  (default 50, max 100), `--import-scenarios <dir>` imports a whole folder (per-file ≤256KB/UTF-8/object
  precheck, content-derived stable idempotency key so re-runs are skipped, per-file `[ok]/[skip]/[error]`
  report; exit code 1 on any failure). Web multi-file UI will follow in a later front-end batch
  ([docs/design/scenario-pack-batch.md](docs/design/scenario-pack-batch.md)).

### World books (SillyTavern-compatible lorebooks)

World books are a **read-only lore injection layer**: when keys match, entry text is handed to the narrator;
they never write game state, events, saves or replays.

- Imported formats (auto-detected, ≤256KB): SillyTavern World Info JSON (`{"entries": {...}}`, arrays or bare
  maps), Character Card V2 `data.character_book`, and this project's simplified schema.
- Trigger semantics (deterministic): `keys`/`keysecondary` + `selectiveLogic`, `constant`, `order`,
  `scanDepth`, recursion ≤2, budget 800 chars / max 8 entries, deterministic `probability`.
  Since the UC batch: **regex keys** (`/pattern/flags`, `i/m/s`), **timed effects**
  (`sticky/cooldown/delay`, simulated per committed turn), **inclusion groups** (`group/groupWeight/
  groupOverride`, at most one per group per turn) and `position/depth` ordering.
  Still unsupported: `vectorized`, `outlet`, `characterFilter`, `useGroupScoring` (kept in raw JSON with a
  warning — never silently dropped). Compatibility matrix:
  [docs/design/world-book-format-compat.md](docs/design/world-book-format-compat.md).
- The detail page shows the trigger configuration (probability, group, timed effects, position/depth,
  scan depth, recursion/case flags); "what is active this turn" is shown in the in-game World Lore panel.
- Bundled examples: [examples/world-books/story-lorebook.json](examples/world-books/story-lorebook.json)
  (29 tavern entries) and [story-character-card.json](examples/world-books/story-character-card.json)
  (Character Card V2 with an embedded book).

### Your own DeepSeek API key

- **Local development machine**: put `TEXTGAME_LLM_API_KEY` in `.env` (see [.env.example](.env.example));
  the dev key is used automatically. `TEXTGAME_LLM_ENABLED=false` forces offline forever.
- **Clean clone / player machine**: no `.env` → offline template narration (still fully playable); a
  dismissible "LLM not configured" banner leads to the **Settings** page where you paste your own
  DeepSeek API key. **Saved keys take effect on the next turn** (no restart); the page can test the
  connection (latency + human-readable failure), clear the key and shows the masked key/source.
- Storage: the key is kept only in the local, gitignored `data/llm.credentials.json` (path configurable).
  It is never written to the database, never logged and never echoed back unmasked.

### Streaming narration (optional, LLM configured)

With a LLM configured, the game page shows narration incrementally: the first words appear earlier instead of
waiting for the whole turn. Offline template narration keeps using the plain synchronous turn path.

- Protocol: `POST /api/v1/turns/stream` (NDJSON, one event per line: `start`/`delta`/`replace`/`done`/`error`);
  `done.result` is exactly the same `TurnResult` as `POST /api/v1/turns`, and the turn is still committed after
  it fully completes (client disconnect does not roll it back).
- Front-end behaviour: incremental rendering, authoritative correction on `done`; on failure/unsupported server
  it **falls back to the plain `/turns` call once** (manual retry only, no automatic retry loop); the stream read
  can be cancelled without leaving half-written prose.
- Rollback switch: `TEXTGAME_STREAM_ENABLED=false` makes the streaming endpoint return a 404 error envelope and
  the front-end uses synchronous turns; the default offline/template path does not need it.
- Phase-1 boundaries: no stream resume/reconnect, no automatic retry, streaming is not forced on by default.
  See [docs/design/narrative-streaming.md](docs/design/narrative-streaming.md) and progress docs 037–040.

### Save export / import (single-file backup and migration)

- `GET /api/v1/games/{game_id}/export` returns one JSON save package
  (`export_version`, `exported_at`, `game`, `events[]`, `snapshot{state_hash, version, last_event_seq, ...}`,
  `checksum`). Read-only: no DB/event/snapshot writes; unknown save → 404.
- `POST /api/v1/saves/import` (requires `Idempotency-Key`, body = the exported package) → 201
  `{game_id, turn, version, snapshot_version}`: validates package structure/checksum/event model and requires the
  `state_hash` of a **full event replay** to match the declared one (failures write nothing and return 422 with
  structured `violations`), then replays the events through the single writer into a **new game**; same
  key + same package replays the first response, same key + different package → 409; limits 8MB / 20 000 events.
- Safety: the package contains no API keys/credentials/logs, and `checksum` is an integrity check, **not a
  signature** (treat it as local backup/migration; evaluate before sharing).
  See [docs/design/save-export-import.md](docs/design/save-export-import.md). The web UI for it will follow in a
  later front-end batch; the terminal/API path is available now.

### Other

- Character cards: JSON import (`/cards/import`) and LLM/template generation (`/cards/generate`);
  offline generation produces a clearly labelled deterministic template draft. In the terminal,
  `--list-cards` browses the library (`--card-query` filters by title/source, `--card-limit` 1..100,
  `--card-all`) and `--card-detail <card-id>` shows a read-only safe subset (source/attributes/inventory/warnings).
- Web polish: a self-built **Aurora Glass** dual theme (light default / dark, top-bar toggle, optional
  follow-system), aurora/glass layers + typewriter, and a 3-level effects setting (respects
  `prefers-reduced-motion`); zero new runtime dependencies, no UI component library, 375px layout,
  no horizontal overflow.
- Accessibility (N2 041–042): skip link + main landmark, route/dialog focus management, full keyboard operation
  of the command box and panels, screen-reader live regions, `prefers-reduced-motion` static fallback, and
  light/dark contrast fixes checked against WCAG 2.1 AA (manual checklist + automated contrast assertions;
  axe-style automation is not a CI gate). See [docs/design/a11y-checklist.md](docs/design/a11y-checklist.md).
- HTTP API for integration: `POST /api/v1/games` → `POST /api/v1/turns` (or `POST /api/v1/turns/stream` when a
  LLM is configured) → `GET /api/v1/games/{id}` / `view` / `replay`; scenario share export
  `GET /api/v1/scenarios/{id}/export`; save export `GET /api/v1/games/{id}/export` and import
  `POST /api/v1/saves/import`; plus `/character-cards`, `/world-books`, `/scenarios`, `/settings`;
  interactive docs at `/api/docs`. Write requests require `Idempotency-Key`; business failures are
  `200 + TurnResult(ok=false)`.

## Release & distribution

- Version `0.3.0`; version number lives in `pyproject.toml` and `textgame.__version__`; see
  [CHANGELOG.md](CHANGELOG.md). Release checklist, wheel smoke tests and `git tag` / release-notes flow:
  [RELEASE.md](RELEASE.md). The last released snapshot is the annotated tag **`v0.2.0`** (commit
  `0e52814`, CI run `43032265398` green, 2026-11-11); `0.3.0` is pushed to `main`, with the tag / GitHub
  Release / PyPI still pending a maintainer decision. The N-series batch (2026-11-12) is merged into `main`
  with version still `0.3.0`; releasing `0.4.0` is pending a maintainer decision.
- Distribution: Windows double-click launcher (`启动游戏.bat` → `scripts/play.ps1 -Launcher`),
  clone + `play` script (prebuilt `web/dist`) or the Python wheel (embeds the web UI). The release
  checklist in [RELEASE.md](RELEASE.md) includes the launcher smoke test
  (`pwsh -File scripts/play.ps1 -LauncherCheck`).
  Docker images are **not provided** for now (single-user SQLite + static SPA; rationale and a draft
  Dockerfile for later in [docs/design/deployment-and-distribution.md](docs/design/deployment-and-distribution.md)).
  Portable packages / PyInstaller / installers were evaluated in N5-058 and are **not implemented** this round
  (decision D-N5-1). Publishing to PyPI is on hold pending a maintainer decision.
- Roadmap: post-M6 tracks (content / experience / features / release experience) are defined in chapter 17
  §17.9 of the development docs and [the roadmap ADR](docs/decisions/2026-11-11-post-m6-roadmap.md).
  The launcher batch (N5 057) is documented in [ADR D-LA-1..N6](docs/decisions/2026-11-11-player-launcher-and-exit.md);
  the Playwright re-evaluation (keep dependency-free CDP, D-N4-2) in
  [ADR D-N4-2](docs/decisions/2026-11-12-playwright-a11y-eval.md) and the portable-package evaluation in
  [ADR D-N5-1](docs/decisions/2026-11-12-portable-package-eval.md).

## Development

```bash
uv sync --frozen                 # full dev environment
uv run pytest -q                 # unit + integration + replay + security
uv run ruff format --check . && uv run ruff check .
uv run mypy src/textgame && uv run lint-imports
uv run python scripts/check_docs.py
pnpm --dir web install --frozen-lockfile && pnpm --dir web test
pnpm --dir web build             # rebuild web/dist (committed; CI checks zero drift)
```

Browser e2e (dependency-free CDP driver, real Edge/Chrome; start the server first):

```bash
uv run --no-dev uvicorn textgame.main:app --port 8794
node scripts/e2e-web.mjs --base-url http://127.0.0.1:8794 --out artifacts/e2e-web
```

> N4-055 re-evaluated the e2e tooling and keeps **dependency-free CDP only** (no Playwright CI browser
> matrix, D-N4-2); a11y automation (axe-style) is not a CI gate — manual checklist + Vitest assertions +
> two CDP steps (keyboard / reduced-motion) cover it. See
> [ADR D-N4-2](docs/decisions/2026-11-12-playwright-a11y-eval.md) and
> [docs/design/a11y-checklist.md](docs/design/a11y-checklist.md).

Maintainer tools (read-only or `.work/` only, never touch the production DB):

```bash
# Controlled m3 narrative re-baseline: dry-run by default (read-only rebuild + field diff; 0 diff = clean)
uv run python scripts/rebaseline_golden.py --golden m3 --dry-run --diff-report --out .work/n-batch/rebase-054.json
# Long-session performance baseline: 120 turns / 601 events, JSON metrics (exit 0 = within thresholds)
uv run python scripts/perf-long-session.py --turns 120 --out .work/n-batch/perf-056.json
```

Tech stack: Python 3.10+ (runtime 3.12) · Pydantic v2 · FastAPI · SQLAlchemy 2.0 + SQLite + Alembic
(0001–0006) · Vue 3 + TypeScript + Vite + Pinia (zero new runtime deps) · ruff / mypy strict /
import-linter / pytest (+ golden replays) / Vitest / GitHub Actions (Python 3.10+3.12 matrix, license &
vulnerability gates, SBOM, web-build with browser e2e).

Contributing guide (Chinese): [CONTRIBUTING.md](CONTRIBUTING.md). Security policy: [SECURITY.md](SECURITY.md).

## Known limitations

- Default offline mode is template narration; real LLM narration is optional and requires a key. When a LLM
  is configured the web game page tries streaming narration first and falls back to the synchronous turn path
  once on failure (rollback switch: `TEXTGAME_STREAM_ENABLED=false`); phase 1 has no stream resume/reconnect
  and no automatic retry.
- Scenario JSON (v1/v2) still does not support custom recipes/crafting chains, combat, scripts/expressions,
  a scenario **editor** (not delivered yet), reveal/history-type conditions (N1 033–034, not scheduled —
  requires maintainer sign-off) or a share marketplace. v2 conditions are limited to the documented whitelist.
- World books: `vectorized`/`outlet`/`characterFilter`/`useGroupScoring` unsupported by design for now.
- Save export/import: `checksum` is an integrity check, not a signature; it is intended for local
  backup/migration, not as an authenticated sharing format.
- Single-user, local-only: no accounts, no cloud saves, don't expose the port to the internet.
- The GUI launcher window is Windows-only (WinForms); other platforms use the console launcher.
  There is no packaged installer/`.exe` yet (N5-058 evaluated: not implemented this round, D-N5-1):
  [uv](https://github.com/astral-sh/uv) is still required (the launcher explains how to install it, but never
  installs anything by itself).

## License

MIT — see [LICENSE](LICENSE).
