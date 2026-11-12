# Deployment

LifeOS runs on free tiers:

| Part | Host | Defined in |
| --- | --- | --- |
| Web app | Vercel (Hobby) | `frontend/` (Next.js, built natively by Vercel) |
| API | Render (Free web service, Docker) | `render.yaml`, `backend/Dockerfile` |
| Database | Neon (Free Postgres) | `DATABASE_URL` |
| Keeping the API awake | Cloudflare Workers (Free, a Cron Trigger) | `ops/keep-awake/` |
| CI | GitHub Actions | `.github/workflows/ci.yml` |

```
Browser ──► Vercel (Next.js) ── /api/* ──────────► Render (FastAPI) ──► Neon, Notion, Gemini
Visitor ──► Vercel (demo)    ── /api/* ── /demo ──►   same service, demo mode
Cloudflare Cron Trigger ── every 10 minutes ── GET /api/v1/health (via the web app)
```

The API schedules its own routines while it's awake; the Worker keeps it awake (decisions I56,
I57).

## First deployment

### 1. Database (Neon)

1. Create a project in a region near the API (for example Singapore).
2. Copy the **direct** connection string (not the `-pooler` one), for example
   `postgresql://user:password@ep-….aws.neon.tech/neondb?sslmode=require&channel_binding=require`.
   LifeOS accepts it as is and derives the driver-specific forms itself.
3. Nothing to install: the first migration that needs pgvector runs `CREATE EXTENSION IF NOT
   EXISTS vector`, which Neon allows.

### 2. API (Render)

1. New → **Blueprint**, pick this repository. Render reads `render.yaml` and creates
   `lifeos-api` (Docker, free, Singapore, health check `/api/v1/health`). Render appends a
   suffix when the name is taken, so the URL may look like `https://lifeos-api-xxxx.onrender.com`.
2. Enter the values it asks for: `DATABASE_URL` (from Neon), `NOTION_TOKEN`, `GEMINI_API_KEY`
   and the `NOTION_DS_*` data-source IDs.
3. `LIFEOS_API_TOKEN` and `LIFEOS_CRON_SECRET` are generated. Copy both from the service's
   Environment page; the next steps need them.
4. The first deploy builds the image and runs the migrations on start (for the demo's
   database too). It deploys again on every push to `main`. With the CI workflow on, set
   `autoDeployTrigger: checksPass` to deploy only commits that pass it; Render never deploys a
   commit that has no checks under `checksPass`.

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

### 5. Keeping the API awake

With `LIFEOS_TICK_MINUTES=30` (set in `render.yaml`), the API runs the scheduler tick itself at
:00 and :30 while it's awake, so routines start within 30 minutes of their time. Render stops a
free instance after 15 minutes without a request, so a Cloudflare Worker requests `/health`
every 10 minutes:

```bash
cd ops/keep-awake
npx wrangler login     # once, in a browser
npx wrangler deploy
```

