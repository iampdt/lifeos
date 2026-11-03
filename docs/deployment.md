# Deployment

LifeOS runs on free tiers:

| Part | Host | Defined in |
| --- | --- | --- |
| Web app | Vercel (Hobby) | `frontend/` (Next.js, built natively by Vercel) |
| API | Render (Free web service, Docker) | `render.yaml`, `backend/Dockerfile` |
| Database | Neon (Free Postgres) | `DATABASE_URL` |
| Scheduled jobs and CI | GitHub Actions | `.github/workflows/cron.yml`, `.github/workflows/ci.yml` |

```
Browser ──► Vercel (Next.js) ── /api/* ──► Render (FastAPI) ──► Neon, Notion, Gemini
GitHub Actions ── signed POST /api/v1/internal/cron/{job} ──► Render
```

## First deployment

### 1. Database (Neon)

1. Create a project in a region near the API (for example Singapore).
2. Copy the **direct** connection string (not the `-pooler` one), for example
   `postgresql://user:password@ep-….aws.neon.tech/neondb?sslmode=require&channel_binding=require`.
   LifeOS accepts it as is and derives the driver-specific forms itself.

### 2. API (Render)

1. New → **Blueprint**, pick this repository. Render reads `render.yaml` and creates
   `lifeos-api` (Docker, free, Singapore, health check `/api/v1/health`). Render appends a
   suffix when the name is taken, so the URL may look like `https://lifeos-api-xxxx.onrender.com`.
2. Enter the values it asks for: `DATABASE_URL` (from Neon), `NOTION_TOKEN`, `GEMINI_API_KEY`
   and the `NOTION_DS_*` data-source IDs.
3. `LIFEOS_API_TOKEN` and `LIFEOS_CRON_SECRET` are generated. Copy both from the service's
   Environment page; the next steps need them.
4. The first deploy builds the image and runs the migrations on start. It deploys again
   whenever CI passes on `main`.

Writes start **off** (`LIFEOS_WRITES_ENABLED` is `"false"` in `render.yaml` for a first
deployment).

### 3. Owner account

From a local checkout, against the production database:

```bash
cd backend
DATABASE_URL='<Neon connection string>' .venv/bin/python -m scripts.create_owner you@example.com
```

### 4. Web app (Vercel)

1. Import the repository. Vercel detects two applications (the FastAPI backend and the Next.js
   frontend) and offers to group them; choose **Import single project** for `frontend` only,
   since the API runs on Render. Root Directory becomes `frontend`, framework Next.js.
2. Add the environment variable `LIFEOS_BACKEND_URL` = the Render URL
   (for example `https://lifeos-api-xxxx.onrender.com`). It is read at build time, so redeploy
   after changing it.
3. Deploy, then sign in at the Vercel URL. If the first deployment didn't start (Vercel may ask
   to re-authenticate), use Deployments → ⋯ → **Create Deployment** with branch `main`.

### 5. Scheduled jobs (GitHub)

Repository → Settings → Secrets and variables → Actions → add:

| Secret | Value |
| --- | --- |
| `LIFEOS_API_URL` | The Render URL, without a trailing slash |
| `LIFEOS_CRON_SECRET` | The value from Render |

Run **Scheduled jobs** once from the Actions tab (`workflow_dispatch`, job `expire_proposals`) to
check the signature. Without these secrets, scheduled runs are skipped with a notice.

### 6. Smoke test

```bash
cd backend
read -rs LIFEOS_API_TOKEN && export LIFEOS_API_TOKEN   # paste the token; it isn't echoed
.venv/bin/python -m scripts.smoke_test \
  --api https://lifeos-api-xxxx.onrender.com --app https://<your-app>.vercel.app --chat
```

It waits for a sleeping instance, then checks auth, the database, the model, that the API docs
are hidden, every Notion schema, the web app and its proxy, and that chat streams through
the proxy. It doesn't write to Notion.

### 7. Turn on writes

When the smoke test passes, set `LIFEOS_WRITES_ENABLED` to `"true"` in `render.yaml` and push;
the API redeploys after CI passes. Approved changes are written to Notion from then on. The
switch lives in `render.yaml`, not the dashboard, because a Blueprint sync resets values that the
file sets.

### 8. Google sign-in (optional)

1. In Google Cloud, create a project, configure the OAuth consent screen (external; scopes
   `openid`, `email` and `profile` only, so no verification is needed) and publish it.
2. Create an OAuth client ID of type *Web application* with the authorised redirect URI
   `https://<your-app>.vercel.app/api/v1/auth/google/callback`. The callback goes through the web
   app's proxy, so the session cookie stays first-party.
