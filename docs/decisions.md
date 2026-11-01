# Decisions

Why LifeOS is built the way it is. Each entry records the choice, the reason, and what it
replaced. Dates are when the decision was made.

## Architecture (2026-11-01)

| # | Decision | Why | Rejected alternative |
| --- | --- | --- | --- |
| D1 | **Notion is the only store of productivity records.** Postgres holds only state LifeOS owns: proposals, runs, audit, sessions, checkpoints | No sync engine, no conflicts, no stale copies | Mirroring Notion into Postgres |
| D2 | **Modular monolith:** one FastAPI service | One user, free hosting, fastest to change | Microservices |
| D3 | **Workflows are explicit LangGraph graphs.** Data loading and rules are plain code; the model only prioritises, summarises and parses free text | Predictable, testable, cheap on free-tier quotas | Free-form multi-agent delegation |
| D4 | **The model proposes, code executes.** Specialists can only produce typed proposals; a deterministic executor applies approved ones | A model can never write to Notion directly | Giving agents write tools |
| D5 | **The official Notion API** (version `2025-09-03`, data-source endpoints) behind repositories and a small client interface | Supported and documented; tests swap in an in-memory fake | Calling an MCP server at runtime |
| D6 | **A thin LLM layer over the official SDK**, with a mock provider. LangGraph is used for state, checkpoints and interrupts only | Full control of retries, timeouts and fallbacks; trivial to mock | LangChain chat-model wrappers |
| D7 | **Backend-owned single-owner auth** (argon2id, HttpOnly session cookie); the web app proxies `/api/*` so the cookie is first-party | The app and API hosts are different sites, and browsers increasingly block cross-site cookies | Auth in the frontend with token forwarding |
| D8 | **No in-process scheduler.** GitHub Actions calls signed `POST /internal/cron/{job}` endpoints; each job runs at most once a day | Free hosts sleep when idle, so an in-process scheduler would miss runs | APScheduler, Celery beat |
| D9 | **Due decides the week and month** (Monday–Sunday, Asia/Kolkata), not the optional Week/Month relations | Matches how the workspace is used and works for unlinked tasks | Trusting the manual relations |

## Product rules (2026-11-01)

| # | Question | Decision |
| --- | --- | --- |
| P1 | Scope | All of Life OS: Tasks, Goals, Habits, Weekly/Monthly Progress, the career databases and the reading system |
| P2 | What is a "major task"? | Due that day, Status Next or Doing, not Recurring (rule R1) |
| P3 | Moving a task to a new date | Its Week and Month relations move too; missing period pages are created first (R7) |
| P4 | A moved task that had no Week/Month relation | Linked as well, so every moved task ends up linked |
| P5 | Weekly review length | 20–25 minutes (R9) |
| P6 | Monthly review | Career-only, covering the calendar month (R10) |
| P7 | Backfilling `Completed` on old Done tasks | No; LifeOS sets `Completed` only on tasks it marks done |
| P8 | New habit row titles | `dd/mm/yyyy`; legacy `d/m/yy` titles are still recognised |
| P9 | Sending workspace data to a free-tier LLM | Acceptable |
| P10 | Testing writes | Only against a sandbox copy of the databases, never the real workspace |

## Implementation

### Workflows and approvals (2026-11-01)

| # | Topic | Decision |
| --- | --- | --- |
| I1 | Answering a paused run | One endpoint, `POST /runs/{id}/resume`, for every pause; resuming is a compare-and-set on the run status, so two answers can't both advance a run |
| I2 | Proposals left undecided in an approval step | Rejected, so nothing lingers half-decided |
| I3 | An approval not executed before its TTL (for example while writes were off) | Expires; the executor refuses it rather than writing days later |
| I4 | Editing the Due date of a move | Allowed only within the same week and month, because the Week/Month links were resolved for the original date |
| I5 | Models | Pinned chains in configuration: fast `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`; smart `gemini-3.5-flash` → `gemini-3.5-flash-lite`, chosen from measured calls |
| I6 | Habits `Screen Time` unit | Hours, like Sleep |
| I7 | Model output shown or written | Reference codes (T1, D2) are replaced by task titles first |
| I8 | Proposal titles | `action_proposals.target_title` stores the task or page name, so approval screens can name it without workflow state |

### Web app and API (2026-11-01)

| # | Topic | Decision |
| --- | --- | --- |
| I9 | Sessions | Server-side; only a SHA-256 of the cookie token is stored, with a per-session CSRF token sent as `X-CSRF-Token`. Logout and password resets revoke sessions |
| I10 | Sign-in rate limit | 5 attempts a minute, in memory (one process, one owner) |
| I11 | Chat | Structured steps (one tool call or the answer per step) on the same router as the workflows, instead of provider-native function calling: same fallback chain, budget, mock and validation |
| I12 | Where proposals are decided | A waiting run's proposals through `/runs/{id}/resume` (409 elsewhere); standalone ones (chat) through `/proposals/{id}/approve|reject`. `GET /approvals` lists both |
| I13 | Streaming through a proxy | The chat stream sets `Cache-Control: no-transform`; without it, the production Next.js server gzips and buffers the whole stream |
| I14 | Page gating | Pages render immediately with their own loading states, and a signed-out visitor is redirected to `/login`. Authorization stays in the backend |
| I15 | "Today" in the UI | Comes from the backend, in the owner's timezone, never from the browser clock |

### Deployment (2026-11-01)

| # | Topic | Decision |
| --- | --- | --- |
| I16 | Scheduled-job authentication | HMAC-SHA256 over `timestamp.job.body` with a 5-minute window; the signature names the job, so it can't be replayed later or reused for another job |
| I17 | Scheduled-job idempotency | `cron_runs` has one row per (job, local date); a failed run releases its row so a retry can run |
| I18 | Database migrations | Run at container start; the free tier has no pre-deploy step, and a single instance means no race |
| I19 | Database URL | Accepts a standard libpq URL (as Neon provides it) or an asyncpg URL; the app derives the asyncpg form for SQLAlchemy and the libpq form for the LangGraph checkpointer |
| I20 | Production mode | `LIFEOS_ENV=production` hides the OpenAPI schema and docs and always uses Secure cookies |
| I21 | Deploys | Render deploys only after CI passes (`autoDeployTrigger: checksPass`); the API starts with writes off until the smoke test passes |

## Open questions

- Does streaming chat pass through Vercel's rewrite without buffering? It does through the
  Next.js server; `scripts/smoke_test.py --chat` checks it on the deployed app.
