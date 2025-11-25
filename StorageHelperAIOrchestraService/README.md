# StorageHelperAIOrchestraService

## Overview

`StorageHelperAIOrchestraService` is the core AI orchestration layer for the StorageHelper system.  
It coordinates all intelligent behavior: understanding user requests, running document/photo pipelines, and combining model outputs into actionable results for the user.

This service **does not own data** (no direct UI and no long-term storage).  
Instead, it orchestrates calls between external AI components (LLMs, OCR, embeddings, etc.) and the `StorageHelperDataStorageService`.

---

## Responsibilities

- **Document Ingestion Pipeline**
  - Route new uploads (cabinet/drawer/box photos, document photos, optional text prompts).
  - Call OCR / image-to-text modules to extract text from document images.
  - Run text cleaning and metadata extraction.
  - Call the Location Recommendation module to suggest where a document should be stored.
  - Prepare structured “document records” and send them to the Data Storage service.

- **Search & Retrieval Pipeline**
  - Interpret natural-language or mixed (text + photo) queries from the Web Service.
  - Normalize and classify queries (e.g., “find my car insurance policy from 2023”).
  - Run embedding models and semantic similarity search (via vector storage or APIs).
  - Combine semantic search, metadata filters, and rules into a ranked result list.
  - Return results with the best cabinet/drawer/box photo and location hints.

- **Feedback Handling**
  - Accept explicit user feedback (correct / incorrect result, moved location, etc.).
  - Log feedback for analysis and model improvement.
  - Trigger simple online updates (e.g., update metadata, re-index embeddings).
  - Optionally produce training data for future model re-training.

---

## Boundaries with Other Services

- **Talks to `StorageHelperWebService`**
  - Receives high-level user actions: “ingest this document”, “where is X?”, “suggest a location”.
  - Returns structured responses: recommended location, ranked search results, explanations.

- **Talks to `StorageHelperDataStorageService`**
  - Reads and writes structured metadata for locations and documents.
  - Reads and updates embeddings / vector indices (if implemented there).
  - Never manages raw storage details (tables, buckets, indexes) directly.

---

## Implementation Notes (TBD)

- Programming language and framework (e.g., Python/FastAPI, Node.js/NestJS, etc.).
- Integration with external AI providers (LLM, OCR, embeddings).
- Retry, timeout, and error-handling strategy for AI calls.
