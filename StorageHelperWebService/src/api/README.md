# API Services

This directory contains all API-related code. **All API calls must go through the services defined in `services.ts`**.

## Structure

```
api/
├── client.ts      # Axios client configuration
├── services.ts    # All API service functions (USE THIS)
└── README.md       # This file
```

## Usage

### ✅ Correct: Import from services.ts

```typescript
import { userService, documentService, Document } from '../api/services'

// Use the services
const users = await userService.getAll()
const doc = await documentService.getById(1)
```

### ✅ Also Correct: Use default export

```typescript
import api from '../api/services'

// Use the services
const users = await api.users.getAll()
const doc = await api.documents.getById(1)
```

### ❌ Wrong: Direct API client usage

```typescript
// DON'T DO THIS
import apiClient from '../api/client'
const response = await apiClient.get('/users')
```

## Available Services

### User Service (`userService`)

- `getAll()` - Get all users
- `getById(id)` - Get user by ID
- `create(data)` - Create a new user
- `update(id, data)` - Update user
- `delete(id)` - Delete user
- `getDocuments(userId)` - Get all document IDs for a user

### Document Service (`documentService`)

- `getById(id)` - Get document by ID
- `getPages(documentId)` - Get all pages for a document
- `uploadAndProcess(formData)` - Upload and process a document page
- `saveOcrAndEmbedding(documentId, ocrText, embedding)` - Save OCR and embedding
- `updateStatus(documentId, statusValue, metadata?)` - Update document status
- `searchSimilar(embedding, limit, ownerId?)` - Search for similar documents

### Category Service (`categoryService`)

- `getAll()` - Get all document categories (TODO: API endpoint not available yet)

### Location Service (`locationService`)

- `getAll()` - Get all storage locations (TODO: API endpoint not available yet)

### Event Service (`eventService`)

- `getAll()` - Get all events (TODO: API endpoint not available yet)

## Type Definitions

All TypeScript interfaces are exported from `services.ts`:

- `User`
- `Document`
- `DocumentPage`
- `DocumentCategory`
- `StorageLocation`
- `Event`

## Adding New APIs

When adding new API endpoints:

1. Add the API call to the appropriate service in `services.ts`
2. Add JSDoc comments describing the endpoint
3. Export the function from the service object
4. Update this README if needed

**Never add API calls directly in components or pages. Always add them to `services.ts`.**

