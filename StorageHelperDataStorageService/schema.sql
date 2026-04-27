-- ============================================================
-- 0. Enable pgvector extension
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1. user: who the document belongs to
-- ============================================================


CREATE TABLE "user" (
    id              SERIAL PRIMARY KEY,
    google_id       VARCHAR(255) UNIQUE NOT NULL,  -- Google OAuth ID
    email           VARCHAR(255) UNIQUE NOT NULL,  -- Google account email
    display_name    VARCHAR(100) NOT NULL,
    note            TEXT,
    cooking_level   VARCHAR(20) NOT NULL DEFAULT 'beginner',  -- beginner | intermediate | expert
    is_premium      BOOLEAN NOT NULL DEFAULT FALSE,
    premium_expiry  TIMESTAMP,
    premium_source  VARCHAR(50),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_google_id ON "user"(google_id);

-- ============================================================
-- 2. document_category: document categories (TAX / VISA / MED / INS / etc.)
-- ============================================================

CREATE TABLE document_category (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL,                  -- Owner of this category
    code            VARCHAR(50) NOT NULL,           -- e.g. "TAX", "VISA", "MED", "INS"
    name            VARCHAR(100) NOT NULL,         -- display name, e.g. "Tax Documents", "Immigration Documents"
    description     TEXT,
    classification  TEXT,                      -- e.g. virtual/physical (placeholder)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE,
    UNIQUE (user_id, code)
);
CREATE INDEX idx_document_category_user_id ON document_category(user_id);

-- ============================================================
-- 3. storage_location: physical storage (cabinet / drawer / box)
-- ============================================================

CREATE TABLE storage_location (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL,                  -- Owner of this location
    name            VARCHAR(100) NOT NULL,         -- e.g. "Bedroom desk, left drawer #2"
    description     TEXT,
    photo_url       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
);
CREATE INDEX idx_storage_location_user_id ON storage_location(user_id);

-- ============================================================
-- 4. event: contextual grouping (e.g. "2024 Tax Filing", "2025-03 Dental Visit")
-- ============================================================

CREATE TABLE event (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,         -- e.g. "2024 Tax Filing", "Q2 Dental Visit"
    category        VARCHAR(50),                  -- tag for organizing events (independent from document_category)
    start_date      DATE,
    end_date        DATE,
    description     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 5. document: core table
--    - metadata JSON holds flexible fields (tax_year, expiry_date, issuer_name, etc.)
--    - generated columns expose a few important ones for easy querying + indexing
-- ============================================================

CREATE TABLE document (
    id                  SERIAL PRIMARY KEY,
    title               VARCHAR(255),
    category_id    INT,
    owner_id            INT NOT NULL,
    event_id            INT,
    current_location_id INT,
    -- Flexible metadata (per-type fields live here)
    metadata            JSONB,          -- e.g. {"tax_year":2024,"issuer_name":"IRS","expiry_date":"2026-01-01"}
    -- File content
    image_url           TEXT,          -- Optional: URL to thumbnail or first page
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES document_category(id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id)            REFERENCES "user"(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id)            REFERENCES event(id) ON DELETE SET NULL,
    FOREIGN KEY (current_location_id) REFERENCES storage_location(id) ON DELETE SET NULL
);
CREATE INDEX idx_document_owner_id ON document(owner_id);
CREATE INDEX idx_document_category_id ON document(category_id);

-- ============================================================
-- 6. document_page: pages within a document (e.g. pages in a PDF)
-- ============================================================

CREATE TABLE document_page (
    id                  SERIAL PRIMARY KEY,
    document_id         INT NOT NULL,
    page_number     INT NOT NULL,
    image_url       TEXT NOT NULL,
    ocr_text        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE,
    UNIQUE (document_id, page_number)
);

-- ============================================================
-- 7. document_embedding: semantic vector representation (for search)
-- ============================================================

CREATE TABLE document_embedding (
    document_id     INT PRIMARY KEY,
    embedding       vector(768) NOT NULL,    -- 768-dimensional vector for semantic search
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);

-- ============================================================
-- 8. feedback_message: user feedback used to improve the system
-- ============================================================

CREATE TABLE feedback_message (
    id              SERIAL PRIMARY KEY,
    document_id     INT,
    feedback_type   VARCHAR(50),      -- e.g. "type_fix", "location_error", "metadata_fix"
    note            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);

-- ============================================================
-- 9. Subscription & usage tracking (freemium / Google Play)
-- ============================================================

-- For existing databases, run:
--   ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE,
--     ADD COLUMN IF NOT EXISTS premium_expiry TIMESTAMP, ADD COLUMN IF NOT EXISTS premium_source VARCHAR(50);

CREATE TABLE IF NOT EXISTS daily_usage (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    usage_date      DATE NOT NULL,
    meal_plan_sessions INT NOT NULL DEFAULT 0,
    token_count     INT NOT NULL DEFAULT 0,
    UNIQUE(user_id, usage_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(user_id, usage_date);

-- ============================================================
-- END OF SCHEMA
-- ============================================================
