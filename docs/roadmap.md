# Roadmap

The MVP is Phases 3, 4, a minimal 6 and a minimal 8. Build notes for each finished phase are in
[build-log/](build-log/).

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Workspace discovery: which databases, views and routines exist and how they are used | ✅ 2026-11-01 |
| 2 | System design ([architecture.md](architecture.md), [decisions.md](decisions.md)) and a sandbox copy of the databases for write tests | ✅ 2026-11-01 |
| 3 | Read-only Notion integration: client, mapping, schema drift, repositories, view queries | ✅ 2026-11-01 · [log](build-log/phase-3.md) |
| 4a | Approval and execution pipeline: proposals, executor, verification, audit log | ✅ 2026-11-01 · [log](build-log/phase-4a.md) |
| 4b | LLM layer, LangGraph workflows (evening check-in, morning briefing, weekly review), API | ✅ 2026-11-01 · [log](build-log/phase-4b.md) |
| 6 (min) | Web app: sign-in, Command Center with chat and approvals, weekly review, settings | ✅ 2026-11-01 · [log](build-log/phase-6.md) |
| 8 (min) | Deployment: Docker, Render + Neon + Vercel, CI, signed scheduled jobs, smoke test | ✅ deployed and smoke-tested 2026-11-02 · [log](build-log/phase-8.md) |
| 5a | Evaluation set: check-in parsing and planning accuracy per model, prompt-injection cases | ✅ 2026-11-02 · [results](evals/results.md) |
| 5 (rest) | Monthly career review, memory (facts and a knowledge index with citations), goal and learning specialists, latency and cost reports | Next |
| 6 (rest) | More pages: daily briefing history, goals, tasks, learning, monthly review, agent activity | Later |
| 7 | Automation: editable permission modes and schedules; optional `auto` mode for allowlisted safe actions | Later |
| 8b | Public demo on a sample workspace | ✅ live 2026-11-02 · [log](build-log/phase-8b.md) |
| 9 | Multi-user: Google sign-in, Notion OAuth per user, generic workspace model, free-tier AI allowance plus own keys ([design](multi-user.md)) | In progress: 9.1–9.5 done · [log](build-log/phase-9.md) |
