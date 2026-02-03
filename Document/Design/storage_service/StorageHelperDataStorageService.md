> **For Cursor AI**: This document serves as the **Master Plan and Context** for the `StorageHelperDataStorageService`.
> Please read this before generating code to understand the architecture, current progress, and task dependencies.

## 1. Service Overview

**StorageHelperDataStorageService** is the "database backbone" of the Home AI Paper Organizer. It manages persistent data storage, handles document metadata, and provides a unified interface for other services to access/modify document information.

**Core Responsibilities:**
1. **Data Persistence**: Store and manage all document metadata, embeddings, and organizational information
2. **Query Interface**: Provide REST API for CRUD operations on documents, locations, categories, and events
3. **Search Support**: Store document embeddings for semantic search queries
4. **User Management**: Multi-user support with proper data isolation
5. **Feedback Collection**: Record user feedback for continuous system improvement
6. **File Storage Management**: Handle document file storage with temporary/permanent lifecycle management

**Technology Stack:**
- **Database**: PostgreSQL (with pgvector extension for embeddings)
- **Framework**: FastAPI (Python)
- **ORM**: SQLAlchemy
- **File Storage**: 
  - Local filesystem (development: `./tmp`)
  - Supabase Storage (production/preprod: cloud-based with S3-compatible API)

---

## 2. Data Model & Schema

### 2.1 Core Tables

#### Table 1: user
Represents document owners in the system.

