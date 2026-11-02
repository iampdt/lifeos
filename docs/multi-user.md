# Multi-user LifeOS (Phase 9)

LifeOS today serves one owner and one Notion workspace. Phase 9 turns it into something anyone
can use: sign in with Google, connect your own Notion, and run the same routines on your own
tasks, with nothing in the code that only makes sense for one person's setup.

## Decisions

| # | Topic | Decision |
| --- | --- | --- |
| P11 | Sign-in | Google only (OpenID Connect, scopes `openid email profile`). Passwords remain only where Google isn't configured (local development); the demo keeps its one-click session |
| P12 | Notion access | A public Notion integration with OAuth. New users can duplicate a LifeOS template during authorisation; existing setups are mapped instead |
| P13 | Workspace model | Generic: modules (tasks, habits, reviews, goals, reading) mapped to whatever databases and properties a user has. Habit names, status names, review cadence and limits are data, not code |
| P14 | AI | Free tier only. Every user gets a small daily allowance on the service's key and can add their own free Gemini key for full use; without either, features run in rule-based mode |

## Constraints that shaped the design

- **Google:** an app asking only for `openid`, `email` and `profile` needs no verification and has
  no test-user cap once published; any further scope would trigger verification.
- **Notion:** public integrations use OAuth with access and refresh tokens. A template URL lets a
  user duplicate a starter page during authorisation (`duplicated_template_id` in the token
  response). The authorisation URL becomes available after the integration is submitted for
  Notion's review.
- **Gemini free tier:** Google may use prompts and responses on unpaid quota to improve its
  products, and human reviewers may read them. Users are told this before any data is sent and
  must agree; with their own key, they agree under their own Google account.
- **Hosting:** the free tiers stay (Render, Neon, Vercel). Work per user must be small and
  bounded: per-user rate limits, token budgets and a cap on scheduled runs per tick.

## Accounts

1. `GET /auth/google/start` creates a single-use sign-in attempt (state, nonce, PKCE verifier)
   that expires after 10 minutes, and redirects to Google.
2. Google redirects to `/api/v1/auth/google/callback` on the web app's domain (proxied to the API,
   so the session cookie stays first-party). The API checks the state, exchanges the code, and
   verifies the ID token: signature against Google's published keys, issuer, audience, expiry,
   nonce, and a verified email.
3. The account is found by Google's stable subject ID. An existing account with the same email
   and no Google link yet is linked on first sign-in, which is how the owner account moves over.
   Otherwise a new account is created, but only while `LIFEOS_OPEN_SIGNUP` is on; it stays off
   until per-user data isolation (9.2) is in place. A normal session (HttpOnly cookie, CSRF
   token) follows.
4. **Delete account** (Settings) removes the user's runs and their checkpoints, proposals,
   executions, scheduled-job records, audit entries and sessions (and, from 9.3, tokens and
   settings). Notion content is never touched. It needs the account's email typed as
   confirmation and a browser session; the shared demo account can't be deleted.

## Notion connection

- **Connect Notion** starts Notion's OAuth flow with a state value tied to the account; the
  callback checks that the signed-in account started the attempt, exchanges the code, stores the
  access and refresh tokens encrypted at rest (`LIFEOS_ENCRYPTION_KEY`), and records the
  workspace name and ID. The authorising user's details in Notion's response aren't kept.
- A `401` from Notion triggers one refresh (and an expiry, if Notion ever sends one, triggers an
  early refresh). Each refresh returns a new refresh token and invalidates the old one, so
  refreshes are serialised by a row lock and a request that finds the token already refreshed
  uses the new one. Notion refusing the refresh marks the connection `needs_reconnect`; a
  network failure leaves it as it was.
- One workspace per user in this phase. **Disconnect** revokes the token at Notion and deletes
  it; so does deleting the account.
- Each user's Notion client has its own rate limiter, kept across requests, so one user's
  traffic can't slow another's.
- Until 9.4 maps a connected workspace's databases, a connection shows which databases it can
  see, and the account's routines don't run yet.

## Generic workspace model

