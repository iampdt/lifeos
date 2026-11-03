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

## Automatic mode (2026-11-03)

| Piece | Purpose |
| --- | --- |
| `AUTO_ALLOWED` | Reading progress, goal progress and the scorecard: the kinds that may run automatically |
| Migration 0014 | On `action_proposals`: `auto_applied`, `restore` (the values that undo a change) and `undone_at` |
| Executor | Keeps the fields' values from just before a write for those kinds (empty ones for a row it created); `undo` writes them back, verified, and refuses when the page no longer has what the change wrote |
| Approval flow | A step before approval approves (as `auto`) and executes the account's automatic proposals; approval is skipped when nothing else is left. The run's notes say what was applied |
| API | `auto` in `PUT /settings/modes` for allowed kinds; `auto_kinds` in settings; `automatic` in `/approvals`; `POST /proposals/{id}/undo` for a week |
| Web app | "Automatic (undoable)" in Settings for the allowed kinds; Approvals → Applied automatically, with Undo |

| Check | Result |
| --- | --- |
| Backend tests | 297 passed. New: a progress figure written without asking while the week's own changes wait; kinds left on approval still wait; the automatic change listed, undone once and then refused; undo refused after the owner's own edit, which stands; a new scorecard row undone by clearing it; `auto` accepted only for allowed kinds |
