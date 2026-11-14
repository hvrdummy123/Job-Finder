-- Multi-location support + richer aliases for filter expansion
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS locations JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS jobs_locations_gin_idx ON jobs USING GIN (locations);

INSERT INTO location_aliases (alias, canonical_city, canonical_state, canonical_country, workplace_hint) VALUES
    ('india', NULL, NULL, 'India', NULL),
    ('in', NULL, NULL, 'India', NULL),
    ('bharat', NULL, NULL, 'India', NULL),
    ('usa', NULL, NULL, 'United States', NULL),
    ('us', NULL, NULL, 'United States', NULL),
    ('united states', NULL, NULL, 'United States', NULL),
    ('united states of america', NULL, NULL, 'United States', NULL),
    ('uk', NULL, NULL, 'United Kingdom', NULL),
    ('united kingdom', NULL, NULL, 'United Kingdom', NULL),
    ('gb', NULL, NULL, 'United Kingdom', NULL),
    ('canada', NULL, NULL, 'Canada', NULL),
    ('ca', NULL, NULL, 'Canada', NULL),
    ('germany', NULL, NULL, 'Germany', NULL),
    ('de', NULL, NULL, 'Germany', NULL),
    ('israel', NULL, NULL, 'Israel', NULL),
    ('il', NULL, NULL, 'Israel', NULL),
    ('ireland', NULL, NULL, 'Ireland', NULL),
    ('ie', NULL, NULL, 'Ireland', NULL),
    ('poland', NULL, NULL, 'Poland', NULL),
    ('pl', NULL, NULL, 'Poland', NULL),
    ('karnataka', NULL, 'Karnataka', 'India', NULL),
    ('ka', NULL, 'Karnataka', 'India', NULL),
    ('maharashtra', NULL, 'Maharashtra', 'India', NULL),
    ('mh', NULL, 'Maharashtra', 'India', NULL),
    ('telangana', NULL, 'Telangana', 'India', NULL),
    ('ts', NULL, 'Telangana', 'India', NULL),
    ('tamil nadu', NULL, 'Tamil Nadu', 'India', NULL),
    ('tn', NULL, 'Tamil Nadu', 'India', NULL),
    ('haryana', NULL, 'Haryana', 'India', NULL),
    ('uttar pradesh', NULL, 'Uttar Pradesh', 'India', NULL),
    ('west bengal', NULL, 'West Bengal', 'India', NULL),
    ('washington', NULL, 'Washington', 'United States', NULL),
    ('wa', NULL, 'Washington', 'United States', NULL),
    ('california', NULL, 'California', 'United States', NULL),
    ('ca-us', NULL, 'California', 'United States', NULL),
    ('texas', NULL, 'Texas', 'United States', NULL),
    ('tx', NULL, 'Texas', 'United States', NULL),
    ('new york state', NULL, 'New York', 'United States', NULL),
    ('ny', NULL, 'New York', 'United States', NULL),
    ('redmond', 'Redmond', 'Washington', 'United States', NULL),
    ('bellevue', 'Bellevue', 'Washington', 'United States', NULL),
    ('sunnyvale', 'Sunnyvale', 'California', 'United States', NULL),
    ('cupertino', 'Cupertino', 'California', 'United States', NULL),
    ('arlington', 'Arlington', 'Virginia', 'United States', NULL),
    ('herndon', 'Herndon', 'Virginia', 'United States', NULL),
    ('denver', 'Denver', 'Colorado', 'United States', NULL),
    ('vancouver', 'Vancouver', 'British Columbia', 'Canada', NULL),
    ('dublin', 'Dublin', NULL, 'Ireland', NULL),
    ('haifa', 'Haifa', NULL, 'Israel', NULL),
    ('tel aviv', 'Tel Aviv', NULL, 'Israel', NULL)
ON CONFLICT (alias) DO NOTHING;