| Module | Required | Mapped properties |
| --- | --- | --- |
| Tasks | Yes | title, status, due date; optional priority, area, recurring, completed date, links to review periods |
| Habits | No | the day (a date property, or a title holding a date), every checkbox property as a habit, optional number properties as metrics |
| Weekly / monthly reviews | No | title, start, end, status, review text |
| Goals | No | title, status, optional current and target |
| Reading | No | title, status, optional pages and pages read |

- **Template users** get the mapping automatically from the duplicated template.
- **Existing workspaces** go through onboarding: LifeOS lists the databases shared with it,
  suggests a database per module and a property per field (by type and name), and asks which
  status options mean *to do*, *in progress*, *done* and *dropped*, and how priorities sort. The
  user confirms or adjusts.
- **Per-user rules:** timezone, the daily limit of major tasks (default 3), and review day and
  time. Rule IDs stay; their parameters come from the user's settings.
- **Schema drift** is checked at onboarding and daily. A module whose mapping no longer matches
  is paused, with a notice and a link back to onboarding, and the executor refuses writes to it.

## AI under free tier

- Each call goes through the user's own key when they have added one (stored encrypted), and
  otherwise through the service key within a per-user daily allowance plus a global cap.
- When neither is available, workflows use their rule-based fallbacks and chat explains why.
- The evaluation set keeps guarding quality; new cases cover generic habit names and mappings.

## Multi-tenancy

- `user_id` on runs, proposals, executions (through proposals), audit entries and scheduled-job
  records, indexed and filtered in every query. Proposal idempotency keys are unique per
  account. Tests assert that one user can never see or act on another user's runs, proposals or
  tasks by guessing IDs.
- Each account has a context: its workflow dependencies (Notion client, mapping, timezone, model
  router, `user_id`) and a runner whose graphs are compiled with them. Contexts are built on
  first use and cached per account; the runner, proposal service and executor only ever touch
  their account's rows.
- Until accounts connect their own Notion (9.3), the deployment's environment-configured
  workspace belongs to the **owner account** (`users.is_owner`, at most one). The bearer token
  and the scheduled jobs act as the owner. Other accounts can sign in, see a "no workspace yet"
  page and delete their account; workspace routes return 409 `no_workspace`.
- Account deletion removes the user's audit entries through a dedicated database function
  (`purge_user_audit`); the audit log stays append-only for everything else.
- Migration 0005 gives existing rows to the owner (the earliest account) and refuses to run if
  there are runs or proposals but no account.

## Scheduling

One GitHub Actions schedule every 30 minutes calls a signed `/internal/cron/tick`. For each user
whose local time has reached a scheduled routine that hasn't run today, the API starts it
(idempotent per user, routine and local date), up to a cap per tick so the free host keeps up.

## Privacy and setup

- Privacy policy and terms pages describe what is stored (encrypted tokens, run and proposal
  metadata, proposal payloads with task titles), the Gemini free-tier terms, and deletion.
- **Owner setup:** a Google Cloud OAuth client (web application; consent screen with the three
  basic scopes, published), a Notion public connection ("any workspace", redirect URI, template
  URL, submitted for review), and the new secrets on Render: `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`, `NOTION_OAUTH_CLIENT_ID`, `NOTION_OAUTH_CLIENT_SECRET`,
  `LIFEOS_ENCRYPTION_KEY` (see [deployment.md](deployment.md) §8 and §9).

## Delivery

| Step | Scope | Status |
| --- | --- | --- |
| 9.1 | Google sign-in and account linking; passwords only where Google isn't configured | Done |
| 9.2 | Multi-tenancy: `user_id` everywhere, per-account contexts, isolation tests, delete account | Done |
| 9.3 | Notion OAuth connect, encrypted tokens with refresh, disconnect | Done |
| 9.4 | Generic workspace model: modules, mappings, onboarding UI, template, generic habits, per-user rules | In progress: generic engine and saved mappings done |
| 9.5 | AI allowance, own-key option, disclosure and consent | |
| 9.6 | Per-user scheduling | |
| 9.7 | Privacy and terms pages, Notion review submission, the owner's migration to the new flow, rollout | |
