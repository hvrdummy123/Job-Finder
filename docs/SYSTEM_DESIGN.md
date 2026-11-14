# Personal Job Radar — System Design

This document explains **what problem the system solves**, **why this architecture was chosen**, **where it wins**, and **how to improve it**. For schemas, APIs, and implementation detail see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Problem

Job seekers track dozens to hundreds of company careers pages. Doing that by hand means:

- Opening each site repeatedly and missing new postings
- Inconsistent filters (location, YoE, title) across Greenhouse, Lever, Workday, custom SPAs, etc.
- Noise from the first “import everything” crawl looking like a flood of “new” jobs
- No durable history of when a role appeared or disappeared

**Personal Job Radar** is a single-user aggregator: keep a fixed company list, poll careers sources continuously, store normalized open jobs in Postgres, and search/filter them like a small job portal — without becoming a multi-tenant SaaS or a scraping farm.

---

## 2. Solution in one picture

```
  CSV / Companies UI
          │
          ▼
  ┌───────────────────┐     detect ATS slug / type
  │  companies+sources │◄────────────────────────┐
  └─────────┬─────────┘                          │
            │ poll / manual refresh              │
            ▼                                    │
  ┌─────────────────────────────────────────────┐│
  │ Connector registry                          ││
  │  Greenhouse · Lever · Ashby · Workday · …   ││
  │  Generic: JSON-LD → sitemap → XHR → HTML    ││
  │           → Playwright (+ board hydrate)    ││
  └─────────┬───────────────────────────────────┘│
            │ JobStub / JobDraft                 │
            ▼                                    │
  ┌───────────────────┐   baseline / two-strike  │
  │ Ingest + normalize │   close / saved-search  │
  └─────────┬─────────┘                          │
            ▼                                    │
  ┌───────────────────┐     SSE progress/events  │
  │ PostgreSQL 16     │──────────────────────────┤
  └─────────┬─────────┘                          │
            ▼                                    │
  ┌───────────────────┐                          │
  │ React UI (search, │     one process: API +   │
  │ titles, companies,│     scheduler + ingest   │
  │ source health)    │◄─────────────────────────┘
  └───────────────────┘
```

One Compose stack (`app` + `postgres`), one Python process (FastAPI + asyncio scheduler), one browser pool for hard careers pages.

---

## 3. Why this design

### 3.1 Single process + Postgres (not workers / Redis / Kafka)

| Pressure | Choice |
|---|---|
| One operator, ~hundreds of companies | Complexity of a job queue fleet is not earned yet |
| Need durable poll state | `sources` rows + `FOR UPDATE SKIP LOCKED` is enough |
| Need live UI progress | In-process EventBus → SSE; no second realtime bus |

**Advantage:** trivial ops (`docker compose up`), easy debugging, cheap hosting.  
**Trade-off:** one VM / one process; horizontal scale is a later step.

### 3.2 Pluggable ATS connectors first, HTML last

Most serious tech employers already publish jobs on **Greenhouse, Lever, Ashby, or Workday**. Those have stable JSON APIs. Preferring them:

- Is faster and more complete than scraping
- Respects robots for HTML while still using public board APIs
- Gives clean IDs for fingerprinting and close detection

**Advantage:** high signal for the majority of the seed list with low breakage.  
**Trade-off:** remaining companies need a generic path (below).

### 3.3 Generic fallback chain (including no public API)

~200 companies only ship a marketing careers page. The generic connector solves that **without requiring each site to expose an ATS API**:

1. JSON-LD `JobPosting`
2. Sitemap job URLs
3. Embedded / discovered JSON
4. Static HTML job-link scrape
5. Playwright: render SPA, capture XHR, follow “open roles”, hydrate Greenhouse/Lever/Ashby if loaded only in the browser

**Advantage:** one path covers custom boards, SPA shells, and “API exists but only after JS.”  
**Trade-off:** slower, flakier (bot walls, empty marketing pages), must gate titles/URLs to avoid junk.

### 3.4 Silent baseline + two-strike closes

- **Baseline:** first successful full list is a backfill — no “100 new jobs!” spam.
- **Closes:** a job disappears only after **two consecutive complete** list polls miss it (incomplete/Playwright failures do not close roles).

**Advantage:** trustworthy “new” and “closed” signals for a personal radar.  
**Trade-off:** brief lag before a taken-down posting is marked closed.

### 3.5 Tech-title gate + UI filters

Ingest keeps a **broad tech/AI/fintech title gate**, then the Titles / Jobs UI narrows further. That avoids filling Postgres with sales/HR noise from huge boards while still letting the user decide precision.

### 3.6 Phase-2-ready job payload

Every job stores `apply_url`, description HTML/text, and `raw` JSON so later auto-apply / ranking can reuse data without re-fetching closed postings.

---

## 4. Where this design is advantageous

| Situation | Why it fits |
|---|---|
| Personal / household use | No auth product, no multi-tenant isolation tax |
| Fixed company list you care about | Polling beats web-wide search relevance |
| Mix of ATS + custom careers sites | Connector registry + generic/Playwright chain |
| Want “what’s new since yesterday” | Baseline + fingerprints + job events |
| Prefer one deployable | Compose: API serves built SPA; Postgres holds queue + data |
| Debugging a bad company | Source health shows ATS, fallback stage, errors, `needs_playwright` |

---

## 5. What this design deliberately does *not* solve

- Public job-board scraping at Indeed/Naukri scale as a primary strategy (portals are optional feeds, not the core model)
- Guaranteed coverage of every bot-walled or login-gated careers site
- Multi-user accounts, billing, or shared cloud tenancy
- Real-time alerts across many devices (SSE is for the open UI; push/email can come later)
- Legal/ToS clearance for aggressive scraping — prefer public ATS APIs; HTML/Playwright is best-effort and rate-limited

---

## 6. How we can improve

Ordered by leverage for this product:

1. **Smarter detect after Playwright** — when the browser discovers `boards-api.greenhouse.io/.../ably30`, persist `ats_type`/`ats_slug` so later polls skip Playwright and use the API connector.
2. **Batch heal of remaining zeros** — run `heal_empty_companies` over generic zeros; track `fallback_stage` histograms on the Companies health view.
3. **Bot-challenge playbook** — longer challenge waits, residential-friendly pacing, or manual careers URL overrides when sites (e.g. Automattic hashcash) block headless Chromium.
4. **Phenom / iCIMS / custom enterprise boards** — first-class connectors when enough seed companies share a host pattern.
5. **Listing caps + priority** — for huge boards (thousands of GH jobs), prioritize tech titles / locations the user pinned before full detail fetch.
6. **Split scheduler process** — only if poll latency or Playwright CPU contends with API on one core; use Postgres `NOTIFY` or a second Compose service.
7. **Notifications** — email/Telegram for saved-search hits once baseline noise is proven clean.
8. **Phase 2** — resume-aware ranking and assisted apply using stored JD + `raw`.

---

## 7. Design principles (keep these)

1. **Structured before scraped** — APIs when they exist; pages when they don’t.
2. **Complete lists before closes** — never punish an incomplete fetch.
3. **Baseline before novelty** — first crawl is inventory, not news.
4. **One operator UX** — source health and heal scripts over opaque “magic sync.”
5. **Boring ops until scale hurts** — add Redis/workers only with a measured bottleneck.

---

## 8. Related docs

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Components, schema, ingest rules, API sketch |
| [`../README.md`](../README.md) | Runbook, seed, generic/Playwright usage, heal script |
