# LifeOS API

FastAPI service with the Notion integration, LangGraph workflows, specialists, the approval
pipeline and the executor. Design: [../docs/architecture.md](../docs/architecture.md).

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.api.main:app --reload --port 8000
.venv/bin/python -m pytest
```

Setup, tests and scripts: [../docs/development.md](../docs/development.md).

## Layout

```
app/
  api/          routes, request guard (session + CSRF or bearer), signed cron endpoint, errors
  auth/         passwords (argon2id), sessions, sign-in rate limiter
  workflows/    evening.py, morning.py, weekly.py (graphs), runner.py, proposing.py, common.py
  agents/       planner, checkin, reviewer, chat (prompt + output schema + validation)
  execution/    actions (kinds, payloads, write plans), proposals (lifecycle), executor, audit
  permissions/  field allowlist and permission modes
  services/     rules (R1–R12), calendar, periods, prompt context
  notion/       client, codecs, encoders, mapping, schema drift, queries, repositories
  llm/          provider protocol, Gemini, mock, router, factory
  db/           models, engine and database URLs
  core/         settings
config/         notion_mapping.yaml (logical names → Notion properties and options)
alembic/        migrations
scripts/        notion_check, run_workflow, create_owner, smoke_test, start.sh
tests/          unit tests; db/ (Postgres); live/ (real Notion, opt-in)
evals/          evaluation set (cases/), scorer and runner for real models
```
