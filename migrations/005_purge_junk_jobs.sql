-- Reclassify with stricter title rules, then delete non-jobs / non-tech junk

UPDATE jobs SET is_tech = false
WHERE title ~* '(diversity|inclusion|milestones|our stories|empowers|fraud alert|value proposition|press coverage)'
   OR title ~* '^(brazil|india|usa|uk|bahrain|argentina|singapore|canada|germany|france|careers?|jobs?|internship|entry level|slp|xplore)\s*$'
   OR title !~* '(engineer|developer|sde|swe|quant|programmer|architect|scientist|sre|devops|sdet|blockchain|solidity|full[\s-]?stack|backend|frontend)';

UPDATE jobs SET is_tech = false
WHERE url ~* '/(insights|blog|press|news|about|diversity|inclusion|life-at|students|university|fraud|benefits|category|categories|teams|find-your-team|job-categories)/'
   OR apply_url ~* '/(insights|blog|press|news|about|diversity|inclusion|life-at|students|university|fraud|benefits|category|categories|teams)/';

-- Soft-delete junk: close + hide from feed
UPDATE jobs
SET status = 'closed', closed_at = COALESCE(closed_at, now()), is_tech = false, updated_at = now()
WHERE is_tech = false AND status = 'open';

-- Point Amazon / AWS at the amazon.jobs connector
UPDATE sources s
SET ats_type = 'amazon',
    ats_slug = 'software-development',
    ats_override = true,
    failure_count = 0,
    last_error = NULL,
    circuit_open_until = NULL,
    baseline_completed = false,
    next_poll_at = now(),
    fallback_stage = 'api',
    needs_playwright = false,
    updated_at = now()
FROM companies c
WHERE c.id = s.company_id
  AND c.name IN ('Amazon', 'AWS');

UPDATE companies
SET careers_url = 'https://www.amazon.jobs/en/'
WHERE name IN ('Amazon', 'AWS');
