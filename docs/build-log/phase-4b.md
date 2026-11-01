# Phase 4b — LLM layer, workflows and API

Date: 2026-11-01.

## What was built

| Module | Purpose |
| --- | --- |
| `app/llm/` | Provider-neutral structured-output interface; Gemini (`google-genai`), a scripted mock for tests, and a router with a pinned model chain per tier, a 30 s timeout, a daily token budget and usage accounting |
| `app/services/rules.py` | Domain rules as pure functions: major tasks and free slots (R1), open (R2), fallback order "P0, then P1, then earliest Due", the overdue queue (R6), planning candidates, day allocation, weekly metrics (R5), habit summaries |
| `app/services/periods.py` | Finds the Weekly/Monthly Progress page for a date or describes the page to create (R7) |
| `app/services/context.py` | Compact prompt context: short references (T1, B1…) instead of page IDs, sanitised one-line titles, a `<notion_data>` block the system prompt marks as data |
| `app/agents/` | Planner (which tasks for a day), check-in parser (free text → done/partial/habits/sleep/screen time/pages/new tasks), weekly reviewer (narrative + carry/cancel advice). Every answer is validated in code; each agent has a deterministic fallback |
| `app/workflows/` | LangGraph graphs with `interrupt()` pauses: `evening_checkin`, `morning_briefing`, `weekly_review`. A two-phase proposal builder does the Notion reads first, then stores all proposals in one transaction |
| `app/workflows/runner.py` | Start / resume / get runs; keeps `agent_runs` (status, waiting request, summary, tokens, latency) in step with the graph checkpoint |
| `app/api/` | FastAPI: `/health`, `/health/ready`, `/workflows/{name}/runs`, `/runs/{id}`, `/runs/{id}/resume`, `/proposals`, `/tasks`. Bearer token; an unset token refuses everything (fail closed); one error shape |
| `scripts/run_workflow.py` | Drive a workflow from the terminal; `--writes` only works with the sandbox profile |
| New action kinds | `week.close`, `month.close` (Review text + Closed), `book.update_progress` |

## Workflows

```
evening_checkin   load → ⏸ "what did you finish?" → parse → plan tomorrow → propose → ⏸ approve → execute → summary
morning_briefing  load → plan (trim a day over 3 / fill an empty day / leave it) → propose → ⏸ approve → execute → brief
weekly_review     load → AI draft → ⏸ decisions (overdue + Inbox) → propose → ⏸ approve → execute → summary
```

Rules enforced in code, whatever the model says:

- R1: a plan naming an unknown task, a duplicate, or more tasks than free slots is rejected and
  replaced by the rule-based order.
- R6: the nightly and morning plans never touch overdue tasks. Only the weekly review proposes
  carry forward / reschedule / cancel, and the owner decides each one (defaults are the model's
  recommendation, dates are allocated by code into days with a free slot).
- R7: every moved task gets Week and Month links. A missing page becomes a `week.create` /
  `month.create` proposal that the move depends on.
- Approval: proposals not approved in a run are rejected; an approval that isn't executed
  before its TTL expires instead of executing later.
- Owner text is preserved: check-in notes are appended to the day's existing Notes; the weekly
  Review is appended below whatever the owner already wrote.

Without a model (no key, quota spent, every model failing), the check-in matches task titles
quoted in the reply, planning uses "P0, then P1, then earliest Due", and the review is built from
the numbers. Each of these is labelled in the run's warnings.

## Verification

| Check | Result |
| --- | --- |
| Unit + database tests | **132 passed**: rules, router fallback and budget, agent validation, prompt sanitising, new action kinds, the R7 edit guard, approval expiry, end-to-end workflow runs (FakeNotion + MockLLM + checkpointer + Postgres), API auth and flows |
| Live tests (`LIFEOS_LIVE_TESTS=1`) | 5 passed (Phase 4a read checks and sandbox write cycle still green) |
| Live dry run: evening check-in on Life OS, writes off | Gemini parsed the reply; no proposals, correctly: every open task is due today or overdue, and overdue tasks aren't moved nightly (R6) |
| Live dry run: morning briefing | Today's priorities, the overdue count, days without a scorecard and the week's done count all matched Notion |
| Live dry run: weekly review, stopped at the decision step | Real metrics, a narrative and a recommendation for every overdue task. It exposed reference codes leaking into the prose; fixed (decision I7) |
| Full loop on the sandbox, writes on | 5 proposals (mark done, scorecard row, `week.create`, `month.create`, carry the backlog task to 2 Nov) → approved → **5 verified**, then read back independently: Due, Week and Month links, `01/11/2026` habit row, notes |
| API smoke test with uvicorn | Lifespan starts the Postgres checkpointer; `/health` public, 401 without the token, `/tasks?view=today` returns the live tasks, a morning run completes over HTTP |

Measured model latency on the free tier was uneven: 1.4–4 s per call when choosing models,
but 18 s for one check-in parse and 12 s for the weekly review (2,272 output tokens including
thinking) during the dry runs. The 30 s timeout and fallback chain cover this; Phase 5 adds
latency and cost metrics.

## Next

Phase 6 (minimal frontend): Command Center with the check-in, approvals drawer and the weekly
decisions screen on top of these endpoints; session login alongside the bearer token. Chat with
the Chief of Staff arrives with that UI.
