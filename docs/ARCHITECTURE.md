# Personal Job Radar — Architecture (technical reference)

**Phase:** 1 (discovery)  
**Companion:** design rationale and trade-offs live in [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md)  
**Stack:** Python 3.12 · FastAPI · asyncpg · Postgres 16 · Playwright · React/Vite · Docker Compose

---

## 1. Goals and constraints

Single-user job aggregation over a fixed list of ~100–150 company careers pages. Detect new postings within minutes, store in Postgres, search/filter like a job portal.

| Constraint | Choice |
|---|---|
| One process, one DB, one deploy unit | No Redis, Celery, Kafka, microservices |
| Prefer structured APIs over HTML | Pluggable connectors + generic fallback chain |
| Full refresh of 150 companies ≤ ~15s (warm) | Bounded concurrency + HTTP/2 + cheap change detection |
| Continuous polling without hammering | Adaptive per-company intervals + ETag + circuit breaker |
| Phase 2 ready | Reserved tables + optional `apply()` on connectors; jobs keep apply URL, JD text/HTML, raw JSON |

---

## 2. Why this stack (justification)

**Python + FastAPI + asyncio** over Node/Fastify:

- Connector work is I/O-bound HTTP + light HTML/JSON parsing; asyncio + httpx matches that well.
- `asyncpg` raw SQL is faster and clearer than an ORM for indexed search/filter queries.
- Playwright’s async API and selectolax fit the same process.
- One language for ingest, parse, schedule, and API reduces surface area for a single-user app.

**Postgres as queue** (not as pub/sub in v1):

- At 150 companies and one user, `FOR UPDATE SKIP LOCKED` is enough for claim/work.
- Realtime fan-out stays **in-process** (asyncio pub/sub → SSE). No `LISTEN/NOTIFY` in v1.
- Avoids a second stateful service and keeps Compose to `app` + `postgres`.

**In-process scheduler** instead of a worker fleet:

- One Uvicorn process owns the asyncio event loop, HTTP server, scheduler loop, and SSE fan-out.
- If the process restarts, poll claims are lost safely (row locks release); next tick reclaims due work.

---

## 3. Component overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Single app process                        │
│  ┌──────────┐  ┌─────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ FastAPI  │  │ Scheduler   │  │ Refresh      │  │ SSE       │ │
│  │ REST API │  │ (asyncio)   │  │ orchestrator │  │ broadcaster│ │
│  └────┬─────┘  └──────┬──────┘  └──────┬───────┘  └─────▲─────┘ │
│       │               │                │                 │       │
│       │         claim sources    parallel company   in-process   │
│       │         FOR UPDATE       refresh (semaphores)  EventBus  │
│       ▼               ▼                ▼                 │       │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              Connector registry + HTTP client pool           │ │
│  │  Greenhouse │ Lever │ Ashby │ Workday │ Generic fallback     │ │
│  └────────────────────────────┬───────────────────────────────┘ │
│                               │                                  │
│  ┌────────────────────────────▼───────────────────────────────┐ │
│  │  Ingest: normalize → upsert → (maybe) match → EventBus.publish│ │
│  └────────────────────────────┬───────────────────────────────┘ │
└───────────────────────────────┼──────────────────────────────────┘
                                │
                    ┌───────────▼──────────┐
                    │   PostgreSQL 16      │
                    │   (data + work queue) │
                    └──────────────────────┘

Browser ← static (Vite build) ← FastAPI StaticFiles
Browser ← SSE /api/events ← in-process EventBus
```

**Directory layout (proposed):**

```
app/
  main.py                 # FastAPI app, lifespan (pool, scheduler, httpx, EventBus)
  db.py                   # asyncpg pool, helpers
  api/                    # routes: companies, jobs, searches, refresh, events, health
  connectors/             # base + greenhouse, lever, ashby, workday, generic/
  ingest/                 # normalize, parse_jd, dedupe, match_searches, close_policy
  scheduler/              # adaptive poll loop, claim, circuit breaker
  detect/                 # ATS fingerprint from careers URL
  realtime/               # in-process EventBus → SSE subscriber fan-out
