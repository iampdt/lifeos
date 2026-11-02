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
