# Storage Protocol

## URI Format

```
{backend}://{key}

backend: local | minio | s3
key: owners/{owner_id}/docs/{doc_id}/original.jpg

where:
  owner_id = user.id (INT)
  doc_id = document.id (INT)
```

Example: `local://owners/123/docs/456/original.jpg`

---

## Key Naming Convention

```
owners/{owner_id}/docs/{doc_id}/original.jpg       # Document image
owners/{owner_id}/embeddings/{doc_id}.json         # Document embedding
shared/locations/{location_id}/photo.jpg           # Location photo

where:
  owner_id = user.id (INT)
  doc_id = document.id (INT)
  location_id = storage_location.id (INT)
```

---

## Storage Operations

### Upload File
```
POST /internal/storage/upload
Input:
  - key: storage key (e.g., "owners/123/docs/456/original.jpg")
  - file_bytes: file content
  - document_id: (optional) database record ID (INT)

Output:
  - storage_uri: URI with backend prefix (e.g., "local://owners/123/docs/456/original.jpg")
  
Behavior:
  - Uploads file to configured backend (local/minio/s3)
  - Saves storage_uri to database: UPDATE document SET image_url = storage_uri WHERE id = document_id
  - Returns storage_uri
```

### Get Accessible URL
```
POST /internal/storage/resolve-url
Input:
  - storage_uri: storage URI (e.g., "local://owners/123/docs/456/original.jpg")

Output:
  - accessible_url: HTTP/file URL for accessing the file
  
Example:
  Input:  "local://owners/123/docs/456/original.jpg"
  Output: "file:///storage/owners/123/docs/456/original.jpg"
```

### Delete File
```
DELETE /internal/storage/{key}
Deletes file from backend and removes URI from database
```

### Update Document
```
PATCH /internal/documents/{doc_id}
Input:
  {
    "current_location_id": 1,
    "title": "W2 2024",
    "metadata": {"tax_year": 2024, "issuer": "Employer Inc"}
  }

Output:
  Updated document record with all fields

Behavior:
  - Updates document fields in database
  - Returns updated document
```

---

## Data Query Operations

### Get All Embeddings
```
GET /internal/embeddings?owner_id=123

Output:
  - List of documents with embeddings for semantic search
  [{
    "document_id": 456,
    "embedding": [0.1, 0.2, 0.3, ...],
    "metadata": {...}
  }, ...]
```

### Get Document
```
GET /internal/documents/{doc_id}

Output:
  - Document record with all metadata
  {
    "id": 456,
    "owner_id": 123,
    "title": "W2 2024",
    "image_url": "local://owners/123/docs/456/original.jpg",
    "ocr_text": "...",
    "current_location_id": 1,
    "created_at": "2025-01-01T00:00:00",
    "metadata": {...}
  }
```

### Get Documents by Owner
```
GET /internal/documents?owner_id=123

Output:
  - List of documents for an owner
  [{...}, {...}]
```

### Create Document
```
POST /internal/documents
Input:
  {
    "owner_id": 123,
    "title": "W2 2024",
    "image_url": "local://owners/123/docs/456/original.jpg",  (from file upload)
    "current_location_id": 1,
    "metadata": {"tax_year": 2024}
  }

Output:
  {
    "id": 456,
    "owner_id": 123,
    "title": "W2 2024",
    "image_url": "local://owners/123/docs/456/original.jpg",
    "current_location_id": 1,
    "created_at": "2025-01-01T00:00:00",
    "metadata": {...}
  }
```

### Get Location
```
GET /internal/locations/{location_id}

Output:
  {
    "id": 1,
    "name": "Cabinet A",
    "description": "...",
    "photo_url": "shared://locations/1/photo.jpg"
  }
```

### Get All Locations
```
GET /internal/locations

Output:
  - List of all locations
  [{
    "id": 1,
    "name": "Cabinet A",
    "description": "...",
    "photo_url": "shared://locations/1/photo.jpg"
  }, ...]
```

