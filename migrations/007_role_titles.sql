-- Catalog of popular role titles + which ones the user is actively searching for
CREATE TABLE IF NOT EXISTS role_title_catalog (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL DEFAULT 'engineering',
    aliases     TEXT[] NOT NULL DEFAULT '{}',
    sort_order  INT NOT NULL DEFAULT 100,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_active_role_titles (
    title_id    BIGINT PRIMARY KEY REFERENCES role_title_catalog(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS role_title_settings (
    id                  INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    filter_enabled      BOOLEAN NOT NULL DEFAULT false,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO role_title_settings (id, filter_enabled) VALUES (1, false)
ON CONFLICT (id) DO NOTHING;

CREATE INDEX IF NOT EXISTS role_title_catalog_category_idx ON role_title_catalog (category);
CREATE INDEX IF NOT EXISTS role_title_catalog_title_lower_idx ON role_title_catalog (lower(title));
