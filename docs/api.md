# API

Base path `/api/v1`. JSON in and out, except chat, which streams server-sent events.

## Authentication

| Method | Used by | How |
| --- | --- | --- |
| Session cookie | The web app | `POST /auth/login` sets `lifeos_session` (`HttpOnly; SameSite=Lax; Secure` in production). Every request that changes state must also send `X-CSRF-Token` with the token returned by login or `/auth/me` |
| Bearer token | Scripts | `Authorization: Bearer <LIFEOS_API_TOKEN>`. Refused when the token isn't configured |
| HMAC signature | Scheduled jobs | Only for `/internal/cron/{job}` (see below) |

Everything except `/health`, `/auth/login` and `/internal/cron/{job}` requires a session or the
bearer token.

## Errors

Every error has the same shape:

```json
{"error": {"code": "csrf_failed", "message": "missing or invalid X-CSRF-Token header", "retryable": false, "request_id": null}}
```

| Status | Codes |
| --- | --- |
| 401 | `unauthorized`, `invalid_credentials`, `bad_signature` |
| 403 | `csrf_failed` |
| 404 | `unknown_workflow`, `unknown_job`, `http_error` |
| 409 | `run_state` (run not found or not waiting), `run_owned` (decide it through its run) |
| 422 | `invalid_request` |
| 429 | `rate_limited` (sign-in) |
| 502 | `notion_<code>`, `run_failed` |
| 503 | `auth_not_configured`, `cron_not_configured` |

## Endpoints

### Health and session

| Method and path | Description |
| --- | --- |
| `GET /health` | Liveness, public |
| `GET /meta` | Public: `{demo}`, so the sign-in page knows which form to show |
| `POST /auth/demo` | Demo deployments only (404 otherwise): a session on the shared demo account, no password. Returns `{email, csrf_token}` |
| `GET /health/ready` | Database check; also returns the Notion profile, write switch and whether a model is configured |
| `POST /auth/login` | Body `{email, password}`. Returns `{email, csrf_token}` and sets the cookie |
| `GET /auth/me` | `{via, email, csrf_token}` |
| `POST /auth/logout` | Revokes the session and clears the cookie |

### Workflow runs

| Method and path | Description |
| --- | --- |
| `POST /workflows/{name}/runs` | Start `evening_checkin`, `morning_briefing` or `weekly_review`. Body `{inputs?}`. Returns a run (201) |
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
| `question` (evening check-in) | `{"text": "…"}` and/or structured fields: `done_refs`, `partial_refs`, `habits` (`{name: bool}`), `sleep_hours`, `screen_time_hours`, `pages_read` (`[{ref, pages}]`), `learned`, `new_tasks`. Structured fields win over what the model extracts |
| `decisions` (weekly review) | `{"overdue": {"T1": {"decision": "carry_forward" \| "reschedule" \| "cancel" \| "skip", "due": "YYYY-MM-DD"}}, "inbox": {"I1": {"action": "keep" \| "next" \| "cancel", "due": "…"}}, "note": "…"}`. Anything omitted takes the recommendation (Inbox items are kept) |
| `approval` | `{"approve": "all"}` or `{"approve": ["<proposal id>", …], "edits": {"<id>": {"new_due": "…"}}, "note": "…"}`. Everything not approved is rejected |

Workflow inputs: `evening_checkin` takes `plan_tomorrow` (default `true`); `morning_briefing`
takes `plan_if_empty` (default `true`); `weekly_review` takes `week_of` (a date in the week to
review; default today).

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
| `GET /settings` | Notion profile, write switch, timezone, today's date in that timezone, model chains, tokens used today, permission modes |
| `GET /notion/status` | Integration name, workspace, and per data source: reachable, schema matches, drift details |

### Scheduled jobs

`POST /internal/cron/{job}` for `morning_briefing`, `evening_checkin`, `weekly_review` and
`expire_proposals`, with headers:

```
X-LifeOS-Timestamp: <unix seconds>
X-LifeOS-Signature: hex(HMAC-SHA256(LIFEOS_CRON_SECRET, "<timestamp>.<job>.<body>"))
```

The timestamp must be within 5 minutes. A job runs at most once per local day; a repeat returns
`{"status": "already_ran"}`. Responses: `{job, date, status, run_id?}`, or `{…, expired}` for
`expire_proposals`.