### Create Location
```
POST /internal/locations
Input:
  {
    "name": "Bedroom desk, left drawer #2",
    "description": "...",
    "photo_url": "shared://locations/1/photo.jpg"  (optional, from file upload)
  }

Output:
  {
    "id": 1,
    "name": "Bedroom desk, left drawer #2",
    "description": "...",
    "photo_url": "shared://locations/1/photo.jpg"
  }
```

### Get All Categories
```
GET /internal/categories

Output:
  - List of all document categories
  [{
    "id": 1,
    "code": "TAX",
    "name": "Tax Documents",
    "description": "..."
  }, ...]
```

### Get Document Types by Category
```
GET /internal/types?category_id=1

Output:
  - List of document types in a category
  [{
    "id": 1,
    "code": "TAX_W2",
    "name": "W2 Form",
    "category_id": 1,
    "description": "..."
  }, ...]
```

### Submit Feedback
```
POST /internal/feedback
Input:
  {
    "document_id": 456,
    "feedback_type": "type_fix",  # or "location_error", "metadata_fix"
    "note": "This should be categorized as VISA, not TAX"
  }

Output:
  {
    "id": 1,
    "document_id": 456,
    "feedback_type": "type_fix",
    "note": "...",
    "created_at": "2025-01-01T00:00:00"
  }
```

---

## How Services Use It

### Phase 1 Verification: Basic Storage & Retrieval

```
Step 1: Create location with photo
  POST /internal/storage/upload
    key: "shared/locations/1/photo.jpg"
    file_bytes: <image data>
  ← Returns: "local://shared/locations/1/photo.jpg"

  POST /internal/locations
    name: "Bedroom desk, left drawer #2"
    photo_url: "local://shared/locations/1/photo.jpg"
  ← Returns: location with id=1

Step 2: Create document with image
  POST /internal/storage/upload
    key: "owners/123/docs/456/original.jpg"
    document_id: 456
    file_bytes: <image data>
  ← Returns: "local://owners/123/docs/456/original.jpg"

  POST /internal/documents
    owner_id: 123
    title: "W2 2024"
    image_url: "local://owners/123/docs/456/original.jpg"
    current_location_id: 1
  ← Returns: document with id=456

Step 3: Query document and its location
  GET /internal/documents/456
  ← Returns: document with current_location_id=1

  GET /internal/locations/1
  ← Returns: location with photo_url

Complete!
```

### AIOrchestraService (Upload)
```
1. Process document (OCR, embedding, etc.)
2. Generate key: "owners/123/docs/456/original.jpg"  (doc_id from database)
3. Call DataStorageService: POST /internal/storage/upload
4. Get back: "local://owners/123/docs/456/original.jpg"
5. Done! URI already saved to database by DataStorageService
```

### AIOrchestraService (Search)
```
1. Receive search query from frontend
2. Get all embeddings: GET /internal/embeddings?owner_id=123
3. Perform similarity search locally using embeddings
4. For each result, get full document: GET /internal/documents/{doc_id}
5. Get location info: GET /internal/locations/{location_id}
6. Return search results
```

### WebService (Retrieve & Display)
```
1. Get documents: GET /internal/documents?owner_id=123
2. For each document, resolve image URL:
   POST /internal/storage/resolve-url with storage_uri
3. Get location photo:
   POST /internal/storage/resolve-url with location photo URI
4. Return to frontend
```

---

## Backend Configuration (These are examples)

```bash
# Set one of:
STORAGE_BACKEND=local      # Local filesystem (development)
STORAGE_BACKEND=minio      # MinIO (staging)
STORAGE_BACKEND=s3         # AWS S3 (production)

# For local backend:
LOCAL_STORAGE_PATH=/data/storage

# For MinIO:
MINIO_ENDPOINT=localhost:9000
MINIO_BUCKET=storage-helper

# For S3:
AWS_S3_BUCKET=storage-helper
AWS_REGION=us-east-1
```

---

## Design Principles

✅ **DataStorageService owns all storage layer logic** - file upload, URI generation, database persistence

✅ **URI format encodes backend** - `local://`, `minio://`, `s3://` prefixes indicate storage location

✅ **Other services never touch storage database** - only DataStorageService updates storage URIs

✅ **Backend switching is configuration only** - change `STORAGE_BACKEND` env var, no code changes needed
