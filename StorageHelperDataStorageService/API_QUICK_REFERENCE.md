# Quick API Reference

## Document Page Upload (Main Entry Point)

```
POST /api/v1/documents/upload-and-process
```

**Create new document with first page:**
```bash
curl -X POST http://localhost:8000/api/v1/documents/upload-and-process \
  -F "file=@page1.jpg" \
  -F "owner_id=1" \
  -F "page_number=1" \
  -F "ocr_text=Extracted text..."
```

**Response:**
```json
{
  "document_id": 1,
  "page_id": 5,
  "status": "created",
  "page_number": 1
}
```

**Add another page to existing document:**
```bash
curl -X POST http://localhost:8000/api/v1/documents/upload-and-process \
  -F "file=@page2.jpg" \
  -F "owner_id=1" \
  -F "page_number=2" \
  -F "ocr_text=More text..." \
  -F "document_id=1"
```

**Response:**
```json
{
  "document_id": 1,
  "page_id": 6,
  "status": "updated",
  "page_number": 2
}
```

---

## Document Queries

Get all pages for a document:
```
GET /api/documents/{document_id}/pages
```

Get all documents for a user:
```
GET /api/users/{user_id}/documents
```

---

## User Management

Create user:
```
POST /api/users
```

Get user:
```
GET /api/users/{user_id}
```

List all users:
```
GET /api/users
```

Update user:
```
PATCH /api/users/{user_id}
```

Delete user:
```
DELETE /api/users/{user_id}
```

---

## Other Document APIs

Save OCR and embedding:
```
POST /api/v1/documents/{document_id}/save-ocr-and-embedding
```

Get document details:
```
GET /api/v1/documents/{document_id}
```

Search similar documents:
```
POST /api/v1/documents/search-similar
```

Update document status:
```
PATCH /api/v1/documents/{document_id}/status
```

---

## Key Tables

- **document** - Documents (title, owner, category, location, metadata)
- **document_page** - Pages within documents (image_url, ocr_text)
- **user** - Users
- **document_category** - Document types
- **storage_location** - Physical locations
- **event** - Contextual groupings
- **document_embedding** - Vector embeddings for search
- **feedback_message** - User feedback

---

## Relationships

```
user → documents → document_pages
user → documents → document_embedding
document → feedback_messages
```

---

## Database Setup

```bash
./scripts/init-db.sh          # Initialize database
./scripts/refresh-db.sh       # Reset database (delete all data)
```

---

## Start Server

```bash
python main.py                # Start with auto-reload
# or
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API docs available at:
- http://localhost:8000/docs (Swagger UI)
- http://localhost:8000/redoc (ReDoc)
