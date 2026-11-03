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

## Knowledge index (2026-11-03)

| Piece | Purpose |
| --- | --- |
| Migration 0013 | The `vector` extension; `knowledge_pages`; `knowledge_chunks` with a generated `tsvector` (GIN index) and a `vector(768)` embedding |
| `app/knowledge/extract.py` | Blocks to passages: headings as a path, lists, to-dos, toggles, quotes, callouts, code and table rows; nested blocks three levels deep; long text cut at sentence ends; limits on blocks, nested reads and passages |
| `app/knowledge/index.py` | Add, refresh (rebuilt only when Notion's last edit changed), remove; embeddings in batches of 100, or keyword search alone when AI can't make them; hybrid search with reciprocal rank fusion |
| LLM layer | `embed` on the provider, the router (one model, tokens estimated at four characters each) and an account's own-key-then-allowance routing; the mock embeds as a bag of words |
| `app/agents/knowledge.py` | Answers from the passages with `[S1]` citations; unknown references dropped, the rest renumbered and attached; the closest passages when no model answers |
| Chat | `search_knowledge`; the pages an answer names are linked after it |
| API and web app | `/knowledge` routes and page: ask with numbered sources, add pages by title, refresh, remove |
| Demo | Three sample documents, indexed on every reset; read-only |
| Privacy | The index is a copy of page text; disconnecting Notion or deleting the account deletes it |

| Check | Result |
| --- | --- |
| Backend tests | 286 passed. New: passages grouped under headings, long text split without loss, deep nesting and long pages cut and flagged, table rows; pages found, added, listed, removed; refresh rebuilds only after a change; duplicates refresh; the page limit; empty and unshared pages; cited answers (unknown reference dropped, repeats numbered once) with the usage report counting the run; keyword search and passages without AI; isolation between accounts; the read-only demo; chat's search and its linked sources; disconnecting Notion and deleting the account clear the index |
| Live | `gemini-embedding-001` with the deployment's key: unit-length 768-dimension vectors in input order; a question about "stuck payments" ranks the outbox-worker passage first (cosine 0.69 against 0.53). In the local demo, a question returned a one-sentence answer citing the on-call passage, and chat answered from the reading notes |
