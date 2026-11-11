# Text Interactive Game

[![ci](https://github.com/bd-lo/text-interactive-game/actions/workflows/ci.yml/badge.svg)](https://github.com/bd-lo/text-interactive-game/actions/workflows/ci.yml)

A **text-adventure single-player game** in a web/terminal UI: you type natural-language actions, an LLM agent
interprets intent and writes the prose, while a deterministic rules engine owns every state change.
Default mode is **fully offline** — no API key, no Node/pnpm, no network after the first dependency sync.

Current version: **`0.3.0`** (2026-11-11, player launcher & exit experience: double-click launcher,
in-game quit, idle auto-exit). Last tagged release: `v0.2.0`.
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

### Player-uploaded scenarios (Scenario JSON v1)

The story is not hard-coded: upload your own scenario JSON and start a new game.

- Web: **Scenarios** page (`/scenarios`) → import/paste JSON → validation preview (errors carry JSON paths)
  → import → "start with this scenario". The Home scenario dropdown lists the 4 built-in scenarios plus
  uploaded ones; the game page shows a scenario badge.
- Format: `schema_version/slug/meta/player/rooms/items/npcs/goal/endings/suggestions`; conditions use a
  whitelist DSL (`in_room/has_item/has_item_tag/npc_state/hp_lte/hp_gte` + `all/any/not/always`, no eval).
  See [docs/design/user-scenario-modules.md](docs/design/user-scenario-modules.md).
- Examples: [examples/scenarios/tiny-tower.json](examples/scenarios/tiny-tower.json) (2 rooms),
  [examples/scenarios/ember-watch.json](examples/scenarios/ember-watch.json) (5 rooms, dialogue, hazard,
  two endings). Entity IDs are deterministic (`sha256(scenario_id|kind|key)`), goals/endings are evaluated
  at read time — no new event types, no `GameState` changes, old goldens unaffected.

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

### Other

- Character cards: JSON import (`/cards/import`) and LLM/template generation (`/cards/generate`);
  offline generation produces a clearly labelled deterministic template draft.
- Web polish: a self-built **Aurora Glass** dual theme (light default / dark, top-bar toggle, optional
  follow-system), aurora/glass layers + typewriter, and a 3-level effects setting (respects
  `prefers-reduced-motion`); zero new runtime dependencies, no UI component library, 375px layout,
  no horizontal overflow.
- HTTP API for integration: `POST /api/v1/games` → `POST /api/v1/turns` → `GET /api/v1/games/{id}`,
  `/view`, `/replay`, plus `/character-cards`, `/world-books`, `/scenarios`, `/settings`; interactive docs
  at `/api/docs`. Write requests require `Idempotency-Key`; business failures are `200 + TurnResult(ok=false)`.

## Release & distribution

- Version `0.3.0`; version number lives in `pyproject.toml` and `textgame.__version__`; see
  [CHANGELOG.md](CHANGELOG.md). Release checklist, wheel smoke tests and `git tag` / release-notes flow:
  [RELEASE.md](RELEASE.md). The last released snapshot is the annotated tag **`v0.2.0`** (commit
  `0e52814`, CI run `43032265398` green, 2026-11-11); `0.3.0` is pushed to `main`, with the tag / GitHub
  Release / PyPI still pending a maintainer decision.
- Distribution: Windows double-click launcher (`启动游戏.bat` → `scripts/play.ps1 -Launcher`),
  clone + `play` script (prebuilt `web/dist`) or the Python wheel (embeds the web UI). The release
  checklist in [RELEASE.md](RELEASE.md) includes the launcher smoke test
  (`pwsh -File scripts/play.ps1 -LauncherCheck`).
  Docker images are **not provided** for now (single-user SQLite + static SPA; rationale and a draft
  Dockerfile for later in [docs/design/deployment-and-distribution.md](docs/design/deployment-and-distribution.md)).
  Publishing to PyPI is on hold pending a maintainer decision.
- Roadmap: post-M6 tracks (content / experience / features / release experience) are defined in chapter 17
  §17.9 of the development docs and [the roadmap ADR](docs/decisions/2026-11-11-post-m6-roadmap.md).
  The launcher batch (N5 057) is documented in [ADR D-LA-1..N6](docs/decisions/2026-11-11-player-launcher-and-exit.md).

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

Tech stack: Python 3.10+ (runtime 3.12) · Pydantic v2 · FastAPI · SQLAlchemy 2.0 + SQLite + Alembic
(0001–0006) · Vue 3 + TypeScript + Vite + Pinia (zero new runtime deps) · ruff / mypy strict /
import-linter / pytest (+ golden replays) / Vitest / GitHub Actions (Python 3.10+3.12 matrix, license &
vulnerability gates, SBOM, web-build with browser e2e).

Contributing guide (Chinese): [CONTRIBUTING.md](CONTRIBUTING.md). Security policy: [SECURITY.md](SECURITY.md).

## Known limitations

- Default offline mode is template narration; real LLM narration is optional and requires a key.
- Real-LLM latency is non-streaming for now (first turn can take several seconds); streaming is scoped as
  the next experience track (chapter 17 §17.9, N2).
- Scenario JSON v1 does not support custom recipes/crafting chains, combat, scripts/expressions, flag/quest
  conditions, a scenario editor or a share marketplace yet (see chapter 17 §17.9).
- World books: `vectorized`/`outlet`/`characterFilter`/`useGroupScoring` unsupported by design for now.
- Single-user, local-only: no accounts, no cloud saves, don't expose the port to the internet.
- The GUI launcher window is Windows-only (WinForms); other platforms use the console launcher.
  There is no packaged installer/`.exe` yet: [uv](https://github.com/astral-sh/uv) is still required
  (the launcher explains how to install it, but never installs anything by itself).

## License

MIT — see [LICENSE](LICENSE).
