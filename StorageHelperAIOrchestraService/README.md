# StorageHelperAIOrchestraService

AI-powered document processing and organization service for the Home AI Paper Organizer system.

## 🎯 Overview

StorageHelperAIOrchestraService is the intelligent processing engine that:
- **Processes documents** from images and PDFs using OCR and text extraction
- **Understands content** using LLM-powered analysis (Gemini 2.5 Flash)
- **Recommends storage** locations based on document type and content
- **Enables search** through semantic vector similarity matching
- **Organizes documents** with automatic categorization and tagging

## 🚀 Features

### Multi-Format Support
- **Images**: JPG, PNG, GIF, BMP, WEBP, TIFF
- **PDFs**: Text-based and image-based PDFs (multi-page support)

### Intelligent Processing
- **OCR**: Tesseract-based text extraction with image preprocessing
- **PDF Handling**: Automatic detection of text-based vs image-based PDFs
- **Text Cleaning**: Noise removal and normalization
- **LLM Recommendations**: AI-powered category and location suggestions
- **Vector Embeddings**: Semantic search using Google's text-embedding-004 model

### Pipelines
1. **Ingestion Pipeline**: Image/PDF → OCR → Cleaning → [Recommendation + Embedding] → Storage
2. **Search Pipeline**: Query → Normalization → Embedding → Similarity Search → Results

## 📦 Installation

### Prerequisites
- Python 3.10+
- Tesseract OCR (bundled in `app/modules/tesseract/` or install system-wide)

### Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure environment:**

Create `.env.local` for local development:
```env
# Gemini API Keys
GEMINI_EMBEDDING_API_KEY=your_embedding_api_key
GEMINI_LLM_API_KEY=your_llm_api_key

# OCR Configuration
TESSERACT_LANG=eng
OCR_ENABLE_PREPROCESSING=True
```

3. **Run the service:**

**Windows:**
```powershell
.\script\start_local.ps1
```

**Linux/Mac:**
```bash
./script/start_local.sh
```

## 🔌 API Endpoints

### 1. Document Ingestion
**POST** `/api/ingestion`

Process and store a document (image or PDF).

```json
{
  "image_url": "path/to/document.pdf",
  "owner_id": 1,
  "file_type": "pdf"
}
```

**Response:**
```json
{
  "status": "completed",
  "document_id": "abc-123-uuid",
  "detected_type_code": "TAX_W2",
  "recommended_location_id": 2,
  "recommended_location_reason": "Tax documents are commonly stored in filing cabinets"
}
```

### 2. Document Search
**POST** `/api/search`

Search for documents using natural language queries.

```json
{
  "query": "Where is my W2 from 2024?",
  "owner_id": 1,
  "top_k": 5
}
```

**Response:**
```json
{
  "results": [
    {
      "document_id": "abc-123-uuid",
      "score": 0.89,
      "title": "W-2 Wage and Tax Statement",
      "snippet": "2024 tax form from employer...",
      "file_type": "pdf",
      "location": {
        "id": 2,
        "name": "Filing Cabinet - Tax Drawer",
        "photo_url": "/images/cabinet.jpg"
      }
    }
  ]
}
```

### 3. Feedback
**POST** `/api/feedback`

Submit feedback to improve recommendations.

```json
{
  "document_id": "abc-123-uuid",
  "feedback_type": "location_error",
  "note": "Document was actually in desk drawer"
}
```

## 📂 Project Structure

```
StorageHelperAIOrchestraService/
├── app/
│   ├── api/                    # REST API layer
│   ├── core/                   # Configuration
│   ├── integrations/           # External service clients
│   ├── modules/                # Core processing modules
│   │   ├── ocr.py             # OCR engine (images + PDFs)
│   │   ├── pdf_processor.py   # PDF processing
│   │   ├── embedding.py       # Vector embeddings
│   │   ├── recommendation.py  # LLM recommendations
│   │   └── ...
│   ├── pipelines/             # Orchestration workflows
│   ├── storage/               # Data persistence
│   └── ...
├── tmp/                       # Runtime storage
│   ├── documents/             # Document metadata
│   ├── embeddings/            # Vector embeddings
│   ├── images/                # Stored images
│   └── pdfs/                  # Stored PDFs
├── main.py                    # FastAPI entry point
└── requirements.txt
```

## 🧪 Testing

Run the PDF support test suite:

```bash
cd StorageHelperAIOrchestraService
python test_pdf_support.py
```

Or test via API:

```bash
curl -X POST http://localhost:8000/api/ingestion \
  -H "Content-Type: application/json" \
  -d '{
    "image_url": "path/to/test.pdf",
    "owner_id": 1,
    "file_type": "pdf"
  }'
```

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_ENV` | Environment (`local` or `prod`) | Required |
| `GEMINI_EMBEDDING_API_KEY` | Gemini API key for embeddings | Required |
| `GEMINI_LLM_API_KEY` | Gemini API key for LLM | Required |
| `TESSERACT_LANG` | OCR language | `eng` |
| `OCR_ENABLE_PREPROCESSING` | Enable image preprocessing | `True` |
| `OCR_PSM` | Tesseract page segmentation mode | `1` |

### Storage Configuration

Locations are managed in:
- `tmp/Storage/locations.json`

Document categories are now managed via API and stored in the database (see `/api/users/{user_id}/categories`).

## 📊 Architecture

### Ingestion Pipeline Flow

```
Input (Image/PDF) 
  → File Type Detection
  → OCR/Text Extraction
  → Text Cleaning
  → [Parallel]
      ├─ LLM Recommendation (Gemini)
      └─ Vector Embedding (Gemini)
  → Persistence (Local + Optional Remote)
  → Response
```

### PDF Processing Strategy

1. **Load PDF**: From URL, local path, or bytes
2. **Check for Text**: Detect if PDF has embedded text
3. **Process**:
   - **Text-based PDF**: Direct extraction (fast, accurate)
   - **Image-based PDF**: Convert to images → OCR → Combine
4. **Return**: Unified OCRResult for pipeline

## 🤝 Integration

### With DataStorageService
Optional integration for persistent database storage via `storage_client.py`.

### With WebService
Provides REST API for frontend to:
- Upload documents
- Search documents
- View recommendations

## 📖 Documentation

Full architecture documentation: [`Document/Design/ai_service/StorageHelperAIOrchestraService.md`](../../Document/Design/ai_service/StorageHelperAIOrchestraService.md)

## 🔄 Recent Updates

### PDF Support (December 3, 2025)
- ✅ Multi-format support (images + PDFs)
- ✅ Intelligent PDF processing (text extraction + OCR)
- ✅ Multi-page PDF handling (up to 10 pages)
- ✅ Auto file type detection
- ✅ Enhanced storage module
- ✅ Backward compatible with existing image processing

## 📝 License

Part of the Home AI Paper Organizer project.
