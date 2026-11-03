# API

Base path `/api/v1`. JSON in and out, except chat, which streams server-sent events.

## Authentication

| Method | Used by | How |
| --- | --- | --- |
| Session cookie | The web app | `POST /auth/login` sets `lifeos_session` (`HttpOnly; SameSite=Lax; Secure` in production). Every request that changes state must also send `X-CSRF-Token` with the token returned by login or `/auth/me` |
| Bearer token | Scripts | `Authorization: Bearer <LIFEOS_API_TOKEN>`, acting as the owner account. Refused when the token isn't configured or there is no owner account |
| HMAC signature | Scheduled jobs | Only for `/internal/cron/{job}` (see below) |

Everything except health, `/meta`, sign-in and `/internal/cron/{job}` requires a session or the
bearer token. Every route works on the caller's own runs, proposals and tasks: another account's
IDs behave as if they didn't exist. Routes that use a workspace return 409 `no_workspace` for an
account without one.

## Errors

Every error has the same shape:

```json
{"error": {"code": "csrf_failed", "message": "missing or invalid X-CSRF-Token header", "retryable": false, "request_id": null}}
```

| Status | Codes |
| --- | --- |
| 401 | `unauthorized`, `invalid_credentials`, `bad_signature` |
| 403 | `csrf_failed`, `password_sign_in_disabled`, `session_required`, `demo_account`, `environment_workspace`, `deployment_key` |
| 404 | `unknown_workflow`, `unknown_job`, `unknown_note`, `unknown_page`, `not_shared`, `http_error` |
| 409 | `run_state` (run not found or not waiting), `run_owned` (decide it through its run), `no_workspace`, `notion_reconnect`, `not_connected`, `disclosure_changed` |
| 422 | `invalid_request`, `confirmation_mismatch`, `invalid_mapping` (with `details`), `key_rejected` |
| 429 | `rate_limited` (sign-in) |
| 502 | `notion_<code>`, `run_failed` |
| 503 | `auth_not_configured`, `cron_not_configured`, `no_owner`, `key_check_failed` |

## Endpoints

### Health and session

| Method and path | Description |
| --- | --- |
| `GET /health` | Liveness, public |
| `GET /meta` | Public: `{demo, google, password, contact}`: which sign-in to offer, and the contact address for the privacy and terms pages |
| `POST /auth/demo` | Demo deployments only (404 otherwise): a session on the shared demo account, no password. Returns `{email, csrf_token}` |
| `GET /health/ready` | Database check; also returns the Notion profile, write switch and whether a model is configured |
| `GET /auth/google/start` | Starts Google sign-in: records a single-use attempt (state, nonce, PKCE verifier; 10 minutes) and redirects to Google. 404 when Google isn't configured |
| `GET /auth/google/callback` | Google's redirect target. On success sets the cookie and redirects to `/`; otherwise redirects to `/login?error=<code>`: `cancelled`, `expired`, `exchange_failed`, `invalid_token`, `email_unverified`, `signup_closed`, `account_conflict` |
| `POST /auth/login` | Body `{email, password}`. Returns `{email, csrf_token}` and sets the cookie. Refused with 403 `password_sign_in_disabled` when Google sign-in is configured |
| `GET /auth/me` | `{via, email, name, csrf_token, owner, workspace}`; `workspace` is false until the account has a workspace |
| `POST /account/delete` | Body `{confirm}`: the account's email address. Deletes the account and everything stored for it (its Notion connection, revoked at Notion too; sessions, runs and their checkpoints, proposals, executions, scheduled-job records, audit entries); Notion content is never touched. Browser sessions only; refused for the demo account |
| `POST /auth/logout` | Revokes the session and clears the cookie |

### Workflow runs

| Method and path | Description |
| --- | --- |
| `POST /workflows/{name}/runs` | Start `evening_checkin`, `morning_briefing`, `weekly_review` or `monthly_review`. Body `{inputs?}`. Returns a run (201) |
| `GET /runs?status=&limit=` | Recent runs, newest first (without request or summary bodies) |
| `GET /runs/{id}` | One run |
| `POST /runs/{id}/resume` | Body `{answer}`: the reply to the run's `waiting_for` request |

A run:

```json
{
  "id": "…", "workflow": "evening_checkin", "status": "waiting_approval",
  "waiting_for": {"type": "approval", "proposals": ["…"], "writes_enabled": false},
  "summary": null, "error": null,
  "usage": {"model": "gemini-3.5-flash-lite", "input_tokens": 546, "output_tokens": 150, "llm_latency_ms": 1400, "llm_calls": 1},
  "trigger": "manual", "started_at": "…", "finished_at": null
}
```