web/                      # React + Vite frontend
migrations/               # numbered .sql files, applied at startup
scripts/seed_companies.py
tests/
docker-compose.yml
```

---

## 4. Data flow

### 4.1 ATS detection (once per company, or on override)

1. User seeds CSV → `companies` row + `sources` row (`ats_type = unknown`).
2. Detector fetches careers URL (follow redirects), fingerprints:
   - Host patterns (`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `*.myworkdayjobs.com`, …)
   - Embedded iframes / script URLs / meta tags
   - Lightweight API probes when ambiguous (e.g. Greenhouse board slug heuristics)
3. Persist `sources.ats_type`, `sources.ats_slug`, `sources.detect_confidence`.
4. Manual override via API/UI wins over auto-detect.

### 4.2 First-run baseline (critical)

The **first successful complete list poll** of a source is a **backfill / baseline**, not a “new jobs” event.

| Behavior | Baseline poll (`sources.baseline_completed = false`) | Subsequent polls |
|---|---|---|
| Ingest all listed jobs | Yes | Yes (diff) |
| Set `first_seen_at` | Yes, but jobs are marked `is_baseline = true` | Real discoveries: `is_baseline = false` |
| Emit SSE / `job_events` “new” | **No** | Yes for true new ids |
| Match saved searches / notify | **No** | Yes |
| Count toward “new since last visit” | **No** (`is_baseline` excluded) | Yes |
| Closing logic | Off until baseline done | Two-strike rule (below) |

After a successful complete list + ingest, set `sources.baseline_completed = true` and `baseline_completed_at = now()`.

If the first poll fails or is incomplete, leave `baseline_completed = false` and retry; never promote a partial list to baseline.

### 4.3 Adaptive background poll

```
every ~5s (scheduler tick):
  SELECT id FROM sources
  WHERE next_poll_at <= now()
    AND (circuit_open_until IS NULL OR circuit_open_until <= now())
  ORDER BY next_poll_at
  FOR UPDATE SKIP LOCKED
  LIMIT N   -- e.g. 8–16 concurrent claims

  for each claimed source (asyncio.create_task, global + per-host sem):
    cheap_check → if unchanged: bump next_poll_at, done
    list_jobs → MUST return ListResult{complete=True, stubs=[...]}
    if not complete: record error / soft failure; DO NOT close; DO NOT baseline
    if not baseline_completed:
      ingest all silently (baseline mode); set baseline_completed; done
    else:
      diff external_ids vs DB
      fetch details only for unknown ids
      upsert jobs; match saved searches for true-new / content-changed
      two-strike close for ids missing from this complete list
      EventBus.publish(refresh_progress, job_new, …)
    update etag / last_modified / failure_count / next_poll_at
```

### 4.4 Closing jobs safely (two-strike rule)

Mass-closing from a partial list is unsafe (timeouts, truncated pages, Workday pagination glitches).

Rules:

1. Closures run **only** after a **successful and complete** `list_jobs` (`complete=True`). Incomplete / errored lists never touch close state.
2. On a complete list, for each currently `open` job whose `external_job_id` is absent:
   - Increment `jobs.missing_poll_count` (or upsert a per-job miss counter).
   - If `missing_poll_count >= 2` **consecutive successful complete polls**: set `status=closed`, `closed_at=now()`, emit `closed` event.
3. If the job reappears on any successful complete list before strike 2: reset `missing_poll_count = 0`.
4. IDs present on the list: reset miss counter; update `last_seen_at`.
5. Baseline poll does not close anything (there is no prior open set to trust yet).

Connectors must set `complete=False` when pagination is truncated, HTTP fails mid-page, or item count looks inconsistent with declared totals (when the API provides them).

### 4.5 Manual “Refresh all”

Same ingest path as the scheduler, but:

- Claims **all** (or eligible) sources immediately, ignoring `next_poll_at`.
- Still bound by global concurrency + per-host semaphore (no thundering herd).
- Streams SSE events via EventBus: `company_started` → `company_done` / `company_error` → `refresh_complete`.
- Baseline vs incremental rules unchanged (first success for a source remains silent).
- Does not permanently force “hot” intervals; optional one-shot bump of `next_poll_at`.

### 4.6 Search / filter (read path)

Single SQL query against `jobs` + joins to `companies` / `user_job_state`, with:

