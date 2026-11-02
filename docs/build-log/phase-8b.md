# Phase 8b — Public demo

Date: 2026-11-02.

A second deployment that anyone can try without a Notion account, for the portfolio.

## What was built

| Piece | Purpose |
| --- | --- |
| `app/demo/notion.py` | The in-memory Notion client (moved from the tests, which now subclass it with failure hooks): same request and response shapes, filters and verification as the real API |
| `app/demo/seed.py` | A believable week for a software engineer, computed from today: three major tasks due today, two overdue, upcoming and undated work, Inbox items, done tasks, six days of scorecards, active goals, books in progress |
| `app/demo/setup.py` | Demo services, workspace and table reset, the shared demo account, and a guard that refuses any database whose name doesn't contain `demo` |
| `GET /meta`, `POST /auth/demo`, `POST /demo/reset` | Public mode check, password-less demo session, rate-limited reset |
| Web app | "Try the demo" sign-in with suggestions of what to try, a demo banner with **Reset demo**, task rows without Notion links |
| `render.yaml` | `lifeos-demo-api` service with a smaller daily token budget |

## Verification

| Check | Result |
| --- | --- |
| Backend tests | 167 passed, including the seed's shape (R1 load, overdue, Inbox, habits, goals, books), reseeding, the database-name guard, demo sign-in, a full check-in on the sample workspace, reset and its rate limit, and demo endpoints returning 404 outside demo mode |
| Local demo in the browser | **Try the demo** → sample workspace (3 of 3 major tasks, 2 overdue) → evening check-in with ticks and free text → the model proposed the finished task, a scorecard, reading progress, an Inbox item and two tasks for tomorrow's free slots → approved → 6 verified |
| Phone width | The cards overflowed a 390 px screen: grids had no base column template, so the single column grew to its widest content. Fixed with `grid-cols-1` below the breakpoint |

Also cleaned up: example prompts in the chat and check-in now use generic tasks instead of
workspace-specific ones.

## Next

Deploy it (see [deployment.md](../deployment.md#public-demo)) and link it from the README.
