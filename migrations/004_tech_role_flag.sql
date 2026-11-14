ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_tech BOOLEAN NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS jobs_open_tech_posted_at_idx
    ON jobs (posted_at DESC NULLS LAST)
    WHERE status = 'open' AND is_tech = true;

-- Best-effort backfill: mark obvious non-tech titles
UPDATE jobs SET is_tech = false
WHERE title ~* '\y(recruiter|talent acquisition|human resources|\yhr\y|account executive|account manager|business development|\ybd\y|sales manager|customer success|marketing manager|content writer|copywriter|office manager|executive assistant)\y'
  AND title !~* '\y(engineer|developer|sde|swe|quant)\y';

-- Ensure engineering-ish titles stay tech
UPDATE jobs SET is_tech = true
WHERE title ~* '\y(software|engineer|developer|sde|swe|backend|frontend|full[\s-]?stack|devops|sre|quant|data engineer|machine learning|blockchain|security engineer|platform engineer)\y';