- `websearch_to_tsquery` on `search_vector`
- Filters on indexed columns (`first_seen_at`, `posted_at`, `yoe_min/max`, location, status, company_ids, …)
- **Experience filter:** when `yoe_min`/`yoe_max` bounds are set, include rows where both `jobs.yoe_min` and `jobs.yoe_max` are NULL **iff** `include_unspecified_yoe=true` (default **on**). Without that toggle, “0–2 years” would hide most JDs that never state years.
- **Location filter:** match against normalized `city`/`country`/`workplace_type`, resolving through `location_aliases` so Bengaluru ≡ Bangalore, etc.
- **Pagination:** `LIMIT/OFFSET` for v1 (all sort modes). Optional keyset only for default `newest` later if needed.
- “New since last visit”: `first_seen_at > last_visit_at AND is_baseline = false`
- Target: p95 &lt; 100ms with indexes below

### 4.7 Keyword matching at ingest + saved-search backfill

**At ingest (post-baseline only):** for true-new or content-changed jobs, evaluate active `saved_searches` and upsert `job_search_matches`.

**On create/update of a saved search:** run a **backfill** over existing `status='open'` jobs (and optionally recently closed if desired — default: open only):

1. Delete existing rows for that `saved_search_id` (on edit).
2. Re-evaluate matching in batches (SQL where possible + Python for term logic).
3. Insert new `job_search_matches`.
4. Do **not** flood SSE with thousands of “new match” events; optional single `saved_search_reindexed` event so the UI refreshes counts.

Without backfill, a new/edited search would only see jobs ingested after the change.

---

## 5. Connector framework

```python
class ListResult(NamedTuple):
    stubs: list[JobStub]
    complete: bool          # False ⇒ no closes, no baseline promotion
    etag: str | None
    fingerprint: str | None

class Connector(Protocol):
    name: str
    def detect(self, url: str, response: FetchResult) -> DetectResult | None: ...
    async def list_jobs(self, company, source, client) -> ListResult: ...
    async def get_job(self, company, source, stub, client) -> JobDraft: ...
    # Phase 2
    async def apply(self, job, application, artifacts) -> ApplyResult: ...  # optional
```

| Connector | Primary method | Notes |
|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | Stable public API |
| Lever | `GET api.lever.co/v0/postings/{site}` | Public postings API |
| Ashby | Job board JSON API via `jobs.ashbyhq.com` | Board slug from URL |
| Workday | `POST /wday/cxs/{tenant}/{site}/jobs` | Pagination; `complete=False` if any page fails |
| Generic | JSON-LD → sitemap → XHR → HTML scrape → Playwright | No public ATS API required; Playwright pooled |

**Normalization:** every connector emits a canonical `JobDraft`:

- `external_job_id`, `title`, `normalized_title`, `url`, **`apply_url`**
- `location_*`, `employment_type`, `department`, `seniority`
- **`description_html`**, **`description_text`**, `posted_at`, `salary_*`
- **`raw` (JSONB)** — full upstream payload retained for Phase 2
- Content hash of normalized text for dedupe / parse cache

Phase 2 (auto-apply / resume tailoring) needs `apply_url` + JD text/HTML + `raw` without re-fetching a closed posting. All three are first-class columns, not optional.

**Generic fallback order** (`app/connectors/generic.py` + `page_scraper.py`):

1. `JobPosting` JSON-LD / schema.org
2. `sitemap.xml` job URL patterns → fetch each (capped, concurrent)
3. Heuristic discovery of the page’s own JSON XHR (cached per source)
4. Static HTML scrape — job links / cards (tech-title + job-URL gated)
5. Playwright last resort: shared Chromium, capture listing XHR, DOM scrape, follow “open roles” / RSS, hydrate Greenhouse/Lever/Ashby boards discovered in network traffic

**Source health** must flag companies stuck before a complete list (or Playwright unavailable): `needs_playwright` / `fallback_degraded` so the operator knows which rows need the browser path.

---

## 6. Schema (logical)

### Core (Phase 1)

**companies**  
`id`, `name`, `careers_url`, `website`, `notes`, `created_at`, `updated_at`

**sources** (1:1 with company for v1; FK allows 1:N later)  
`id`, `company_id`, `ats_type`, `ats_slug`, `ats_override` (bool),  
`poll_interval_seconds`, `next_poll_at`, `last_checked_at`, `last_success_at`,  
`last_etag`, `last_modified`, `last_fingerprint`,  
`failure_count`, `last_error`, `circuit_open_until`,  
`baseline_completed` (bool, default false), `baseline_completed_at`,  
`robots_allowed` (nullable/meaningful for HTML paths — see §7),  
`detect_confidence`, `fallback_stage` (`api`|`jsonld`|`sitemap`|`xhr`|`playwright`|`unknown`),  
`needs_playwright` (bool),  
`created_at`, `updated_at`

