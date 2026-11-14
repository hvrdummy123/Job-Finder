-- Personal Job Radar — initial schema
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- companies / sources
-- ---------------------------------------------------------------------------
CREATE TABLE companies (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    careers_url     TEXT NOT NULL,
    website         TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX companies_name_trgm_idx ON companies USING GIN (name gin_trgm_ops);

CREATE TABLE sources (
    id                      BIGSERIAL PRIMARY KEY,
    company_id              BIGINT NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
    ats_type                TEXT NOT NULL DEFAULT 'unknown',
    ats_slug                TEXT,
    ats_override            BOOLEAN NOT NULL DEFAULT false,
    poll_interval_seconds   INTEGER NOT NULL DEFAULT 1800,
    next_poll_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at         TIMESTAMPTZ,
    last_success_at         TIMESTAMPTZ,
    last_etag               TEXT,
    last_modified           TEXT,
    last_fingerprint        TEXT,
    failure_count           INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    circuit_open_until      TIMESTAMPTZ,
    baseline_completed      BOOLEAN NOT NULL DEFAULT false,
    baseline_completed_at   TIMESTAMPTZ,
    robots_allowed          BOOLEAN,
    detect_confidence       REAL,
    fallback_stage          TEXT NOT NULL DEFAULT 'unknown',
    needs_playwright        BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX sources_next_poll_at_idx ON sources (next_poll_at);

-- ---------------------------------------------------------------------------
-- jobs
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    source_id           BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_job_id     TEXT NOT NULL,
    title               TEXT NOT NULL,
    normalized_title    TEXT,
    url                 TEXT,
    apply_url           TEXT,
    location_raw        TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    city_normalized     TEXT,
    workplace_type      TEXT NOT NULL DEFAULT 'unknown',
    employment_type     TEXT,
    department          TEXT,
    seniority           TEXT,
    yoe_min             SMALLINT,
    yoe_max             SMALLINT,
    salary_min          NUMERIC,
    salary_max          NUMERIC,
    salary_currency     TEXT,
    description_text    TEXT,
    description_html    TEXT,
    skills              TEXT[] NOT NULL DEFAULT '{}',
    content_hash        TEXT,
    raw                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    posted_at           TIMESTAMPTZ,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at           TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'open',
    is_baseline         BOOLEAN NOT NULL DEFAULT false,
    missing_poll_count  INTEGER NOT NULL DEFAULT 0,
    duplicate_of_job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    search_vector       TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(department, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description_text, '')), 'C')
    ) STORED,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, external_job_id)
);

CREATE INDEX jobs_open_first_seen_at_idx ON jobs (first_seen_at DESC) WHERE status = 'open';
CREATE INDEX jobs_posted_at_idx ON jobs (posted_at DESC NULLS LAST);
CREATE INDEX jobs_company_id_idx ON jobs (company_id);
CREATE INDEX jobs_status_idx ON jobs (status);
CREATE INDEX jobs_workplace_type_idx ON jobs (workplace_type);
CREATE INDEX jobs_country_idx ON jobs (country);
CREATE INDEX jobs_city_normalized_idx ON jobs (city_normalized);
CREATE INDEX jobs_yoe_idx ON jobs (yoe_min, yoe_max);
CREATE INDEX jobs_search_vector_idx ON jobs USING GIN (search_vector);
CREATE INDEX jobs_title_trgm_idx ON jobs USING GIN (title gin_trgm_ops);
CREATE INDEX jobs_soft_dedupe_idx ON jobs (company_id, normalized_title, country, city_normalized, content_hash);
CREATE INDEX jobs_source_external_idx ON jobs (source_id, external_job_id);
CREATE INDEX jobs_open_non_baseline_first_seen_idx
    ON jobs (first_seen_at DESC)
    WHERE status = 'open' AND is_baseline = false;

-- ---------------------------------------------------------------------------
-- location aliases
-- ---------------------------------------------------------------------------
CREATE TABLE location_aliases (
    id                  BIGSERIAL PRIMARY KEY,
    alias               TEXT NOT NULL,
    canonical_city      TEXT,
    canonical_state     TEXT,
    canonical_country   TEXT,
    workplace_hint      TEXT,
    UNIQUE (alias)
);

