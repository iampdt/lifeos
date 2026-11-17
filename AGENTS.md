# AGENTS.md

Working notes for anyone (or any coding agent) changing LifeOS. The product docs are in
`docs/`; read `docs/architecture.md` before changing behaviour.

## What it is

An AI chief of staff over a Notion workspace: FastAPI + LangGraph + Postgres backend
(`backend/`), Next.js frontend (`frontend/`, which has its own `AGENTS.md`). The model proposes
changes; deterministic code applies them, after approval, and verifies them.

## Layout

| Path | Holds |
| --- | --- |
| `backend/app/api` | Routes (FastAPI) |
| `backend/app/workflows`, `agents` | LangGraph workflows (check-in, briefing, reviews) and the model-facing agents |
| `backend/app/execution`, `permissions` | Proposals, the executor, the action allowlist |
| `backend/app/notion` | Notion client, OAuth, connections |
| `backend/app/llm` | Model router (chains, hedging, budget), Gemini, the Tollgate gateway, per-account access |
| `backend/app/knowledge`, `workspaces`, `services`, `domain` | Knowledge index, workspace mapping, scorecards, rules |
| `backend/alembic` | Migrations |
| `backend/tests`, `backend/evals` | Tests (Postgres needed for `tests/db`) and model evaluations |
| `docs/` | architecture, decisions (D/P/I records), api, development, deployment, roadmap, build log |
| `ops/keep-awake` | Cloudflare Worker that keeps the Render API awake |

## Commands

```sh
docker compose up -d db                       # Postgres + pgvector on :5433 (lifeos, lifeos_test)
cd backend && .venv/bin/pytest -q              # all backend tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m evals.run --pause 5       # model evals (spends free-tier quota)
cd frontend && npm run lint && npm run typecheck && npm run build
```

Setup is in `docs/development.md`; settings are listed in `.env.example` (the real `.env` is at
the repo root and git-ignored).

## Shipping

Pushing to `main` deploys: Render builds the API on every commit (`autoDeployTrigger: commit`)
and Vercel builds both web projects. There is no CI, so run every command above first.

## Rules that keep it safe

- Notion is the source of truth. Every write is a proposal; the executor re-reads, applies only
  allowlisted actions and verifies. `LIFEOS_WRITES_ENABLED` is the kill switch (set it in
  `render.yaml`, not the dashboard).
- Try writes on the sandbox profile first. Never write to the live workspace in tests.
- Model output is structured only: every reply is validated against a Pydantic schema, and free
  text never drives an action.
- The Tollgate gateway (`tollgate:` chain entries) is for the owner and the demo. Other accounts
  stay on Gemini, the provider their AI terms name (`accounts_router`).
- Schema changes are new Alembic revisions; never edit one that has run in production.
- Record decisions in `docs/decisions.md` (the next implementation record follows the last `I`
  number) and keep code comments that cite "architecture §N" in sync.
- Docs are the project's own engineering docs: neutral voice, current, no scratch notes.

## Gotchas

- DB tests truncate tables in `lifeos_test`.
- `next start` buffers server-sent events unless the response sets `Cache-Control: no-transform`.
- The UI uses shadcn on Base UI: components take `render=`, not `asChild`.
- In zsh, don't name a loop variable `path`.
- Don't copy secrets between projects; each project has its own keys.
