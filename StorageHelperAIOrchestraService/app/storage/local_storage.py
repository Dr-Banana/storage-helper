"""
Local file-based storage for documents, embeddings, and metadata.
Used as a simple temporary database for development and testing.
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
import uuid

logger = logging.getLogger(__name__)

# Default storage directory
STORAGE_DIR = Path(__file__).parent.parent.parent / "tmp"
DOCUMENTS_DIR = STORAGE_DIR / "documents"
EMBEDDINGS_DIR = STORAGE_DIR / "embeddings"  # Separate directory for embeddings
IMAGES_DIR = STORAGE_DIR / "images"  # Directory for storing document images
INDEX_FILE = STORAGE_DIR / "index.json"


class LocalStorage:
    """
    Simple file-based storage system for documents and embeddings.
    Stores documents as JSON files and maintains an index for quick lookups.
    """
    
    def __init__(self, storage_dir: Optional[Path] = None):
        """
        Initialize local storage.
        
        :param storage_dir: Root directory for storage. Defaults to project_root/tmp
        """
        self.storage_dir = storage_dir or STORAGE_DIR
        self.documents_dir = self.storage_dir / "documents"
        self.embeddings_dir = self.storage_dir / "embeddings"  # Separate embeddings directory
        self.images_dir = self.storage_dir / "images"  # Directory for storing document images
        self.index_file = self.storage_dir / "index.json"
        
        # Create directories if they don't exist
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.embeddings_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize index if it doesn't exist
        self._ensure_index()
    
    def _ensure_index(self):
        """Ensure index file exists and is valid."""
        if not self.index_file.exists():
            self._write_index({})
        else:
            # Validate index file
            try:
                self._read_index()
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Index file corrupted, recreating: {e}")
                self._write_index({})
    
    def _read_index(self) -> Dict[str, Any]:
        """Read the index file."""
        with open(self.index_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _write_index(self, index: Dict[str, Any]):
        """Write the index file."""
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
    
    def _save_embedding(self, doc_id: str, embedding: List[float], embedding_dimension: int) -> bool:
        """
        Save embedding separately from document.
        
        :param doc_id: Document ID to associate with embedding
        :param embedding: Embedding vector
        :param embedding_dimension: Dimension of the embedding vector
        :return: True if saved successfully
        """
        if not embedding or embedding_dimension == 0:
            logger.warning(f"No embedding to save for document {doc_id}")
            return False
        
        # Create embedding record (reference to document)
        embedding_record = {
            "document_id": doc_id,  # Link to document
            "embedding": embedding,
            "dimension": embedding_dimension,
            "created_at": datetime.now().isoformat()
        }
        
        # Save embedding file (same ID as document for easy lookup)
        embedding_file = self.embeddings_dir / f"{doc_id}.json"
        with open(embedding_file, 'w', encoding='utf-8') as f:
            json.dump(embedding_record, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Embedding saved separately: {doc_id} -> {embedding_file}")
        return True
    
    def _load_embedding(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Load embedding for a document.
        
        :param doc_id: Document ID
        :return: Embedding record or None if not found
        """
        embedding_file = self.embeddings_dir / f"{doc_id}.json"
        
        if not embedding_file.exists():
            return None
        
        try:
            with open(embedding_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading embedding for {doc_id}: {e}")
            return None
    
    def save_image(self, image_path: str, doc_id: str) -> Optional[str]:
        """
        Save an image file to the images directory, associated with a document ID.
        
        :param image_path: Path to the source image file (local path or URL)
        :param doc_id: Document ID to associate with the image
        :return: Path to the saved image file, or None if saving failed
        """
        try:
            import shutil
            import httpx
            
            source_path = Path(image_path)
            
            # Determine file extension from source
            if source_path.suffix:
                ext = source_path.suffix
            else:
                # Try to determine from URL or default to .jpg
                if image_path.startswith(('http://', 'https://')):
                    # For URLs, try to get extension from URL or default to .jpg
                    url_path = Path(image_path.split('?')[0])  # Remove query parameters
                    ext = url_path.suffix or '.jpg'
                else:
                    ext = '.jpg'
            
            # Create destination path with document ID
            dest_path = self.images_dir / f"{doc_id}{ext}"
            
            # If source is a local file, copy it
            if source_path.exists() and source_path.is_file():
                shutil.copy2(source_path, dest_path)
                logger.info(f"Image copied from {source_path} to {dest_path}")
                return str(dest_path)
            # If source is a URL, download it synchronously
            elif image_path.startswith(('http://', 'https://')):
                try:
                    with httpx.Client(timeout=30.0) as client:
                        response = client.get(image_path)
                        response.raise_for_status()
                        with open(dest_path, 'wb') as f:
                            f.write(response.content)
                    logger.info(f"Image downloaded from {image_path} to {dest_path}")
                    return str(dest_path)
                except Exception as e:
                    logger.error(f"Failed to download image from URL {image_path}: {e}")
                    return None
            else:
                logger.warning(f"Image source not found or invalid: {image_path}")
                return None
                
        except Exception as e:
            logger.error(f"Error saving image for document {doc_id}: {e}", exc_info=True)
            return None
    
    def save_document(self, document_data: Dict[str, Any]) -> str:
        """
        Save a document with its embedding and metadata to local storage.
        Embedding is saved separately in embeddings/ folder and linked via doc_id.
        
        :param document_data: Dictionary containing document data (text, embedding, metadata, etc.)
        :return: Document ID (UUID string)
        """
        # Generate unique document ID
        doc_id = str(uuid.uuid4())
        
        # Extract embedding before saving document (to save separately)
        embedding = document_data.get("embedding", [])
        embedding_dimension = document_data.get("embedding_dimension", len(embedding) if embedding else 0)
        
        # Save image if image_path is provided
        image_path = document_data.get("image_path")
        saved_image_path = None
        if image_path:
            saved_image_path = self.save_image(image_path, doc_id)
        
        # Prepare document record (without full embedding, just reference)
        document_record = {
            "id": doc_id,
            "created_at": datetime.now().isoformat(),
            "owner_id": document_data.get("owner_id"),
            "source": document_data.get("source"),  # Original source (URL or path)
            "image_path": saved_image_path,  # Path to saved image in images/ directory
            "extracted_text": document_data.get("extracted_text", ""),
            "ocr_confidence": document_data.get("ocr_confidence"),
            # Note: embedding is NOT stored in document file, only reference via doc_id
            "has_embedding": len(embedding) > 0,
            "embedding_dimension": embedding_dimension,
            "recommendation_data": document_data.get("recommendation_data"),
            "recommendation_status": document_data.get("recommendation_status"),
            "metadata": {
                "processing_steps": document_data.get("processing_steps", []),
                "status": document_data.get("status", "unknown"),
            }
        }
        
        # Save document file (without embedding)
        doc_file = self.documents_dir / f"{doc_id}.json"
        with open(doc_file, 'w', encoding='utf-8') as f:
            json.dump(document_record, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Document saved: {doc_id} -> {doc_file}")
        
        # Save embedding separately if available
        if embedding:
            self._save_embedding(doc_id, embedding, embedding_dimension)
        
        # Update index
        index = self._read_index()
        
        # Create index entry
        index_entry = {
            "id": doc_id,
            "owner_id": document_record["owner_id"],
            "created_at": document_record["created_at"],
            "has_embedding": document_record.get("has_embedding", False),
            "text_preview": document_record["extracted_text"][:200] if document_record["extracted_text"] else "",
        }
        
        # Add recommendation info to index if available
        if document_record.get("recommendation_data"):
            rec_data = document_record["recommendation_data"]
            index_entry["suggested_location"] = rec_data.get("suggested_location")
            index_entry["suggested_tags"] = rec_data.get("suggested_tags", [])
        
        # Store by owner_id for easy filtering
        owner_id = str(document_record["owner_id"])
        if owner_id not in index:
            index[owner_id] = []
        
        index[owner_id].append(index_entry)
        
        # Also create a global index for quick lookup by doc_id
        if "documents" not in index:
            index["documents"] = {}
        index["documents"][doc_id] = index_entry
        
        # Update index file
        self._write_index(index)
        
        return doc_id
    
    def get_document(self, doc_id: str, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
        """
        Retrieve a document by ID.
        
        :param doc_id: Document ID (UUID string)
        :param include_embedding: If True, load embedding from separate file and include in result
        :return: Document data dictionary or None if not found
        """
        doc_file = self.documents_dir / f"{doc_id}.json"
        
        if not doc_file.exists():
            logger.warning(f"Document not found: {doc_id}")
            return None
        
        try:
            with open(doc_file, 'r', encoding='utf-8') as f:
                document = json.load(f)
            
            # Load embedding separately if requested
            if include_embedding:
                embedding_record = self._load_embedding(doc_id)
                if embedding_record:
                    document["embedding"] = embedding_record.get("embedding", [])
                else:
                    document["embedding"] = []
            
            return document
        except Exception as e:
            logger.error(f"Error reading document {doc_id}: {e}")
            return None
    
    def get_embedding(self, doc_id: str) -> Optional[List[float]]:
        """
        Retrieve embedding for a document.
        
        :param doc_id: Document ID
        :return: Embedding vector or None if not found
        """
        embedding_record = self._load_embedding(doc_id)
        if embedding_record:
            return embedding_record.get("embedding")
        return None
    
    def list_documents(self, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List all documents, optionally filtered by owner_id.
        
        :param owner_id: Optional owner ID to filter by
        :return: List of document index entries
        """
        index = self._read_index()
        
        if owner_id is not None:
            owner_key = str(owner_id)
            return index.get(owner_key, [])
        
        # Return all documents from global index
        return list(index.get("documents", {}).values())
    
    def get_all_embeddings(self, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all documents with their embeddings for similarity search.
        Embeddings are loaded from separate embedding files.
        
        :param owner_id: Optional owner ID to filter by
        :return: List of documents with embeddings
        """
        documents = []
        
        if owner_id is not None:
            # Get document IDs from owner index
            index = self._read_index()
            owner_key = str(owner_id)
            owner_docs = index.get(owner_key, [])
            doc_ids = [doc["id"] for doc in owner_docs if doc.get("has_embedding")]
        else:
            # Get all document IDs with embeddings
            index = self._read_index()
            all_docs = index.get("documents", {})
            doc_ids = [doc_id for doc_id, doc_info in all_docs.items() if doc_info.get("has_embedding")]
        
        # Load documents and embeddings separately
        for doc_id in doc_ids:
            # Load document (without embedding)
            doc = self.get_document(doc_id, include_embedding=False)
            if not doc:
                continue
            
            # Load embedding from separate file
            embedding = self.get_embedding(doc_id)
            if not embedding:
                continue  # Skip documents without embeddings
            
            documents.append({
                "id": doc["id"],
                "text": doc.get("extracted_text", ""),
                "embedding": embedding,  # Loaded from separate file
                "metadata": doc.get("metadata", {}),
                "recommendation": doc.get("recommendation_data"),
            })
        
        return documents
    
    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document and its associated embedding from storage.
        
        :param doc_id: Document ID to delete
        :return: True if deleted successfully, False otherwise
        """
        doc_file = self.documents_dir / f"{doc_id}.json"
        embedding_file = self.embeddings_dir / f"{doc_id}.json"
        
        if not doc_file.exists():
            logger.warning(f"Document not found for deletion: {doc_id}")
            return False
        
        try:
            # Remove document file
            doc_file.unlink()
            
            # Remove embedding file if exists
            if embedding_file.exists():
                embedding_file.unlink()
                logger.info(f"Embedding deleted: {doc_id}")
            
            # Remove image file if exists (try common extensions)
            image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
            for ext in image_extensions:
                image_file = self.images_dir / f"{doc_id}{ext}"
                if image_file.exists():
                    image_file.unlink()
                    logger.info(f"Image deleted: {doc_id}{ext}")
                    break
            
            # Remove from index
            index = self._read_index()
            
            # Remove from global index
            if "documents" in index and doc_id in index["documents"]:
                doc_entry = index["documents"][doc_id]
                owner_id = str(doc_entry.get("owner_id"))
                
                # Remove from owner index
                if owner_id in index:
                    index[owner_id] = [d for d in index[owner_id] if d["id"] != doc_id]
                
                # Remove from global index
                del index["documents"][doc_id]
                
                self._write_index(index)
            
            logger.info(f"Document deleted: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting document {doc_id}: {e}")
            return False


# Default instance for easy access
_default_storage = LocalStorage()


def save_document(document_data: Dict[str, Any]) -> str:
    """Convenience function to save document using default storage."""
    return _default_storage.save_document(document_data)


def get_document(doc_id: str, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
    """Convenience function to get document using default storage."""
    return _default_storage.get_document(doc_id, include_embedding=include_embedding)


def get_embedding(doc_id: str) -> Optional[List[float]]:
    """Convenience function to get embedding for a document."""
    return _default_storage.get_embedding(doc_id)


def get_all_embeddings(owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Convenience function to get all embeddings using default storage."""
    return _default_storage.get_all_embeddings(owner_id)