`status` is `running`, `waiting_input`, `waiting_approval`, `completed` or `failed`.

Answers by request type:

| `waiting_for.type` | `answer` |
| --- | --- |
| `question` (evening check-in) | `{"text": "…"}` and/or structured fields: `done_refs`, `partial_refs`, `habits` (`{key: bool}`), `metrics` (`{key: number}`), `pages_read` (`[{ref, pages}]`), `learned`, `new_tasks`. The question lists the workspace's habits and metrics (`habits.fields`, `habits.metrics`) with their keys; it's `null` without a habits module. Structured fields win over what the model extracts |
| `decisions` (weekly review) | `{"overdue": {"T1": {"decision": "carry_forward" \| "reschedule" \| "cancel" \| "skip", "due": "YYYY-MM-DD"}}, "inbox": {"I1": {"action": "keep" \| "next" \| "cancel", "due": "…"}}, "note": "…"}`. Anything omitted takes the recommendation (Inbox items are kept) |
| `month_review` (monthly review) | `{"note": "…"}` (optional), added to the month's Review |
| `approval` | `{"approve": "all"}` or `{"approve": ["<proposal id>", …], "edits": {"<id>": {"new_due": "…"}}, "note": "…"}`. Everything not approved is rejected |

Workflow inputs: `evening_checkin` takes `plan_tomorrow` (default `true`); `morning_briefing`
takes `plan_if_empty` (default `true`); `weekly_review` takes `week_of` (a date in the week to
review; default today); `monthly_review` takes `month_of` (a date in the month; default last
month during the first week of a month, otherwise this month).

### Approvals and proposals

| Method and path | Description |
| --- | --- |
| `GET /approvals` | `{runs: [{run_id, workflow, started_at, proposals}], standalone: [proposal]}`: everything waiting for a decision |
| `GET /proposals?status=pending&limit=` | Proposals by status |
| `POST /proposals/{id}/approve` | Body `{edits?, note?}`. Approves and executes. 409 if the proposal belongs to a run waiting for approval |
| `POST /proposals/{id}/reject` | Body `{note?}` |
| `POST /proposals/approve-batch` | Body `{ids, note?}` |

A proposal:

```json
{
  "id": "…", "kind": "task.set_due", "status": "pending", "importance": "normal",
  "target": {"data_source": "tasks", "page_id": "…", "title": "Write integration notes"},
  "before": {"due": "2026-10-30", "week": ["…"], "month": ["…"]},
  "payload": {"page_id": "…", "new_due": "2026-11-03", "decision": "reschedule", "week_page_id": "…", "month_page_id": "…"},
  "reason": "You asked for this in chat.", "rule_ids": ["R1", "R7"], "depends_on": [], "expires_at": "…"
}
```

Decision responses: `{approved, rejected, errors, results: [{proposal_id, outcome, detail}], writes_enabled}`.
Outcomes: `verified`, `noop`, `mismatch`, `stale`, `failed`, `blocked` (writes off), `skipped`.

### Chat

`POST /chat` with body `{message, history?: [{role: "user" | "assistant", content}]}` (at most
20 history items). The response is `text/event-stream`; each event's `data` is JSON:

| Event | Data |
| --- | --- |
| `run` | `{run_id}` |
| `tool` | `{name}`: a tool is being called |
| `tool_refused` | `{name, reason}`: arguments failed validation or a rule (the model sees the reason) |
| `proposal` | `{proposal}`: a pending proposal was created |
| `sources` | `{sources: [{title, url}]}`, after the answer: indexed pages the answer names |
| `memory_suggestion` | `{text}`: a note to remember; nothing is saved unless the account saves it (`POST /memory` with `source: "chat"`) |
| `answer` | `{text}` |
| `error` | `{code, message}` (for example `llm_unavailable`) |
| `done` | `{run_id, proposals}` |

### Demo

| Method and path | Description |
| --- | --- |
| `POST /demo/reset` | Demo deployments only: rebuild the sample workspace and clear runs and proposals. At most twice a minute |

### Workspace