```sql
CREATE TABLE user (
    id INT PRIMARY KEY AUTO_INCREMENT,
    display_name VARCHAR(100) NOT NULL,
    note TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Usage:**
- Every document, location, and event is associated with a user
- Enables multi-user isolation and privacy

---

#### Table 2: document_category
Represents document classification types (TAX, MED, VISA, etc.).

```sql
CREATE TABLE document_category (
    id INT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(50) UNIQUE NOT NULL,        -- e.g. "TAX", "VISA", "MED"
    name VARCHAR(100) NOT NULL,
    description TEXT,
    classification TEXT,                     -- Reserved field for future use (e.g., "secure", "frequent_access")
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Key Points:**
- `code` is case-sensitive and unique (used for categorization rules)
- `classification` field is reserved for metadata about the category itself
- Examples: TAX, VISA, MED, INS, EDU, LEG, BANK, UTIL, WORK, REC, MISC

**Current Data:**
```
id | code  | name                    | classification
1  | TAX   | Tax Documents           | NULL
2  | VISA  | Immigration Documents   | NULL
3  | MED   | Medical Documents       | NULL
4  | INS   | Insurance Documents     | NULL
...
```

---

#### Table 3: storage_location
Represents physical storage places (drawers, cabinets, boxes, etc.).

```sql
CREATE TABLE storage_location (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,              -- e.g. "Bedroom desk, left drawer #2"
    description TEXT,
    photo_url TEXT,                          -- Photo of the location
    parent_id INT,                           -- Hierarchical: NULL for root locations
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES storage_location(id) ON DELETE SET NULL
);
```

**Key Points:**
- Supports hierarchical organization (cabinet → drawer → compartment)
- `photo_url` helps users visually identify locations
- `parent_id` allows recursive location structures

**Example Hierarchy:**
```
Bedroom Desk
├── Left Drawer
│   ├── Compartment 1 (TAX files)
│   └── Compartment 2 (Medical files)
├── Right Drawer
└── File Cabinet
    ├── Drawer 1
    └── Drawer 2
```

---

#### Table 4: event
Represents contextual groupings of documents (e.g., "2024 Tax Filing", "Dental Checkup").

```sql
CREATE TABLE event (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(200) NOT NULL,              -- e.g. "2024 Tax Filing", "Q2 Dental Visit"
    category VARCHAR(50),                   -- Optional: tag for organizing events (independent from document_category)
    start_date DATE,
    end_date DATE,
    description TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Key Points:**
- Events are **independent from document categories** (orthogonal dimension)
- A document can have category="TAX" AND event="2024 Tax Filing"
- Useful for temporal grouping and context

**Example Events:**
```
- 2024 Tax Filing (2024-01-01 to 2024-04-15)
- Q2 Dental Visit (2024-04-15)
- Insurance Claim #2024-05 (2024-05-10 to pending)
```

---

#### Table 5: document (CORE)
The central table storing all document metadata.

```sql
CREATE TABLE document (
    id INT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(255),                      -- e.g. "2024 W-2 Form"
    category_id INT NOT NULL,                -- Reference to document_category
    owner_id INT NOT NULL,                   -- Which user owns this document
    event_id INT,                            -- Optional: associated event
    current_location_id INT,                 -- Where the document is currently stored
    metadata JSON,                           -- Flexible per-document fields
    image_url TEXT NOT NULL,                 -- Reference to scanned/uploaded image
    ocr_text LONGTEXT,                       -- Extracted text from OCR
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES document_category(id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id) REFERENCES user(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id) REFERENCES event(id) ON DELETE SET NULL,
    FOREIGN KEY (current_location_id) REFERENCES storage_location(id) ON DELETE SET NULL
);
```

**Key Points:**
- `category_id` is required (every document must have a category)
- `current_location_id` is where the document is physically stored (single location)
- `metadata` JSON stores flexible per-document fields based on category
- `ocr_text` enables full-text search

**Metadata Examples:**
```json
{
  "tax_year": 2024,
  "issuer_name": "Employer Inc",
  "form_type": "W-2",
  "source_location": "Email"
}
```

**Metadata for Different Categories:**
```
TAX: {"tax_year", "issuer_name", "form_type", "filing_status"}
MED: {"provider_name", "service_date", "procedure", "amount"}
VISA: {"document_type", "issue_date", "expiry_date", "country"}
```

---

#### Table 6: document_embedding
Stores vector embeddings for semantic search.

```sql
CREATE TABLE document_embedding (
    document_id INT PRIMARY KEY,
    embedding JSON NOT NULL,                 -- Vector representation [0.123, -0.456, ...]
    created_at TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);
```

**Key Points:**
- One embedding per document
- Stores 768-dimensional vector (from Gemini text-embedding-004)
- Enables semantic/similarity search across documents
- Deleted when document is deleted

---

#### Table 7: feedback_message
Records user corrections and feedback for system improvement.

```sql
CREATE TABLE feedback_message (
    id INT PRIMARY KEY AUTO_INCREMENT,
    document_id INT,
    feedback_type VARCHAR(50),               -- "category_fix", "location_error", "metadata_correction"
    note TEXT,                               -- User's feedback text
    created_at TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);
```

**Feedback Types:**
- `category_fix`: User corrected the document category
- `location_error`: Suggested location is wrong
- `metadata_correction`: Metadata extraction was incorrect
- `other`: General feedback

---

### 2.2 Key Relationships

```
user (1) ──┬──→ (N) document
           ├──→ (N) storage_location
           └──→ (N) event

document_category (1) ──→ (N) document
storage_location (1) ──→ (N) document
event (1) ──→ (N) document
document (1) ──→ (1) document_embedding
document (1) ──→ (N) feedback_message
```

**Multi-user Isolation:**
- All queries must filter by `owner_id` to ensure data isolation
- Storage locations can be shared or private (indicated by owner relationship if added)

---

## 3. File Storage Strategy

### 3.1 Storage Backend Selection

The service supports two storage backends based on environment:

| Environment | Storage Backend | Configuration |
|-------------|----------------|---------------|
| **Local (dev)** | Local filesystem | `APP_ENV=local`, `STORAGE_LOCAL_PATH=./tmp` |
| **Preprod/Prod** | Supabase Storage (Cloud) | `APP_ENV=prod/preprod`, `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_BUCKET` |

**Code Location**: `app/integrations/storage_client.py`

```python
if settings.APP_ENV in ("prod", "preprod"):
    return cls._upload_to_supabase(file_content, full_path)
else:
    return cls._upload_to_local(file_content, full_path)
```

---

### 3.2 Temporary File Management

To prevent storage waste from abandoned uploads, the service implements a two-stage file lifecycle:

#### Preview Stage (Temporary Storage)
When users upload files for AI processing but haven't confirmed:
- Files are stored in **temporary folders** with `tmp/` prefix
- These files can be automatically cleaned up if not confirmed within retention period (default: 7 days)

#### Confirm Stage (Permanent Storage)
When users confirm and save to database:
- Files are automatically **moved from temporary to permanent storage**
- The `tmp/` prefix is removed from the path
- Database records are created with permanent file paths

---

### 3.3 File Path Structure

#### Local Environment (APP_ENV=local)

```
./tmp/                                    # STORAGE_LOCAL_PATH
├── tmp/                                  # Temporary files root
│   └── documents/
│       └── {user_id}/
│           └── pages/
│               └── {uuid}.jpg           # Preview uploads (7 days TTL)
└── documents/                            # Permanent files root
    └── {user_id}/
        └── pages/
            └── {uuid}.jpg               # Confirmed uploads (persistent)
```

**File Path Examples:**
- Temporary: `E:\storage-helper\StorageHelperDataStorageService\tmp\tmp\documents\1\pages\abc-123.jpg`
- Permanent: `E:\storage-helper\StorageHelperDataStorageService\tmp\documents\1\pages\abc-123.jpg`

#### Production Environment (APP_ENV=prod/preprod)

```
Supabase Bucket: "documents"
├── tmp/                                  # Temporary files root
│   └── documents/
│       └── {user_id}/
│           └── pages/
│               └── {uuid}.jpg           # Preview uploads (7 days TTL)
└── documents/                            # Permanent files root
    └── {user_id}/
        └── pages/
            └── {uuid}.jpg               # Confirmed uploads (persistent)
```

**URL Examples:**
- Temporary: `https://xxx.supabase.co/storage/v1/object/public/documents/tmp/documents/1/pages/abc-123.jpg`
- Permanent: `https://xxx.supabase.co/storage/v1/object/public/documents/documents/1/pages/abc-123.jpg`

---

### 3.4 Temporary File Cleanup Service

#### Current Implementation
**Script Location**: `scripts/cleanup_temp_files.py`

**Features:**
- Deletes temporary files older than configurable threshold (default: 7 days)
- Supports both local filesystem and Supabase Storage
- Auto-detects environment based on `APP_ENV`

**Usage:**
```bash
# Clean up files older than 7 days (default)
python scripts/cleanup_temp_files.py

# Custom threshold
python scripts/cleanup_temp_files.py --days 3
```

**Scheduled Execution (Recommended):**
```bash
# Cron job (Linux/Mac) - Daily at 2 AM
0 2 * * * cd /path/to/service && python scripts/cleanup_temp_files.py

# Task Scheduler (Windows) - Daily at 2 AM
# Create task with action: python scripts\cleanup_temp_files.py

# Docker/Kubernetes CronJob
apiVersion: batch/v1
kind: CronJob
metadata:
  name: storage-cleanup
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: cleanup
            image: storage-service:latest
            command: ["python", "scripts/cleanup_temp_files.py"]
```

---

### 3.5 Future Enhancement: Dedicated Cleanup Service

#### Design Considerations for Future Service

**Option 1: Microservice Approach**
- Create standalone `StorageHelperCleanupService`
- Runs as scheduled job (cron/k8s CronJob)
- Communicates with DataStorageService via API

**Option 2: Internal Background Task**
- Integrate cleanup as FastAPI background task
- Use APScheduler or similar scheduler
- Runs within DataStorageService process

**Recommended Architecture (Option 1):**

```
┌──────────────────────────────────┐
│  StorageHelperCleanupService     │
│  (Dedicated microservice)        │
│                                  │
│  - Scheduled execution           │
│  - Cleanup temp files            │
│  - Cleanup orphaned files        │
│  - Generate cleanup reports      │
│  - Send notifications            │
└──────────────────────────────────┘
         │
         │ REST API / Direct Storage Access
         ↓
┌──────────────────────────────────┐
│  Storage Backend                 │
│  - Supabase Storage (prod)       │
│  - Local filesystem (dev)        │
└──────────────────────────────────┘
         │
         │ Query metadata
         ↓
┌──────────────────────────────────┐
│  StorageHelperDataStorageService │
│  (Database queries)              │
└──────────────────────────────────┘
```

**Key Features for Future Service:**
1. **Smart Cleanup**
   - Check if files are referenced in database before deletion
   - Detect orphaned files (storage exists but no DB record)
   - Handle failed uploads (incomplete records)

2. **Monitoring & Reporting**
   - Track cleanup metrics (files deleted, space reclaimed)
   - Alert on anomalies (sudden spike in temp files)
   - Generate periodic reports

3. **Configurable Policies**
   - Per-user retention policies
   - Different TTL for different file types
   - Grace period for new uploads

4. **Garbage Collection**
   - Find and remove orphaned files (no DB reference)
   - Clean up database records with missing files
   - Reconcile storage and database state

**API Endpoints for Cleanup Service:**
```
POST /api/v1/cleanup/run
  - Trigger manual cleanup
  - Input: { "retention_days": 7, "dry_run": false }

GET /api/v1/cleanup/status
  - Get last cleanup status
  - Output: { "last_run": "2025-01-01T02:00:00Z", "files_deleted": 42, "space_reclaimed_mb": 125 }

POST /api/v1/cleanup/reconcile
  - Reconcile storage and database
  - Find orphaned files and missing files
```

**Database Tables for Cleanup Service:**
```sql
CREATE TABLE cleanup_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    cleanup_type VARCHAR(50),        -- "temporary", "orphaned", "manual"
    files_scanned INT,
    files_deleted INT,
    space_reclaimed_bytes BIGINT,
    retention_days INT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR(20),              -- "success", "failed", "partial"
    error_message TEXT
);

CREATE TABLE orphaned_files (
    id INT PRIMARY KEY AUTO_INCREMENT,
    file_path TEXT,
    file_size_bytes BIGINT,
    detected_at TIMESTAMP,
    resolved_at TIMESTAMP,
    resolution_action VARCHAR(50)    -- "deleted", "restored", "ignored"
);
```

**Environment Variables for Cleanup Service:**
```env
# Storage access
APP_ENV=prod
SUPABASE_URL=...
SUPABASE_KEY=...
SUPABASE_BUCKET=documents

# Cleanup policies
CLEANUP_RETENTION_DAYS=7
CLEANUP_SCHEDULE="0 2 * * *"
CLEANUP_DRY_RUN=false

# DataStorage API (for metadata queries)
STORAGE_SERVICE_URL=http://storage-service:8000

# Monitoring
CLEANUP_ALERT_THRESHOLD_MB=1000
CLEANUP_REPORT_EMAIL=admin@example.com
```

**Implementation Checklist for Future Service:**
- [ ] Create new microservice repository
- [ ] Implement storage backend connectors (Supabase + Local)
- [ ] Add database query integration with DataStorageService
- [ ] Implement cleanup policies and scheduling
- [ ] Add orphaned file detection
- [ ] Implement monitoring and alerting
- [ ] Add API endpoints for manual operations
- [ ] Create dashboard for cleanup metrics
- [ ] Write comprehensive tests
- [ ] Document deployment procedures

---

## 4. API Interface Contract

### 3.1 Document Operations

#### Create Document
```
POST /internal/documents
Input:
  {
    "owner_id": 123,
    "title": "W2 2024",
    "category_id": 1,
    "image_url": "local://owners/123/docs/456/original.jpg",
    "current_location_id": 1,
    "metadata": {"tax_year": 2024, "issuer": "Employer Inc"}
  }

Output:
  {
    "id": 456,
    "owner_id": 123,
    "title": "W2 2024",
    "category_id": 1,
    "image_url": "...",
    "current_location_id": 1,
    "created_at": "2025-01-01T00:00:00Z"
  }
```

#### Get Document
```
GET /internal/documents/{doc_id}

Output:
  {
    "id": 456,
    "owner_id": 123,
    "title": "W2 2024",
    "category_id": 1,
    "current_location_id": 1,
    "metadata": {...},
    "image_url": "...",
    "ocr_text": "...",
    "created_at": "2025-01-01T00:00:00Z"
  }
```

#### Get Documents by Owner
```
GET /internal/documents?owner_id=123

Output: List[Document]
```

#### Update Document
```
PATCH /internal/documents/{doc_id}
Input:
  {
    "title": "W2 2024 (Updated)",
    "current_location_id": 5,
    "metadata": {"tax_year": 2024, "issuer": "Employer Inc"}
  }

Output: Updated document record
```

#### Delete Document
```
DELETE /internal/documents/{doc_id}
```

---

### 3.2 Category Operations

#### Get All Categories
```
GET /internal/categories

Output:
  [
    {
      "id": 1,
      "code": "TAX",
      "name": "Tax Documents",
      "description": "...",
      "classification": null
    },
    ...
  ]
```

#### Create Category
```
POST /internal/categories
Input:
  {
    "code": "NEW_CAT",
    "name": "New Category",
    "description": "...",
    "classification": "optional"
  }
```

---

### 3.3 Location Operations

#### Create Location
```
POST /internal/locations
Input:
  {
    "name": "Bedroom desk, left drawer #2",
    "description": "...",
    "photo_url": "...",
    "parent_id": null
  }
```

#### Get All Locations
```
GET /internal/locations
Output: List[StorageLocation]
```

#### Get Location
```
GET /internal/locations/{location_id}
```

---

### 3.4 Embedding Operations

#### Get All Embeddings
```
GET /internal/embeddings?owner_id=123

Output:
  [
    {
      "document_id": 456,
      "embedding": [0.1, 0.2, 0.3, ...],
      "created_at": "2025-01-01T00:00:00Z"
    },
    ...
  ]
```

#### Create/Update Embedding
```
POST /internal/embeddings
Input:
  {
    "document_id": 456,
    "embedding": [0.1, 0.2, 0.3, ...]
  }
```

---

### 3.5 Feedback Operations

#### Submit Feedback
```
POST /internal/feedback
Input:
  {
    "document_id": 456,
    "feedback_type": "category_fix",
    "note": "This should be VISA, not TAX"
  }

Output:
  {
    "id": 789,
    "document_id": 456,
    "feedback_type": "category_fix",
    "note": "...",
    "created_at": "2025-01-01T00:00:00Z"
  }
```

---

## 5. Implementation Checklist

### Core Features
- [x] Database schema creation and initialization
- [x] FastAPI application setup
- [x] User management endpoints
- [x] Document CRUD operations
- [x] Category management
- [x] Location hierarchy support
- [x] Embedding storage and retrieval
- [x] Feedback collection
- [x] Multi-user isolation and authentication
- [x] API documentation (OpenAPI/Swagger)
- [x] Database migration scripts
- [x] Error handling and validation
- [x] Logging and monitoring
- [x] Unit and integration tests

### File Storage Features
- [x] Storage backend abstraction (local + Supabase)
- [x] Temporary file upload (preview mode)
- [x] File lifecycle management (temp → permanent)
- [x] Automatic file movement on confirm
- [x] Cleanup script for temporary files
- [ ] Scheduled cleanup job (cron/systemd)
- [ ] Orphaned file detection
- [ ] Storage metrics and monitoring
- [ ] Dedicated cleanup microservice (future)

---

## 6. Design Principles

1. **Data Integrity**: Foreign key constraints enforce referential integrity
2. **Multi-user Safety**: All operations must filter by `owner_id`
3. **Flexibility**: JSON `metadata` field allows per-category customization
4. **Auditability**: `created_at` and `updated_at` timestamps on all entities
5. **Separation of Concerns**: 
   - Categories = document classification system
   - Events = temporal grouping system
   - Locations = physical storage system
   - These are three independent dimensions

---

## 7. Future Enhancements

### Data & API Enhancements
- [ ] Tag-based organization (separate from categories)
- [ ] Sharing/permission system for documents and locations
- [ ] Bulk operations (update multiple documents)
- [ ] Search filters (by date, category, location)
- [ ] Archival/soft delete for historical documents
- [ ] Audit logs for all data modifications
- [ ] Backup and recovery procedures

### File Storage Enhancements
- [ ] Dedicated cleanup microservice (see Section 3.5)
- [ ] Storage quota management per user
- [ ] Intelligent file compression
- [ ] CDN integration for faster file access
- [ ] File versioning support
- [ ] Automated backup to secondary storage
- [ ] Storage analytics dashboard
