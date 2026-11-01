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

## Configuration

All settings come from environment variables, read from the repo-root `.env` in development.
`.env.example` lists and explains every variable. The important switches:

| Variable | Effect |
| --- | --- |
| `NOTION_PROFILE` | `live` uses the real workspace; `sandbox` uses the `NOTION_SANDBOX_*` copies |
| `LIFEOS_WRITES_ENABLED` | Off by default. When off, approvals are recorded but nothing is written |
| `LIFEOS_ENV` | `production` hides the API docs and forces Secure cookies |

## Tests

```bash
cd backend
.venv/bin/python -m pytest                          # unit, database, workflow and API tests
LIFEOS_LIVE_TESTS=1 .venv/bin/python -m pytest -m live   # reads the real workspace, writes only to the sandbox
.venv/bin/ruff check . && .venv/bin/ruff format --check .

cd ../frontend
npm run lint && npx tsc --noEmit && npm run build
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
| `python -m scripts.create_owner EMAIL [--reset]` | Create the owner account or set a new password (signs out every session) |
| `python -m scripts.smoke_test --api URL [--app URL --chat]` | Read-only checks against a deployment (needs `LIFEOS_API_TOKEN`) |

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
