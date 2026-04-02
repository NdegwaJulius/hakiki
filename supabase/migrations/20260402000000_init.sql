-- Hakiki SACCO API — initial schema
-- Compatible with PostgreSQL 15 (local) and Supabase (production).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── saccos ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS saccos (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT        NOT NULL,
    postal_address   TEXT        NOT NULL DEFAULT '',
    physical_location TEXT       NOT NULL DEFAULT '',
    county           TEXT        NOT NULL DEFAULT '',
    schedule         VARCHAR(10) NOT NULL,
    business_type    VARCHAR(50) NOT NULL,
    is_visible       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prevent duplicate upserts keyed on (name, schedule)
CREATE UNIQUE INDEX IF NOT EXISTS saccos_name_schedule_idx ON saccos (name, schedule);

-- Fast county/schedule/name filtering
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS saccos_county_idx    ON saccos (county);
CREATE INDEX IF NOT EXISTS saccos_schedule_idx  ON saccos (schedule);
CREATE INDEX IF NOT EXISTS saccos_name_trgm_idx ON saccos USING gin (name gin_trgm_ops);

-- ── api_users ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_users (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT        UNIQUE NOT NULL,
    api_key_hash TEXT        UNIQUE NOT NULL,
    tier         VARCHAR(20) NOT NULL DEFAULT 'free',  -- free | pro | enterprise
    credits      INTEGER     NOT NULL DEFAULT 100,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── usage_logs ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usage_logs (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    api_user_id    UUID        NOT NULL REFERENCES api_users (id),
    endpoint       TEXT        NOT NULL,
    credits_used   INTEGER     NOT NULL DEFAULT 1,
    request_params JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS usage_logs_user_idx ON usage_logs (api_user_id);
CREATE INDEX IF NOT EXISTS usage_logs_time_idx ON usage_logs (created_at DESC);

-- ── payments ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id           UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    api_user_id  UUID           NOT NULL REFERENCES api_users (id),
    amount       NUMERIC(10, 2) NOT NULL,
    currency     VARCHAR(10)    NOT NULL DEFAULT 'KES',
    status       VARCHAR(20)    NOT NULL DEFAULT 'pending',  -- pending | completed | failed
    provider     TEXT,
    provider_ref TEXT,
    credits_added INTEGER       NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS payments_user_idx ON payments (api_user_id);

-- ── scrape_history ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_history (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url      TEXT,
    file_name       TEXT,
    records_count   INTEGER,
    schedule_counts JSONB       NOT NULL DEFAULT '{}',
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
