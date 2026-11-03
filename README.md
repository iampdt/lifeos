# LifeOS

A chief of staff for a personal productivity system in Notion.

**[Try the live demo →](https://lifeos-demo-rho.vercel.app)** No account needed: it runs the
real application on a sample workspace. The first visit can take up to a minute while the free
server wakes up.

LifeOS reads the workspace and runs its routines: an evening check-in that turns a free-text
reply into updates, a morning briefing, and weekly and monthly reviews. It answers questions in
a chat, including from the Notion pages you choose to index, with citations. Everything it
wants to change becomes a **proposal**. Nothing is written to Notion until it's approved (or,
for a few kinds you choose, applied with an undo), and every write is checked by reading it
back and recorded in an append-only audit log.

## How it works

```
 Browser ──► Next.js ── /api/* ──► FastAPI ──► Notion API (source of truth)
                                     │  ├────► Gemini (structured output, embeddings)
 GitHub Actions ── signed cron ──────┘  └────► Postgres + pgvector (proposals, runs, audit,
                                                 knowledge index, memory)
```

- **Workflows are explicit graphs** (LangGraph). Loading data and applying rules is plain code;
  the model only parses free text, prioritises and summarises, and every answer is validated
  against a schema and the rules (for example, a daily limit on major tasks).
- **The model proposes, code executes.** Approved proposals go through a deterministic executor:
  kill switch, field allowlist, stale detection, find-or-create, read-after-write verification,
  audit.
- **Runs can wait.** A check-in pauses for the owner's reply and again for approval, checkpointed
  in Postgres, and resumes hours later.
- **Degrades without a model.** If every model fails, planning falls back to "P0, then P1,
  then earliest Due", reviews are built from the numbers alone, and questions get the closest
  passages instead of an answer.
- **Any Notion layout.** Each account maps its own databases and properties; only a tasks
  database is required, and the routines use whatever else is mapped.

## Features

- Evening check-in with a nightly plan for tomorrow
- Morning briefing that keeps each day within its limit of major tasks
- Weekly review: metrics, a drafted review, a decision for every overdue task, Inbox triage,
  where each goal stands with a next step for goals that have none, reading pace
- Monthly review that closes the month and prepares the next
- Chat that answers from the workspace and proposes changes
- Knowledge index: ask the Notion pages you choose; hybrid search (words and meaning, pgvector)
  and answers that cite the passages they use
- Memory: preferences LifeOS keeps in mind when it plans, reviews and chats
- Approvals with before → after for every change; per kind of change: ask first, suggest only,
  off, or automatic with undo
- Pages for tasks, goals and reading, day-by-day history, reviews and usage (runs, AI calls,
  latency, tokens)
- Google sign-in, a Notion connection per account (OAuth, encrypted tokens), setup from any
  layout, and a free-tier AI allowance or the account's own Gemini key
- Scheduled routines in each account's timezone through signed calls from GitHub Actions

## Stack

| Layer | Technology |
| --- | --- |
| API | Python 3.12, FastAPI, Pydantic, SQLAlchemy (async), Alembic, LangGraph, `google-genai` |
| Data | PostgreSQL with pgvector (Neon in production), the Notion API |
| Web | Next.js 16, React 19, TypeScript, Tailwind CSS 4, shadcn/ui, TanStack Query |
| Operations | Docker, Render, Vercel, GitHub Actions |

## Getting started

```bash
cp .env.example .env            # Notion token and data-source IDs, Gemini key
docker compose up -d db
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head && .venv/bin/python -m scripts.create_owner you@example.com
LIFEOS_COOKIE_SECURE=false .venv/bin/uvicorn app.api.main:app --reload --port 8000
cd ../frontend && npm install && npm run dev        # http://localhost:3000
```

Details: [docs/development.md](docs/development.md). Deployment: [docs/deployment.md](docs/deployment.md).

## Repository

```
backend/    FastAPI app, workflows, agents, Notion integration, tests (see backend/README.md)
frontend/   Next.js web app (see frontend/README.md)
docs/       architecture, decisions, API, development, deployment, roadmap, build log
.github/    CI and scheduled jobs
render.yaml Render Blueprint for the API
```

## Documentation

| Document | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | System design: Notion integration, rules, workflows, approvals, LLM layer, data model, security |
| [Decisions](docs/decisions.md) | Why it's built this way, with the alternatives considered |
| [API](docs/api.md) | Endpoints, authentication, run and proposal shapes, chat events, scheduled jobs |
| [Development](docs/development.md) | Setup, configuration, tests, scripts, conventions |
| [Deployment](docs/deployment.md) | Free-tier deployment and operations |
| [Evaluation](docs/evals/README.md) | Accuracy of the model-backed agents per model, including prompt-injection cases |
| [Multi-user design](docs/multi-user.md) | Phase 9: Google and Notion sign-in, generic workspaces, free-tier AI and scheduling for many users |
| [Starter template](docs/template.md) | A Notion layout LifeOS maps completely on its own |
| [Roadmap](docs/roadmap.md) | Phases and status |
| [Build log](docs/build-log/) | What each phase built and how it was verified |

## Status

LifeOS runs in production on free tiers (Vercel, Render, Neon) against a real Notion workspace,
with a public demo on a sample workspace. Multi-user support is built; opening sign-up waits on
the owner's Google and Notion setup ([deployment](docs/deployment.md) §10). The model-backed
agents are measured by an [evaluation set](docs/evals/README.md), including prompt-injection
cases. What's done and what's next: [roadmap](docs/roadmap.md).
