# Phase 9 — Multi-user

Design: [multi-user.md](../multi-user.md). This log grows with each step.

## 9.1 Google sign-in (2026-11-02)

| Piece | Purpose |
| --- | --- |
| `app/auth/google.py` | OpenID Connect authorisation code flow with PKCE, state and nonce; the ID token is verified locally against Google's keys (signature, issuer, audience, expiry, nonce, verified email) |
| `app/auth/accounts.py` | Match by Google subject ID, link an existing account by email, or create one while sign-up is open |
| Migration 0004 | `users.google_sub`, `users.name`, optional password hash; `oauth_states` for single-use, 10-minute sign-in attempts |
| `GET /auth/google/start`, `/auth/google/callback` | Redirect to Google; on return, a session cookie and `/`, or `/login?error=<code>` |
| Web app | **Continue with Google** and readable sign-in errors; the password form only where Google isn't configured |

| Check | Result |
| --- | --- |
| Backend tests | 17 new, against a fake Google with its own signing key: PKCE, linking, replayed and expired attempts, wrong nonce, audience, issuer or key, expired token, unverified email, closed and open sign-up, an email already linked elsewhere, password sign-in refused once Google is configured |
| Local stack | Redirects pass through the web app's proxy (start → Google with the right callback URL; a cancelled sign-in → `/login?error=cancelled`) |
| Production | Deployed with Google not yet configured: `/meta` reports the password form, the Google routes return 404, and migration 0004 ran on both databases |

## 9.2 Multi-tenancy (2026-11-02)

| Piece | Purpose |
| --- | --- |
| Migration 0005 | `user_id` on runs, proposals, scheduled-job records and audit entries (existing rows go to the owner); `users.is_owner` (at most one); idempotency keys unique per account; `purge_user_audit` and a trigger that allows it only for the account being purged |
| `app/workflows/contexts.py` | Per-account context: workflow dependencies and a runner compiled with them, cached per account. Only the owner has a workspace until Notion OAuth (9.3) |
| Runner, proposal service, executor | Built for one account; every query filters on it |
| API | `require_user` and `user_context` dependencies; the bearer token acts as the owner; workspace routes return 409 `no_workspace` for accounts without one; `POST /account/delete` |
| Scheduled jobs | Workflow jobs run for the owner account; expiring proposals covers every account |
| Web app | A "no workspace yet" page, and an Account card in Settings with **Delete account** (type the email to confirm) |

| Check | Result |
| --- | --- |
| Backend tests | 198 passed. New: another account's runs can't be listed, read or resumed; its proposals can't be listed, approved, rejected or batch-approved, and the run-ownership check doesn't reveal them; tasks come from each account's own workspace; the same suggestion is a separate proposal per account; an executor skips another account's approvals; an account without a workspace gets 409; the bearer token acts as the owner and fails without one; deletion removes rows, checkpoints and audit entries and leaves other accounts intact; the audit log stays append-only outside a purge; deletion needs a browser session and is refused for the demo account |
| Migration on real data | On a copy of a development database (5 runs, 5 proposals, 15 audit entries, no account), the upgrade stopped with a hint; after adding an account it gave every row to the owner. Downgrade to 0003 and upgrade again succeeded |
| Local stack in the browser | A non-owner account sees the "no workspace yet" page; **Delete account** removed it and its audit entries, leaving one ID-only `account.deleted` entry |

## 9.3 Connecting Notion (2026-11-02)