Before deploying, set `APP_URL` in `wrangler.jsonc` to the web app (the request goes through its
proxy, the same path as a visitor's). The Worker has no URL of its own; its runs are under
Workers & Pages → `lifeos-keep-awake` → Logs. It skips `QUIET_HOURS` (04:00 to 06:59 in
`TIMEZONE`), which keeps one instance inside the 750 free hours a month; a request in those
hours still wakes the API, after about a minute. `node --test ops/keep-awake/worker.test.js`
tests it.

**Instead, GitHub Actions** (`.github/workflows/cron.yml`) can call the signed tick every hour.
Add the repository secrets `LIFEOS_API_URL` (the Render URL, without a trailing slash) and
`LIFEOS_CRON_SECRET` (the value from Render), then run **Scheduled jobs** once from the Actions
tab to check the signature. GitHub's schedules can run hours late.

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
the API redeploys. Approved changes are written to Notion from then on. The
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

### 10. Opening LifeOS to other people

In this order, so each step works before the next depends on it:

1. **Google sign-in** (§8). In the consent screen, set the app's home page to the web app's URL,
   the privacy policy to `https://<your-app>.vercel.app/privacy` and the terms to `/terms`, then
   publish it.
2. **Contact address.** Set `LIFEOS_CONTACT_EMAIL` on Render; the privacy and terms pages show
   it.
3. **Notion connection** (§9), with the [starter template](template.md) if you offer one. The
   review asks for the same three URLs, a support email (the contact address), a short
   description ("runs daily and weekly routines on your Notion workspace and writes only the
   changes you approve"), and why each capability is needed: read content for the routines,
   update and insert content for the changes users approve; no comments or user information.
4. **Your own move.** Sign in with Google, connect your Notion (share every database your
   routines use), and choose Settings → Your Notion → **Move my routines to this connection**.
   Your mapping and routine times carry over and are checked against what the connection can
   see. Afterwards `NOTION_TOKEN` and the `NOTION_DS_*` variables can be removed from Render.
5. **Open sign-up.** Set `LIFEOS_OPEN_SIGNUP` to `"true"` on Render. New Google accounts can then
   sign in, connect Notion, set up their workspace and, after agreeing to the AI terms, use the
   shared allowance or their own key.

## Public demo

`lifeos-api` also serves the demo: with `LIFEOS_DEMO_MOUNT_DATABASE=lifeos_demo` (set in
`render.yaml`), the same process answers under `/demo` in demo mode, with an in-memory sample
workspace and no Notion connection (see architecture §13). It uses the same Gemini key with its
own daily budget (`LIFEOS_DEMO_DAILY_TOKENS`, 150,000) and none of the deployment's Notion,
sign-in or job credentials.

1. **Database.** In the Neon project, add a database named `lifeos_demo` (Databases → New
   database). Demo mode resets its database on every start and refuses to run against a
   database whose name doesn't contain `demo`.
2. **API.** Nothing to add: the start script migrates the demo's database too.
3. **Web app.** Import the repository into Vercel again as a second project (for example
   `lifeos-demo`, root directory `frontend`) with `LIFEOS_BACKEND_URL` = the API's URL followed
   by `/demo` (for example `https://lifeos-api-xxxx.onrender.com/demo`). Redeploy after
   changing it.
4. **Check.** Open the demo URL: the sign-in page shows **Try the demo** instead of the password
   form.

The demo can still run as a service of its own (`LIFEOS_DEMO_MODE=true` and
`LIFEOS_DEMO_DATABASE=lifeos_demo`), but two free services can't both stay awake.

## Operations

| Task | How |
| --- | --- |
| Logs | Render dashboard → `lifeos-api` → Logs |
| Run a job now | A signed `POST /api/v1/internal/cron/{job}` ([api.md](api.md#scheduled-jobs)), or Actions → Scheduled jobs → Run workflow |
| Keep-awake runs | Cloudflare dashboard → Workers & Pages → `lifeos-keep-awake` → Logs |
| Rotate the bearer token | Change `LIFEOS_API_TOKEN` on Render |
| Rotate the cron secret | Change `LIFEOS_CRON_SECRET` on Render and in GitHub secrets together |
| Change the password | `create_owner EMAIL --reset` against the production `DATABASE_URL`; signs out every session (password sign-in only) |
| Stop all writes | Set `LIFEOS_WRITES_ENABLED` to `"false"` in `render.yaml` and push (or, for an immediate stop, in the dashboard; the next Blueprint sync restores the file's value) |
| Change models | `LLM_FAST_MODELS` / `LLM_SMART_MODELS` on Render; `LLM_FAST_HEDGE_SECONDS` / `LLM_SMART_HEDGE_SECONDS` set when the next model joins (0: never) |

## Notes from the first deployment

- With `rootDir` set in `render.yaml`, Render resolves `dockerfilePath` and `dockerContext`
  relative to that directory, not the repository root.
- A Blueprint deploys from `main` as soon as it syncs; later commits deploy as
  `autoDeployTrigger` says.
- Removing a service from `render.yaml` doesn't delete it. The demo's former service,
  `lifeos-demo-api`, stays in the dashboard (asleep, using no hours) until it's deleted there.
- Commits that change only files outside `backend/` don't rebuild the API.

## Free-tier behaviour

| Behaviour | Effect | Handling |
| --- | --- | --- |
| Render sleeps after 15 minutes without traffic | The first request after idle takes about a minute | The keep-awake Worker pings every 10 minutes outside quiet hours; the web app tells the visitor the server is waking and retries |
| Render: 750 instance hours a month for the workspace | One instance awake 21 hours a day uses at most 651; two awake services wouldn't fit, and running out suspends every free service until the next month | The demo shares the instance; quiet hours each night |
| Neon scales to zero after 5 minutes | The first query after idle is slower | The connection pool pings before use |
| Neon: 1 GB and 100 CU-hours a month | Each tick keeps the database up about 5 minutes: roughly 27 CU-hours a month at the smallest size, plus use | — |
| Cloudflare Workers Free: 5 Cron Triggers an account, 100,000 requests a day | The Worker uses one trigger and 144 runs a day | — |
| GitHub Actions schedules (the alternative) can run hours late | Ticks start late | Routines stay due for four hours and run at most once per local day |
| Gemini free-tier limits change | Calls fail with 429 | The model chain falls back, then the rules take over |
