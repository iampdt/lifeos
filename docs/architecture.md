# Architecture

LifeOS is a chief of staff for a personal productivity system that lives in Notion. It reads
the workspace, runs the daily and weekly routines (evening check-in, morning briefing, Sunday
review), and proposes changes. **Nothing is written to Notion until the owner approves it**, and
every write is verified and recorded in an append-only audit log.

Design rationale for each choice is in [decisions.md](decisions.md); endpoints are in
[api.md](api.md).

## 1. Goals and non-goals

**Goals**

- Run the existing Notion routines with less effort and better data.
- Read the whole workspace; write only through approved, typed, verified actions.
- Work within free-tier limits and degrade gracefully when the LLM or Notion is unavailable.
- Keep the codebase small enough to explain line by line.

**Non-goals**

- Replacing Notion, mirroring it into Postgres, or adding Notion databases.
- Autonomous changes: in v1 every write needs approval.
- Multi-user SaaS in v1: one owner, one workspace. Phase 9 opens it to more accounts
  ([multi-user.md](multi-user.md)); data is already separated per account.

## 2. System overview

```
 Browser ──► Next.js (Vercel) ── /api/* rewrite ──► FastAPI (Render, Docker)
                                                     │
 GitHub Actions cron ── HMAC-signed POST ───────────►│
                                                     ├──► Notion API      (source of truth)
                                                     ├──► Gemini          (structured output)
                                                     └──► Postgres (Neon) (proposals, runs, audit,
                                                                           sessions, checkpoints)
```

The browser only talks to the Next.js app; `/api/*` is proxied to the backend, so the session
cookie is first-party. Scheduled jobs call the backend directly with a signed request.

### Backend modules (`backend/app`)

| Package | Responsibility |
| --- | --- |
| `api/` | FastAPI app and routes, request guard (session + CSRF, or bearer token), signed cron endpoint, error shape |
| `auth/` | Google sign-in (OpenID Connect), account linking, argon2id passwords, server-side sessions, login rate limiter |
| `workflows/` | LangGraph graphs (`evening.py`, `morning.py`, `weekly.py`), the run manager, the two-phase proposal builder, per-account contexts (`contexts.py`) |
| `workspaces/` | Module catalogue and mapping validation, onboarding suggestions, saved account workspaces |
| `agents/` | Specialists: `planner`, `checkin`, `reviewer`, `chat`. Each is a prompt, an output schema and validation |
| `execution/` | Action kinds and payloads, proposal lifecycle, executor, audit log |
| `permissions/` | Field-level write allowlist and per-kind permission modes |
| `services/` | Domain rules, calendar (weeks, months, timezone), period lookup, prompt-safe context |
| `notion/` | API client, property codecs and encoders, mapping, schema drift, view queries, repositories, OAuth and accounts' connections (`oauth.py`, `connections.py`) |
| `llm/` | Provider protocol, Gemini provider, mock provider, router with fallback and budget, accounts' AI access (own key, allowance, consent) |
| `db/` | SQLAlchemy models, engine and database URL handling |
| `core/` | Settings from the environment |

### Frontend (`frontend/src`)

Next.js App Router with Cache Components, Tailwind CSS, shadcn/ui on Base UI, and TanStack
Query. Pages: sign-in, Command Center, Weekly review, Settings (see §11).

## 3. Notion integration

### 3.1 Client

- `httpx.AsyncClient` with `Notion-Version: 2025-09-03` (data-source endpoints).
- **Rate limit:** a token bucket at 2.5 requests/s, below Notion's average of 3 per connection.
- **Retries:** `429` (honouring `Retry-After`), `5xx` and network errors, with exponential
  backoff and full jitter, at most 5 attempts within 60 s. `create_page` is retried only on
  `429`, because a timeout may mean the page was created.
- **Typed errors**, cursor pagination, and a full fetch for relations the page object truncates.
- No archive or delete method exists. Cancelling is a status change, and needs approval.

### 3.2 Mapping and schema drift

A mapping ties logical names to Notion property names, types and option values
(`tasks.status.done → "Done"`). Code never uses a Notion display name, so a renamed property is
a configuration change. The owner's workspace is mapped in `backend/config/notion_mapping.yaml`,
with data-source IDs from the environment (and a `sandbox` profile that reads
`NOTION_SANDBOX_*`); every other account saves its own mapping (`workspaces` table).

