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

**Technology Stack:**
- **Database**: MySQL 8.0+
- **Framework**: FastAPI (Python) or similar REST framework
- **ORM**: SQLAlchemy (if using Python)
- **File Storage**: Local or cloud-based storage for images and documents

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

## 3. API Interface Contract

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

## 4. Implementation Checklist

- [ ] Database schema creation and initialization
- [ ] FastAPI application setup
- [ ] User management endpoints
- [ ] Document CRUD operations
- [ ] Category management
- [ ] Location hierarchy support
- [ ] Embedding storage and retrieval
- [ ] Feedback collection
- [ ] Multi-user isolation and authentication
- [ ] API documentation (OpenAPI/Swagger)
- [ ] Database migration scripts
- [ ] Error handling and validation
- [ ] Logging and monitoring
- [ ] Unit and integration tests

---

## 5. Design Principles

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

## 6. Future Enhancements

- [ ] Tag-based organization (separate from categories)
- [ ] Sharing/permission system for documents and locations
- [ ] Bulk operations (update multiple documents)
- [ ] Search filters (by date, category, location)
- [ ] Archival/soft delete for historical documents
- [ ] Audit logs for all data modifications
- [ ] Backup and recovery procedures
