# Minervini AI — Next.js Production Frontend

> **Phase 12** of the Minervini SEPA Stock Analysis System.  
> A dark-themed, mobile-first Next.js 15 app that talks exclusively to the
> ShreeVault FastAPI backend. It never reads SQLite or Parquet directly.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Node.js | **18 +** (LTS recommended) |
| npm | 9 + (bundled with Node 18) |
| ShreeVault FastAPI | running locally or deployed |

---

## Local Development Setup

### 1 — Clone and install

```bash
git clone <repo-url>
cd minervini_ai/frontend
npm install
```

### 2 — Configure environment variables

```bash
cp .env.local.example .env.local
```

Open `.env.local` and fill in the three required values:

```env
# Public — sent to the browser
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_API_READ_KEY=your_read_key_here

# Server-side only — never exposed to the browser
API_ADMIN_KEY=your_admin_key_here
```

See the **Environment Variables** table below for full descriptions.

### 3 — Point to the ShreeVault FastAPI

Set `NEXT_PUBLIC_API_URL` in `.env.local` to the base URL of your running
FastAPI server. In development this is typically:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

The Next.js dev server proxies all `/api/v1/*` requests to this URL via the
rewrite rule in `next.config.ts`, eliminating CORS issues during development.

In production, set `NEXT_PUBLIC_API_URL` to your deployed API URL (see Vercel
Deployment below). The proxy rewrite is not needed in production when both
services share a domain or are accessed via a reverse proxy.

### 4 — Run the dev server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## Optional Password Gate


To activate a simple password gate across the entire app:

```bash
# In .env.local
NEXT_PUBLIC_REQUIRE_AUTH=true
SITE_PASSWORD=your-secret-password
```

When `NEXT_PUBLIC_REQUIRE_AUTH=true`, `middleware.ts` intercepts every request
and redirects unauthenticated visitors to `/login`. The login page calls the
Route Handler at `POST /api/auth/login`, which validates the password
server-side and sets an HttpOnly cookie for 7 days.

Leave `NEXT_PUBLIC_REQUIRE_AUTH` unset (or set it to `false`) to disable the
gate entirely — the middleware becomes a no-op.

---

## Vercel Deployment

**Required environment variables (set in Vercel → Settings → Environment Variables):**

| Variable | Value | Required |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Your backend API URL, e.g. `https://your-server.com` | ✅ Yes |
| `NEXT_PUBLIC_REQUIRE_AUTH` | `true` to enable the password gate | Optional |
| `SITE_PASSWORD` | Password for the login gate | Only if `REQUIRE_AUTH=true` |

> ⚠️ If `NEXT_PUBLIC_API_URL` is not set, all screener tables and charts will
> show empty data in production. This is the most common deployment mistake.

### Step 1 — Install and authenticate the Vercel CLI

```bash
npm i -g vercel
vercel login
```

### Step 2 — Link to your Vercel project

```bash
vercel link
```

### Step 3 — Add environment secrets

The `vercel.json` references secrets with `@` prefixes. Add each one:

```bash
# Required
vercel env add NEXT_PUBLIC_API_URL       production
vercel env add NEXT_PUBLIC_API_READ_KEY  production
vercel env add API_ADMIN_KEY             production

# Optional — only needed if you enable the password gate
vercel env add NEXT_PUBLIC_REQUIRE_AUTH  production   # set to "true"
vercel env add SITE_PASSWORD             production
```

You will be prompted to enter the value for each variable interactively.
Alternatively, manage them in the Vercel dashboard:
**Project → Settings → Environment Variables**.

### Step 4 — Deploy

```bash
vercel --prod
```

Vercel reads `frontend/vercel.json`, runs `npm run build`, and publishes the
`.next` output directory.

---

## Environment Variables

| Variable | Scope | Required | Description |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | Browser + Server | ✅ | Base URL of the ShreeVault FastAPI server, e.g. `https://api.example.com`. Used by `apiFetch()` and the dev-mode rewrite proxy. |
| `NEXT_PUBLIC_API_READ_KEY` | Browser + Server | ✅ | Read-only API key injected as the `X-API-Key` header on every `apiFetch()` call. Safe to expose in the browser bundle. |
| `API_ADMIN_KEY` | Server only | ✅ | Admin key for the `/api/v1/run` endpoint. **Never** sent to the browser. Used exclusively by the Route Handler at `/api/proxy/run`. |
| `NEXT_PUBLIC_REQUIRE_AUTH` | Browser + Server | ❌ | Set to `"true"` to activate the optional password gate (middleware.ts). Omit or set to `"false"` to disable. |
| `SITE_PASSWORD` | Server only | ❌ | Plaintext password for the optional auth gate. Only relevant when `NEXT_PUBLIC_REQUIRE_AUTH=true`. |


---

## Architecture

```
Browser
  │
  ▼
Next.js 15 App Router  (frontend/)
  │  pages:  / | /screener | /screener/[symbol] | /watchlist | /portfolio
  │  layout: NavBar (desktop sidebar) + MobileTabBar (bottom) + MarketStatusBar
  │
  ├─ apiFetch()          →  GET  /api/v1/*  (read key in header)
  ├─ /api/proxy/run      →  POST /api/v1/run  (admin key, server-side only)
  └─ /api/proxy/watchlist/clear  →  DELETE  (admin key, server-side only)
          │
          ▼
  ShreeVault FastAPI  (api/)
    Routers: /health · /meta · /stocks · /watchlist · /portfolio · /run
          │
          ▼
  SQLite database  (data/minervini.db)
  Parquet cache    (data/cache/)
```

> **Key principle:** The Next.js app talks exclusively to the FastAPI
> `/api/v1/*` endpoints. It **never** reads SQLite or Parquet directly.

---

## Available Scripts

| Command | Description |
|---|---|
| `npm run dev` | Start the development server on port 3000 |
| `npm run build` | Production build (type-checks + compiles) |
| `npm run start` | Start the production server (after build) |
| `npm run lint` | Run ESLint across the app |

---

## Project Structure

```
frontend/
├── app/
│   ├── layout.tsx          Root layout: NavBar + MobileTabBar + MarketStatusBar
│   ├── page.tsx            Dashboard — KPI cards + best setups + quick actions
│   ├── error.tsx           Global error boundary
│   ├── not-found.tsx       Global 404 page
│   ├── login/              Optional password gate login page
│   ├── screener/
│   │   ├── page.tsx        Full universe screener with filters
│   │   ├── loading.tsx     Screener page skeleton
│   │   └── [symbol]/       Stock deep-dive (chart + gauge + tabs)
│   ├── watchlist/
│   │   ├── page.tsx        Watchlist management + results
│   │   └── loading.tsx     Watchlist page skeleton
│   ├── portfolio/
│   │   ├── page.tsx        Paper portfolio KPIs + equity curve + trades
│   │   └── loading.tsx     Portfolio page skeleton
│   └── api/
│       ├── proxy/run/      Route Handler: POST /api/v1/run (admin key)
│       ├── proxy/watchlist/ Route Handler: watchlist admin ops
│       └── auth/login/     Route Handler: password gate cookie setter
├── components/             Shared UI components (ShadCN + custom)
├── lib/
│   ├── api.ts              Typed API client (apiFetch / adminFetch)
│   └── types.ts            Shared TypeScript types
├── middleware.ts            Optional password gate middleware
├── vercel.json             Vercel deployment config
└── .env.local.example      Environment variable template
```
