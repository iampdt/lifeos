# Development

## Prerequisites

- Python 3.12, Node.js 22 or later, Docker (for Postgres)
- A Notion internal integration connected to the Life OS pages, and optionally to a sandbox copy
  of the Tasks, Habits, Weekly and Monthly Progress databases for write tests
- A Gemini API key (optional: without it, workflows use their rule-based fallbacks)

## Setup

```bash
cp .env.example .env                     # then fill in the Notion and Gemini values
docker compose up -d db                  # Postgres 16 + pgvector on localhost:5433 (lifeos, lifeos_test)

cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.create_owner you@example.com   # prompts for a password

cd ../frontend
npm install
```

## Running

```bash
# terminal 1: API on :8000 (cookies over plain http need LIFEOS_COOKIE_SECURE=false)
cd backend && LIFEOS_COOKIE_SECURE=false .venv/bin/uvicorn app.api.main:app --reload --port 8000

# terminal 2: web app on :3000, proxying /api/* to the API
cd frontend && npm run dev
```

Or run the whole stack in containers: `docker compose --profile app up --build`.

To run the public demo locally instead (no Notion needed), create a database whose name contains
`demo`, migrate it, and start the API with `LIFEOS_DEMO_MODE=true`:

```bash
docker compose exec db psql -U lifeos -c "CREATE DATABASE lifeos_demo"
export DATABASE_URL=postgresql+asyncpg://lifeos:lifeos@localhost:5433/lifeos_demo
.venv/bin/alembic upgrade head
LIFEOS_DEMO_MODE=true LIFEOS_COOKIE_SECURE=false .venv/bin/uvicorn app.api.main:app --port 8000
```

Sign-in uses the password form unless Google is configured. To try Google sign-in locally, create
an OAuth client whose redirect URI is `http://localhost:3000/api/v1/auth/google/callback` and set
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `LIFEOS_PUBLIC_URL=http://localhost:3000`. Your
account is linked on first sign-in when its email matches your Google account.

### Upgrading an older development database

Migration 0005 gives every existing run and proposal to the owner account. A database that has
runs but no account (one used only through the bearer token) stops the upgrade with a hint:
create the account at revision 0004 first, then upgrade and set its password.

```bash
docker compose exec db psql -U lifeos -d lifeos \
  -c "INSERT INTO users (id, email) VALUES (gen_random_uuid(), 'you@example.com')"
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.create_owner you@example.com --reset
```

## Configuration

All settings come from environment variables, read from the repo-root `.env` in development.
`.env.example` lists and explains every variable. The important switches:

| Variable | Effect |
| --- | --- |
| `NOTION_PROFILE` | `live` uses the real workspace; `sandbox` uses the `NOTION_SANDBOX_*` copies |
| `LIFEOS_WRITES_ENABLED` | Off by default. When off, approvals are recorded but nothing is written |
| `LIFEOS_ENV` | `production` hides the API docs and forces Secure cookies |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `LIFEOS_PUBLIC_URL` | All three set: Google sign-in only; otherwise the password form |
| `NOTION_OAUTH_CLIENT_ID`, `NOTION_OAUTH_CLIENT_SECRET`, `LIFEOS_ENCRYPTION_KEY` | With `LIFEOS_PUBLIC_URL`: accounts can connect their own Notion. The tests cover the flow against a fake Notion; `NOTION_BASE_URL` points the app at a stand-in for a manual walkthrough |
| `LIFEOS_OPEN_SIGNUP` | Off by default. When on, new Google users get an account |

## Tests

```bash
cd backend
.venv/bin/python -m pytest                          # unit, database, workflow and API tests
LIFEOS_LIVE_TESTS=1 .venv/bin/python -m pytest -m live   # reads the real workspace, writes only to the sandbox
.venv/bin/ruff check . && .venv/bin/ruff format --check .

cd ../frontend
npm run lint && npm run typecheck && npm run build
```

Database tests use the `lifeos_test` database: the schema is rebuilt from the migrations once per
session, and tables are truncated before each test. Workflow tests run whole graphs against an
in-memory Notion fake (`tests/fake_notion.py`) and a scripted model (`app/llm/mock.py`).

No test writes to the real workspace. Live write tests check that every sandbox data-source ID
differs from the live one before writing.

## Scripts

| Command (from `backend/`) | Purpose |
| --- | --- |
| `python -m scripts.notion_check --profile live` | Read-only check: integration, schema drift, view queries, week/month keys |
| `python -m scripts.run_workflow evening_checkin --answer "…"` | Run a workflow from the terminal; it stops when it needs an answer you didn't give. Continue with `--resume RUN_ID --answer '{"approve": "all"}'`. `--writes` works only with `--profile sandbox` |
| `python -m scripts.create_owner EMAIL [--reset]` | Create the owner account (the one that uses the workspace in `.env`; there is at most one), or set a new password for an account (signs out every session) |
| `python -m scripts.smoke_test --api URL [--app URL --chat]` | Read-only checks against a deployment (needs `LIFEOS_API_TOKEN`) |
| `python -m evals.run [--models …] [--suites …]` | Evaluation set against real models; writes `docs/evals/results.md` ([method](evals/README.md)) |

## Conventions

- **Migrations:** change `app/db/models.py`, add a revision in `alembic/versions/`, and check
  that `alembic check` reports no differences.
- **Notion fields:** code uses logical names from `config/notion_mapping.yaml`, never Notion
  display names.
- **New write actions:** add a payload model and plan in `execution/actions.py`, extend the
  allowlist in `permissions/policy.py`, and cover the executor path with a test.
- **Formatting:** `ruff` (pinned in `requirements-dev.txt`), line length 100.
- **Frontend:** Next.js 16 changes several APIs. Read the guide bundled at
  `frontend/node_modules/next/dist/docs/` before relying on older patterns.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Signed in, but every request is 401 locally | The cookie is `Secure` and the app runs on http: set `LIFEOS_COOKIE_SECURE=false` |
| `notion_check` sees no data sources | The integration isn't connected to the pages yet (Notion page → Connections) |
| Commands vanish mid-script in zsh | A loop variable named `path` overwrites `$PATH` in zsh; use another name |
| Chat answers arrive all at once | A proxy is buffering the stream; the API sends `Cache-Control: no-transform` for this |
