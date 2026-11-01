# LifeOS — System Design (Phase 2)

Status: **approved by the owner on 2026-11-01**; all open questions answered (§19). Date: 2026-11-01.

This document turns the Phase 1 discovery into a buildable design. Workspace IDs and usage data
are in `docs/private/00-discovery.md` (git-ignored). Items marked **⚠️ verify** are assumptions
to confirm against live APIs in Phase 3.

---

## 1. Goals and non-goals

**Goals**

- Run the owner's *existing* Life OS loops — evening check-in, nightly plan, Sunday review,
  monthly career review — with less effort and better data.
- Read the whole Life OS workspace; write only through approved, typed, verified actions.
- Be honest about free-tier limits and degrade gracefully when the LLM or Notion is unavailable.
- Be a codebase the owner can defend line by line in an interview.

**Non-goals**

- Replacing Notion, copying all of Notion into Postgres, or adding new Notion databases.
- Autonomous changes. Nothing is written without approval in v1.
- Multi-user SaaS. One owner, one workspace.

## 2. Confirmed owner decisions

| Decision | Effect on the design |
| --- | --- |
| Scope is all of Life OS | Tasks, Goals, Habits, Weekly/Monthly Progress, Engineer OS DBs and the Reading system are in scope |
| Knowledge Base and Energy & Well-being dropped | No adapters for them |
| Weekly review targets 20–25 minutes | Review UI is pre-filled; the owner makes decisions, not data entry |
| Monthly review is career-only | Monthly workflow reads career Areas, Technical Learning, Career Evidence, Career Growth, the Switch Plan and the Job Switch Dashboard. Money Log and body check stay manual in Life OS |
| Free-tier LLM is acceptable for workspace data | Gemini free tier is the default provider |
| No backfill of `Completed` | Progress is attributed by `Due` (the owner's rule). LifeOS sets `Completed` only on tasks it marks Done from now on |
| LifeOS **is Flagship 2** and becomes a case study on the portfolio site | Phase 5 includes the Flagship 2 quality bar; §20 lists what must be captured from day one |
| "Major task" = Due that day, Status Next or Doing, not `Recurring` | Rule R1 |
| Carrying a task forward also moves its Week and Month relations | Rule R7; the target period pages are created first if missing |
| Monthly career review covers the calendar month | §8.4 |
| Write tests run against a sandbox | "LifeOS Sandbox (dev)" created 2026-11-01 (private page, outside Life OS) with empty copies of Tasks, Habits, Weekly and Monthly Progress |
| New habit rows are titled `dd/mm/yyyy` | `habit.upsert_day`; the Life OS page and the Habits description were updated to match |
| Sunday review text on Life OS updated to 20–25 minutes | Done 2026-11-01 |

## 3. Key design decisions

| # | Decision | Why | Rejected alternative |
| --- | --- | --- | --- |
| D1 | **Notion is the only store of productivity records.** Postgres holds only LifeOS-owned state: proposals, approvals, audit, runs, memory, schema snapshots, embeddings | No sync engine, no conflicts, no stale copies | Mirroring Notion into Postgres (sync bugs, two sources of truth) |
| D2 | **Modular monolith** (one FastAPI service) | One user, free tier, fastest to change | Microservices |
| D3 | **Workflows are explicit LangGraph graphs.** Data gathering and rule checks are plain code; the LLM is used only to prioritise, summarise and parse free text | Predictable, testable, cheap on free-tier quotas | Free-form multi-agent delegation |
| D4 | **The LLM proposes, code executes.** Agents can only emit typed `ActionProposal`s; a non-LLM executor applies approved ones | An LLM can never write to Notion directly | Giving agents write tools |
| D5 | **Official Notion API (version `2025-09-03`, data-source endpoints)** behind a `NotionGateway` interface | Supported, documented; an MCP adapter or a fake can implement the same interface | Calling MCP at runtime |
| D6 | **Thin LLM abstraction over official SDKs** (`google-genai`, `groq`), with a mock provider. LangGraph is used for graph state, checkpoints and interrupts only | Fewer dependencies, full control of retries/timeouts, trivial mocking | LangChain chat-model wrappers |
| D7 | **Backend-owned single-owner auth** (argon2id password, httpOnly session cookie); the frontend proxies `/api/*` to the backend so cookies stay first-party | `*.vercel.app` and `*.onrender.com` are different sites; cross-site cookies are increasingly blocked | Auth.js on the frontend plus token forwarding |
| D8 | **No in-process scheduler.** GitHub Actions cron calls `POST /internal/cron/{job}` (HMAC-signed); jobs are idempotent per (job, date) | Render free instances sleep after ~15 min idle, so in-process schedulers miss runs | APScheduler / Celery beat |
| D9 | **Week and month attribution comes from `Due`** (Mon–Sun weeks, Asia/Kolkata), not from the optional manual Week/Month relations | Matches the owner's rule "Due drives Week and Month keys"; works for unlinked tasks | Trusting the relations |

### How the six agents in the brief map onto this design

| Brief | In this design | LLM? |
| --- | --- | --- |
| Chief of Staff | The orchestrator: routes a request to a workflow graph, or runs the chat graph with read tools | Yes |
| Planning Agent | Specialist node in the morning, evening and nightly workflows | Yes |
| Review Agent | Specialist node in the weekly and monthly workflows | Yes |
| Goal Management Agent | Specialist node, Phase 5 (goals have little data today) | Yes |
| Learning Agent | Specialist node plus knowledge retrieval, Phase 5 (Technical Learning is empty today) | Yes |
| Notion Operations Agent | **Deterministic executor**, deliberately not an LLM | **No** |

A specialist is a function with its own prompt, input slice and Pydantic output schema. It makes
one structured LLM call, and its output is validated in code. Specialists never call each other.

## 4. System overview

```
 Browser ──► Next.js (Vercel)  ── /api/* rewrite ──►  FastAPI (Render, Docker)
                                                      │
          GitHub Actions cron ── HMAC POST ───────────┤
                                                      ├── api/            routers, auth, SSE
                                                      ├── workflows/      LangGraph graphs (+ checkpoints)
                                                      ├── agents/         specialists, prompts, output schemas
                                                      ├── execution/      proposal validation, executor, verifier
                                                      ├── permissions/    action policy, property allowlist
                                                      ├── services/       domain rules, metrics (pure code)
                                                      ├── notion/         gateway, client, codecs, repositories
                                                      ├── memory/         facts, knowledge index
                                                      ├── llm/            provider abstraction + fallback
                                                      └── repositories/   SQLAlchemy (Postgres)
                                                      │
                         Notion API ◄─────────────────┤ (3 req/s average)
                         Gemini / Groq ◄──────────────┤
                         Neon Postgres + pgvector ◄───┘
```

## 5. Backend layout

```
backend/
  app/
    api/            # FastAPI routers, dependencies, auth, error handlers
    workflows/      # morning_briefing.py, evening_checkin.py, weekly_review.py, monthly_career.py, chat.py
    agents/         # planning.py, review.py, goals.py, learning.py — prompt + schema + call
    execution/      # proposals.py (lifecycle), executor.py, verifier.py, idempotency.py
    permissions/    # policy.py (action kinds, modes), allowlist.py (data source × property × op)
    services/       # rules.py, metrics.py, calendar.py (IST weeks/months)
    notion/         # client.py, gateway.py, codecs.py, schema_registry.py, queries.py, repositories/
    memory/         # facts.py, knowledge/ (Phase 5)
    llm/            # base.py, gemini.py, groq.py, mock.py, router.py (fallback + budget)
    models/         # SQLAlchemy models
    schemas/        # Pydantic API + domain models
    repositories/   # Postgres data access
    core/           # config (pydantic-settings), logging, security, time
  config/
    notion_mapping.yaml          # logical names → data-source IDs and property names (example committed)
  tests/  alembic/
```

## 6. Notion integration layer

### 6.1 Client (`notion/client.py`)

- `httpx.AsyncClient` with `Notion-Version: 2025-09-03`, connect timeout 5 s, read timeout 20 s.
- **Rate limiting:** a token bucket at 2.5 req/s (below Notion's documented 3 req/s average per
  connection, which also has a workspace-wide limit).
- **Retries:** on `429` (honour `Retry-After`), `500/502/503/504` and network errors, use
  exponential backoff with full jitter, up to 5 attempts within a 60 s deadline. Never retry
  `400/401/403/404/409`.
- **Typed errors:** `NotionRateLimited`, `NotionNotShared` (object not shared with the
  integration), `NotionNotFound`, `NotionValidation`, `NotionConflict`, `NotionUnavailable`.
- **Pagination:** an async iterator over `start_cursor` / `next_cursor` with `page_size=100`.
- **Relations:** a page object may truncate long relation lists; use the page-property-item
  endpoint when a relation can exceed the inline limit. **⚠️ verify the current limit.**

### 6.2 Schema registry and drift detection (`notion/schema_registry.py`)

`config/notion_mapping.yaml` maps logical names to IDs and exact property names, for example:

```yaml
tasks:
  data_source_id: ${NOTION_DS_TASKS}
  properties:
    title: {name: "Task", type: title}
    status: {name: "Status", type: select, values: {inbox: "Inbox", next: "Next", doing: "Doing", waiting: "Waiting", done: "Done", cancelled: "Cancelled"}}
    due: {name: "Due", type: date}
    completed: {name: "Completed", type: date}
    priority: {name: "Priority", type: select}
    week_key: {name: "Week Key", type: formula, read_only: true}
```

On startup and on demand (`POST /notion/discover`), the registry fetches each data source, hashes
its schema and stores a snapshot. If a mapped property is missing or has changed type, that
repository goes **read-only** (fails closed) and the UI shows a drift warning. A renamed Notion
property is then a one-line config change, not a code hunt.

### 6.3 Codecs and repositories

- `codecs.py` encodes and decodes Notion property JSON (title, rich_text, select, multi_select,
  date, checkbox, number, relation; formula, rollup and created_time are read-only).
- One repository per data source returns **domain models**, for example
  `Task(id, title, status, priority, area, due, completed, goal_ids, week_ids, week_key, last_edited_time, url)`.
- `queries.py` rebuilds the owner's saved views in code, because the API queries data sources
  with filters but doesn't run saved views. Each builder names the view it mirrors:
  `active()`, `today(d)`, `overdue(d)`, `inbox()`, `next_7_days(d)`, `done_between(a, b)`.
- A per-request cache prevents re-reading the same page within one workflow run. There is no
  long-lived cache in v1.

### 6.4 Write path

1. The executor loads the approved proposal and re-reads the target page.
2. **Stale check:** Notion has no conditional writes. If the page's `last_edited_time` is newer
   than the proposal's snapshot *and* any target property differs from the snapshot, the
   proposal becomes `stale` and needs re-approval. A small race window remains, and is documented.
3. **Permission check:** every (data source, property, operation) must be in the allowlist (§9.3).
4. The write is either `PATCH` page properties or create a page in a data source.
5. **Read-after-write verification:** re-read the page and compare the intended values, giving
   `verified` or `mismatch`.
6. Append the result to the audit log.

The gateway has **no archive or delete method**. Cancelling is a status change, which needs approval.

## 7. Domain rules (`services/rules.py`)

Pure functions with unit tests. Each rule has an ID that appears in proposals and logs.

| ID | Rule | Enforcement |
| --- | --- | --- |
| R1 | Max 3 major tasks per day. A **major task** has Due = that day, Status Next or Doing, and `Recurring` unchecked | Planning output is rejected if it schedules more than 3. The briefing flags days with more than 3 and proposes moves |
| R2 | Active = Status not in {Done, Cancelled} | `queries.active()`; matches the Notion views |
| R3 | Completed tasks are never deleted | No delete code path; executor rejects anything else |
| R4 | Done tasks leave active views but stay in history | Views are reproduced in code; history = Status Done grouped by Due week/month |
| R5 | Done tasks count toward progress | Weekly metrics: *planned* = tasks with Due in the week and Status ≠ Inbox; *done* = Status Done; Cancelled reported separately |
| R6 | Unfinished tasks enter a deliberate carry-forward queue; never moved automatically | Overdue tasks appear only as weekly-review decisions: carry forward, reschedule or cancel |
| R7 | Moving a task forward changes Due **and moves its Week and Month relations to the new period** | `task.set_due` carries `week_page_id` and `month_page_id`; if the target Weekly/Monthly Progress page doesn't exist, a `week.create` / `month.create` proposal is added and must execute first (`depends_on`) |
| R8 | Due drives Week and Month keys | `calendar.py`: ISO Mon–Sun weeks (`YYYY-Www`), calendar months, Asia/Kolkata. Verified 2026-11-01: identical grouping to the Notion Week Key formula on all 49 dated tasks. The Notion Month Key formula returns a boolean and is ignored |
| R9 | Weekly review on Sunday, 20–25 min | Workflow pre-computes everything; target ≤ 15 decisions per review |
| R10 | Monthly review focuses on career and the switch | Monthly workflow scope (§8.4) |
| R11 | Inbox triaged in the weekly review | Each Inbox item: plan (Status Next + Due), keep for later, or cancel |
| R12 | Reading notes are in the owner's words | LifeOS never writes `My Explanation` unless the text is the owner's own dictation |

## 8. Workflows

Every workflow follows the same skeleton:

```
load_context (code) → compute (code) → specialist (1 LLM call, validated) → build proposals (code)
   → ⏸ interrupt for approval → validate (rules + allowlist) → execute → verify → summarise + audit
```

The interrupt uses LangGraph's `interrupt()` with a Postgres checkpointer, so a run can wait for
hours (the owner approves from their phone in the evening) and resume with `Command(resume=...)`.
**⚠️ verify the current API and package names when pinning versions.**

### 8.1 Morning briefing (Phase 4)

Inputs: today's and overdue tasks, Next 7 Days, active goals, this week's done count, the last 7
scorecard rows, recall reviews due.
Output: today's priorities (max 3), deadlines, observations (for example "4 overdue tasks", "no
scorecard for 2 days"), and suggested actions as proposals.
Planning schema: `top: list[TaskRef]` (at most 3, IDs must come from the candidate set), `defer: list[{task, suggested_due, reason}]`, `warnings: list[str]`.

### 8.2 Evening check-in and nightly plan (Phase 4, highest value)

1. Show today's tasks and ask: "What did you finish?"
2. Parse the free-text reply into structure (`done_ids`, `partial`, `learned`, `habits`, `sleep`, `screen_time`, `pages_read`).
3. Proposals: `task.mark_done` (+ `Completed`=today), `habit.upsert_day`, `book.update_progress`.
   Unfinished tasks are only *noted* for the weekly review, never moved.
4. Propose tomorrow's 1–3 tasks (R1) with Due dates.
5. One approval screen, then execute and verify.

Target: under 5 minutes, in line with the "≤ 10 minutes a day in Notion" rule.

### 8.3 Sunday weekly review (Phase 5; MVP version in Phase 4)

Pre-computed: done vs planned by Area, weekly minimums met or missed (from tasks and scorecard),
Screen Time vs last week, the overdue decision table, the Inbox list, active goals with Current.
Review specialist: summary, accomplishments, missed commitments, bottlenecks, recommended priorities.
Proposals: one decision per overdue task, Inbox triage, `week.close` (Review text), `week.create`
for next week. **Goals are never changed** except an optional, separately approved `goal.set_current`.

### 8.4 Monthly career review (Phase 5)

Reads career tasks (Area ∈ {Career Switch, Work, Learning}) for the month, Technical Learning,
Career Evidence, Career Growth (current vs target levels), the Switch Plan month milestones and
the Job Switch Dashboard. Produces a structured report. Proposals: `month.close` (Review text),
`month.create`. Reporting period: the **calendar month** (owner decision), with a short section on
the current Switch Plan month (17th–16th) for context.

### 8.5 Chat with the Chief of Staff (Phase 4)

A tool-calling loop with **read tools** (`get_today`, `get_overdue`, `get_week_summary`,
`get_goals`, `get_reading_status`; later `search_knowledge`) and **propose tools**, which only
create pending proposals. Limits: at most 6 tool iterations per turn, 30 s per LLM call, 90 s per
turn.

### 8.6 Graceful degradation

If every LLM provider fails or the daily budget is spent, workflows still return the
deterministic sections (lists, counts, overdue table) from templates, marked "AI summary
unavailable". Planning falls back to the rule "P0, then P1, then earliest Due", labelled as such.

## 9. Actions, approvals and permissions

### 9.1 Proposal lifecycle

```
pending ──approve──► approved ──► executing ──► executed(verified | noop | mismatch)
   │                                  └──► failed ──retry──► executing
   ├──reject──► rejected
   ├──TTL────► expired     (daily: 24 h, weekly: 7 days)
   └──target changed──► stale (needs re-approval)
```

An approval can include **edits** (for example a different Due date). The edited payload is
re-validated before execution.

Proposals can declare `depends_on` (for example, carrying a task into a week whose Weekly Progress
page doesn't exist yet depends on the `week.create` proposal). The executor runs dependencies
first and skips a proposal whose dependency failed or was rejected.

### 9.2 Action kinds (v1)

| Kind | Payload | Notes |
| --- | --- | --- |
| `task.mark_done` | page_id, completed_date | Sets Status Done + Completed |
| `task.set_due` | page_id, new_due, week_page_id, month_page_id, decision (carry_forward \| reschedule), reason | Weekly review / planning; relations follow the new Due (R7) |
| `task.set_status` | page_id, status ∈ {Next, Doing, Waiting, Cancelled} | Cancelled flagged "important" in the UI |
| `task.create` | title, status ∈ {Inbox, Next}, due?, area?, priority?, goal_id? | |
| `habit.upsert_day` | date, fields | New rows are titled `dd/mm/yyyy`. Lookup parses every existing title as a date and also accepts the legacy `d/m/yy` and `dd/mm/yy` forms, so a row typed by hand as `1/11/26` is found instead of duplicated |
| `week.create` / `week.close` | start, end / page_id, review | Find-or-create by start date |
| `month.create` / `month.close` | start, end / page_id, review | Find-or-create by start date |
| `book.update_progress` | page_id, status?, pages_read?, started?, finished? | |
| *Phase 5* `goal.set_current`, `learning.log_practice`, `evidence.create` | | Approval always |

### 9.3 Permission modes and allowlist

- Each action kind has a mode: `off` (never proposed), `suggest` (shown, cannot be executed;
  apply manually), `approve` (**default**: one-click execute) and `auto` (reserved; disabled in
  v1). Modes are editable in Settings.
- The global kill switch `LIFEOS_WRITES_ENABLED=false` blocks every write regardless of mode.
- The allowlist is code-reviewed config: `(data_source, property, op)`. Anything not listed is rejected.
  Goals: `Current` only. Career Growth, Feedback, Books metadata, Recall `My Explanation`: never.

### 9.4 Idempotency

- `idempotency_key = sha256(kind | target_id | canonical_json(payload) | period)`, unique in Postgres.
- Property updates are naturally idempotent; creates use **find-or-create** on a natural key
  (habit by date, week/month by start date, task by title + created-by-LifeOS marker).
- A cron job runs at most once per (job, local date), enforced by a unique key.

## 10. Memory

| Layer | Store | Contents | Control |
| --- | --- | --- | --- |
| Short-term | LangGraph checkpoints (Postgres) | Current conversation and paused workflow state | Threads expire after 30 days; chat history trimmed to recent turns + a summary |
| Long-term | `memory_facts` table | Rules (seeded from Life OS / Engineer OS pages, with links), stated preferences ("remember…"), approved patterns ("you skip DSA on Fridays — save?") | Listed in Settings; edit/delete; nothing saved silently |
| Knowledge (Phase 5) | pgvector `knowledge_chunks` | Selected Notion pages: learning notes, system-design pages, Book pages, Life OS rule pages | Index roots configurable; re-index by `last_edited_time` + content hash; answers cite the source page |

## 11. Data model (Postgres)

```sql
-- auth
CREATE TABLE users (id uuid PRIMARY KEY, email text UNIQUE NOT NULL, password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now());

-- Notion schema snapshots (drift detection)
CREATE TABLE notion_schema_snapshots (id bigserial PRIMARY KEY, logical_name text NOT NULL,
  data_source_id text NOT NULL, schema jsonb NOT NULL, schema_hash text NOT NULL,
  drift jsonb, fetched_at timestamptz NOT NULL DEFAULT now());

-- workflow / agent runs
CREATE TABLE agent_runs (id uuid PRIMARY KEY, workflow text NOT NULL, trigger text NOT NULL, -- manual|cron|chat
  thread_id text NOT NULL, status text NOT NULL, -- running|waiting_approval|completed|failed
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  provider text, model text, input_tokens int DEFAULT 0, output_tokens int DEFAULT 0,
  llm_latency_ms int DEFAULT 0, error jsonb, output jsonb);

-- proposals
CREATE TABLE action_proposals (id uuid PRIMARY KEY, run_id uuid REFERENCES agent_runs(id),
  kind text NOT NULL, target_data_source text, target_page_id text,
  payload jsonb NOT NULL, before_snapshot jsonb, reason text NOT NULL, rule_ids text[],
  status text NOT NULL, -- pending|approved|executing|executed|failed|rejected|expired|stale
  importance text NOT NULL DEFAULT 'normal',
  depends_on uuid[] NOT NULL DEFAULT '{}',
  idempotency_key text NOT NULL UNIQUE,
  target_last_edited_at timestamptz, expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), decided_at timestamptz, decision_note text,
  edited_payload jsonb,
  result_page_id text);  -- page created/updated, so dependants can link to a newly created page
CREATE INDEX ON action_proposals (status, created_at);

CREATE TABLE action_executions (id bigserial PRIMARY KEY, proposal_id uuid NOT NULL REFERENCES action_proposals(id),
  attempt int NOT NULL, started_at timestamptz NOT NULL, finished_at timestamptz,
  outcome text, -- verified|noop|mismatch|stale|failed
  notion_request_id text, error jsonb, UNIQUE (proposal_id, attempt));

-- append-only audit (no UPDATE/DELETE grants for the app role)
CREATE TABLE audit_log (id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL, -- owner|agent:<name>|system|cron
  event text NOT NULL, entity_type text, entity_id text, details jsonb NOT NULL DEFAULT '{}');

-- scheduled jobs
CREATE TABLE cron_runs (job text NOT NULL, local_date date NOT NULL, run_id uuid,
  started_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (job, local_date));

-- memory
CREATE TABLE memory_facts (id uuid PRIMARY KEY, kind text NOT NULL, -- rule|preference|pattern|context
  content text NOT NULL, source text NOT NULL, source_url text, active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz);

CREATE TABLE settings (key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());

-- Phase 5: knowledge index
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE knowledge_chunks (id bigserial PRIMARY KEY, page_id text NOT NULL, chunk_index int NOT NULL,
  heading_path text, content text NOT NULL, content_hash text NOT NULL, embedding vector(768),
  page_last_edited_at timestamptz NOT NULL, UNIQUE (page_id, chunk_index));
-- LangGraph checkpoint tables are created by the checkpointer's own migration.
```

**Why it's shaped this way:**

- Proposals carry their full payload and a *before snapshot*, so the audit trail can show
  "Due 29 Oct → 3 Nov" years later without trusting Notion's history.
- Executions are a separate table, so retries never overwrite the record of an earlier attempt.
- `audit_log` is append-only by privilege, not by convention.
- Nothing in this schema stores task titles or habit values except inside proposals and snapshots.
  Productivity data lives in Notion (D1).

## 12. API contracts (`/api/v1`)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/login`, `/auth/logout`; GET `/auth/me` | Single-owner session |
| GET | `/health`, `/health/ready` | Liveness; readiness (DB + Notion reachable) |
| GET | `/notion/status`, `/notion/schema`; POST `/notion/discover` | Connection, mapped schema, drift |
| GET | `/tasks?view=active\|today\|overdue\|inbox\|next7\|done&from=&to=` | Read-through to Notion |
| GET | `/goals?status=active`, `/habits?from=&to=`, `/reading/queue` | Read-through |
| POST | `/workflows/{name}/runs` | Start `morning_briefing`, `evening_checkin`, `weekly_review`, `monthly_career_review` → `{run_id}` |
| GET | `/runs/{id}`; POST `/runs/{id}/resume` `{answer}` | Run state and outputs; answer what the run is waiting for (check-in reply, weekly decisions, approvals) |
| POST | `/chat` (SSE) | Chief of Staff chat; streams tokens and tool events |
| GET | `/proposals?status=pending` | Pending approvals |
| POST | `/proposals/{id}/approve` `{edits?}`, `/proposals/{id}/reject` `{note?}`, `/proposals/approve-batch` `{ids}` | Decide |
| GET | `/audit?from=&to=&entity_id=` | Audit trail |
| GET/PUT | `/settings` | Permission modes, automation toggles, schedule |
| GET/POST/DELETE | `/memory/facts` | Long-term memory control |
| POST | `/internal/cron/{job}` | HMAC-signed (`X-LifeOS-Signature`, `X-LifeOS-Timestamp`, 5-min replay window) |

Example proposal:

```json
{
  "id": "6f1c…",
  "kind": "task.set_due",
  "status": "pending",
  "importance": "normal",
  "target": {"data_source": "tasks", "page_id": "…", "title": "SOLID and design principles", "url": "https://www.notion.so/…"},
  "before": {"Due": "2026-10-31", "Status": "Next"},
  "after":  {"Due": "2026-11-03"},
  "decision": "carry_forward",
  "reason": "Overdue since 31 Oct; Tuesday has 2 of 3 major-task slots free.",
  "rule_ids": ["R1", "R6", "R7"],
  "expires_at": "2026-11-08T00:00:00+05:30"
}
```

Errors use a single shape: `{"error": {"code": "notion_rate_limited", "message": "...", "retryable": true, "request_id": "..."}}`.

## 13. Frontend

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui; TanStack Query for server state; SSE for chat.

| Page | Phase | Notes |
| --- | --- | --- |
| Command Center | 6 (MVP) | Chat, today's priorities, pending approvals, recent runs, Notion status |
| Approvals drawer | 6 (MVP) | Before → after diff per proposal, edit, approve/reject, batch approve |
| Weekly Review | 6 (MVP) | Decision table for overdue tasks, Inbox triage, summary |
| Settings / Notion Integration | 6 (MVP) | Permission modes, schedule, memory facts, schema drift |
| Daily Briefing, Goals, Tasks, Learning, Monthly Review, Agent Activity | 6 (later) | |

`/api/*` is rewritten to the backend so the session cookie stays first-party. **⚠️ verify SSE
streaming through Vercel rewrites**; the fallback is a custom domain with `app.` and `api.`
subdomains (same-site cookies).

## 14. Security

- **Secrets** only in environment variables (Render, Vercel, GitHub Actions); `.env.example` is
  committed; `gitleaks` runs as a pre-commit hook.
- **Notion integration:** an internal integration shared only with the Life OS page.
  Capabilities: read content, update content, insert content. No user-information or comment access.
- **Auth:** argon2id hashing; session cookie `HttpOnly; Secure; SameSite=Lax`; login rate limit
  5/min; a CSRF header is required on mutating requests.
- **Authorisation:** single owner; every route except `/health` and signed cron requires a session.
- **Prompt-injection defences:** Notion content is untrusted data, wrapped in delimiters and
  labelled as data in prompts. Tools are typed and allowlisted, there is no URL-fetch tool, and the
  LLM cannot execute anything. Outputs are schema-validated, and every write needs owner approval.
- **Logs** record IDs, kinds and counts, never task titles, habit notes or tokens.
- Input validation via Pydantic on every request.

## 15. Reliability and observability

- Timeouts on every external call; retries only for idempotent operations.
- Limits: 6 tool iterations, 90 s per chat turn, 5 minutes per workflow run, a daily LLM token budget.
- `structlog` JSON logs carrying `request_id`, `run_id` and `proposal_id`.
- `agent_runs` records provider, model, tokens and latency per run, for later cost and latency reports
  (useful as Flagship-2 evidence if chosen).
- Graceful degradation (§8.6).

## 16. LLM provider strategy

`LLMProvider` protocol: `generate_structured(messages, schema) -> BaseModel`, `stream(messages, tools)`.
Implementations: Gemini (default), Groq (fallback), Mock (tests; deterministic fixtures).
Router: primary → fallback on timeout/429/5xx; per-day budget; LLM calls serialised under a
per-provider rate limiter.

Expected load: about 15–25 calls a day (briefing 1–2, check-in 2–3, chat ~10–15) and 3–5 for a
weekly review. This fits even the most conservative free-tier figures reported, but those figures
change often. **⚠️ read the actual limits from the Google AI Studio dashboard before choosing the
model.** The model name is configuration, not code.

## 17. Deployment and free-tier constraints

| Service | Use | Constraint to design for |
| --- | --- | --- |
| Vercel (Hobby) | Next.js | Personal, non-commercial use |
| Render (Free) | FastAPI container | Sleeps after ~15 min without requests; cold start up to ~30 s or more. Cron and the first page load after idle will be slow |
| Neon (Free) | Postgres + pgvector | Current docs: 1 GB storage/project, 100 CU-hours/project/month; compute is suspended (not billed) when exhausted; pgvector available on all plans |
| Gemini (Free) | Primary LLM | Per-model RPM/TPM/RPD limits that have changed repeatedly; check the dashboard |
| Groq (Free) | Fallback LLM | Rate limited; check the console |
| GitHub Actions | Cron | Scheduled runs can be delayed; jobs must tolerate late execution |
| Notion API | Source of truth | ~3 req/s average per connection, plus a workspace-level limit; 429 + Retry-After |

Local development runs everything in Docker Compose (Postgres with pgvector, backend, frontend).

## 18. Testing strategy

| Level | What | Network |
| --- | --- | --- |
| Unit | Rules, calendar (IST weeks), codecs, query builders, idempotency keys, policy | None |
| Component | Repositories against **recorded** Notion JSON (sanitised fixtures) | None |
| Workflow | Graphs with `FakeNotionGateway` + `MockLLM`: given workspace state X, expect proposals Y; interrupt/resume | None |
| Execution | Stale detection, mismatch, retry, find-or-create, kill switch | None |
| API | OpenAPI snapshot, auth, CSRF, cron signature | None |
| Live smoke | Read-only checks against Life OS; write checks **only** against the "LifeOS Sandbox (dev)" databases | Notion |
| Frontend | Playwright: log in, approve a proposal, see it verified | Local stack |

No test ever writes to the real Life OS databases.

## 19. Decisions log and open questions

| # | Question | Decision (2026-11-01) |
| --- | --- | --- |
| Q1 | Portfolio framing | LifeOS **is Flagship 2** and will be a portfolio case study (§20) |
| Q2 | Definition of a "major task" | Due = that day, Status Next or Doing, not `Recurring` |
| Q3 | Week/Month relations on carry-forward | Move them to the new week and month |
| Q4 | Monthly review period | Calendar month |
| Q5 | Sandbox for write tests | Approved and created |
| Q6 | Habit row title format | `dd/mm/yyyy` |
| Q7 | Update the Life OS Sunday-review text | Approved and done (20–25 min) |
| Q8 | Carried-forward task with *no* Week/Month relation | Link it to the new week and month too (every carried task ends up linked) |

Phase 4b implementation decisions (2026-11-01):

| # | Topic | Decision |
| --- | --- | --- |
| I1 | Answering a run | One endpoint, `POST /runs/{id}/resume`, for every pause (question, decisions, approval); resuming is a compare-and-set on the run status |
| I2 | Proposals left undecided in an approval step | Rejected, so nothing lingers half-decided |
| I3 | An approval that isn't executed before its TTL (e.g. writes were off) | Expires; the executor refuses it rather than writing days later |
| I4 | Editing the Due date of a `task.set_due` proposal | Allowed only within the same week and month, because the Week/Month links were resolved for the original date (R7) |
| I5 | Models | Pinned chains in config: fast `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`; smart `gemini-3.5-flash` → `gemini-3.5-flash-lite`. The 2.5 models return 404 for new accounts |
| I6 | Habits `Screen Time` unit | Hours, like Sleep (the column was empty). **Owner to confirm** |
| I7 | Model output shown to the owner | Reference codes (T1, D2) are replaced by task titles before anything is displayed or written |

## 20. Flagship 2 and portfolio case study requirements

Because LifeOS replaces the original Flagship 2 plan, it must also meet that plan's quality bar.
These are cheap to build in from the start and expensive to retrofit:

- **Metrics from day one.** Every run records latency per step, LLM tokens, provider used,
  fallbacks, Notion calls and retries, and proposal outcomes (approved, rejected, edited, stale,
  mismatch). These numbers become the case study's results section; none are invented.
- **Decision records.** Each significant choice gets a short ADR in `docs/adr/` (context, options,
  decision, consequences), starting with D1–D9 above.
- **Evaluation set.** A small labelled set of check-in messages and workspace states with
  expected structured outputs (done tasks, habit values, top-3 picks). This gives parsing and
  planning accuracy numbers for each model, and catches regressions when prompts change.
- **Demo mode.** The public deployment must never show the real workspace. A `DEMO_MODE` runs the
  same code against `FakeNotionGateway` with a synthetic seed workspace, for the live demo,
  screenshots and the GIF.
- **Write-ups.** At least "Letting an LLM write to a live system safely: proposals, approvals,
  idempotency and verification", plus a latency and cost report on free-tier providers.
- **Portfolio page.** The portfolio site already has a per-project route
  (`src/app/projects/notification-platform`); LifeOS gets its own page in the same pattern in Phase 8.

## Sandbox IDs

Sandbox data-source IDs are recorded in `docs/private/00-discovery.md` and go into the
`.env` files for tests, not into committed config.

## Sources for external constraints (checked 2026-11-01)

- Notion API: [Upgrading to 2025-09-03](https://developers.notion.com/docs/upgrade-guide-2025-09-03), [Request limits](https://developers.notion.com/reference/request-limits)
- Neon: [Plans](https://neon.com/docs/introduction/plans), [pgvector](https://neon.com/docs/extensions/pgvector)
- Render free-tier sleep behaviour: [Render community](https://community.render.com/t/cold-boot-start-of-the-server-for-first-request/15911/2)
- Gemini free-tier limits: third-party summaries only, which conflict; e.g. [tinkerllm](https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/). Check AI Studio.
- LangGraph interrupts: third-party tutorials, e.g. [markaicode](https://www.markaicode.com/langgraph-interrupt-pause-resume-agent/). Check the official docs when pinning versions.