3. On Render, set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in the dashboard, and
   `LIFEOS_PUBLIC_URL` (the web app's URL) in `render.yaml`.

From the next deploy the sign-in page offers only **Continue with Google**. The existing account
is linked on first sign-in when the Google account's email matches it; new accounts are refused
while `LIFEOS_OPEN_SIGNUP` is off.

### 9. Connecting Notion (optional)

Lets accounts connect their own Notion workspace (multi-user step 9.3).

1. In Notion's developer portal (https://app.notion.com/developers/connections), create a
   **public** connection. Set the installation scope to **Any workspace** (it can't be changed
   later) and add the redirect URI `https://<your-app>.vercel.app/api/v1/notion/callback`.
   LifeOS needs to read, update and insert content; it doesn't need comments or user
   information.
2. Submit the connection for review. Notion shows the authorisation URL once it has been
   submitted; LifeOS builds the same URL from the client ID.
3. From the **Configuration** tab, set `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET`
   on Render (dashboard).
4. Optionally, build the [starter template](template.md) on a public Notion page and enter its
   URL as the connection's template (Configuration → Basic information → "Notion URL for
   optional template"). New users can then duplicate it while connecting.
5. Generate an encryption key and set it as `LIFEOS_ENCRYPTION_KEY` on Render:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Keep a copy somewhere safe: without it, stored tokens can't be read and every account has to
   connect again. To rotate it, set `new_key,old_key`.

The same key lets accounts add their own Gemini key (Settings → Your AI access). Without one,
accounts share `GEMINI_API_KEY` within `LLM_USER_DAILY_TOKENS` a day each (default 40,000), after
agreeing to the free-tier terms.

From the next deploy, Settings shows **Your Notion** with **Connect Notion**. The owner's routines
keep using the workspace configured by `NOTION_TOKEN`.

## Public demo

The Blueprint also defines `lifeos-demo-api`: the same image with `LIFEOS_DEMO_MODE=true`, an
in-memory sample workspace and no Notion connection (see architecture §13).

1. **Database.** In the Neon project, add a database named `lifeos_demo` (Databases → New
   database). Demo mode resets its database on every start and refuses to run against a
   database whose name doesn't contain `demo`.
2. **API.** Sync the Blueprint (Blueprints → `lifeos` → Manual sync). Render creates
   `lifeos-demo-api`, copies `DATABASE_URL` and `GEMINI_API_KEY` from `lifeos-api`
   (`fromService`), and `LIFEOS_DEMO_DATABASE=lifeos_demo` points the copied URL at the demo
   database. The demo shares the Gemini quota with the main service; to separate them, set its
   own `GEMINI_API_KEY` in the dashboard and drop the `fromService` entry.
3. **Web app.** Import the repository into Vercel again as a second project (for example
   `lifeos-demo`, root directory `frontend`) with `LIFEOS_BACKEND_URL` = the demo API's URL.
4. **Check.** Open the demo URL: the sign-in page shows **Try the demo** instead of the password
   form.

## Operations

| Task | How |
| --- | --- |
| Logs | Render dashboard → `lifeos-api` → Logs |
| Run a job now | Actions → Scheduled jobs → Run workflow |
| Rotate the bearer token | Change `LIFEOS_API_TOKEN` on Render |
| Rotate the cron secret | Change `LIFEOS_CRON_SECRET` on Render and in GitHub secrets together |
| Change the password | `create_owner EMAIL --reset` against the production `DATABASE_URL`; signs out every session (password sign-in only) |
| Stop all writes | Set `LIFEOS_WRITES_ENABLED` to `"false"` in `render.yaml` and push (or, for an immediate stop, in the dashboard; the next Blueprint sync restores the file's value) |
| Change models | `LLM_FAST_MODELS` / `LLM_SMART_MODELS` on Render |

## Notes from the first deployment

- With `rootDir` set in `render.yaml`, Render resolves `dockerfilePath` and `dockerContext`
  relative to that directory, not the repository root.
- A Blueprint deploys from `main` as soon as it syncs; later commits deploy after CI passes.
- Commits that change only files outside `backend/` don't rebuild the API.

## Free-tier behaviour

| Behaviour | Effect | Handling |
| --- | --- | --- |
| Render sleeps after 15 minutes without traffic | The first request after idle takes about a minute | Scheduled jobs call `/health` first and retry; the web app shows loading states |
| Render: 750 instance hours a month | Ample for one service that sleeps when idle | — |
| Neon scales to zero after 5 minutes | The first query after idle is slower | The connection pool pings before use |
| Neon: 1 GB and 100 CU-hours a month | Well above what one owner's proposals and runs need | — |
| GitHub Actions schedules can run late | Jobs may start minutes after the hour | Jobs run at most once per local day, whenever they arrive |
| Public repositories: schedules are disabled after 60 days without activity | Scheduled jobs stop | Re-enable the workflow in the Actions tab |
| Gemini free-tier limits change | Calls fail with 429 | The model chain falls back, then the rules take over |
