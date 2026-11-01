# Phase 3 — Notion integration (read-only): results

Date: 2026-11-01. No writes to Notion were made by LifeOS code in this phase.

## What was built

| Module | Purpose |
| --- | --- |
| `app/notion/client.py` | Async client: auth + version headers, token-bucket rate limit (2.5 req/s), retries with full-jitter backoff on 429/5xx/network errors (honours `Retry-After`), 60 s deadline, cursor pagination, full relation fetch. No write methods |
| `app/notion/errors.py` | Typed errors; only `retryable` ones are retried |
| `app/notion/codecs.py` | Pure decoding of Notion property JSON |
| `app/notion/mapping.py` + `config/notion_mapping.yaml` | Logical field names → exact Notion names and option values; `live` / `sandbox` profiles |
| `app/notion/schema.py` | Drift detection: missing property, wrong type, missing option; stable schema hash |
| `app/notion/queries.py` | Filters rebuilt from the saved Notion views |
| `app/notion/repositories.py` | Tasks, Goals, Habits, Weekly/Monthly Progress, Books → domain models |
| `app/services/calendar.py` | Mon–Sun weeks, calendar months (Asia/Kolkata), `dd/mm/yyyy` habit titles with legacy parsing |
| `scripts/notion_check.py` | Read-only end-to-end check against the live workspace or sandbox |

## Verification

| Check | Result |
| --- | --- |
| Unit tests (no network) | 52 passed |
| Live read-only tests (`LIFEOS_LIVE_TESTS=1`) | 4 passed |
| Schema drift, live (6 data sources) | None |
| Schema drift, sandbox (4 data sources) | None; schema hashes identical to the live databases |
| Connector `collection://` IDs vs API data-source IDs | Identical |
| Today, Overdue / Carry Forward, All Active, Inbox, Next 7 Days | Same tasks **in the same order** as the saved Notion views |
| Notion `Week Key` vs LifeOS `week_key(Due)` | 49 dated tasks, 0 conflicts. Format is `YYYY-Www` (ISO week) |
| Notion `Month Key` | **The formula returns boolean `false` for every task** (56/56), so it carries no information. LifeOS ignores non-text formula values and derives the month from Due |
| Requests for a full check | 18 Notion calls, 0 retries |

## Findings for the owner

- The `Month Key` formula in Tasks is broken in Notion itself. The Monthly History view shows an
  empty checkbox in that column. Fixing it is a Notion change and needs owner approval.
- 43 tasks are linked to a Monthly Progress page and some to a Weekly Progress page; linking
  remains optional in the owner's rules, but LifeOS will link carried-forward tasks (Q3, Q8).

## Problems hit and how they were solved

- **Integration saw nothing at first** (`search` returned 0 results). Both the connector and the
  integration were in the same workspace, so the pages simply weren't connected yet; it resolved
  once the Connections step took effect.
- **zsh `path` variable**: a shell loop variable named `path` overwrote `$PATH` (zsh ties `path`
  to `PATH`), so `curl` and `python3` disappeared mid-script. Renamed the variable.
- **Notion MCP SQL quota**: the connector's SQL allowance ran out during discovery; saved-view
  queries still worked and were used for the view comparison.
