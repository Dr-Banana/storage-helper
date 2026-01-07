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

## Implementation Notes

- **Database**: MySQL 8.0 (via Docker)
- **Schema**: See `schema.sql` for the complete database structure
- **Object Storage**: Supports local storage, MinIO, and S3 (see `STORAGE_PROTOCOL_USAGE.md`)
- **Vector Search**: Custom implementation with embedding storage

---

## Quick Start

### Environment Modes

The service supports three environment modes:

- **`local`** (default): Uses local database and local file storage
- **`preprod`**: Runs locally but uses Supabase storage (same as production)
- **`prod`**: Production mode, uses Supabase storage (deployed on Render)

### Local Mode (Default)

#### 1. Start the Database

From the `StorageHelperDataStorageService` directory:

**Linux/Mac:**
```bash
chmod +x scripts/*.sh
./scripts/init-db.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\init-db.ps1
```

#### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

#### 3. Run the API Server

**Linux/Mac:**
```bash
./scripts/start_local.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\start_local.ps1
```

Or manually:
```bash
python main.py
```

### Preprod Mode (Local + Supabase Storage)

Preprod mode allows you to run the service locally while using Supabase storage (same as production). This is useful for testing storage operations without deploying to production.

#### 1. Create Configuration File

Copy the example configuration:
```bash
cp .env.preprod.example .env.preprod
```

Edit `.env.preprod` and update with your Supabase credentials:
```env
APP_ENV=preprod
DATABASE_URL=postgresql://postgres:root@localhost:5432/storage_helper
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-service-role-key
SUPABASE_BUCKET=documents
```

#### 2. Run in Preprod Mode

**Linux/Mac:**
```bash
./scripts/start_preprod.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\start_preprod.ps1
```

Or manually:
```bash
export APP_ENV=preprod  # Linux/Mac
# or
$env:APP_ENV="preprod"  # Windows PowerShell
python main.py
```

**Note**: In preprod mode, files are uploaded to Supabase storage (same as production), but you can still use a local database for testing.

### 4. Access the API

- **API Documentation**: http://localhost:8000/docs (Swagger UI)
- **API Schema**: http://localhost:8000/redoc (ReDoc)
- **Health Check**: http://localhost:8000/health

### 5. Database Connection Details

**Local Mode:**
- **Host**: localhost
- **Port**: 5432 (PostgreSQL)
- **User**: postgres
- **Password**: root
- **Database**: storage_helper

**Preprod Mode:**
- Can use local database (same as above) or Supabase database
- Storage uses Supabase (same as production)

### 6. Common Commands

**Stop the database:**
```bash
docker-compose down
```

**Force refresh database (delete all data):**
```bash
./scripts/refresh-db.sh
```

**View logs:**
```bash
docker-compose logs mysql
```

**Access MySQL CLI:**
```bash
docker-compose exec mysql mysql -uroot -proot storage_helper
```

For more details, see:
- `db_local_setup_guide.md` - Database setup guide
- `STORAGE_PROTOCOL_USAGE.md` - Storage protocol documentation
- `PREPROD_SETUP.md` - Preprod mode setup guide

---

## API & Database Documentation

For comprehensive documentation on all API endpoints and database schema, see:
- **[API & Database Documentation](./API_DATABASE_DOCUMENTATION.md)** - Complete reference guide

This document includes:
- All HTTP API endpoints with request/response examples
- Full database schema with table descriptions
- Data relationships and design decisions
- Common query patterns
- Environment configuration
