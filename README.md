# LifeOS

A personal AI chief of staff that runs on top of an existing Notion productivity system.

Notion stays the source of truth. LifeOS reads it, prepares briefings and reviews, and
proposes changes. Nothing is written back to Notion until the owner approves it, and every
write is verified and logged.

**Status:** Phase 4a done (2026-11-01): approval and execution pipeline, with real writes verified on a
sandbox copy of the workspace. Phase 4b (LLM workflows and API) is next. LifeOS is the second flagship
portfolio project.

## Documents

| Document | What it covers |
| --- | --- |
| [docs/01-architecture.md](docs/01-architecture.md) | System design: architecture, Notion adapter, agents, approvals, data model, API contracts, security, deployment |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases, exit criteria and estimates |
| [docs/progress/phase-3.md](docs/progress/phase-3.md) | Phase 3 results: what was built and how it was verified against the live workspace |
| [docs/progress/phase-4a.md](docs/progress/phase-4a.md) | Phase 4a results: proposals, approvals, executor, audit log, sandbox writes |
| `docs/private/` | Workspace discovery report (personal data; git-ignored) |

## Planned stack

FastAPI · Pydantic · SQLAlchemy · PostgreSQL (Neon, pgvector) · LangGraph · Gemini (Groq fallback, mock for tests) ·
Next.js · TypeScript · Tailwind · shadcn/ui · Docker · Vercel + Render free tiers.

## Running the backend locally

```bash
docker compose up -d db                                  # Postgres 16 + pgvector on localhost:5433
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head                           # create the tables
.venv/bin/python -m pytest                              # unit tests, no network
LIFEOS_LIVE_TESTS=1 .venv/bin/python -m pytest tests/live  # live: reads Life OS, writes only to the sandbox
.venv/bin/python -m scripts.notion_check --profile live   # or --profile sandbox
```

Configuration comes from the repo-root `.env` (see `.env.example`).
