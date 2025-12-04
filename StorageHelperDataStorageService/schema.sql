-- ============================================================
-- 1. user: who the document belongs to
-- ============================================================


CREATE TABLE user (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    display_name    VARCHAR(100) NOT NULL,
    note            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ============================================================
-- 2. document_category: document categories (TAX / VISA / MED / INS / etc.)
-- ============================================================

CREATE TABLE document_category (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(50) NOT NULL UNIQUE,   -- e.g. "TAX", "VISA", "MED", "INS"
    name            VARCHAR(100) NOT NULL,         -- display name, e.g. "Tax Documents", "Immigration Documents"
    description     TEXT,
    classification  TEXT,                      -- e.g. virtual/physical (placeholder)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ============================================================
-- 3. storage_location: physical storage (cabinet / drawer / box)
-- ============================================================

CREATE TABLE storage_location (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,         -- e.g. "Bedroom desk, left drawer #2"
    description     TEXT,
    photo_url       TEXT,
    parent_id       INT,                           -- For hierarchical locations (optional)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES storage_location(id) ON DELETE SET NULL
);

-- ============================================================
-- 4. event: contextual grouping (e.g. "2024 Tax Filing", "2025-03 Dental Visit")
-- ============================================================

CREATE TABLE event (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,         -- e.g. "2024 Tax Filing", "Q2 Dental Visit"
    category        VARCHAR(50),                  -- Optional: tag for organizing events (independent from document_category)
    start_date      DATE,
    end_date        DATE,
    description     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ============================================================
-- 5. document: core table
--    - metadata JSON holds flexible fields (tax_year, expiry_date, issuer_name, etc.)
--    - generated columns expose a few important ones for easy querying + indexing
-- ============================================================

CREATE TABLE document (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    title               VARCHAR(255),
    category_id    INT,
    owner_id            INT NOT NULL,
    event_id            INT,
    current_location_id INT,
    -- Flexible metadata (per-type fields live here)
    metadata            JSON,          -- e.g. {"tax_year":2024,"issuer_name":"IRS","expiry_date":"2026-01-01"}
    -- File content
    image_url           TEXT NOT NULL,
    ocr_text            LONGTEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES document_category(id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id)            REFERENCES user(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id)            REFERENCES event(id) ON DELETE SET NULL,
    FOREIGN KEY (current_location_id) REFERENCES storage_location(id) ON DELETE SET NULL
);

-- ============================================================
-- 6. document_embedding: semantic vector representation (for search)
-- ============================================================

CREATE TABLE document_embedding (
    document_id     INT PRIMARY KEY,
    embedding       JSON NOT NULL,    -- e.g. [0.123, -0.98, ...]
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);

-- ============================================================
-- 7. feedback_message: user feedback used to improve the system
-- ============================================================

CREATE TABLE feedback_message (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    document_id     INT,
    feedback_type   VARCHAR(50),      -- e.g. "type_fix", "location_error", "metadata_fix"
    note            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);

-- ============================================================
-- END OF SCHEMA
-- ============================================================
