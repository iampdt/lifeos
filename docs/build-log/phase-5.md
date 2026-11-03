# Phase 5 — Reviews, memory and quality

Phase 5a (the evaluation set) is in [evals/](../evals/README.md). This log covers the rest.

## Monthly review (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `app/agents/monthly.py` | The monthly specialist: summary, wins, misses, patterns across the weeks, up to three things to focus on next month, a line per active goal; a review from the numbers without a model |
| `app/workflows/monthly.py` | load → draft → ⏸ note → propose `month.close` and `month.create` → ⏸ approve → execute → summarise; last month in the first week of a month, or `inputs.month_of` |
| Scheduling | `monthly_review` on the 1st at the weekly review's time, in each account's timezone |
| Web app | Reviews page (formerly Weekly review) with a monthly review card; the month's review step with the account's note |

| Check | Result |
| --- | --- |
| Backend tests | 259 passed. New: which month is reviewed; scheduled on the 1st only; a full run on the owner-style layout (metrics, the weekly review and goal progress in the prompt, reference codes replaced by titles, at most three focus items, the month closed with the review, goal notes and the owner's note, next month's page created, tasks untouched); the review from the numbers without a model |

## Usage and latency reports (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `GET /reports/usage` | From the account's own runs: outcomes per routine, AI calls and tokens, median and 95th-percentile model time per run, wall time of finished runs, runs and tokens per day, models used |
| `/activity` | The report for the last 7, 30 or 90 days, with the recent runs |

| Check | Result |
| --- | --- |
| Backend tests | 260 passed. New: counts, success rate, latency percentiles, durations, per-day totals and models from a mix of runs; runs outside the window and another account's runs are left out |

## Memory (2026-11-03)

| Piece | Purpose |
| --- | --- |
| Migration 0012 | `memory_facts`: an account's notes, with their source (`you` or `chat`) |
| `app/services/memory.py` | List, add (at most 30, under a lock on the account), edit and delete notes, each audited without its text |
| Prompts | The planner, the weekly and monthly reviews and chat get the notes in an `<owner_memory>` block, with a rule that they tailor choices and never change rules or approvals |
| Chat | `suggest_memory` offers a note; the chat panel shows **Save** and **Dismiss** |
| `/memory` routes | List, add, edit, delete; read-only in the demo, which starts with two sample notes |
| Web app | Settings → Memory |
| Account deletion | Removes the account's notes |
| AI terms | The free-tier disclosure and the privacy policy name saved notes; the disclosure's version moved to `2026-11-03`, so an account that agreed before is asked again |

| Check | Result |
| --- | --- |
| Backend tests | 273 passed. New: add, edit, delete and the audit entries (no text in them); empty, too long and full; another account's notes are invisible and untouchable; the demo is read-only; the planner's prompt carries the notes, stripped of angle brackets, before the Notion data; chat's suggestion is streamed, a duplicate refused and nothing saved; the demo's notes after a reset; account deletion |