A workspace is made of modules (`workspaces/modules.py`): Tasks is required; Habits, Weekly and
Monthly reviews, Goals and Reading are optional, and the workflows skip what isn't mapped. In
Habits, every checkbox property is a habit and every number property a metric (with an optional
unit and description), so the check-in, review and chat describe whatever the workspace tracks.
Status fields map semantic keys (`next`, `done`, …) to the workspace's own options.

`notion/schema.py` compares each data source with the mapping and reports missing properties,
wrong types and missing options. `GET /api/v1/notion/status` and `scripts/notion_check.py` show
the result.

### 3.3 Reading

Codecs turn property JSON into Python values; repositories return domain models (`Task`, `Goal`,
`HabitDay`, `PeriodPage`, `Book`). The API can filter data sources but can't run a saved view,
so `notion/queries.py` rebuilds the views the workflows depend on (Today, Overdue, All Active,
Inbox, Next 7 Days, Done history). They return the same tasks in the same order as the Notion
views.

### 3.4 Writing

The executor is the only code that writes:

1. Check the kill switch (`LIFEOS_WRITES_ENABLED`) and the field allowlist.
2. For updates, re-read the page. If it already has the target values, the result is `noop`.
   If a field changed since the proposal was made, the result is `stale` and nothing is written.
3. For creates, find an existing page by natural key first (habit row by date, period page by
   start date, task by title and due date), so a retry never creates a duplicate.
4. Write, read back and compare: `verified` or `mismatch`.
5. Record the attempt and append to the audit log.

Notion has no conditional writes, so a small race window between steps 2 and 4 remains.

## 4. Domain rules

Rules live in `services/rules.py` as pure functions. Their IDs appear in proposals and logs.

| ID | Rule | Enforcement |
| --- | --- | --- |
| R1 | At most 3 major tasks a day (each workspace can set its own limit). A major task is due that day, has Status Next or Doing, and isn't Recurring | Plans that exceed the free slots are rejected; the morning briefing offers to move extras; chat refuses to overfill a day |
| R2 | Active means Status is not Done or Cancelled | Query builders and `is_open` |
| R3 | Completed tasks are never deleted | No delete code path |
| R4 | Done tasks leave active views but stay in history | History is Status Done grouped by Due week or month |
| R5 | Done tasks count toward progress | Weekly metrics: planned = due in the week and not Inbox; done = Status Done; Cancelled reported separately |
| R6 | Unfinished tasks are never moved automatically | Nightly and morning plans never touch overdue tasks; only the weekly review proposes carry forward, reschedule or cancel |
| R7 | Moving a task moves its Week and Month links too | `task.set_due` names the target pages; a missing page becomes a `week.create` / `month.create` proposal that runs first |
| R8 | Due decides the week and month | Monday–Sunday ISO weeks and calendar months in Asia/Kolkata (`services/calendar.py`), checked against the Notion Week Key formula on every dated task |
| R9 | The weekly review takes 20–25 minutes | Everything is pre-computed; at most 15 overdue decisions per review |
| R10 | The monthly review looks back at the whole month: tasks, scorecard, goals and the weekly reviews; it never changes tasks or goals | Monthly workflow scope |
| R11 | The Inbox is triaged in the weekly review | Each item is kept, made Next (optionally with a date) or cancelled |
| R12 | Reading notes stay in the owner's words | LifeOS never writes reading notes; check-in notes are the owner's text, appended |

## 5. Workflows

Each workflow is a LangGraph graph with the same shape:

```
load (code) → compute (code) → specialist (one LLM call, validated) → propose (code)
  → ⏸ approval → execute → verify → summary
```

Pauses use LangGraph's `interrupt()` with a Postgres checkpointer, so a run can wait for hours
and resume with `POST /api/v1/runs/{id}/resume`. Each run is a row in `agent_runs` with its
status, the request it is waiting on, its summary, and LLM tokens and latency.

### 5.1 Evening check-in and nightly plan

1. Show today's and overdue tasks, books in progress, and the habits already logged today.
2. **Pause** for the owner's reply: free text and/or ticked tasks and habits.
3. The check-in specialist extracts finished and partly done tasks, habits, sleep, screen time,
   pages read, what was learned, and new to-dos. Ticked items skip the model and win.
