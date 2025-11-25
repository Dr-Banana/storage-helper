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
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ============================================================
-- 3. document_type: specific document types (W-2 / 1099 / I-20 / insurance / etc.)
-- ============================================================

CREATE TABLE document_type (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(50) NOT NULL UNIQUE,   -- e.g. "TAX_W2", "VISA_I20"
    doc_name        VARCHAR(100) NOT NULL,         -- display name
    category_id     INT NOT NULL,                  -- Reference to document_category
    description     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES document_category(id) ON DELETE RESTRICT
);

-- ============================================================
-- 4. storage_location: physical storage (cabinet / drawer / box)
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
-- 5. location_document_type: define which document types/categories can be stored in each location
--    This allows pre-defining rules for where different document categories should go
-- ============================================================

CREATE TABLE location_document_type (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    location_id         INT NOT NULL,
    document_type_id    INT,                          -- Specific document type (NULL = applies to all types in category)
    category_id         INT,                          -- Document category (NULL = applies only if document_type_id is set)
                                                       -- If document_type_id is NULL and category_id is set, applies to all types in that category
    priority            INT DEFAULT 0,                 -- Higher priority = preferred location for this type
    is_allowed          BOOLEAN DEFAULT TRUE,          -- Can be used to explicitly block certain types
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (location_id) REFERENCES storage_location(id) ON DELETE CASCADE,
    FOREIGN KEY (document_type_id) REFERENCES document_type(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES document_category(id) ON DELETE CASCADE,
    UNIQUE KEY unique_location_type (location_id, document_type_id),
    UNIQUE KEY unique_location_category (location_id, category_id, document_type_id)
);

-- Example usage:
-- - location_id=1, document_type_id=NULL, category_id=1 (TAX) → This location accepts all TAX documents
-- - location_id=2, document_type_id=5, category_id=NULL → This location accepts only document_type 5
-- - location_id=3, document_type_id=NULL, category_id=2 (VISA), is_allowed=FALSE → This location does NOT accept VISA docs

-- ============================================================
-- 6. event: contextual grouping (e.g. "2024 Tax Filing", "2025-03 Dental Visit")
-- ============================================================

CREATE TABLE event (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    category        VARCHAR(50),                  -- tax / medical / visa / other
    start_date      DATE,
    end_date        DATE,
    description     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ============================================================
-- 7. document: core table
--    - metadata JSON holds flexible fields (tax_year, expiry_date, issuer_name, etc.)
--    - generated columns expose a few important ones for easy querying + indexing
-- ============================================================

CREATE TABLE document (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    title               VARCHAR(255),
    document_type_id    INT,
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
    FOREIGN KEY (document_type_id)    REFERENCES document_type(id) ON DELETE SET NULL,
    FOREIGN KEY (owner_id)            REFERENCES user(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id)            REFERENCES event(id) ON DELETE SET NULL,
    FOREIGN KEY (current_location_id) REFERENCES storage_location(id) ON DELETE SET NULL
);

-- ============================================================
-- 8. document_embedding: semantic vector representation (for search)
-- ============================================================

CREATE TABLE document_embedding (
    document_id     INT PRIMARY KEY,
    embedding       JSON NOT NULL,    -- e.g. [0.123, -0.98, ...]
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);

-- ============================================================
-- 9. feedback_message: user feedback used to improve the system
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
