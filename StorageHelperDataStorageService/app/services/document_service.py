"""
Document business logic service - High-level API for AI Service
"""
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from io import BytesIO
import logging


from app.models.document import Document
from app.models.document_category import DocumentCategory
from app.models.event import Event
from app.models.document_embedding import DocumentEmbedding
from app.models.document_page import DocumentPage
from app.models.user import User
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.integrations.storage_client import StorageClient, StorageException

logger = logging.getLogger(__name__)


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
                .filter(
                    DocumentCategory.code == category_code,
                    DocumentCategory.user_id == owner_id
                ).first()
            if not doc_category:
                # Auto-create new category
                doc_category = DocumentCategory(
                    user_id=owner_id,
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
            
            # Use pgvector's cosine distance for similarity search
            # Order by distance (smaller is more similar)
            documents = query.order_by(
                DocumentEmbedding.embedding.cosine_distance(embedding)
            ).limit(limit).all()
            
            return documents
            
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

    @staticmethod
    def upload_file_only(
        file_content: BytesIO,
        filename: str,
        owner_id: int
    ) -> str:
        """
        Upload file to storage and return image_url (no database operations)
        
        This is the first step in the separated upload/process flow.
        Handles file upload to storage backend only.
        
        Args:
            file_content: Image file content (BytesIO)
            filename: Original filename
            owner_id: Document owner user ID (for folder organization)
            
        Returns:
            image_url of the uploaded file
            
        Raises:
            ValueError: If operation fails
        """
        try:
            # Upload image file to storage
            image_url = StorageClient.upload_image(
                file_content=file_content,
                filename=filename,
                folder=f"documents/{owner_id}/pages"
            )
            
            logger.info(f"File uploaded to storage. URL: {image_url}")
            return image_url
            
        except Exception as e:
            logger.error(f"Failed to upload file to storage: {e}")
            raise ValueError(f"File upload failed: {str(e)}")

    @staticmethod
    def process_document_page(
        db: Session,
        image_url: str,
        owner_id: int,
        page_number: int,
        ocr_text: Optional[str] = None,
        document_id: Optional[int] = None,
        category_id: Optional[int] = None,
        location_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """
        Process and persist document page metadata (no file upload)
        
        This is the second step in the separated upload/process flow.
        Saves page metadata to database, inherited from upload_document_page logic.
        
        Args:
            db: Database session
            image_url: URL of already-uploaded image file
            owner_id: Document owner user ID
            page_number: Page number within document
            ocr_text: Optional extracted OCR text for this page
            document_id: Optional existing document ID. If None, creates new document
            category_id: Optional document category ID
            location_id: Optional storage location ID (use -1 for no location, will be converted to None)
            metadata: Optional document metadata
            
        Returns:
            Tuple of (document_id, page_id, image_url)
            
        Raises:
            ValueError: If operation fails
        """
        try:
            # Verify user exists
            user = db.query(User).filter(User.id == owner_id).first()
            if not user:
                raise ValueError(f"User {owner_id} not found")
            
            # Normalize location_id: -1 means no location, convert to None
            normalized_location_id = None if location_id == -1 else location_id
            
            # Get or create document
            if document_id:
                # Verify document exists and belongs to owner
                document = db.query(Document)\
                    .filter(Document.id == document_id, Document.owner_id == owner_id).first()
                if not document:
                    raise ValueError(f"Document {document_id} not found or does not belong to user {owner_id}")
                
                # Update document with category and location if provided
                should_update = page_number == 1 or document.category_id is None or document.current_location_id is None
                
                if should_update:
                    # Update category if provided
                    if category_id is not None:
                        document.category_id = category_id
                    
                    # Update location if provided
                    if location_id is not None:
                        document.current_location_id = normalized_location_id

                    # Persist extracted metadata (merge into doc_metadata)
                    if metadata:
                        # Re-assign to ensure SQLAlchemy detects the change
                        current_metadata = dict(document.doc_metadata or {})
                        current_metadata.update(metadata)
                        document.doc_metadata = current_metadata
                        db.add(document)
            else:
                # Create new document
                document = Document(
                    title=None,
                    owner_id=owner_id,
                    image_url=None,
                    category_id=category_id,
                    event_id=None,
                    current_location_id=normalized_location_id,
                    doc_metadata=metadata or {}
                )
                db.add(document)
                db.flush()  # Get document.id
                document_id = document.id
            
            # Check if page already exists (for Step 3B updates, page might already exist)
            existing_page = db.query(DocumentPage).filter(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number
            ).first()
            
            if existing_page:
                page_id = existing_page.id
                returned_image_url = existing_page.image_url
                db.commit()
                return (document_id, page_id, returned_image_url)
            else:
                # Create document page record
                page = DocumentPage(
                    document_id=document_id,
                    page_number=page_number,
                    image_url=image_url,
                    ocr_text=ocr_text
                )
                db.add(page)
                
                # Update document thumbnail if first page
                if page_number == 1:
                    document.image_url = image_url
                
                db.commit()
                db.refresh(page)
                
                return (document_id, page.id, image_url)
            
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to process document page: {e}")
            raise ValueError(f"Failed to process document page: {str(e)}")

    @staticmethod
    def upload_document_page(
        db: Session,
        file_content: BytesIO,
        filename: str,
        owner_id: int,
        page_number: int,
        ocr_text: Optional[str] = None,
        document_id: Optional[int] = None
    ) -> tuple:
        """
        Upload a document page (with optional OCR text)
        
        Creates new document if document_id not provided, or adds page to existing document
        
        Args:
            db: Database session
            file_content: Image file content (BytesIO)
            filename: Original filename
            owner_id: Document owner user ID
            page_number: Page number within document
            ocr_text: Optional extracted OCR text for this page
            document_id: Optional existing document ID. If None, creates new document
            
        Returns:
            Tuple of (document_id, page_id, image_url)
            
        Raises:
            ValueError: If operation fails
        """
        image_url = None
        try:
            # Verify user exists
            user = db.query(User).filter(User.id == owner_id).first()
            if not user:
                raise ValueError(f"User {owner_id} not found")
            
            # Step 1: Upload image file to storage
            image_url = StorageClient.upload_image(
                file_content=file_content,
                filename=filename,
                folder=f"documents/{owner_id}/pages"
            )
            
            # Step 2: Get or create document
            if document_id:
                # Verify document exists and belongs to owner
                document = db.query(Document)\
                    .filter(Document.id == document_id, Document.owner_id == owner_id).first()
                if not document:
                    raise ValueError(f"Document {document_id} not found or does not belong to user {owner_id}")
            else:
                # Create new document
                document = Document(
                    title=filename,
                    owner_id=owner_id,
                    image_url=None,  # Will be set to first page if needed
                    category_id=None,
                    event_id=None,
                    doc_metadata={}
                )
                db.add(document)
                db.flush()  # Get document.id
                document_id = document.id
            
            # Step 3: Create document page record
            page = DocumentPage(
                document_id=document_id,
                page_number=page_number,
                image_url=image_url,
                ocr_text=ocr_text
            )
            db.add(page)
            
            # Step 4: Update document thumbnail if first page
            if page_number == 1:
                document.image_url = image_url
            
            db.commit()
            db.refresh(page)
            
            return (document_id, page.id, image_url)
            
        except Exception as e:
            db.rollback()
            
            # Cleanup: delete uploaded file if save failed
            if image_url:
                try:
                    StorageClient.delete_image(image_url)
                except StorageException:
                    pass  # Log but don't raise
            
            raise ValueError(f"Failed to upload document page: {str(e)}")
    
    @staticmethod
    def delete_document(db: Session, document_id: int) -> bool:
        """
        Delete a document and all its associated data (pages, embeddings, files)
        
        Args:
            db: Database session
            document_id: Document ID to delete
            
        Returns:
            True if deleted successfully
            
        Raises:
            ValueError: If document not found or deletion fails
        """
        try:
            # 1. Get document and its pages
            document = db.query(Document).filter(Document.id == document_id).first()
            if not document:
                raise ValueError(f"Document {document_id} not found")
            
            # 2. Get all page image URLs to delete from storage
            pages = db.query(DocumentPage).filter(DocumentPage.document_id == document_id).all()
            image_urls = [page.image_url for page in pages if page.image_url]
            
            # Also include document thumbnail if it's different from pages
            if document.image_url and document.image_url not in image_urls:
                image_urls.append(document.image_url)
            
            # 3. Delete files from storage
            for url in image_urls:
                try:
                    StorageClient.delete_image(url)
                except StorageException as e:
                    logger.warning(f"Failed to delete file from storage: {url}. Error: {e}")
            
            # 4. Delete from database (cascading will handle pages and embeddings)
            db.delete(document)
            db.commit()
            
            return True
            
        except Exception as e:
            db.rollback()
            if isinstance(e, ValueError):
                raise e
            logger.error(f"Failed to delete document: {e}")
            raise ValueError(f"Failed to delete document: {str(e)}")