4. The planner fills tomorrow's free major-task slots from undated or later tasks.
5. Propose: `task.mark_done`, `habit.upsert_day` (notes appended, not replaced),
   `book.update_progress`, `task.create` for new Inbox items, and `task.set_due` for the plan.
6. **Pause** for approval, then execute and summarise. Unfinished tasks are only noted.

### 5.2 Morning briefing

If today has more than 3 major tasks, the planner picks the 3 to keep and the rest are offered
for the next days with a free slot. If today is empty, it proposes up to 3 tasks. The briefing
lists today's priorities, the next 7 days, overdue tasks (pointed to the weekly review) and days
without a scorecard.

### 5.3 Weekly review

1. Load the week: tasks due in it, open tasks, the Inbox, habit rows, the Weekly Progress page.
2. Compute R5 metrics and habit summaries for this week and last week, and suggest a carry-forward
   day for each overdue task (earliest day next week with a free slot).
3. The review specialist writes a summary, wins, misses, bottlenecks and up to 3 priorities,
   and recommends carry forward or cancel for each overdue task.
4. **Pause** for decisions: per overdue task carry forward, reschedule, cancel or decide later;
   per Inbox item keep, make Next or cancel; plus a note in the owner's words.
5. Propose the decisions, `week.close` (the Review text is appended below anything already
   there) and `week.create` for next week if missing.
6. **Pause** for approval, then execute and summarise. Goals are never changed.

### 5.4 Monthly review

1. Load the month (last month in the first week of a month, as on the scheduled 1st; otherwise
   the current one): tasks due in it, the scorecard against the month before, active goals with
   progress, the month's weekly reviews and the monthly page.
2. The monthly specialist (`agents/monthly.py`) writes a summary, wins, misses, patterns across
   the weeks, up to 3 things to focus on next month, and a line per active goal. Without a model
   the review comes from the numbers.
3. **Pause** for the owner's note.
4. Propose `month.close` (the Review text goes below anything already there) and `month.create`
   for next month if missing, where the workspace has monthly pages.
5. **Pause** for approval, then execute and summarise (R10).

### 5.5 Chat

A loop of structured steps: each model call returns one tool call or the final answer, with at
most 6 steps and 90 s per turn. Read tools: today, overdue, next 7 days, Inbox, week summary,
goals, reading. Propose tools (mark done, reschedule, set status, create task) only create
pending proposals, and code checks every argument and R1 first. Answers stream to the browser as
server-sent events.

### 5.6 Without a model

If every model fails or the daily token budget is spent:

- the check-in matches task titles quoted in the reply;
- planning uses "P0, then P1, then earliest Due";
- the weekly review is built from the numbers alone.

Each fallback is named in the run's warnings. Chat reports that the AI is unavailable.

### 5.7 Scheduling

Every hour GitHub Actions calls `POST /api/v1/internal/cron/tick` with an HMAC signature. The
tick expires stale proposals, runs each account's daily drift check, and starts the routines due
in each account's own timezone (`workflows/schedule.py`):

| Routine | When (defaults) |
| --- | --- |
| `morning_briefing` | Daily, 07:30 |
| `evening_checkin` | Daily, 21:00 (waits for the answer) |
| `weekly_review` | Sundays, 18:00 (waits for decisions) |
| `monthly_review` | The 1st, at the weekly review's time (waits for a note) |

The owner's times come from settings (`LIFEOS_MORNING_TIME`, …) in `TIMEZONE`; every other
account sets its own, or turns them off. A routine is due for four hours from its time, within
the same local day, and runs at most once per account and local date (`cron_runs`); the oldest
due routines run first, up to `LIFEOS_TICK_MAX_RUNS` per tick, and the rest wait for the next
tick. A failed run frees its slot so a later tick retries it. Because scheduled runs can arrive
hours late, the web app also asks on load (`POST /schedule/catch-up`) for the account's due
routines, which start then if they haven't yet.

## 6. Actions, approvals and permissions

### 6.1 Proposal lifecycle

```
pending ──approve──► approved ──► executing ──► executed (verified | noop | mismatch)
   │                    │                  └──► failed ──retry──► approved
   ├──reject──► rejected│
   ├──TTL────► expired ◄┘ (an approval not executed before its TTL expires too)
   └── target changed in Notion ──► stale
```

