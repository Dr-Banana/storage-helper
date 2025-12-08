# StorageHelper Data Storage Service - API & Database Documentation

## Overview

This document provides a comprehensive guide to the `StorageHelperDataStorageService` API endpoints and database schema.

---

## API Endpoints

### 1. Document Page Management (`/api/documents`)

#### Get all pages for a document
```
GET /api/documents/{document_id}/pages
```

Returns all page IDs for a specific document.

**Parameters:**
- `document_id` (path, int, required) - Document ID

**Response (201):**
```json
{
  "document_id": 1,
  "total": 5,
  "page_ids": [1, 2, 3, 4, 5]
}
```

**Error Responses:**
- `404` - Document not found

---

### 2. User Management (`/api/users`)

#### Get all documents for a user
```
GET /api/users/{user_id}/documents
```

Returns all document IDs owned by a specific user.

**Parameters:**
- `user_id` (path, int, required) - User ID

**Response:**
```json
{
  "user_id": 1,
  "total": 3,
  "document_ids": [1, 2, 3]
}
```

**Error Responses:**
- `404` - User not found

---

#### Create a user
```
POST /api/users
```

**Request Body:**
```json
{
  "display_name": "John Doe",
  "note": "Optional note"
}
```

**Response (201):**
```json
{
  "id": 1,
  "display_name": "John Doe",
  "note": "Optional note",
  "created_at": "2024-12-07T10:00:00",
  "updated_at": "2024-12-07T10:00:00"
}
```

---

#### Get all users
```
GET /api/users
```

**Response:**
```json
{
  "total": 2,
  "users": [
    {
      "id": 1,
      "display_name": "John Doe",
      "note": "Optional note",
      "created_at": "2024-12-07T10:00:00",
      "updated_at": "2024-12-07T10:00:00"
    }
  ]
}
```

---

#### Get user by ID
```
GET /api/users/{user_id}
```

**Parameters:**
- `user_id` (path, int, required) - User ID

**Response:**
```json
{
  "id": 1,
  "display_name": "John Doe",
  "note": "Optional note",
  "created_at": "2024-12-07T10:00:00",
  "updated_at": "2024-12-07T10:00:00"
}
```

---

#### Update user
```
PATCH /api/users/{user_id}
```

**Parameters:**
- `user_id` (path, int, required) - User ID

**Request Body (all optional):**
```json
{
  "display_name": "Jane Doe",
  "note": "Updated note"
}
```

---

#### Delete user
```
DELETE /api/users/{user_id}
```

**Parameters:**
- `user_id` (path, int, required) - User ID

**Response:** `204 No Content`

---

### 3. Document Page Upload (`/api/v1/documents/upload-and-process`)

#### Upload document page with OCR

```
POST /api/v1/documents/upload-and-process
```

Upload a document page image with OCR extracted text. Creates a new document if `document_id` is not provided, otherwise adds page to existing document.

**Parameters (form-data):**
- `file` (file, required) - Document page image file
- `owner_id` (int, required) - Document owner user ID
- `page_number` (int, required) - Page number within document (1-indexed)
- `ocr_text` (string, required) - OCR extracted text for this page
- `document_id` (int, optional) - Existing document ID. If not provided, creates new document

**Response (201):**
```json
{
  "document_id": 1,
  "page_id": 5,
  "status": "created",
  "page_number": 1
}
```

**Status Values:**
- `"created"` - New document was created (document_id was null)
- `"updated"` - Page was added to existing document

**Error Responses:**
- `400` - Invalid parameters, user not found, or document doesn't belong to user
- `500` - Server error

**Example cURL:**
```bash
curl -X POST http://localhost:8000/api/v1/documents/upload-and-process \
  -F "file=@page1.jpg" \
  -F "owner_id=1" \
  -F "page_number=1" \
  -F "ocr_text=Sample OCR extracted text" \
  -F "document_id=1"
```

