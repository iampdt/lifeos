# LifeOS web app

Next.js 16 (App Router, Cache Components) · TypeScript · Tailwind CSS 4 · shadcn/ui (Base UI) ·
TanStack Query. All data comes from the FastAPI backend in `../backend`.

```bash
npm install
npm run dev            # http://localhost:3000, proxies /api/* to the backend
npm run lint && npm run build
```

`LIFEOS_BACKEND_URL` (default `http://127.0.0.1:8000`) sets where `/api/*` is proxied. The
browser only ever talks to this app, so the backend's session cookie stays first-party.

For local http development, run the backend with `LIFEOS_COOKIE_SECURE=false`; the default
(`true`) only sends the cookie over HTTPS.

| Page | What it does |
| --- | --- |
| `/login` | Owner sign-in (the account is created with `backend/scripts/create_owner.py`) |
| `/` Command Center | Today's tasks, evening check-in and morning briefing, recent runs, chat, approvals drawer |
| `/weekly` | Weekly review: numbers, AI draft, a decision per overdue task, Inbox triage, approval |
| `/settings` | Write switch, models, permission modes, Notion connection and schema drift (read-only) |

Data flows through TanStack Query; `src/lib/api.ts` adds the CSRF token to every change and
reads chat answers as a stream. In production the app is built by Vercel; `Dockerfile` builds a
standalone server for the local container stack.