-- Fix Amazon-style "IN, KA, Bengaluru" / "US, WA, Seattle" rows (country code first)
UPDATE jobs SET
    country = CASE upper(trim(split_part(location_raw, ',', 1)))
        WHEN 'IN' THEN 'India'
        WHEN 'US' THEN 'United States'
        WHEN 'GB' THEN 'United Kingdom'
        WHEN 'UK' THEN 'United Kingdom'
        WHEN 'CA' THEN 'Canada'
        WHEN 'DE' THEN 'Germany'
        WHEN 'IE' THEN 'Ireland'
        WHEN 'IL' THEN 'Israel'
        WHEN 'PL' THEN 'Poland'
        WHEN 'SG' THEN 'Singapore'
        WHEN 'AU' THEN 'Australia'
        WHEN 'JP' THEN 'Japan'
        ELSE upper(trim(split_part(location_raw, ',', 1)))
    END,
    state = NULLIF(trim(split_part(location_raw, ',', 2)), ''),
    city = NULLIF(trim(split_part(location_raw, ',', 3)), ''),
    city_normalized = NULLIF(trim(split_part(location_raw, ',', 3)), ''),
    updated_at = now()
WHERE location_raw ~ '^[A-Z]{2},[[:space:]]*[A-Z0-9]{1,3},[[:space:]]*.+'
  AND length(trim(split_part(location_raw, ',', 1))) = 2
  AND NULLIF(trim(split_part(location_raw, ',', 3)), '') IS NOT NULL;

-- Two-part "GB, London"
UPDATE jobs SET
    country = CASE upper(trim(split_part(location_raw, ',', 1)))
        WHEN 'IN' THEN 'India'
        WHEN 'US' THEN 'United States'
        WHEN 'GB' THEN 'United Kingdom'
        WHEN 'UK' THEN 'United Kingdom'
        WHEN 'CA' THEN 'Canada'
        WHEN 'DE' THEN 'Germany'
        WHEN 'IE' THEN 'Ireland'
        WHEN 'IL' THEN 'Israel'
        WHEN 'PL' THEN 'Poland'
        WHEN 'SG' THEN 'Singapore'
        ELSE upper(trim(split_part(location_raw, ',', 1)))
    END,
    city = NULLIF(trim(split_part(location_raw, ',', 2)), ''),
    city_normalized = NULLIF(trim(split_part(location_raw, ',', 2)), ''),
    state = NULL,
    updated_at = now()
WHERE location_raw ~ '^[A-Z]{2},[[:space:]]*[^,]+$'
  AND NULLIF(trim(split_part(location_raw, ',', 3)), '') IS NULL
  AND length(trim(split_part(location_raw, ',', 1))) = 2;

-- Expand common Indian state codes left in state column
UPDATE jobs SET state = CASE upper(state)
    WHEN 'KA' THEN 'Karnataka'
    WHEN 'TS' THEN 'Telangana'
    WHEN 'MH' THEN 'Maharashtra'
    WHEN 'TN' THEN 'Tamil Nadu'
    WHEN 'DL' THEN 'Delhi'
    WHEN 'HR' THEN 'Haryana'
    WHEN 'UP' THEN 'Uttar Pradesh'
    WHEN 'WB' THEN 'West Bengal'
    WHEN 'WA' THEN 'Washington'
    WHEN 'CA' THEN 'California'
    WHEN 'TX' THEN 'Texas'
    WHEN 'NY' THEN 'New York'
    WHEN 'VA' THEN 'Virginia'
    WHEN 'CO' THEN 'Colorado'
    WHEN 'MA' THEN 'Massachusetts'
    WHEN 'BC' THEN 'British Columbia'
    WHEN 'ON' THEN 'Ontario'
    ELSE state
END
WHERE state IS NOT NULL AND length(state) <= 3;

-- Seed locations jsonb from corrected scalar fields
UPDATE jobs SET locations = jsonb_build_array(
    jsonb_strip_nulls(jsonb_build_object(
        'raw', location_raw,
        'city', city,
        'state', state,
        'country', country,
        'city_normalized', city_normalized
    ))
)
WHERE locations = '[]'::jsonb
  AND (location_raw IS NOT NULL OR city IS NOT NULL OR country IS NOT NULL);