Approvals can include edits, which are re-validated. Edits can't change the target page, and an
edited Due date must stay in the same week and month (the links were resolved for that period).
In an approval step, anything not approved is rejected. Proposals of a waiting run are decided
through that run; standalone proposals (from chat) through `/proposals/{id}/approve|reject`.

### 6.2 Action kinds

| Kind | Effect |
| --- | --- |
| `task.mark_done` | Status Done and Completed date |
| `task.set_due` | New Due date with Week and Month links (`carry_forward` or `reschedule`) |
| `task.set_status` | Next, Doing, Waiting or Cancelled (Cancelled is flagged as important) |
| `task.create` | New task in the Inbox, or Next with a Due date |
| `habit.upsert_day` | Creates or updates the day's scorecard row (titled `dd/mm/yyyy`) |
| `week.create` / `month.create` | New Weekly or Monthly Progress page |
| `week.close` / `month.close` | Review text and Status Closed |
| `book.update_progress` | Pages read, status, start and finish dates |

Payloads are Pydantic models with `extra="forbid"`; `build_plan` turns a payload into concrete
field values.

### 6.3 Permissions

- A **field allowlist** per data source (`permissions/policy.py`). Goals: `Current` only. A plan
  that touches any other field is refused.
- **Modes** per action kind, chosen per account in Settings (`account_settings`; anything not
  chosen is `approve`):
  - `approve`: proposed, and executed once approved.
  - `suggest`: proposed and shown, but approving it is refused; nothing is executed.
  - `off`: never proposed. The run says so in its warnings. A new due date whose weekly or
    monthly page doesn't exist yet, with creating that page `off`, is proposed without the link.
    Chat refuses a request that needs an `off` kind.
  - `auto` (reserved): not offered.
- The **kill switch** `LIFEOS_WRITES_ENABLED=false` blocks every write regardless of approval.

### 6.4 Idempotency

- Proposal key: SHA-256 of kind, payload and local day, unique per account in Postgres. The
  same suggestion twice in a day is one proposal; on a later day it is a new one.
- Updates are naturally idempotent; creates use find-or-create (§3.4).
- Scheduled jobs run at most once per (account, job, local date).

## 7. LLM layer

- **Structured output only.** Every call asks for JSON matching a Pydantic schema, and the
  result is validated again in code. Free text never drives an action.
- **Router.** Each tier has a pinned chain of models, tried in order on rate limits, timeouts,
  server errors and invalid output. The fast tier parses and plans; the smart tier writes
  reviews. A daily token budget stops calls, and the workflows fall back to rules.
- **References instead of IDs.** Records appear in prompts as short codes (T1, B1). Every code
  the model returns is checked against the map, and codes left in prose are replaced by titles
  before anything is shown or written.
- **Prompt injection.** Notion content is untrusted. Titles are flattened to one line and
  stripped of angle brackets, and the data sits in a `<notion_data>` block that the system
  prompt marks as data, never instructions. The model has no tool that writes, fetches URLs or
  executes anything.
- **Usage.** Provider, model, tokens and latency are recorded per run in `agent_runs`.

## 8. Data model

Every row of `agent_runs`, `action_proposals`, `cron_runs` and `audit_log` belongs to an
account (`user_id`), and every query filters on it.

| Table | Contents |
| --- | --- |
| `agent_runs` | One row per workflow or chat run: status, waiting request or summary, provider, model, tokens, latency, error |
| `action_proposals` | Kind, payload, edited payload, before-snapshot, reason, rule IDs, status, dependencies, idempotency key, expiry, target title, result page |
| `action_executions` | One row per execution attempt and its outcome |
| `audit_log` | Append-only (a trigger rejects `UPDATE` and `DELETE`, except through `purge_user_audit` when an account is deleted): account, actor, event, entity, details. IDs and kinds only, no page contents |
| `users`, `sessions` | Accounts (Google subject ID or password hash; at most one `is_owner`); sessions store a SHA-256 of the cookie token and a CSRF token |
| `oauth_states` | Single-use sign-in and connection attempts: state, nonce and PKCE verifier (Google), account (Notion), expiry |
| `ai_access`, `ai_usage` | An account's agreement to the free-tier terms (with the disclosure version) and its own Gemini key (encrypted); tokens used per UTC day on the service key and on its own |
| `workspaces` | An account's own mapping (module → data source and properties) and rule settings: timezone, major tasks a day, review day and time |
| `notion_connections` | An account's Notion workspace connection: workspace, bot, access and refresh tokens (encrypted), status (`active` or `needs_reconnect`) |
| `cron_runs` | One row per (account, job, local date): scheduled routines and the daily drift check |
| `checkpoint*` | LangGraph checkpoints, created and migrated by the checkpointer itself |

