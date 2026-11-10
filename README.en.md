# Text Interactive Game (English summary)

A retro **2G text-MUD style single-player text game**: you type natural-language actions, an LLM
agent interprets intent and narrates, and a deterministic rules engine owns all state changes.
Default mode is **fully offline** — no API key, no Node/pnpm, no network after the first dependency sync.

> Full documentation is in Chinese: [README.md](README.md) · [docs/开发文档.md](docs/开发文档.md) · [docs/PROGRESS.md](docs/PROGRESS.md)

## Quick start (one command)

Prerequisite: [uv](https://github.com/astral-sh/uv) (Python 3.12 is downloaded by uv).
The repository ships a prebuilt web bundle (`web/dist`), so **Node/pnpm is not needed to play**.

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
`-Llm` / `--llm` (real LLM narration; needs `TEXTGAME_LLM_API_KEY` in `.env`).

Terminal-only (no server):

```bash
uv sync --frozen --no-dev
uv run --no-dev python -m textgame.console
```

Saves go to `data/textgame.db` (gitignored). Scenarios: `--scenario m2|m3|m4`
(single-player / multi-actor conflicts / crafting).

## What this project is

- **Deterministic core, generative shell**: dice, damage, crafting are computed by the rules engine;
  LLM agents only parse intent and narrate. Every write goes through a single writer (`GameEngine`)
  and an event log; fixed-seed golden replays guard behavior.
- **Safety by construction**: tool whitelist + `actor_scope` visibility, JSON-Schema validated LLM
  output (invalid output never mutates state), secrets only from env, socket-disabled tests.
- **Packaged for players**: `web/dist` is committed for one-command play, and embedded into the
  Python wheel (`textgame/web_dist`) so `pip install` users get the web UI too.

## Development

```bash
uv sync --frozen                 # full dev environment
uv run pytest -q                 # unit + integration + replay + security
uv run ruff format --check . && uv run ruff check .
uv run mypy src/textgame && uv run lint-imports
pnpm --dir web install --frozen-lockfile && pnpm --dir web build && pnpm --dir web test
```

Contributing guide (Chinese): [CONTRIBUTING.md](CONTRIBUTING.md). Security policy: [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
