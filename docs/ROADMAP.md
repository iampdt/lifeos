# LifeOS Roadmap

Estimates are focused hours, including tests and docs. The Switch Plan gives projects roughly
20% of 14–16 hours a week in Months 4–6, so about **3 hours a week**. At that pace the MVP
(Phases 3, 4, a minimal 6 and 8) takes 10–12 weeks. Everything after the MVP is optional until
the switch is done.

| Phase | Scope | Exit criteria | Estimate | Status |
| --- | --- | --- | --- | --- |
| 1 | Notion discovery | Report delivered; owner answers recorded | — | ✅ Done (2026-11-01) |
| 2 | System design | `docs/01-architecture.md` approved; open questions Q1–Q7 answered | — | ✅ Done (2026-11-01); sandbox created |
| 3 | Notion integration (read-only) | Owner creates the internal integration and shares Life OS + Sandbox; client with rate limit/retry/pagination; schema registry with drift detection; repositories for Tasks, Goals, Habits, Weeks, Months, Books; view-mirroring queries match the Notion views on live data; Week Key assumption verified; unit tests | 8–10 h | ✅ Done (2026-11-01), see `docs/progress/phase-3.md`. Snapshot persistence to Postgres moves to Phase 4 with the database |
| 4 | Chief of Staff MVP | Proposal/approval/execution/verify/audit pipeline; evening check-in + nightly plan; morning briefing; minimal weekly review; mock + Gemini providers with fallback; workflow tests with fake gateway + mock LLM; writes proven on the sandbox page | 15–20 h | In progress: **4a done** (approval/execution pipeline, sandbox writes verified, see `docs/progress/phase-4a.md`); 4b next (LLM layer, workflows, API) |
| 6 (min) | Frontend MVP | Login, Command Center (chat, today, approvals drawer), Weekly Review decisions, Settings | 8–10 h | Not started |
| 8 (min) | Deployment | Docker; Vercel + Render + Neon; GitHub Actions cron for evening and Sunday jobs; secrets configured; live smoke test | 4–6 h | Not started |
| 5 | More agents + memory | Full weekly review, monthly career review, Goal and Learning specialists, memory facts UI, knowledge index (pgvector) with citations | 15–20 h | Later |
| 6 (rest) | Remaining pages | Daily Briefing, Goals, Tasks, Learning, Monthly Review, Agent Activity | 8–10 h | Later |
| 7 | Automation | Opt-in schedules per workflow; optional `auto` mode for allowlisted safe kinds | 3–5 h | Later |

LifeOS is Flagship 2 (decided 2026-11-01), so Phase 5 also includes the Flagship 2 quality bar:
an evaluation set, latency and cost metrics, provider-fallback tests and an engineering write-up.
Phase 8 adds `DEMO_MODE` with a synthetic workspace and the portfolio case-study page
(see architecture §20).