**jobs**  
`id`, `company_id`, `source_id`,  
`external_job_id`, `title`, `normalized_title`,  
`url`, **`apply_url`**,  
`location_raw`, `city`, `state`, `country`, `city_normalized` (alias-resolved),  
`workplace_type` (`remote`|`hybrid`|`onsite`|`unknown`),  
`employment_type`, `department`, `seniority`,  
`yoe_min`, `yoe_max`,  -- both nullable = unspecified  
`salary_min`, `salary_max`, `salary_currency`,  
**`description_text`**, **`description_html`**,  
`skills` (text[]),  
`content_hash`,  
**`raw` jsonb**,  
`posted_at`, `first_seen_at`, `last_seen_at`, `closed_at`,  
`status` (`open`|`closed`),  
`is_baseline` (bool),  
`missing_poll_count` (int, default 0),  
`duplicate_of_job_id` (nullable FK),  
`search_vector` (generated tsvector),  
`UNIQUE (company_id, external_job_id)`

**location_aliases**  
`id`, `alias` (unique, lowercased), `canonical_city`, `canonical_state`, `canonical_country`, `workplace_hint` (nullable)  
Seed with common cases: Bengaluru/Bangalore, Mumbai/Bombay, “Remote - India” → workplace remote + country India, etc.  
Ingest resolves `location_raw` → alias → `city_normalized` / country / workplace when confident.

**job_events**  
`id`, `job_id`, `event_type` (`seen`|`updated`|`closed`|`reopened`), `at`, `detail` jsonb  
(Baseline ingest does not write per-job “seen” spam; optional single source-level event in `crawl_runs` is enough.)

**saved_searches**  
`id`, `name`, `include_terms`, `exclude_terms`,  
`locations`, `yoe_min`, `yoe_max`, `include_unspecified_yoe` (bool, default true),  
`workplace_types`,  
`is_active`, `created_at`, `updated_at`

**job_search_matches**  
`job_id`, `saved_search_id`, `matched_at` — PK `(job_id, saved_search_id)`

**user_job_state**  
`job_id` PK, `starred`, `hidden`, `applied`, `applied_at`, `notes`, `updated_at`  
(single-user: no `user_id` yet)

**crawl_runs**  
`id`, `trigger` (`scheduler`|`manual`), `started_at`, `finished_at`,  
`companies_total`, `companies_ok`, `companies_failed`, `jobs_new`, `jobs_closed`

**crawl_run_companies**  
`run_id`, `source_id`, `status`, `started_at`, `finished_at`,  
`jobs_found`, `jobs_new`, `was_baseline`, `error`, `duration_ms`

**user_visits**  
`id` (singleton), `last_visit_at`

**parse_cache**  
`content_hash` PK, `yoe_min`, `yoe_max`, `workplace_type`, `locations` jsonb, `parsed_at`, `parser` (`regex`|`llm`)

### Phase 2 hooks (create stub tables now, unused)

**resumes**, **profile**, **tailored_resumes**, **applications**

Connectors expose optional `apply()`; Phase 1 never calls it.

### Indexes (minimum)

- `UNIQUE (company_id, external_job_id)`
- **Hot feed:** partial composite  
  `CREATE INDEX jobs_open_first_seen_at_idx ON jobs (first_seen_at DESC) WHERE status = 'open';`
- `GIN (search_vector)`
- B-tree: `posted_at DESC`, `company_id`, `(yoe_min, yoe_max)`, `workplace_type`, `country`, `city_normalized`
- Partial helpful variants as needed, e.g. open + non-baseline for “new” badges
- `pg_trgm` GIN on `title`, `companies.name`
- `sources (next_poll_at)` for scheduler
- Soft-dedupe lookup index on `(company_id, normalized_title, country, city_normalized, content_hash)`
- `location_aliases (alias)` unique

---

## 7. Polling strategy (detail)

### Adaptive interval

Track per source a rolling “new job rate” using **non-baseline** first-seens only.

| Signal | Interval |
|---|---|
| New jobs in last 24h, or manual hotlist | 3–5 min |
| New jobs in last 7 days | 15–30 min |
| Quiet (none in 30 days) | 2–6 hours |
| After consecutive failures | backoff (see circuit) |

