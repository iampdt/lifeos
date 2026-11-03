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