---

### 4. Other Document APIs (`/api/v1`)

#### Save OCR and embedding
```
POST /api/v1/documents/{document_id}/save-ocr-and-embedding
```

Save OCR text and vector embedding for semantic search.

**Parameters (form-data):**
- `ocr_text` (string, required) - Extracted text from OCR
- `embedding` (list[float], required) - Vector embedding

**Response:**
```json
{
  "document_id": 1,
  "status": "saved",
  "ocr_length": 234,
  "embedding_dimensions": 768
}
```

---

#### Get document details
```
GET /api/v1/documents/{document_id}
```

Returns document metadata and OCR text.

**Response:**
```json
{
  "id": 1,
  "filename": "tax_form_2024.pdf",
  "url": "s3://bucket/documents/1/tax_form_2024.jpg",
  "owner_id": 1,
  "ocr_text": "Sample OCR text...",
  "created_at": "2024-12-07T10:00:00"
}
```

---

#### Search documents by embedding
```
POST /api/v1/documents/search-similar
```

Search for semantically similar documents.

**Parameters (form-data):**
- `embedding` (list[float], required) - Query embedding vector
- `limit` (int, optional, default=10) - Maximum results
- `owner_id` (int, optional) - Filter by owner

**Response:**
```json
{
  "count": 3,
  "documents": [
    {
      "id": 1,
      "filename": "document1.pdf",
      "owner_id": 1
    }
  ]
}
```

---

#### Update document status
```
PATCH /api/v1/documents/{document_id}/status
```

Update document processing status and metadata.

**Parameters (form-data):**
- `status_value` (string, required) - Processing status (processing, completed, failed, etc.)
- `metadata` (dict, optional) - Additional metadata

**Response:**
```json
{
  "id": 1,
  "status": "completed",
  "updated_at": "2024-12-07T10:00:00"
}
```

---

## Database Schema

### Table: user
User accounts in the system.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| display_name | VARCHAR(100) | NOT NULL | User's display name |
| note | TEXT | | Optional note about user |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE | |

---

### Table: document_category
Document types (TAX, VISA, MED, INS, etc.).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| code | VARCHAR(50) | NOT NULL, UNIQUE | e.g., "TAX", "VISA", "MED", "INS" |
| name | VARCHAR(100) | NOT NULL | Display name |
| description | TEXT | | Category description |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE | |

---

### Table: storage_location
Physical storage locations (cabinet, drawer, box, etc.).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| name | VARCHAR(100) | NOT NULL | e.g., "Bedroom desk, left drawer #2" |
| description | TEXT | | Location description |
| photo_url | TEXT | | URL to location photo |
| parent_id | INT | FOREIGN KEY (self-referential) | For hierarchical locations |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE | |

---

### Table: event
Contextual groupings (e.g., "2024 Tax Filing", "Q2 Dental Visit").

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| name | VARCHAR(200) | NOT NULL | e.g., "2024 Tax Filing" |
| start_date | DATE | | Event start date |
| end_date | DATE | | Event end date |
| description | TEXT | | Event description |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE | |

---

### Table: document
Core document metadata.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| title | VARCHAR(255) | | Document title |
| category_id | INT | FOREIGN KEY (document_category.id, RESTRICT) | Document type |
| owner_id | INT | FOREIGN KEY (user.id, CASCADE), NOT NULL | Document owner |
| event_id | INT | FOREIGN KEY (event.id, SET NULL) | Associated event |
| current_location_id | INT | FOREIGN KEY (storage_location.id, SET NULL) | Storage location |
| metadata | JSON | | Flexible metadata {tax_year, expiry_date, issuer_name, etc.} |
| image_url | TEXT | | Thumbnail/first page URL (optional) |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE | |

**Indexes:**
- `INDEX idx_owner_id (owner_id)` - For querying user's documents
- `INDEX idx_category_id (category_id)` - For filtering by category

---

