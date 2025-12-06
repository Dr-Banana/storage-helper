"""
Document business logic service - High-level API for AI Service
"""
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from io import BytesIO

from app.models.document import Document
from app.models.document_category import DocumentCategory
from app.models.event import Event
from app.models.document_embedding import DocumentEmbedding
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.integrations.storage_client import StorageClient, StorageException


class DocumentService:
    """
    High-level service for document operations
    
    This service handles complex business logic including:
    - File upload to storage
    - Database persistence
    - Transaction management
    - Error recovery
    """
    
    @staticmethod
    def process_new_document(
        db: Session,
        file_content: BytesIO,
        filename: str,
        owner_id: int,
        category_code: str,
        event_name: Optional[str] = None,
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> Document:
        """
        Complete document processing: upload + save to DB
        
        Called by AI Service to upload and register a new document.
        Handles all internal complexity (S3, DB transaction, error recovery)
        
        Args:
            db: Database session
            file_content: File content (BytesIO)
            filename: Original filename
            owner_id: Document owner user ID
            category_code: Document category code (TAX, VISA, MED, etc.)
            event_name: Optional event name to associate document with
            additional_metadata: Additional metadata to store
            
        Returns:
            Created Document object
            
        Raises:
            ValueError: If operation fails (with automatic cleanup)
        """
        image_url = None
        try:
            # Step 1: Upload file to storage
            image_url = StorageClient.upload_image(
                file_content=file_content,
                filename=filename,
                folder=f"documents/{owner_id}"
            )
            
            # Step 2: Get or create category
            doc_category = db.query(DocumentCategory)\
                .filter(DocumentCategory.code == category_code).first()
            if not doc_category:
                # Auto-create new category
                doc_category = DocumentCategory(
                    code=category_code,
                    name=category_code.title(),
                    description=f"Auto-created from document upload"
                )
                db.add(doc_category)
                db.flush()  # Flush to get category.id
            
            # Step 3: Get or create event
            event = None
            if event_name:
                event = db.query(Event)\
                    .filter(Event.name == event_name).first()
                if not event:
                    event = Event(
                        name=event_name,
                        category=None,
                        description=f"Auto-created event from document upload"
                    )
                    db.add(event)
                    db.flush()  # Flush to get event.id
            
            # Step 4: Create document record
            metadata = additional_metadata or {}
            document = Document(
                title=filename,
                image_url=image_url,
                owner_id=owner_id,
                category_id=doc_category.id,
                event_id=event.id if event else None,
                doc_metadata=metadata
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            
            return document
            
        except Exception as e:
            db.rollback()
            
            # Cleanup: delete uploaded file if DB save failed
            if image_url:
                try:
                    StorageClient.delete_image(image_url)
                except StorageException:
                    pass  # Log but don't raise
            
            raise ValueError(f"Failed to process document: {str(e)}")
    
    @staticmethod
    def save_embedding_and_ocr(
        db: Session,
        document_id: int,
        ocr_text: str,
        embedding: List[float]
    ) -> DocumentEmbedding:
        """
        Save OCR text and vector embedding for a document
        
        Called by AI Service after processing document with OCR and embeddings
        
        Args:
            db: Database session
            document_id: Document to update
            ocr_text: Extracted text from OCR
            embedding: Vector embedding (list of floats)
            
        Returns:
            DocumentEmbedding record
            
        Raises:
            ValueError: If document not found or save fails
        """
        try:
            # Verify document exists
            document = db.query(Document)\
                .filter(Document.id == document_id).first()
            if not document:
                raise ValueError(f"Document {document_id} not found")
            
            # Update OCR text
            document.ocr_text = ocr_text
            
            # Save or update embedding
            embedding_record = db.query(DocumentEmbedding)\
                .filter(DocumentEmbedding.document_id == document_id).first()
            
            if embedding_record:
                embedding_record.embedding = embedding
            else:
                embedding_record = DocumentEmbedding(
                    document_id=document_id,
                    embedding=embedding
                )
                db.add(embedding_record)
            
            db.commit()
            db.refresh(embedding_record)
            
            return embedding_record
            
        except Exception as e:
            db.rollback()
            raise ValueError(f"Failed to save embedding: {str(e)}")
    
    @staticmethod
    def search_by_embedding(
        db: Session,
        embedding: List[float],
        limit: int = 10,
        owner_id: Optional[int] = None
    ) -> List[Document]:
        """
        Search documents by vector similarity
        
        Called by AI Service for semantic search
        
        Args:
            db: Database session
            embedding: Query embedding vector
            limit: Maximum results to return
            owner_id: Optional filter by owner
            
        Returns:
            List of similar documents (ordered by similarity)
        """
        try:
            query = db.query(Document)\
                .join(DocumentEmbedding, Document.id == DocumentEmbedding.document_id)
            
            if owner_id:
                query = query.filter(Document.owner_id == owner_id)
            
            documents = query.all()
            
            # TODO: Calculate similarity scores and sort
            # This requires vector similarity computation (cosine, euclidean, etc.)
            # For now, return all documents
            
            return documents[:limit]
            
        except Exception as e:
            raise ValueError(f"Failed to search documents: {str(e)}")
    
    @staticmethod
    def get_document_with_details(db: Session, document_id: int) -> Document:
        """
        Get complete document information including all relations
        
        Called by AI Service to retrieve full document data
        
        Args:
            db: Database session
            document_id: Document ID
            
        Returns:
            Document with all related data loaded
            
        Raises:
            ValueError: If document not found
        """
        try:
            document = db.query(Document)\
                .filter(Document.id == document_id).first()
            
            if not document:
                raise ValueError(f"Document {document_id} not found")
            
            # Ensure relations are loaded
            _ = document.category_id
            _ = document.owner_id
            _ = document.event_id
            
            return document
            
        except Exception as e:
            raise ValueError(f"Failed to get document: {str(e)}")
    
    @staticmethod
    def update_document_status(
        db: Session,
        document_id: int,
        status: str,
        metadata_update: Optional[Dict[str, Any]] = None
    ) -> Document:
        """
        Update document status and metadata
        
        Called by AI Service to update processing status
        
        Args:
            db: Database session
            document_id: Document ID
            status: New status (e.g., "processing", "completed", "failed")
            metadata_update: Additional metadata to merge
            
        Returns:
            Updated Document
            
        Raises:
            ValueError: If document not found or update fails
        """
        try:
            document = db.query(Document)\
                .filter(Document.id == document_id).first()
            
            if not document:
                raise ValueError(f"Document {document_id} not found")
            
            # Update metadata with status
            if not document.doc_metadata:
                document.doc_metadata = {}
            
            document.doc_metadata["status"] = status
            
            if metadata_update:
                document.doc_metadata.update(metadata_update)
            
            db.commit()
            db.refresh(document)
            
            return document
            
        except Exception as e:
            db.rollback()
            raise ValueError(f"Failed to update document: {str(e)}")

