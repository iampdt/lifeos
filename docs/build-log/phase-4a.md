# Phase 4a — Approval and execution pipeline

Date: 2026-11-01.

## What was built

| Module | Purpose |
| --- | --- |
| `docker-compose.yml`, `backend/db/init/` | Local Postgres 16 with pgvector; separate `lifeos_test` database |
| `app/db/models.py`, `alembic/versions/0001_initial.py` | `agent_runs`, `action_proposals`, `action_executions`, `audit_log`; a trigger makes `audit_log` append-only |
| `app/notion/encoders.py` | Python values → Notion write JSON (2,000-character text chunks; computed types refused) |
| `app/notion/client.py` | `update_page` (idempotent, normal retries) and `create_page` (retried only on 429, because a timeout may mean the page exists) |
| `app/execution/actions.py` | Seven action kinds with strict payloads (`extra="forbid"`), `build_plan`, value normalisation |
| `app/permissions/policy.py` | Field-level write allowlist; modes off / suggest / approve / auto (auto reserved) |
| `app/execution/proposals.py` | Propose (idempotency key per kind + payload + local day), approve with validated edits, reject, retry, expire |
| `app/execution/executor.py` | Kill switch, dependency ordering, stale detection, find-or-create, write, read-after-write verification, audit |

## Outcomes the executor can report

| Outcome | Meaning | Proposal status |
| --- | --- | --- |
| `verified` | Written and read back exactly | executed |
| `noop` | Notion already had the target values (or the page to create already existed) | executed |
| `mismatch` | Written, but the read-back differs | executed (flagged) |
| `stale` | The page changed in Notion after the proposal was made; nothing written | stale |
| `failed` | Notion error, allowlist refusal or failed dependency | failed (can be retried) |
| `blocked` | Kill switch off; nothing written | stays approved |
| `skipped` | Not approved, or waiting for a dependency | unchanged |

## Verification

| Check | Result |
| --- | --- |
| Unit + database tests (`pytest`) | 89 passed (live tests skipped by default) |
| Migration vs models (`alembic check`) | No differences |
| Audit log `UPDATE` / `DELETE` | Rejected by the database |
| Live tests (`LIFEOS_LIVE_TESTS=1`) | 5 passed: read checks on Life OS + a full write cycle on the sandbox |
| Sandbox cycle | create week → create month → create task → carry forward (Due + Week + Month moved) → mark done → habit upsert, all `verified` |
| Second sandbox run | Week, month and habit row reused (still 1 of each); only the new test task added |

Covered by tests: duplicate proposals collapse; same suggestion on a later day is new; edits are
re-validated and can't retarget; expired, rejected, suggest-mode and off-mode proposals can't
execute; stale pages are left untouched; already-applied changes are no-ops; a timeout after a
create followed by a retry produces exactly one page; a write that doesn't stick is a mismatch;
allowlist violations are refused; the kill switch blocks everything; dependencies run first and
a rejected dependency fails its dependants; the hand-typed `1/11/26` habit row is found and keeps
its title, while new rows are `dd/mm/yyyy`.

## Workspace change

- Tasks → `Month Key` formula replaced with `if(empty(prop("Due")), "", formatDate(prop("Due"), "YYYY-MM"))`.
  Verified: matches LifeOS's month on all 49 dated tasks.

## Next (Phase 4b)

LLM provider layer (Gemini + mock, fallback, budgets), the LangGraph workflows (evening check-in
with nightly plan, morning briefing, minimal weekly review) producing these proposals, and the
FastAPI endpoints for proposals and runs.