| Method and path | Description |
| --- | --- |
| `GET /tasks?view=active\|today\|overdue\|inbox\|next7` | Tasks from the corresponding view |
| `GET /settings` | Workspace (`notion_profile`: `live`, `sandbox`, `demo` or `account`), write switch, timezone, major tasks a day, today's date in that timezone, model chains, tokens used today, permission modes (`modes`: kind → `approve`, `suggest` or `off`) |
| `PUT /settings/modes` | Body `{modes: {kind: mode}}` with `approve`, `suggest` or `off`; only the kinds sent change. Returns every kind's mode. An unknown kind or mode, or `auto`, is 422 `invalid_request`; the demo answers 403 `demo_account` |
| `GET /notion/status` | Integration name, workspace, and per data source: reachable, schema matches, drift details |

### Workspace mapping

Which data source and properties each module uses (multi-user §Generic workspace model). The
owner's workspace comes from the environment and is read-only here.

| Method and path | Description |
| --- | --- |
| `GET /workspace/modules` | What each module needs: fields, allowed property types, required ones, and the meanings a status field must or may map (`needs`, `allows`) |
| `POST /workspace/import-environment` | The owner only: save the environment's mapping and routine times as its account workspace, checked against its own Notion connection (422 `invalid_mapping` with every problem, e.g. a database the connection can't see) |
| `GET /workspace` | `{source, editable, modules, settings, paused, drift, checked_at, can_import}`; `can_import` tells the owner its connection could take over the environment's workspace; `source` is `environment`, `account` or null (not set up yet); `paused` lists the modules the daily drift check left out, with the problems in `drift` |
| `GET /workspace/suggestion` | A first guess at the whole mapping: `{data_sources, modules, schemas, from_template}`, from the databases the connection can see (names and types only) |
| `GET /workspace/data-sources/{id}?module=` | One database's properties (`schema`) and a guess at mapping the module onto it (`suggestion`); 404 `not_shared` when the connection can't see it |
| `PUT /workspace` | Body `{modules, settings}`, with `settings` `{timezone, max_major_per_day, review_weekday, review_time, morning_time, evening_time, schedule_enabled}`. Checked structurally and then against the live data sources; problems come back as 422 `invalid_mapping` with every one in `error.details`. The owner's environment workspace answers 403 `environment_workspace` |

### AI access

An account's use of AI. The deployment owner uses the service's key: `GET /ai` returns
`{"mode": "deployment"}` and the other routes answer 403 `deployment_key`.

| Method and path | Description |
| --- | --- |
| `GET /ai` | `{mode, service_available, consent, allowance, own_key}`. `mode` is `own_key`, `allowance`, `allowance_used` or `off`; `consent` carries the disclosure `text` and `version`; `allowance` is `{tokens, used_today}`; `own_key` is `{available, hint, status, tokens_today}` |
| `POST /ai/consent` | Body `{version}`: agree to the free-tier disclosure that was shown. 409 `disclosure_changed` if it has changed since |
| `POST /ai/consent/withdraw` | Stop using the service key for this account |
| `PUT /ai/key` | Body `{key}`. Browser sessions only. Checked with Google, then stored encrypted. 422 `key_rejected`, 503 `key_check_failed` (retryable), 404 `own_keys_unavailable` without `LIFEOS_ENCRYPTION_KEY` |
| `DELETE /ai/key` | Remove the stored key |

### Reports

| Method and path | Description |
| --- | --- |
| `GET /reports/usage?days=30` | From the account's own runs (1–365 days): `{days, totals, workflows: {name: summary}, by_day: {date: {runs, tokens}}, models}`; a summary is `{runs, statuses, success_rate, llm_calls, input_tokens, output_tokens, llm_latency_ms: {p50, p95}, duration_ms: {p50}}` |

### Memory

Notes LifeOS keeps in mind when it plans, reviews and chats (architecture §5.8). They belong to
the account, so these routes work before a workspace is set up.

| Method and path | Description |
| --- | --- |
| `GET /memory` | `{notes: [{id, text, source, created_at, updated_at}], limits: {notes, chars}, editable}`; oldest first. `editable` is false in the demo |
| `POST /memory` | Body `{text, source?: "you" \| "chat"}`. Whitespace is collapsed; 422 `invalid_request` when empty or over 200 characters, 409 `memory_full` at 30 notes. Returns 201 and the note |
| `PUT /memory/{id}` | Body `{text}`; the same checks. 404 `unknown_note` for another account's note |
| `DELETE /memory/{id}` | 204, or 404 `unknown_note` |

The demo answers every change with 403 `demo_account`.

### Knowledge

The account's knowledge index (architecture §5.9).