```
base = clamp(
  target_detection_latency / max(estimated_post_rate, ε),
  min=180s,
  max=21600s
)
next_poll_at = now() + base * uniform(0.85, 1.15)  # jitter
```

### Cheap change detection

1. Conditional GET with `If-None-Match` / `If-Modified-Since` when supported.
2. Else: list stubs → fingerprint = hash(sorted external_ids).
3. Unchanged fingerprint → no detail fetches; still treat as successful complete list for miss-counter / last_seen bookkeeping when the connector affirms completeness.
4. `get_job` only for unknown ids (and stub metadata changes).

### robots.txt scope

- Apply **robots.txt only to HTML page fetches** (careers page detection, JSON-LD scrape, sitemap/HTML job pages, Playwright).
- **Do not** apply robots.txt to well-known **public ATS JSON APIs** (Greenhouse boards API, Lever postings API, Ashby board API, Workday CXS JSON, etc.). Those endpoints exist to be consumed; robots rules for the marketing/careers HTML must not block them.
- Persist `robots_allowed` for the HTML careers URL path; API connectors ignore it.

### Rate limits and failure handling

- **Global semaphore:** e.g. 32 in-flight HTTP ops.
- **Per-host semaphore:** e.g. 2–4 concurrent per hostname.
- **Retries:** exponential backoff + jitter, max 3, on 429/5xx/timeout.
- **Circuit breaker:** after N consecutive failures (e.g. 5), `circuit_open_until` with escalating cooldown.
- **Isolation:** one source’s exception never cancels siblings.
- **User-Agent:** honest, e.g. `PersonalJobRadar/0.1 (+local; contact=...)`.

### ~15s full refresh budget

Warm API-backed sources under concurrency should land ~5–15s; HTML/Playwright/Workday tails stream in as they finish.

---

## 8. Realtime (SSE + in-process EventBus)

**v1: no Postgres LISTEN/NOTIFY.** One process → an asyncio `EventBus` (subscribers = SSE response queues) is enough and simpler.

Events:

- `refresh_progress` — per-company start/done/error + run aggregates
- `job_new` / `job_updated` / `job_closed` — summaries (skip baseline)
- `saved_search_reindexed` — after backfill

**Later (multi-process only):** reintroduce `NOTIFY` or an external bus. Not needed now.

**Endpoints:**

- `GET /api/events` — SSE
- `POST /api/refresh` — returns `run_id`; progress via SSE

---

## 9. API surface (Phase 1)

| Method | Path | Purpose |
|---|---|---|
| GET/POST/PATCH | `/api/companies` | CRUD + ATS override |
| POST | `/api/companies/detect` | Re-run detection |
| GET | `/api/jobs` | Search + filters + sort + OFFSET pagination; `include_unspecified_yoe` |
| GET | `/api/jobs/{id}` | Detail |
| PATCH | `/api/jobs/{id}/state` | star/hide/apply |
| GET/POST/PATCH/DELETE | `/api/saved-searches` | CRUD; create/update triggers match backfill |
| GET | `/api/saved-searches/{id}/jobs` | “New for me” |
| POST | `/api/refresh` | Manual parallel refresh |
| GET | `/api/events` | SSE |
| GET | `/api/sources/health` | Status, ATS, fallback stage, needs_playwright, last success, errors, baseline flag |
| POST | `/api/visits` | Update last-visit watermark |

Static SPA catch-all after `/api/*`.

---

## 10. Frontend (dense portal)

- **Layout:** left filters · center virtualized job list · right detail drawer.
- **Filters:** date (posted / first_seen), YoE + **include unspecified (default on)**, location (alias-aware), company multi-select, employment type, seniority, department, workplace type.
- **Refresh:** SSE progress per company.
- **Badges:** “new since last visit” (excludes baseline), saved-search match.
- **Source health:** ATS, interval, circuit, last error, **baseline pending**, **fallback stage**, **needs Playwright** callout.
- **Stack:** React + Vite + TS, TanStack Query + Virtual, plain CSS or Tailwind.
- **Optimistic updates** for star/hide/applied.

Utilitarian dense UI — scan speed over marketing chrome.

---

## 11. Parsing and dedupe