Productivity records stay in Notion. Postgres holds only state LifeOS owns. Migrations are in
`backend/alembic/versions`.

## 9. Security

- **Secrets** only in environment variables; `.env` is git-ignored and `.env.example` documents
  every variable.
- **Notion:** the owner's workspace uses an internal integration connected only to the Life OS
  pages (read, update and insert content; no user or comment access). Accounts connect their own
  workspace through a public integration with OAuth; its access and refresh tokens are encrypted
  at rest (Fernet, `LIFEOS_ENCRYPTION_KEY`, rotatable), refreshed under a row lock because each
  refresh invalidates the previous refresh token, and revoked at Notion on disconnect or account
  deletion. A connection attempt is tied to the account that started it.
- **Authentication:** Google sign-in (authorisation code with PKCE, state and nonce; the ID token
  is verified against Google's keys) where configured, otherwise argon2id passwords; the session
  cookie is `HttpOnly; SameSite=Lax` and
  `Secure` in production; only a SHA-256 of the token is stored; logout and password resets
  revoke sessions; sign-in is limited to 5 attempts a minute.
- **CSRF:** every request that changes state must send the session's `X-CSRF-Token`.
- **Machine access:** a bearer token for scripts; HMAC signatures with a 5-minute window for
  scheduled jobs. Both fail closed when their secret isn't configured.
- **Authorization:** every route except health, sign-in and signed cron requires a session or
  the bearer token (which acts as the owner account). Each account sees and acts on only its own
  runs, proposals and workspace: the runner, proposal service and executor are built for one
  account and filter every query by it, and tests check that another account's IDs behave as if
  they didn't exist. The web app's sign-in redirect is a convenience only.
- **Deleting an account** removes everything stored for it in one transaction, its run
  checkpoints, and its audit entries through a database function that the append-only trigger
  allows only for that account; a single ID-only entry records the deletion.
- **Production** hides the OpenAPI schema and interactive docs.
- **Logs and audit records** hold IDs, kinds and counts, never task titles, notes or tokens. Chat
  messages and answers are not stored.

## 10. Reliability and observability

- Timeouts on every external call; retries only where a repeat is safe.
- Limits: 30 s per model call, 6 steps and 90 s per chat turn, a daily token budget.
- `agent_runs` records per-run tokens, latency, model and status; `action_executions` records
  every write attempt; the audit log records every decision and outcome.
- `GET /api/v1/reports/usage` turns an account's runs into a report (runs and outcomes per
  routine, AI calls and tokens, median and 95th-percentile model time per run, runs and tokens
  per day), shown on the Activity page.
- `GET /api/v1/health` (liveness) and `GET /api/v1/health/ready` (database).
- Free-tier hosts sleep: the database pool pings connections before use, and scheduled jobs
  wake the API before calling it.

## 11. Frontend

| Page | Contents |
| --- | --- |
| `/login` | Sign-in |
| `/` Command Center | Today's tasks and major-task count, evening check-in and morning briefing, recent runs, streaming chat, approvals drawer |
| `/tasks` | Today, overdue, next 7 days, Inbox and all open tasks, with a filter; each links to its Notion page |
| `/goals` | Goals with progress, success metric, deadline and their open tasks; the reading queue with pages read |
| `/history` | Each day's morning briefing (priorities, observations) beside its evening check-in (finished, left over, scorecard, the next day's plan) |
| `/weekly` Reviews | Weekly review: metrics, draft review, decisions, Inbox triage, the owner's note, approval; the monthly review |
| `/settings` | Write switch, timezone, permission modes, model chains and tokens used, Notion connection and drift, account |
| `/activity` | Runs and outcomes per routine, AI calls, tokens and model time, runs per day, recent runs |
| `/privacy`, `/terms` | Public privacy policy and terms of use |
| `/setup` | Onboarding for an account's own workspace: a database per module, a property per field, status meanings, habits and metrics, rules |

