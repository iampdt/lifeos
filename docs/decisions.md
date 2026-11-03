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
| P11–P14 | Multi-user (2026-11-02) | Google-only sign-in, Notion OAuth, a generic workspace model, free-tier AI with a shared allowance plus users' own keys. See [multi-user.md](multi-user.md) |

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
| I22 | Streaming through Vercel | Verified on 2026-11-02: with `Cache-Control: no-transform`, chat events pass through Vercel's external rewrite as they happen (first event at 0.2 s, answer at 2.8 s) |
| I23 | Pasted secrets | The API ignores whitespace around the configured bearer token and cron secret, since values pasted into dashboards often carry a stray space or newline |

### Evaluation and demo (2026-11-02)

| # | Topic | Decision |
| --- | --- | --- |
| I24 | Measuring the agents | A synthetic labelled set (check-in parsing, day planning, prompt-injection cases) scored by code, run per model on demand; failed calls are errors, not wrong answers |
| I25 | Model chain | Confirmed by the evaluation: `gemini-3.5-flash-lite` first (all cases passed, fastest), `gemini-3.1-flash-lite` as fallback |
| I26 | Public demo | The real application on an in-memory Notion seeded relative to today, rather than screenshots or a mocked frontend, so every feature works and is verified the same way |
| I27 | Demo isolation | One shared workspace with a visible reset and a rebuild on every start, instead of per-visitor workspaces; demo mode refuses any database not named for the demo, because it resets it |
| I28 | Demo secrets | The demo copies the main service's database URL and model key through the Blueprint (`fromService`) and swaps the database name (`LIFEOS_DEMO_DATABASE`), so no secret is copied by hand |
| I29 | Injection found by the evaluation | `gemini-3.1-flash-lite` reported every habit as done because a book title claimed so. The check-in prompt now states that facts come only from the owner's message, never from titles; both production models then passed every case |

### Multi-user foundations (2026-11-02)

| # | Topic | Decision |
| --- | --- | --- |
| I30 | Google sign-in | Authorisation code with PKCE, state and nonce, verified locally against Google's keys; an existing account is linked by email on first sign-in; new accounts only while `LIFEOS_OPEN_SIGNUP` is on |
| I31 | Account isolation | `user_id` on every owned row; the runner, proposal service and executor are built for one account and filter every query by it, so isolation doesn't depend on each route remembering a filter |
| I32 | Workspace before Notion OAuth | The environment's workspace belongs to the owner account (`is_owner`, at most one), which the bearer token and scheduled jobs act as; other accounts have no workspace until they connect one |
| I33 | Per-account contexts | Each account's dependencies and compiled graphs are built on first use and cached, rather than passing dependencies through graph config, so the graph code stays unchanged |
| I34 | Deleting audit entries | Account deletion purges that account's entries through `purge_user_audit`, which the append-only trigger allows only for the account being purged and only during the call |
| I35 | Token storage | Notion tokens encrypted with Fernet (authenticated, so a wrong key or tampering fails loudly); `LIFEOS_ENCRYPTION_KEY` accepts several keys for rotation |
| I36 | Token refresh | Refresh on a 401 (and before a stated expiry), under a row lock: Notion rotates refresh tokens, so two concurrent refreshes would lock the account out. Only Notion refusing the refresh asks the user to reconnect |
| I37 | Connection attempts | Tied to the account that started them, checked against the session on return, so a forged callback can't attach a workspace to someone else's account |
| I38 | Generic habits | In the habits data source every checkbox is a habit and every number a metric, with optional units and descriptions; the check-in's output schema is built from them per workspace |
| I39 | Optional modules | Only Tasks is required. Week and month links, Inbox and cancelled statuses, priority, habits, goals and reading are used where mapped and skipped otherwise |
| I40 | Onboarding | Suggestions come from names and property types only and are always shown for confirmation; the saved mapping is checked against the live data sources. A starter template whose names the suggestions match completely makes setup a review |
| I41 | AI per account | Own key first, then the service key within a per-account daily allowance and the overall cap, then rules. Usage is counted in Postgres per UTC day, so a restart of the free host doesn't reset anyone's allowance |
| I42 | Free-tier consent | The service key sends an account's data only after it agrees to a versioned disclosure of the Gemini free-tier terms; a changed disclosure asks again. Own keys follow the key owner's own terms |
| I43 | Own keys | Checked with Google before saving (a refused key is a 400 naming the key, or 401/403; quota isn't the key's fault), stored encrypted, shown only as the last four characters |
| I44 | Scheduling many accounts | One hourly signed tick instead of fixed per-routine schedules: routines are due for four hours in each account's timezone, the oldest run first up to a cap, and idempotency per account and local day makes late, retried or overlapping ticks harmless. Hourly keeps a private repository inside GitHub's free minutes |
| I45 | Drift | Checked daily; a module that no longer matches is left out of the workspace (so nothing reads or writes it) until the mapping is saved again, rather than failing runs one by one |
| I46 | The owner's move | An account workspace takes precedence over the environment's for the owner too, so moving is one saved mapping (the environment's, checked against the owner's connection) and `NOTION_TOKEN` can then go; the owner keeps the deployment's AI key |
| I47 | Privacy and terms | Written from what the code stores and sends, not from a template; the AI section matches the consent disclosure |
| I48 | Late schedules | Opening the app starts the account's due routines that haven't run, claimed like the tick's, because GitHub's schedules ran up to 8 hours late; the tick stays the source of truth for accounts that don't open the app |
| I49 | Editable permission modes | Per account, stored as the changes from `approve`, so a new action kind needs approval until chosen otherwise; `auto` stays unavailable until an allowlist of safe actions and an undo exist. `off` is applied where proposals are built, so the run explains what was left out |