### Table: document_page
Individual pages within a document (supports multi-page documents like PDFs).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| document_id | INT | FOREIGN KEY (document.id, CASCADE), NOT NULL | Parent document |
| page_number | INT | NOT NULL | Page number (1-indexed) |
| image_url | TEXT | NOT NULL | URL to page image |
| ocr_text | LONGTEXT | | OCR extracted text for this page |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE | |

**Constraints:**
- `UNIQUE KEY (document_id, page_number)` - Ensures no duplicate pages

---

### Table: document_embedding
Vector embeddings for semantic search.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| document_id | INT | PRIMARY KEY, FOREIGN KEY (document.id, CASCADE) | |
| embedding | JSON | NOT NULL | Vector [0.123, -0.98, ...] |

---

### Table: feedback_message
User feedback for system improvement.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | INT | PRIMARY KEY, AUTO_INCREMENT | |
| document_id | INT | FOREIGN KEY (document.id, CASCADE) | Associated document |
| feedback_type | VARCHAR(50) | | e.g., "type_fix", "location_error", "metadata_fix" |
| note | TEXT | | Feedback text |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |

---

## Data Model Relationships

```
user (1) ─────────────── (*) document
  │
  └─ Has many documents
     └─ owns

document (1) ──────────── (*) document_page
  │                         │
  ├─ image_url              ├─ Contains page images
  │  (thumbnail/first page) └─ Contains page OCR text
  │
  ├─ category_id ───→ document_category
  ├─ event_id ───→ event
  ├─ current_location_id ───→ storage_location
  └─ (1) ───→ document_embedding
     └─ For semantic search

document (1) ───────────── (*) feedback_message
  └─ User corrections and feedback

storage_location (1) ──── (*) storage_location
  └─ parent_id (self-referential for hierarchical organization)
```

---

## Key Design Decisions

### 1. Multi-page Document Support
- Each document can have multiple pages via `document_page` table
- Each page stores its own image URL and OCR text
- `document.image_url` serves as thumbnail (first page) for quick display

### 2. Flexible Metadata
- Documents support JSON `metadata` field for flexible field storage
- Enables per-document-type fields without schema changes
- Example: `{"tax_year": 2024, "issuer_name": "IRS", "expiry_date": "2026-01-01"}`

### 3. Hierarchical Storage Locations
- Locations can have parent locations via `parent_id`
- Enables organizational hierarchy: Cabinet → Drawer → Box

### 4. Semantic Search Support
- `document_embedding` table stores vector embeddings
- Enables semantic similarity search across documents
- Embeddings stored as JSON for flexibility

### 5. User Feedback
- `feedback_message` table captures user corrections
- Helps improve categorization and metadata extraction
- Supports multiple feedback types

### 6. Performance Optimization
- Indexes on `document.owner_id` and `document.category_id` for fast queries
- Unique constraint on (document_id, page_number) prevents duplicates
- Foreign key cascades for data integrity

---

## Common Queries

### Get all pages for a document
```python
# Python/SQLAlchemy
pages = db.query(DocumentPage)\
    .filter(DocumentPage.document_id == document_id)\
    .order_by(DocumentPage.page_number)\
    .all()
```

### Get all documents for a user
```python
documents = db.query(Document)\
    .filter(Document.owner_id == user_id)\
    .all()
```

### Get document with all pages
```python
document = db.query(Document)\
    .filter(Document.id == document_id)\
    .first()

pages = document.pages  # SQLAlchemy relationship
```

### Search similar documents
```python
# Find documents with similar embeddings
# (implementation depends on vector database backend)
```

---

## Environment Configuration

See `.env.local` for configuration:

```env
# Database connection
DATABASE_URL=mysql+pymysql://root:root@localhost:3306/storage_helper

# Storage
STORAGE_LOCAL_PATH=./tmp

# Logging
LOG_LEVEL=INFO

# API
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=true
```
