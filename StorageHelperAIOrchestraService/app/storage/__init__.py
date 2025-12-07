"""
Storage module for document storage.
Provides unified document storage via API.
"""
from app.storage.pipeline_storage import (
    PipelineStorage,
    LocationDataHandler,
    save_document,
    save_error_document,
    get_document,
    get_embedding,
    get_all_embeddings,
    get_location_info,
    update_document_metadata,
    log_feedback,
    LLM_LOCATION_FORMAT,
    DB_LOCATION_FORMAT,
)
from app.storage.output_schema import (
    DocumentOutputSchema,
    OutputField,
    default_output_schema
)

__all__ = [
    # Pipeline storage (API-based) - handles all AI Orchestration pipeline output results
    "PipelineStorage",
    "LocationDataHandler",
    "save_document",
    "save_error_document",
    "get_document",
    "get_embedding",
    "get_all_embeddings",
    "get_location_info",
    "update_document_metadata",
    "log_feedback",
    "LLM_LOCATION_FORMAT",
    "DB_LOCATION_FORMAT",
    # Output schema management
    "DocumentOutputSchema",
    "OutputField",
    "default_output_schema"
]