| Method and path | Description |
| --- | --- |
| `GET /knowledge` | `{pages: [{id, page_id, title, url, passages, embedded, truncated, indexed_at, notion_edited_at}], limits: {pages}, editable}` |
| `GET /knowledge/search-pages?q=` | Notion pages the connection can see whose title matches: `{results: [{page_id, title, url, edited_at, indexed}]}` |
| `POST /knowledge/pages` | Body `{page_id}`. Reads, splits and stores the page (an indexed page is refreshed instead); 201 with the page and `warnings` (for example "searched by keyword only" when embeddings failed). 404 `not_shared`, 409 `knowledge_full` at 25 pages, 422 `empty_page` |
| `POST /knowledge/pages/{id}/refresh` | Reads the page again; rebuilds it if Notion changed it (or it lacks embeddings). Returns the page with `changed` and `warnings`. 404 `unknown_page` or `not_shared` |
| `DELETE /knowledge/pages/{id}` | 204, or 404 `unknown_page` |
| `POST /knowledge/ask` | Body `{question}` (at most 500 characters). `{answer, found, source, search, citations, warnings}`: `source` is `ai`, or `search` when no model answered (`answer` is null and `citations` are the closest passages); `search` is `hybrid` or `keyword`; a citation is `{n, page_id, title, url, heading, excerpt}` and `n` matches the `[n]` in the answer |

The demo answers every change with 403 `demo_account`; asking works.

### Pages

| Method and path | Description |
| --- | --- |
| `GET /goals` | `{available, tasks_linked, goals}`. Every goal with `state` (`active`, `planned`, `done`, `dropped` or null), `progress` (current ÷ target, 0–1, or null) and `open_tasks` linked to it; active goals first, then by deadline. `available` is false when the workspace has no goals |
| `GET /reading` | `{available, books}`: the reading queue (books with a status) with `state` (`reading`, `want`, `finished`, `abandoned`) and `progress` (pages read ÷ pages); reading first, then by Up Next |
| `GET /history?days=14` | Newest day first (1–90 days): `[{day, briefing, checkin}]`, each the latest run of that routine for the day as `{id, status, started_at, summary}` or null. A run belongs to the day its summary names, so a late check-in after midnight counts for the day it reviews |

### Schedule

| Method and path | Description |
| --- | --- |
| `POST /schedule/catch-up` | Starts the caller's routines that are due but haven't run today (at most two), claimed like the tick's; the web app calls it on load. `{started: [{job, status, run_id}]}`. 404 in the demo |

### Notion connection

An account's own Notion workspace, connected with OAuth. Available when the deployment has a
Notion public integration configured; otherwise these routes return 404 and
`/notion/connection` reports `available: false`.

| Method and path | Description |
| --- | --- |
| `GET /notion/connect` | Browser sessions only. Records a single-use attempt for the account (10 minutes) and redirects to Notion's consent page |
| `GET /notion/callback` | Notion's redirect target. Checks that the attempt belongs to the signed-in account, exchanges the code and stores the tokens encrypted. Redirects to `/settings?notion=connected`, or `/settings?notion_error=<code>`: `cancelled`, `expired`, `wrong_account`, `exchange_failed` |
| `GET /notion/connection` | `{available, connection}`; `connection` is null or `{workspace_id, workspace_name, workspace_icon, status, connected_at, from_template}`, with `status` `active` or `needs_reconnect` |
| `GET /notion/data-sources` | `{data_sources: [{id, title, database_id, url}], truncated}`: what the connection can see (at most 200). 409 `notion_reconnect` when there's no usable connection |
| `POST /notion/disconnect` | Revokes the token at Notion (best effort) and deletes it. Returns `{status, revoked}`; 409 `not_connected` when there is none |

### Scheduled jobs

`POST /internal/cron/{job}` for `tick`, `morning_briefing`, `evening_checkin`, `weekly_review`,
`monthly_review` and `expire_proposals`, with headers:

```
X-LifeOS-Timestamp: <unix seconds>
X-LifeOS-Signature: hex(HMAC-SHA256(LIFEOS_CRON_SECRET, "<timestamp>.<job>.<body>"))
```

The timestamp must be within 5 minutes.

- `tick` (hourly) expires stale proposals, runs the daily drift check of each connected account,
  and starts the routines due in each account's timezone. Response:
  `{job, status, expired, drift_checked, ran: [{user_id, job, status, run_id}], waiting}`, where
  `waiting` counts due routines left for the next tick by the cap.
- The routine jobs run one routine for the owner, at most once per local day; a repeat returns
  `{"status": "already_ran"}`, and without an owner account `{"status": "skipped"}`. Responses:
  `{job, date, status, run_id?}`.
- `expire_proposals` covers every account and runs on each call: `{job, date, status, expired}`.
