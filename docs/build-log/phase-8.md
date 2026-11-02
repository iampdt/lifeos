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

## Production deployment (2026-11-02)

Neon (Singapore, Postgres 16), Render (free Docker service, Singapore) and Vercel, set up as in
[deployment.md](../deployment.md). The API starts in production mode against the live workspace
with writes off.

| Check (`scripts/smoke_test.py --chat`) | Result |
| --- | --- |
| API reachable, auth required, API docs hidden | ok |
| Database ready, model configured | ok |
| Notion schemas (tasks, goals, habits, weeks, months, books) | all match |
| Tasks readable from the live workspace | ok |
| Web app serves and proxies `/api` to the API | ok |
| Chat streams through Vercel's rewrite | ok: `run` 0.2 s, `tool` 1.5 s, answer 2.8 s |
| Scheduled jobs: GitHub Actions → signed call → API (`expire_proposals`, run by hand) | ok: `{"job":"expire_proposals","status":"done"}` |
| Owner sign-in on the deployed web app | ok |

Issues found on the way:

1. The first Render build failed: with `rootDir: backend`, the Dockerfile path and build context
   are resolved inside `backend/`, so `./backend` pointed at `backend/backend`. Fixed in
   `render.yaml`.
2. Vercel detects the backend and the frontend and offers to group them; only the frontend is
   imported, as a single project.
3. The smoke test crashed when the bearer token was wrong instead of reporting it; it now stops
   with a clear message, and the API ignores whitespace around configured secrets.

## Decisions

I16–I23 in [decisions.md](../decisions.md): HMAC scheme, per-day idempotency, migrations at
start, database URL forms, production mode, deploys gated on CI, streaming through Vercel,
whitespace in pasted secrets.

## Next

Writes were switched on in production on 2026-11-02 (`LIFEOS_WRITES_ENABLED` in `render.yaml`),
after the smoke test and a token rotation. Next: Phase 5.
