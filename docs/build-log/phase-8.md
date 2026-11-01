# Phase 8 (minimal) — Deployment

Date: 2026-11-02.

## What was built

| Piece | Purpose |
| --- | --- |
| `backend/Dockerfile`, `backend/scripts/start.sh` | API image: Python 3.12 slim, non-root user, migrations on start, uvicorn on `$PORT` with proxy headers |
| `frontend/Dockerfile` | Web app as a standalone Node server (local stack and self-hosting; Vercel builds natively) |
| `docker-compose.yml` (`app` profile) | The whole stack in containers: Postgres, API, web app |
| `render.yaml` | Render Blueprint: Docker web service on the free plan, health check, deploys only after CI passes, secrets entered once or generated |
| `.github/workflows/ci.yml` | Backend lint and tests against Postgres with pgvector; frontend lint, type check and build |
| `.github/workflows/cron.yml` | Scheduled jobs: wakes the API, signs each call, retries safely; skipped until its secrets exist |
| `app/api/cron.py`, migration `0003` | `POST /internal/cron/{job}` with HMAC-SHA256 signatures and a 5-minute window; `cron_runs` allows one run per job per local day; a failed run releases its slot |
| `app/db/session.py` | Accepts Neon's `postgresql://…?sslmode=require&channel_binding=require` URLs and derives the asyncpg and psycopg forms |
| `LIFEOS_ENV=production` | Hides the OpenAPI schema and docs; Secure cookies always |
| `scripts/smoke_test.py` | Read-only checks against a deployment, including streaming through the proxy |
| `docs/deployment.md` | Step-by-step setup and operations runbook |

## Verification

| Check | Result |
| --- | --- |
| Backend tests | **154 passed** (new: cron signatures, stale and cross-job signatures rejected, once per day, failed-run retry, disabled without a secret, production hides docs, URL conversion) |
| `alembic check` | Models and migrations match |
| Images | Both build; the API container starts, runs the migrations and serves |
| Smoke test against the containers (production mode, sandbox workspace, a plain `postgresql://` URL) | All checks pass: auth required, database ready, model configured, API docs hidden, every Notion schema matches, the web app serves and proxies `/api`, chat streams through the standalone Next.js server (`run` 0.0 s, `tool` 1.9 s, answer 3.9 s) |
| The cron workflow's shell step, run against the containers | Signature accepted; `expire_proposals` done; `morning_briefing` started a run; the repeat returned `already_ran`; a wrong secret got 401 |

## Decisions

I16–I21 in [decisions.md](../decisions.md): HMAC scheme, per-day idempotency, migrations at
start, database URL forms, production mode, deploys gated on CI.

## Next

The first cloud deployment follows [deployment.md](../deployment.md); the smoke test's `--chat`
check confirms streaming through Vercel's rewrite. After that: Phase 5.
