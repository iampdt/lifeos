# Phase 6 (minimal) — Owner login and the web app

Date: 2026-11-01.

## What was built

**Backend**

| Module | Purpose |
| --- | --- |
| `alembic/versions/0002_auth_and_titles.py` | `users`, `sessions` (SHA-256 of the token, CSRF token, expiry, revocation), `action_proposals.target_title` |
| `app/auth/` | argon2id passwords (constant-time path for unknown emails), server-side sessions, sliding-window login limiter |
| `app/api/security.py` | One guard for every route: browser session (+ `X-CSRF-Token` on changes) or the bearer token |
| `app/agents/chat.py` | Chief of Staff chat: read tools (today, overdue, next 7 days, Inbox, week summary, goals, reading) and propose tools (mark done, reschedule, set status, create task). 6 steps and 90 s per turn; R1 enforced; Notion data wrapped as untrusted |
| `app/api/routes.py` | `/auth/login|logout|me`, `/runs`, `/approvals`, `/proposals/{id}/approve|reject`, `/proposals/approve-batch`, `/chat` (SSE), `/settings`, `/notion/status` |
| `scripts/create_owner.py` | Create the owner or reset the password (getpass; a reset signs out every session) |

**Frontend** (`frontend/`): Next.js 16.4 (App Router, Cache Components), React 19, Tailwind 4,
shadcn/ui on Base UI, TanStack Query.

| Page | Contents |
| --- | --- |
| `/login` | Sign-in |
| `/` Command Center | Today (major-task count, overdue flagged for Sunday), evening check-in and morning briefing in a dialog, recent runs (continue a waiting one), streaming chat, approvals drawer with a count |
| `/weekly` | Week metrics, AI or rule-based draft, one decision per overdue task (carry forward / reschedule / cancel / later), Inbox triage, the owner's note, then the approval list |
| `/settings` | Write switch, timezone, permission modes, model chains and tokens used today, Notion connection with schema drift per data source |

Approval cards show before → after for every action kind. A move selects the page it depends on;
cancelling a task needs an explicit tick; anything not approved is rejected.

## Verification

| Check | Result |
| --- | --- |
| Backend tests | **144 passed** (new: passwords, limiter, login cookie flags, CSRF, logout revocation by replaying the old cookie, session expiry, rate limit, chat SSE with proposals and approval, R1 and unknown-reference refusals, model-down chat, run-owned proposals refused outside their run, settings and Notion status) |
| `alembic check` | Models and migrations match (LangGraph's checkpoint tables are excluded) |
| Frontend | `eslint` clean, `tsc` clean, `next build` passes; all five routes prerender a static shell |
| Through the Next proxy | Login sets `HttpOnly; SameSite=Lax` (+ `Secure` by default); unauthenticated → 401; a change without the CSRF header → 403 |
| Chat streaming | Before the fix, all events arrived together after 3.3 s (gzip buffering in `next start`). With `Cache-Control: no-transform`: `run` 0.01 s, `tool` 1.7 s, answer 3.5 s |
| In Chrome, sandbox, writes on | Sign-in → evening check-in (ticks + free text) → 3 proposals → approved → **3 verified**; chat "move the overdue example to Tuesday" → proposal → approved from the drawer → **verified**; weekly review → Inbox item made Next with a date → **2 verified**; settings shows the real connection and matching schemas |

Bugs the browser run found and fixed:

1. Chat crashed the page: newer Chrome returns a Promise from `scrollIntoView`, and an effect
   with an expression body returned it to React as a "cleanup" function.
2. Next.js flagged the signed-in pages as dropped from rendering; pages now render immediately
   with their own loading states instead of waiting behind the session check.
3. The Today card read the browser clock during render (frozen into the prerender, and the
   wrong timezone when travelling); "today" now comes from the backend in Asia/Kolkata.

Not yet verified on a device: the layout at phone width. It is built to stack on narrow
screens (one column below the `lg` breakpoint, wrapping header, stacked decision rows).

## More pages (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `app/api/pages.py` | `GET /goals` (progress, state, open tasks per goal), `GET /reading` (the queue with pages read), `GET /history` (each day's briefing and check-in); an unmapped module is reported as unavailable |
| `/tasks` | The task views with a filter |
| `/goals` | Goals and reading; a module the workspace doesn't map points to setup |
| `/history` | Day by day, the morning plan beside the evening check-in, each openable in full |
| Navigation | Seven pages; on a phone the menu takes its own row and scrolls sideways |
| Wording | Overdue and unfinished tasks point to "the weekly review" rather than a weekday, and the Today card uses the workspace's own number of major tasks |
| Demo | Some sample tasks are linked to goals, which now have success metrics |

| Check | Result |
| --- | --- |
| Backend tests | 267 passed. New: goals ordered by state with capped progress and their open tasks in due order; the reading queue in progress first; unmapped modules; history pairing a day's runs, a late check-in counted for its own day, a waiting run dated by its start, other routines and accounts left out |
| Browser | Tasks, Goals and History checked against the local demo at desktop width and at 390 px |

## Notes

- `npm audit` reports 19 moderate and 2 low advisories, all from `minimatch` (via ESLint and the
  shadcn CLI's `ts-morph`) and `deepmerge` (shadcn registry). These are build and lint tools; none
  of it is bundled for the browser (`shadcn` is a dev dependency used only for a CSS import at
  build time). `npm audit fix --force` would downgrade eslint-config-next to 12, ESLint to 4 and
  shadcn to 1, so they stay until upstream releases fixes.
- The browser tests used a throwaway account in the test database.

## Next

Phase 8 (minimal deployment): Docker images, Vercel + Render + Neon free tiers, CI, signed
scheduled jobs, and a smoke test.