**Regex-first JD parse:** YoE (leave null if unspecified), remote/hybrid/onsite, location (+ alias table), salary, tech keywords.

**LLM fallback (optional):** low-confidence only; cached by `content_hash` in `parse_cache`. App works with no LLM key.

**Dedupe:**

1. Hard identity: `(company_id, external_job_id)` upsert.
2. Soft duplicate → `duplicate_of_job_id`.
3. Repost after close → reopen; clear `closed_at`; reset `missing_poll_count`; keep original `first_seen_at`; emit `reopened` (not baseline).

---

## 12. Failure handling summary

| Failure | Behavior |
|---|---|
| Single company HTTP error | Log, increment failures, continue others; no closes |
| Incomplete list (`complete=False`) | Soft failure; no closes; no baseline promotion |
| Parse error on one job | Skip job; keep others |
| DB upsert conflict | `ON CONFLICT` update fields / `last_seen_at` |
| Playwright missing/crash | Mark `needs_playwright` / error; restart browser if crashed |
| Process restart mid-refresh | Locks drop; reconcile incomplete `crawl_runs` on startup |

Structured JSON logs: `source_id`, `company_id`, `run_id`, `event`, `duration_ms`, `error`.

---

## 13. Scaling limits (honest)

| Dimension | Expected limit on one small VM |
|---|---|
| Companies | ~150 comfortably; ~500 with tuned intervals |
| Jobs retained | ~100k–500k before FTS/index tuning |
| Concurrent SSE clients | tens (one user, a few tabs) |
| Full refresh | ~15s warm for API sources; HTML/Playwright slower |
| When to split processes / NOTIFY | Multi-process deploy — not Phase 1 |

---

## 14. Deployment

```yaml
services:
  postgres:  # 16, volume, healthcheck
  app:       # uv sync → SQL migrate → uvicorn
```

- Migrations: `migrations/001_*.sql` … applied in order at startup.
- Config via env (`DATABASE_URL`, `HTTP_USER_AGENT`, `MAX_CONCURRENCY`, optional LLM).
- **Network exposure:** no auth in-app. On a VM, **do not publish the port to the public internet**. Prefer SSH tunnel, VPN, or reverse-proxy basic auth. Compose should bind `127.0.0.1` by default; document the risk in README.

---

## 15. Testing strategy

- Connector fixtures (recorded JSON/HTML); assert `complete` flag behavior.
- Baseline ingest: no events, `is_baseline=true`, no search matches.
- Two-strike close: missing once → still open; missing twice on complete polls → closed; incomplete list → no strike.
- Saved-search create/edit triggers backfill over existing open jobs.
- YoE filter with `include_unspecified_yoe` on/off.
- Location alias resolution.
- Dedupe / reopen logic.
- No live network in CI by default.

---

## 16. Implementation order (after approval)

1. ~~Architecture doc~~ (this document, revision 2)
2. Postgres schema + migration runner
3. Connector framework + Greenhouse, Lever, Ashby, Workday + generic fallback
4. Scheduler, adaptive polling, parallel refresh + SSE (EventBus)
5. Search/filter API (incl. unspecified YoE + aliases)
6. Frontend (incl. source-health Playwright/fallback callouts)
7. Seed script (CSV → detect → report manual attention)
8. Tests + README (incl. bind/tunnel guidance)

---

## 17. Locked decisions

| Topic | Decision |
|---|---|
| sources ↔ companies | 1:1 in v1 |
| Soft dedupe | `duplicate_of_job_id` |
| Pagination | OFFSET for v1; keyset optional later for newest-only |
| Playwright | Core dep for generic/no-API careers pages; health page lists `needs_playwright` |
| Auth | None in-app; don’t expose publicly (tunnel / VPN / basic auth at proxy) |
| Realtime | In-process EventBus → SSE; no LISTEN/NOTIFY in v1 |
| First crawl | Silent baseline (`is_baseline`, no events/matches) |
| Closes | Complete list only + 2 consecutive misses |
| Saved searches | Backfill on create/update |
| YoE filter | `include_unspecified_yoe` default on |
| Locations | `location_aliases` table |
| Phase 2 job payload | `apply_url` + `description_text`/`html` + `raw` jsonb required |
| robots.txt | HTML fetches only; not public ATS JSON APIs |

---

**Stop here.** Confirm revision 2 (or note remaining nits). Next step is schema + migrations only.
