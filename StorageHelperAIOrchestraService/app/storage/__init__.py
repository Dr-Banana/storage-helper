"""
Local storage module for temporary document storage.
"""
from app.storage.local_storage import (
    LocalStorage, 
    save_document, 
    get_document, 
    get_embedding,
    get_all_embeddings
)

__all__ = [
    "LocalStorage", 
    "save_document", 
    "get_document", 
    "get_embedding",
    "get_all_embeddings"
]