Approval cards show before → after for each kind. Selecting a move also selects the page it
depends on, and cancelling a task needs an explicit tick. Pages render immediately with their
own loading states, so each route has a static shell. "Today" comes from the backend, in the
owner's timezone.

## 12. Testing

| Level | What | Network |
| --- | --- | --- |
| Unit | Rules, calendar, codecs, encoders, queries, policy, LLM router, agents with the mock provider, prompt sanitising, passwords, URL handling | None |
| Database | Proposals, executor (stale, noop, mismatch, find-or-create, kill switch, expiry), cron idempotency | Local Postgres |
| Workflow | Whole graphs with an in-memory Notion fake, the mock provider and a checkpointer: interrupts, resumes, fallbacks, writes | Local Postgres |
| API | Auth, CSRF, sessions, rate limit, approvals, chat streaming, cron signatures, production settings | Local Postgres |
| Live | Read checks on the real workspace; write checks only on a sandbox copy | Notion |
| Frontend | Lint, type check, production build; end-to-end runs in a browser against the sandbox | Local stack |
| Evaluation | Accuracy of check-in parsing and day planning per model on a synthetic labelled set, including prompt-injection cases ([evals](evals/README.md)) | Gemini, on demand |

No test writes to the real workspace. CI runs everything except the live tests.

## 13. Deployment

| Service | Use | Constraint designed for |
| --- | --- | --- |
| Vercel (Hobby) | Next.js app | Personal, non-commercial use |
| Render (Free, Docker) | FastAPI API | Sleeps after 15 minutes without traffic and takes about a minute to wake; 750 instance hours a month; no pre-deploy step, so migrations run at container start |
| Neon (Free) | Postgres | 1 GB per project, 100 CU-hours a month, scales to zero after 5 minutes idle |
| GitHub Actions | CI and scheduled jobs | Scheduled runs can be late; in a public repository they are disabled after 60 days without activity |
| Gemini (free tier) | Models | Per-model rate limits that change; the model chain is configuration |
| Notion API | Source of truth | About 3 requests/s per connection; `429` with `Retry-After` |

Setup and operations: [deployment.md](deployment.md).

### Public demo

A second deployment runs the same image with `LIFEOS_DEMO_MODE=true`, for anyone to try without
a Notion account:

- **Workspace:** an in-memory implementation of the Notion client (`app/demo/notion.py`, the
  same one the tests use) seeded with a believable week, relative to today (`app/demo/seed.py`).
  Everything above it (repositories, rules, workflows, agents, approvals, executor with
  read-after-write verification, chat) runs unchanged.
- **Access:** `POST /auth/demo` opens a normal session on a shared demo account with no password.
  The password form isn't offered.
- **Reset:** the workspace is rebuilt and the run, proposal and audit tables are emptied on every
  start (the free host restarts after an idle spell) and from the in-app **Reset demo** button
  (rate-limited). Because a reset wipes tables, demo mode refuses to start unless the database
  name contains `demo`.
- **Limits:** a separate, smaller daily token budget; no scheduled jobs.

## 14. Planned

- **Memory:** long-term facts the owner can see and edit; a knowledge index over selected Notion
  pages (pgvector) with cited answers.
- **Goal and learning specialists**, and an optional `goal.set_current` action.
- **Quality bar:** more evaluation suites (weekly and monthly review, chat tool use).
- **Multi-user:** sign-up, Notion OAuth per user, per-user data, onboarding from a Notion
  template, paid model and hosting tiers.
- Refuse writes to a data source whose schema has drifted (today drift is reported, not enforced).
- A second LLM provider in the fallback chain.

## References

- Notion API: [Upgrading to 2025-09-03](https://developers.notion.com/docs/upgrade-guide-2025-09-03), [request limits](https://developers.notion.com/reference/request-limits)
- Render: [free instances](https://render.com/docs/free), [web services](https://render.com/docs/web-services), [Blueprint spec](https://render.com/docs/blueprint-spec)
- Neon: [plans](https://neon.com/docs/introduction/plans)
- GitHub: [disabling and enabling a workflow](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow)
- LangGraph: [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
