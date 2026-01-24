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
from app.models.storage_location import StorageLocation
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
        owner_id: Optional[int] = None,
        exclude_receipts: bool = False
    ) -> List[Document]:
        """
        Search documents by vector similarity (cosine distance).
        
        Called by AI Service for semantic search. Results are ordered by
        similarity to the query embedding (most similar first).
        
        Args:
            db: Database session
            embedding: Query embedding vector (768-dimensional)
            limit: Maximum results to return
            owner_id: Optional filter by owner
            exclude_receipts: If True, exclude receipt parent documents (category is RECEIPT/REC 
                            but no source_receipt_id in metadata). Only return item documents.
            
        Returns:
            List of documents ordered by cosine similarity (most similar first)
        """
        try:
            from sqlalchemy import and_, or_
            from app.models.document_category import DocumentCategory
            
            query = db.query(Document)\
                .join(DocumentEmbedding, Document.id == DocumentEmbedding.document_id)
            
            if owner_id:
                query = query.filter(Document.owner_id == owner_id)
            
            # If excluding receipts, filter out receipt parent documents
            # Receipt parent: category is RECEIPT/REC but metadata does NOT have source_receipt_id
            # Item documents: have source_receipt_id in metadata (or category is not RECEIPT/REC)
            if exclude_receipts:
                # Join with category to check category code
                query = query.join(DocumentCategory, Document.category_id == DocumentCategory.id, isouter=True)
                
                # Filter: exclude receipt parent documents
                # Receipt parent: category is RECEIPT/REC AND metadata does NOT have source_receipt_id
                # Keep: all non-RECEIPT docs, or RECEIPT docs that have source_receipt_id (items)
                # Note: Items typically have their own category (FRUIT, SNACK, etc.), not RECEIPT
                # So we mainly need to exclude category=RECEIPT with no source_receipt_id
                # Use PostgreSQL JSONB '?' operator via text() for key existence check
                from sqlalchemy import text, cast
                from sqlalchemy.dialects.postgresql import JSONB
                
                # Filter: exclude documents where category is RECEIPT/REC AND metadata does NOT have source_receipt_id
                # This excludes receipt parent documents but keeps item documents (which have their own category or source_receipt_id)
                query = query.filter(
                    or_(
                        Document.category_id.is_(None),  # No category = keep
                        DocumentCategory.code.is_(None),  # Category has no code = keep
                        ~DocumentCategory.code.in_(["RECEIPT", "REC"]),  # Category is not RECEIPT/REC = keep (includes most items)
                        # If category IS RECEIPT/REC, must have source_receipt_id (meaning it's an item, not parent receipt)
                        and_(
                            DocumentCategory.code.in_(["RECEIPT", "REC"]),
                            text("document.metadata ? 'source_receipt_id'")  # PostgreSQL JSONB '?' operator to check key exists
                        )
                    )
                )
            
            # Order by vector similarity (cosine distance: lower = more similar)
            query = query.order_by(DocumentEmbedding.embedding.cosine_distance(embedding))
            
            # Search more documents if we're filtering, to ensure enough results after removing duplicates
            search_limit = limit * 3 if exclude_receipts else limit * 2
            
            # Select Document AND distance
            query = query.add_columns(DocumentEmbedding.embedding.cosine_distance(embedding).label("distance"))
            
            # Search more documents to ensure we have enough candidates to filter from
            # We fetch more candidates (limit * 10) to avoid missing valid results that might be 
            # pushed down by noise, then apply the strict threshold filtering in Python.
            search_limit = limit * 10
            
            results = query.limit(search_limit).all()
            
            # Process results: remove duplicates, filter by distance threshold, and return Documents
            # Threshold tuning:
            # 0.45: Too strict for cross-lingual (missed "Tomato" for "西红柿")
            # 0.60: Too loose (included "Rice" for "Tomato")
            # 0.52: Compromise to filter noise while keeping semantic matches
            DISTANCE_THRESHOLD = 0.52
            
            seen_ids = set()
            unique_documents = []
            
            # Debug logging for search quality
            debug_results = []
            
            for row in results:
                # row is a tuple (Document, distance)
                # IMPORTANT: When using add_columns, row is a Row object.
                # Accessing by index 0 gets the Document entity.
                doc = row[0]
                distance = row[1]
                
                # Log top results for debugging
                if len(debug_results) < 10:
                    debug_results.append(f"{doc.title} (id={doc.id}, dist={distance:.4f})")
                
                # Filter by distance threshold
                if distance > DISTANCE_THRESHOLD:
                    continue
                
                if doc.id not in seen_ids:
                    seen_ids.add(doc.id)
                    unique_documents.append(doc)
            
            logger.info(f"Search results (limit={limit}, candidates={len(results)}): {', '.join(debug_results)}")
            logger.info(f"Filtered results: {len(unique_documents)} docs (threshold={DISTANCE_THRESHOLD})")
            
            # Apply final limit
            return unique_documents[:limit]
            
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
    def _get_or_create_location_by_name(db: Session, owner_id: int, location_name: str) -> Optional[int]:
        """
        Helper to find a location by name or create it if it doesn't exist.
        """
        if not location_name:
            return None
            
        try:
            # 1. Try to find existing location (case-insensitive)
            location = db.query(StorageLocation).filter(
                StorageLocation.user_id == owner_id,
                StorageLocation.name.ilike(location_name)
            ).first()
            
            if location:
                return location.id
            
            # 2. Not found, auto-create it
            logger.info(f"Auto-creating missing storage location: '{location_name}' for user {owner_id}")
            new_location = StorageLocation(
                user_id=owner_id,
                name=location_name,
                description=f"Auto-created location for {location_name} storage"
            )
            db.add(new_location)
            db.flush() # Get new_location.id
            return new_location.id
        except Exception as e:
            logger.error(f"Failed to get/create location '{location_name}': {e}")
            return None

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
            Tuple of (document_id, page_id, image_url, item_ids) where item_ids is a list of created item document IDs
            
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
            
            # 🎯 AUTO-CREATE LOCATION LOGIC:
            # If no location_id is provided (or it's -1) and we have an AI storage suggestion,
            # create the location automatically.
            if normalized_location_id is None and metadata and metadata.get("storage_suggestion"):
                suggestion = metadata.get("storage_suggestion")
                # Don't auto-create if it's "Other"
                if suggestion and suggestion != "Other":
                    created_id = DocumentService._get_or_create_location_by_name(db, owner_id, suggestion)
                    if created_id:
                        normalized_location_id = created_id
                        logger.info(f"Automatically assigned document to new/existing location: {suggestion} (ID: {created_id})")

            # Track item IDs created
            item_ids = []

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
                    
                    # Special handling for Receipts: create child documents for each item
                    if metadata and "items" in metadata:
                        is_receipt = False
                        if document.category_id:
                            category = db.query(DocumentCategory).filter(DocumentCategory.id == document.category_id).first()
                            if category and category.code.upper() in ["RECEIPT", "REC"]:
                                is_receipt = True
                        
                        if is_receipt:
                            item_ids = DocumentService._create_item_documents(db, document, metadata)
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

                # Special handling for Receipts: create child documents for each item (New Document)
                if metadata and "items" in metadata:
                    is_receipt = False
                    if document.category_id:
                        category = db.query(DocumentCategory).filter(DocumentCategory.id == document.category_id).first()
                        if category and category.code.upper() in ["RECEIPT", "REC"]:
                            is_receipt = True
                    
                    if is_receipt:
                        item_ids = DocumentService._create_item_documents(db, document, metadata)
            
            # Check if page already exists (for Step 3B updates, page might already exist)
            existing_page = db.query(DocumentPage).filter(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number
            ).first()
            
            if existing_page:
                page_id = existing_page.id
                returned_image_url = existing_page.image_url
                db.commit()
                return (document_id, page_id, returned_image_url, item_ids)
            else:
                # If image_url is missing but we're updating an existing document, 
                # try to fallback to the document's own thumbnail
                if not image_url and document:
                    image_url = document.image_url

                # Only create a page if we have a valid image_url
                if image_url:
                    # Create document page record
                    page = DocumentPage(
                        document_id=document_id,
                        page_number=page_number,
                        image_url=image_url,
                        ocr_text=ocr_text
                    )
                    db.add(page)
                    
                    # Update document thumbnail if first page
                    if page_number == 1 and not document.image_url:
                        document.image_url = image_url
                    
                    db.commit()
                    db.refresh(page)
                    return (document_id, page.id, image_url, item_ids)
                else:
                    # No image_url and no fallback, just commit the metadata changes we made earlier
                    db.commit()
                    return (document_id, 0, None, item_ids)
            
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

    @staticmethod
    def _create_item_documents(db: Session, receipt_doc: Document, metadata: Dict[str, Any]) -> List[int]:
        """
        Create child Document records for each item in a receipt.
        
        Args:
            db: Database session
            receipt_doc: The parent receipt Document
            metadata: The extracted metadata containing 'items'
            
        Returns:
            List of created item document IDs
        """
        try:
            items_data = metadata.get("items", [])
            if not items_data:
                return 0

            purchase_date_str = metadata.get("purchase_date")
            purchase_date = None
            if purchase_date_str:
                try:
                    from datetime import datetime
                    purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d")
                except Exception:
                    logger.warning(f"Failed to parse purchase_date: {purchase_date_str}")

            # Delete existing child documents for this receipt to avoid duplicates on update
            # We identify them by source_receipt_id in metadata
            existing_items = db.query(Document).filter(
                Document.owner_id == receipt_doc.owner_id
            ).all()
            for doc in existing_items:
                if doc.doc_metadata and doc.doc_metadata.get("source_receipt_id") == receipt_doc.id:
                    db.delete(doc)
            db.flush()

            item_ids = []
            # For each item, create a new document
            for item_data in items_data:
                item_name = item_data.get("product_name") or item_data.get("original_text") or "Unknown Item"
                category_code = item_data.get("category")
                
                # 1. Get or create the category for this item
                category_id = None
                if category_code:
                    category = db.query(DocumentCategory).filter(
                        DocumentCategory.user_id == receipt_doc.owner_id,
                        DocumentCategory.code.ilike(category_code)
                    ).first()
                    if not category:
                        # Auto-create kitchen category if it doesn't exist
                        category = DocumentCategory(
                            user_id=receipt_doc.owner_id,
                            code=category_code.upper(),
                            name=category_code.title(),
                            description=f"Auto-created from receipt item"
                        )
                        db.add(category)
                        db.flush()
                    category_id = category.id

                # 2. Determine location
                # Try to map storage suggestion to an existing location, or create it
                item_location_id = receipt_doc.current_location_id
                item_location_name = None
                storage_suggestion = item_data.get("storage_suggestion")
                
                if storage_suggestion and storage_suggestion != "Other":
                    # Use helper to find or create the location
                    created_id = DocumentService._get_or_create_location_by_name(db, receipt_doc.owner_id, storage_suggestion)
                    if created_id:
                        item_location_id = created_id
                        # Get the name for metadata update
                        matched_loc = db.query(StorageLocation).filter(StorageLocation.id == created_id).first()
                        item_location_name = matched_loc.name if matched_loc else storage_suggestion
                
                # Update item_data in the metadata list so it reflects the matched/created location
                item_data["location_id"] = item_location_id
                item_data["location_name"] = item_location_name

                # 3. Calculate expiry
                expiry_date_str = None
                shelf_life_days = item_data.get("estimated_shelf_life_days")
                if shelf_life_days is not None and purchase_date:
                    from datetime import timedelta
                    expiry_date = purchase_date + timedelta(days=int(shelf_life_days))
                    expiry_date_str = expiry_date.strftime("%Y-%m-%d")

                # 4. Prepare metadata
                item_doc_metadata = {
                    "source_receipt_id": receipt_doc.id,
                    "is_food": item_data.get("is_food", True),
                    "quantity": str(item_data.get("quantity") or "1"),
                    "purchase_date": purchase_date_str,
                    "expiry_date": expiry_date_str,
                    "status": "unopened",
                    "original_text": item_data.get("original_text"),
                    "suggested_storage": storage_suggestion # Store AI suggestion even if not matched to a location_id
                }

                # 5. Create the item document
                item_doc = Document(
                    title=item_name,
                    owner_id=receipt_doc.owner_id,
                    category_id=category_id,
                    current_location_id=item_location_id,
                    image_url=receipt_doc.image_url, # Reference the same image
                    doc_metadata=item_doc_metadata
                )
                db.add(item_doc)
                db.flush()  # Flush to get item_doc.id
                item_ids.append(item_doc.id)

                # 6. 若 caller 在 metadata.items[].embedding 中传入了 768 维向量，则写入 document_embedding（不调 AI，仅用现有 API/DB）
                emb_raw = item_data.get("embedding")
                if isinstance(emb_raw, list) and len(emb_raw) == 768:
                    try:
                        emb_floats = [float(x) for x in emb_raw]
                        db.add(DocumentEmbedding(document_id=item_doc.id, embedding=emb_floats))
                    except (ValueError, TypeError):
                        logger.warning("Item document_id=%s has invalid embedding, skipping document_embedding", item_doc.id)
            
            # Save updated metadata with location info back to receipt document
            current_metadata = dict(receipt_doc.doc_metadata or {})
            current_metadata["items"] = items_data
            receipt_doc.doc_metadata = current_metadata
            db.add(receipt_doc)
            
            db.flush()
            items_created = len(item_ids)
            logger.info(f"Created {items_created} item documents for receipt {receipt_doc.id}: {item_ids}")
            return item_ids
            
        except Exception as e:
            logger.error(f"Error creating item documents: {e}")
            # Non-critical, don't raise
            return 0
