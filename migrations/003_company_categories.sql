ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS categories TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS companies_categories_idx ON companies USING GIN (categories);

-- Portal provenance for Apply deep-links + employer display on aggregated listings
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS source_portal TEXT,
    ADD COLUMN IF NOT EXISTS employer_name TEXT;

CREATE INDEX IF NOT EXISTS jobs_source_portal_idx ON jobs (source_portal);
