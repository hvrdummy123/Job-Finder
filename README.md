# Personal Job Radar

Single-user job aggregation over a fixed company list. Polls Greenhouse, Lever, Ashby, Workday, and a **generic page reader** for careers sites with no public ATS API; stores jobs in Postgres; search/filter like a small job portal.

Design & rationale: [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) · technical detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Stack

- Python 3.12 · FastAPI · asyncpg · httpx · Playwright (Chromium)
- Postgres 16
- React · Vite · TanStack Query/Virtual
- Docker Compose (`app` + `postgres`)

## Quick start (Docker)

```bash
docker compose up --build
```

App binds to **127.0.0.1:8000** only. Do not publish this port to the public internet without an SSH tunnel, VPN, or reverse-proxy auth.

Open http://127.0.0.1:8000

## Local dev

### 1. Postgres

```bash
docker compose up -d postgres
```

### 2. Backend

```bash
cp .env.example .env
# DATABASE_URL=postgresql://jobradar:jobradar@127.0.0.1:5432/jobradar

uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Migrations run automatically on startup.

### 3. Frontend (optional while iterating)

```bash
cd web
npm install
npm run dev
```

Vite proxies `/api` to `:8000`. For a single deployable, build the SPA and let FastAPI serve it:

```bash
cd web && npm run build
# serves from web/dist via the API process
```

### 4. Seed companies (+ portals)

Curated lists (~500+ employers across engineering / blockchain / fintech / remote) plus Indeed, Wellfound, and Naukri remote portal feeds.

**If you use Docker Compose** (recommended), seed **inside** the app container so it hits Compose Postgres — not whatever Postgres may already be on your Mac’s `:5432`:

```bash
uv run python -m scripts.generate_companies_csv
docker compose up -d
docker compose exec app uv run python -m scripts.seed_companies data/companies.csv --skip-detect
```

Then open http://127.0.0.1:8000 and hit **Refresh all**. The first successful poll per company is a silent **baseline** (no “new” flood).

Host-side seed only works if `DATABASE_URL` points at the Job Radar database (Compose does **not** publish Postgres on the host by default, to avoid clashing with a local Postgres).

CSV columns: `name,careers_url` (optional `website,notes,categories,ats_type,ats_slug`).

**Apply** on a job opens the original posting URL (company careers page or Indeed / Wellfound / Naukri listing) — no auto-apply.

## Generic careers pages (no public ATS API)

Many companies only expose a marketing careers site. The **generic** connector reads those pages without requiring a public board API:

1. **JSON-LD** `JobPosting` on the careers HTML  
2. **Sitemap** job URL patterns  
3. **Discovered JSON** endpoints embedded in the page  
4. **Static HTML** scrape — job links / cards (`app/connectors/page_scraper.py`)  
5. **Playwright** — render the SPA, capture job JSON from XHR, click “load more” / follow “open roles”, and hydrate Greenhouse / Lever / Ashby boards if the page loads them in the browser  

Docker image installs Chromium at build time (`playwright install --with-deps chromium`). For local (non-Docker) runs:

```bash
uv sync
uv run playwright install --with-deps chromium
```

Source health shows `fallback_stage` (`jsonld` | `sitemap` | `xhr` | `html` | `playwright`) and `needs_playwright` when the browser path is required but unavailable.

Re-detect + refresh companies still at zero open jobs:

```bash
docker compose exec app uv run python -m scripts.heal_empty_companies --limit 50
# or named: --names Ably BambooHR Anduril
```

## Tests

```bash
uv sync
uv run pytest
```

## API sketch

| Endpoint | Purpose |
|---|---|
| `GET /api/jobs` | Search + filters (`include_unspecified_yoe`, dates, location, …) |
| `POST /api/refresh` | Parallel refresh; progress on `GET /api/events` (SSE) |
| `GET /api/sources/health` | Per-company ATS / errors / Playwright need |
| `POST /api/saved-searches` | Create search (backfills matches over open jobs) |

## Security note

No in-app auth. Keep the bind address on loopback, or put basic auth / VPN in front if you deploy on a VM.
