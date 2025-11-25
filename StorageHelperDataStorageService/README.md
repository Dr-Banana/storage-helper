# StorageHelperDataStorageService

## Overview

`StorageHelperDataStorageService` is the dedicated data persistence layer for the StorageHelper system.  
It abstracts all access to databases, object storage, and optional vector search, so that other services do not need to know any storage details.

This service is responsible for **data correctness, schema evolution, and performant queries**.

---

## Responsibilities

- **Location & Container Storage**
  - Store metadata for physical storage locations (cabinets, drawers, boxes, folders).
  - Maintain human-readable names, photos, short descriptions, and IDs.
  - Track which documents are currently stored in which locations.

- **Document Storage**
  - Store metadata for each uploaded document (e.g., title, type, tags, timestamps).
  - Store OCR text or text summary references.
  - Keep links to raw images in object storage (not the images themselves, if using S3/GCS, etc.).

- **Object Storage Integration**
  - Manage keys/paths for cabinet photos and document photos in external object storage.
  - Provide signed URLs or other access patterns for the Web Service or AI Service when needed.

- **Vector & Search Storage (Optional)**
  - Store text/document embeddings for semantic search.
  - Expose APIs for similarity search (k-NN) over embeddings.
  - Manage re-indexing when documents or locations are updated.

---

## Boundaries with Other Services

- **Used by `StorageHelperAIOrchestraService`**
  - For reading/writing document and location metadata.
  - For embedding storage and similarity queries if vector search is handled here.

- **Used by `StorageHelperWebService`**
  - For simple CRUD endpoints (e.g., list locations, fetch a document’s metadata) when no AI logic is required.

The Data Storage service should provide **stable APIs** and handle **schema migrations** without breaking callers.

---

## Implementation Notes (TBD)

- Relational database choice (e.g., PostgreSQL/MySQL) and initial schema.
- Object storage provider (e.g., AWS S3, GCS) and naming conventions.
- Vector search solution (e.g., pgvector, OpenSearch, dedicated vector DB, or custom implementation).