-- ---------------------------------------------------------------------------
-- events / saved searches / user state
-- ---------------------------------------------------------------------------
CREATE TABLE job_events (
    id          BIGSERIAL PRIMARY KEY,
    job_id      BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX job_events_job_id_idx ON job_events (job_id, at DESC);

CREATE TABLE saved_searches (
    id                      BIGSERIAL PRIMARY KEY,
    name                    TEXT NOT NULL,
    include_terms           TEXT[] NOT NULL DEFAULT '{}',
    exclude_terms           TEXT[] NOT NULL DEFAULT '{}',
    locations               TEXT[] NOT NULL DEFAULT '{}',
    yoe_min                 SMALLINT,
    yoe_max                 SMALLINT,
    include_unspecified_yoe BOOLEAN NOT NULL DEFAULT true,
    workplace_types         TEXT[] NOT NULL DEFAULT '{}',
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job_search_matches (
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    saved_search_id BIGINT NOT NULL REFERENCES saved_searches(id) ON DELETE CASCADE,
    matched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, saved_search_id)
);

CREATE INDEX job_search_matches_search_idx ON job_search_matches (saved_search_id, matched_at DESC);

CREATE TABLE user_job_state (
    job_id      BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    starred     BOOLEAN NOT NULL DEFAULT false,
    hidden      BOOLEAN NOT NULL DEFAULT false,
    applied     BOOLEAN NOT NULL DEFAULT false,
    applied_at  TIMESTAMPTZ,
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE crawl_runs (
    id                  BIGSERIAL PRIMARY KEY,
    trigger             TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    companies_total     INTEGER NOT NULL DEFAULT 0,
    companies_ok        INTEGER NOT NULL DEFAULT 0,
    companies_failed    INTEGER NOT NULL DEFAULT 0,
    jobs_new            INTEGER NOT NULL DEFAULT 0,
    jobs_closed         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE crawl_run_companies (
    run_id          BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
    source_id       BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending',
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    jobs_found      INTEGER NOT NULL DEFAULT 0,
    jobs_new        INTEGER NOT NULL DEFAULT 0,
    was_baseline    BOOLEAN NOT NULL DEFAULT false,
    error           TEXT,
    duration_ms     INTEGER,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE user_visits (
    id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_visit_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO user_visits (id, last_visit_at) VALUES (1, now());

CREATE TABLE parse_cache (
    content_hash    TEXT PRIMARY KEY,
    yoe_min         SMALLINT,
    yoe_max         SMALLINT,
    workplace_type  TEXT,
    locations       JSONB NOT NULL DEFAULT '[]'::jsonb,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    parser          TEXT NOT NULL DEFAULT 'regex'
);

-- ---------------------------------------------------------------------------
-- Phase 2 stubs (unused in Phase 1)
-- ---------------------------------------------------------------------------
CREATE TABLE resumes (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    content_text    TEXT,
    content_html    TEXT,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE profile (
    id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    full_name       TEXT,
    email           TEXT,
    skills          TEXT[] NOT NULL DEFAULT '{}',
    experience      JSONB NOT NULL DEFAULT '[]'::jsonb,
    projects        JSONB NOT NULL DEFAULT '[]'::jsonb,
    facts           JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tailored_resumes (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    resume_id       BIGINT REFERENCES resumes(id) ON DELETE SET NULL,
    ats_type        TEXT,
    content_text    TEXT,
    content_html    TEXT,
    artifacts       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX tailored_resumes_job_resume_ats_idx
    ON tailored_resumes (job_id, coalesce(resume_id, 0), coalesce(ats_type, ''));

CREATE TABLE applications (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    tailored_resume_id BIGINT REFERENCES tailored_resumes(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    submitted_at    TIMESTAMPTZ,
    artifacts       JSONB NOT NULL DEFAULT '{}'::jsonb,
    connector_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX applications_job_id_idx ON applications (job_id);