| Piece | Purpose |
| --- | --- |
| `app/core/crypto.py` | Fernet encryption for stored credentials, with key rotation |
| `app/notion/oauth.py` | Notion's OAuth: authorisation URL, code exchange, refresh and revoke (HTTP Basic with the client ID and secret) |
| `app/notion/connections.py` | Per-account connections: encrypted tokens, refresh on 401 under a row lock (refresh tokens rotate), `needs_reconnect` when Notion refuses, revoke on disconnect, one rate limiter per account |
| `NotionClient` | Takes a token source and asks it once for a new token on 401; `search_data_sources` |
| Migration 0006 | `notion_connections`; `oauth_states` verifier and nonce optional |
| `app/api/notion_connect.py` | Connect, callback, connection status, visible databases, disconnect; account deletion revokes and removes the connection |
| Web app | **Your Notion** card in Settings (connect, databases LifeOS can see, reconnect, disconnect, messages after Notion's redirect) and **Connect Notion** on the no-workspace page |

| Check | Result |
| --- | --- |
| Backend tests | 215 passed. New: encryption round trip, rotation, wrong key and tampering; the flow against a fake Notion (authorisation URL, encrypted storage, single-use and expiring attempts, cancel, bad code, a callback from another account's attempt, no session, not configured); pagination of visible databases; refresh with rotation, three concurrent requests refreshing once, a refused refresh marking `needs_reconnect` and reconnecting, an outage leaving the connection usable, refresh ahead of a stated expiry; disconnect revoking; account deletion revoking and removing the connection |
| Local stack in the browser | With a local stand-in for Notion's endpoints: **Connect Notion** → consent redirect → back through the web app's proxy → Settings showing the workspace and its five databases; tokens stored as ciphertext; **Disconnect** revoked the token and reset the card |

## 9.4 Generic workspace model (2026-11-03)

| Piece | Purpose |
| --- | --- |
| Mappings as data | `DataSourceSpec` serves both the owner's YAML and accounts' saved mappings (`workspaces` table, migrations 0007–0008) |
| `app/workspaces/modules.py` | The module catalogue: Tasks required; Habits, Weekly and Monthly reviews, Goals, Reading optional; allowed property types and the status meanings each field must or may map |
| Generic habits | Every checkbox in the habits data source is a habit and every number a metric (unit, description); the day from a date property or the title in the mapping's format. `Scorecard` drives the check-in schema, the review, chat and the web form |
| Optional modules | Workflows, queries and writes use only what is mapped: no week/month links, Inbox, cancelled status, priority or habits where a workspace has none |
| Per-account rules | Major tasks a day (R1), timezone, review day and time |
| Account contexts | A connected account with a saved mapping gets its own workflow dependencies and Notion client |
| `app/workspaces/suggest.py` | Onboarding suggestions: databases by title, properties by type and name, status meanings by the longest matching phrase, priority order, habits and metric units, title date formats |
| API | `GET/PUT /workspace`, `GET /workspace/modules`, `GET /workspace/suggestion`, `GET /workspace/data-sources/{id}` |
| Web app | `/setup`; the check-in form, proposal cards and settings show whatever the workspace tracks |
| [Starter template](../template.md) | A layout the suggestions map completely |

| Check | Result |
| --- | --- |
| Backend tests | 231 passed. New: a workspace with Notion's default statuses, a date-based habits table, its own habits and metrics, and no weekly, monthly, goal or reading databases runs the check-in (writes verified), the weekly review and the morning briefing; the daily limit comes from the workspace; a saved mapping is refused with every problem (structure, live schema, unshared database, timezone); onboarding with the suggestion as is gives a working workspace through the account's own client; the owner's layout is mapped in full from names alone; status meanings, priority order, unit guesses and date formats |
| Evaluation | After the check-in schema became per-workspace: `gemini-3.5-flash-lite` 17/17 (both injection cases included); `gemini-3.1-flash-lite` 9/9 answered, 8 provider errors (overload) |
| Production after part 1 | The demo's check-in lists habits and metrics with units, records `metrics`, and verifies the write |
| Local walkthrough | Through the web app's proxy against a stand-in Notion: connect → suggestion (all three databases, status meanings, priorities, habits with units) → save → tasks listed and a check-in verified. The setup page itself wasn't checked visually (the browser extension wasn't available) |

## 9.5 AI under the free tier (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `app/llm/access.py` | `AIAccessService` (consent, own keys, daily usage) and `AccountLLM`, the router an account's workflows use: own key, then the allowance, then rules |
| Migration 0009 | `ai_access` (consent and its version, encrypted key, hint, status) and `ai_usage` (tokens per account and UTC day) |
| `GeminiProvider.check_key` | Asks Google about the key without generating anything; tells a refused key from a quota or outage |
| `app/api/ai_routes.py` | `GET /ai`, consent and withdrawal, add and remove a key |
| Messages | Run warnings and chat give the account's reason (allowance used up, AI off) instead of a generic "unavailable" |
| Web app | Settings → **Your AI access**: the disclosure and agreement, today's usage, and an own-key form |

| Check | Result |
| --- | --- |
| Backend tests | 239 passed. New: no consent and no key means rules, with the reason; consent uses the service key until the allowance runs out; an own key comes first and doesn't touch the allowance; a refused key is marked and the allowance takes over; an own key out of quota falls back for that call; keys are checked first and stored encrypted; the API's consent versioning, key errors, owner refusal and account deletion |
| Against Google, with the service key | The real key passed the check; a made-up key was refused (400 `INVALID_ARGUMENT`) and classified as rejected; a call routed through an account's own key (stored encrypted in a throwaway database) answered with `gemini-3.5-flash-lite` and was counted as own-key usage, not allowance |

## 9.6 Per-account scheduling (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `app/workflows/schedule.py` | When each account's routines are due: own timezone, four-hour window within the local day, the review's weekday |
| `POST /internal/cron/tick` | Expire stale proposals, run each connected account's daily drift check, start due routines (oldest first, capped, time-boxed); a failed run frees its slot |
| `app/workspaces/drift.py` | The daily drift check: modules that no longer match are paused and left out of the account's workspace until the mapping is saved again |
| Migration 0010 | `workspaces`: morning and evening times, routines on or off, paused modules, drift, last check |
| `.github/workflows/cron.yml` | One hourly schedule (minute 30) calling the tick; manual runs default to the tick |
| Web app | Routine times and an on/off switch in setup; a notice on every page when something is paused |

| Check | Result |
| --- | --- |
| Backend tests | 250 passed. New: due windows, local midnight, the review's weekday, other timezones and disabled schedules; a tick runs the owner's weekly review (Asia/Kolkata) and another account's morning briefing (New York) once each; the cap leaves the rest for the next tick; a failed run is retried by a later tick; accounts with routines off or a lost connection aren't scheduled; renaming properties in the (HTTP-backed) workspace pauses Habits while the rest keeps working, pausing Tasks removes the workspace, and saving the setup again restores it |

## 9.7 Privacy, terms, the owner's move, rollout (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `/privacy`, `/terms` | Public pages written from what the code stores and sends; the contact from `LIFEOS_CONTACT_EMAIL`; linked from sign-in and every page |
| `POST /workspace/import-environment` | The owner saves the environment's mapping and routine times as its account workspace, checked against its own connection |
| Contexts | An account workspace takes precedence for the owner too, still on the deployment's AI key; `NOTION_TOKEN` is optional once Notion connections are configured |
| Scheduling | A moved owner is scheduled from its saved times and gets the daily drift check |
| Web app | Settings → Your Notion → **Move my routines to this connection**, with every problem listed if the connection can't see a database |
| [deployment.md](../deployment.md) §10 | The rollout checklist: Google consent screen URLs, contact, Notion review answers, the owner's move, opening sign-up |

| Check | Result |
| --- | --- |
| Backend tests | 253 passed. New: the owner moves from the environment's workspace to its connection (tasks then come through the connection, the settings say so, the AI key stays the deployment's, the tick schedules it from its saved times); moving is refused with the problem when the connection can't see a database, and nothing changes; only the owner can import |
| Web app | Lint, types and build; `/privacy` and `/terms` prerendered |

What remains needs the owner's accounts: the Google OAuth client, the Notion public connection
and its review, the Render secrets (`GOOGLE_*`, `NOTION_OAUTH_*`, `LIFEOS_ENCRYPTION_KEY`,
`LIFEOS_CONTACT_EMAIL`), the template page, the move, and `LIFEOS_OPEN_SIGNUP`.

