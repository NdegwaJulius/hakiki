-- Hakiki CBK API — cbk_entities table
-- Compatible with PostgreSQL 15 (local) and Supabase (production).
-- Run after 20260402000000_init.sql

-- ── cbk_entities ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cbk_entities (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    license_number     TEXT        NOT NULL,
    entity_name        TEXT        NOT NULL,
    category           VARCHAR(50) NOT NULL,   -- dcp | psp | forex | mfb | remittance
    sub_category       TEXT        NOT NULL DEFAULT '',
    status             VARCHAR(30) NOT NULL DEFAULT 'licensed',  -- licensed | suspended | revoked | expired
    county             TEXT        NOT NULL DEFAULT '',
    town               TEXT        NOT NULL DEFAULT '',
    licensed_since     DATE,
    license_expiry     DATE,
    services_permitted JSONB       NOT NULL DEFAULT '[]',
    source_doc         TEXT        NOT NULL DEFAULT '',
    last_scraped       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Deduplicate on license_number (each licence is unique across all CBK entities)
CREATE UNIQUE INDEX IF NOT EXISTS cbk_entities_license_idx     ON cbk_entities (license_number);

-- Fast category / status / county filtering
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS cbk_entities_category_idx           ON cbk_entities (category);
CREATE INDEX IF NOT EXISTS cbk_entities_status_idx             ON cbk_entities (status);
CREATE INDEX IF NOT EXISTS cbk_entities_county_idx             ON cbk_entities (county);
CREATE INDEX IF NOT EXISTS cbk_entities_name_trgm_idx          ON cbk_entities USING gin (entity_name gin_trgm_ops);
