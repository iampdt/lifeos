# Phase 7 — Automation

## Editable permission modes (2026-11-03)

| Piece | Purpose |
| --- | --- |
| Migration 0011 | `account_settings`: an account's chosen mode per action kind, stored only where it differs from `approve` |
| `app/permissions/modes.py` | Load an account's modes over the defaults; save changes and audit them as `settings.modes` |
| Proposal builder | Leaves out `off` kinds and notes each one in the run's warnings; a new due date whose weekly or monthly page doesn't exist, with creating it `off`, is proposed without the link |
| Chat | Refuses a request that needs an `off` kind, saying which |
| `PUT /settings/modes` | Change one or more kinds to `approve`, `suggest` or `off`; the account's cached context is rebuilt with them |
| Web app | Settings → Safety: a choice per kind (Ask me first, Suggest only, Off); read-only in the demo |

| Check | Result |
| --- | --- |
| Backend tests | 263 passed. New: modes listed and changed, with 422 for `auto`, an unknown kind or an unknown mode; a scorecard that is `off` isn't proposed and the run says why; a proposal in `suggest` mode can't be approved and nothing runs |
| Frontend | Lint and type check clean |
