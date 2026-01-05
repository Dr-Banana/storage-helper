# File Upload Server

This server handles temporary file uploads for the StorageHelperWebService frontend.

## Setup

1. Install dependencies:
```bash
npm install
```

2. Start the server:
```bash
npm run server
```

Or with auto-reload during development:
```bash
npm run dev:server
```

The server will run on `http://localhost:3001`

## API Endpoints

### POST /api/v1/files/upload-temp

Upload a file to temporary storage and get the absolute file path.

**Request:**
- Method: POST
- Content-Type: multipart/form-data
- Body: `file` (file to upload)

**Response:**
```json
{
  "file_path": "/absolute/path/to/file",
  "filename": "unique-filename.ext"
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "ok"
}
```

## File Storage

Files are stored in `tmp/temp_uploads/` directory relative to the project root.


